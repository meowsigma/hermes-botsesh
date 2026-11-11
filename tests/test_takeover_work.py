"""Work-item dedupe, recovery and scope; local doubles, zero model calls.

The separate takeover-after-pass.test.mjs uses the unchanged real server and
must not be replaced by these transport doubles as server-compatibility proof.
"""
import copy
import json
import unittest
from unittest.mock import AsyncMock, Mock
from test_solid import SolidTests
from hermes_botsesh_test import adapter as mod, tools
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test.reliability import receipt, begin_takeover
from gateway.platforms.event import ProcessingOutcome
from gateway.session_context import set_session_vars, clear_session_vars

EVENT = 'evt_11111111-1111-4111-8111-111111111111'
TASK = '22222222-2222-4222-8222-222222222222'

class TakeoverWorkTests(SolidTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        mod._ADAPTERS.clear(); mod._ADAPTERS.add(self.adapter)
        self.addCleanup(mod._ADAPTERS.discard, self.adapter)
        self.targets['a'] = [3]
        self.offers = []
        self.accepted = False
        original = self.client.enrolled.side_effect
        def enrolled(room, op, **args):
            if op == 'check':
                self.calls.append((room, op, args))
                return {'mentions': list(self.targets[room]), 'replies': [], 'offers': list(self.offers)}
            if op.startswith('takeover_'):
                self.calls.append((room, op, args))
                if op == 'takeover_accept':
                    self.accepted = True
                    return {**self.offer(), 'accepted': True, 'inference_authorized': True, 'read_effect': 'none'}
                if op == 'takeover_pickup':
                    return {'task': {'id': TASK, 'context': {'partialNotes': []}}}
                if op == 'takeover_claim':
                    return {'granted': True, 'task': {'id': TASK, 'winner': {'id': 'seat-a'}}, 'read_effect': 'exact_source'}
                if op == 'takeover_settle':
                    return {'settled': True}
            if op == 'read':
                result = original(room, op, **args)
                if self.accepted:
                    result['receipt_id'] = 'takeover-receipt'
                return result
            if op == 'finish':
                self.calls.append((room, op, args))
                return {'read_effect': 'processed_frozen_receipt'}
            return original(room, op, **args)
        self.client.enrolled.side_effect = enrolled

    def offer(self, event=EVENT):
        return {'number': 3, 'event_id': event, 'task_id': TASK, 'version': 0, 'retry_after_ms': 0}

    async def pass_ambient(self):
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        from hermes_botsesh_test.room_pass import pass_frozen
        current = self.adapter._current['a']
        pass_frozen(self.client, 'a', current['receipt_id'], True, target=3)
        await self.adapter._finish_current('a', current, 'interrupted')
        return event, copy.deepcopy(self.client.store.room('a')['processing_receipts']['3'])

    async def test_ambient_pass_then_offer_gets_new_turn_and_claim_without_old_pass(self):
        ambient, passed = await self.pass_ambient()
        self.offers = [self.offer()]
        self.adapter._skipped_targets.add(('a', 3))
        await self.adapter._check_room('a')
        self.assertEqual(self.adapter._admit_event.await_count, 2)
        takeover = self.adapter._admit_event.call_args.args[0]
        self.assertEqual((ambient.message_id, takeover.message_id, takeover.source.message_id), ('3', 'takeover:' + EVENT, '3'))
        current = self.adapter._current['a']
        self.assertEqual(current['batch_targets'], [3])
        self.assertIn('question 3', current['text'])
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertNotIn('explicit_pass', row)
        self.assertNotEqual(row['client_id'], passed['client_id'])
        self.assertNotEqual(row['receipt_id'], passed['receipt_id'])
        self.assertEqual(self.client.store.room('a')['work_receipts']['message:3'], passed)
        ctx = Mock(); tools.register_tools(ctx)
        claim = next(c.kwargs['handler'] for c in ctx.register_tool.call_args_list if c.kwargs['name'] == 'botsesh_takeover_claim')
        tokens = set_session_vars(platform='botsesh', chat_id='a', message_id='3')
        try:
            self.assertTrue(json.loads(claim({'reason': 'I can help', 'processed': True}))['result']['granted'])
        finally:
            clear_session_vars(tokens)
        current['model_processed'] = True
        self.assertTrue((await self.adapter.send('a', 'Text fallback', metadata={'notify': True})).success)
        await self.adapter._finish_current('a', current, 'empty_response')
        await self.adapter._check_room('a')  # Same offered event remains in the double.
        self.assertEqual(self.adapter._admit_event.await_count, 2)
        self.assertEqual(len([c for c in self.calls if c[1] == 'takeover_accept']), 1)
        self.assertEqual(len(self.posts), 1)

    async def test_duplicate_ambient_stays_deduped_after_restart(self):
        _, passed = await self.pass_ambient()
        for adapter in (self.adapter, self.make_adapter()):
            adapter._admit_event = AsyncMock(return_value=True)
            await adapter._check_room('a')
            await adapter._check_room('a')
            adapter._admit_event.assert_not_awaited()
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3'], passed)
        self.assertEqual(self.posts, {})

    async def test_unresolved_prior_work_is_never_replaced(self):
        _, passed = await self.pass_ambient()
        self.offers = [self.offer()]
        for change in ({'state': 'started'}, {'failure_pending': True},
                       {'explicit_pass': {'payload': passed['explicit_pass']['payload']}},
                       {'explicit_post_calls': {'uncertain': {'payload': {'text': 'pending'}}}}):
            with self.subTest(change=change):
                prior = {**passed, **change, 'instance': self.adapter._instance}
                self.client.store.update(lambda d: d['rooms']['a']['processing_receipts'].update({'3': prior}))
                self.calls.clear()
                # Avoid testing unrelated flush logic; exercise the identity selector.
                from hermes_botsesh_test.reliability import can_begin_takeover
                self.assertFalse(can_begin_takeover(self.client.store.room('a'), 3, EVENT))
                with self.assertRaises(RuntimeError):
                    begin_takeover(self.client.store, 'a', 3, self.offer())
                self.assertEqual(self.client.store.room('a')['processing_receipts']['3'], prior)

    async def test_same_archived_offer_cannot_replay_after_a_new_offer(self):
        _, passed = await self.pass_ambient()
        first = {**self.offer(), 'accepted': True, 'inference_authorized': True}
        begin_takeover(self.client.store, 'a', 3, first)
        receipt(self.client.store, 'a', 3, state='replied', takeover_settled=True)
        second = {**first, 'event_id': 'evt_33333333-3333-4333-8333-333333333333'}
        begin_takeover(self.client.store, 'a', 3, second)
        room = self.client.store.room('a')
        self.assertIn('takeover:' + EVENT, room['work_receipts'])
        self.assertEqual(room['work_receipts']['message:3'], passed)
        receipt(self.client.store, 'a', 3, state='replied', takeover_settled=True)
        from hermes_botsesh_test.reliability import can_begin_takeover
        self.assertFalse(can_begin_takeover(self.client.store.room('a'), 3, EVENT))

    async def test_accepted_offer_read_retry_recovers_when_ambient_target_left_inbox(self):
        await self.pass_ambient()
        self.targets['a'] = []
        self.offers = [self.offer()]
        original = self.client.enrolled.side_effect
        once = False
        def lose(room, op, **args):
            nonlocal once
            if op == 'read' and not once:
                once = True
                raise BotSeshError('connection_failed_retry_same_intent')
            return original(room, op, **args)
        self.client.enrolled.side_effect = lose
        with self.assertRaises(BotSeshError):
            await self.adapter._check_room('a')
        self.offers = []  # The server lists only fresh, not accepted, offers.
        restarted = self.make_adapter(); restarted._admit_event = AsyncMock(return_value=True)
        restarted._is_sender_authorized = lambda *args: True
        await restarted._check_room('a')
        self.assertEqual(restarted._admit_event.await_count, 1)
        self.assertEqual(restarted._current['a']['event_id'], 'takeover:' + EVENT)
        self.assertEqual(len([c for c in self.calls if c[1] == 'takeover_accept']), 1)

    async def test_wrong_accept_identity_preserves_pass_and_never_admits(self):
        _, passed = await self.pass_ambient()
        self.offers = [self.offer()]
        original = self.client.enrolled.side_effect
        def wrong(room, op, **args):
            value = original(room, op, **args)
            return {**value, 'event_id': 'foreign-event'} if op == 'takeover_accept' else value
        self.client.enrolled.side_effect = wrong
        with self.assertRaisesRegex(BotSeshError, 'takeover_admission_mismatch'):
            await self.adapter._check_room('a')
        self.assertEqual(self.adapter._admit_event.await_count, 1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3'], passed)

    async def test_duplicate_server_accept_never_authorizes_a_second_turn(self):
        _, passed = await self.pass_ambient()
        self.offers = [self.offer()]
        original = self.client.enrolled.side_effect
        def duplicate(room, op, **args):
            value = original(room, op, **args)
            return {**value, 'inference_authorized': False} if op == 'takeover_accept' else value
        self.client.enrolled.side_effect = duplicate
        await self.adapter._check_room('a')
        self.assertEqual(self.adapter._admit_event.await_count, 1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3'], passed)

    async def test_work_item_ingress_identity_survives_native_codec_and_restore(self):
        await self.pass_ambient()
        self.offers = [self.offer()]
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        from gateway.session_envelope import _snapshot_native, restore_native
        runner = Mock()
        runner.session_store._generate_session_key.return_value = 'botsesh:a'
        encoded = {**event.source.to_dict(), 'is_bot': event.source.is_bot}
        payload = _snapshot_native(runner, event, None, encoded_source=encoded)
        restored = restore_native(payload)
        self.assertEqual(restored.message_id, 'takeover:' + EVENT)
        self.assertEqual(restored.source.message_id, '3')
        receipt(self.client.store, 'a', 3, native_admission={'admission_id': 'same-work-item'})
        saved = self.client.store.room('a')['processing_receipts']['3']
        self.adapter._current.clear()
        self.adapter._restore_native('a', saved)
        current = self.adapter._current['a']
        self.assertEqual(current['event_id'], 'takeover:' + EVENT)
        self.assertEqual(current['event'].message_id, restored.message_id)
        self.assertEqual(current['event'].source.message_id, '3')
        self.assertEqual(current['receipt_id'], saved['receipt_id'])

    async def test_late_ambient_callbacks_cannot_settle_or_fail_takeover(self):
        ambient, _ = await self.pass_ambient()
        self.offers = [self.offer()]
        await self.adapter._check_room('a')
        before = self.client.store.room('a')['processing_receipts']['3']
        await self.adapter.on_processing_start(ambient)
        await self.adapter.on_processing_complete(ambient, ProcessingOutcome.FAILURE)
        await self.adapter._notify_turn_error(ambient, RuntimeError('late'))
        sent, _ = await self.adapter.send_final_ledgered(ambient, 's', 'Late ambient final', {}, reply_to='3')
        self.assertFalse(sent.success)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3'], before)
        self.assertEqual(self.posts, {})


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(TakeoverWorkTests(name) for name in TakeoverWorkTests.__dict__ if name.startswith('test_'))
