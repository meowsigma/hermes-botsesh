"""Offline native command integration: real BotSeshAdapter + GatewayRunner.

Run from plugin-reference with the installed Hermes interpreter and core on
PYTHONPATH. All homes/SQLite files are private temporary directories under the
owning task workspace. Only Client transport is faked; native command parsing,
authorization, dispatch, handlers, SessionStore and control resolvers are real.
No gateway daemon, adapter listener, network, or model is started.
"""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

# Reuse the established package loader, never invent a parallel import package.
from test_plugin import package, Client, Store

WORKSPACE = Path(__file__).resolve().parents[2]
# Set before importing gateway.run (it captures its launch home on import).
_BOOT_HOME = tempfile.TemporaryDirectory(prefix='native-command-boot-', dir=WORKSPACE)
os.environ['HERMES_HOME'] = _BOOT_HOME.name
os.environ['HERMES_DISABLE_LAZY_INSTALLS'] = '1'

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry
from gateway.run import GatewayRunner
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from hermes_botsesh_test.adapter import BotSeshAdapter


class RoomTransport:
    """Authenticated server projections at the Client.enrolled transport seam."""
    def __init__(self):
        self.messages = {}
        self.calls = []
        self.posts = []
        self.controls = []
        self.deliveries = []
        self.number = 1000

    def add(self, number, text, control=None, human='human-owner'):
        message = dict(number=number, text=text, senderParticipantId=human,
                       authorParticipant=dict(id=human, verified=True, name='Owner'))
        if control:
            message['dmControl'] = control
        self.messages[number] = message
        return message

    def __call__(self, room, operation, **kwargs):
        assert room == 'native-dm'
        self.calls.append((operation, deepcopy(kwargs)))
        if operation == 'dm_context':
            return dict(room_type='dm', human_participant_id='human-owner',
                        membership_id='agent-membership', participant_id='agent-participant')
        if operation == 'check':
            return dict(mentions=list(self.messages), replies=[], backlog=len(self.messages))
        if operation == 'read':
            target = kwargs['target_number']
            return dict(receipt_id=f'frozen-{target}', batch=dict(messages=[deepcopy(self.messages[target])]))
        if operation == 'delivery':
            self.deliveries.append(deepcopy(kwargs))
            # Retain the inbox item deliberately: replay suppression must be local/durable.
            return dict(ok=True)
        if operation == 'post':
            self.posts.append(deepcopy(kwargs))
            self.number += 1
            return dict(number=self.number)
        if operation == 'dm_control':
            action, payload = kwargs['action'], deepcopy(kwargs['payload'])
            self.controls.append((action, payload))
            self.number += 1
            if action == 'manifest':
                control = dict(commands=payload['commands'], agentMembershipId='agent-membership')
            else:
                control = dict(payload, agentMembershipId='agent-membership',
                               kind='interactive', status='pending')
            return dict(message=dict(number=self.number, dmControl=control))
        raise AssertionError(f'Unexpected transport operation {operation}: {kwargs}')


class NativeCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-command-case-', dir=WORKSPACE)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.enterContext(patch.dict(os.environ, {
            'PATH': os.environ.get('PATH', ''), 'HOME': str(self.home),
            'TMPDIR': str(WORKSPACE), 'HERMES_HOME': str(self.home),
            'HERMES_DISABLE_LAZY_INSTALLS': '1',
            'GATEWAY_ALLOWED_USERS': 'human-owner', 'GATEWAY_ALLOW_ALL_USERS': 'false',
        }, clear=True))
        token = set_hermes_home_override(self.home)
        self.addCleanup(reset_hermes_home_override, token)
        self.enterContext(patch('gateway.run._hermes_home', self.home))
        # A declared local-only provider and fresh metadata cache exercise native
        # inventory/resolution without replacing either resolver or fetching APIs.
        from agent import models_dev
        from hermes_cli.config import atomic_config_write
        self.enterContext(patch.object(models_dev, '_models_dev_cache', {}))
        self.enterContext(patch.object(models_dev, '_models_dev_cache_time', 0))
        models_dev._save_disk_cache({'offline': dict(id='offline', name='Offline',
            models={'offline-a': dict(id='offline-a', name='Offline A',
                limit=dict(context=8192, output=1024), cost=dict(input=0, output=0)),
                    'offline-b': dict(id='offline-b', name='Offline B',
                limit=dict(context=8192, output=1024), cost=dict(input=0, output=0))})})
        atomic_config_write(self.home / 'config.yaml', {
            'model': {'provider': 'offline', 'default': 'offline-a', 'base_url': 'http://127.0.0.1:9/v1'},
            'providers': {'offline': {'name': 'Offline', 'base_url': 'http://127.0.0.1:9/v1',
                'models': ['offline-a', 'offline-b'], 'discover_models': False}},
        })
        # Block at the network boundary, even if a read-only command probes metadata.
        self.network = self.enterContext(patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK FORBIDDEN')))
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh',
            adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        self.transport = RoomTransport()
        self.client = Client(Store(self.home / 'plugin'))
        self.client.store.update(lambda data: data['rooms'].update({'native-dm': dict(
            active=True, url='https://botsesh.com/b/native-dm', name='Native DM',
            transport='oauth', room_type='dm', participant_id='agent-participant')}))
        self.client.enrolled = self.transport
        with patch('hermes_botsesh_test.adapter.Client', return_value=self.client):
            self.adapter = BotSeshAdapter(PlatformConfig())
        config = GatewayConfig(sessions_dir=self.home / 'sessions', multiplex_profiles=False)
        config.platforms[self.adapter.platform] = self.adapter.config
        self.runner = GatewayRunner(config)
        self.runner.adapters[self.adapter.platform] = self.adapter
        self.runner._wire_adapter_handlers(self.adapter)
        # Rejection sentinels, not model doubles: every attempt makes the test fail.
        self.model = self.enterContext(patch('run_agent.AIAgent', side_effect=AssertionError('MODEL FORBIDDEN')))
        self.agent_turn = self.enterContext(patch.object(self.runner, '_run_agent', new=AsyncMock(side_effect=AssertionError('MODEL TURN FORBIDDEN'))))
        self.resolve = self.enterContext(patch.object(self.runner, '_hm_resolve_command', wraps=self.runner._hm_resolve_command))
        self.ingress = self.enterContext(patch.object(self.adapter, 'handle_message', wraps=self.adapter.handle_message))
        self.source = self.adapter.build_source(chat_id='native-dm', chat_type='dm', user_id='human-owner')
        self.key = self.runner._session_key_for_source(self.source)
        self.assertIs(self.adapter._is_sender_authorized('human-owner', 'dm', 'native-dm'), True)

    async def asyncTearDown(self):
        tasks = [t for t in self.adapter._background_tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        from gateway.status import flush_runtime_status
        flush_runtime_status(timeout=2.0)
        self.runner.close_all_session_db_handles()
        self.runner.session_store.close_all_db_handles()
        if self.runner._executor:
            self.runner._executor.shutdown(wait=True)
        self.assertEqual(self.model.call_count, 0)
        self.assertEqual(self.agent_turn.call_count, 0)
        self.assertEqual(self.network.call_count, 0)
        for post in self.transport.posts:
            self.assertNotIn('receipt_id', post)
            self.assertNotIn('processed', post)

    async def drain(self):
        for _ in range(100):
            pending = [t for t in self.adapter._background_tasks if not t.done()]
            if not pending:
                return
            await asyncio.wait_for(asyncio.gather(*pending), timeout=10)
        self.fail('Native background dispatch did not drain')

    async def command(self, number, text):
        self.transport.add(number, text)
        await self.adapter._check_room('native-dm')
        await self.drain()

    def reply(self, target):
        return '\n'.join(p['text'] for p in self.transport.posts if p.get('reply_to') == target)

    def assert_control_event(self, text):
        event = next(call.args[0] for call in self.ingress.call_args_list if call.args[0].text == text)
        self.assertEqual(event.text, text)
        self.assertEqual(event.source.chat_type, 'dm')
        self.assertEqual(event.source.user_id, 'human-owner')
        self.assertIs(event.allow_gateway_control, True)
        self.assertTrue(event.metadata['botsesh_control'])
        self.assertNotIn('Complete BotSesh collaboration data', event.text)
        return event

    async def test_native_goal_set_show_and_status_persist_without_inference(self):
        # Native /goal <objective> sets AND schedules a model kickoff. Capture
        # that directive with core's own local slash.exec sink, not a fake handler
        # or a model call. /goal show and /status use the complete messaging path.
        from gateway.session_command_sink import capture
        from hermes_cli.goals import load_goal
        with capture() as kickoff:
            await self.command(1, '/goal Ship the native command proof')
        self.assert_control_event('/goal Ship the native command proof')
        self.assertIn('Ship the native command proof', self.reply(1))
        self.assertIn('Ship the native command proof', kickoff.prompt)
        entry = self.runner.session_store.get_or_create_session(self.source)
        self.assertEqual(load_goal(entry.session_id).goal, 'Ship the native command proof')
        await self.command(2, '/goal show')
        self.assertIn('Ship the native command proof', self.reply(2))
        await self.command(3, '/status')
        entry = self.runner.session_store.get_or_create_session(self.source)
        self.assertIn(entry.session_id, self.reply(3))
        self.assertIn('botsesh', self.reply(3))
        self.assertNotIn('native-dm', self.adapter._current)
        self.assertFalse(self.client.store.room('native-dm').get('processing_receipts'))
        for post in self.transport.posts:
            self.assertNotIn('receipt_id', post)
            self.assertNotIn('processed', post)

    async def test_unknown_command_uses_native_helpful_response(self):
        with patch('hermes_botsesh_test.command_transport.build_manifest', side_effect=ValueError('catalog exceeds v1 limit')):
            await self.command(10, '/not-a-real-native-command')
        self.assert_control_event('/not-a-real-native-command')
        reply = self.reply(10)
        self.assertIn('not-a-real-native-command', reply)
        self.assertIn('/commands', reply)
        self.assertNotIn("Hermes couldn't answer", reply)
        await self.command(11, '/')
        self.assertIn('/commands', self.reply(11))

    def occupy_normal_turn(self, active):
        """Frozen ordinary turn + native adapter/runner pending-turn guards.

        The owner is this live test task, not a model. This drives native BUSY
        bypass dispatch while keeping zero inference and no invented handler.
        """
        from gateway.run import _AGENT_PENDING_SENTINEL
        self.adapter._current['native-dm'] = deepcopy(active)
        guard = asyncio.Event()
        self.adapter._active_sessions[self.key] = guard
        self.adapter._session_tasks[self.key] = asyncio.current_task()
        self.runner._running_agents[self.key] = _AGENT_PENDING_SENTINEL
        self.addCleanup(self.adapter._active_sessions.pop, self.key, None)
        self.addCleanup(self.adapter._session_tasks.pop, self.key, None)
        self.addCleanup(self.runner._running_agents.pop, self.key, None)
        return guard

    async def test_busy_status_preserves_exact_command_and_active_turn(self):
        active = dict(receipt_id='normal-receipt', target=50, client_id='normal-turn',
                      delivered=False, model_processed=False, text='immutable normal prompt')
        guard = self.occupy_normal_turn(active)
        await self.command(51, '/status')
        self.assertIs(self.adapter._active_sessions[self.key], guard)
        self.assert_control_event('/status')
        self.assertIn('botsesh', self.reply(51))
        self.assertEqual(self.adapter._current['native-dm'], active)
        self.assertFalse(self.client.store.room('native-dm').get('processing_receipts'))
        self.assertTrue(all('receipt_id' not in p and 'processed' not in p for p in self.transport.posts))

    def choice(self, number, card, value):
        option = next(token for token, actual in card.values.items() if actual == value)
        return self.transport.add(number, 'server-rendered choice (never a model prompt)', dict(
            version=1, kind='choice', callbackId=card.payload['callbackId'],
            optionId=option, sourceNumber=card.source_number,
            agentMembershipId='agent-membership'))

    async def test_busy_choice_resolves_real_native_clarify_without_mutating_turn(self):
        from tools import clarify_gateway as clarify
        await self.adapter._refresh_dm_controls('native-dm')
        self.addCleanup(clarify.clear_session, self.key)
        entry = clarify.register('native-clarify', self.key, 'Which proof?', ['Native', 'Scripted'])
        result = await self.adapter.send_clarify('native-dm', entry.question, entry.choices,
                                                entry.clarify_id, self.key)
        self.assertTrue(result.success, result.error)
        card = next(c for c in self.adapter._controls().values() if c.kind == 'clarify')
        active = dict(receipt_id='active-normal', target=60, client_id='normal',
                      delivered=False, model_processed=False, text='unchanged question')
        self.occupy_normal_turn(active)
        self.choice(61, card, ('answer', 'Native'))
        await self.adapter._check_room('native-dm')
        self.assertTrue(entry.event.is_set())
        self.assertEqual(entry.response, 'Native')
        self.assertEqual(self.adapter._current['native-dm'], active)
        self.assertIn('Answer submitted.', self.reply(61))
        self.assertFalse(self.client.store.room('native-dm').get('processing_receipts'))
        self.assertEqual(self.resolve.call_count, 0)  # no new normal/model turn
        posts = deepcopy(self.transport.posts)
        await self.adapter._check_room('native-dm')
        self.assertEqual(self.transport.posts, posts)  # repeated server row is consumed once

    async def test_new_invalidates_old_cards_and_native_confirmation_resets_session_once(self):
        from tools import clarify_gateway as clarify
        from tools import slash_confirm
        self.addCleanup(clarify.clear_session, self.key)
        self.addCleanup(slash_confirm.clear, self.key)
        await self.command(70, '/status')
        original = self.runner.session_store.get_or_create_session(self.source).session_id
        before_generation = self.adapter._control_context('native-dm')['generation']
        entry = clarify.register('old-clarify', self.key, 'Old question?', ['Yes', 'No'])
        self.assertTrue((await self.adapter.send_clarify('native-dm', entry.question,
            entry.choices, entry.clarify_id, self.key)).success)
        old_card = next(c for c in self.adapter._controls().values() if c.kind == 'clarify')
        self.choice(72, old_card, ('answer', 'Yes'))
        stale = self.transport.messages.pop(72)
        await self.command(71, '/new')
        self.assert_control_event('/new')
        self.assertNotEqual(before_generation, self.adapter._control_context('native-dm')['generation'])
        self.assertNotIn(old_card.payload['callbackId'], self.adapter._controls())
        # Native defaults require confirmation: merely requesting /new is not approval.
        self.assertEqual(self.runner.session_store.get_or_create_session(self.source).session_id, original)
        confirm_card = next(c for c in self.adapter._controls().values() if c.kind == 'slash')
        self.choice(73, confirm_card, 'once')
        await self.adapter._check_room('native-dm')
        await self.drain()
        reset = self.runner.session_store.get_or_create_session(self.source).session_id
        self.assertNotEqual(reset, original)
        self.assertNotIn('native-dm', self.adapter._current)
        self.transport.messages[72] = stale
        await self.adapter._check_room('native-dm')
        await self.drain()
        self.assertNotEqual(entry.response, 'Yes')
        self.assertIn('expired', self.reply(72))
        posts = deepcopy(self.transport.posts)
        await self.adapter._check_room('native-dm')
        await self.drain()
        self.assertEqual(self.runner.session_store.get_or_create_session(self.source).session_id, reset)
        self.assertEqual(self.transport.posts, posts)

    async def test_busy_typed_choice_uses_native_clarify_text_resolver(self):
        from tools import clarify_gateway as clarify
        self.addCleanup(clarify.clear_session, self.key)
        await self.adapter._refresh_dm_controls('native-dm')
        entry = clarify.register('typed-native', self.key, 'Which proof?', ['Native', 'Scripted'])
        self.assertTrue((await self.adapter.send_clarify('native-dm', entry.question,
            entry.choices, entry.clarify_id, self.key)).success)
        active = dict(receipt_id='normal-read', target=100, client_id='normal-turn',
                      delivered=False, model_processed=False, text='immutable normal prompt')
        guard = self.occupy_normal_turn(active)
        await self.command(101, '1')
        self.assertIs(self.adapter._active_sessions[self.key], guard)
        self.assertTrue(entry.event.is_set())
        self.assertEqual(entry.response, 'Native')
        self.assertEqual(self.adapter._current['native-dm'], active)
        self.assertFalse(self.client.store.room('native-dm').get('processing_receipts'))

    async def test_oauth_consent_and_verified_identity_do_not_grant_native_operator_auth(self):
        await self.adapter._refresh_dm_controls('native-dm')
        # Keep the authenticated DM owner identity, remove its NATIVE grant.
        os.environ['GATEWAY_ALLOWED_USERS'] = 'different-operator'
        self.assertIs(self.adapter._is_sender_authorized('human-owner', 'dm', 'native-dm'), False)
        self.transport.add(110, '/status')
        await self.adapter._check_room('native-dm')
        await self.drain()
        self.assertEqual(self.resolve.call_count, 0)
        self.assertNotIn('110', self.client.store.room('native-dm').get('control_inputs', {}))
        self.assertIn("Hermes couldn't answer", self.reply(110))
        self.assertNotIn('processed', self.transport.posts[-1])
        self.assertNotIn('receipt_id', self.transport.posts[-1])

    async def test_idle_manifest_refresh_does_not_grow_durable_operation_journal(self):
        await self.adapter._refresh_dm_controls('native-dm')
        before = deepcopy(self.client.store.read().get('operation_ids', {}))
        for _ in range(3):
            self.adapter._dm_manifest_at['native-dm'] = 0
            await self.adapter._refresh_dm_controls('native-dm')
        self.assertEqual(self.client.store.read().get('operation_ids', {}), before)
        self.assertEqual(sum(action == 'manifest' for action, _ in self.transport.controls), 1)

    async def test_native_model_picker_callback_commits_selected_model_once(self):
        await self.command(90, '/model --session')
        self.assert_control_event('/model --session')
        root = next(c for c in self.adapter._controls().values()
                    if c.kind == 'model' and c.payload['text'] == 'Choose a provider')
        offline_option = next(value for token, value in root.values.items()
            if any(row['id'] == token and row['label'].lstrip('✓ ') == 'Offline'
                   for row in root.payload['choices']))
        self.choice(91, root, offline_option)
        await self.adapter._check_room('native-dm')
        models = next(c for c in self.adapter._controls().values()
                      if c.kind == 'model' and c.payload['text'] == 'Choose a model: Offline')
        model_option = next(v for v in models.values.values() if v[0] == 'model' and v[2] == 'offline-b')
        self.choice(92, models, model_option)
        await self.adapter._check_room('native-dm')
        warnings = [c for c in self.adapter._controls().values()
                    if c.kind == 'model' and not c.claimed and any(v[0] == 'continue' for v in c.values.values())]
        if warnings:
            warning = warnings[-1]
            self.choice(93, warning, next(v for v in warning.values.values() if v[0] == 'continue'))
            await self.adapter._check_room('native-dm')
        override = self.runner._session_model_overrides.get(self.key, {})
        self.assertEqual(override.get('model'), 'offline-b', self.transport.posts)
        self.assertEqual(override.get('provider'), 'offline')
        entry = self.runner.session_store.get_or_create_session(self.source)
        self.assertEqual(self.runner.session_store._db.get_session(entry.session_id)['model'], 'offline-b')
        self.assertNotIn('native-dm', self.adapter._current)
        posts = deepcopy(self.transport.posts)
        await self.adapter._check_room('native-dm')
        self.assertEqual(self.transport.posts, posts)
        self.assertEqual(self.resolve.call_count, 1)
        await self.command(94, '/status')
        self.assertIn('offline-b', self.reply(94))

    async def test_external_native_session_rotation_retires_old_callbacks(self):
        from tools import clarify_gateway as clarify
        self.addCleanup(clarify.clear_session, self.key)
        await self.command(120, '/status')
        entry = clarify.register('external-session', self.key, 'Still current?', ['Yes', 'No'])
        self.assertTrue((await self.adapter.send_clarify('native-dm', entry.question,
            entry.choices, entry.clarify_id, self.key)).success)
        card = next(c for c in self.adapter._controls().values() if c.kind == 'clarify')
        self.runner.session_store.reset_session(self.key)
        self.assertFalse(self.adapter._control_current(card))
        self.choice(121, card, ('answer', 'Yes'))
        await self.adapter._check_room('native-dm')
        self.assertFalse(entry.event.is_set())
        self.assertIsNone(entry.response)

    async def test_native_dispatch_is_not_replayed_after_adapter_reconstruction(self):
        await self.command(80, '/status')
        self.assertEqual(self.resolve.call_count, 1)
        rows = deepcopy(self.client.store.room('native-dm')['control_inputs'])
        posts = deepcopy(self.transport.posts)
        with patch('hermes_botsesh_test.adapter.Client', return_value=self.client):
            replacement = BotSeshAdapter(PlatformConfig())
        self.runner.adapters[replacement.platform] = replacement
        self.runner._wire_adapter_handlers(replacement)
        await replacement._check_room('native-dm')
        self.assertEqual(self.resolve.call_count, 1)
        self.assertEqual(self.client.store.room('native-dm')['control_inputs'], rows)
        self.assertEqual(self.transport.posts, posts)
