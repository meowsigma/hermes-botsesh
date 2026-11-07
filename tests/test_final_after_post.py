"""Offline regression through composed adapters, tools, Client and OAuth journals.

Only the MCP wire is doubled. No production, provider or model calls.
"""
import hashlib
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from test_plugin import Client, Store, BotSeshError, tools
from hermes_botsesh_test.adapter import BotSeshAdapter, processed_turn, ended_turn
from hermes_botsesh_test.oauth import OAuthClient
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.platforms.event import ProcessingOutcome


class FinalAfterPostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.client = Client(Store(Path(tmp.name) / 'private'))
        self.client.store.update(lambda d: d['rooms'].update({room: {
            'room_id': room, 'url': 'https://botsesh.com/b/' + room,
            'transport': 'oauth', 'active': True, 'participant_id': 'bot-' + room,
        } for room in ('a', 'b')}))
        self.targets = {'a': [3, 5], 'b': [3, 5]}
        self.messages, self.requests, self.revoked = {}, [], set()
        self.lose_send = False
        self.lose_notice = False
        def rpc(client, binding, name, args=None, **kwargs):
            room = binding['room_id']
            self.requests.append((room, name, args))
            if room in self.revoked:
                raise BotSeshError('authorization_revoked', 401)
            if name == 'list_rooms':
                return {'rooms': [{'room_id': room, 'participant_id': 'bot-' + room}]}
            if name == 'hermes_inbox':
                return {'targets': [{'number': n} for n in self.targets[room]],
                        'has_more': False, 'backlog': len(self.targets[room])}
            if name == 'catchup':
                n = args['target_number']
                return {'batch_id': 'receipt-' + room + '-' + str(n), 'ack_eligible': True,
                        'has_more': False, 'truncated': False, 'read_checkpoint': 0,
                        'next_checkpoint': n, 'messages': [{
                            'number': n, 'text': 'Question ' + str(n),
                            'senderParticipantId': 'owner',
                            'authorParticipant': {'id': 'owner', 'verified': True}}]}
            if name in ('send_message', 'hermes_notice'):
                if args.get('failure_code'):
                    self.assertIn(args['failure_code'], ('provider_budget', 'provider_error',
                        'interrupted', 'cancelled', 'empty_response', 'admission_denied',
                        'context_limit', 'runtime_error'))
                key = (room, args['client_id'])
                value = self.messages.setdefault(key, {'number': 100 + len(self.messages),
                    'text': args['text'], 'text_sha256': hashlib.sha256(args['text'].encode()).hexdigest(),
                    'authorParticipant': {'id': 'bot-' + room}})
                self.assertEqual(value['text'], args['text'])
                if name == 'send_message' and self.lose_send:
                    self.lose_send = False
                    raise BotSeshError('connection_failed_retry_same_intent')
                if name == 'hermes_notice' and self.lose_notice:
                    self.lose_notice = False
                    raise BotSeshError('connection_failed_retry_same_intent')
                return {'message': value}
            if name == 'hermes_delivery':
                return {'read_effect': 'none'}
            self.fail('Unexpected MCP operation: ' + name)
        self.patch_rpc = patch.object(OAuthClient, 'rpc', rpc)
        self.patch_rpc.start()
        self.addCleanup(self.patch_rpc.stop)
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh',
            adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        self.adapters = {}
        for room in ('a', 'b'):
            with patch('hermes_botsesh_test.adapter.Client', return_value=self.client):
                adapter = BotSeshAdapter(PlatformConfig())
            adapter._is_sender_authorized = lambda *args: True
            async def admit(event):
                event._gateway_accepted = True
            adapter.handle_message = AsyncMock(side_effect=admit)
            self.adapters[room] = adapter

    async def begin(self, room='a'):
        adapter = self.adapters[room]
        await adapter._check_room(room)
        event = adapter.handle_message.call_args.args[0]
        await adapter.on_processing_start(event)
        return adapter, event

    def model_ended(self, adapter, room='a', *, failed=False):
        processed_turn(platform='botsesh', user_message=adapter._current[room]['text'],
                       session_id=room, turn_id='turn')
        ended_turn(platform='botsesh', session_id=room, turn_id='turn',
                   completed=not failed, failed=failed, interrupted=False)

    def tool_post(self, adapter, room='a', *, reply_to=True):
        current = adapter._current[room]
        args = {'room_id': room, 'text': 'Exact tool answer.\nUnicode: café 🐚\n',
                'receipt_id': current['receipt_id'], 'processed': True,
                'client_id': str(uuid.uuid4())}
        if reply_to:
            args['reply_to'] = current['target']
        with patch.object(tools, 'Client', return_value=self.client), \
             patch.object(tools, 'calling_scope', return_value=('botsesh', room)):
            return tools.post(args, Mock())

    async def test_tool_post_then_final_settles_both_adapters_and_admits_later_turn(self):
        for room in ('a', 'b'):
            with self.subTest(room=room):
                adapter, event = await self.begin(room)
                posted = self.tool_post(adapter, room)
                self.model_ended(adapter, room)
                result = await adapter.send(room, 'A DIFFERENT final must not post.', metadata={'notify': True})
                state = self.client.store.room(room)
                print('final-after-tool:', room, 'success=', result.success, 'error=', result.error,
                      'pending_final=', bool(state.get('pending_final')),
                      'visible=', len([k for k in self.messages if k[0] == room]))
                self.assertTrue(result.success, result.error)
                self.assertEqual(result.message_id, str(posted['number']))
                await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
                state = self.client.store.room(room)
                self.assertEqual(state['processing_receipts']['3']['state'], 'replied')
                self.assertNotIn('pending_final', state)
                self.assertEqual(len([k for k in self.messages if k[0] == room]), 1)
                await adapter._check_room(room)
                self.assertEqual(adapter.handle_message.call_args.args[0].message_id, '5')
                self.assertEqual(adapter.handle_message.await_count, 2)

    async def test_native_final_ledger_does_not_journal_a_second_answer(self):
        adapter, event = await self.begin()
        posted = self.tool_post(adapter, reply_to=False)
        self.model_ended(adapter)
        with patch('gateway.platforms.base.BasePlatformAdapter.send_final_ledgered', new_callable=AsyncMock) as native:
            result, sender = await adapter.send_final_ledgered(event, 'native-session',
                'Different prose final', {'notify': True}, reply_to='3')
        self.assertTrue(result.success)
        self.assertEqual(result.message_id, str(posted['number']))
        native.assert_not_awaited()
        self.assertNotIn('pending_final', self.client.store.room('a'))
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(len(self.messages), 1)

    async def test_tool_answer_then_provider_failure_or_empty_completion_is_not_two_outcomes(self):
        for room in ('a', 'b'):
            adapter, event = await self.begin(room)
            self.tool_post(adapter, room)
            self.model_ended(adapter, room, failed=True)
            if room == 'a':
                result = await adapter.send(room, 'HTTP 402 provider detail', metadata={'notify': True})
                self.assertTrue(result.success)
            await adapter.on_processing_complete(event, ProcessingOutcome.FAILURE)
            await adapter._notify_turn_error(event, RuntimeError('later tool error'))
            self.assertEqual(len([k for k in self.messages if k[0] == room]), 1)
            self.assertEqual(self.client.store.room(room)['processing_receipts']['3']['state'], 'replied')

    def save_old_pending_final(self, adapter, room='a'):
        current = adapter._current[room]
        payload = {'text': 'Old already-generated final', 'client_id': current['client_id'],
                   'receipt_id': current['receipt_id'], 'processed': True, 'reply_to': current['target']}
        adapter._journal_gateway_delivery(room, payload, current['target'], pending_final=True)
        # Simulate a pre-upgrade registry: no new Client confirmation field.
        self.client.store.update(lambda d: d['rooms'][room]['processing_receipts']['3'].pop('posted_reply', None))
        adapter._current.pop(room)
        return payload

    async def test_pre_upgrade_pending_final_self_heals_without_another_visible_post(self):
        from gateway.delivery_ledger import RECOVERED_MARKER
        adapter, event = await self.begin()
        posted = self.tool_post(adapter)
        payload = self.save_old_pending_final(adapter)
        adapter._instance = 'restarted'
        await adapter._check_room('a')
        state = self.client.store.room('a')
        self.assertEqual(state['processing_receipts']['3']['state'], 'replied')
        self.assertNotIn('pending_final', state)
        self.assertEqual(adapter.handle_message.call_args.args[0].message_id, '5')
        # A stale core-ledger retry must not post or disturb the NEW active turn.
        new_current = adapter._current['a']
        newer_payload = {'text': 'New turn final, not sent yet', 'client_id': new_current['client_id'],
                         'receipt_id': new_current['receipt_id'], 'processed': True, 'reply_to': 5}
        adapter._journal_gateway_delivery('a', newer_payload, 5, pending_final=True)
        result = await adapter.send('a', RECOVERED_MARKER + payload['text'])
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.message_id, str(posted['number']))
        self.assertFalse(adapter._current['a']['delivered'])
        self.assertEqual(adapter._current['a']['target'], 5)
        self.assertEqual(self.client.store.room('a')['pending_final']['payload'], newer_payload)
        self.assertEqual(len(self.messages), 1)

    async def test_lost_tool_response_resolves_original_uuid_before_final(self):
        adapter, event = await self.begin()
        self.lose_send = True
        with self.assertRaises(BotSeshError):
            self.tool_post(adapter)
        original_id = next(iter(self.messages))[1]
        self.model_ended(adapter)
        result = await adapter.send('a', 'Do not send a substitute final', metadata={'notify': True})
        self.assertTrue(result.success, result.error)
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(len(self.messages), 1)
        sends = [args for _, name, args in self.requests if name == 'send_message']
        self.assertEqual([args['client_id'] for args in sends], [original_id, original_id])
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'replied')

    async def test_expired_unanswered_receipt_gets_one_failure_then_later_turn(self):
        from gateway.delivery_ledger import RECOVERED_MARKER
        adapter, event = await self.begin()
        payload = self.save_old_pending_final(adapter)
        self.client.store.update(lambda d: d['rooms']['a']['oauth_receipts'].clear())
        adapter._instance = 'restarted'
        await adapter._check_room('a')
        state = self.client.store.room('a')
        self.assertEqual(state['processing_receipts']['3']['state'], 'failed')
        self.assertNotIn('pending_final', state)
        self.assertNotIn(3, state.get('handled', []))
        self.assertEqual(adapter.handle_message.call_args.args[0].message_id, '5')
        self.assertEqual(len(self.messages), 1)
        self.assertIn("Hermes couldn't answer:", next(iter(self.messages.values()))['text'])
        self.assertEqual(len([1 for _, name, _ in self.requests if name == 'send_message']), 0)
        for _ in range(2):
            result = await adapter.send('a', RECOVERED_MARKER + payload['text'])
            self.assertTrue(result.success, result.error)
        self.assertEqual(len(self.messages), 1)
        self.assertNotIn(3, self.client.store.room('a').get('handled', []))

    async def test_revoked_completed_tool_reply_cannot_be_reconciled_from_cache(self):
        adapter, event = await self.begin()
        self.tool_post(adapter)
        self.model_ended(adapter)
        self.revoked.add('a')
        result = await adapter.send('a', 'No auth bypass', metadata={'notify': True})
        self.assertFalse(result.success)
        self.assertIn('authorization_revoked', result.error)
        self.assertNotIn('handled', self.client.store.read()['rooms']['a'])
        self.assertEqual(len(self.messages), 1)

    async def test_expired_failure_lost_response_retains_original_notice_and_recovers(self):
        adapter, event = await self.begin()
        self.save_old_pending_final(adapter)
        self.client.store.update(lambda d: d['rooms']['a']['oauth_receipts'].clear())
        self.lose_notice = True
        with self.assertRaises(BotSeshError):
            await adapter._check_room('a')
        state = self.client.store.room('a')
        self.assertTrue(state['failure_outbox'])
        self.assertIn('pending_final', state)
        original_id = next(iter(self.messages))[1]
        await adapter._check_room('a')
        self.assertNotIn('pending_final', self.client.store.room('a'))
        self.assertEqual(adapter.handle_message.call_args.args[0].message_id, '5')
        self.assertEqual(len(self.messages), 1)
        notices = [args for _, name, args in self.requests if name == 'hermes_notice']
        self.assertEqual([args['client_id'] for args in notices], [original_id, original_id])

    async def test_restart_after_tool_commit_before_any_final_is_answered_not_interrupted(self):
        adapter, event = await self.begin()
        self.tool_post(adapter, reply_to=False)
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['3'].pop('posted_reply'))
        adapter._current.clear()
        adapter._instance = 'restarted'
        await adapter._check_room('a')
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'replied')
        self.assertEqual(adapter.handle_message.call_args.args[0].message_id, '5')

    async def test_unthreaded_post_requires_the_same_durable_addressed_receipt(self):
        adapter, event = await self.begin()
        receipt_id = adapter._current['a']['receipt_id']
        self.tool_post(adapter, reply_to=False)
        self.assertIsNone(await adapter._posted_reply('a', 5, receipt_id))
        self.assertNotIn('handled', self.client.store.room('a'))

    async def test_foreign_receipt_or_target_never_counts_as_answered(self):
        adapter, event = await self.begin()
        original = dict(adapter._current['a'])
        self.tool_post(adapter)
        # Neither a different target nor a different receipt inherits success.
        self.assertIsNone(await adapter._posted_reply('a', 5, original['receipt_id']))
        self.assertIsNone(await adapter._posted_reply('a', 3, 'foreign-receipt'))
        self.assertIsNone(await adapter._posted_reply('b', 3, original['receipt_id']))
        self.assertNotIn('handled', self.client.store.room('a'))
