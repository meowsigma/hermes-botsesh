"""B1 (LAUNCH FIX): the plugin works on Hermes cores on both sides of the session-store refactor.

Doubles only. The real-core proof (stock Hermes images built from caafe538 and bf0203a7, native gateway, listener and
scripted model) is the harness recorded in the B1 report; these tests pin the three seams it exposed:

* A5b-03: a native turn's admission row and result are read through the core's supported persistence interface
  (``authority.persistence.admissions``) and only fall back to ``hermes_state_runtime`` on cores that still ship it.
* A5b-02: a room message that arrives before the gateway runner has published this adapter (Hermes connects all platforms
  at once and publishes them together) waits; it is not refused for good as ``admission_denied``.
* A5b-04: an ended access produces exactly one stop notice, whichever of the listener wait, the room check or the
  supervisor sees it first.
"""
import asyncio
import contextlib
import enum
import logging
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_plugin import BotSeshError
import test_ongoing_turns
from hermes_botsesh_test import adapter as mod, native_turn


class Status(str, enum.Enum):
    """Stands in for the core's StrEnum admission status."""
    QUEUED = 'queued'
    STARTED = 'started'
    TERMINAL = 'terminal'


def record(status='terminal', outcome='completed', session='s'):
    return types.SimpleNamespace(admission_id='a', status=Status(status), outcome=outcome, target_session_id=session)


def stubs(**modules):
    """Replace core modules for one block; ``None`` makes the import itself fail, which proves a path is not taken."""
    return patch.dict(sys.modules, modules)


class ReadAdmission(unittest.TestCase):
    def test_profile_scoped_interface_wins_and_never_touches_the_removed_module(self):
        admissions = Mock()
        admissions.get.return_value = record()
        store = types.SimpleNamespace(admissions=admissions)
        results = types.ModuleType('gateway.session_results')
        results.admission_result = Mock(return_value={'result': {'final_response': 'hi'}})
        with stubs(**{'gateway.session_results': results, 'hermes_state_runtime': None}):
            row, result = native_turn.read_admission(types.SimpleNamespace(persistence=store, db=object()), 'a')
        self.assertEqual(row, {'admission_id': 'a', 'status': 'terminal', 'outcome': 'completed', 'target_session_id': 's'})
        self.assertEqual(result, {'result': {'final_response': 'hi'}})
        admissions.get.assert_called_once_with(admission_id='a')
        results.admission_result.assert_called_once_with(store, 'a')

    def test_a_running_admission_has_no_result_and_an_unknown_one_has_no_row(self):
        admissions = Mock()
        results = types.ModuleType('gateway.session_results')
        results.admission_result = Mock()
        authority = types.SimpleNamespace(persistence=types.SimpleNamespace(admissions=admissions))
        with stubs(**{'gateway.session_results': results, 'hermes_state_runtime': None}):
            admissions.get.return_value = record('started', None)
            row, result = native_turn.read_admission(authority, 'a')
            self.assertEqual((row['status'], row['outcome'], result), ('started', None, None))
            admissions.get.return_value = None
            self.assertEqual(native_turn.read_admission(authority, 'a'), (None, None))
        results.admission_result.assert_not_called()

    def test_core_that_still_ships_the_state_module_keeps_the_raw_handle_path(self):
        db = object()
        state = types.ModuleType('hermes_state_runtime')
        state.get_session_admission = Mock(return_value={'status': 'terminal', 'outcome': 'completed', 'target_session_id': 's'})
        results = types.ModuleType('gateway.session_results')
        results.admission_result = Mock(return_value={'result': {'completed': True}})
        with stubs(**{'gateway.session_results': results, 'hermes_state_runtime': state}):
            row, result = native_turn.read_admission(types.SimpleNamespace(db=db), 'a')
            self.assertEqual((row['status'], row['target_session_id'], result), ('terminal', 's', {'result': {'completed': True}}))
            state.get_session_admission.assert_called_once_with(db, admission_id='a')
            results.admission_result.assert_called_once_with(db, 'a')
            state.get_session_admission.return_value = {'status': 'queued', 'outcome': None, 'target_session_id': 's'}
            results.admission_result.reset_mock()
            self.assertIsNone(native_turn.read_admission(types.SimpleNamespace(db=db), 'a')[1])
            results.admission_result.assert_not_called()


class NativeState(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        test_ongoing_turns.OngoingTurns.setUp(self)
        self.current = test_ongoing_turns.OngoingTurns.current(self)
        self.current['event'] = Mock(source=Mock(chat_id='room'))
        self.admissions = Mock()
        self.results = types.ModuleType('gateway.session_results')
        self.results.admission_result = Mock(return_value={'result': {'completed': True, 'final_response': 'hello'}})
        self.authority = types.SimpleNamespace(profile_id='profile', persistence=types.SimpleNamespace(admissions=self.admissions))

    def scope(self):
        @contextlib.asynccontextmanager
        async def producer_scope(adapter, event):
            yield self.authority
        return patch('gateway.platforms.webhook_ingress.producer_scope', producer_scope)

    async def test_terminal_completion_settles_through_the_persistence_interface(self):
        self.admissions.get.return_value = record()
        with self.scope(), stubs(**{'gateway.session_results': self.results, 'hermes_state_runtime': None}):
            row, value = await self.a._native_state(self.current)
        self.assertEqual((row['status'], value['final_response']), ('terminal', 'hello'))
        self.assertTrue(self.current['model_processed'])
        self.assertEqual(self.current['native_terminal'], 'completed')

    async def test_a_failed_or_interrupted_result_is_not_a_processed_turn(self):
        for result in ({'failed': True}, {'interrupted': True}, {'error': 'boom'}):
            with self.subTest(result=result):
                self.admissions.get.return_value = record(outcome='completed')
                self.results.admission_result.return_value = {'result': result}
                with self.scope(), stubs(**{'gateway.session_results': self.results, 'hermes_state_runtime': None}):
                    await self.a._native_state(self.current)
                self.assertFalse(self.current['model_processed'])
        self.admissions.get.return_value = record(outcome='failed')
        self.results.admission_result.return_value = {'result': {}}
        with self.scope(), stubs(**{'gateway.session_results': self.results, 'hermes_state_runtime': None}):
            await self.a._native_state(self.current)
        self.assertFalse(self.current['model_processed'])

    async def test_foreign_profile_or_session_or_missing_row_is_refused(self):
        with stubs(**{'gateway.session_results': self.results, 'hermes_state_runtime': None}), self.scope():
            self.admissions.get.return_value = record(session='another-session')
            with self.assertRaises(BotSeshError) as error:
                await self.a._native_state(self.current)
            self.assertEqual(error.exception.code, 'native_admission_missing')
            self.admissions.get.return_value = None
            with self.assertRaises(BotSeshError) as error:
                await self.a._native_state(self.current)
            self.assertEqual(error.exception.code, 'native_admission_missing')
            self.authority.profile_id = 'foreign-profile'
            with self.assertRaises(BotSeshError) as error:
                await self.a._native_state(self.current)
            self.assertEqual(error.exception.code, 'native_admission_scope_mismatch')


class RunnerRegistration(unittest.IsolatedAsyncioTestCase):
    """Hermes publishes adapters on its runner only after every platform's connect() returned."""

    class Passed(Exception):
        pass

    def setUp(self):
        test_ongoing_turns.OngoingTurns.setUp(self)
        self.registered = [False]
        runner = types.SimpleNamespace(_owning_profile=lambda adapter, platform: (self.registered[0] and adapter is self.a, None))
        self.a._message_handler = types.SimpleNamespace(__self__=runner)
        self.a._running = True
        self.a._refresh_dm_controls = AsyncMock(side_effect=self.Passed)

    async def test_room_pass_waits_for_publication_then_continues(self):
        task = asyncio.create_task(self.a._check_room('room'))
        await asyncio.sleep(0.5)
        self.assertFalse(task.done())
        self.a._refresh_dm_controls.assert_not_awaited()
        self.registered[0] = True
        with self.assertRaises(self.Passed):
            await asyncio.wait_for(task, 3)

    def enroll_one_question(self):
        message = dict(number=3, senderParticipantId='guest', isOwn=False, text='Question',
                       authorParticipant=dict(id='guest', verified=False, active=True, name='Guest'))
        frozen = dict(receipt_id='batch', batch={'messages': [message]})
        policy = dict(room_id='room', participant_id='seat', number=3, batch_id='batch', toolsAllowed=True,
                      author_is_agent_owner=False, guest_tools_default=False, tools_ceiling_allowed=True,
                      requester=dict(id='guest', isOwner=False))
        self.client.enrolled = Mock(side_effect=lambda room, op, **kw: {'mentions': [3], 'replies': []} if op == 'check'
                                    else frozen if op == 'read' else policy)
        self.a._refresh_dm_controls = AsyncMock()

    async def test_message_before_publication_is_not_refused_and_is_answered_after(self):
        self.enroll_one_question()
        self.a._is_sender_authorized = lambda author, kind, room: self.registered[0]  # the runner's verdict before publication: no
        self.a._admit_event = AsyncMock(return_value=True)
        task = asyncio.create_task(self.a._check_room('room'))
        await asyncio.sleep(0.5)
        self.a._admit_event.assert_not_awaited()
        self.a._delivery.assert_not_awaited()  # nothing was reported failed while waiting
        self.registered[0] = True
        await asyncio.wait_for(task, 3)
        self.a._admit_event.assert_awaited_once()
        for call in self.a._delivery.await_args_list:
            self.assertNotEqual(call.args[2:3], ('failed',))
        self.assertEqual(self.client.store.room('room')['processing_receipts']['3']['state'], 'admitted')

    async def test_after_publication_an_unauthorized_sender_is_still_refused(self):
        # The wait is not a way around authorization: once the runner can answer, "no" is still a permanent refusal.
        self.registered[0] = True
        self.enroll_one_question()
        self.a._is_sender_authorized = lambda author, kind, room: False
        self.a._admit_event = AsyncMock(return_value=True)
        await asyncio.wait_for(self.a._check_room('room'), 3)
        self.a._admit_event.assert_not_awaited()
        self.a._delivery.assert_awaited_with('room', 3, 'failed', failure_code='admission_denied')
        self.assertEqual(self.posts, [])

    async def test_an_inactive_or_spoofed_author_is_refused_even_after_publication(self):
        self.registered[0] = True
        for bad in (dict(authorParticipant=dict(id='guest', verified=False, active=False)),
                    dict(senderParticipantId='someone-else'), dict(authorParticipant=dict(id='guest', verified=False))):
            with self.subTest(bad=bad):
                self.client.store.update(lambda d: d['rooms']['room'].pop('processing_receipts', None))
                self.a._delivery.reset_mock()
                base = dict(number=3, senderParticipantId='guest', isOwn=False, text='Question',
                            authorParticipant=dict(id='guest', verified=False, active=True, name='Guest'))
                frozen = dict(receipt_id='batch', batch={'messages': [{**base, **bad}]})
                self.client.enrolled = Mock(side_effect=lambda room, op, **kw: {'mentions': [3], 'replies': []} if op == 'check' else frozen)
                self.a._refresh_dm_controls = AsyncMock()
                self.a._is_sender_authorized = lambda author, kind, room: True
                self.a._admit_event = AsyncMock(return_value=True)
                await asyncio.wait_for(self.a._check_room('room'), 3)
                self.a._admit_event.assert_not_awaited()
                self.a._delivery.assert_awaited_with('room', 3, 'failed', failure_code='admission_denied')

    async def test_a_stopped_channel_does_not_wait_forever(self):
        self.a._running = False
        await asyncio.wait_for(self.a._check_room('room'), 1)
        self.a._refresh_dm_controls.assert_not_awaited()

    async def test_no_runner_or_an_unreadable_one_never_holds_the_room(self):
        for handler in (None, types.SimpleNamespace(__self__=None),
                        types.SimpleNamespace(__self__=types.SimpleNamespace(_owning_profile=Mock(side_effect=RuntimeError)))):
            with self.subTest(handler=handler):
                self.a._message_handler = handler
                with self.assertRaises(self.Passed):
                    await asyncio.wait_for(self.a._check_room('room'), 1)

    async def test_runner_without_the_profile_helper_is_read_through_its_adapter_table(self):
        runner = types.SimpleNamespace(adapters={self.a.platform: self.a})
        self.a._message_handler = types.SimpleNamespace(__self__=runner)
        self.assertTrue(self.a._registered_with_runner())
        runner.adapters = {self.a.platform: object()}
        self.assertFalse(self.a._registered_with_runner())


class StopNotice(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        test_ongoing_turns.OngoingTurns.setUp(self)
        self.a._running = True

    def lines(self, logs):
        return [r for r in logs.records if ' stopped: ' in r.getMessage()]

    def test_one_notice_per_episode_and_a_working_check_rearms_it(self):
        with self.assertLogs(mod.logger, logging.ERROR) as logs:
            for _ in range(3):
                self.a._stop_notice('room', 'authorization_revoked')
        self.assertEqual([r.getMessage() for r in self.lines(logs)],
                         ['BotSesh room room stopped: authorization_revoked. Run hermes botsesh connect.'])
        self.assertEqual(self.client.store.room('room')['health']['state'], 'reconnect_required')
        self.a._stop_noticed.discard('room')
        with self.assertLogs(mod.logger, logging.ERROR) as logs:
            self.a._stop_notice('room', 'membership_revoked')
        self.assertEqual(len(self.lines(logs)), 1)

    def test_the_code_is_sanitized(self):
        with self.assertLogs(mod.logger, logging.ERROR) as logs:
            self.a._stop_notice('room', 'Bearer abc.def secret\n')
        self.assertIn('stopped: access_ended.', logs.records[0].getMessage())
        self.assertNotIn('secret', logs.output[0])

    async def test_listener_and_room_check_seeing_the_same_denial_say_it_once(self):
        def enrolled(room, op, **kw):
            raise BotSeshError('authorization_revoked', 401)
        self.client.enrolled = Mock(side_effect=enrolled)
        with self.assertLogs(mod.logger, logging.ERROR) as logs:
            await self.a._stream('room', asyncio.Event())          # the wait sees it first and returns
            await self.a._stream('room', asyncio.Event())          # restarted by the room loop: same ended access
            with patch.object(self.a, '_check_room', side_effect=BotSeshError('authorization_revoked', 401)):
                await asyncio.wait_for(self.a._room_loop('room'), 3)  # ... and then the room check
        self.assertEqual(len(self.lines(logs)), 1)

    async def test_supervisor_reports_an_access_the_client_already_marked_inactive_but_not_a_local_leave(self):
        class Capture(logging.Handler):
            def __init__(self):
                super().__init__(logging.ERROR)
                self.messages = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        async def forever():
            await asyncio.sleep(60)
        for state, expected in (('authorization_revoked', 1), ('membership_revoked', 1), ('detached', 0), ('revoked', 0)):
            with self.subTest(state=state):
                self.a._stop_noticed = set()
                self.client.store.update(lambda d: d['rooms']['room'].update(active=False, state=state))
                self.a._rooms['room'] = asyncio.create_task(forever())
                capture = Capture()
                mod.logger.addHandler(capture)
                try:
                    with patch('hermes_cli.config.load_config_readonly', return_value={}):
                        supervisor = asyncio.create_task(self.a._sync_rooms_loop())
                        await asyncio.sleep(0.3)
                        supervisor.cancel()
                        await asyncio.gather(supervisor, return_exceptions=True)
                finally:
                    mod.logger.removeHandler(capture)
                self.assertEqual(len([m for m in capture.messages if ' stopped: ' in m]), expected)
                self.assertTrue(self.a._rooms['room'].cancelled())


if __name__ == '__main__':
    unittest.main()
