"""0.10.4 r4: ONE table-driven test of every turn-attribution boundary against every state of every identity it can carry.

Four rounds of review each found one more path that attributed a turn on weaker identity than the caller held (a missing callback event
id read as "the room's current row", a partial gateway scope read as "nothing named").  This file ends the find-by-find cycle: for each
lifecycle boundary (start, progress, typing, status, completion, failure, read proof and the pairing that precedes them) it calls the REAL
production callback or hook with every combination of

    absent (None / variable never set), empty (''), matching (the row's own id), different (another id)

for each identity that boundary can carry (chat, input, event, hook session, hook turn, gateway platform), on a receipt-framed row and on
a native-bound compact row, and asserts that the boundary acts exactly when every REQUIRED identity matches and every PRESENT one agrees
(and never otherwise).  A new boundary or identity has to be added to the table; a boundary that attributes on less fails here.
"""
import copy
import itertools
import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from test_lean_dm import LeanDmTests, turn_env
from hermes_botsesh_test import live as live_module, tool_policy
from hermes_botsesh_test.adapter import _dm_catchup_context, ended_turn, processed_turn, started_turn
from gateway import session_context
from gateway.platforms.event import ProcessingOutcome

# 'match' first: the all-matching combination runs first, and again after the table as a control against leaked state
STATES = ('match', 'absent', 'empty', 'differ')
SESSION, TURN = 's1', 't1'


@contextmanager
def gateway_env(platform, chat, message):
    """The gateway's session variables of a running turn: each None = never set in this context, '' = set to empty."""
    tokens = session_context.set_session_vars(platform=platform or '', chat_id=chat or '', message_id=message or '')
    unset = getattr(session_context, '_UNSET', None)
    if unset is not None:
        for var, value in ((getattr(session_context, '_SESSION_PLATFORM', None), platform),
                           (getattr(session_context, '_SESSION_CHAT_ID', None), chat),
                           (getattr(session_context, '_SESSION_MESSAGE_ID', None), message)):
            if var is not None and value is None:
                var.set(unset)
    try:
        yield
    finally:
        session_context.clear_session_vars(tokens)


def acts(kind, required, state):
    """Does one identity in `state` allow the boundary to act?  kind: 'arg' (an explicit argument of the callback or hook),
    'ambient' (a gateway session variable: never set and empty are the same, 'not present') or 'fresh' (a hook id that pairs a turn for
    the first time: any nonempty id is fine, there is nothing yet to match)."""
    if kind == 'fresh':
        return state in ('match', 'differ')
    if state == 'match':
        return True
    if state == 'differ':
        return False
    if required:
        return False  # a required identity that is missing or empty binds nothing
    return state == 'absent' if kind == 'arg' else True  # an optional explicit blank is refused; an absent gateway variable names nothing


class Boundary:
    """One row of the table.  `fields`: name -> (kind, required for 'compact', required for 'framed').  `fire(t, case, values)` calls the
    production boundary with the identity values; `effect(t, case)` reads whether the row was attributed (a bool)."""

    def __init__(self, name, kinds, stage, fields, fire, effect):
        self.name, self.kinds, self.stage, self.fields, self.fire, self.effect = name, kinds, stage, fields, fire, effect


def hook_fields(required_ambient=(False, False), fresh=False):
    ids = 'fresh' if fresh else 'arg'
    return {'session': (ids, True, True), 'turn': (ids, True, True),
            'chat': ('ambient', required_ambient[0], required_ambient[1]), 'input': ('ambient', required_ambient[0], required_ambient[1]),
            'platform': ('ambient', required_ambient[0], required_ambient[1])}


def tool_fields():
    # the veto polices only BotSesh turns (another platform's tool call is not its business), so the platform is not a variable here
    return {key: spec for key, spec in hook_fields((True, True)).items() if key != 'platform'}


EVENT_FIELDS = {'chat': ('arg', True, True), 'event': ('arg', True, True)}
CHAT_FIELDS = {'chat': ('arg', True, True)}


def event_of(chat, event, case):
    return SimpleNamespace(source=SimpleNamespace(chat_id=chat, message_id=case.target), message_id=event, metadata={}, text=case.text)


async def fire_hook(case, values, call):
    with gateway_env(values.get('platform', 'botsesh'), values['chat'], values['input']):
        return call(values['session'], values['turn'])


# ── the table ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
async def fire_pair(t, case, v):
    await fire_hook(case, v, lambda s, n: processed_turn(user_message=case.text, platform='botsesh', session_id=s, turn_id=n))


async def fire_start_hook(t, case, v):
    await fire_hook(case, v, lambda s, n: started_turn(user_message='', platform='botsesh', session_id=s, turn_id=n, is_first_turn=False))


async def fire_progress(t, case, v):
    relay = t.adapter.live
    for name in ('stream_start', 'stream_delta', 'tool_started', 'tool_finished'):
        setattr(relay, name, lambda *args, _name=name: t.progress.append(_name))

    def call(session, turn):
        live_module.stream_start_hook(session_id=session, turn_id=turn)
        live_module.stream_delta_hook(delta='x', kind='text', session_id=session, turn_id=turn)
        live_module.tool_started_hook(tool_name='t', args={}, session_id=session, turn_id=turn, tool_call_id='c')
        live_module.tool_finished_hook(tool_name='t', session_id=session, turn_id=turn, tool_call_id='c', status='ok')
    await fire_hook(case, v, call)


async def fire_end(t, case, v):
    await fire_hook(case, v, lambda s, n: ended_turn(platform='botsesh', session_id=s, turn_id=n, completed=True, failed=False, interrupted=False))


async def fire_proof(t, case, v):
    await fire_hook(case, v, lambda s, n: _dm_catchup_context(s, n, False, case.text))


async def fire_tool(t, case, v):
    t.tool_verdict = await fire_hook(case, v, lambda s, n: tool_policy._enforce_tools(tool_name='botsesh_read', session_id=s, turn_id=n))


async def fire_event_start(t, case, v):
    await t.adapter.on_processing_start(event_of(v['chat'], v['event'], case))


async def fire_event_complete(t, case, v):
    await t.adapter.on_processing_complete(event_of(v['chat'], v['event'], case), ProcessingOutcome.FAILURE)


async def fire_event_final(t, case, v):
    await t.adapter.send_final_ledgered(event_of(v['chat'], v['event'], case), 'key', 'HTTP 402: credits exhausted', {}, reply_to=None)


async def fire_notify_error(t, case, v):
    await t.adapter._notify_turn_error(event_of(v['chat'], v['event'], case), RuntimeError('escaped'))


async def fire_typing(t, case, v):
    await t.adapter.send_typing(v['chat'])


async def fire_status(t, case, v):
    await t.adapter.send_or_update_status(v['chat'], 'busy', 'still working on it')


async def fire_send_failure(t, case, v):
    await t.adapter.send(v['chat'], 'Model failed.', metadata={'notify': True})


def started(t, case):
    return any(n == 'hermes_delivery' and a.get('number') == case.target and a.get('state') == 'started' for _, n, a in t.requests)


def failed(t, case):
    return any(n == 'hermes_delivery' and a.get('number') == case.target and a.get('state') == 'failed' for _, n, a in t.requests)


def progressed(t, case):
    """Draft text and tool frames reach the relay together or not at all: one hook attributing the turn while another refuses is a defect."""
    if t.progress and sorted(t.progress) != ['stream_delta', 'stream_start', 'tool_finished', 'tool_started']:
        raise AssertionError('only some of the live hooks attributed the turn: %r' % (t.progress,))
    return bool(t.progress)


BOUNDARIES = [
    # the pairing that precedes everything: a compact row pairs only by the gateway's own room AND input (and its platform)
    Boundary('read proof: the hook pairs its turn to a row', ('compact', 'framed'), 'admitted', hook_fields((True, False), fresh=True), fire_pair,
             lambda t, c: c.row.get('hook_turn') is not None),
    Boundary('start: the pre-model hook reports the start', ('compact', 'framed'), 'paired', hook_fields(), fire_start_hook,
             lambda t, c: started(t, c) and bool(c.row.get('start_reported'))),
    Boundary('progress: draft text and tool frames', ('compact', 'framed'), 'paired', hook_fields(), fire_progress, progressed),
    Boundary('completion: the end hook settles the turn', ('compact', 'framed'), 'paired', hook_fields(), fire_end,
             lambda t, c: bool(c.row.get('hook_terminal'))),
    Boundary('read proof: the catch-up hook proves a lean turn', ('lean',), 'paired', hook_fields((True, True)), fire_proof,
             lambda t, c: bool(t.adapter._delivery_proves_read(c.row))),
    Boundary('tool authority: the pre-tool execution veto', ('compact', 'framed'), 'paired', tool_fields(), fire_tool,
             lambda t, c: t.tool_verdict is None),
    Boundary('start: the gateway start callback', ('compact', 'framed'), 'paired', EVENT_FIELDS, fire_event_start,
             lambda t, c: started(t, c)),
    Boundary('completion: the gateway completion callback settles a failure', ('compact', 'framed'), 'paired', EVENT_FIELDS, fire_event_complete,
             lambda t, c: failed(t, c)),
    Boundary('failure: the event-bound final labels its own failure', ('compact', 'framed'), 'paired', EVENT_FIELDS, fire_event_final,
             lambda t, c: c.row.get('final_failure_code') == 'provider_budget'),
    Boundary('failure: the late error notice queues a typed failure', ('compact', 'framed'), 'paired', EVENT_FIELDS, fire_notify_error,
             lambda t, c: failed(t, c)),
    Boundary('typing: the gateway typing callback', ('compact', 'framed'), 'started', CHAT_FIELDS, fire_typing, lambda t, c: started(t, c)),
    Boundary('status: the gateway status callback', ('compact', 'framed'), 'started', CHAT_FIELDS, fire_status, lambda t, c: started(t, c)),
    Boundary('failure: a chat-level failure notice', ('compact', 'framed'), 'started', CHAT_FIELDS, fire_send_failure, lambda t, c: failed(t, c)),
]


class TurnBindingMatrix(LeanDmTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.progress = []
        fsync = patch('hermes_botsesh_test.storage.os.fsync', lambda fd: None)  # durability is not under test; 12 ms per registry write is
        fsync.start()
        self.addCleanup(fsync.stop)

    async def stage(self, kind, stage):
        """A fresh world with one in-flight row of `kind`, brought to `stage`: 'admitted' (delivered), 'paired' (its hooks bound it) or
        'started' (and its start reported)."""
        if getattr(self, '_worlds', 0):
            await super().asyncSetUp()  # a new store, adapter and fake server; the old ones are discarded
        self._worlds = getattr(self, '_worlds', 0) + 1
        self.real_wire(confirm_starts=True)
        if kind == 'framed':
            event = await self.foreign_full_frame_event('group room')
            room, target, other_room = 'b', 3, 'a'
        else:
            event = await self.lean_five()
            room, target, other_room = 'a', 5, 'b'
            self.assertEqual(bool(self.adapter._current['a']['lean_session']), True)
        row = self.adapter._current[room]
        case = SimpleNamespace(kind=kind, room=room, target=target, other_room=other_room, text=event.text, row=row)
        if stage in ('paired', 'started'):
            with turn_env(room, target):
                processed_turn(user_message=event.text, platform='botsesh', session_id=SESSION, turn_id=TURN)
            self.assertEqual(row['hook_turn'], (SESSION, TURN), 'the fixture row is paired')
        if stage == 'started':
            with turn_env(room, target):
                started_turn(user_message=event.text, platform='botsesh', session_id=SESSION, turn_id=TURN, is_first_turn=False)
            self.assertTrue(row.get('start_reported'), 'the fixture row is started')
        case.snapshot = SimpleNamespace(rows=copy.deepcopy(self.adapter._current), data=copy.deepcopy(self.client.store.read()))
        return case

    def restore(self, case):
        adapter = self.adapter
        adapter._current.clear()
        adapter._current.update(copy.deepcopy(case.snapshot.rows))
        case.row = adapter._current[case.room]
        if self.client.store.read() != case.snapshot.data:
            self.client.store.update(lambda d: (d.clear(), d.update(copy.deepcopy(case.snapshot.data))))
        adapter._typing_at.clear()
        adapter.__dict__.setdefault('_working_at', {}).clear()
        del self.requests[:], self.calls[:], self.progress[:]

    @staticmethod
    def values(case, names, states):
        real = {'session': SESSION, 'turn': TURN, 'chat': case.room, 'input': str(case.target), 'event': str(case.target),
                'platform': 'botsesh'}
        other = {'session': 'other-session', 'turn': 'other-turn', 'chat': case.other_room, 'input': '99', 'event': '99',
                 'platform': 'telegram'}
        out = {}
        for name, state in zip(names, states):
            out[name] = {'match': real[name], 'absent': None, 'empty': '', 'differ': other[name]}[state]
        return out

    async def test_every_boundary_attributes_a_turn_only_when_every_required_identity_matches_and_every_present_one_agrees(self):
        ran = bound = 0
        only = os.environ.get('TURN_MATRIX_ONLY')  # debugging aid: a substring of the boundary name
        for boundary in BOUNDARIES:
            if only and only not in boundary.name:
                continue
            names = list(boundary.fields)
            for kind in boundary.kinds:
                case = await self.stage(kind, boundary.stage)
                outcomes = {}
                combos = list(itertools.product(STATES, repeat=len(names)))
                for states in combos + [combos[0]]:  # the all-matching combination again at the end: no state leaked between combinations
                    self.restore(case)
                    values = self.values(case, names, states)
                    expected = all(acts(boundary.fields[n][0], boundary.fields[n][1 if kind != 'framed' else 2], s) for n, s in zip(names, states))
                    label = '%s | %s | %s' % (boundary.name, kind, dict(zip(names, states)))
                    with self.subTest(label):
                        try:
                            await boundary.fire(self, case, values)
                            actual = bool(boundary.effect(self, case))
                        except Exception as error:  # a crash on a missing or foreign id is itself a defect
                            self.fail('raised %r' % (error,))
                        outcomes[states] = actual
                        self.assertEqual(actual, expected, 'attributed' if actual else 'not attributed')
                    ran += 1
                    bound += outcomes.get(states, False)
                self.assertTrue(outcomes.get(combos[0]), '%s | %s: the all-matching claim must act (the table is not vacuous)' % (boundary.name, kind))
        if not only:
            self.assertGreater(ran, 2000, 'the table runs every combination of every boundary')
            self.assertGreater(bound, 0)


def load_tests(loader, tests, pattern):
    """Only this file's case (the LeanDmTests fixture's own cases are covered by test_lean_dm)."""
    suite = unittest.TestSuite()
    for name in loader.getTestCaseNames(TurnBindingMatrix):
        if name in TurnBindingMatrix.__dict__:
            suite.addTest(TurnBindingMatrix(name))
    return suite


if __name__ == '__main__':
    unittest.main()
