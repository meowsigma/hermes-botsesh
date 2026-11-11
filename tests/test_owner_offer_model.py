"""Exact two-turn native admission replay, transport doubles, zero inference."""
# Owner 2026-11-10 living chat: pending inputs are a batch, not one trailing quote.
import copy
import json
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from test_solid import SolidTests
from hermes_botsesh_test import tools
from hermes_botsesh_test.adapter import _ADAPTERS
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test.owner_operator import owner_turn_context
from hermes_botsesh_test.managed_rooms import resolve_room, source_dm
from gateway.session_context import set_session_vars, clear_session_vars
from gateway.platforms.event import ProcessingOutcome

ROOM = 'R' * 32
LINE = 'You are in a private DM with your owner "Owner". They can ask you to create rooms or to make them the owner of a room you manage.'


class OwnerOfferTests(SolidTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.enterContext(patch.object(tools, 'Client', return_value=self.client))
        _ADAPTERS.clear(); _ADAPTERS.add(self.adapter)
        self.client.store.update(lambda d: d['rooms']['a'].update(room_type='dm', participant_id='agent', oauth_id='grant', oauth_root='source', origin='https://botsesh.com'))
        self.live = {'room_id': 'a', 'room_type': 'dm', 'participant_id': 'agent', 'membership_id': 'member', 'human_participant_id': 'owner', 'human_owns_grant': True, 'access_state': 'authorized'}
        original = self.client.enrolled.side_effect
        def enrolled(room, op, **args):
            if op == 'dm_context':
                self.calls.append((room, op, args)); return dict(self.live)
            if op == 'offer_ownership':
                self.calls.append((room, op, args)); return {'room_id': room, 'state': 'ownership_offered', 'claim_link': 'https://botsesh.com/test-only-claim'}
            result = original(room, op, **args)
            if op == 'read':
                result['batch']['messages'][0]['text'] = 'Make a room for my plant swap and send me the link' if args['target_number'] == 3 else 'Make me the owner'
            return result
        self.client.enrolled.side_effect = enrolled
        self.adapter._publish_dm_manifest = AsyncMock()
        def create(name, purpose, client_id):
            self.client.store.update(lambda d: d['rooms'].update({ROOM: {'room_id': ROOM, 'url': 'https://botsesh.com/b/' + ROOM, 'name': name, 'active': True, 'manager': True, 'management_only': True, 'transport': 'guest_manager', 'state': 'created'}}))
            return {'room_id': ROOM, 'share_link': 'https://botsesh.com/b/' + ROOM}
        self.client.create = Mock(side_effect=create)
        self.ctx = Mock(); self.ctx.session_surface.return_value = 'botsesh'
        tools.register_tools(self.ctx)
        self.handlers = {c.kwargs['name']: c.kwargs['handler'] for c in self.ctx.register_tool.call_args_list}

    def call(self, tool, args, target='3'):
        tokens = set_session_vars(platform='botsesh', chat_id='a', message_id=target)
        try:
            return json.loads(self.handlers[tool](args))
        finally:
            clear_session_vars(tokens)

    async def test_exact_create_then_make_me_owner_transcript(self):
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        self.assertIn(LINE, event.text)
        inputs = json.loads(event.text.split('CURRENT BOTSESH INPUTS (exact text; these pending inputs share this frozen turn):\n', 1)[1])
        self.assertEqual([(m['number'], m['text']) for m in inputs], [(3, 'Make a room for my plant swap and send me the link')])
        result = self.call('botsesh_create_room', {'name': 'Plant Swap'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['room_id'], ROOM)
        room = self.client.store.room(ROOM)
        self.assertEqual(room['created_from_owner_dm'], source_dm(self.client, 'a'))
        self.adapter._current['a']['delivered'] = True
        await self.adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        self.assertIn(LINE, event.text)
        self.assertIn('Most recent room created from this owner DM:', event.text)
        self.assertIn(ROOM, event.text)
        inputs = json.loads(event.text.split('CURRENT BOTSESH INPUTS (exact text; these pending inputs share this frozen turn):\n', 1)[1])
        self.assertEqual([(m['number'], m['text']) for m in inputs], [(5, 'Make me the owner')])
        listed = self.call('botsesh_rooms', {}, '5')['result']['rooms']
        created = next(r for r in listed if r['room_id'] == ROOM)
        self.assertIs(created['managed_by_this_agent'], True)
        self.assertIs(created['created_from_this_owner_dm'], True)
        self.assertNotIn('created_from_owner_dm', created)
        for args in ({'room': 'Plant Swap'}, {'room': 'the room you just made'}, {'room_id': result['result']['room_id']}, {}):
            offered = self.call('botsesh_offer_ownership', {**args, 'user_requested': True}, '5')
            self.assertTrue(offered['ok'], offered)
            self.assertEqual(offered['result']['room_id'], ROOM)
        offers = [(r, a) for r, op, a in self.calls if op == 'offer_ownership']
        self.assertEqual(len(offers), 4)
        self.assertEqual(len({a['client_id'] for r, a in offers}), 1, 'resolved exact ID retains durable intent')
        self.assertTrue(all(r == ROOM for r, a in offers))

    async def test_foreign_group_no_flag_and_truthy_never_get_line_or_management(self):
        self.client.store.update(lambda d: d['rooms'].update({ROOM: {'room_id': ROOM, 'name': 'Hidden managed room', 'active': True, 'manager': True, 'management_only': True, 'transport': 'guest_manager'}}))
        for label, changes in [('foreign', {'human_owns_grant': False}), ('no flag', {'human_owns_grant': None}), ('truthy', {'human_owns_grant': 1}), ('group', {'room_type': 'room'}), ('paused', {'access_state': 'paused'}), ('wrong room', {'room_id': 'other'}), ('wrong human', {'human_participant_id': 'other'})]:
            with self.subTest(label=label):
                original = dict(self.live)
                self.live.update(changes)
                if label == 'group': self.client.store.update(lambda d: d['rooms']['a'].update(room_type='room'))
                await self.adapter._check_room('a')
                event = self.adapter._admit_event.call_args.args[0]
                self.assertNotIn('You are in a private DM with your owner', event.text)
                self.assertFalse(self.call('botsesh_create_room', {'name': 'Plant Swap'})['ok'])
                self.assertFalse(self.call('botsesh_offer_ownership', {'room': 'the room you just made', 'user_requested': True})['ok'])
                self.assertEqual([r['room_id'] for r in self.call('botsesh_rooms', {})['result']['rooms']], ['a'])
                self.client.create.assert_not_called()
                self.assertFalse(any(op == 'offer_ownership' for r, op, a in self.calls))
                self.adapter._current.pop('a', None)
                self.client.store.update(lambda d: (d['rooms']['a'].update(room_type='dm', processing_receipts={}), d['rooms']['a'].pop('handled', None)))
                self.live.clear(); self.live.update(original)

    async def test_ambiguous_unknown_inactive_names_and_other_dm_recent_fail_closed(self):
        await self.adapter._check_room('a')
        self.assertTrue(self.call('botsesh_create_room', {'name': 'Plant Swap'})['ok'])
        duplicate = copy.deepcopy(self.client.store.room(ROOM)); duplicate['room_id'] = 'S' * 32
        self.client.store.update(lambda d: d['rooms'].update({duplicate['room_id']: duplicate}))
        result = self.call('botsesh_offer_ownership', {'room': 'Plant Swap', 'user_requested': True})
        self.assertEqual(result['error'], 'room_ambiguous_ask_which_room')
        self.assertEqual(self.call('botsesh_offer_ownership', {'room': 'not here', 'user_requested': True})['error'], 'managed_room_not_found')
        self.assertEqual(self.call('botsesh_offer_ownership', {'room': 'the room you just made', 'user_requested': True})['error'], 'room_ambiguous_ask_which_room', 'tied recent provenance must not guess')
        self.client.store.update(lambda d: d['rooms'][duplicate['room_id']].update(active=False))
        self.assertEqual(resolve_room(self.client, {'room': 'Plant Swap'}, 'a'), ROOM)
        self.client.store.update(lambda d: d['rooms']['a'].update(oauth_id='rebound'))
        with self.assertRaises(BotSeshError): resolve_room(self.client, {'room': 'the room you just made'}, 'a')
        self.assertFalse(any(op == 'offer_ownership' for r, op, a in self.calls))

    async def test_display_projection_needs_exact_fresh_verified_sender(self):
        await self.adapter._check_room('a')
        binding = self.client.store.room('a')
        context = self.adapter._dm_contexts['a']
        message = binding['processing_receipts']['3']['frozen']['batch']['messages'][0]
        self.assertIn(LINE, owner_turn_context(self.client, binding, context, message))
        for mutate in (lambda c, m: c.update(until=0), lambda c, m: m['authorParticipant'].update(verified=False), lambda c, m: m.update(senderParticipantId='foreign'), lambda c, m: c['operator_projection'].pop('human_owns_grant')):
            c, m = copy.deepcopy((context, message)); mutate(c, m)
            self.assertEqual(owner_turn_context(self.client, binding, c, m), '')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(OwnerOfferTests(name) for name in OwnerOfferTests.__dict__ if name.startswith('test_'))
