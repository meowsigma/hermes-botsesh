"""Cross-input refusals through native terminal settlement, zero inference.

Based on the unchanged Opus t_6df522b4 r2b wire/adapter probe. Only MCP is
replaced; tools, journals, native finish and next-input admission are real.
"""

import json
import time
import unittest
import uuid
from unittest.mock import Mock, patch

import test_final_after_post as fx
from test_plugin import tools, BotSeshError
from gateway.platforms.event import ProcessingOutcome


class ReceiptScopeFinishTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fx.FinalAfterPostTests.asyncSetUp
    begin = fx.FinalAfterPostTests.begin
    model_ended = fx.FinalAfterPostTests.model_ended

    def add_other(self, rid='receipt-a-5'):
        def add(data):
            room = data['rooms']['a']
            room.setdefault('oauth_receipts', {})[rid] = {
                'numbers': [5], 'participant_id': 'bot-a', 'expires': time.time() + 3600}
            room['processing_receipts']['5'] = {
                'target': 5, 'state': 'admitted', 'receipt_id': rid,
                'instance': self.adapters['a']._instance,
                'frozen': {'receipt_id': rid, 'read_effect': 'none',
                           'batch': {'messages': [{'number': 5, 'isOwn': False, 'text': 'Question 5',
                               'senderParticipantId': 'owner',
                               'authorParticipant': {'id': 'owner', 'verified': True}}],
                               'hasMore': False, 'nextCheckpoint': 5}}}
        self.client.store.update(add)
        return rid

    def post(self, payload):
        with fx.native_input('a', 3), patch.object(tools, 'Client', return_value=self.client), \
                patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            return json.loads(tools.guarded(lambda a: tools.post(a, Mock()))({'room_id': 'a', **payload}))

    async def assert_finished_without_other_read(self, adapter, event, other):
        self.model_ended(adapter)
        result = await adapter.send('a', 'Do not substitute an automatic final.', metadata={'notify': True})
        self.assertTrue(result.success, result.error)
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertFalse(any(args and args.get('batch_id') == other
                             for _, name, args in self.requests
                             if name in ('send_message', 'finish_room_turn', 'hermes_delivery')))
        room = self.client.store.room('a')
        self.assertNotIn('read_attested', room['processing_receipts']['5'])
        self.assertNotIn('posted_reply', room['processing_receipts']['5'])
        self.assertEqual(room['processing_receipts']['3']['state'], 'failed')
        self.assertNotIn('a', adapter._current)
        self.assertNotIn('pending_final', room)
        self.assertEqual(self.messages, {})
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)
        self.assertEqual(adapter._admit_event.await_count, 2)

    async def refused_post(self, *, threaded, supplied=None, rid='receipt-a-5'):
        adapter, event = await self.begin()
        other = self.add_other(rid)
        cid = str(uuid.uuid4())
        payload = {'text': 'Borrowed.', 'receipt_id': supplied or other, 'processed': True, 'client_id': cid}
        if threaded:
            payload['reply_to'] = 3
        denied = self.post(payload)
        # Check lifecycle first: pre-fix unthreaded forged #5, threaded stranded #3.
        await self.assert_finished_without_other_read(adapter, event, other)
        self.assertFalse(denied['ok'])
        self.assertEqual(denied['error'], 'receipt_expired')
        self.assertNotIn('pending', denied)
        call = self.client.store.room('a')['processing_receipts']['3']['explicit_post_calls'][cid]
        self.assertEqual(call['rejected']['code'], 'receipt_expired')
        self.assertEqual(call['payload'], payload)
        self.assertNotIn(cid, self.client.store.room('a').get('oauth_posts', {}))
        self.assertNotIn(cid, self.client.store.room('a').get('oauth_turns', {}))

    async def test_cross_input_unthreaded_refusal_survives_native_finish(self):
        await self.refused_post(threaded=False)

    async def test_cross_input_threaded_refusal_survives_native_finish(self):
        await self.refused_post(threaded=True)

    async def test_cross_input_full_uuid_refusal_survives_native_finish(self):
        await self.refused_post(threaded=False, rid=str(uuid.uuid4()))

    async def test_cross_input_handle_refusal_survives_native_finish(self):
        await self.refused_post(threaded=False, supplied='r2')

    async def test_read_attested_bookkeeping_is_restricted_to_original_input(self):
        adapter, _ = await self.begin()
        own = adapter._current['a']['receipt_id']
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts'].update({
            '7': {'target': 7, 'state': 'received', 'receipt_id': own}}))
        self.client.enrolled('a', 'post', _input_target=3, text='Exact input only.',
            receipt_id=own, processed=True, client_id=str(uuid.uuid4()))
        room = self.client.store.room('a')
        self.assertTrue(room['processing_receipts']['3']['read_attested'])
        self.assertNotIn('read_attested', room['processing_receipts']['7'])
        self.assertNotIn('posted_reply', room['processing_receipts']['7'])
        self.assertFalse(any(args and '_input_target' in args for _, _, args in self.requests))

    async def test_gateway_receipt_requires_an_explicit_original_target(self):
        adapter, _ = await self.begin()
        with self.assertRaises(BotSeshError) as error:
            await adapter._post_payload('a', {'text': 'Missing native scope.',
                'receipt_id': adapter._current['a']['receipt_id'],
                'processed': True, 'client_id': str(uuid.uuid4())})
        self.assertTrue(error.exception.definitive)
        self.assertFalse(any(name == 'send_message' for _, name, _ in self.requests))

    async def test_gateway_pending_final_cannot_borrow_another_input(self):
        adapter, event = await self.begin()
        other = self.add_other()
        payload = {'text': 'Corrupted retained final.', 'receipt_id': other,
                   'processed': True, 'client_id': str(uuid.uuid4())}
        adapter._journal_gateway_delivery('a', payload, 3, pending_final=True)
        result = await adapter._post_final('a', payload, 3)
        self.assertEqual(result['read_effect'], 'none')
        self.assertFalse(any(args and args.get('batch_id') == other
                             for _, name, args in self.requests if name == 'send_message'))
        room = self.client.store.room('a')
        self.assertNotIn('read_attested', room['processing_receipts']['5'])
        self.assertNotIn('posted_reply', room['processing_receipts']['5'])
        self.assertNotIn('pending_final', room)
        self.assertEqual(room['processing_receipts']['3']['state'], 'failed')

    async def test_posted_reconciliation_requires_exact_input_before_retry(self):
        adapter, _ = await self.begin()
        other = self.add_other()
        payload = {'text': 'Other input operation.', 'receipt_id': other,
                   'processed': True, 'client_id': str(uuid.uuid4())}
        self.client.store.update(lambda d: d['rooms']['a'].update(
            oauth_posts={payload['client_id']: {'exact': payload}}))
        result = await adapter._posted_reply('a', 3, other)
        self.assertIsNone(result)
        self.assertFalse(any(name == 'send_message' for _, name, _ in self.requests))
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['5'])

    async def test_delivery_metadata_cannot_carry_another_input_batch(self):
        adapter, _ = await self.begin()
        other = self.add_other()
        with self.assertRaises(BotSeshError) as error:
            await adapter._delivery('a', 3, 'completed', batch_id=other)
        self.assertTrue(error.exception.definitive)
        self.assertFalse(any(name == 'hermes_delivery' and args.get('batch_id') == other
                             for _, name, args in self.requests))

    async def test_pass_scope_refusal_is_definitive_and_never_journaled(self):
        adapter, _ = await self.begin()
        other = self.add_other()
        with fx.native_input('a', 3), patch.object(tools, 'Client', return_value=self.client), \
                patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            with self.assertRaises(BotSeshError) as error:
                tools.pass_turn({'room_id': 'a', 'receipt_id': other, 'processed': True}, Mock())
        self.assertTrue(error.exception.definitive)
        self.assertIsNone(error.exception.pending)
        self.assertNotIn('explicit_pass', self.client.store.room('a')['processing_receipts']['3'])
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))

    async def test_every_native_batch_send_operation_requires_its_original_input(self):
        adapter, _ = await self.begin()
        other = self.add_other()
        cid = str(uuid.uuid4())
        self.client.store.update(lambda d: d['rooms']['a'].update(oauth_posts={cid: {
            'exact': {'text': 'Other input operation.', 'receipt_id': other,
                      'processed': True, 'client_id': cid}}}))
        before = list(self.requests)
        for op, args in (
            ('post', {'text': 'No bypass.', 'processed': True, 'client_id': str(uuid.uuid4())}),
            ('finish', {'output': {'nothing_to_add': True}, 'processed': True, 'client_id': str(uuid.uuid4())}),
            ('fail_task', {'failure': {'status': 'cant_do'}, 'client_id': str(uuid.uuid4())}),
            ('takeover_claim', {'event_id': 'offer', 'processed': True, 'reason': 'I can help.'}),
            ('tool_policy', {'target': 3, 'client_id': str(uuid.uuid4())}),
            ('delivery', {'target': 3, 'state': 'completed', 'batch_id': other}),
            ('retry', {'client_id': cid}),
        ):
            with self.subTest(op=op):
                if op not in ('delivery', 'retry'):
                    args['receipt_id'] = other
                with self.assertRaises(BotSeshError) as error:
                    self.client.enrolled('a', op, _input_target=3, **args)
                self.assertTrue(error.exception.definitive)
                self.assertIsNone(error.exception.pending)
        self.assertEqual(self.requests, before)
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['5'])

    async def test_typed_failure_flush_drops_a_cross_input_receipt(self):
        adapter, _ = await self.begin()
        other = self.add_other()
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['3'].update(
            explicit_failure={'payload': {'failure': {'status': 'cant_do'},
                'receipt_id': other, 'client_id': str(uuid.uuid4())}}))
        await adapter._flush_explicit_failure('a', 3)
        room = self.client.store.room('a')
        self.assertEqual(room['processing_receipts']['3']['state'], 'failed')
        self.assertIn('rejected_failure', room['processing_receipts']['3'])
        self.assertNotIn('read_attested', room['processing_receipts']['5'])
        self.assertFalse(any(name in ('finish_room_turn', 'hermes_cant_do', 'send_message')
                             for _, name, _ in self.requests))

    async def test_foreign_transport_row_does_not_authorize_native_finish_replay(self):
        adapter, event = await self.begin()
        other = self.add_other()
        cid = str(uuid.uuid4())
        payload = {'text': 'Other transport is not this input.', 'receipt_id': other,
                   'processed': True, 'client_id': cid}
        def save(data):
            room = data['rooms']['a']
            room['processing_receipts']['3']['explicit_post_calls'] = {cid: {'payload': payload}}
            room['oauth_posts'] = {cid: {'exact': payload}}
        self.client.store.update(save)
        self.model_ended(adapter)
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        room = self.client.store.room('a')
        self.assertEqual(room['processing_receipts']['3']['state'], 'failed')
        self.assertEqual(room['processing_receipts']['3']['explicit_post_calls'][cid]['rejected']['code'], 'receipt_expired')
        self.assertNotIn('failed', room['oauth_posts'][cid], 'do not rewrite uncertain original transport')
        self.assertNotIn('read_attested', room['processing_receipts']['5'])
        self.assertFalse(any(name == 'send_message' for _, name, _ in self.requests))

    async def test_original_uncertain_transport_retries_its_expired_receipt_only(self):
        adapter, event = await self.begin()
        own = adapter._current['a']['receipt_id']
        cid = str(uuid.uuid4())
        self.lose_send = True
        denied = self.post({'text': 'Original exact bytes.', 'receipt_id': own,
                            'processed': True, 'client_id': cid})
        self.assertIn('pending', denied, 'actual response loss remains uncertain')
        exact = self.client.store.room('a')['oauth_posts'][cid]['exact']
        self.client.store.update(lambda d: d['rooms']['a']['oauth_receipts'][own].update(expires=0))
        self.model_ended(adapter)
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        room = self.client.store.room('a')
        self.assertEqual(room['processing_receipts']['3']['state'], 'replied')
        self.assertEqual(room['oauth_posts'][cid]['exact'], exact)
        sends = [args for _, name, args in self.requests if name == 'send_message']
        self.assertEqual(len(sends), 2)
        self.assertEqual(sends[0], sends[1])
        self.assertEqual(sends[0]['batch_id'], own)
        self.assertEqual(len(self.messages), 1)
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['5'])

    async def test_scoped_client_blocks_wire_and_local_read_bookkeeping(self):
        adapter, _ = await self.begin()
        other = self.add_other()
        with self.assertRaises(BotSeshError) as error:
            self.client.enrolled('a', 'post', _input_target=3,
                text='No bypass.', receipt_id=other, processed=True, client_id=str(uuid.uuid4()))
        self.assertTrue(error.exception.definitive)
        self.assertFalse(any(name == 'send_message' for _, name, _ in self.requests))
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['5'])

    async def test_persisted_unsent_cross_input_audit_is_rejected_at_finish(self):
        adapter, event = await self.begin()
        other = self.add_other()
        cid = str(uuid.uuid4())
        payload = {'text': 'Old pre-upgrade audit.', 'receipt_id': other,
                   'processed': True, 'client_id': cid}
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['3'].update(
            explicit_post_calls={cid: {'payload': payload}}))
        await self.assert_finished_without_other_read(adapter, event, other)
        call = self.client.store.room('a')['processing_receipts']['3']['explicit_post_calls'][cid]
        self.assertEqual(call['rejected']['code'], 'receipt_expired')
        self.assertEqual(call['payload'], payload)


if __name__ == '__main__':
    unittest.main()
