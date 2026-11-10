"""Focused changed-hunk checks. Doubles are not native model-return evidence."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock

from test_plugin import Client, Store, BotSeshError
from hermes_botsesh_test import adapter as mod, cli, tool_policy
from hermes_botsesh_test.reliability import receipt
from gateway.config import PlatformConfig
from gateway.platforms.event import ProcessingOutcome
from gateway.platform_registry import PlatformEntry, platform_registry


class OngoingTurns(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict('os.environ', {'HERMES_HOME': str(Path(self.tmp.name) / 'home')})
        env.start()
        self.addCleanup(env.stop)
        self.client = Client(Store(Path(self.tmp.name) / 'private'))
        self.binding = dict(active=True, room_id='room', participant_id='seat', display_name='Scout', transport='oauth', room_type='room')
        self.client.store.update(lambda d: d['rooms'].update(room=self.binding))
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=mod.BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with patch.object(mod, 'Client', return_value=self.client):
            self.a = mod.BotSeshAdapter(PlatformConfig(extra={'group_allowed_chats': ['room']}))
        self.a._is_sender_authorized = lambda author, kind, room: room == 'room' and kind == 'group'
        self.a._delivery = AsyncMock()
        self.a._posted_reply = AsyncMock(return_value=None)
        self.posts = []
        async def post(room, payload):
            self.posts.append(dict(payload))
            return {'number': 9, 'read_effect': 'none'}
        self.a._post_payload = post
        self.message = dict(number=3, senderParticipantId='guest', isOwn=False, text='Question',
            authorParticipant=dict(id='guest', verified=False, active=True, name='Guest'))
        self.frozen = dict(receipt_id='batch', batch={'messages': [self.message]})
        self.policy = dict(room_id='room', participant_id='seat', number=3, batch_id='batch',
            toolsAllowed=True, requester=dict(id='guest', isOwner=False))

    def current(self, native=True):
        text = json.dumps(dict(addressed_message_number=3, **self.frozen))
        current = dict(receipt_id='batch', target=3, client_id='74e66fe1-837d-4c41-91a0-b0266476eaf6',
            text=text, delivered=False, model_processed=False, tool_policy=self.policy)
        if native:
            current['native_admission'] = dict(admission_id='admission', ref=dict(profile_id='profile', session_id='s'))
        self.a._current['room'] = current
        receipt(self.client.store, 'room', 3, state='admitted', frozen=self.frozen, **{k: v for k, v in current.items() if k in ('receipt_id', 'client_id', 'text', 'native_admission', 'tool_policy')})
        return current

    async def test_exact_active_guest_and_busy_intake_keep_native_room_fence(self):
        self.assertTrue(self.a._sender_identity_admitted('room', self.message))
        for bad in [dict(self.message, senderParticipantId='other'),
                    dict(self.message, authorParticipant=dict(id='guest', verified=False, active=False)),
                    dict(self.message, authorParticipant=dict(id='guest', verified=False))]:
            self.assertFalse(self.a._sender_identity_admitted('room', bad))
        self.client.store.update(lambda d: d['rooms']['room'].update(room_type='dm'))
        self.assertFalse(self.a._sender_identity_admitted('room', self.message))
        self.client.store.update(lambda d: d['rooms']['room'].update(room_type='room'))
        self.a._current['room'] = {'target': 1}
        self.client.enrolled = Mock(side_effect=lambda room, op, **kw: {'mentions': [3]} if op == 'check' else self.frozen)
        await self.a._receive_while_busy('room')
        self.a._delivery.assert_awaited_once_with('room', 3, 'received')
        self.a._delivery.reset_mock()
        self.client.store.update(lambda d: d['rooms']['room'].pop('processing_receipts', None))
        self.a._is_sender_authorized = lambda *a: False
        await self.a._receive_while_busy('room')
        self.assertEqual(self.posts[-1]['failure_code'], 'admission_denied')
        self.assertNotIn('processed', self.posts[-1])

    async def test_admission_ack_keeps_target_then_terminal_no_output_fails_once(self):
        current = self.current()
        state = ({'status': 'queued'}, {})
        self.a._native_state = AsyncMock(side_effect=lambda c: state)
        event = Mock(source=Mock(chat_id='room'), message_id='3', metadata={})
        await self.a.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertIs(self.a._current['room'], current)
        self.assertEqual(self.posts, [])
        current['model_processed'] = True
        state = ({'status': 'terminal', 'outcome': 'completed'}, {'final_response': ''})
        await self.a._reconcile_native('room')
        await self.a._reconcile_native('room')
        self.assertNotIn('room', self.a._current)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0]['text'], "Scout couldn't do it.")
        self.assertEqual(self.posts[0]['failure_code'], 'empty_response')
        self.assertNotIn('handled', self.client.store.room('room'))

    async def test_normal_guest_intake_freezes_identity_policy_and_receipt_before_admission(self):
        def enrolled(room, op, **kw):
            self.assertEqual(room, 'room')
            if op == 'check':
                return {'mentions': [3], 'replies': []}
            if op == 'read':
                self.assertEqual(kw['target_number'], 3)
                return self.frozen
            if op == 'tool_policy':
                self.assertEqual(kw['receipt_id'], 'batch')
                return self.policy
            self.fail(op)
        self.client.enrolled = Mock(side_effect=enrolled)
        async def admitted(event):
            row = self.client.store.room('room')['processing_receipts']['3']
            self.assertEqual(row['frozen'], self.frozen)
            self.assertEqual(row['tool_policy'], self.policy)
            self.assertEqual(event.source.user_id, 'guest')
            self.assertFalse(event.allow_gateway_control)
            self.assertEqual(event.message_id, '3')
            return True
        self.a._admit_event = AsyncMock(side_effect=admitted)
        await self.a._check_room('room')
        self.a._admit_event.assert_awaited_once()
        self.assertEqual(self.client.store.room('room')['processing_receipts']['3']['state'], 'admitted')
        self.assertNotIn('handled', self.client.store.room('room'))

    async def test_advanced_intake_and_native_tools_do_not_require_oauth_policy(self):
        # Legacy Advanced bindings have no transport key. Keep that real format.
        self.client.store.update(lambda d: d['rooms']['room'].pop('transport'))
        self.message['authorParticipant']['verified'] = True
        def enrolled(room, op, **kw):
            if op == 'check':
                return {'mentions': [3]}
            if op == 'read':
                return self.frozen
            self.fail('Advanced must not request OAuth operation: ' + op)
        self.client.enrolled = Mock(side_effect=enrolled)
        self.a._admit_event = AsyncMock(return_value=True)
        await self.a._check_room('room')
        self.a._admit_event.assert_awaited_once()
        self.assertIsNone(self.a._current['room']['tool_policy'])
        current = self.a._current['room']
        mod.processed_turn(user_message=current['text'], platform='botsesh', session_id='s', turn_id='t')
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
        from hermes_cli import plugins
        with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default)), \
             patch('hermes_cli.lifecycle.invoke_hook', side_effect=lambda name, **kw: [tool_policy.enforce_tools(**kw)]):
            for tool in ('terminal', 'botsesh_post', 'botsesh_cant_do'):
                self.assertIsNone(plugins._get_pre_tool_call_directive_details(tool, {}, session_id='s', turn_id='t').action)
            # The exemption is binding-specific, never a missing-policy fallback.
            self.client.store.update(lambda d: d['rooms']['room'].update(transport='oauth'))
            self.assertEqual(plugins._get_pre_tool_call_directive_details('terminal', {}, session_id='s', turn_id='t').action, 'block')
        self.assertEqual([call.args[1] for call in self.client.enrolled.call_args_list], ['check', 'read'])
        self.assertEqual(self.posts, [])
        self.assertNotIn('handled', self.client.store.room('room'))

    async def test_unavailable_or_malformed_oauth_policy_fails_once_and_admits_later_target(self):
        for bad in (BotSeshError('method_not_found', 404), BotSeshError('temporarily_unavailable', 503),
                    None, dict(self.policy, toolsAllowed='true'), dict(self.policy, room_id='foreign')):
            with self.subTest(policy=bad):
                self.client.store.update(lambda d: d['rooms'].update(room=dict(self.binding)))
                self.a._current.clear()
                self.posts.clear()
                self.a._admit_event = AsyncMock(return_value=True)
                def enrolled(room, op, **kw):
                    if op == 'check':
                        return {'mentions': [3, 5]}
                    if op == 'read':
                        n = kw['target_number']
                        return dict(receipt_id='batch-' + str(n), batch={'messages': [dict(self.message, number=n)]})
                    if op == 'tool_policy':
                        if kw['target'] == 3:
                            if isinstance(bad, BotSeshError):
                                raise bad
                            return bad
                        return dict(self.policy, number=5, batch_id='batch-5')
                    self.fail(op)
                self.client.enrolled = Mock(side_effect=enrolled)
                await self.a._check_room('room')
                self.a._admit_event.assert_awaited_once()
                self.assertEqual(self.a._admit_event.call_args.args[0].message_id, '5')
                row = self.client.store.room('room')['processing_receipts']['3']
                self.assertEqual(row['state'], 'failed')
                self.assertFalse(row['failure_pending'])
                self.assertEqual(row['tool_policy_error']['code'], bad.code if isinstance(bad, BotSeshError) else 'native_tool_policy_scope_mismatch')
                self.assertEqual(self.posts[0]['text'], "Scout couldn't do it.")
                self.assertEqual(self.posts[0]['failure_code'], 'runtime_error')
                self.assertEqual(self.posts[0]['reply_to'], 3)
                self.assertNotIn('processed', self.posts[0])
                self.assertNotIn('receipt_id', self.posts[0])
                for _ in range(2):
                    await self.a._check_room('room')
                self.assertEqual(len(self.posts), 1)
                self.assertEqual(self.a._current['room']['target'], 5)
                self.assertNotIn('handled', self.client.store.room('room'))

    async def test_terminal_policy_revocation_never_sends_fallback_notice(self):
        def enrolled(room, op, **kw):
            if op == 'check':
                return {'mentions': [3]}
            if op == 'read':
                return self.frozen
            raise BotSeshError('authorization_revoked', 401)
        self.client.enrolled = Mock(side_effect=enrolled)
        self.a._admit_event = AsyncMock()
        with self.assertRaises(BotSeshError):
            await self.a._check_room('room')
        self.a._admit_event.assert_not_awaited()
        self.assertNotIn('room', self.a._current)
        self.assertEqual(self.posts, [])
        self.assertNotIn('handled', self.client.store.room('room'))

    async def test_terminal_cancel_interruption_and_pre_hook_failure_never_ack(self):
        for outcome, code in [('cancelled', 'cancelled'), ('interrupted', 'interrupted'), ('failed', 'runtime_error')]:
            with self.subTest(outcome=outcome):
                self.client.store.update(lambda d: d['rooms']['room'].pop('processing_receipts', None))
                self.current()
                self.a._native_state = AsyncMock(return_value=({'status': 'terminal', 'outcome': outcome}, {'failed': True}))
                await self.a._reconcile_native('room')
                self.assertEqual(self.posts[-1]['failure_code'], code)
                self.assertNotIn('receipt_id', self.posts[-1])
                self.assertNotIn('handled', self.client.store.room('room'))

    async def test_unknown_execution_is_preserved_without_replay_or_read(self):
        current = self.current()
        self.a._native_state = AsyncMock(return_value=({'status': 'unknown'}, {}))
        await self.a._reconcile_native('room')
        self.assertIs(self.a._current['room'], current)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.client.store.room('room')['health']['state'], 'execution_unknown')

    async def test_terminal_output_waits_for_exact_delivery_and_pending_retry_survives(self):
        current = self.current()
        current['model_processed'] = True
        self.a._native_state = AsyncMock(return_value=({'status': 'terminal', 'outcome': 'completed'}, {'final_response': 'Authored exact answer'}))
        await self.a._reconcile_native('room')
        self.assertIs(self.a._current['room'], current)
        payload = dict(text='Authored exact answer', processed=True, receipt_id='batch', reply_to=3, client_id=current['client_id'])
        self.a._journal_gateway_delivery('room', payload, 3, pending_final=True)
        await self.a._finish_current('room', current, 'runtime_error')
        self.assertNotIn('room', self.a._current)
        self.assertEqual(self.client.store.room('room')['pending_final']['payload'], payload)
        self.assertEqual(self.posts, [])
        # A late callback on old target cannot release a newer admitted request.
        self.a._current['room'] = dict(target=5)
        await self.a.on_processing_complete(Mock(source=Mock(chat_id='room'), message_id='3', metadata={}), ProcessingOutcome.FAILURE)
        self.assertEqual(self.a._current['room']['target'], 5)

    async def test_inline_terminal_requires_pre_hook_binding(self):
        current = self.current(native=False)
        event = Mock(source=Mock(chat_id='room'), message_id='3', metadata={})
        await self.a.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertIs(self.a._current['room'], current)
        mod.processed_turn(user_message=current['text'], platform='botsesh', session_id='s', turn_id='t')
        mod.ended_turn(platform='botsesh', session_id='s', turn_id='t', completed=True, failed=False, interrupted=False)
        await self.a.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.posts[-1]['failure_code'], 'empty_response')

    async def test_native_veto_blocks_actual_dispatch_and_rechecks_revocation(self):
        current = self.current()
        mod.processed_turn(user_message=current['text'], platform='botsesh', session_id='s', turn_id='t')
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
        self.client.enrolled = Mock(return_value=self.policy)
        from hermes_cli import plugins
        from model_tools import handle_function_call
        # Real native pre-tool directive evaluator; forced tool attempts are not model promises.
        with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default)), \
             patch('hermes_cli.lifecycle.invoke_hook', side_effect=lambda name, **kw: [tool_policy.enforce_tools(**kw)]):
            self.assertIsNone(plugins._get_pre_tool_call_directive_details('terminal', {}, session_id='s', turn_id='t').action)
            self.client.enrolled.return_value = dict(self.policy, toolsAllowed=False)
            self.assertEqual(plugins._get_pre_tool_call_directive_details('terminal', {}, session_id='s', turn_id='t').action, 'block')
            with patch('model_tools.registry.dispatch', side_effect=AssertionError('blocked tool reached dispatch')) as dispatch:
                result = json.loads(handle_function_call('terminal', {'command': 'pwd'}, session_id='s', turn_id='t', tool_call_id='forced'))
                self.assertIn('not authorized', result['error'])
                dispatch.assert_not_called()
            self.client.enrolled.side_effect = BotSeshError('membership_revoked', 403)
            self.assertEqual(plugins._get_pre_tool_call_directive_details('read_file', {}, session_id='s', turn_id='t').action, 'block')
            env['HERMES_SESSION_MESSAGE_ID'] = 'foreign'
            self.assertEqual(plugins._get_pre_tool_call_directive_details('terminal', {}, session_id='s', turn_id='t').action, 'block')
        self.assertNotIn('handled', self.client.store.room('room'))

    async def test_policy_cannot_change_binding_or_pretend_boolean(self):
        for extra in [dict(room_id='foreign'), dict(participant_id='other'), dict(number=5),
                      dict(batch_id='other'), dict(toolsAllowed='true'), dict(requester={'id': 'other', 'isOwner': True})]:
            with self.assertRaises(BotSeshError):
                tool_policy.validate_policy(dict(self.policy, **extra), self.binding, 3, 'batch', 'guest')


class InheritedTools(unittest.TestCase):
    def test_absent_inherits_but_explicit_empty_and_restrictive_are_untouched(self):
        for saved in [None, [], ['botsesh'], ['file']]:
            cfg = dict(platform_toolsets={'cli': ['terminal', 'file']})
            if saved is not None:
                cfg['platform_toolsets']['botsesh'] = saved
            writes = {}
            with patch('hermes_cli.config.load_config', return_value=cfg), \
                 patch('hermes_cli.config.set_config_value', side_effect=lambda k, v: writes.update({k: v})), \
                 patch('hermes_cli.tools_config._get_platform_tools', return_value={'terminal', 'file'}) as resolve:
                cli.configure(dict(room_id='room', name='Room'))
            if saved is None:
                self.assertEqual(json.loads(writes['platform_toolsets.botsesh']), ['file', 'terminal', 'botsesh'])
                resolve.assert_called_once_with(cfg, 'cli')
            else:
                self.assertNotIn('platform_toolsets.botsesh', writes)
                resolve.assert_not_called()
