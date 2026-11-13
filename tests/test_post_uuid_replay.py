"""Cross-context durable UUID recovery; composed tools/client/native wire double."""
import copy
import json
import unittest
import uuid
from unittest.mock import patch

import test_forgiving_post as fixture
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test.oauth import OAuthClient


class PostUUIDReplayTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ForgivingPostTests.asyncSetUp
    begin = fixture.ForgivingPostTests.begin
    model_ended = fixture.ForgivingPostTests.model_ended
    post = fixture.ForgivingPostTests.post

    async def settle(self, adapter, *, state='completed_unread'):
        self.model_ended(adapter)
        await adapter._finish_current('a', adapter._current['a'], 'empty_response')
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['state'], state)
        self.assertNotIn('read_attested', row)
        self.assertFalse([name for _, name, _ in self.requests if name == 'send_message'])
        await adapter._check_room('a')
        self.assertEqual(adapter._current['a']['target'], 5)

    async def test_changed_cross_context_retry_rejects_without_poisoning_original(self):
        adapter, _ = await self.begin()
        originals = []
        for variant in ({'text': 'Changed'}, {'processed': False},
                        {'receipt_id': 'receipt-a-3'}, {'reply_to': 3},
                        {'draft_ready': {'title': 'Changed'}}):
            payload = {'text': 'Exact café 🐚\n', 'processed': True, 'client_id': str(uuid.uuid4())}
            first = json.loads(self.post(payload, target='999'))
            self.assertTrue(first['ok'], first)
            saved = copy.deepcopy(self.client.store.room('a')['oauth_posts'][payload['client_id']])
            changed = json.loads(self.post({**payload, **variant}))
            self.assertEqual(changed['error'], 'pending_send_mismatch')
            self.assertNotIn('pending', changed)
            call = self.client.store.room('a')['processing_receipts']['3']['explicit_post_calls'][payload['client_id']]
            self.assertEqual(call['payload'], {**payload, **variant})
            self.assertTrue(call['intent_mismatch'])
            self.assertEqual(call['rejected']['code'], 'pending_send_mismatch')
            self.assertEqual(self.client.store.room('a')['oauth_posts'][payload['client_id']], saved)
            originals.append((payload, first))
        await self.settle(adapter, state='failed')
        for payload, first in originals:
            replay = json.loads(self.post(payload, target='5'))
            self.assertTrue(replay['ok'], replay)
            self.assertEqual(replay['result']['number'], first['result']['number'])
            self.assertEqual(replay['result']['read_effect'], 'none')
        self.assertEqual(len(self.messages), len(originals))
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['5'])

    async def test_unannotated_fallback_refuses_guess_and_settles(self):
        adapter, _ = await self.begin()
        payload = {'text': 'Original', 'processed': True, 'client_id': str(uuid.uuid4())}
        self.assertTrue(json.loads(self.post(payload, target='999'))['ok'])
        # Crash/older persisted journal: no proof of the authored normalization.
        self.client.store.update(lambda d: d['rooms']['a']['oauth_posts'][payload['client_id']].pop('authored_options'))
        saved = copy.deepcopy(self.client.store.room('a')['oauth_posts'])
        result = json.loads(self.post(payload))
        self.assertEqual(result['error'], 'pending_send_mismatch')
        self.assertNotIn('pending', result)
        self.assertEqual(self.client.store.room('a')['oauth_posts'], saved)
        await self.settle(adapter, state='failed')
        replay = self.client.enrolled('a', 'retry', client_id=payload['client_id'])
        self.assertEqual(replay['read_effect'], 'none')
        self.assertEqual(len(self.messages), 1)

    async def test_uncertain_fallback_replays_after_native_context_appears(self):
        adapter, _ = await self.begin()
        payload = {'text': 'Original', 'processed': True, 'client_id': str(uuid.uuid4())}
        original = OAuthClient.rpc
        def lose_response(api, binding, name, args=None, **kwargs):
            value = original(api, binding, name, args, **kwargs)
            if name == 'hermes_authored':
                raise BotSeshError('connection_failed_retry_same_intent')
            return value
        with patch.object(OAuthClient, 'rpc', lose_response):
            first = json.loads(self.post(payload, target='999'))
        self.assertFalse(first['ok'])
        self.assertEqual(first['pending']['client_id'], payload['client_id'])
        saved = copy.deepcopy(self.client.store.room('a')['oauth_posts'][payload['client_id']]['exact'])
        replay = json.loads(self.post(payload))
        self.assertTrue(replay['ok'], replay)
        self.assertEqual(replay['result']['read_effect'], 'none')
        self.assertEqual(self.client.store.room('a')['oauth_posts'][payload['client_id']]['exact'], saved)
        authored = [args for _, name, args in self.requests if name == 'hermes_authored']
        self.assertEqual(authored, [authored[0], authored[0]])
        self.assertEqual(len(self.messages), 1)
        await self.settle(adapter)

    async def test_archived_structured_post_replays_exact_turn_not_new_receipt(self):
        adapter, _ = await self.begin()
        payload = {'text': 'Exact structured answer', 'processed': True,
                   'client_id': str(uuid.uuid4()),
                   'draft_ready': {'title': 'Deliverable', 'recipientId': str(uuid.uuid4())}}
        original = OAuthClient.rpc
        effects = []
        def finish(api, binding, name, args=None, **kwargs):
            if name == 'finish_room_turn':
                effects.append(args)
                return {'reply_number': 100, 'read_checkpoint': 3}
            return original(api, binding, name, args, **kwargs)
        with patch.object(OAuthClient, 'rpc', finish):
            first = json.loads(self.post(payload))
            self.assertTrue(first['ok'], first)
            self.model_ended(adapter)
            await adapter._finish_current('a', adapter._current['a'], 'empty_response')
            # begin_takeover archives the native audit; transport history remains.
            self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={}))
            await adapter._check_room('a')
            self.assertEqual(adapter._current['a']['target'], 5)
            saved = copy.deepcopy(self.client.store.room('a')['oauth_turns'])
            replay = json.loads(self.post(payload, target='5'))
            self.assertTrue(replay['ok'], replay)
            self.assertEqual(replay['result']['number'], first['result']['number'])
            self.assertIn('no newer turn input', replay['result']['notice'])
            self.assertEqual(self.client.store.room('a')['oauth_turns'], saved)
            self.assertEqual(len(effects), 1)
            self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['5'])
            self.model_ended(adapter)
            await adapter._finish_current('a', adapter._current['a'], 'empty_response')
            self.assertEqual(self.client.store.room('a')['processing_receipts']['5']['state'], 'completed_unread')

    async def test_transport_uuid_race_rejects_only_losing_invocation(self):
        adapter, _ = await self.begin()
        cid = str(uuid.uuid4())
        winner = {'text': 'Winning original', 'client_id': cid, 'processed': False}
        enrolled = self.client.enrolled
        def race(room_id, op, **args):
            if op == 'post':
                enrolled(room_id, 'post', **winner)
            return enrolled(room_id, op, **args)
        with patch.object(self.client, 'enrolled', side_effect=race):
            losing = json.loads(self.post({'text': 'Losing attempt', 'client_id': cid, 'processed': True}))
        self.assertEqual(losing['error'], 'pending_send_mismatch')
        self.assertNotIn('pending', losing)
        saved = self.client.store.room('a')['oauth_posts'][cid]
        # Freshness now freezes the exact native input's ID cursor, not the
        # profile-wide latest room number; all winning authored fields stay exact.
        self.assertEqual(saved['exact'], {**winner, 'seen_through': 3})
        self.assertNotIn('failed', saved)
        self.assertNotIn('authored_options', saved)
        await self.settle(adapter, state='failed')
        self.assertEqual(len(self.messages), 1)

    async def test_uuid_of_non_post_turn_is_definitively_rejected(self):
        adapter, _ = await self.begin()
        cid = str(uuid.uuid4())
        saved = {'exact': {'client_id': cid, 'receipt_id': 'old-receipt', 'processed': True,
                          'output': {'nothing_to_add': True}},
                 'complete': True, 'result': {'read_effect': 'processed_frozen_receipt'}}
        self.client.store.update(lambda d: d['rooms']['a'].update(oauth_turns={cid: saved}))
        result = json.loads(self.post({'text': 'Not a pass', 'client_id': cid, 'processed': True}))
        self.assertEqual(result['error'], 'pending_send_mismatch')
        self.assertNotIn('pending', result)
        self.assertEqual(self.client.store.room('a')['oauth_turns'][cid], saved)
        await self.settle(adapter, state='failed')
        self.assertEqual(self.messages, {})


if __name__ == '__main__':
    unittest.main()
