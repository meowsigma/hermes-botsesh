"""Exact-server regression for direct native producer turns; zero model calls.

The admission/terminal result is scripted, not a live model/gateway proof. The
registered pre_llm_call hook, Python OAuth RPC, sends, receipts and takeover GET
are real. No base-adapter on_processing_start or typing callback is invoked.
"""
import asyncio
import json
import os
import shutil
import unittest
import weakref
from unittest.mock import AsyncMock, Mock, patch

import test_oauth_connect as connect_fixture
from test_plugin import Client, Store, package
from hermes_botsesh_test import adapter as mod
from hermes_botsesh_test.reliability import receipt
from hermes_botsesh_test.client import BotSeshError
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry


class StartHookBoundaries(unittest.TestCase):
    def setUp(self):
        import test_ongoing_turns as fixture
        fixture.OngoingTurns.setUp(self)
        self.current = fixture.OngoingTurns.current(self)
        self.client.enrolled = Mock(return_value={'delivery': {'startedAt': 1000}, 'read_effect': 'none'})
        self.kwargs = dict(platform='botsesh', user_message=self.current['text'], session_id='s', turn_id='t')

    def test_only_actual_pre_model_hook_records_start_once(self):
        mod.processed_turn(**self.kwargs)  # post_llm_call must never backfill a start.
        self.client.enrolled.assert_not_called()
        mod.started_turn(**self.kwargs)
        mod.started_turn(**self.kwargs)
        self.client.enrolled.assert_called_once_with('room', 'delivery', target=3, state='started')
        self.assertTrue(self.current['start_reported'])
        self.assertEqual(self.client.store.room('room')['processing_receipts']['3']['started_at'], 1)

    def test_missing_or_rejected_confirmation_never_marks_a_start(self):
        for response in ({'delivery': {'startedAt': None}}, BotSeshError('authorization_revoked', 401)):
            with self.subTest(response=type(response).__name__):
                self.client.enrolled.side_effect = response if isinstance(response, Exception) else None
                self.client.enrolled.return_value = response
                mod.started_turn(**self.kwargs)
                self.assertNotIn('start_reported', self.current)
                self.assertNotIn('started_at', self.client.store.room('room')['processing_receipts']['3'])
        mod.ended_turn(platform='botsesh', session_id='s', turn_id='t', completed=True, failed=False, interrupted=False)
        self.client.enrolled.reset_mock()
        mod.started_turn(**self.kwargs)
        self.client.enrolled.assert_not_called()  # Never retry from terminal evidence.

    def test_failure_during_start_confirmation_cannot_be_resurrected(self):
        def confirm(*args, **kwargs):
            receipt(self.client.store, 'room', 3, state='failed', failure_code='provider_error')
            return {'delivery': {'startedAt': 1000}}
        self.client.enrolled.side_effect = confirm
        mod.started_turn(**self.kwargs)
        saved = self.client.store.room('room')['processing_receipts']['3']
        self.assertEqual(saved['state'], 'failed')
        self.assertEqual(saved['failure_code'], 'provider_error')
        self.assertNotIn('start_reported', self.current)

    def test_invitation_advanced_foreign_and_ended_turns_get_no_native_authority(self):
        for transport in ('invitation', None):
            self.client.store.update(lambda d: d['rooms']['room'].update(transport=transport))
            mod.started_turn(**self.kwargs)
        self.client.store.update(lambda d: d['rooms']['room'].update(transport='oauth'))
        mod.started_turn(**{**self.kwargs, 'platform': 'telegram'})
        mod.started_turn(**{**self.kwargs, 'user_message': 'foreign receipt', 'turn_id': 'foreign'})
        mod.started_turn(**{**self.kwargs, 'session_id': None})
        self.client.enrolled.assert_not_called()


@unittest.skipUnless(os.environ.get('BOTSESH_OAUTH_CONNECT_SERVER_COMMAND'),
                     'explicit exact-image local OAuth server command required')
class NativeStartedAt(unittest.IsolatedAsyncioTestCase):
    setUp = connect_fixture.NativeOAuthConnect.setUp
    control = connect_fixture.NativeOAuthConnect.control
    stop_server = connect_fixture.NativeOAuthConnect.stop_server
    connect = connect_fixture.NativeOAuthConnect.connect

    async def begin(self):
        self.assertIsNone(shutil.which('node'))
        self.assertIsNone(shutil.which('nodejs'))
        room = self.ready['room_ids'][0]
        self.connect(requested=room)
        self.enterContext(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        self.enterContext(patch.object(mod, '_ADAPTERS', weakref.WeakSet()))
        self.client = Client(Store(self.home / 'plugin-data/botsesh'))
        binding = self.client.store.room(room)
        self.client.enrolled(room, 'listener', state='listening')
        question = self.control(op='message', room_id=room, text='@Fixture Please answer this exact source')['message']
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=mod.BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        adapter = mod.BotSeshAdapter(PlatformConfig())
        adapter.client = self.client
        adapter.set_authorization_check(lambda user, kind, chat: user == question['senderParticipantId'] and chat == room)
        events = []
        async def admit(event):
            events.append(event)
            adapter._current[room]['native_admission'] = {'admission_id': 'scripted-native'}
            return True
        adapter._admit_event = admit  # Only native execution is scripted.
        adapter.on_processing_start = AsyncMock(side_effect=AssertionError('producer ingress never calls base lifecycle'))
        await adapter._check_room(room)
        self.assertEqual(len(events), 1)
        ctx = Mock()
        package.register(ctx)
        self.hooks = {c.args[0]: c.args[1] for c in ctx.register_hook.call_args_list}
        self.kwargs = dict(platform='botsesh', user_message=events[0].text, session_id='scripted-session', turn_id='scripted-turn')
        self.room, self.pid, self.question, self.adapter = room, binding['participant_id'], question, adapter
        return events[0]

    def inspect(self):
        return self.control(op='inspect_turn', room_id=self.room, participant_id=self.pid)

    def delivery(self, snapshot):
        return next(d for d in snapshot['deliveries'] if d['number'] == self.question['number'])

    def record(self, **facts):
        path = os.environ.get('BOTSESH_NATIVE_START_EVIDENCE')
        if path:
            with open(path, 'a') as stream:
                stream.write(json.dumps({'test': self._testMethodName, 'provider_requests': 0,
                                         'node_available': False, 'scripted_native_execution': True, **facts}) + '\n')

    async def test_native_reply_records_real_start_and_becomes_takeover_eligible(self):
        await self.begin()
        before = self.inspect()
        self.assertIsNone(self.delivery(before)['started_at'])
        self.assertEqual((before['checkpoint'], before['attestations']), (0, 0))
        self.assertFalse(next(a for a in before['takeover']['agents'] if a['id'] == self.pid)['eligible'])
        await asyncio.to_thread(self.hooks['pre_llm_call'], **self.kwargs)
        running = self.inspect()
        # A later arrival is outside the immutable turn and must remain unread.
        late = self.control(op='message', room_id=self.room, text='Later input outside the frozen turn')['message']
        self.hooks['post_llm_call'](**self.kwargs)
        self.hooks['on_session_end'](platform='botsesh', session_id='scripted-session', turn_id='scripted-turn', completed=True, failed=False, interrupted=False)
        sent = await self.adapter.send(self.room, 'Scripted native final: café 🐚', metadata={'notify': True})
        self.assertTrue(sent.success, sent.error)
        self.adapter._native_state = AsyncMock(return_value=({'status': 'terminal', 'outcome': 'completed'}, {'final_response': 'Scripted native final: café 🐚'}))
        await self.adapter._reconcile_native(self.room)
        after = self.inspect()
        delivery = self.delivery(after)
        eligible = next(a for a in after['takeover']['agents'] if a['id'] == self.pid)
        self.record(before=before, running=running, after=after)
        self.assertIsNotNone(delivery['answered_at'], 'the real reply must already exist')
        self.assertIsNotNone(delivery['completed_at'])
        self.assertIsNotNone(delivery['started_at'], 'native producer omitted the real turn-start signal')
        self.assertEqual(delivery['started_at'], self.delivery(running)['started_at'], 'start must be recorded before output, never reconstructed at completion')
        self.assertLessEqual(delivery['received_at'], delivery['started_at'])
        self.assertLessEqual(delivery['started_at'], delivery['answered_at'])
        self.assertIsNone(self.delivery(running)['answered_at'])
        self.assertEqual((running['checkpoint'], running['attestations']), (0, 0), 'start is not read attestation')
        self.assertEqual(after['checkpoint'], self.question['id'])
        self.assertLess(after['checkpoint'], late['id'])
        self.assertTrue(eligible['eligible'], eligible)
        self.adapter.on_processing_start.assert_not_awaited()

    async def test_native_failure_stays_failed_without_read_or_eligibility(self):
        await self.begin()
        await asyncio.to_thread(self.hooks['pre_llm_call'], **self.kwargs)
        running = self.inspect()
        self.hooks['on_session_end'](platform='botsesh', session_id='scripted-session', turn_id='scripted-turn', completed=False, failed=True, interrupted=False)
        self.adapter._current[self.room]['provider_hint'] = 'provider_error'
        self.adapter._native_state = AsyncMock(return_value=({'status': 'terminal', 'outcome': 'failed'}, {'failed': True}))
        await self.adapter._reconcile_native(self.room)
        after = self.inspect()
        delivery = self.delivery(after)
        self.record(running=running, after=after)
        self.assertIsNotNone(delivery['failed_at'])
        self.assertEqual(delivery['failure_code'], 'provider_error')
        self.assertIsNone(delivery['answered_at'])
        self.assertIsNone(delivery['completed_at'])
        self.assertEqual((after['checkpoint'], after['attestations']), (0, 0))
        self.assertFalse(next(a for a in after['takeover']['agents'] if a['id'] == self.pid)['eligible'])
        self.assertEqual(len(after['messages']), 1, 'no provider diagnostic is a room answer')
        self.assertIsNotNone(delivery['started_at'])
        self.assertEqual(delivery['started_at'], self.delivery(running)['started_at'])
        self.assertLessEqual(delivery['started_at'], delivery['failed_at'])
        self.adapter.on_processing_start.assert_not_awaited()
