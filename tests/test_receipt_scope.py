"""Opus 11j probe made assertive: unread other inputs are never attestable."""
import copy
import time
import unittest
from unittest.mock import Mock, patch

import test_receipt_handles as handles
import test_threaded_post_guidance as prior
from test_plugin import tools
from hermes_botsesh_test import receipt_handles
from gateway.session_context import set_session_vars, clear_session_vars

OTHER = 'ad961e50-7765-4892-8df8-ea6e76a49676'
LOCAL = 'bc687dba-656d-4e57-b8a5-3427fabbe6f6'


class ReceiptScopeTests(handles.ReceiptHandleTests):
    def local_call(self, name, args):
        with patch.object(tools, 'Client', return_value=self.client), patch.object(
                tools, 'calling_scope', return_value=('local', '')):
            import json
            return json.loads(tools.guarded(lambda payload: getattr(tools, name)(payload, Mock()))(
                {'room_id': prior.ROOM, **args}))

    def add_local(self, rid=LOCAL, number=7):
        self.client.store.update(lambda d: d['rooms'][prior.ROOM].setdefault('oauth_receipts', {}).update({rid: {
            'numbers': [number], 'participant_id': 'offline-Bo', 'expires': time.time() + 3600}}))

    def test_opus_probe_other_open_input_handle_refused_without_attestation(self):
        self.read_native()
        self.add_receipt(OTHER, 3, number=3)  # This briefing was NOT supplied to turn #0.
        bad = self.call({'text': 'hello', 'receipt_id': 'bogus', 'processed': True})
        denied = self.call({'text': 'hello', 'receipt_id': 'r2', 'processed': True})
        self.assertFalse(denied['ok'], denied)
        self.assertNotIn('message #3', bad['message'])
        self.assertNotIn('r2:', bad['message'])
        self.assertIn('r1: message #0', bad['message'])
        self.assertEqual(denied['error'], 'receipt_expired')
        self.assertIn('r1: message #0', denied['message'])
        self.assert_unread()
        self.assertNotIn('posted_reply', self.client.store.room(prior.ROOM)['processing_receipts']['3'])

    def test_other_input_full_uuid_refused_even_with_explicit_processed(self):
        self.read_native()
        self.add_receipt(OTHER, 3, number=3)
        before = copy.deepcopy(self.client.store.room(prior.ROOM)['processing_receipts']['3'])
        denied = self.call({'text': 'hello', 'receipt_id': OTHER, 'processed': True})
        self.assertFalse(denied['ok'], denied)
        self.assertEqual(denied['error'], 'receipt_expired')
        self.assertIn('r1: message #0', denied['message'])
        self.assertNotIn('message #3', denied['message'])
        self.assert_unread()
        self.assertEqual(self.client.store.room(prior.ROOM)['processing_receipts']['3'], before)
        self.assertFalse(self.client.store.room(prior.ROOM).get('oauth_posts'))

    def test_native_cannot_borrow_local_receipt_or_an_old_cross_input_alias(self):
        self.add_local()
        self.add_receipt(OTHER, 3, number=3)
        self.client.store.update(lambda d: d['rooms'][prior.ROOM]['processing_receipts']['0'].update(
            model_receipt_handles={'r1': prior.RECEIPT, 'r2': OTHER, 'r3': LOCAL}))
        for supplied in ('r2', 'r3', LOCAL):
            with self.subTest(supplied=supplied):
                denied = self.call({'text': 'hello', 'receipt_id': supplied, 'processed': True})
                self.assertFalse(denied['ok'], denied)
                self.assertNotIn('message #3', denied['message'])
                self.assertNotIn('message #7', denied['message'])
                self.assert_unread()

    def test_cross_input_thread_hint_is_retired_not_recommended(self):
        self.read_native()
        self.add_receipt(OTHER, 3, number=3)
        denied = self.call({'text': 'hello', 'reply_to': 3})
        self.assertFalse(denied['ok'], denied)
        self.assertNotIn(OTHER, denied['message'])
        self.assertNotIn('r2', denied['message'])
        self.assertIn('No complete open receipt for this target', denied['message'])
        self.assert_unread()

    def test_local_handles_include_only_local_reads_and_never_native_uuids(self):
        self.add_receipt(OTHER, 3, number=3)
        # A stale row pointer must not reclassify its native frozen packet as
        # a local read. Both recorded and frozen receipt identities stay fenced.
        self.client.store.update(lambda d: d['rooms'][prior.ROOM]['processing_receipts']['3'].update(
            receipt_id='stale-pointer'))
        self.add_local()
        room = self.client.store.room(prior.ROOM)
        options = receipt_handles.choices(room)
        self.assertEqual([(c['receipt_id'], c['uuid']) for c in options], [('r1', LOCAL)])
        for supplied in (prior.RECEIPT, OTHER, 'r2'):
            with self.subTest(supplied=supplied):
                denied = self.local_call('post', {'text': 'hello', 'receipt_id': supplied, 'processed': True})
                self.assertFalse(denied['ok'], denied)
                self.assertIn('message #7', denied['message'])
                self.assertNotIn('message #0', denied['message'])
                self.assertNotIn('message #3', denied['message'])
                self.assert_unread()
        landed = self.local_call('post', {'text': 'local reply', 'receipt_id': 'r1', 'processed': True})
        self.assertTrue(landed['ok'], landed)
        self.assertEqual(self.wire[-1][1]['batch_id'], LOCAL)
        self.assertNotIn('read_attested', self.client.store.room(prior.ROOM)['processing_receipts']['3'])

    def test_local_pass_cannot_attest_native_full_uuid(self):
        denied = self.local_call('pass_turn', {'receipt_id': prior.RECEIPT, 'processed': True})
        self.assertFalse(denied['ok'], denied)
        self.assertIn('No valid open handles', denied['message'])
        self.assert_unread()

    def test_native_pass_rejects_other_input_handle_and_full_uuid(self):
        self.read_native()
        self.add_receipt(OTHER, 3, number=3)
        for supplied in ('r2', OTHER):
            with self.subTest(supplied=supplied):
                tokens = set_session_vars(platform='botsesh', chat_id=prior.ROOM, message_id='0')
                try:
                    import json
                    with patch.object(tools, 'Client', return_value=self.client), patch.object(
                            tools, 'calling_scope', return_value=('botsesh', prior.ROOM)):
                        denied = json.loads(tools.guarded(lambda payload: tools.pass_turn(payload, Mock()))(
                            {'room_id': prior.ROOM, 'receipt_id': supplied, 'processed': True}))
                finally:
                    clear_session_vars(tokens)
                self.assertFalse(denied['ok'], denied)
                self.assertIn('r1: message #0', denied['message'])
                self.assertNotIn('message #3', denied['message'])
                self.assert_unread()

    def test_advanced_uuid_compatibility_cannot_bypass_native_input_scope(self):
        self.add_receipt(OTHER, 3, number=3)
        def legacy(data):
            room = data['rooms'][prior.ROOM]
            room.pop('transport')
            room['url'] = 'https://botsesh.com/b/' + prior.ROOM
            room['oauth_receipts'] = {}  # The SDK, not Python, owns this metadata.
        self.client.store.update(legacy)
        room = self.client.store.room(prior.ROOM)
        self.assertTrue(receipt_handles.available(room, '0', prior.RECEIPT))
        self.assertFalse(receipt_handles.available(room, '', prior.RECEIPT))
        self.assertFalse(receipt_handles.available(room, '0', OTHER))
        self.assertFalse(receipt_handles.available(room, None, OTHER))
        self.assertFalse(receipt_handles.available(room, '0', 'r2'))
        with patch.object(self.client, 'call', side_effect=AssertionError('No bridge mutation')) as bridge:
            for supplied in (OTHER, 'r2'):
                denied = self.call({'text': 'hello', 'receipt_id': supplied, 'processed': True})
                self.assertFalse(denied['ok'], denied)
                self.assertEqual(denied['error'], 'receipt_expired')
                denied = self.local_call('post', {'text': 'hello', 'receipt_id': supplied, 'processed': True})
                self.assertFalse(denied['ok'], denied)
            bridge.assert_not_called()
        self.assert_unread()

    def test_missing_native_input_cannot_attest_even_a_known_uuid(self):
        denied = self.call({'text': 'hello', 'receipt_id': prior.RECEIPT, 'processed': True}, target='')
        self.assertFalse(denied['ok'], denied)
        self.assertEqual(denied['error'], 'receipt_expired')
        self.assert_unread()

    def test_local_mapping_prunes_dead_receipts_without_reusing_labels(self):
        self.add_local()
        room = self.client.store.room(prior.ROOM)
        # Simulate the persisted pre-fix mapping, which has no counter yet.
        room['model_receipt_handles'] = {'r1': 'expired-old-read', 'r8': LOCAL}
        self.assertEqual([c['receipt_id'] for c in receipt_handles.choices(room)], ['r8'])
        self.assertEqual(room['model_receipt_handles'], {'r8': LOCAL})
        room['oauth_receipts'][LOCAL]['expires'] = 0
        self.assertEqual(receipt_handles.choices(room), [])
        for n in range(20):
            rid = 'fresh-local-' + str(n)
            room['oauth_receipts'][rid] = {'numbers': [n], 'participant_id': room['participant_id'], 'expires': time.time() + 3600}
            result = receipt_handles.choices(room)
            self.assertEqual(result[0]['receipt_id'], 'r' + str(n + 9))
            self.assertEqual(len(room['model_receipt_handles']), 1)
            room['oauth_receipts'][rid]['expires'] = 0
            self.assertEqual(receipt_handles.choices(room), [])
            self.assertEqual(room['model_receipt_handles'], {})


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ReceiptScopeTests(name) for name in ReceiptScopeTests.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
