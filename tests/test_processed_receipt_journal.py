"""Missing-receipt poison regression: composed tool/client/native recovery.

Offline wire double only; no server, credentials, gateway or model execution.
Run: PYTHONPATH=tests:/path/to/hermes-agent python3 -m unittest test_processed_receipt_journal -v
"""
import copy
import unittest
import uuid
from unittest.mock import Mock, patch

import test_final_after_post as fixture
from hermes_botsesh_test import tools
from hermes_botsesh_test.oauth import OAuthClient
from hermes_botsesh_test.client import BotSeshError


class ProcessedReceiptJournalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.FinalAfterPostTests.asyncSetUp
    begin = fixture.FinalAfterPostTests.begin
    model_ended = fixture.FinalAfterPostTests.model_ended

    def post(self, payload, room='a', *, target='3'):
        with fixture.native_input(room, target), patch.object(tools, 'Client', return_value=self.client), \
             patch.object(tools, 'calling_scope', return_value=('botsesh', room)):
            return tools.post({'room_id': room, **payload}, Mock())

    def payload(self, **options):
        return {'text': 'Exact authored bytes.\ncafé 🐚\n',
                'client_id': str(uuid.uuid4()), 'processed': True, **options}

    def calls(self, room='a', target='3'):
        return self.client.store.room(room)['processing_receipts'][target].get('explicit_post_calls', {})

    def sends(self):
        return [args for _, name, args in self.requests if name in ('send_message', 'hermes_authored')]

    async def test_missing_receipt_without_native_scope_posts_then_valid_reply_and_later_wake(self):
        # 0.7.2: no exact task-local target means ordinary content, not inferred read.
        # t_d31c619a: this fallback is explicitly unthreaded; threading now teaches.
        adapter, event = await self.begin()
        payload = self.payload()
        ordinary = self.post(payload, target='')
        self.assertEqual(ordinary['read_effect'], 'none')
        self.assertIn('did not advance read state', ordinary['notice'])
        self.assertEqual(self.calls()[payload['client_id']]['authored_options'],
                         {k: v for k, v in payload.items() if k not in ('text', 'client_id')})
        self.assertFalse(self.calls()[payload['client_id']]['payload']['processed'])
        self.assertEqual(len(self.sends()), 1)
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        receipt = adapter._current['a']['receipt_id']
        # Validation never grants permission to change an already authored UUID.
        with self.assertRaises(BotSeshError) as mismatch:
            self.post({**payload, 'receipt_id': receipt})
        self.assertEqual(mismatch.exception.code, 'pending_send_mismatch')
        valid = {**payload, 'client_id': str(uuid.uuid4()), 'receipt_id': receipt, 'reply_to': 3}
        result = self.post(valid)
        self.assertEqual(result['read_effect'], 'processed_frozen_receipt')
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.sends()[0]['text'].encode(), payload['text'].encode())
        self.assertNotIn('a', adapter._current)
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)
        self.assertEqual(adapter._admit_event.await_count, 2)

    async def test_persisted_missing_receipt_is_rejected_without_post_or_read(self):
        adapter, event = await self.begin()
        payload = self.payload(reply_to=3)
        # Exact baseline shape: explicit intent persisted, no transport-journal row
        # or definitive rejection; tools returned a misleading pending identity.
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['3'].update(
            explicit_post_calls={payload['client_id']: {'payload': payload}}))
        before = copy.deepcopy(self.client.store.room('b'))
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        saved = self.calls()[payload['client_id']]
        self.assertEqual(saved['payload'], payload)
        self.assertEqual(saved['rejected'], {'code': 'processed_receipt_required', 'status': None})
        self.assertNotIn('result', saved)
        self.assertEqual(self.messages, {})
        self.assertEqual(self.sends(), [])
        self.assertNotIn('processed_cursor', self.client.store.room('a'))
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertNotIn('read_attested', row)
        self.assertEqual(row['state'], 'failed')  # F5-03: rejected reply is not quiet success.
        self.assertFalse(any(name == 'hermes_delivery' and args['state'] == 'completed'
                             for _, name, args in self.requests))
        self.assertEqual(self.client.store.room('b'), before)
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)

    async def test_persisted_poison_does_not_block_already_confirmed_valid_reply(self):
        adapter, event = await self.begin()
        payload = self.payload(reply_to=3)
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['3'].update(
            explicit_post_calls={payload['client_id']: {'payload': payload}}))
        self.post(self.payload(reply_to=3, receipt_id=adapter._current['a']['receipt_id']))
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'replied')
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)

    async def test_ambiguous_transport_keeps_exact_pending_and_blocks_same_room(self):
        adapter, event = await self.begin()
        payload = self.payload(reply_to=3, receipt_id=adapter._current['a']['receipt_id'])
        original = OAuthClient.rpc
        def unavailable(api, binding, name, args=None, **kwargs):
            if name == 'send_message':
                raise BotSeshError('connection_failed_retry_same_intent')
            return original(api, binding, name, args, **kwargs)
        with patch.object(OAuthClient, 'rpc', unavailable):
            with self.assertRaises(BotSeshError) as error:
                self.post(payload)
            self.assertFalse(error.exception.definitive)
            self.assertEqual(error.exception.pending['client_id'], payload['client_id'])
            self.model_ended(adapter)
            with self.assertRaises(BotSeshError):
                await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(self.calls()[payload['client_id']], {'payload': payload})
        self.assertEqual(self.client.store.room('a')['oauth_posts'][payload['client_id']]['exact'], payload)
        self.assertIn('a', adapter._current)
        self.assertEqual(self.messages, {})
        # Independent room still admits its own input; no cross-room clearing.
        other, _ = await self.begin('b')
        self.assertEqual(other._current['b']['target'], 3)
        await adapter._check_room('a')
        self.assertEqual(adapter._admit_event.await_count, 1)

    async def test_accepted_response_lost_retries_original_once_without_duplicate(self):
        adapter, event = await self.begin()
        payload = self.payload(reply_to=3, receipt_id=adapter._current['a']['receipt_id'])
        self.lose_send = True
        with self.assertRaises(BotSeshError) as error:
            self.post(payload)
        self.assertFalse(error.exception.definitive)
        self.assertEqual(len(self.messages), 1)
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        before = copy.deepcopy(self.client.store.room('a')['oauth_posts'])
        # Even a structured local refusal must not settle this real uncertain
        # ordinary send's UUID (the other transport journal owns it).
        with self.assertRaises(BotSeshError) as invalid:
            self.client.enrolled('a', 'finish', output={'reply': payload['text']},
                receipt_id=payload['receipt_id'], client_id=payload['client_id'], processed=False)
        self.assertFalse(invalid.exception.definitive)
        with self.assertRaises(BotSeshError) as invalid:
            self.client.enrolled('a', 'post', **{**payload, 'draft_ready': self.draft()})
        self.assertFalse(invalid.exception.definitive)
        self.assertEqual(self.client.store.room('a')['oauth_posts'], before)
        changed = {**payload, 'text': payload['text'] + 'changed'}
        with self.assertRaises(BotSeshError) as mismatch:
            self.post(changed)
        self.assertEqual(mismatch.exception.code, 'pending_send_mismatch')
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        sends = self.sends()
        self.assertEqual(len(sends), 2)
        self.assertEqual(sends[0], sends[1])
        self.assertEqual(sends[0]['client_id'], payload['client_id'])
        self.assertEqual(len(self.messages), 1)
        self.assertTrue(self.client.store.room('a')['processing_receipts']['3']['read_attested'])

    def draft(self):
        return {'title': 'Deliverable', 'recipientId': str(uuid.uuid4())}

    async def structured_refusal(self, *, processed, receipt):
        adapter, _ = await self.begin()
        payload = self.payload(draft_ready=self.draft())
        if not processed:
            payload.pop('processed')
        if receipt:
            payload['receipt_id'] = adapter._current['a']['receipt_id']
        with self.assertRaises(BotSeshError) as error:
            self.post(payload)
        self.assertEqual(error.exception.code, 'processed_receipt_required')
        self.assertTrue(error.exception.definitive)
        self.assertIsNone(error.exception.pending)
        saved = self.calls()[payload['client_id']]
        self.assertEqual(saved['payload'], payload)
        self.assertEqual(saved['rejected']['code'], 'processed_receipt_required')
        with self.assertRaises(BotSeshError) as mismatch:
            self.post({**payload, 'processed': True,
                       'receipt_id': adapter._current['a']['receipt_id']})
        self.assertEqual(mismatch.exception.code, 'pending_send_mismatch')
        # Simulate an old audit persisted without definitive metadata; normal
        # native exact-payload reconciliation must settle it, not resend it.
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['3']
            ['explicit_post_calls'][payload['client_id']].pop('rejected'))
        before_b = copy.deepcopy(self.client.store.room('b'))
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(self.calls()[payload['client_id']]['payload'], payload)
        self.assertEqual(self.calls()[payload['client_id']]['rejected']['code'], 'processed_receipt_required')
        self.assertNotIn('result', self.calls()[payload['client_id']])
        self.assertEqual(self.messages, {})
        self.assertEqual(self.client.store.room('a').get('oauth_turns', {}), {})
        self.assertEqual(self.client.store.room('a').get('oauth_posts', {}), {})
        self.assertFalse(any(name in ('send_message', 'hermes_authored', 'finish_room_turn', 'ack')
                             for _, name, _ in self.requests))
        self.assertNotIn('read_attested', row)
        self.assertNotIn('processed_cursor', self.client.store.room('a'))
        self.assertEqual(row['state'], 'failed')  # F5-03: rejected reply is not quiet success.
        self.assertFalse(any(name == 'hermes_delivery' and args['state'] == 'completed'
                             for _, name, args in self.requests))
        self.assertEqual(self.client.store.room('b'), before_b)
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)
        self.assertEqual(adapter._admit_event.await_count, 2)

    async def test_structured_missing_receipt_without_scope_posts_only_ordinary_text(self):
        adapter, _ = await self.begin()
        payload = self.payload(draft_ready=self.draft())
        result = self.post(payload, target='')
        self.assertEqual(result['read_effect'], 'none')
        self.assertIn('structured declarations', result['notice'])
        saved = self.calls()[payload['client_id']]
        self.assertEqual(saved['authored_options'],
                         {k: v for k, v in payload.items() if k not in ('text', 'client_id')})
        self.assertNotIn('draft_ready', saved['payload'])
        self.assertFalse(saved['payload']['processed'])
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'completed_unread')
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)

    async def test_structured_missing_processed_settles_new_and_persisted_attempt(self):
        await self.structured_refusal(processed=False, receipt=True)

    async def test_other_structured_local_refusals_do_not_poison_native_audit(self):
        adapter, _ = await self.begin()
        receipt = adapter._current['a']['receipt_id']
        routine = {'routineId': str(uuid.uuid4()), 'revision': 1, 'stepIndex': 0,
                   'scheduledFor': 0, 'status': 'completed'}
        for options, code in (
                ({'draft_ready': {'title': 'Missing recipient'}}, 'invalid_runtime_answer'),
                ({'routine_report': routine}, 'oauth_routine_report_not_supported'),
                ({'draft_ready': self.draft(), 'reply_to': 3}, 'oauth_structured_reply_target_not_supported')):
            with self.subTest(code=code):
                payload = self.payload(receipt_id=receipt, **options)
                with self.assertRaises(BotSeshError) as error:
                    self.post(payload)
                self.assertEqual(error.exception.code, code)
                self.assertTrue(error.exception.definitive)
                self.assertIsNone(error.exception.pending)
                self.assertEqual(self.calls()[payload['client_id']]['rejected']['code'], code)
        # file_refs is only available on finish(), not the advertised post tool.
        with self.assertRaises(BotSeshError) as error:
            self.client.enrolled('a', 'finish', output={'reply': 'Draft', 'file_refs': ['F1']},
                receipt_id=receipt, client_id=str(uuid.uuid4()), processed=True)
        self.assertEqual(error.exception.code, 'file_bytes_not_supplied')
        self.assertTrue(error.exception.definitive)
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(self.messages, {})
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)

    async def test_structured_lost_response_keeps_journal_and_retries_same_identity(self):
        adapter, _ = await self.begin()
        payload = self.payload(draft_ready=self.draft(), receipt_id=adapter._current['a']['receipt_id'])
        accepted, attempts = {}, []
        original = OAuthClient.rpc
        def finish(api, binding, name, args=None, **kwargs):
            if name != 'finish_room_turn':
                return original(api, binding, name, args, **kwargs)
            self.requests.append((binding['room_id'], name, copy.deepcopy(args)))
            attempts.append(copy.deepcopy(args))
            key = (binding['room_id'], args['client_id'])
            result = accepted.setdefault(key, {'reply_number': 101, 'read_checkpoint': 3})
            if len(attempts) == 1:
                raise BotSeshError('connection_failed_retry_same_intent')
            return result
        with patch.object(OAuthClient, 'rpc', finish):
            with self.assertRaises(BotSeshError) as error:
                self.post(payload)
            self.assertFalse(error.exception.definitive)
            self.assertEqual(error.exception.pending['client_id'], payload['client_id'])
            before = copy.deepcopy(self.client.store.room('a')['oauth_turns'])
            self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
            # A caller's different/invalid retry cannot retire an existing UUID.
            # These local guards must remain uncertain if EITHER journal owns it.
            for output, processed, receipt in (
                    ({'reply': payload['text'], 'draft_ready': payload['draft_ready']}, False, payload['receipt_id']),
                    ({'reply': 'Bad', 'draft_ready': {}}, True, payload['receipt_id']),
                    ({'reply': 'Bad', 'file_refs': ['F1']}, True, payload['receipt_id']),
                    ({'reply': 'Bad'}, True, None)):
                with self.subTest(output=output):
                    with self.assertRaises(BotSeshError) as invalid:
                        self.client.enrolled('a', 'finish', output=output, processed=processed,
                            receipt_id=receipt, client_id=payload['client_id'])
                    self.assertFalse(invalid.exception.definitive)
            with self.assertRaises(BotSeshError) as invalid:
                self.client.enrolled('a', 'post', **{**payload, 'reply_to': 3})
            self.assertFalse(invalid.exception.definitive)
            self.assertEqual(self.client.store.room('a')['oauth_turns'], before)
            with self.assertRaises(BotSeshError) as mismatch:
                self.post({**payload, 'text': 'Changed'})
            self.assertEqual(mismatch.exception.code, 'pending_send_mismatch')
            self.assertNotIn('rejected', self.calls()[payload['client_id']])
            self.model_ended(adapter)
            await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(len(accepted), 1)
        self.assertEqual(attempts, [attempts[0], attempts[0]])
        self.assertEqual(attempts[0]['client_id'], payload['client_id'])
        self.assertEqual(attempts[0]['output']['reply'].encode(), payload['text'].encode())
        self.assertTrue(self.client.store.room('a')['oauth_turns'][payload['client_id']]['complete'])
        self.assertTrue(self.client.store.room('a')['processing_receipts']['3']['read_attested'])
        self.assertNotIn('a', adapter._current)

    async def test_structured_unknown_receipt_is_definitively_rejected_without_read_or_send(self):
        adapter, _ = await self.begin()
        other, _ = await self.begin('b')
        before_b = copy.deepcopy(self.client.store.room('b'))
        for receipt in ('unknown-receipt', other._current['b']['receipt_id']):
            with self.subTest(receipt=receipt):
                payload = self.payload(draft_ready=self.draft(), receipt_id=receipt)
                with self.assertRaises(BotSeshError) as error:
                    self.post(payload)
                self.assertEqual(error.exception.code, 'receipt_expired')
                # 11j r3: a pre-transport scope refusal is not acceptance loss.
                self.assertTrue(error.exception.definitive)
                self.assertIsNone(error.exception.pending)
                self.assertEqual(self.calls()[payload['client_id']], {'payload': payload,
                    'rejected': {'code': 'receipt_expired', 'status': None}})
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertNotIn('a', adapter._current)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'failed')
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        self.assertEqual(self.client.store.room('a').get('oauth_turns', {}), {})
        self.assertFalse(any(name in ('send_message', 'hermes_authored', 'finish_room_turn', 'ack')
                             for _, name, _ in self.requests))
        self.assertEqual(self.client.store.room('b'), before_b)

    async def test_wrong_receipt_and_foreign_room_remain_fail_closed(self):
        adapter, event = await self.begin()
        other, _ = await self.begin('b')
        foreign_receipt = other._current['b']['receipt_id']
        before_b = copy.deepcopy(self.client.store.room('b'))
        for receipt in ('unknown-receipt', foreign_receipt):
            with self.subTest(receipt=receipt):
                payload = self.payload(reply_to=3, receipt_id=receipt)
                with self.assertRaises(BotSeshError) as error:
                    self.post(payload)
                self.assertEqual(error.exception.code, 'receipt_expired')
                self.assertTrue(error.exception.definitive)
                self.assertIsNone(error.exception.pending)
                self.assertEqual(self.calls()[payload['client_id']]['rejected']['code'], 'receipt_expired')
                self.assertEqual(self.client.store.room('a').get('oauth_posts', {}), {})
        receipt = adapter._current['a']['receipt_id']
        with self.assertRaises(BotSeshError) as error:
            self.post(self.payload(reply_to=999, receipt_id=receipt))
        self.assertEqual(error.exception.code, 'unread_reply_target')
        with patch.object(tools, 'Client', return_value=self.client), \
             patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            with self.assertRaises(BotSeshError) as error:
                tools.post({'room_id': 'b', **self.payload(receipt_id=foreign_receipt)}, Mock())
        self.assertEqual(error.exception.code, 'cross_room_denied')
        self.assertEqual(self.client.store.room('b'), before_b)
        self.assertEqual(self.messages, {})
        self.assertEqual(self.sends(), [])
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])


if __name__ == '__main__':
    unittest.main()
