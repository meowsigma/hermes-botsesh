"""Owner-authorized 0.7.2 missing-bookkeeping contract; wire double only."""
import copy
import unittest
import uuid
from unittest.mock import Mock, patch

import test_final_after_post as fixture
from hermes_botsesh_test import tools
from gateway.session_context import set_session_vars, clear_session_vars


class ForgivingPostTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.FinalAfterPostTests.asyncSetUp
    begin = fixture.FinalAfterPostTests.begin
    model_ended = fixture.FinalAfterPostTests.model_ended

    def post(self, payload, *, target='3', room='a'):
        tokens = set_session_vars(platform='botsesh', chat_id=room, message_id=target)
        try:
            with patch.object(tools, 'Client', return_value=self.client):
                ctx = Mock()
                ctx.session_surface.return_value = 'botsesh'
                return tools.guarded(lambda args: tools.post(args, ctx))(payload)
        finally:
            clear_session_vars(tokens)

    async def test_three_missing_receipt_posts_land_instead_of_three_strikes(self):
        import json
        await self.begin()
        outcomes = [json.loads(self.post({'text': 'LANTERN is the codename.',
                     'processed': True})) for _ in range(3)]
        self.assertEqual([r.get('ok') for r in outcomes], [True, True, True], outcomes)
        self.assertEqual(len(self.messages), 3)
        self.assertTrue(all(r['result']['read_effect'] == 'processed_frozen_receipt' for r in outcomes))

    async def test_no_current_turn_posts_without_read_or_reply_inference(self):
        import json
        await self.begin()
        result = json.loads(self.post({'text': 'Ordinary authored message.', 'processed': True}, target='999'))
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['result']['read_effect'], 'none')
        self.assertIn('did not advance read state', result['result']['notice'])
        self.assertFalse(any(name == 'send_message' for _, name, _ in self.requests))
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])

    async def test_foreign_agent_expiry_incomplete_and_terminal_never_attach(self):
        import json
        await self.begin()
        original = self.client.store.room('a')
        receipt = original['processing_receipts']['3']['receipt_id']
        variants = [
            lambda r: r['oauth_receipts'][receipt].update(participant_id='other-agent'),
            lambda r: r['oauth_receipts'][receipt].update(expires=0),
            lambda r: r['processing_receipts']['3']['frozen']['batch'].update(hasMore=True),
            lambda r: r['processing_receipts']['3'].update(state='replied'),
            lambda r: r['processing_receipts']['3'].update(receipt_id='wrong-turn'),
            lambda r: r['processing_receipts'].update(duplicate=copy.deepcopy(r['processing_receipts']['3'])),
            lambda r: r['oauth_receipts'][receipt].update(numbers=[3, 999]),
            lambda r: r['processing_receipts']['3'].update(explicit_pass={'confirmed': True}),
        ]
        for mutate in variants:
            with self.subTest(mutation=variants.index(mutate)):
                changed = copy.deepcopy(original)
                mutate(changed)
                self.client.store.update(lambda d: d['rooms'].update(a=changed))
                result = json.loads(self.post({'text': 'Keep my message.', 'processed': True}))
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['result']['read_effect'], 'none')
                self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        self.assertFalse(any(name == 'send_message' for _, name, _ in self.requests))

    async def test_retry_keeps_original_resolution_after_turn_changes(self):
        import json
        await self.begin()
        payload = {'text': 'Exact bytes. café 🐚\n', 'processed': True, 'client_id': str(uuid.uuid4())}
        first = json.loads(self.post(payload))
        self.assertTrue(first['ok'], first)
        original = copy.deepcopy(self.client.store.room('a')['oauth_posts'])
        second = json.loads(self.post(payload, target='5'))
        self.assertTrue(second['ok'], second)
        self.assertEqual({k: v for k, v in second['result'].items() if k != 'notice'},
                         {k: v for k, v in first['result'].items() if k != 'notice'})
        self.assertIn('no newer turn input', second['result']['notice'])
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.client.store.room('a')['oauth_posts'], original)
        changed = json.loads(self.post({**payload, 'text': 'Changed'}, target='5'))
        self.assertEqual(changed['error'], 'pending_send_mismatch')

    async def test_lost_auto_attached_response_replays_exact_frozen_send(self):
        import json
        adapter, _ = await self.begin()
        payload = {'text': 'Exact useful answer.', 'processed': True, 'client_id': str(uuid.uuid4())}
        self.lose_send = True
        first = json.loads(self.post(payload))
        self.assertFalse(first['ok'])
        self.assertEqual(first['pending']['client_id'], payload['client_id'])
        original = copy.deepcopy(self.client.store.room('a')['oauth_posts'][payload['client_id']]['exact'])
        self.assertIn('receipt_id', original)
        second = json.loads(self.post(payload, target='5'))
        self.assertTrue(second['ok'], second)
        self.assertEqual(len(self.messages), 1)
        sends = [args for _, name, args in self.requests if name == 'send_message']
        self.assertEqual(sends, [sends[0], sends[0]])
        self.assertEqual(self.client.store.room('a')['oauth_posts'][payload['client_id']]['exact'], original)
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['5'])

    async def test_replay_in_real_later_turn_is_audited_without_reading_or_duplicate_final(self):
        import json
        adapter, _ = await self.begin()
        payload = {'text': 'Original answer.', 'processed': True, 'client_id': str(uuid.uuid4())}
        first = json.loads(self.post(payload))
        self.assertTrue(first['ok'], first)
        old_receipt = self.client.store.room('a')['processing_receipts']['3']['receipt_id']
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)
        for _ in range(2):
            replay = json.loads(self.post(payload, target='5'))
            self.assertTrue(replay['ok'], replay)
        row = self.client.store.room('a')['processing_receipts']['5']
        self.assertEqual(row['explicit_post_calls'][payload['client_id']]['payload']['receipt_id'], old_receipt)
        self.assertNotIn('read_attested', row)
        self.assertNotIn('posted_reply', row)
        self.assertEqual(len(self.messages), 1)
        self.model_ended(adapter)
        final = await adapter.send('a', 'Do not duplicate this final.', metadata={'notify': True})
        self.assertTrue(final.success, final.error)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        self.assertEqual(self.client.store.room('a')['processing_receipts']['5']['state'], 'completed_unread')
        self.assertEqual(len(self.messages), 1)

    async def test_plain_post_does_not_infer_processed_and_revocation_still_denies(self):
        import json
        await self.begin()
        plain = json.loads(self.post({'text': 'Ordinary content.'}))
        self.assertTrue(plain['ok'], plain)
        self.assertEqual(plain['result']['read_effect'], 'none')
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        self.revoked.add('a')
        denied = json.loads(self.post({'text': 'Must not post.', 'processed': True}))
        self.assertFalse(denied['ok'])
        self.assertEqual(denied['error'], 'authorization_revoked')
        self.assertEqual(len(self.messages), 1)

    async def test_legacy_missing_receipt_thread_is_rejected_not_silently_unthreaded(self):
        # t_d31c619a parent decision supersedes 96ec090's disclosed unthreading.
        import json
        await self.begin()
        self.client.store.update(lambda d: d['rooms']['a'].update(transport='invitation'))
        payload = {'text': 'Exact bytes. café 🐚\n', 'processed': True, 'reply_to': 3}
        with patch.object(self.client, 'enrolled') as wire:
            result = json.loads(self.post(payload, target='999'))
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'], 'unattested_reply_target_not_supported')
        self.assertIn('receipt-a-3', result['message'])
        wire.assert_not_called()
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])

    async def test_explicit_foreign_receipt_is_not_replaced(self):
        import json
        await self.begin()
        await self.begin('b')
        foreign = self.client.store.room('b')['processing_receipts']['3']['receipt_id']
        before = copy.deepcopy(self.client.store.room('b'))
        result = json.loads(self.post({'text': 'Do not send.', 'processed': True, 'receipt_id': foreign}))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'receipt_expired')
        self.assertEqual(self.messages, {})
        self.assertEqual(self.client.store.room('b'), before)


if __name__ == '__main__':
    unittest.main()
