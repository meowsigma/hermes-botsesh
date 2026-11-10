"""Actual native adapter admission + tool dispatch with offline transport doubles."""
import json
import unittest
from unittest.mock import AsyncMock, Mock
from test_solid import SolidTests
from hermes_botsesh_test import tools
from hermes_botsesh_test.adapter import _ADAPTERS
from gateway.session_context import set_session_vars, clear_session_vars


class OwnerDmAdmissionTests(SolidTests):
    async def test_admission_pins_owner_dm_identity_and_registered_tool_uses_fresh_lookup(self):
        _ADAPTERS.clear(); _ADAPTERS.add(self.adapter)
        self.client.store.update(lambda d: d['rooms']['a'].update(room_type='dm', participant_id='agent', oauth_id='grant', oauth_root='source', origin='https://botsesh.com'))
        original = self.client.enrolled.side_effect
        live = {'room_id': 'a', 'room_type': 'dm', 'participant_id': 'agent', 'membership_id': 'member',
                'human_participant_id': 'owner', 'human_owns_grant': True, 'access_state': 'authorized'}
        def enrolled(room, op, **args):
            if op == 'dm_context':
                self.calls.append((room, op, args))
                return dict(live)
            return original(room, op, **args)
        self.client.enrolled.side_effect = enrolled
        self.adapter._publish_dm_manifest = AsyncMock()  # catalog is unrelated to management authority
        self.client.create = Mock(return_value={'room_id': 'new', 'share_link': 'https://botsesh.com/b/new'})
        await self.adapter._check_room('a')
        event = self.adapter._admit_event.call_args.args[0]
        self.assertEqual(event.source.message_id, '3')
        self.assertEqual(self.adapter._current['a']['operator_binding'], {
            'participant_id': 'agent', 'oauth_id': 'grant', 'oauth_root': 'source', 'origin': 'https://botsesh.com',
            'membership_id': 'member', 'human_id': 'owner'})
        ctx = Mock(); ctx.session_surface.return_value = 'botsesh'
        tools.register_tools(ctx)
        handler = next(c.kwargs['handler'] for c in ctx.register_tool.call_args_list if c.kwargs['name'] == 'botsesh_create_room')
        tokens = set_session_vars(platform='botsesh', chat_id='a', message_id='3')
        try:
            self.assertTrue(json.loads(handler({'name': 'Plants'}))['ok'])
            self.assertEqual(len([c for c in self.calls if c[1] == 'dm_context']), 2, 'fresh check after admission')
            live['human_owns_grant'] = False
            self.assertEqual(json.loads(handler({'name': 'Other'}))['error'], 'owner_dm_operator_required')
            self.client.create.assert_called_once_with('Plants', None, None)
        finally:
            clear_session_vars(tokens)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([OwnerDmAdmissionTests('test_admission_pins_owner_dm_identity_and_registered_tool_uses_fresh_lookup')])
