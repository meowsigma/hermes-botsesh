"""Zero-inference 11i literal failures through native registered dispatch."""
import copy
import json
from unittest.mock import Mock, patch
import unittest
import test_threaded_post_guidance as prior
import test_final_after_post as native
import test_confirmed_pass as quiet
from test_plugin import tools
from gateway.session_context import set_session_vars, clear_session_vars

ARI_RECEIPT = '398f95b0-595b-41c5-9c56-886a3aa5d9b8'
MALFORMED = '398f95b0-595b-41c5-9c-886a3aa5d9b8'
ARI_CALLS = [
    {'text': 'The workshop needs 12 attendees and 90 minutes. I don’t have Harbour Room availability or facility details, so Bo agent will need to confirm whether it’s available Saturday and suggest a time that fits a 90-minute booking.', 'reply_to': 0, 'receipt_id': MALFORMED, 'processed': True},
    {'processed': True, 'receipt_id': MALFORMED, 'reply_to': 0, 'text': 'The workshop needs 12 attendees and 90 minutes. I don’t have Harbour Room availability or facility details, so Bo agent will need to confirm whether it’s available Saturday and suggest a time that fits a 90-minute booking.'},
]

class ReceiptHandleTests(prior.ThreadedPostGuidance):
    def read_native(self):
        tokens = set_session_vars(platform='botsesh', chat_id=prior.ROOM, message_id='0')
        try:
            with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('botsesh', prior.ROOM)):
                return tools.read({}, Mock())
        finally:
            clear_session_vars(tokens)

    def test_read_projects_only_protocol_labels_and_never_attests_or_mutates_frozen_bytes(self):
        def decorate(data):
            frozen = data['rooms'][prior.ROOM]['processing_receipts']['0']['frozen']
            frozen['briefing'] = [{'batch_id': prior.RECEIPT, 'messages': [{'text': prior.RECEIPT}]}]
            frozen['batch']['messages'][0]['text'] = 'Do not replace ' + prior.RECEIPT
        self.client.store.update(decorate)
        before = copy.deepcopy(self.client.store.room(prior.ROOM)['processing_receipts']['0']['frozen'])
        projected = self.read_native()
        self.assertEqual(projected['receipt_id'], 'r1')
        self.assertEqual(projected['briefing'][0]['batch_id'], 'r1')
        self.assertEqual(projected['briefing'][0]['messages'], before['briefing'][0]['messages'])
        self.assertEqual(projected['batch'], before['batch'])
        self.assertEqual(projected['read_effect'], 'none')
        self.assertEqual(self.client.store.room(prior.ROOM)['processing_receipts']['0']['frozen'], before)
        self.assert_unread()

    def test_handle_does_not_infer_processed_and_full_id_remains_compatible(self):
        self.read_native()
        incomplete = self.call({**prior.FIXTURE['calls'][0], 'receipt_id': 'r1'})
        self.assertFalse(incomplete['ok'], incomplete)
        self.assert_unread()
        full = self.call({**prior.FIXTURE['calls'][0], 'receipt_id': prior.RECEIPT, 'processed': True})
        self.assertTrue(full['ok'], full)
        self.assertEqual(self.wire[-1][1]['batch_id'], prior.RECEIPT)

    def test_correction_lists_three_handles_and_excludes_unavailable_receipts_without_recycling(self):
        self.read_native()
        for n in range(1, 6): self.add_receipt('other-' + str(n), n, number=n)
        def invalidate(data):
            room = data['rooms'][prior.ROOM]
            room['oauth_receipts']['other-1']['participant_id'] = 'foreign'
            room['oauth_receipts']['other-2']['expires'] = 0
        self.client.store.update(invalidate)
        result = self.call({**ARI_CALLS[0], 'receipt_id': 'r999'})
        self.assertEqual(result['error'], 'receipt_expired')
        for label in ('r1: message #0', 'r2: message #3', 'r3: message #4'):
            self.assertIn(label, result['message'])
        self.assertNotIn('message #5', result['message'])
        self.assertNotIn('message #1', result['message'])
        self.assertNotIn('message #2', result['message'])
        self.assert_unread()
        self.client.store.update(lambda d: d['rooms'][prior.ROOM]['oauth_receipts'][prior.RECEIPT].update(expires=0))
        expired = self.call({**ARI_CALLS[0], 'receipt_id': 'r1'})
        self.assertFalse(expired['ok'])
        self.assertNotIn('r1:', expired['message'])
        self.assertIn('r2: message #3', expired['message'])
        self.assert_unread()

    def test_unknown_receipt_without_processed_still_lists_handles_without_replacing_it(self):
        request = {**ARI_CALLS[0], 'processed': False}
        denied = self.call(request)
        self.assertFalse(denied['ok'], denied)
        self.assertIn('r1: message #0', denied['message'])
        self.assertIn(MALFORMED, denied['message'], 'The suggested call must not repair a supplied token')
        self.assert_unread()
        self.assertEqual(self.wire, [])

    def test_exact_handle_retry_replays_original_uuid_after_later_turn_and_changed_fields_fail(self):
        import uuid
        fixed = {**prior.FIXTURE['calls'][0], 'receipt_id': 'r1', 'processed': True, 'client_id': str(uuid.uuid4())}
        first = self.call(fixed)
        self.assertTrue(first['ok'], first)
        self.add_receipt('later-frozen-receipt', 1, number=1)
        replay = self.call(fixed, target='1')
        self.assertTrue(replay['ok'], replay)
        self.assertEqual(len(self.wire), 1)
        self.assertEqual(self.wire[0][1]['batch_id'], prior.RECEIPT)
        changed = self.call({**fixed, 'text': 'altered'}, target='1')
        self.assertEqual(changed['error'], 'pending_send_mismatch')
        self.assertEqual(len(self.wire), 1)

    def test_ari_literal_malformed_calls_teach_handle_and_same_calls_with_handle_land(self):
        self.client.store.update(lambda d: d['rooms'][prior.ROOM].update(oauth_receipts={}, processing_receipts={}))
        self.add_receipt(ARI_RECEIPT, 0)
        original = copy.deepcopy(self.client.store.room(prior.ROOM))
        for request in ARI_CALLS:
            with self.subTest(request=request):
                self.client.store.update(lambda d: d['rooms'].update({prior.ROOM: copy.deepcopy(original)}))
                self.wire.clear()
                denied = self.call(request)
                self.assertEqual(denied['error'], 'receipt_expired', denied)
                self.assertIn('r1', denied['message'])
                self.assertIn('message #0', denied['message'])
                self.assertNotIn(ARI_RECEIPT, denied['message'])
                self.assert_unread()
                fixed = {**request, 'receipt_id': 'r1'}
                landed = self.call(fixed)
                self.assertTrue(landed['ok'], landed)
                self.assertEqual(landed['result']['read_effect'], 'processed_frozen_receipt')
                self.assertEqual(self.wire[-1][1]['batch_id'], ARI_RECEIPT)
                self.assertEqual(self.wire[-1][1]['reply_to'], request['reply_to'])
                self.assertEqual(self.wire[-1][1]['text'], request['text'])
                row = self.client.store.room(prior.ROOM)['processing_receipts']['0']
                rejected = [c for c in row['explicit_post_calls'].values() if c['payload'].get('receipt_id') == MALFORMED]
                self.assertTrue(rejected, 'Keep the exact malformed authored token')


class NativeHandleTests(native.FinalAfterPostTests):
    async def test_native_packet_uses_handle_but_start_end_and_turn_identity_keep_exact_uuid(self):
        adapter, event = await self.begin()
        packet = json.JSONDecoder().raw_decode(event.text)[0]
        self.assertEqual(packet['receipt_id'], 'r1')
        self.assertEqual(packet['briefing'][0]['batch_id'], 'r1')
        self.assertEqual(adapter._current['a']['receipt_id'], 'receipt-a-3')
        self.model_ended(adapter)
        self.assertTrue(adapter._current['a']['model_processed'])
        self.assertEqual(adapter._current['a']['hook_turn'], ('a', 'turn'))


class PassHandleTests(quiet.ConfirmedPassTests):
    async def test_handle_pass_requires_attestation_and_interrupts_only_after_exact_confirmed_journal(self):
        adapter, args = await self.prepare()
        for update in ({'processed': False}, {'receipt_id': 'r99'}):
            denied, agent, _ = self.core_call({**args, 'receipt_id': 'r1', **update})
            self.assertFalse(denied['ok'])
            agent.interrupt.assert_not_called()
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))
        passed, agent, _ = self.core_call({**args, 'receipt_id': 'r1'})
        self.assertTrue(passed['ok'], passed)
        agent.interrupt.assert_called_once_with(hard_cancel=True, tool_reason='botsesh_passed')
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['explicit_pass']['payload']['receipt_id'], args['receipt_id'])
        self.assertEqual(self.messages, {})


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(cls(name) for cls in (ReceiptHandleTests, NativeHandleTests, PassHandleTests)
                              for name in cls.__dict__ if name.startswith('test_'))

if __name__ == '__main__': unittest.main()
