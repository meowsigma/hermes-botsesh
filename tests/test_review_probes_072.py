"""Reviewer adversarial probes for 0.7.2 (t_73ed6a3e). Wire double only."""
import copy
import json
import uuid
import unittest
from unittest.mock import Mock, patch

import test_forgiving_post as fp
from hermes_botsesh_test import tools
from gateway.session_context import set_session_vars, clear_session_vars


class ReviewProbes(fp.ForgivingPostTests):
    def post_as(self, payload, *, target='3', room='a', surface='botsesh'):
        tokens = set_session_vars(platform=surface, chat_id=room, message_id=target)
        try:
            with patch.object(tools, 'Client', return_value=self.client):
                ctx = Mock()
                ctx.session_surface.return_value = surface
                return json.loads(tools.guarded(lambda args: tools.post(args, ctx))(payload))
        finally:
            clear_session_vars(tokens)

    def sends(self):
        return [a for _, n, a in self.requests if n == 'send_message']

    def assert_no_read(self, result):
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['read_effect'], 'none')
        self.assertFalse(self.sends())
        for row in self.client.store.room('a')['processing_receipts'].values():
            self.assertNotIn('read_attested', row)

    async def mutate_and_post(self, mutate, **kw):
        await self.begin()
        changed = copy.deepcopy(self.client.store.room('a'))
        mutate(changed)
        self.client.store.update(lambda d: d['rooms'].update(a=changed))
        return self.post_as({'text': 'Probe.', 'processed': True}, **kw)

    # --- receipt attachment boundaries ---
    async def test_P01_own_message_target_never_attaches(self):
        def m(r):
            for msg in r['processing_receipts']['3']['frozen']['batch']['messages']:
                msg['isOwn'] = True
        self.assert_no_read(await self.mutate_and_post(m))

    async def test_P02_target_absent_from_frozen_batch(self):
        def m(r):
            rid = r['processing_receipts']['3']['receipt_id']
            r['processing_receipts']['3']['frozen']['batch']['messages'][0]['number'] = 2
            r['oauth_receipts'][rid]['numbers'] = [2]
        self.assert_no_read(await self.mutate_and_post(m))

    async def test_P03_pre_072_receipt_without_participant(self):
        def m(r):
            rid = r['processing_receipts']['3']['receipt_id']
            r['oauth_receipts'][rid].pop('participant_id', None)
        self.assert_no_read(await self.mutate_and_post(m))

    async def test_P04_room_participant_changed_after_fetch(self):
        await self.mutate_and_post(lambda r: r.update(participant_id='bot-other'))
        self.assertFalse(self.sends())  # fixture author check may reject; never a receipt send
        for row in self.client.store.room('a')['processing_receipts'].values():
            self.assertNotIn('read_attested', row)

    async def test_P05_inactive_room(self):
        res = await self.mutate_and_post(lambda r: r.update(active=False))
        print('\nP05', res)
        # Either refused or posted without read; never attested.
        for row in self.client.store.read()['rooms']['a']['processing_receipts'].values():
            self.assertNotIn('read_attested', row)
        self.assertFalse(self.sends())

    async def test_P06_explicit_failure_row(self):
        self.assert_no_read(await self.mutate_and_post(
            lambda r: r['processing_receipts']['3'].update(explicit_failure={'x': 1})))

    async def test_P07_frozen_receipt_differs_from_row(self):
        self.assert_no_read(await self.mutate_and_post(
            lambda r: r['processing_receipts']['3']['frozen'].update(receipt_id='other')))

    async def test_P08_non_native_surface_never_attaches(self):
        await self.begin()
        res = self.post_as({'room_id': 'a', 'text': 'cli', 'processed': True}, surface='cli')
        self.assert_no_read(res)

    async def test_P09_missing_session_target_never_attaches(self):
        await self.begin()
        self.assert_no_read(self.post_as({'text': 'no target', 'processed': True}, target=''))

    async def test_P10_cross_room_argument_denied(self):
        await self.begin()
        await self.begin('b')
        res = self.post_as({'room_id': 'a', 'text': 'x', 'processed': True}, room='b')
        self.assertFalse(res['ok'])
        self.assertEqual(res['error'], 'cross_room_denied')
        self.assertEqual(self.messages, {})

    async def test_P11_other_turn_target_in_same_room(self):
        # Session says target 5 (not the admitted input): must not borrow 3's receipt.
        await self.begin()
        self.assert_no_read(self.post_as({'text': 'wrong turn', 'processed': True}, target='5'))

    # --- unseen messages ---
    async def test_P12_later_messages_not_attested(self):
        await self.begin()
        self.targets['a'] = [3, 4, 5]
        res = self.post_as({'text': 'answer', 'processed': True})
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['result']['read_effect'], 'processed_frozen_receipt')
        sends = self.sends()
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]['batch_id'], 'receipt-a-3')
        rid = self.client.store.room('a')['processing_receipts']['3']['receipt_id']
        self.assertEqual(self.client.store.room('a')['oauth_receipts'][rid]['numbers'], [3])

    # --- supplied receipts never replaced ---
    async def test_P13_supplied_bogus_receipt_not_replaced(self):
        await self.begin()
        res = self.post_as({'text': 'x', 'processed': True, 'receipt_id': 'made-up'})
        self.assertFalse(res['ok'])
        self.assertEqual(res['error'], 'receipt_expired')
        self.assertIn('Do not guess', res['message'])
        self.assertEqual(self.messages, {})

    async def test_P14_empty_string_receipt_is_treated_as_missing_only(self):
        await self.begin()
        res = self.post_as({'text': 'x', 'processed': True, 'receipt_id': ''})
        # Empty receipt == omitted; may resolve the current frozen receipt only.
        if res['ok'] and res['result']['read_effect'] != 'none':
            self.assertEqual(self.sends()[0]['batch_id'], 'receipt-a-3')

    # --- replay / mismatch ---
    async def test_P15_replay_changed_fields_mismatch(self):
        await self.begin()
        cid = str(uuid.uuid4())
        first = self.post_as({'text': 'A', 'processed': True, 'client_id': cid})
        self.assertTrue(first['ok'], first)
        for variant in ({'text': 'A', 'client_id': cid},
                        {'text': 'A', 'processed': True, 'client_id': cid, 'receipt_id': 'receipt-a-3'},
                        {'text': 'A', 'processed': True, 'client_id': cid, 'reply_to': 3}):
            with self.subTest(variant=variant):
                res = self.post_as(variant)
                self.assertFalse(res['ok'], res)
                self.assertEqual(res['error'], 'pending_send_mismatch')
        self.assertEqual(len(self.messages), 1)

    async def test_P16_fallback_replay_stays_unattested_after_receipt_appears(self):
        # First call has no provable receipt (target 999) -> ordinary post.
        # Replaying the same UUID inside the real turn must NOT now attach.
        await self.begin()
        cid = str(uuid.uuid4())
        first = self.post_as({'text': 'B', 'processed': True, 'client_id': cid}, target='999')
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['result']['read_effect'], 'none')
        again = self.post_as({'text': 'B', 'processed': True, 'client_id': cid}, target='3')
        self.assertTrue(again['ok'], again)
        self.assertEqual(again['result']['read_effect'], 'none')
        self.assertFalse(self.sends())
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        self.assertEqual(len(self.messages), 1)

    async def test_P18_cross_context_uuid_reuse_then_turn_settles(self):
        adapter, _ = await self.begin()
        cid = str(uuid.uuid4())
        first = self.post_as({'text': 'B', 'processed': True, 'client_id': cid}, target='999')
        again = self.post_as({'text': 'B', 'processed': True, 'client_id': cid}, target='3')
        print('\nP18 first', first.get('ok'), first.get('result', first.get('error')))
        print('P18 again', again.get('ok'), again.get('result', again.get('error')))
        print('P18 row3 calls', self.client.store.room('a')['processing_receipts']['3'].get('explicit_post_calls'))
        self.model_ended(adapter)
        try:
            await adapter._finish_current('a', adapter._current['a'], 'empty_response')
            print('P18 finish ok')
        except Exception as e:
            print('P18 finish raised', type(e).__name__, e)
        for attempt in range(3):
            try:
                await adapter._check_room('a')
            except Exception as e:
                print('P18 check raised', type(e).__name__, e)
        row = self.client.store.room('a')['processing_receipts']['3']
        print('P18 row3 state', row.get('state'), 'current', adapter._current.get('a', {}).get('target'))
        print('P18 pending_completion', self.client.store.room('a').get('pending_completion'))
        self.assertNotIn('read_attested', row)
        self.assertFalse(self.sends())
        # Liveness: the native turn must settle and the next target must start.
        self.assertIn(row.get('state'), ('failed', 'completed_unread', 'replied', 'passed'))
        self.assertEqual(adapter._current.get('a', {}).get('target'), 5)

    async def test_P17_structured_declaration_dropped_on_fallback(self):
        await self.begin()
        res = self.post_as({'text': 'C', 'processed': True,
                            'draft_ready': {'title': 'T'}}, target='999')
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['result']['read_effect'], 'none')
        self.assertFalse([n for _, n, _ in self.requests if n == 'finish_room_turn'])


if __name__ == '__main__':
    unittest.main()
