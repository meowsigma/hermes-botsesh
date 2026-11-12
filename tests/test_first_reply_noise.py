"""Exact retained missing-attestation repro, native notice boundary and join scope."""
# Owner 2026-11-10: zero/one/many posts, no processed:true refusal, no runtime posts.
import json
import uuid
import unittest
from unittest.mock import Mock, patch
from test_plugin import Client, Store, BotSeshError, StorageError, tools
from test_final_after_post import FinalAfterPostTests
from hermes_botsesh_test.oauth import OAuthClient


class ReplyNoiseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = FinalAfterPostTests.asyncSetUp
    begin = FinalAfterPostTests.begin
    model_ended = FinalAfterPostTests.model_ended
    tool_post = FinalAfterPostTests.tool_post

    async def test_retained_receipt_without_processed_teaches_then_corrected_post_suppresses_final(self):
        # t_d31c619a: threaded posts must teach, not infer processed or unthread.
        adapter, event = await self.begin()
        args = {'room_id': 'a', 'text': 'Tool answer', 'reply_to': 3,
                'receipt_id': adapter._current['a']['receipt_id']}
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            result = json.loads(tools.guarded(lambda a: tools.post(a, Mock()))(args))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'], 'unattested_reply_target_not_supported')
        self.assertEqual(self.messages, {})
        self.assertNotIn('handled', self.client.store.room('a'))
        self.assertNotIn('processed_cursor', self.client.store.room('a'))
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertIn('frozen', row)
        calls = list(row['explicit_post_calls'].values())
        self.assertEqual(len(calls), 1)
        self.assertNotIn('processed', calls[0]['payload'])
        self.assertNotIn('read_attested', row)
        corrected = json.JSONDecoder().raw_decode(result['message'].split('botsesh_post(', 1)[1])[0]
        self.assertEqual(corrected, {**args, 'processed': True})
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            posted = tools.post(corrected, Mock())
        self.assertEqual(posted['read_effect'], 'processed_frozen_receipt')
        self.model_ended(adapter)
        self.assertTrue((await adapter.send('a', 'Actual final answer', metadata={'notify': True})).success)
        self.assertEqual([m['text'] for m in self.messages.values()], ['Tool answer'])
        from gateway.platforms.event import ProcessingOutcome
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'replied')
        self.assertEqual(self.client.store.room('a')['processed_cursor'], 3)
        self.assertTrue(self.client.store.room('a')['processing_receipts']['3']['read_attested'])

    async def test_confirmed_post_suppresses_distinct_final_and_keeps_later_target(self):
        adapter, event = await self.begin()
        result = self.tool_post(adapter)
        self.model_ended(adapter)
        final = await adapter.send('a', 'Distinct explanatory final must not appear', metadata={'notify': True})
        self.assertTrue(final.success, final.error)
        self.assertEqual(final.message_id, str(result['number']))
        self.assertEqual(len(self.messages), 1)
        self.assertNotIn('Distinct explanatory', next(iter(self.messages.values()))['text'])
        from gateway.platforms.event import ProcessingOutcome
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)

    async def test_unmarked_native_notices_idle_and_active_never_send_or_attest(self):
        adapter = self.adapters['a']
        for active in (False, True):
            if active:
                await self.begin()
            before = self.client.store.room('a')
            for text, meta in [('No home channel is configured. Run /sethome.', None),
                               ('Arbitrary localized runtime status', {'_interim_send': True}),
                               ('gateway.notify.no_home_channel', {})]:
                self.assertTrue((await adapter.send('a', text, metadata=meta)).success)
            after = self.client.store.room('a')
            self.assertEqual(after.get('handled'), before.get('handled'))
            self.assertEqual(after.get('processed_cursor'), before.get('processed_cursor'))
            self.assertEqual(self.messages, {})
        # Text is not the classifier. Authored finals containing the same words survive.
        self.model_ended(adapter)
        text = 'My answer quotes: No home channel is configured.'
        await adapter.send('a', text, metadata={'notify': True})
        self.assertEqual([m['text'] for m in self.messages.values()], [text])

    async def test_invitation_upgrade_replaces_notification_stream_without_reenrollment(self):
        import asyncio
        adapter = self.adapters['a']
        self.client.store.update(lambda d: d['rooms']['a'].pop('transport'))
        adapter._running = True
        adapter.poll_seconds = 0.001
        starts, cancelled = [], []
        async def stream(room, changed):
            transport = self.client.store.room(room).get('transport')
            starts.append(transport)
            try:
                await asyncio.Future()
            finally:
                cancelled.append(transport)
        async def check(room):
            await asyncio.sleep(0)
            if starts == [None]:
                self.client.store.update(lambda d: d['rooms']['a'].update(transport='oauth'))
            if len(starts) == 2:
                adapter._running = False
        with patch.object(adapter, '_stream', stream), patch.object(adapter, '_check_room', check), patch.object(self.client, 'enrolled', return_value={}):
            await asyncio.wait_for(adapter._room_loop('a'), 1)
        self.assertEqual(starts, [None, 'oauth'])
        self.assertEqual(cancelled, starts)
        self.assertEqual(self.messages, {})
        self.assertNotIn('processed_cursor', self.client.store.room('a'))

    async def test_live_grant_state_is_only_added_for_literal_true_projection(self):
        original = OAuthClient.rpc
        for state in (None, False, 'true', True):
            self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={}))
            self.adapters['a']._current.clear()
            def rpc(api, binding, name, args=None, **kw):
                value = original(api, binding, name, args, **kw)
                if name == 'hermes_tool_policy' and state is not None:
                    value['ongoing_replies'] = state
                return value
            with patch.object(OAuthClient, 'rpc', rpc):
                adapter, event = await self.begin()
            self.assertEqual('Ongoing replies are on for this room.' in event.text, state is True)


class JoinTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = Client(Store(Path(self.tmp.name) / 'private'))
        self.id = str(uuid.uuid4())
        self.link = 'https://botsesh.com/agent-room/room/invite/' + self.id + '/bsi_' + 'a' * 43

    def test_not_enrolled_is_typed_without_hiding_private_storage_errors(self):
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('cli', '')):
            result = json.loads(tools.guarded(lambda a: tools.read(a, Mock()))({'room_id': 'room'}))
        self.assertEqual(result['error'], 'not_enrolled')
        self.assertEqual(result['message'], 'Not in this room yet. Use botsesh_join with the invitation link.')
        self.client.store.path.write_text('{}'); self.client.store.path.chmod(0o644)
        with self.assertRaises(StorageError):
            self.client.enrolled('room', 'read')

    def test_native_join_local_only_and_complete_context_not_read_ack(self):
        ctx = Mock(); ctx.get_config.return_value = ''
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'room')), patch.object(self.client, 'join') as join:
            with self.assertRaises(BotSeshError): tools.join({'invitation_url': self.link}, ctx)
            join.assert_not_called()
        # The real join/read persists exact participant receipt metadata before
        # its model-only handle projection (AGENTS 11j).
        import time
        self.client.store.update(lambda d: d['rooms'].update(room={
            'active': True, 'participant_id': 'joined-seat', 'oauth_receipts': {
                'frozen': {'participant_id': 'joined-seat', 'expires': time.time()+3600, 'numbers': []}}}))
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('cli', '')), patch.object(self.client, 'join', return_value={'room_id':'room'}) as join, patch.object(self.client, 'enrolled', return_value={'receipt_id':'frozen','read_effect':'none','batch':{'messages':[]}}) as enrolled:
            result = tools.join({'invitation_url':self.link},ctx)
            self.assertEqual(result['receipt_id'], 'r1')
            self.assertEqual(result['read_effect'], 'none')
            self.assertIn('frozen', self.client.store.room('room')['oauth_receipts'])
            enrolled.assert_called_once_with('room','read')

    def test_exact_invitation_parser_existing_identity_and_origin_denial(self):
        from hermes_botsesh_test.invitation import InvitationClient
        result = {'room': {'id': 'room'}, 'participantId': 'seat',
                  'membership': {'id': 'membership', 'participantId': 'seat', 'expiresAt': 9999999999999},
                  'access': {'token': 'bsa_' + 'a' * 43, 'expiresAt': 9999999999999}}
        # The transport changed deliberately: keep this parser/identity oracle
        # offline by doubling the Python boundary, never a dead JS call site.
        with patch('hermes_botsesh_test.invitation._http', return_value=result) as call, patch.object(InvitationClient, 'verify', return_value={}):
            self.assertEqual(self.client.join(self.link)['participant_id'], 'seat')
            self.assertEqual(call.call_args.args, ('https://botsesh.com', 'room', 'POST', '/agent-invites/redeem'))
            self.assertEqual(call.call_args.kwargs['payload']['invitationId'], self.id)
        with patch.object(self.client, 'enrolled', return_value={}) as call:
            self.assertEqual(self.client.join(self.link)['participant_id'],'seat')
            call.assert_called_once_with('room', 'verify')
        for bad in (self.link.replace('https://botsesh.com','https://evil.example'),self.link+'?q=secret',self.link.replace(self.id,'00000000-0000-1000-8000-000000000000'),self.link+'/extra'):
            with self.assertRaises(BotSeshError): self.client.join(bad)
        self.client.store.update(lambda d:d['rooms']['room'].update(active=False))
        with patch.object(self.client,'call') as call:
            with self.assertRaises(StorageError):self.client.join(self.link)
            call.assert_not_called()

if __name__ == '__main__': unittest.main()
