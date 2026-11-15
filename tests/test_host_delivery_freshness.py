"""Freshness is opt-in (rc.4): only a model-authored tool post or a native exact-input
send carries seen_through. Host/cron delivery, notices and direct Client.enrolled posts
carry none. A chunked native reply is freshness-checked once, on its first chunk.

Offline: only the MCP wire is doubled (a double of the server's room_moved_on refusal
for any send that carries seen_through once the room has moved on). Zero inference.
"""
import unittest
import uuid
from unittest.mock import Mock, patch

import test_final_after_post as fixture
from test_plugin import BotSeshError, tools
from hermes_botsesh_test import adapter as adapter_module
from hermes_botsesh_test.oauth import OAuthClient

SENDS = ('send_message', 'hermes_authored')


class OptInFreshness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.FinalAfterPostTests.asyncSetUp(self)
        self.moved = False          # a person has posted since this seat last read
        self.moved_after_first = False  # ... between chunk 1 and chunk 2
        self.refused = []
        original = OAuthClient.rpc

        def rpc(api, binding, name, args=None, **kw):
            if name in SENDS and self.moved and 'seen_through' in (args or {}) and not args.get('allow_stale'):
                self.refused.append(dict(args))
                raise BotSeshError('room_moved_on', 409, rejected=True,
                                   room_moved_on={'code': 'room_moved_on', 'messages': [{'number': 4}], 'read_effect': 'none'})
            value = original(api, binding, name, args, **kw)
            if name in SENDS and self.moved_after_first:
                self.moved = True
            return value
        patcher = patch.object(OAuthClient, 'rpc', rpc)
        patcher.start()
        self.addCleanup(patcher.stop)

    begin = fixture.FinalAfterPostTests.begin
    tool_post = fixture.FinalAfterPostTests.tool_post

    def sends(self):
        return [args for _, name, args in self.requests if name in SENDS]

    def room_moved_on(self, latest=9):
        self.client.store.update(lambda d: d['rooms']['a'].update(latest_seen_id=latest))
        self.moved = True

    async def test_host_delivery_direct_enrolled_post_and_notice_send_no_freshness(self):
        self.room_moved_on()
        with patch.object(adapter_module, 'Client', return_value=self.client):
            host = await adapter_module.standalone_send(None, 'a', 'Scheduled report: hello')
        self.assertTrue(host.get('success'), host)
        direct = self.client.enrolled('a', 'post', text='Direct enrolled post', client_id=str(uuid.uuid4()))
        self.assertIn('number', direct)
        adapter = self.adapters['a']
        notice = await adapter._post_payload('a', {'text': 'Gateway notice', 'client_id': str(uuid.uuid4())})
        self.assertIn('number', notice)
        sends = self.sends()
        self.assertEqual(len(sends), 3)
        for args in sends:
            self.assertNotIn('seen_through', args)
            self.assertNotIn('allow_stale', args)
        for saved in self.client.store.room('a')['oauth_posts'].values():
            self.assertNotIn('seen_through', saved['exact'])
        self.assertEqual(self.refused, [])

    async def test_model_authored_tool_post_still_opts_in_and_can_be_overridden(self):
        self.room_moved_on(latest=9)
        ctx = Mock()
        ctx.session_surface.return_value = 'cli'
        with patch.object(tools, 'Client', return_value=self.client):
            with self.assertRaises(BotSeshError) as refused:
                tools.post({'room_id': 'a', 'text': 'Model words', 'client_id': str(uuid.uuid4())}, ctx)
            self.assertEqual(refused.exception.code, 'room_moved_on')
            self.assertEqual([r['seen_through'] for r in self.refused], [9])
            # Explicit, deliberate override is a new authored post.
            sent = tools.post({'room_id': 'a', 'text': 'Model words', 'client_id': str(uuid.uuid4()),
                               'allow_stale': True}, ctx)
        self.assertIn('number', sent)
        landed = self.sends()[-1]
        self.assertEqual((landed['seen_through'], landed['allow_stale']), (9, True))

    async def test_native_authored_post_keeps_the_exact_input_cursor(self):
        adapter, _ = await self.begin()
        self.tool_post(adapter)
        self.assertEqual(self.sends()[-1]['seen_through'], 3)

    async def test_later_chunks_of_an_accepted_reply_do_not_recheck_freshness(self):
        adapter, _ = await self.begin()
        current = adapter._current['a']
        text = 'a' * 4000 + 'b' * 4000 + 'tail'
        payload = {'text': text, 'receipt_id': current['receipt_id'], 'processed': True,
                   'reply_to': current['target'], 'client_id': current['client_id']}
        self.moved_after_first = True   # a person posts right after chunk 1 lands
        with fixture.native_input('a', current['target']):
            result = await adapter._post_payload('a', payload, current['target'])
        self.assertEqual(result['read_effect'], 'processed_frozen_receipt')
        self.assertTrue(self.moved)
        sends = self.sends()
        self.assertEqual([s['text'] for s in sends], ['a' * 4000, 'b' * 4000, 'tail'])
        self.assertEqual(sends[0]['seen_through'], 3, 'the first chunk is checked once, on the exact input')
        self.assertNotIn('seen_through', sends[1])
        self.assertNotIn('seen_through', sends[2])
        self.assertNotIn('batch_id', sends[1])
        self.assertEqual(sends[2]['batch_id'], current['receipt_id'])
        self.assertEqual(self.refused, [])

    async def test_first_chunk_is_still_refused_and_nothing_else_is_sent(self):
        adapter, _ = await self.begin()
        current = adapter._current['a']
        payload = {'text': 'a' * 4000 + 'b' * 10, 'receipt_id': current['receipt_id'], 'processed': True,
                   'reply_to': current['target'], 'client_id': current['client_id']}
        self.moved = True               # the room moved on while the model was writing
        with fixture.native_input('a', current['target']):
            with self.assertRaises(BotSeshError) as refused:
                await adapter._post_payload('a', payload, current['target'])
        self.assertEqual(refused.exception.code, 'room_moved_on')
        self.assertEqual(len(self.refused), 1)
        self.assertEqual(self.refused[0]['seen_through'], 3)
        self.assertEqual(self.messages, {})


if __name__ == '__main__':
    unittest.main()
