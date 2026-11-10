"""Living-chat contract; local storage + doubled MCP only, no providers."""
import hashlib
import unittest
import json
from unittest.mock import Mock, AsyncMock, patch

import test_final_after_post as fixture
from hermes_botsesh_test import tools
from hermes_botsesh_test.oauth import OAuthClient
from gateway.platforms.event import ProcessingOutcome


class LivingChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.FinalAfterPostTests.asyncSetUp(self)
        original = OAuthClient.rpc
        def rpc(api, binding, name, args=None, **kw):
            if name == 'hermes_authored':
                self.requests.append((binding['room_id'], name, args))
                key = (binding['room_id'], args['client_id'])
                message = self.messages.setdefault(key, {'number': 100 + len(self.messages), 'text': args['text'],
                    'text_sha256': hashlib.sha256(args['text'].encode()).hexdigest(),
                    'authorParticipant': {'id': binding['participant_id']}})
                return {'message': message}
            if name == 'finish_room_turn':
                self.requests.append((binding['room_id'], name, args))
                self.assertEqual(args['output'], {'nothing_to_add': True})
                return {'read_checkpoint': 5, 'reply_number': None}
            return original(api, binding, name, args, **kw)
        p = patch.object(OAuthClient, 'rpc', rpc)
        p.start()
        self.addCleanup(p.stop)
    begin = fixture.FinalAfterPostTests.begin
    model_ended = fixture.FinalAfterPostTests.model_ended

    def post(self, **options):
        args = {'room_id': 'a', 'text': 'Same deliberate words.', **options}
        with patch.object(tools, 'Client', return_value=self.client), \
             patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            return tools.post(args, Mock())

    async def test_identical_deliberate_posts_reuse_receipt_not_identity(self):
        adapter, event = await self.begin()
        receipt = adapter._current['a']['receipt_id']
        first = self.post(receipt_id=receipt, processed=True)
        second = self.post(receipt_id=receipt, processed=True)
        self.assertNotEqual(first['client_id'], second['client_id'])
        self.assertEqual(len(self.messages), 2)
        self.assertIn(receipt, self.client.store.room('a')['oauth_receipts'])
        self.assertTrue(await adapter._posted_reply('a', 3, receipt))
        self.assertIn('frozen', self.client.store.room('a')['processing_receipts']['3'])
        self.assertNotIn(3, self.client.store.room('a').get('handled', []))
        self.model_ended(adapter)
        await adapter.send('a', 'Do not narrate after explicit posts.', metadata={'notify': True})
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(len(self.messages), 2)
        self.assertNotIn('a', adapter._current)
        self.assertTrue(any(name == 'hermes_delivery' and args['state'] == 'completed'
                            and args['batch_id'] == receipt for _, name, args in self.requests))

    async def test_unattested_post_is_authored_not_runtime_notice_and_suppresses_final(self):
        adapter, event = await self.begin()
        first = self.post(receipt_id=adapter._current['a']['receipt_id'])
        second = self.post(client_id=first['client_id'], receipt_id=adapter._current['a']['receipt_id'])
        self.assertEqual(first, second)
        self.assertEqual(first['read_effect'], 'none')
        self.assertFalse(any(name in ('send_message', 'hermes_notice') for _, name, _ in self.requests))
        self.model_ended(adapter)
        await adapter.send('a', 'No extra narration.', metadata={'notify': True})
        self.assertEqual(len(self.messages), 1)
        self.assertNotIn('processed_cursor', self.client.store.room('a'))
        with patch('gateway.platforms.base.BasePlatformAdapter.send_final_ledgered') as native:
            result, _ = await adapter.send_final_ledgered(event, 'session', 'No ledger narration.', {}, reply_to='3')
            self.assertTrue(result.success)
            native.assert_not_called()

    async def test_runtime_failure_only_updates_delivery_status(self):
        adapter, event = await self.begin()
        self.model_ended(adapter, failed=True)
        await adapter.send('a', 'Provider traceback', metadata={'notify': True})
        await adapter.on_processing_complete(event, ProcessingOutcome.FAILURE)
        self.assertEqual(self.messages, {})
        self.assertTrue(any(name == 'hermes_delivery' and args['state'] == 'failed' for _, name, args in self.requests))
        self.assertFalse(any(name in ('hermes_authored', 'hermes_notice', 'send_message') for _, name, _ in self.requests))
        self.assertNotIn('processed_cursor', self.client.store.room('a'))

    async def test_explicit_pass_acknowledges_only_given_receipt_and_can_end_interrupted(self):
        from gateway.session_context import set_session_vars, clear_session_vars
        from hermes_botsesh_test.adapter import _ADAPTERS
        adapter, event = await self.begin()
        receipt = adapter._current['a']['receipt_id']
        _ADAPTERS.clear(); _ADAPTERS.add(adapter)
        self.addCleanup(_ADAPTERS.discard, adapter)
        args = {'room_id': 'a', 'receipt_id': receipt, 'processed': True}
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            with self.assertRaises(fixture.BotSeshError):
                tools.pass_turn({**args, 'processed': False}, Mock())
            tokens = set_session_vars(platform='botsesh', chat_id='a', message_id='3')
            try:
                passed = tools.pass_turn(args, Mock())
                from hermes_botsesh_test.terminal_turn import settled_turn
                agent = Mock(platform='botsesh')
                with patch('agent.subagent_lifecycle.get_active_subagent_parent', return_value=agent):
                    settled_turn(tool_name='botsesh_pass', args=args, result=json.dumps({'ok': True, 'result': passed}))
                agent.interrupt.assert_called_once_with(hard_cancel=True, tool_reason='botsesh_passed')
            finally:
                clear_session_vars(tokens)
        await adapter.on_processing_complete(event, ProcessingOutcome.CANCELLED)
        self.assertEqual(self.messages, {})
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'passed')
        self.assertNotIn('a', adapter._current)

    async def test_native_marker_only_passes_after_success_not_substring_or_empty(self):
        adapter, event = await self.begin()
        result = await adapter.send('a', 'NO_REPLY', metadata={'notify': True})
        self.assertTrue(result.success)
        self.assertEqual(self.messages, {})
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))
        self.model_ended(adapter)
        await adapter.send('a', 'NO_REPLY', metadata={'notify': True})
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.messages, {})
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'passed')
        adapter, event = await self.begin('b')
        self.model_ended(adapter, 'b')
        await adapter.send('b', 'The literal NO_REPLY is a marker.', metadata={'notify': True})
        self.assertEqual(len(self.messages), 1)

    async def test_busy_arrivals_are_received_without_freezing_and_next_turn_batches(self):
        adapter, event = await self.begin()
        self.requests.clear()
        await adapter._receive_while_busy('a')
        self.assertFalse(any(name == 'catchup' for _, name, _ in self.requests))
        self.assertTrue(any(name == 'hermes_delivery' and args['number'] == 5 and args['state'] == 'received'
                            for _, name, args in self.requests))
        self.assertNotIn('frozen', self.client.store.room('a')['processing_receipts'].get('5', {}))

    async def test_frozen_pending_inputs_share_one_admission(self):
        original = OAuthClient.rpc
        def batch(api, binding, name, args=None, **kw):
            value = original(api, binding, name, args, **kw)
            if name == 'catchup':
                value['messages'].append({**value['messages'][0], 'number': 5, 'text': 'Followup question'})
                value['next_checkpoint'] = 5
            return value
        with patch.object(OAuthClient, 'rpc', batch):
            adapter, event = await self.begin()
        self.assertEqual(adapter._current['a']['target'], 3)
        self.assertEqual(adapter._current['a']['batch_targets'], [3, 5])
        self.assertIn('"pending_message_numbers": [3, 5]', event.text)
        self.model_ended(adapter)
        await adapter.send('a', 'Answer both questions.', metadata={'notify': True})
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.client.store.room('a')['handled'], [3, 5])
        self.assertEqual(adapter._admit_event.await_count, 1)

    async def test_post_does_not_interrupt_and_tool_policy_stays_frozen(self):
        from gateway.session_context import set_session_vars, clear_session_vars
        from hermes_botsesh_test.adapter import _ADAPTERS, processed_turn
        from hermes_botsesh_test.terminal_turn import settled_turn
        from hermes_botsesh_test.tool_policy import enforce_tools
        adapter, event = await self.begin()
        _ADAPTERS.clear(); _ADAPTERS.add(adapter)
        self.addCleanup(_ADAPTERS.discard, adapter)
        processed_turn(platform='botsesh', user_message=event.text, session_id='session', turn_id='turn')
        args = {'room_id': 'a', 'text': 'First thought.', 'receipt_id': adapter._current['a']['receipt_id'], 'processed': True}
        tokens = set_session_vars(platform='botsesh', chat_id='a', message_id='3')
        try:
            result = self.post(**{k: v for k, v in args.items() if k != 'room_id'})
            await adapter._posted_reply('a', 3, args['receipt_id'])
            with patch('agent.subagent_lifecycle.get_active_subagent_parent') as parent:
                settled_turn(tool_name='botsesh_post', args=args, result=json.dumps({'ok': True, 'result': result}))
                parent.assert_not_called()
            self.assertIsNone(enforce_tools(session_id='session', turn_id='turn'))
            self.assertIn('frozen', self.client.store.room('a')['processing_receipts']['3'])
        finally:
            clear_session_vars(tokens)

    async def test_empty_native_final_never_arms_ledger_or_reads(self):
        adapter, event = await self.begin()
        self.model_ended(adapter)
        with patch('gateway.platforms.base.BasePlatformAdapter.send_final_ledgered') as native:
            result, _ = await adapter.send_final_ledgered(event, 'session', '   ', {}, reply_to='3')
            self.assertTrue(result.success)
            native.assert_not_called()
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.messages, {})
        self.assertFalse(any(name == 'finish_room_turn' or name == 'hermes_delivery' and args['state'] == 'completed'
                             for _, name, args in self.requests))
        self.assertNotIn('processed_cursor', self.client.store.room('a'))

    async def test_runtime_failure_after_explicit_processed_reply_does_not_strand_answered_turn(self):
        adapter, event = await self.begin()
        self.post(receipt_id=adapter._current['a']['receipt_id'], processed=True)
        self.model_ended(adapter, failed=True)
        await adapter.on_processing_complete(event, ProcessingOutcome.FAILURE)
        self.assertEqual(len(self.messages), 1)
        self.assertTrue(any(name == 'hermes_delivery' and args['state'] == 'completed' for _, name, args in self.requests))
        self.assertFalse(any(name == 'hermes_delivery' and args['state'] == 'failed' for _, name, args in self.requests))
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'replied')
        self.assertNotIn('a', adapter._current)

    async def test_queued_failure_recovers_only_its_confirmed_processed_answer(self):
        adapter, event = await self.begin()
        receipt = adapter._current['a']['receipt_id']
        self.post(receipt_id=receipt, processed=True)
        adapter._queue_failure('a', 3, 'runtime_error')
        await adapter._flush_failures('a')
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'replied')
        self.assertEqual(self.client.store.room('a')['failure_outbox'], {})
        self.assertTrue(any(name == 'hermes_delivery' and args['state'] == 'completed' for _, name, args in self.requests))

    async def test_lost_unattested_post_retries_exact_id_without_a_final(self):
        adapter, event = await self.begin()
        original = OAuthClient.rpc
        lost = [True]
        def rpc(api, binding, name, args=None, **kw):
            value = original(api, binding, name, args, **kw)
            if name == 'hermes_authored' and lost[0]:
                lost[0] = False
                raise fixture.BotSeshError('connection_failed_retry_same_intent')
            return value
        with patch.object(OAuthClient, 'rpc', rpc):
            with self.assertRaises(fixture.BotSeshError) as error:
                self.post()
            saved_id = error.exception.pending['client_id']
            self.model_ended(adapter)
            await adapter.send('a', 'No replacement final.', metadata={'notify': True})
            await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        sends = [args for _, name, args in self.requests if name == 'hermes_authored']
        self.assertEqual([s['client_id'] for s in sends], [saved_id, saved_id])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(sends[0]['seen_number'], 3)
        self.assertNotIn('batch_id', sends[0])
        self.assertNotIn('processed_cursor', self.client.store.room('a'))

    async def test_next_arrivals_batch_only_after_current_terminal(self):
        adapter, event = await self.begin()
        self.targets['a'].append(7)
        await adapter._receive_while_busy('a')
        self.assertEqual(adapter._admit_event.await_count, 1)
        self.model_ended(adapter)
        await adapter.send('a', 'Answer initial input.', metadata={'notify': True})
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        original = OAuthClient.rpc
        def rpc(api, binding, name, args=None, **kw):
            value = original(api, binding, name, args, **kw)
            if name == 'catchup':
                value['messages'].append({**value['messages'][0], 'number': 7, 'text': 'Later followup'})
                value['next_checkpoint'] = 7
            return value
        with patch.object(OAuthClient, 'rpc', rpc):
            await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)
        self.assertEqual(adapter._current['a']['batch_targets'], [5, 7])
        self.assertEqual(adapter._admit_event.await_count, 2)

    async def test_native_terminal_silence_reconciles_without_a_send_callback(self):
        adapter, event = await self.begin()
        current = adapter._current['a']
        current['native_admission'] = {'synthetic': True}
        current['model_processed'] = True
        with patch.object(adapter, '_native_state', AsyncMock(return_value=(
                {'status': 'terminal', 'outcome': 'completed'}, {'final_response': '[SILENT]'}))):
            await adapter._reconcile_native('a')
        self.assertEqual(self.messages, {})
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'passed')

    async def test_missing_enrollment_keeps_typed_error_and_join_is_local_only(self):
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'missing')):
            result = json.loads(tools.guarded(lambda args: tools.post(args, Mock()))({'room_id': 'missing', 'text': 'Hello'}))
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'not_enrolled')
            with self.assertRaises(fixture.BotSeshError) as error:
                tools.join({'invitation_url': 'not used'}, Mock())
            self.assertEqual(error.exception.code, 'local_enrollment_only')


if __name__ == '__main__':
    unittest.main()
