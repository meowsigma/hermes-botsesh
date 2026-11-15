"""Real loopback native OAuth path (same fixture as test_stale_post), zero inference.

A long native reply is split into <=4000-UTF-16-unit chunks. Only the FIRST chunk is
judged against the exact input's freshness cursor: a person posting between chunk 1 and
chunk 2 must not strand a half-posted reply. (Reviewer item 4 on rc.3 eaece81 reproduced
chunk 2 being refused with room_moved_on.)

Set BOTSESH_STALE_SERVER_ROOT to the exact server checkout, as for test_stale_post.
"""
import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from test_plugin import BotSeshError
from test_stale_post import RealStalePost
from hermes_botsesh_test.adapter import BotSeshAdapter
from hermes_botsesh_test.oauth import OAuthClient


class ChunkedReplyFreshness(RealStalePost):
    # Inherited fixture tests live in test_stale_post; a non-callable is never collected.
    test_definitive_stale_refusal_is_terminal_after_store_restart_for_every_native_send_kind = None
    test_native_conflict_reaches_tool_without_read_or_retry_flood_then_explicit_override_and_exact_replay = None

    def mine(self):
        return [m['text'] for m in self.command(op='inspect')['messages'] if m['participant'] == self.fixture['participant_id']]

    def test_later_chunks_survive_a_person_posting_between_chunks(self):
        q = self.command(op='human', text='Write something long.')
        frozen = self.freeze(q['number'])
        original = OAuthClient.rpc
        landed = []
        def person_speaks_after_first_chunk(api, binding, name, args=None, **kwargs):
            value = original(api, binding, name, args, **kwargs)
            if name in ('hermes_authored', 'send_message'):
                landed.append(dict(args))
                if len(landed) == 1:
                    self.command(op='human', text='A person speaks between chunk 1 and chunk 2')
            return value
        payload = {'text': 'a'*4000 + 'b'*4000 + 'tail', 'client_id': str(uuid.uuid4()), 'receipt_id': frozen['receipt_id'],
                   'processed': True, 'reply_to': q['number']}
        adapter = SimpleNamespace(client=self.client)  # _post_payload only needs the Client
        with patch.object(OAuthClient, 'rpc', person_speaks_after_first_chunk):
            result = asyncio.run(BotSeshAdapter._post_payload(adapter, self.room, payload, q['number']))
        self.assertEqual(result['read_effect'], 'processed_frozen_receipt', result)
        self.assertEqual([a['text'] for a in landed], ['a'*4000, 'b'*4000, 'tail'])
        self.assertEqual(landed[0]['seen_through'], q['id'], 'chunk 1 is judged against the exact input')
        self.assertTrue(all('seen_through' not in a for a in landed[1:]), 'later chunks never re-check')
        self.assertEqual(self.mine(), ['a'*4000, 'b'*4000, 'tail'], 'the whole reply landed, in order')

    def test_first_chunk_is_still_refused_when_the_room_moved_on_first(self):
        q = self.command(op='human', text='Another question.')
        frozen = self.freeze(q['number'])
        self.command(op='human', text='Moved on before the reply started')
        before = self.command(op='inspect')
        payload = {'text': 'c'*4000 + 'd'*10, 'client_id': str(uuid.uuid4()), 'receipt_id': frozen['receipt_id'],
                   'processed': True, 'reply_to': q['number']}
        adapter = SimpleNamespace(client=self.client)
        with self.assertRaises(BotSeshError) as moved:
            asyncio.run(BotSeshAdapter._post_payload(adapter, self.room, payload, q['number']))
        self.assertEqual(moved.exception.code, 'room_moved_on')
        self.assertEqual(self.command(op='inspect'), before, 'nothing posted, no read attested')


del RealStalePost

if __name__ == '__main__':
    unittest.main()
