"""0.10.2 (t_0215642b): accurate tool refusals, no raw failure JSON, in-place upgrade safety, seat reuse.

Real owner failure (Dell, 2026-11-18): plugin files were updated under a running 0.10.0 gateway, the
gateway reloaded a NEW plugin copy for hooks while the OLD channel kept running, and every owner-DM
botsesh_post was refused as a guest. The final model text, a raw {"failure": ...} object, was posted.
"""
import importlib
import json
import os
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import test_ongoing_turns as ongoing
from hermes_botsesh_test import tool_policy, runtime, final_text
from hermes_botsesh_test import adapter as mod
from hermes_botsesh_test.reliability import receipt

ENV = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
CODE = 'A' * 43
DELL_BLOB = json.dumps({'failure': {'task_id': '30a0bb1f-3fc9-41ab-91d3-254176e54aa5', 'status': 'error',
    'reason': 'room post/read/pass tools all refused with a guest tools-off gate despite the owner turn, message could not be delivered',
    'partial_notes': 'Drafted reply ready to send: casual ack plus offer to help.'}})


class Refusals(unittest.IsolatedAsyncioTestCase):
    current = ongoing.OngoingTurns.current

    def setUp(self):
        ongoing.OngoingTurns.setUp(self)
        self.enterContext(patch.object(runtime, 'mixed_here', return_value=False))

    def owner(self, allowed=True):
        return dict(self.policy, toolsAllowed=allowed, author_is_agent_owner=True, batch_all_authors_are_agent_owner=True,
                    requester=dict(id='guest', isOwner=True))

    def guest(self):
        return dict(self.policy, toolsAllowed=False, guest_tools_default=False, requester=dict(id='guest', isOwner=False))

    def call(self, name):
        with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': ENV.get(k, default)):
            return tool_policy.enforce_tools(tool_name=name, session_id='s', turn_id='t')

    def bound(self, policy):
        current = self.current()
        current['hook_turn'] = ('s', 't')
        current['tool_policy'] = policy
        receipt(self.client.store, 'room', 3, tool_policy=policy)
        self.client.enrolled = Mock(return_value=policy)
        return current

    def assertNotGuest(self, result, expected):
        self.assertEqual(result['action'], 'block')
        self.assertEqual(result['message'], expected['message'])
        self.assertNotIn('guest', result['message'].lower())

    def test_missing_frozen_source_is_an_accurate_unverified_refusal(self):
        self.bound(self.owner())
        receipt(self.client.store, 'room', 3, frozen=dict(receipt_id='batch', batch={'messages': []}))
        result = self.call('botsesh_post')
        self.assertNotGuest(result, tool_policy.UNVERIFIED)
        self.assertIn('reply in plain text and it will be posted', result['message'])

    def test_verification_exception_and_receipt_mismatch_are_not_guest_text(self):
        self.bound(self.owner())
        self.client.enrolled = Mock(side_effect=RuntimeError('boom'))
        self.assertNotGuest(self.call('botsesh_post'), tool_policy.UNVERIFIED)
        self.bound(self.owner())
        receipt(self.client.store, 'room', 3, receipt_id='other')
        self.assertNotGuest(self.call('botsesh_read'), tool_policy.UNVERIFIED)

    def test_hook_mismatch_unbound_and_ended_turns(self):
        current = self.bound(self.owner())
        current['hook_turn'] = ('other', 'turn')
        self.assertNotGuest(self.call('botsesh_post'), tool_policy.UNVERIFIED)
        current['hook_turn'] = ('s', 't')
        current['hook_terminal'] = True
        self.assertNotGuest(self.call('botsesh_post'), tool_policy.ENDED)
        self.a._current.clear()
        self.assertNotGuest(self.call('botsesh_post'), tool_policy.NOT_A_TURN)

    def test_owner_turn_room_post_is_allowed_and_owner_tools_off_is_named_as_such(self):
        self.bound(self.owner())
        self.assertIsNone(self.call('botsesh_post'))
        self.assertIsNone(self.call('terminal'))
        self.bound(self.owner(allowed=False))
        self.assertIsNone(self.call('botsesh_post'))
        self.assertNotGuest(self.call('terminal'), tool_policy.OWNER_TOOLS_OFF)

    def test_guest_turn_stays_tools_off_with_the_guest_message(self):
        self.bound(self.guest())
        self.assertIsNone(self.call('botsesh_post'))
        for name in ('terminal', 'execute_code', 'read_file', 'botsesh_create_room'):
            result = self.call(name)
            self.assertEqual(result['action'], 'block', name)
            self.assertEqual(result['message'], tool_policy.DENIED['message'], name)

    def test_mixed_runtime_blocks_every_tool_with_the_updating_message(self):
        self.bound(self.owner())
        with patch.object(runtime, 'mixed_here', return_value=True):
            for name in ('botsesh_post', 'botsesh_read', 'terminal'):
                result = self.call(name)
                self.assertEqual(result, tool_policy.UPDATING, name)
                self.assertIn('Reply in plain text and it will be posted', result['message'])
        self.bound(self.guest())
        with patch.object(runtime, 'mixed_here', return_value=True):
            self.assertEqual(self.call('terminal')['action'], 'block')  # never widens a guest turn

    def test_dm_turn_context_says_the_plain_reply_is_posted(self):
        for policy in (self.owner(), self.guest()):
            text = tool_policy.turn_context(policy, dm=True)
            self.assertIn('your final plain-text reply IS posted to this DM', text)
            self.assertIn('Do not call botsesh_post to answer', text)
            self.assertNotIn('IS posted', tool_policy.turn_context(policy))


class FinalText(unittest.IsolatedAsyncioTestCase):
    def test_dell_failure_object_becomes_one_human_line(self):
        line = final_text.human_text(DELL_BLOB)
        self.assertTrue(line.startswith("I couldn't finish that — room post/read/pass tools all refused"), line)
        self.assertNotIn('{', line)
        self.assertNotIn('\n', line)
        self.assertLessEqual(len(line), 240)
        self.assertEqual(final_text.human_text('```json\n' + DELL_BLOB + '\n```'), line)
        self.assertEqual(final_text.human_text(line), line)  # idempotent: journal and send agree

    def test_error_objects_reply_wrappers_and_ordinary_text(self):
        self.assertEqual(final_text.human_text('{"error": {"code": "receipt_expired"}}'), "I couldn't finish that — receipt_expired")
        self.assertEqual(final_text.human_text('{"error": "tool refused"}'), "I couldn't finish that — tool refused")
        self.assertEqual(final_text.human_text('{"reply": {"text": "Hello there"}}'), 'Hello there')
        self.assertEqual(final_text.human_text('{"failure": {}}'), final_text.FALLBACK)
        for keep in ('PONG 2407', '{"name": "Ada", "age": 3}', '{"status": "ok"}', 'Here is JSON: {"failure": 1}', '{not json}'):
            self.assertEqual(final_text.human_text(keep), keep)

    async def test_adapter_final_posts_the_line_not_the_object(self):
        case = ongoing.OngoingTurns('test_exact_active_guest_and_busy_intake_keep_native_room_fence')
        ongoing.OngoingTurns.setUp(case)
        self.addCleanup(case.doCleanups)
        current = ongoing.OngoingTurns.current(case)
        current['model_processed'] = True
        case.client.enrolled = Mock(return_value={'pending': None})
        async def post(room, payload, target=None):
            case.posts.append(dict(payload))
            return {'number': 9, 'read_effect': 'processed_frozen_receipt'}
        case.a._post_payload = post
        result = await case.a.send('room', DELL_BLOB, metadata={'notify': True})
        self.assertTrue(result.success, result.error)
        self.assertEqual(case.posts[-1]['text'], final_text.human_text(DELL_BLOB))
        self.assertEqual(case.posts[-1]['reply_to'], 3)
        saved = case.client.store.room('room')['gateway_deliveries']
        self.assertTrue(all('{' not in r['payload']['text'] for r in saved.values()))


class OldAdapter:  # an adapter object from the pre-reload copy: same module name, different class
    pass


OldAdapter.__module__ = mod.BotSeshAdapter.__module__


class UpgradeSafety(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.enterContext(patch.dict('os.environ', {'HERMES_HOME': str(self.home)}))
        os.environ.pop('_HERMES_GATEWAY', None)  # a CLI process unless a test says otherwise (restored by patch.dict)
        from hermes_botsesh_test.storage import Store
        self.store = Store(Path(self.tmp.name) / 'private')

    def runner(self, adapter):
        from gateway.config import Platform
        return types.SimpleNamespace(adapters={Platform('botsesh'): adapter}, _profile_adapters={})

    def test_mixed_here_detects_a_pre_reload_channel_and_changed_files(self):
        self.assertEqual(runtime.LOADED_VERSION, runtime.disk_version())
        with patch.object(runtime, '_gateway_runner', return_value=None):
            self.assertFalse(runtime.mixed_here())
        with patch.object(runtime, '_gateway_runner', return_value=self.runner(OldAdapter())):
            self.assertTrue(runtime.mixed_here())
        same = mod.BotSeshAdapter.__new__(mod.BotSeshAdapter)
        with patch.object(runtime, '_gateway_runner', return_value=self.runner(same)):
            self.assertFalse(runtime.mixed_here())
        other = type('OtherProfileAdapter', (), {'__module__': 'hermes_plugins.botsesh__home_abc.adapter'})()
        with patch.object(runtime, '_gateway_runner', return_value=self.runner(other)):
            self.assertFalse(runtime.mixed_here(), 'another profile copy is not ours to judge')
        with patch.object(runtime, '_gateway_runner', return_value=None), patch.object(runtime, 'disk_version', return_value='9.9.9'):
            self.assertTrue(runtime.mixed_here())

    def test_gateway_marker_and_seat_creation_decision(self):
        marker = self.home / 'plugin-data' / 'botsesh' / 'runtime.json'
        with patch.dict('os.environ', {'_HERMES_GATEWAY': '1'}), patch('os.getpid', return_value=4242), \
                patch.object(runtime, '_gateway_process', return_value=True), \
                patch.object(runtime, '_gateway_runner', return_value=self.runner(OldAdapter())):
            self.assertTrue(runtime.publish())
        saved = json.loads(marker.read_text())
        self.assertEqual((saved['pid'], saved['mixed']), (4242, True))
        self.assertEqual(oct(marker.stat().st_mode & 0o777), '0o600')
        # The background `hermes botsesh connect` the gateway spawns inherits _HERMES_GATEWAY=1 but builds no
        # GatewayRunner: it must never overwrite the gateway's record with its own pid (seen live in swap1).
        with patch.dict('os.environ', {'_HERMES_GATEWAY': '1'}), patch('os.getpid', return_value=5151), \
                patch.dict('sys.modules', {'gateway.run': types.SimpleNamespace(_gateway_runner_ref=lambda: None)}):
            self.assertFalse(runtime._gateway_process())
            self.assertFalse(runtime.publish())
        self.assertEqual(json.loads(marker.read_text())['pid'], 4242)
        with patch.object(runtime, '_gateway_runner', return_value=None):
            with patch.object(runtime, '_running_gateway_pid', return_value=4242):
                self.assertEqual(runtime.gateway_state(self.store), 'swapping')
            with patch.object(runtime, '_running_gateway_pid', return_value=None):
                self.assertEqual(runtime.gateway_state(self.store), 'current', 'no running gateway: it starts with the files on disk')
            marker.write_text(json.dumps({'pid': 4242, 'version': runtime.LOADED_VERSION, 'mixed': False, 'serving': True}))
            with patch.object(runtime, '_running_gateway_pid', return_value=4242):
                self.assertEqual(runtime.gateway_state(self.store), 'current')
            marker.write_text(json.dumps({'pid': 4242, 'version': '0.10.0', 'mixed': False, 'serving': True}))
            with patch.object(runtime, '_running_gateway_pid', return_value=4242):
                self.assertEqual(runtime.gateway_state(self.store), 'old')
            marker.unlink()
            with patch.object(runtime, '_running_gateway_pid', return_value=777):
                self.assertEqual(runtime.gateway_state(self.store), 'current', 'fresh install: hot start loads current files')
                self.store.update(lambda d: d['rooms'].update(dm={'room_id': 'dm', 'active': True, 'transport': 'oauth'}))
                self.assertEqual(runtime.gateway_state(self.store), 'old', 'pre-0.10.2 gateway already serving rooms')
                # An old gateway is asked once to load these files (reload-plugins -> hot swap), then waited for.
                hot_start = importlib.import_module('hermes_botsesh_test.hot_start')
                states = iter(['old', 'swapping', 'current'])
                import time as real_time
                fake = lambda *clock: types.SimpleNamespace(sleep=lambda s: None, time=real_time.time,
                                                            monotonic=(lambda it=iter(clock): next(it)) if clock else real_time.monotonic)
                with patch.object(runtime, 'gateway_state', side_effect=lambda store=None: next(states)), \
                        patch.object(hot_start, 'nudge_running_gateway', return_value=True) as nudge, patch.object(runtime, 'time', fake()):
                    self.assertTrue(runtime.wait_until_current(self.store, timeout=30))
                nudge.assert_called_once()
                with patch.object(runtime, 'gateway_state', return_value='swapping'), patch.object(runtime, 'time', fake(0, 0, 100, 100)):
                    self.assertFalse(runtime.wait_until_current(self.store, timeout=30), 'stuck swap -> honest fallback')

    def test_join_link_waits_for_current_code_and_reuses_the_seat_profile(self):
        tools = importlib.import_module('hermes_botsesh_test.tools')
        cli = importlib.import_module('hermes_botsesh_test.cli')
        identity = importlib.import_module('hermes_botsesh_test.identity')
        ctx = types.SimpleNamespace(session_surface=lambda: 'telegram', get_config=lambda *a: '')
        pasted = f'Join me on BotSesh: https://botsesh.com/a/{CODE}\n(Agent: fetch this link with your terminal or web tool and follow its steps yourself.)'
        client = types.SimpleNamespace(store=self.store)
        with patch.object(tools, 'Client', return_value=client), patch.object(cli, 'spawn_connect', return_value='started') as spawn, \
                patch.object(runtime, 'wait_until_current', return_value=False):
            result = tools.join({'invitation_url': pasted}, ctx)
        spawn.assert_not_called()
        self.assertEqual(result['state'], 'restart_required')
        self.assertEqual(result['tell_your_person'], 'BotSesh was updated; restart Hermes to finish connecting.')
        self.assertIn('Do not reinstall', result['next'])
        self.store.update(lambda d: d['rooms'].update(dm={'room_id': 'dm', 'active': True, 'transport': 'oauth',
                                                           'origin': 'https://botsesh.com', 'profile': 'goonbot'}))
        with patch.object(tools, 'Client', return_value=client), patch.object(cli, 'spawn_connect', return_value='started') as spawn, \
                patch.object(runtime, 'wait_until_current', return_value=True) as wait, patch.object(identity, 'current_profile', return_value='default'):
            result = tools.join({'invitation_url': pasted}, ctx)
        wait.assert_called_once()
        spawn.assert_called_once_with('bsp_' + CODE, 'https://botsesh.com', 'goonbot')
        self.assertEqual(result['state'], 'waiting_for_allow')
        self.assertIsNone(tools._pasted_link(f'https://botsesh.com/a/{CODE}X and https://botsesh.com/a/{"B" * 43}x'))

    def test_agents_page_invite_names_the_one_owner_dm_seat_even_with_other_rooms(self):
        oauth = importlib.import_module('hermes_botsesh_test.oauth')
        identity = importlib.import_module('hermes_botsesh_test.identity')
        base = {'active': True, 'transport': 'oauth', 'origin': 'https://botsesh.com', 'profile': 'goonbot'}
        self.store.update(lambda d: d['rooms'].update(
            dm={**base, 'room_id': 'dm', 'participant_id': '11111111-1111-4111-8111-111111111111', 'room_type': 'dm'},
            work={**base, 'room_id': 'work', 'participant_id': '22222222-2222-4222-8222-222222222222', 'room_type': 'room'}))
        seen = {}

        def login(**kw):
            seen.update(kw)
            raise RuntimeError('stop after the authorize request')
        client = oauth.OAuthClient(self.store)
        for room_hint, pair in ((None, 'bsp_' + CODE), ('work', None)):
            seen.clear()
            with patch.object(oauth, 'login', side_effect=login), patch.object(oauth, 'resolve_pair', return_value=None), \
                    patch.object(identity, 'gateway_install_id', return_value='gw-1'):
                with self.assertRaises(RuntimeError):
                    client._connect(origin='https://botsesh.com', open_browser=False, timeout=5, room_hint=room_hint, name=None,
                                    profile='goonbot', paste_redirect=False, pair_code=pair)
            if room_hint is None:
                self.assertEqual(seen['previous']['room_id'], 'dm', 'account invite: keep the existing owner DM seat')
            else:
                self.assertEqual(seen['previous']['room_id'], 'work')
            self.assertEqual(seen['profile'], 'goonbot')

    def test_connect_cli_falls_back_to_one_restart_line_only_when_the_swap_cannot_finish(self):
        cli = importlib.import_module('hermes_botsesh_test.cli')
        args = types.SimpleNamespace(botsesh_action='connect', pair=None, pair_link=f'https://botsesh.com/a/{CODE}', dev_origin='https://botsesh.com',
                                     background=True, timeout=300, room=None, name=None, no_browser=True, set_home=False, no_start=True)
        with patch.object(cli, 'Client', return_value=types.SimpleNamespace(store=self.store)), \
                patch.object(runtime, 'gateway_state', return_value='swapping'), patch.object(runtime, 'wait_until_current', return_value=False), \
                patch.object(cli, 'connect_background') as background, patch.object(cli, 'spawn_connect') as spawn, \
                patch('builtins.print') as printed:
            with self.assertRaises(SystemExit) as stop:
                cli._handle_profile(args, 'default')
        self.assertEqual(stop.exception.code, 3)
        background.assert_not_called(); spawn.assert_not_called()
        self.assertIn('BotSesh was updated; restart Hermes to finish connecting.', [c.args[0] for c in printed.call_args_list])
        with patch.object(cli, 'Client', return_value=types.SimpleNamespace(store=self.store)), \
                patch.object(runtime, 'gateway_state', return_value='swapping'), patch.object(runtime, 'wait_until_current', return_value=True), \
                patch.object(cli, 'connect_background') as background, patch('builtins.print'):
            cli._handle_profile(args, 'default')
        background.assert_called_once()


class HotSwap(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(patch.object(runtime, 'SWAP_BLOCKED', None))  # module state never leaks between tests
        self.enterContext(patch.object(runtime, 'publish', return_value=False))

    @staticmethod
    def core(adapter):
        """The GatewayRunner internals the swap relies on (bf0203a names)."""
        from gateway.config import Platform
        return types.SimpleNamespace(adapters={Platform('botsesh'): adapter} if adapter is not None else {}, _profile_adapters={},
                                     _failed_platforms={}, config=types.SimpleNamespace(platforms={Platform('botsesh'): object()}),
                                     _queue_retryable_fatal_platform=Mock(), _reconnect_failed_platform=Mock())

    @staticmethod
    def channel():
        """An old (pre-reload) channel that really answers: its supervisor keeps one room loop alive."""
        import asyncio
        old = OldAdapter()
        old._current, old._running, old._fatal_error_code = {}, True, None
        old.inbox, old.answered = asyncio.Queue(), []
        old._fatal_error_handler = Mock()
        old.supervisors = 0

        async def room_loop():
            while old._running:
                old.answered.append(await old.inbox.get())

        async def supervise():  # like BotSeshAdapter._supervise -> _sync_rooms_loop
            old.supervisors += 1
            while old._running:
                if 'room' not in old._rooms or old._rooms['room'].done():
                    old._rooms['room'] = asyncio.ensure_future(room_loop())
                await asyncio.sleep(0.05)

        def set_fatal(code, message, *, retryable):  # base: _running=False first, then the fields
            old._running = False
            old._fatal_error_code = code

        def mark_connected():
            old._running, old._fatal_error_code = True, None
        old._supervise, old._mark_connected = supervise, mark_connected
        old._set_fatal_error = Mock(side_effect=set_fatal)
        old._notify_fatal_error = Mock(side_effect=lambda: asyncio.sleep(0))
        old._tasks, old._rooms = [asyncio.ensure_future(supervise())], {}
        return old

    async def answers(self, old, text):
        import asyncio
        await asyncio.sleep(0.15)
        old.inbox.put_nowait(text)
        for _ in range(40):
            await asyncio.sleep(0.05)
            if text in old.answered:
                return True
        return False

    def assertRestartFallback(self, runner):
        with patch.object(runtime, '_gateway_runner', return_value=runner):
            self.assertEqual(runtime.gateway_state(), 'restart')
            with patch.object(runtime.time, 'sleep', side_effect=AssertionError('no 90 s wait for a swap that cannot run')):
                self.assertFalse(runtime.wait_until_current(timeout=90))
            self.assertTrue(runtime.mixed_here())
            with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': ENV.get(k, default)):
                self.assertEqual(tool_policy.enforce_tools(tool_name='botsesh_post', session_id='s', turn_id='t'),
                                 tool_policy.UPDATE_NEEDS_RESTART)

    async def test_missing_hermes_internals_cancel_nothing_and_the_old_channel_keeps_answering(self):
        import asyncio
        gaps = {
            'adapter _notify_fatal_error': lambda old, runner: delattr(old, '_notify_fatal_error'),
            'adapter _set_fatal_error': lambda old, runner: setattr(old, '_set_fatal_error', None),
            'no fatal handler wired by the runner': lambda old, runner: setattr(old, '_fatal_error_handler', None),
            'runner reconnect step renamed': lambda old, runner: delattr(runner, '_reconnect_failed_platform'),
            'runner reconnect queue renamed': lambda old, runner: delattr(runner, '_failed_platforms'),
            'no gateway config entry for botsesh': lambda old, runner: runner.config.platforms.clear(),
        }
        for label, remove in gaps.items():
            with self.subTest(label), patch.object(runtime, 'SWAP_BLOCKED', None):
                old = self.channel()
                runner = self.core(old)
                set_fatal = old._set_fatal_error
                remove(old, runner)
                self.assertTrue(await self.answers(old, 'before'))
                loops = [*old._tasks, *old._rooms.values()]
                self.assertFalse(await asyncio.wait_for(runtime._hot_swap(runner, old, idle_wait=1), 5))
                self.assertFalse(any(t.cancelled() or t.done() for t in loops), 'nothing is cancelled')
                if set_fatal is not None:
                    set_fatal.assert_not_called()
                self.assertTrue(old._running)
                self.assertIsNone(old._fatal_error_code)
                self.assertIs(runner.adapters[next(iter(runner.adapters))], old, 'the old channel stays registered')
                self.assertTrue(await self.answers(old, 'after ' + label), 'the old channel keeps answering')
                self.assertEqual(old.supervisors, 1)
                self.assertIn('lacks', runtime.SWAP_BLOCKED['reason'])
                self.assertRestartFallback(runner)
                for task in [*old._tasks, *old._rooms.values()]:
                    task.cancel()

    async def test_exception_while_retiring_revives_the_old_channel_with_no_half_state(self):
        import asyncio
        # 1) _set_fatal_error raises after it already flipped _running: nothing was cancelled yet.
        old = self.channel()
        runner = self.core(old)
        self.assertTrue(await self.answers(old, 'one'))
        loops = [*old._tasks, *old._rooms.values()]

        def broken(code, message, *, retryable):
            old._running = False
            raise AttributeError('renamed status writer')
        old._set_fatal_error = Mock(side_effect=broken)
        self.assertFalse(await asyncio.wait_for(runtime._hot_swap(runner, old, idle_wait=1), 5))
        self.assertFalse(any(t.cancelled() or t.done() for t in loops), 'loops never saw _running=False')
        self.assertTrue(old._running)
        old._fatal_error_handler.assert_not_called()
        self.assertTrue(await self.answers(old, 'two'))
        self.assertEqual(runtime.SWAP_BLOCKED['reason'], 'retire failed')
        self.assertRestartFallback(runner)
        for task in [*old._tasks, *old._rooms.values()]:
            task.cancel()

        # 2) _notify_fatal_error raises and the core never took the channel: loops were cancelled, so it is
        #    revived (fatal cleared, supervisor restarted, room loop re-created) instead of staying silent.
        with patch.object(runtime, 'SWAP_BLOCKED', None):
            old = self.channel()
            runner = self.core(old)
            self.assertTrue(await self.answers(old, 'three'))
            first_loops = [*old._tasks, *old._rooms.values()]
            old._notify_fatal_error = Mock(side_effect=RuntimeError('handler moved'))
            self.assertFalse(await asyncio.wait_for(runtime._hot_swap(runner, old, idle_wait=1), 5))
            await asyncio.sleep(0)
            self.assertTrue(all(t.cancelled() for t in first_loops))
            self.assertTrue(old._running)
            self.assertIsNone(old._fatal_error_code)
            self.assertIs(runner.adapters[next(iter(runner.adapters))], old)
            self.assertTrue(await self.answers(old, 'four'), 'revived channel answers again')
            self.assertEqual(old.supervisors, 2)
            self.assertEqual(runtime.SWAP_BLOCKED['reason'], 'handover failed')
            self.assertRestartFallback(runner)
            for task in [*old._tasks, *old._rooms.values()]:
                task.cancel()

        # 3) The fatal report raised AFTER the core popped and queued the channel: the core owns the handover.
        with patch.object(runtime, 'SWAP_BLOCKED', None):
            old = self.channel()
            runner = self.core(old)
            self.assertTrue(await self.answers(old, 'five'))

            def popped():
                runner._failed_platforms[next(iter(runner.adapters))] = {'predecessor': old}
                runner.adapters.clear()
                raise RuntimeError('disconnect hiccup')
            old._notify_fatal_error = Mock(side_effect=popped)
            self.assertTrue(await asyncio.wait_for(runtime._hot_swap(runner, old, idle_wait=1), 5))
            self.assertFalse(old._running)
            self.assertEqual(old.supervisors, 1, 'never revived behind the core')
            self.assertIsNone(runtime.SWAP_BLOCKED)

    async def test_unexpected_error_in_the_scheduled_swap_is_logged_and_leaves_the_restart_line(self):
        old = self.channel()
        with patch.object(runtime, '_hot_swap', side_effect=RuntimeError('boom')), self.assertLogs(runtime.logger, 'WARNING'):
            self.assertFalse(await runtime._hot_swap_logged(self.core(old), old))
        self.assertEqual(runtime.SWAP_BLOCKED['reason'], 'unexpected error')
        for task in old._tasks:
            task.cancel()

    async def test_old_channel_finishes_its_turn_then_retires_retryably(self):
        import asyncio
        from gateway.config import Platform
        old = OldAdapter()
        old._current = {'room': {'target': 3}}
        loop_task = asyncio.ensure_future(asyncio.sleep(3600))
        room_task = asyncio.ensure_future(asyncio.sleep(3600))
        old._tasks, old._rooms = [loop_task], {'room': room_task}
        fatal = []
        old._set_fatal_error = lambda code, message, retryable: fatal.append((code, retryable, list(old._current)))
        old._notify_fatal_error = Mock(side_effect=lambda: asyncio.sleep(0))
        old._fatal_error_handler, old._supervise, old._mark_connected = Mock(), Mock(), Mock()
        runner = self.core(old)
        swap = asyncio.ensure_future(runtime._hot_swap(runner, old, idle_wait=30))
        await asyncio.sleep(0.6)
        self.assertEqual(fatal, [], 'an in-flight turn is never cut off')
        self.assertFalse(loop_task.cancelled())
        old._current.clear()  # the old channel delivered its final
        self.assertTrue(await asyncio.wait_for(swap, 5))
        self.assertEqual(fatal, [(runtime.SWAP_CODE, True, [])])
        old._notify_fatal_error.assert_called_once()
        await asyncio.sleep(0)
        self.assertTrue(loop_task.cancelled() and room_task.cancelled())
        self.assertIsNone(runtime.SWAP_BLOCKED)
        # Already replaced (e.g. a second update): never retire the healthy new channel.
        newer = OldAdapter(); newer._current = {}
        newer._set_fatal_error = Mock(); newer._notify_fatal_error = Mock()
        newer._fatal_error_handler, newer._supervise, newer._mark_connected, newer._tasks, newer._rooms = Mock(), Mock(), Mock(), [], {}
        self.assertFalse(await runtime._hot_swap(self.core(None), newer, idle_wait=1))
        newer._set_fatal_error.assert_not_called()
        self.assertIsNone(runtime.SWAP_BLOCKED, 'an already-finished handover is not a blocked swap')


if __name__ == '__main__':
    unittest.main()
