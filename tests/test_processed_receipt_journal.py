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

    def post(self, payload, room='a'):
        with patch.object(tools, 'Client', return_value=self.client), \
             patch.object(tools, 'calling_scope', return_value=('botsesh', room)):
            return tools.post({'room_id': room, **payload}, Mock())

    def payload(self, **options):
        return {'text': 'Exact authored bytes.\ncafé 🐚\n',
                'client_id': str(uuid.uuid4()), 'processed': True, **options}

    def calls(self, room='a', target='3'):
        return self.client.store.room(room)['processing_receipts'][target].get('explicit_post_calls', {})

    def sends(self):
        return [args for _, name, args in self.requests if name in ('send_message', 'hermes_authored')]

    async def test_missing_receipt_is_settled_then_valid_reply_and_later_wake(self):
        adapter, event = await self.begin()
        payload = self.payload(reply_to=3)
        with self.assertRaises(BotSeshError) as error:
            self.post(payload)
        self.assertEqual(error.exception.code, 'processed_receipt_required')
        self.assertTrue(error.exception.definitive, 'local pre-send validation must not become an uncertain send')
        self.assertIsNone(error.exception.pending)
        self.assertEqual(self.calls()[payload['client_id']]['rejected']['code'], 'processed_receipt_required')
        self.assertEqual(self.client.store.room('a').get('oauth_posts', {}), {})
        self.assertEqual(self.sends(), [])
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        receipt = adapter._current['a']['receipt_id']
        # Validation never grants permission to change an already authored UUID.
        with self.assertRaises(BotSeshError) as mismatch:
            self.post({**payload, 'receipt_id': receipt})
        self.assertEqual(mismatch.exception.code, 'pending_send_mismatch')
        valid = {**payload, 'client_id': str(uuid.uuid4()), 'receipt_id': receipt}
        result = self.post(valid)
        self.assertEqual(result['read_effect'], 'processed_frozen_receipt')
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(len(self.messages), 1)
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
        self.assertEqual(row['state'], 'completed_unread')
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
                self.assertNotIn('rejected', self.calls()[payload['client_id']])
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
