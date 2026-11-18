"""0.10.3 lean owner-DM deliveries: transport doubles, zero inference."""
import copy
import json
import re
import time
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

from test_solid import SolidTests
from hermes_botsesh_test import adapter as adapter_module, lean_dm
from hermes_botsesh_test.adapter import _ADAPTERS, started_turn, processed_turn, ended_turn
from gateway.session_context import set_session_vars, clear_session_vars
from gateway.platforms.event import ProcessingOutcome

OWNER = 'owner'
MANIFEST = {'id': 1, 'number': 0, 'text': 'Hermes commands available (85). Type / to choose a command.', 'isOwn': True,
            'senderParticipantId': 'agent', 'authorParticipant': {'id': 'agent', 'name': 'Hermes', 'verified': True},
            'dmControl': {'version': 1, 'kind': 'manifest', 'commands': [{'name': 'cmd%d' % i, 'description': 'x' * 200} for i in range(85)]}}


def human(number, text, **extra):
    return {'id': number + 1, 'number': number, 'text': text, 'isOwn': False, 'senderParticipantId': OWNER,
            'authorParticipant': {'id': OWNER, 'verified': True, 'name': 'Owner'}, 'addressing': {'x': 'y' * 300}, **extra}


def agent(number, text):
    return {'id': number + 1, 'number': number, 'text': text, 'isOwn': True, 'senderParticipantId': 'agent',
            'authorParticipant': {'id': 'agent', 'name': 'Hermes', 'verified': True}, 'addressing': {'x': 'y' * 300}}


class Entry:
    def __init__(self, session_id, suspended=False):
        self.session_id, self.suspended = session_id, suspended


class FakeSessionStore:
    def __init__(self, session_id):
        self.entry = Entry(session_id) if session_id else None

    def lookup_by_session_key(self, key):
        return self.entry

    def _route_reset_reason(self, entry):
        return 'suspended' if entry.suspended else None


@contextmanager
def turn_env(room, target):
    """The session variables the Hermes gateway binds to a running native turn."""
    tokens = set_session_vars(platform='botsesh', chat_id=room, message_id=str(target))
    try:
        yield
    finally:
        clear_session_vars(tokens)


class LeanDmTests(SolidTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        _ADAPTERS.clear(); _ADAPTERS.add(self.adapter)
        self.client.store.update(lambda d: [d['rooms'][r].update(room_type='dm', participant_id='agent', oauth_id='grant', oauth_root='source', origin='https://botsesh.com') for r in ('a', 'b')])
        self.live = {'room_id': 'a', 'room_type': 'dm', 'participant_id': 'agent', 'membership_id': 'member', 'human_participant_id': OWNER, 'human_owns_grant': True, 'access_state': 'authorized'}
        self.thread = [MANIFEST, human(1, 'hi'), agent(2, 'yo'), human(3, 'Yoo wsp big dawg')]
        self.threads = {'a': self.thread, 'b': [MANIFEST, human(1, 'b-secret-hi'), agent(2, 'b-secret-yo'), human(3, 'Yoo wsp big dawg')]}
        self.context = {}  # room -> the Brief (`context`) of the frozen read
        self.read_through = {}  # room -> read further than the target (a message that arrived after the inbox check)
        self.policy_override = None  # callable(room, args) -> the server's tool policy for a service turn
        self.targets['a'] = [3]
        original = self.client.enrolled.side_effect

        def enrolled(room, op, **args):
            if op == 'dm_context':
                self.calls.append((room, op, args)); return {**self.live, 'room_id': room}
            if op == 'read':
                self.calls.append((room, op, args))
                n = args['target_number']
                thread = self.thread if room == 'a' else self.threads[room]
                reach = max(n, self.read_through.get(room, n))
                messages = [copy.deepcopy(m) for m in thread if m['number'] <= reach or m['number'] in self.targets[room]]
                rid = 'receipt-%d' % n if room == 'a' else 'receipt-%s-%d' % (room, n)
                # what the real read saves for the receipt it hands out
                self.client.store.update(lambda d: d['rooms'][room].setdefault('oauth_receipts', {}).update(
                    {rid: {'expires': time.time() + 3600, 'numbers': [m['number'] for m in messages], 'participant_id': d['rooms'][room]['participant_id']}}))
                part = {'room_id': room, 'batch_id': rid, 'instructions': 'You are a member of this group chat. ' * 20, 'messages': copy.deepcopy(messages)}
                if room in self.context:
                    part['context'] = copy.deepcopy(self.context[room])
                return {'receipt_id': rid, 'read_effect': 'none', 'briefing': [part],
                        'batch': {'nextCheckpoint': reach, 'hasMore': False, 'messages': messages}}
            if op == 'tool_policy' and self.policy_override:
                self.calls.append((room, op, args)); return self.policy_override(room, args)
            return original(room, op, **args)
        self.client.enrolled.side_effect = enrolled
        self.adapter._publish_dm_manifest = AsyncMock()
        self.store = FakeSessionStore('s1')
        self.adapter._session_store = self.store

    async def deliver(self, target, session='s1', complete=True, session_id=None, room='a'):
        """Build the delivery for `target`; optionally complete the turn in Hermes session `session`."""
        self.targets[room] = [target]
        await self.adapter._check_room(room)
        event = self.adapter._admit_event.call_args.args[0]
        if complete:
            current = self.adapter._current[room]
            current.update(hermes_session_id=session, model_processed=True, delivered=True)
            await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
            self.assertNotIn(room, self.adapter._current)
        return event

    async def hermes_turn(self, event, session, turn, room='a', first=False, complete=True):
        """What Hermes does around one native turn: the pre_llm_call hook, the post_llm_call hook, the end of the turn, then the
        adapter's completion. Returns what the pre_llm_call hook returned (the ephemeral context)."""
        target = self.adapter._current[room]['target']
        with turn_env(room, target):
            context = started_turn(user_message=event.text, platform='botsesh', session_id=session, turn_id=turn, is_first_turn=first)
            processed_turn(user_message=event.text, platform='botsesh', session_id=session, turn_id=turn)
            ended_turn(platform='botsesh', session_id=session, turn_id=turn, completed=complete, failed=not complete, interrupted=False)
        if complete:
            self.adapter._current[room]['delivered'] = True
            await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
            self.assertNotIn(room, self.adapter._current)
        return context

    async def test_first_delivery_is_full_without_the_manifest_then_next_is_lean(self):
        first = await self.deliver(3, complete=False)
        self.assertIn('Complete BotSesh collaboration data follows', first.text)
        self.assertIn('CURRENT BOTSESH INPUTS', first.text)
        self.assertNotIn('cmd84', first.text, 'the slash-command manifest card is UI plumbing, never delivered to the model')
        self.assertIn('"dm_control_cards_omitted": 1', first.text)
        # the frozen receipt still carries every message (exact read attestation scope)
        frozen = self.client.store.room('a')['processing_receipts']['3']['frozen']
        self.assertEqual([m['number'] for m in frozen['batch']['messages']], [0, 1, 2, 3])
        current = self.adapter._current['a']
        current.update(hermes_session_id='s1', model_processed=True, delivered=True)
        await self.adapter.on_processing_complete(first, ProcessingOutcome.SUCCESS)
        self.thread += [agent(4, 'yo, not much'), human(5, 'Yoo wsp big dawg')]
        second = await self.deliver(5, complete=False)
        self.assertNotIn('Complete BotSesh collaboration data', second.text)
        self.assertNotIn('group chat', second.text)
        self.assertNotIn('cmd84', second.text)
        self.assertLess(len(second.text), 700, second.text)
        self.assertTrue(re.search(r'"addressed_message_number"\s*:\s*5\s*[,}]', second.text), 'the Hermes turn hooks bind on this')
        self.assertTrue(second.text.rstrip().endswith('#5 Yoo wsp big dawg'), 'the new input is last')
        self.assertIn('Reply in plain text', second.text)
        self.assertIn('server-verified owner "Owner"', second.text)
        self.assertNotIn('#1 ', second.text)
        self.assertNotIn('BOTSESH DM FILES', second.text)
        self.assertNotIn('BOTSESH RUNTIME TOOL POLICY', second.text, 'owner with host tools allowed needs no per-turn policy block')
        self.assertEqual(self.client.store.room('a')['dm_session'], {'session_id': 's1', 'through': 3, 'brief': lean_dm.brief_digest(frozen)})
        # the receipt is the same exact frozen read as before: nothing about attestation changed
        row = self.client.store.room('a')['processing_receipts']['5']
        self.assertEqual(row['frozen']['receipt_id'], 'receipt-5')
        self.assertEqual(row['dm_through'], 5)
        self.assertEqual(row['lean_session'], 's1')

    async def test_a_new_hermes_session_gets_the_catch_up_again(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo'), human(5, 'again')]
        self.store.entry = Entry('s2')  # /new, /resume or a reset
        event = await self.deliver(5, complete=False)
        self.assertIn('Complete BotSesh collaboration data follows', event.text)
        self.assertIsNone(self.adapter._current['a']['lean_session'])

    async def test_suspended_session_and_unknown_session_store_fall_back_to_full(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo'), human(5, 'again')]
        self.store.entry.suspended = True
        self.assertIn('Complete BotSesh collaboration data follows', (await self.deliver(5, complete=False)).text)
        self.adapter._current.pop('a', None)
        self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={}, handled=[]))
        self.store.entry.suspended = False
        self.adapter._session_store = None
        self.assertIn('Complete BotSesh collaboration data follows', (await self.deliver(5, complete=False)).text)

    async def test_owner_messages_the_session_never_saw_are_listed_compactly(self):
        await self.deliver(3)
        # #5 was withdrawn/ignored and never delivered; #6 is the new input
        self.thread += [agent(4, 'yo'), human(5, 'remember the milk'), human(6, 'and eggs?')]
        self.client.store.update(lambda d: d['rooms']['a'].setdefault('handled', []).append(5))
        event = await self.deliver(6, complete=False)
        self.assertNotIn('Complete BotSesh collaboration data', event.text)
        self.assertIn('not seen', event.text)
        self.assertIn('#5 remember the milk', event.text)
        self.assertTrue(event.text.rstrip().endswith('#6 and eggs?'))
        self.assertLess(event.text.index('#5 remember'), event.text.index('#6 and eggs?'))

    async def test_a_gap_the_read_window_cannot_cover_or_a_foreign_speaker_keeps_the_full_framing(self):
        await self.deliver(3)
        self.thread = [m for m in self.thread if m['number'] >= 9] + [agent(10, 'x'), human(11, 'later')]
        self.assertIn('Complete BotSesh collaboration data', (await self.deliver(11, complete=False)).text)
        self.adapter._current.pop('a', None)
        self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={}, handled=[]))
        self.thread = [MANIFEST, human(1, 'hi'), agent(2, 'yo'), human(3, 'q'), agent(4, 'a'),
                       dict(human(5, 'someone else'), senderParticipantId='intruder', authorParticipant={'id': 'intruder', 'name': 'X', 'verified': True}), human(6, 'now')]
        self.assertIn('Complete BotSesh collaboration data', (await self.deliver(6, complete=False)).text)

    async def test_failed_or_interrupted_turn_does_not_advance_the_session_ledger(self):
        first = await self.deliver(3, complete=False)
        current = self.adapter._current['a']
        current.update(hermes_session_id='s1', model_processed=False)
        await self.adapter._finish_current('a', current, 'interrupted')
        self.assertNotIn('dm_session', self.client.store.room('a'))

    async def test_service_checkin_group_and_non_owner_turns_keep_the_full_framing(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo'), human(5, 'again')]
        self.live['human_owns_grant'] = False
        self.assertIn('Complete BotSesh collaboration data', (await self.deliver(5, complete=False)).text)
        self.adapter._current.pop('a', None)
        self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={}, handled=[]))
        self.live['human_owns_grant'] = True
        self.client.store.update(lambda d: d['rooms']['a'].update(room_type='room'))
        group = await self.deliver(5, complete=False)
        self.assertIn('Complete BotSesh collaboration data', group.text)
        self.assertIn('group chat' if 'group chat' in group.text else 'Complete BotSesh', group.text)
        self.assertIn('cmd84', group.text, 'a group room keeps its history untouched: only DM control cards are dropped')

    async def test_tools_off_owner_turn_keeps_the_runtime_policy_block(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo'), human(5, 'again')]
        original = self.client.enrolled.side_effect

        def enrolled(room, op, **args):
            value = original(room, op, **args)
            if op == 'tool_policy':
                value = {**value, 'toolsAllowed': False}
            return value
        self.client.enrolled.side_effect = enrolled
        event = await self.deliver(5, complete=False)
        self.assertNotIn('Complete BotSesh collaboration data', event.text)
        self.assertIn('BOTSESH RUNTIME TOOL POLICY', event.text)
        self.assertIn('Owner-triggered turn with tools off', event.text)

    async def lean_five(self, room='a'):
        """A DM whose session s1 holds #1..#3; the lean delivery for #5 is built (not yet run). Returns its event."""
        await self.deliver(3, room=room)
        thread = self.thread if room == 'a' else self.threads[room]
        thread += [agent(4, '%s-private-reply' % room), human(5, 'Yoo wsp big dawg')]
        event = await self.deliver(5, room=room, complete=False)
        self.assertEqual(self.adapter._current[room]['lean_session'], 's1')
        self.assertNotIn('Complete BotSesh collaboration data', event.text)
        return event

    def ledger(self, room='a'):
        return self.client.store.room(room).get('dm_session')

    async def test_hook_safety_net_attaches_a_compact_catch_up_when_hermes_started_a_fresh_session(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo, not much'), human(5, 'Yoo wsp big dawg')]
        event = await self.deliver(5, complete=False)
        current = self.adapter._current['a']
        self.assertEqual(current['lean_session'], 's1')
        # Hermes reports the same session with history: nothing attached, nothing to recover
        with turn_env('a', 5):
            self.assertIsNone(started_turn(user_message=event.text, platform='botsesh', session_id='s1', turn_id='t1', is_first_turn=False))
        self.assertNotIn('lean_needs_recovery', current)
        # Hermes started a different session (reset between the check and the turn): the history rides pre_llm_call
        with turn_env('a', 5):
            context = started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t2', is_first_turn=True)
        self.assertIsInstance(context, dict)
        text = context['context']
        self.assertIn('BOTSESH DM CATCH-UP', text)
        self.assertIn('#1 "Owner": hi', text)
        self.assertIn('#4 YOU (your earlier reply): yo, not much', text)
        self.assertNotIn('cmd84', text)
        self.assertLess(len(text), 9500, 'Hermes spills a larger hook context to a file preview')
        self.assertNotIn('#5 ', text, 'the current input is in the message itself')
        self.assertIs(current['lean_recovered'], True)
        # once per delivery
        with turn_env('a', 5):
            self.assertIsNone(started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t2', is_first_turn=True))

    async def test_a_full_delivery_never_gets_the_safety_net(self):
        event = await self.deliver(3, complete=False)
        with turn_env('a', 3):
            self.assertIsNone(started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t1', is_first_turn=True))

    # --- review t_7bf3f692, finding 1: recovery is bound to the trusted native event -------------------------------------

    async def test_two_rooms_with_identical_lean_texts_never_cross_their_private_history(self):
        await self.deliver(3, room='a'); await self.deliver(3, room='b')
        self.thread += [agent(4, 'a-private-reply'), human(5, 'Yoo wsp big dawg')]
        self.threads['b'] += [agent(4, 'b-private-reply'), human(5, 'Yoo wsp big dawg')]
        event_a = await self.deliver(5, room='a', complete=False)
        event_b = await self.deliver(5, room='b', complete=False)
        self.assertEqual(event_a.text, event_b.text, 'real lean headers repeat: no room id, the same r1 label, the same words')
        current_a, current_b = self.adapter._current['a'], self.adapter._current['b']
        self.assertEqual((current_a['lean_session'], current_b['lean_session']), ('s1', 's1'))
        # Hermes runs room b's turn in a fresh session: b's own history, never a's
        with turn_env('b', 5):
            context = started_turn(user_message=event_b.text, platform='botsesh', session_id='s9', turn_id='tb', is_first_turn=True)
            processed_turn(user_message=event_b.text, platform='botsesh', session_id='s9', turn_id='tb')
            ended_turn(platform='botsesh', session_id='s9', turn_id='tb', completed=True, failed=False, interrupted=False)
        self.assertIn('b-private-reply', context['context'])
        self.assertIn('#1 "Owner": b-secret-hi', context['context'])
        self.assertNotIn('a-private-reply', context['context'])
        self.assertNotIn('#1 "Owner": hi', context['context'])
        # room a's event was not touched by b's turn: no binding, no recovery state, no premature completion
        for key in ('hook_turn', 'hermes_session_id', 'lean_needs_recovery', 'lean_catchup_sent', 'lean_recovered'):
            self.assertNotIn(key, current_a, key)
        self.assertFalse(current_a['model_processed'], 'one turn ending must not mark the other room processed')
        self.assertEqual(current_b['hook_turn'], ('s9', 'tb'))
        with turn_env('a', 5):
            context = started_turn(user_message=event_a.text, platform='botsesh', session_id='s8', turn_id='ta', is_first_turn=True)
        self.assertIn('a-private-reply', context['context'])
        self.assertNotIn('b-private-reply', context['context'])
        self.assertNotIn('b-secret', context['context'])

    async def test_a_turn_bound_to_a_room_without_a_lean_event_discloses_nothing_even_if_another_room_matches_the_text(self):
        await self.deliver(3, room='a'); await self.deliver(3, room='b')
        self.thread += [agent(4, 'a-private-reply'), human(5, 'Yoo wsp big dawg')]
        self.threads['b'] += [agent(4, 'b-private-reply'), human(5, 'Yoo wsp big dawg')]
        event_a = await self.deliver(5, room='a', complete=False)
        await self.deliver(5, room='b', complete=False)
        self.adapter._current.pop('b')
        with turn_env('b', 5):
            self.assertIsNone(started_turn(user_message=event_a.text, platform='botsesh', session_id='s9', turn_id='t1', is_first_turn=True))
        with turn_env('c-unknown', 5):
            self.assertIsNone(started_turn(user_message=event_a.text, platform='botsesh', session_id='s9', turn_id='t2', is_first_turn=True))
        self.assertNotIn('lean_needs_recovery', self.adapter._current['a'])

    async def test_two_adapters_claiming_the_same_input_is_ambiguous_and_attaches_nothing(self):
        event = await self.lean_five()
        other = self.make_adapter()
        other._current['a'] = copy.copy(self.adapter._current['a'])
        _ADAPTERS.add(other)
        self.addCleanup(_ADAPTERS.discard, other)
        with turn_env('a', 5):
            self.assertIsNone(started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t1', is_first_turn=True))
        self.assertNotIn('lean_catchup_sent', self.adapter._current['a'])

    async def test_recovery_attaches_nothing_unless_event_receipt_and_participant_check_out(self):
        event = await self.lean_five()
        current = self.adapter._current['a']
        pristine = copy.deepcopy(self.client.store.read())

        def edit(fn):
            return lambda d: fn(d['rooms']['a'])
        cases = {
            'receipt of another participant': edit(lambda r: r['oauth_receipts']['receipt-5'].update(participant_id='someone-else')),
            'expired receipt': edit(lambda r: r['oauth_receipts']['receipt-5'].update(expires=time.time() - 1)),
            'no saved receipt': edit(lambda r: r['oauth_receipts'].pop('receipt-5')),
            'row of another receipt': edit(lambda r: r['processing_receipts']['5'].update(receipt_id='receipt-other')),
            'frozen read of another receipt': edit(lambda r: r['processing_receipts']['5']['frozen'].update(receipt_id='receipt-other')),
            'row already closed': edit(lambda r: r['processing_receipts']['5'].update(state='replied')),
            'explicit failure on the row': edit(lambda r: r['processing_receipts']['5'].update(explicit_failure={'code': 'x'})),
            'grant re-bound since admission': edit(lambda r: r.update(oauth_id='another-grant')),
            'participant re-bound since admission': edit(lambda r: r.update(participant_id='another-seat')),
            'room no longer a DM': edit(lambda r: r.update(room_type='room')),
            'room deactivated': edit(lambda r: r.update(active=False)),
            'input not from the pinned owner': edit(lambda r: [m.update(senderParticipantId='intruder') for m in r['processing_receipts']['5']['frozen']['batch']['messages'] if m['number'] == 5]),
            'frozen read that names another room': edit(lambda r: r['processing_receipts']['5']['frozen']['briefing'][0].update(room_id='b')),
        }
        def recover(text=event.text, room='a', message=5, tag=''):
            with turn_env(room, message):
                return adapter_module._dm_catchup_context('s9', 't-' + tag, True, text)
        for name, mutate in cases.items():
            with self.subTest(name):
                try:
                    self.client.store.update(mutate)
                    self.assertIsNone(recover(tag=name))
                    self.assertNotIn('lean_catchup_sent', current)
                    self.assertIsNot(current.get('lean_recovered'), True)
                    self.assertTrue(current.get('lean_needs_recovery'), 'a rejected recovery still keeps this session out of the ledger')
                finally:
                    self.client.store.update(lambda d: (d.clear(), d.update(copy.deepcopy(pristine))))
                    current.pop('lean_needs_recovery', None)
        with self.subTest('no gateway identity'):
            self.assertIsNone(adapter_module._dm_catchup_context('s9', 't-none', True, event.text))
        with self.subTest('another input of the same room'):
            self.assertIsNone(recover(message=3))
        with self.subTest('text that is not this turn'):
            self.assertIsNone(recover(text='another message entirely'))
        with self.subTest('other platform'):
            tokens = set_session_vars(platform='telegram', chat_id='a', message_id='5')
            try:
                self.assertIsNone(adapter_module._dm_catchup_context('s9', 't-tg', True, event.text))
            finally:
                clear_session_vars(tokens)
        with self.subTest('after all of that, the exact event still recovers'):
            context = recover(tag='ok')
            self.assertIn('a-private-reply', context['context'])

    # --- finding 2: no recording, no attestation past what was delivered ----------------------------------------------------

    async def test_a_recovery_that_fails_is_not_recorded_and_the_next_turn_gets_the_full_catch_up(self):
        event = await self.lean_five()
        self.adapter._dm_catchup_text = Mock(side_effect=RuntimeError('hook broke'))
        context = await self.hermes_turn(event, 's9', 't1', first=True)
        self.assertIsNone(context)
        self.assertEqual(self.ledger()['session_id'], 's1', 'the session that never received the history is not recorded as holding it')
        self.assertEqual(self.ledger()['through'], 3)
        # Hermes now routes the DM to s9: the next delivery cannot be lean
        self.store.entry = Entry('s9')
        self.thread += [agent(6, 'a reply'), human(7, 'and now?')]
        full = await self.deliver(7, complete=False)
        self.assertIn('Complete BotSesh collaboration data', full.text)
        self.assertIn('a-private-reply', full.text, 'the history the lean turn could not deliver arrives in full')
        self.assertIsNone(self.adapter._current['a']['lean_session'])
        await self.hermes_turn(full, 's9', 't2', first=True)
        self.assertEqual(self.ledger()['session_id'], 's9')
        self.assertEqual(self.ledger()['through'], 7)
        self.thread += [agent(8, 'ok'), human(9, 'lean again')]
        self.assertNotIn('Complete BotSesh collaboration data', (await self.deliver(9, complete=False)).text)

    async def test_a_clipped_recovery_is_not_recorded(self):
        await self.deliver(3)
        self.thread += [human(n, 'long message %d ' % n + 'x' * 700) if n % 2 else agent(n, 'long reply %d ' % n + 'y' * 700) for n in range(4, 40)]
        self.thread += [human(41, 'one more')]
        await self.deliver(41)  # a full catch-up: the session now holds all of it
        self.assertEqual(self.ledger()['through'], 41)
        self.thread += [agent(42, 'z'), human(43, 'Yoo wsp big dawg')]
        event = await self.deliver(43, complete=False)
        self.assertEqual(self.adapter._current['a']['lean_session'], 's1')
        context = await self.hermes_turn(event, 's9', 't1', first=True)
        self.assertIn('older messages omitted', context['context'], 'what fits is still attached for this turn')
        self.assertLess(len(context['context']), 9500)
        self.assertEqual(self.ledger()['session_id'], 's1', 'but a clipped recovery is not evidence that the session holds the DM')
        self.store.entry = Entry('s9')
        self.thread += [agent(44, 'a'), human(45, 'next')]
        self.assertIn('Complete BotSesh collaboration data', (await self.deliver(45, complete=False)).text)

    async def test_a_message_cut_at_the_per_message_limit_or_carrying_files_is_not_a_complete_recovery(self):
        await self.deliver(3)
        self.thread += [agent(4, 'z' * 2000), human(5, 'Yoo wsp big dawg')]
        event = await self.deliver(5, complete=False)
        context = await self.hermes_turn(event, 's9', 't1', first=True)
        self.assertIn('…', context['context'])
        self.assertEqual(self.ledger()['session_id'], 's1')
        self.assertFalse(lean_dm.catchup([human(1, 'see this', attachments=[{'id': 'f'}])], {9})[1])
        self.assertTrue(lean_dm.catchup([human(1, 'plain'), agent(2, 'reply')], {9})[1])

    async def test_a_complete_recovery_is_recorded_and_the_next_turn_is_lean_in_the_new_session(self):
        event = await self.lean_five()
        context = await self.hermes_turn(event, 's9', 't1', first=True)
        self.assertIn('a-private-reply', context['context'])
        self.assertEqual(self.ledger()['session_id'], 's9')
        self.assertEqual(self.ledger()['through'], 5)
        self.store.entry = Entry('s9')
        self.thread += [agent(6, 'ok'), human(7, 'and then')]
        nxt = await self.deliver(7, session='s9', complete=False)
        self.assertNotIn('Complete BotSesh collaboration data', nxt.text)
        self.assertEqual(self.adapter._current['a']['lean_session'], 's9')

    async def test_a_recovery_that_could_not_be_tied_to_the_event_does_not_record_the_new_session(self):
        event = await self.lean_five()
        # no gateway variables for this turn: nothing is attached, and the unknown session must not become the ledger
        with_env = started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t1', is_first_turn=True)
        self.assertIsNone(with_env)
        with turn_env('a', 5):
            processed_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t1')
            ended_turn(platform='botsesh', session_id='s9', turn_id='t1', completed=True, failed=False, interrupted=False)
        self.adapter._current['a']['delivered'] = True
        await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.ledger()['session_id'], 's1')

    async def test_a_fresh_empty_history_in_the_same_session_id_still_needs_the_recovery_to_be_complete(self):
        event = await self.lean_five()
        self.adapter._dm_catchup_text = Mock(side_effect=RuntimeError('hook broke'))
        # Hermes says first turn although the session id is the one the lean text was built for (history was cleared)
        await self.hermes_turn(event, 's1', 't1', first=True)
        self.assertEqual(self.ledger()['through'], 3, 'the failed recovery in an emptied session does not advance the ledger')

    async def test_a_changed_brief_sends_the_full_catch_up_and_a_lean_turn_never_records_one_it_did_not_carry(self):
        self.context['a'] = {'enabled': True, 'revision': 1, 'markdown': 'House rules: answer in French.', 'decisions': []}
        await self.deliver(3)
        self.thread += [agent(4, 'oui'), human(5, 'again')]
        lean = await self.deliver(5, complete=False)
        self.assertNotIn('House rules', lean.text, 'unchanged Brief: the session already got it with the first full delivery')
        context = await self.hermes_turn(lean, 's9', 't1', first=True)
        self.assertEqual(self.ledger()['session_id'], 's1', 'the recovery text cannot carry a Brief, so it is not complete')
        self.assertNotIn('House rules', context['context'])
        self.store.entry = Entry('s1')
        self.context['a'] = {'enabled': True, 'revision': 2, 'markdown': 'House rules: answer in German.', 'decisions': []}
        self.thread += [agent(6, 'ja'), human(7, 'and now')]
        changed = await self.deliver(7, complete=False)
        self.assertIn('Complete BotSesh collaboration data', changed.text)
        self.assertIn('House rules: answer in German.', changed.text)

    async def test_a_lean_reply_attests_only_what_the_session_holds_or_was_just_given(self):
        event = await self.lean_five()
        current = self.adapter._current['a']
        row = self.client.store.room('a')['processing_receipts']['5']
        attested = set(self.client.store.room('a')['oauth_receipts'][row['receipt_id']]['numbers'])
        frozen_numbers = {m['number'] for m in row['frozen']['batch']['messages']}
        self.assertEqual(attested, frozen_numbers, 'the immutable receipt still covers the whole frozen read')
        held = {n for n in attested if n <= self.ledger()['through']}
        given_now = set(current['batch_targets'])
        own = {m['number'] for m in row['frozen']['batch']['messages'] if m.get('isOwn')}
        controls = {m['number'] for m in row['frozen']['batch']['messages'] if lean_dm.is_control(m)}
        self.assertEqual(attested - held - given_now - own - controls, set(), 'nothing is attested that no turn of this session was given')
        # the reply goes out exactly as before: the original receipt, processed:true, for this input
        current.update(model_processed=True, hermes_session_id='s1')
        sent = await self.adapter.send('a', 'a plain reply', metadata={'notify': True})
        self.assertTrue(sent.success, sent)
        (payload,) = self.posts.values()
        self.assertEqual({k: payload[k] for k in ('text', 'receipt_id', 'processed', 'reply_to')},
                         {'text': 'a plain reply', 'receipt_id': row['receipt_id'], 'processed': True, 'reply_to': 5})
        self.assertEqual(self.client.store.room('a')['processing_receipts']['5']['frozen'], row['frozen'])

    async def test_a_message_in_the_frozen_read_that_reaches_the_model_nowhere_sends_the_full_catch_up(self):
        await self.deliver(3)
        # #6 arrives after the inbox check but before the read: in the frozen read, not an input of this turn
        self.thread += [agent(4, 'yo'), human(5, 'Yoo wsp big dawg'), human(6, 'sent while you were thinking')]
        self.read_through['a'] = 6
        event = await self.deliver(5, complete=False)
        self.assertIn('Complete BotSesh collaboration data', event.text)
        self.assertIn('sent while you were thinking', event.text, 'the reply will attest #6, so the model gets it')
        self.assertIsNone(self.adapter._current['a']['lean_session'])

    # --- finding 3: only the owner DM projection changed ------------------------------------------------------------------------

    def assert_original_full_packet(self, event, frozen_row, extra_head=(), owner_context=''):
        """The delivered text is, byte for byte, what the original (v0.10.2) renderer builds from the frozen read: the packet
        JSON over every message (control cards included), the DM media hint, the exact inputs, then the unchanged tool policy."""
        from hermes_botsesh_test import agreement, tool_policy
        head = json.loads(event.text.split('\n\n', 1)[0])
        frozen = frozen_row['frozen']
        self.assertEqual(set(head), {'notice', 'room_id', 'your_earlier_message_numbers', 'addressed_message_number', 'pending_message_numbers',
                                     'receipt_id', 'read_effect', 'briefing', 'batch', *extra_head}, 'no dm_control_cards_omitted, nothing added or dropped')
        self.assertEqual(head['your_earlier_message_numbers'], [m['number'] for m in frozen['batch']['messages'] if m.get('isOwn')],
                         'the manifest card is still one of the agent\'s messages')
        self.assertEqual(head['batch'], frozen['batch'], 'every message of the frozen read, control cards included, byte for byte')
        expected = copy.deepcopy(frozen['briefing'])
        expected[0]['batch_id'] = head['receipt_id']
        self.assertEqual(head['briefing'], expected)
        self.assertEqual(head['briefing'][0]['messages'][0]['dmControl'], MANIFEST['dmControl'])
        inputs = [m for m in frozen['batch']['messages'] if m['number'] in head['pending_message_numbers']]
        policy = tool_policy.turn_context(frozen_row['tool_policy'], self.adapter._guest_tools_override,
                                          agree=agreement.exposed_for(self.client.store, 'a'), dm=True)
        self.assertEqual(event.text, json.dumps(head, ensure_ascii=False) + ('\n\n' + owner_context if owner_context else '') + '\n\n' + adapter_module.DM_MEDIA_HINT
                         + '\n\nCURRENT BOTSESH INPUTS (exact text; these pending inputs share this frozen turn):\n' + json.dumps(inputs, ensure_ascii=False)
                         + '\n\nBOTSESH RUNTIME TOOL POLICY (not room-authored data):\n' + policy)
        self.assertNotIn('BOTSESH DM, your server-verified owner', event.text)

    async def test_a_non_owner_dm_turn_keeps_the_original_packet_and_policy(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo'), human(5, 'again')]
        self.live['human_owns_grant'] = False
        event = await self.deliver(5, complete=False)
        self.assert_original_full_packet(event, self.client.store.room('a')['processing_receipts']['5'])
        self.assertIsNone(self.adapter._current['a']['lean_session'])

    async def test_a_dm_service_checkin_turn_keeps_the_original_packet_and_policy(self):
        source = dict(type='botsesh_checkin', id='schedule', occurrenceId='occ', roomId='a', participantId='agent', approvedBy='owner', active=True)
        self.thread += [agent(4, 'yo'), {'id': 106, 'number': 5, 'text': 'scheduled check-in', 'isOwn': False, 'senderParticipantId': None,
                                         'authorParticipant': None, 'editCount': 0, 'checkin': source}]
        await self.deliver(3)
        self.policy_override = lambda room, args: {
            'room_id': room, 'participant_id': 'agent', 'number': args['target'], 'batch_id': args['receipt_id'], 'toolsAllowed': False,
            'author_is_agent_owner': False, 'batch_all_authors_are_agent_owner': False, 'guest_tools_default': False,
            'tools_ceiling_allowed': False, 'requester': {'id': 'owner', 'isOwner': False}, 'service_input': source}
        event = await self.deliver(5, complete=False)
        self.assert_original_full_packet(event, self.client.store.room('a')['processing_receipts']['5'])
        self.assertIsNone(self.adapter._current['a']['lean_session'])

    async def test_a_dm_takeover_turn_keeps_the_original_packet_and_policy(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo'), human(5, 'again')]
        offer = {'event_id': 'takeover-event', 'number': 5, 'accepted': True, 'inference_authorized': True}
        self.client.store.update(lambda d: d['rooms']['a'].setdefault('processing_receipts', {}).update(
            {'5': {'target': 5, 'received_at': time.time(), 'state': 'received', 'takeover_offer': offer}}))
        original = self.client.enrolled.side_effect

        def enrolled(room, op, **args):
            if op == 'takeover_pickup':
                return {'task': {'id': 'task-1'}}
            return original(room, op, **args)
        self.client.enrolled.side_effect = enrolled
        self.targets['a'] = []
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        self.assertEqual(event.message_id, 'takeover:takeover-event')
        row = self.client.store.room('a')['processing_receipts']['5']
        self.assertIn('takeover_offer', json.loads(event.text.split('\n\n', 1)[0]))
        from hermes_botsesh_test.owner_operator import owner_turn_context
        message = next(m for m in row['frozen']['batch']['messages'] if m['number'] == 5)
        owner_context = owner_turn_context(self.client, self.client.store.room('a'), self.adapter._dm_contexts['a'], message)
        self.assertIn('private DM with your owner', owner_context, 'the original takeover turn still carries the owner line')
        self.assert_original_full_packet(event, row, extra_head=('takeover_offer',), owner_context=owner_context)
        self.assertIsNone(self.adapter._current['a']['lean_session'])


class LeanPieceTests(unittest.TestCase):
    def test_plan_requires_the_exact_session_and_a_reachable_window(self):
        thread = [MANIFEST, human(1, 'a'), agent(2, 'b'), human(3, 'c')]
        binding = {'dm_session': {'session_id': 's1', 'through': 1}}
        self.assertEqual(lean_dm.plan(binding, 's1', thread, {3}, OWNER), {'session_id': 's1', 'missed': []})
        self.assertIsNone(lean_dm.plan(binding, 's2', thread, {3}, OWNER))
        self.assertIsNone(lean_dm.plan(binding, None, thread, {3}, OWNER))
        self.assertIsNone(lean_dm.plan({}, 's1', thread, {3}, OWNER))
        self.assertIsNone(lean_dm.plan({'dm_session': {'session_id': 's1', 'through': 'x'}}, 's1', thread, {3}, OWNER))
        self.assertIsNone(lean_dm.plan({'dm_session': {'session_id': 's1', 'through': -5}}, 's1', thread[2:], {3}, OWNER) and None)
        far = {'dm_session': {'session_id': 's1', 'through': 0}}
        self.assertIsNone(lean_dm.plan(far, 's1', [human(5, 'x')], {5}, OWNER))

    def test_missed_is_bounded_and_skips_controls_and_own(self):
        thread = [MANIFEST] + [human(n, 'm%d' % n) for n in range(2, 20)]
        binding = {'dm_session': {'session_id': 's', 'through': 1}}
        consumed = {'dm_session': {'session_id': 's', 'through': 1}, 'control_inputs': {'3': {'received_at': 1}}}
        listed = lean_dm.plan(consumed, 's', [MANIFEST, human(3, '/model x'), human(4, 'between'), human(5, 'now')], {5}, OWNER)
        self.assertEqual([m['number'] for m in listed['missed']], [4], 'a slash command the gateway took is not conversation')
        newer = lean_dm.plan(binding, 's', [MANIFEST, human(3, 'x'), human(4, 'newer than the input')], {3}, OWNER)
        self.assertIsNone(newer, 'a newer message in the frozen read would be attested by the reply but reaches the model nowhere: full catch-up')
        self.assertIsNone(lean_dm.plan(binding, 's', thread, {19}, OWNER), 'more than the cap: full catch-up')
        small = lean_dm.plan(binding, 's', [MANIFEST, agent(2, 'mine'), human(3, 'x'), human(4, 'y')], {4}, OWNER)
        self.assertEqual([m['number'] for m in small['missed']], [3])
        self.assertIsNone(lean_dm.plan(binding, 's', [MANIFEST, human(3, 'x' * 9000), human(4, 'y')], {4}, OWNER))
        self.assertIsNone(lean_dm.plan(binding, 's', [MANIFEST, human(3, 'see file', attachments=[{'id': 'f'}]), human(4, 'y')], {4}, OWNER))

    def test_render_keeps_exact_text_numbers_and_reply_context(self):
        message = human(7, 'line one\nline two', replyToNumber=5, replyPreview={'number': 5, 'text': 'earlier words'})
        text = lean_dm.render(label='r1', target=7, batch_targets=[7], name='Owner', inputs=[message], missed=[])
        self.assertEqual(json.loads(text.split('\n', 1)[0]), {'receipt_id': 'r1', 'addressed_message_number': 7})
        self.assertTrue(text.endswith('#7 (replying to #5: "earlier words") line one\nline two'))
        several = lean_dm.render(label='r1', target=8, batch_targets=[7, 8], name='Owner', inputs=[message, human(8, 'next')], missed=[])
        self.assertEqual(json.loads(several.split('\n', 1)[0])['pending_message_numbers'], [7, 8])
        self.assertIn('New messages from your owner (oldest first):', several)

    def test_render_carries_attachments_voice_and_tools_off_policy(self):
        files = [{'message_number': 9, 'name': 'note.ogg', 'size': 3, 'provenance': 'room_data', 'voice_note': True, 'delivered': True}]
        text = lean_dm.render(label='r1', target=9, batch_targets=[9], name='Owner', inputs=[human(9, '', attachments=[{'id': 'f'}])],
                              missed=[], media={'files': files}, runtime_policy='CURRENT RUNTIME AUTHORIZATION: x')
        self.assertIn('ATTACHMENTS (room data from your owner', text)
        self.assertIn('"voice_note": true', text)
        self.assertIn('voice note', text)
        self.assertIn('BOTSESH RUNTIME TOOL POLICY (not room-authored data):\nCURRENT RUNTIME AUTHORIZATION: x', text)
        self.assertTrue(text.endswith('#9 (no text; see the attachments)'))
        self.assertLess(text.index('ATTACHMENTS'), text.index('#9 (no text'))

    def test_without_controls_is_a_copy_and_keeps_pending_controls(self):
        result = {'receipt_id': 'x', 'briefing': [{'messages': [MANIFEST, human(1, 'a')]}], 'batch': {'messages': [MANIFEST, human(1, 'a')]}}
        view, omitted = lean_dm.without_controls(result)
        self.assertEqual(omitted, 1)
        self.assertEqual([m['number'] for m in view['batch']['messages']], [1])
        self.assertEqual([m['number'] for m in view['briefing'][0]['messages']], [1])
        self.assertEqual(len(result['batch']['messages']), 2, 'the frozen result is untouched')
        view, omitted = lean_dm.without_controls(result, keep=[0])
        self.assertEqual(omitted, 0)

    def test_catch_up_text_is_bounded_and_newest_first_kept(self):
        thread = [human(n, 'x' * 400) for n in range(1, 60)]
        text = lean_dm.catchup_text(thread, {59})
        self.assertLess(len(text), 9500)
        self.assertIn('older messages omitted', text)
        self.assertIn('#58 ', text)
        self.assertNotIn('#1 ', text)


def load_tests(loader, tests, pattern):
    """Only this file's cases (the SolidTests fixture's own cases are covered by test_solid)."""
    suite = unittest.TestSuite()
    for name in loader.getTestCaseNames(LeanDmTests):
        if name in LeanDmTests.__dict__:
            suite.addTest(LeanDmTests(name))
    suite.addTests(loader.loadTestsFromTestCase(LeanPieceTests))
    return suite


if __name__ == '__main__':
    unittest.main()
