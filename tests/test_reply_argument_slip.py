"""Fable F5-03 calls through the actual registered Hermes tool dispatcher.

Wire-only double, no inference. Source: review5 notes/MODEL-TURNS.md, calls
12, 14 and 29 (opaque room/receipt IDs replaced by frozen fixture IDs;
Fable's question #2 is fixture question #3 in both post replays).
"""
import json
import unittest
import uuid
from unittest.mock import Mock, patch

import test_confirmed_pass as core_fixture
from hermes_botsesh_test import tools
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test.oauth import OAuthClient

ANSWER = 'Second, in one sentence: fix `done` at notes.py:45 by converting the argument with `int(sys.argv[2])` (and raising an "unknown id" error when no note has that id), because today the str never matches the int ids at notes.py:33, so `python notes.py done 0` silently does nothing and exits 0.'


class ReplyArgumentSlipTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = core_fixture.ConfirmedPassTests.asyncSetUp
    begin = core_fixture.ConfirmedPassTests.begin
    prepare = core_fixture.ConfirmedPassTests.prepare
    def core_call(self, args, *, name='botsesh_pass'):
        from agent.tool_executor import _resolve_sequential_dispatch
        from tools.tool_search_validation import validate_deferred_call_args
        def validated(agent, ref, callbacks):
            # Fable used deferred tool_call, whose validation precedes native
            # dispatch. Direct dispatch alone does not enforce extra keys.
            error = validate_deferred_call_args(ref.name, ref.args)
            self.assertIsNone(error, error)
            return _resolve_sequential_dispatch(agent, ref, callbacks)
        with patch('agent.tool_executor._resolve_sequential_dispatch', side_effect=validated):
            return core_fixture.ConfirmedPassTests.core_call(self, args, name=name)

    async def test_fable_first_post_missing_room_and_extra_batch_succeeds(self):
        adapter, args = await self.prepare()
        result, agent, _ = self.core_call({'text': ANSWER, 'reply_to': 3,
            'batch_id': str(uuid.uuid4())}, name='botsesh_post')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['read_effect'], 'none')
        self.assertEqual(next(iter(self.messages.values()))['text'], ANSWER)
        self.assertFalse(any(name == 'send_message' for _, name, _ in self.requests))
        agent.interrupt.assert_not_called()

    async def test_fable_retry_with_room_and_extra_batch_succeeds(self):
        adapter, args = await self.prepare()
        result, agent, _ = self.core_call({**args, 'text': ANSWER, 'reply_to': 3,
            'batch_id': str(uuid.uuid4())}, name='botsesh_post')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['read_effect'], 'processed_frozen_receipt')
        self.assertEqual(next(iter(self.messages.values()))['text'], ANSWER)
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertTrue(row['read_attested'])
        self.assertNotIn('batch_id', next(iter(row['explicit_post_calls'].values()))['payload'])
        agent.interrupt.assert_not_called()

    async def test_fable_read_extra_batch_returns_exact_frozen_input(self):
        adapter, args = await self.prepare()
        result, _, _ = self.core_call({'room_id': 'a', 'batch_id': str(uuid.uuid4())}, name='botsesh_read')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result'], self.client.store.room('a')['processing_receipts']['3']['frozen'])
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])

    async def test_extra_keys_dropped_and_logged_without_values(self):
        _, args = await self.prepare()
        with self.assertLogs(tools.logger, level='WARNING') as logs:
            result, _, _ = self.core_call({**args, 'text': ANSWER, 'receipt': 'private-value',
                'batch_id': 'secret-batch-value'}, name='botsesh_post')
        self.assertTrue(result['ok'], result)
        self.assertIn('batch_id', logs.output[0])
        self.assertIn('receipt', logs.output[0])
        self.assertNotIn('private-value', ''.join(logs.output))
        self.assertNotIn('secret-batch-value', ''.join(logs.output))

    async def test_no_receipt_alias_or_foreign_scope_inference(self):
        _, args = await self.prepare()
        result, _, _ = self.core_call({'text': ANSWER, 'processed': True,
            'batch_id': 'not-the-current-receipt'}, name='botsesh_post')
        # 0.7.2: batch_id stays ignored; only the actual native frozen receipt is used.
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['read_effect'], 'processed_frozen_receipt')
        saved = next(iter(self.client.store.room('a')['processing_receipts']['3']['explicit_post_calls'].values()))
        self.assertEqual(saved['payload']['receipt_id'], args['receipt_id'])
        self.assertNotIn('batch_id', saved['payload'])
        result, _, _ = self.core_call({**args, 'room_id': 'b', 'text': ANSWER}, name='botsesh_post')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'cross_room_denied')
        for scope in (('cli', ''), ('botsesh', ''), ('botsesh', 'a')):
            with patch.object(tools, 'calling_scope', return_value=scope):
                with self.assertRaises(BotSeshError):
                    tools.scoped_room({'room_id': ''} if scope[1] else {}, Mock(), infer=True)
        self.assertEqual(len(self.messages), 1)

    async def test_rejected_post_cannot_turn_into_nothing_to_add(self):
        adapter, args = await self.prepare()
        # Missing bookkeeping now succeeds; an invalid structured declaration is still rejected.
        result, _, _ = self.core_call({**args, 'text': ANSWER,
            'draft_ready': {'title': 'Missing recipient'}}, name='botsesh_post')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'invalid_runtime_answer')
        result, agent, _ = self.core_call(args)
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'], 'reply_attempted_cannot_pass')
        agent.interrupt.assert_not_called()
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))
        adapter._current['a']['model_processed'] = True
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['state'], 'failed')
        self.assertNotIn('explicit_pass', row)
        self.assertNotIn('read_attested', row)
        failures = [a for _, n, a in self.requests if n == 'hermes_delivery' and a['state'] == 'failed']
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['failure_stage'], 'reply_delivery')
        self.assertEqual(self.messages, {})

    async def test_server_rejected_post_reports_delivery_failure_not_pass(self):
        adapter, args = await self.prepare()
        original = OAuthClient.rpc
        def rejected(api, binding, name, payload=None, **kwargs):
            if name == 'send_message':
                raise BotSeshError('message_rejected', 400)
            return original(api, binding, name, payload, **kwargs)
        with patch.object(OAuthClient, 'rpc', rejected):
            result, _, _ = self.core_call({**args, 'text': ANSWER}, name='botsesh_post')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'message_rejected')
        result, agent, _ = self.core_call(args)
        self.assertFalse(result['ok'])
        agent.interrupt.assert_not_called()
        adapter._current['a']['model_processed'] = True
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'failed')
        self.assertFalse(any(n == 'finish_room_turn' for _, n, _ in self.requests))
        failures = [a for _, n, a in self.requests if n == 'hermes_delivery' and a['state'] == 'failed']
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]['failure_stage'], 'reply_delivery')
        self.assertEqual(self.messages, {})

    async def test_uncertain_post_keeps_original_identity_and_blocks_pass(self):
        adapter, args = await self.prepare()
        original = OAuthClient.rpc
        def lost(api, binding, name, payload=None, **kwargs):
            if name == 'send_message':
                raise BotSeshError('connection_failed_retry_same_intent')
            return original(api, binding, name, payload, **kwargs)
        client_id = str(uuid.uuid4())
        with patch.object(OAuthClient, 'rpc', lost):
            result, _, _ = self.core_call({**args, 'text': ANSWER, 'client_id': client_id}, name='botsesh_post')
            self.assertFalse(result['ok'])
            self.assertEqual(result['pending']['client_id'], client_id)
            result, agent, _ = self.core_call(args)
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'reply_attempted_cannot_pass')
            agent.interrupt.assert_not_called()
            with self.assertRaises(BotSeshError):
                await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertIn(client_id, self.client.store.room('a')['oauth_posts'])
        self.assertIn('a', adapter._current)
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))


if __name__ == '__main__':
    unittest.main()
