"""0.10.3 lean owner-DM deliveries: transport doubles, zero inference."""
import copy
import json
import re
import time
import unittest
from unittest.mock import AsyncMock, Mock

from test_solid import SolidTests
from hermes_botsesh_test import adapter as adapter_module, lean_dm
from hermes_botsesh_test.adapter import _ADAPTERS, started_turn
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


class LeanDmTests(SolidTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        _ADAPTERS.clear(); _ADAPTERS.add(self.adapter)
        self.client.store.update(lambda d: d['rooms']['a'].update(room_type='dm', participant_id='agent', oauth_id='grant', oauth_root='source', origin='https://botsesh.com'))
        self.live = {'room_id': 'a', 'room_type': 'dm', 'participant_id': 'agent', 'membership_id': 'member', 'human_participant_id': OWNER, 'human_owns_grant': True, 'access_state': 'authorized'}
        self.thread = [MANIFEST, human(1, 'hi'), agent(2, 'yo'), human(3, 'Yoo wsp big dawg')]
        self.targets['a'] = [3]
        original = self.client.enrolled.side_effect

        def enrolled(room, op, **args):
            if op == 'dm_context':
                self.calls.append((room, op, args)); return dict(self.live)
            if op == 'read':
                self.calls.append((room, op, args))
                n = args['target_number']
                messages = [copy.deepcopy(m) for m in self.thread if m['number'] <= n or m['number'] in self.targets['a']]
                return {'receipt_id': 'receipt-%d' % n, 'read_effect': 'none',
                        'briefing': [{'batch_id': 'receipt-%d' % n, 'instructions': 'You are a member of this group chat. ' * 20, 'messages': copy.deepcopy(messages)}],
                        'batch': {'nextCheckpoint': n, 'messages': messages}}
            return original(room, op, **args)
        self.client.enrolled.side_effect = enrolled
        self.adapter._publish_dm_manifest = AsyncMock()
        self.store = FakeSessionStore('s1')
        self.adapter._session_store = self.store

    async def deliver(self, target, session='s1', complete=True, session_id=None):
        """Build the delivery for `target`; optionally complete the turn in Hermes session `session`."""
        self.targets['a'] = [target]
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        if complete:
            current = self.adapter._current['a']
            current.update(hermes_session_id=session, model_processed=True, delivered=True)
            await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
            self.assertNotIn('a', self.adapter._current)
        return event

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
        self.assertEqual(self.client.store.room('a')['dm_session'], {'session_id': 's1', 'through': 3})
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

    async def test_hook_safety_net_attaches_a_compact_catch_up_when_hermes_started_a_fresh_session(self):
        await self.deliver(3)
        self.thread += [agent(4, 'yo, not much'), human(5, 'Yoo wsp big dawg')]
        event = await self.deliver(5, complete=False)
        current = self.adapter._current['a']
        self.assertEqual(current['lean_session'], 's1')
        # Hermes reports the same session with history: nothing attached
        self.assertIsNone(started_turn(user_message=event.text, platform='botsesh', session_id='s1', turn_id='t1', is_first_turn=False))
        current.pop('hook_turn', None)
        # Hermes started a different session (reset between the check and the turn): the history rides pre_llm_call
        context = started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t2', is_first_turn=True)
        self.assertIsInstance(context, dict)
        text = context['context']
        self.assertIn('BOTSESH DM CATCH-UP', text)
        self.assertIn('#1 "Owner": hi', text)
        self.assertIn('#4 YOU (your earlier reply): yo, not much', text)
        self.assertNotIn('cmd84', text)
        self.assertLess(len(text), 9500, 'Hermes spills a larger hook context to a file preview')
        self.assertNotIn('#5 ', text, 'the current input is in the message itself')
        # once per delivery
        self.assertIsNone(started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t2', is_first_turn=True))

    async def test_a_full_delivery_never_gets_the_safety_net(self):
        event = await self.deliver(3, complete=False)
        self.assertIsNone(started_turn(user_message=event.text, platform='botsesh', session_id='s9', turn_id='t1', is_first_turn=True))


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
        self.assertIsNone(lean_dm.plan(binding, 's', thread, {19}, OWNER), 'more than the cap: full catch-up')
        small = lean_dm.plan(binding, 's', [MANIFEST, agent(2, 'mine'), human(3, 'x'), human(4, 'y')], {4}, OWNER)
        self.assertEqual([m['number'] for m in small['missed']], [3])
        self.assertIsNone(lean_dm.plan(binding, 's', [MANIFEST, human(3, 'x' * 9000), human(4, 'y')], {4}, OWNER))
        self.assertIsNone(lean_dm.plan(binding, 's', [MANIFEST, human(3, 'see file', attachments=[{'id': 'f'}]), human(4, 'y')], {4}, OWNER))

    def test_render_keeps_exact_text_numbers_and_reply_context(self):
        message = human(7, 'line one\nline two', replyToNumber=5, replyPreview={'number': 5, 'text': 'earlier words'})
        text = lean_dm.render(room_id='R', label='r1', target=7, batch_targets=[7], name='Owner', inputs=[message], missed=[])
        self.assertEqual(json.loads(text.split('\n', 1)[0]), {'room_id': 'R', 'receipt_id': 'r1', 'addressed_message_number': 7})
        self.assertTrue(text.endswith('#7 (replying to #5: "earlier words") line one\nline two'))
        several = lean_dm.render(room_id='R', label='r1', target=8, batch_targets=[7, 8], name='Owner', inputs=[message, human(8, 'next')], missed=[])
        self.assertEqual(json.loads(several.split('\n', 1)[0])['pending_message_numbers'], [7, 8])
        self.assertIn('New messages from your owner (oldest first):', several)

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
