"""TAKEOVER1 offline plugin paths; no providers, real rooms or credentials."""
import unittest
import uuid
from unittest.mock import Mock, patch
import test_solid
from test_plugin import BotSeshError, tools
from hermes_botsesh_test.reliability import receipt

TASK = '9bac2041-781b-43b5-9fe6-7d1581f239aa'
OTHER = 'b80ccddf-dc40-48fe-8e49-42a0502b76bc'

class TakeoverTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_solid.SolidTests.asyncSetUp
    make_adapter = test_solid.SolidTests.make_adapter

    def freeze(self, transport='device'):
        self.client.store.update(lambda d: d['rooms']['a'].update(transport=transport))
        receipt(self.client.store, 'a', 3, state='started', receipt_id='receipt-3', frozen={
            'receipt_id': 'receipt-3', 'batch': {'messages': [
                {'number': 3, 'addressing': {'taskId': TASK}, 'text': 'private question'},
                {'number': 5, 'addressing': {'taskId': OTHER}}]}}, text='private prompt')

    async def test_typing_heartbeats_do_not_hide_task_stalls(self):
        self.adapter._current['a'] = {'target': 3}
        for _ in range(3):
            self.adapter._typing_at['a'] = 0
            await self.adapter.send_typing('a')
        self.assertEqual(sum(op == 'delivery' for _, op, _ in self.calls), 3)
        self.assertEqual(sum(op == 'working' for _, op, _ in self.calls), 1)

    async def test_device_budget_failure_is_exact_frozen_task_and_durable_no_read(self):
        self.freeze()
        self.adapter._queue_failure('a', 3, 'provider_budget')
        original = self.client.enrolled.side_effect
        attempts = []
        def lose(room, op, **args):
            if op == 'fail':
                attempts.append(args)
                raise BotSeshError('operation_timeout_retry_same_intent')
            return original(room, op, **args)
        self.client.enrolled.side_effect = lose
        with self.assertRaises(BotSeshError):
            await self.adapter._flush_failures('a')
        self.assertIn('frozen', self.client.store.room('a')['processing_receipts']['3'])
        self.client.enrolled.side_effect = lambda room, op, **args: attempts.append(args) or {} if op == 'fail' else original(room, op, **args)
        await self.make_adapter()._flush_failures('a')
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(attempts[0]['failure'], {'task_id': TASK, 'status': 'out_of_usage', 'reason': "Hermes couldn't answer: provider budget exceeded."})
        self.assertEqual(attempts[0]['target_number'], 3)
        self.assertEqual(attempts[0]['receipt_id'], 'receipt-3')
        self.assertEqual(uuid.UUID(attempts[0]['client_id']).version, 4)
        self.assertEqual(len(self.posts), 1)
        notice = next(iter(self.posts.values()))
        self.assertNotIn('processed', notice)
        self.assertNotIn('receipt_id', notice)
        self.assertNotIn('reply_to', notice, 'A generic device reply must not close the selected task before failure reports.')
        room = self.client.store.room('a')
        self.assertFalse(room['failure_outbox'])
        self.assertNotIn('handled', room)
        self.assertNotIn('processed_cursor', room)
        self.assertNotIn('private question', self.client.store.path.read_text())
        self.assertNotIn('private prompt', self.client.store.path.read_text())

    async def test_explicit_cant_do_tool_routes_without_text_or_ack(self):
        args = {'room_id': 'a', 'receipt_id': str(uuid.uuid4()), 'failure': {
            'task_id': TASK, 'status': 'cant_do', 'reason': 'Requires physical access.', 'partial_notes': 'No changes made.'}}
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            result = tools.post(args, Mock())
        self.assertEqual(result, {})
        room, op, sent = self.calls[-1]
        self.assertEqual((room, op), ('a', 'fail'))
        self.assertEqual(sent['failure'], args['failure'])
        self.assertNotIn('processed', sent)
        self.assertNotIn('text', sent)
        for extra in ({'text': 'also reply'}, {'processed': False}, {'reply_to': 3}, {'draft_ready': {}}, {'failure': {**args['failure'], 'reason': 'two\nlines'}}):
            with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
                with self.assertRaises(BotSeshError):
                    tools.post({**args, **extra}, Mock())

    async def test_oauth_explicit_failure_journals_exact_finish_turn_and_retries(self):
        from hermes_botsesh_test.oauth import OAuthClient
        binding = self.client.store.room('a')
        self.client.store.update(lambda d: d['rooms']['a'].update(participant_id='bot'))
        binding = self.client.store.room('a')
        oauth = OAuthClient(self.client.store)
        batch = str(uuid.uuid4())
        value = {'messages': [{'number': 3, 'addressing': {'taskId': TASK}, 'senderParticipantId': 'owner',
                  'authorParticipant': {'id': 'owner', 'verified': True}}], 'batch_id': batch,
                 'ack_eligible': True, 'has_more': False, 'truncated': False, 'read_checkpoint': 0, 'next_checkpoint': 30}
        with patch.object(oauth, 'rpc', return_value=value):
            oauth.call(binding, 'read', target_number=3)
        args = {'failure': {'task_id': TASK, 'status': 'cant_do', 'reason': 'Physical access is required.', 'partial_notes': 'No changes made.'},
                'receipt_id': batch, 'client_id': str(uuid.uuid4())}
        attempts = []
        def rpc(binding, name, payload=None):
            if name == 'list_rooms':
                return {'rooms': [{'room_id': 'a', 'participant_id': 'bot'}]}
            self.assertEqual(name, 'finish_turn')
            self.assertIn(args['client_id'], self.client.store.room('a')['oauth_posts'])
            attempts.append(payload)
            if len(attempts) == 1:
                raise BotSeshError('connection_failed_retry_same_intent')
            return {'failure': {'applied': True}, 'read_effect': 'none'}
        with patch.object(oauth, 'rpc', side_effect=rpc):
            with self.assertRaises(BotSeshError):
                oauth.call(binding, 'fail', **args)
            self.assertTrue(oauth.call(binding, 'pending')['pending']['failure'])
            result = oauth.call(binding, 'retry', client_id=args['client_id'])
            self.assertEqual(result['read_effect'], 'none')
            self.assertTrue(result['failure'])
            self.assertIsNone(oauth.call(binding, 'pending')['pending'])
            with self.assertRaises(BotSeshError):
                oauth.call(binding, 'fail', **{**args, 'failure': {**args['failure'], 'reason': 'changed'}})
        self.assertEqual(attempts, [{'room_id': 'a', 'batch_id': batch, 'client_id': args['client_id'], 'output': {'failure': args['failure']}}] * 2)

    async def test_confirmed_explicit_failure_suppresses_gateway_final_and_done(self):
        from gateway.platforms.event import ProcessingOutcome
        self.freeze()
        self.adapter._current['a'] = {'target': 3, 'receipt_id': 'receipt-3', 'client_id': str(uuid.uuid4()), 'delivered': False, 'model_processed': True}
        del self.client.enrolled  # Exercise actual Client progress projection, not the fixture's transport double.
        completed = {'client_id': str(uuid.uuid4()), 'failure': True, 'task_id': TASK, 'target_number': 3, 'status': 'cant_do', 'read_effect': 'none'}
        with patch.object(self.client, 'call', return_value=completed) as transport:
            self.client.enrolled('a', 'fail', failure={'task_id': TASK, 'status': 'cant_do', 'reason': 'Hardware access required.'}, receipt_id='receipt-3', client_id=completed['client_id'])
            result = await self.adapter.send('a', 'I gave up.', metadata={'notify': True})
            self.assertTrue(result.success)
            await self.adapter.on_processing_complete(Mock(source=Mock(chat_id='a'), message_id='3'), ProcessingOutcome.SUCCESS)
            self.assertEqual(transport.call_count, 1, 'The final must not send or fetch/ack anything after give-up.')
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['state'], 'failed')
        self.assertFalse(self.posts)
        self.assertNotIn('handled', self.client.store.room('a'))
        self.assertNotIn('pending_final', self.client.store.room('a'))

    async def test_uncertain_explicit_failure_never_creates_gateway_final_journal(self):
        from gateway.platforms.event import ProcessingOutcome
        self.freeze()
        pending = {'client_id': str(uuid.uuid4()), 'failure': True, 'task_id': TASK, 'read_effect': 'none'}
        self.client.store.update(lambda d: d['rooms']['a'].update(pending_completion=pending))
        event = Mock(source=Mock(chat_id='a'), message_id='3')
        for processed in (True, False):
            self.adapter._current['a'] = {'target': 3, 'receipt_id': 'receipt-3', 'client_id': str(uuid.uuid4()), 'delivered': False, 'model_processed': processed}
            sent, _ = await self.adapter.send_final_ledgered(event, 'session', 'I cannot do it.', {}, reply_to=None)
            self.assertFalse(sent.success)
            self.assertNotIn('pending_final', self.client.store.room('a'))
            self.assertFalse(self.posts)
            await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertNotIn('handled', self.client.store.room('a'))

    async def test_oauth_runtime_402_uses_notice_then_content_free_delivery_and_exact_retry(self):
        import hashlib
        from hermes_botsesh_test.oauth import OAuthClient
        self.freeze('oauth')
        self.client.store.update(lambda d: d['rooms']['a'].update(participant_id='bot'))
        del self.client.enrolled
        rpc_calls = []
        def rpc(binding, name, args=None):
            rpc_calls.append((name, args))
            if name == 'hermes_notice':
                return {'message': {'number': 11, 'text_sha256': hashlib.sha256(args['text'].encode()).hexdigest(), 'authorParticipant': {'id': 'bot'}}}
            self.assertEqual(name, 'hermes_delivery')
            if len([c for c in rpc_calls if c[0] == name]) == 1:
                raise BotSeshError('connection_failed_retry_same_intent')
            return {}
        self.adapter._current['a'] = {'target': 3, 'receipt_id': 'receipt-3', 'client_id': str(uuid.uuid4()), 'delivered': False, 'model_processed': False}
        with patch.object(OAuthClient, 'rpc', side_effect=rpc):
            sent = await self.adapter.send('a', 'HTTP 402 private vendor balance detail', metadata={'notify': True})
            self.assertFalse(sent.success)
            await self.make_adapter()._flush_failures('a')
        self.assertEqual([name for name, _ in rpc_calls], ['hermes_notice', 'hermes_delivery', 'hermes_delivery'])
        self.assertEqual(rpc_calls[1][1], {'room_id': 'a', 'number': 3, 'state': 'failed', 'failure_code': 'provider_budget'})
        self.assertEqual(rpc_calls[1], rpc_calls[2])
        self.assertNotIn('private vendor', str(rpc_calls))
        self.assertNotIn('processed', rpc_calls[0][1])
        self.assertNotIn('batch_id', rpc_calls[0][1])
        self.assertNotIn('handled', self.client.store.room('a'))

    async def test_other_runtime_failure_is_error_and_never_borrows_latest_task(self):
        self.freeze()
        self.adapter._queue_failure('a', 3, 'runtime_error')
        await self.adapter._flush_failures('a')
        failure = next(args for _, op, args in self.calls if op == 'fail')
        self.assertEqual(failure['failure'], {'task_id': TASK, 'status': 'error', 'reason': "Hermes couldn't answer: gateway processing error."})
        self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={}, failure_outbox={}))
        receipt(self.client.store, 'a', 3, state='started', receipt_id='receipt-3', frozen={'batch': {'messages': [{'number': 3}, {'number': 5, 'addressing': {'taskId': OTHER}}]}})
        before = len([op for _, op, _ in self.calls if op == 'fail'])
        self.adapter._queue_failure('a', 3, 'runtime_error')
        await self.adapter._flush_failures('a')
        self.assertEqual(len([op for _, op, _ in self.calls if op == 'fail']), before)

    async def test_budget_status_without_final_still_reports_out_of_usage(self):
        from gateway.platforms.event import ProcessingOutcome
        self.freeze()
        self.adapter._current['a'] = {'target': 3, 'receipt_id': 'receipt-3', 'client_id': str(uuid.uuid4()), 'delivered': False, 'model_processed': False}
        await self.adapter.send_or_update_status('a', 'error', 'HTTP 402: provider budget exhausted (private detail)')
        await self.adapter.on_processing_complete(Mock(source=Mock(chat_id='a'), message_id='3'), ProcessingOutcome.FAILURE)
        failure = next(args for _, op, args in self.calls if op == 'fail')
        self.assertEqual(failure['failure']['status'], 'out_of_usage')
        self.assertNotIn('private detail', str(self.calls))

    async def test_oauth_failure_receipt_identity_change_is_denied_before_rpc(self):
        from hermes_botsesh_test.oauth import OAuthClient
        self.client.store.update(lambda d: d['rooms']['a'].update(participant_id='new-bot', oauth_receipts={'frozen': {
            'expires': 9999999999, 'participant_id': 'old-bot', 'target_number': 3, 'task_targets': {'3': TASK}, 'numbers': [3]}}))
        oauth = OAuthClient(self.client.store)
        with patch.object(oauth, 'rpc') as rpc:
            with self.assertRaises(BotSeshError) as raised:
                oauth.call(self.client.store.room('a'), 'fail', receipt_id='frozen', client_id=str(uuid.uuid4()),
                           failure={'task_id': TASK, 'status': 'cant_do', 'reason': 'Access unavailable.'})
            self.assertEqual(raised.exception.code, 'receipt_scope')
            rpc.assert_not_called()

if __name__ == '__main__':
    unittest.main()
