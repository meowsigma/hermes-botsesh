"""Focused owner-DM authorization doubles; not native/model proof."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('owner_dm_test', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from owner_dm_test import tools
from owner_dm_test.client import BotSeshError


class OwnerDmTests(unittest.TestCase):
    def setUp(self):
        self.binding = {'active': True, 'room_type': 'dm', 'transport': 'oauth', 'room_id': 'dm',
                        'participant_id': 'agent', 'oauth_id': 'grant', 'oauth_root': 'source', 'origin': 'https://botsesh.com'}
        self.identity = {key: self.binding.get(key) for key in ('participant_id', 'oauth_id', 'oauth_root', 'origin')}
        self.identity.update(membership_id='member', human_id='human')
        self.message = {'number': 3, 'isOwn': False, 'senderParticipantId': 'human', 'text': 'Make a room for my plant swap',
                        'authorParticipant': {'id': 'human', 'verified': True, 'name': 'Owner'}}
        self.row = {'state': 'admitted', 'receipt_id': 'receipt', 'frozen': {'receipt_id': 'receipt', 'batch': {'messages': [self.message]}}}
        self.binding['processing_receipts'] = {'3': self.row}
        self.current = {'target': 3, 'receipt_id': 'receipt', 'delivered': False, 'operator_binding': self.identity}
        self.client = Mock()
        self.client.store.room.return_value = self.binding
        self.live = {'room_id': 'dm', 'room_type': 'dm', 'participant_id': 'agent', 'membership_id': 'member',
                     'human_participant_id': 'human', 'human_owns_grant': True, 'access_state': 'authorized'}
        self.client.enrolled.return_value = self.live
        self.client.create.return_value = {'room_id': 'new', 'share_link': 'https://botsesh.com/b/new'}
        self.adapter = types.SimpleNamespace(_current={'dm': self.current}, client=self.client, _is_sender_authorized=Mock(return_value=True))
        # Adapter import alone requires host gateway registration. The guard uses
        # this same real adapter registry interface, with no gateway/network double side effects.
        self.adapter_module = types.ModuleType('owner_dm_test.adapter')
        self.adapter_module._ADAPTERS = [self.adapter]
        self.adapter_module._HOOK_LOCK = threading.RLock()
        self.scope = {'HERMES_SESSION_PLATFORM': 'botsesh', 'HERMES_SESSION_CHAT_ID': 'dm', 'HERMES_SESSION_MESSAGE_ID': '3'}
        self.stack = __import__('contextlib').ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {'owner_dm_test.adapter': self.adapter_module}))
        self.stack.enter_context(patch('gateway.session_context.get_session_env', side_effect=lambda key, default='': self.scope.get(key, default)))
        self.stack.enter_context(patch.object(tools, 'Client', return_value=self.client))
        self.ctx = Mock()
        self.ctx.session_surface.return_value = 'botsesh'

    def test_owner_dm_creation_and_offer_are_allowed_with_fresh_checks(self):
        self.assertEqual(tools.create_room({'name': 'Plants'}, self.ctx), self.client.create.return_value)
        self.client.create.assert_called_once_with('Plants', None, None)
        self.client.enrolled.return_value = self.live
        tools.offer_ownership({'room_id': 'new', 'user_requested': True, 'client_id': 'operation'}, self.ctx)
        self.assertEqual([c.args for c in self.client.enrolled.call_args_list], [('dm', 'dm_context'), ('dm', 'dm_context'), ('new', 'offer_ownership')])

    def test_foreign_group_spoof_and_missing_authority_are_denied_before_effects(self):
        mutants = [
            ('foreign DM', lambda: self.live.update(human_owns_grant=False)),
            ('old server', lambda: self.live.pop('human_owns_grant')),
            ('truthy flag', lambda: self.live.update(human_owns_grant=1)),
            ('group', lambda: self.binding.update(room_type='room')),
            ('paused', lambda: self.live.update(access_state='paused')),
            ('spoofed name', lambda: self.message.update(senderParticipantId='stranger', authorParticipant={'id': 'stranger', 'verified': True, 'name': 'Owner'})),
            ('unverified author', lambda: self.message['authorParticipant'].update(verified=False)),
            ('relayed other author', lambda: self.message.update(senderParticipantId='other')),
            ('wrong participant', lambda: self.live.update(participant_id='foreign')),
            ('wrong human', lambda: self.live.update(human_participant_id='foreign')),
            ('wrong member', lambda: self.live.update(membership_id='foreign')),
            ('wrong room', lambda: self.live.update(room_id='foreign')),
            ('wrong target', lambda: self.scope.update(HERMES_SESSION_MESSAGE_ID='4')),
            ('wrong platform', lambda: self.scope.update(HERMES_SESSION_PLATFORM='cli')),
            ('missing turn pin', lambda: self.current.pop('operator_binding')),
            ('stale receipt', lambda: self.row.update(receipt_id='foreign')),
            ('stale frozen receipt', lambda: self.row['frozen'].update(receipt_id='foreign')),
            ('settled turn', lambda: self.row.update(state='replied')),
            ('failed turn', lambda: self.row.update(explicit_failure={'payload': {}})),
            ('delivered', lambda: self.current.update(delivered=True)),
            ('unauthorized', lambda: self.adapter._is_sender_authorized.configure_mock(return_value=False)),
            ('rebound grant', lambda: self.binding.update(oauth_id='other')),
        ]
        original = copy.deepcopy((self.binding, self.current, self.live, self.scope))
        for label, mutate in mutants:
            with self.subTest(label=label):
                self.binding.clear(); self.binding.update(copy.deepcopy(original[0]))
                self.row = self.binding['processing_receipts']['3']; self.message = self.row['frozen']['batch']['messages'][0]
                self.current.clear(); self.current.update(copy.deepcopy(original[1]))
                self.live.clear(); self.live.update(copy.deepcopy(original[2]))
                self.scope.clear(); self.scope.update(original[3])
                self.adapter._is_sender_authorized.return_value = True
                self.client.reset_mock(); self.client.enrolled.return_value = self.live
                mutate()
                with self.assertRaises(BotSeshError): tools.create_room({'name': 'Plants'}, self.ctx)
                self.client.create.assert_not_called()
                with self.assertRaises(BotSeshError): tools.offer_ownership({'room_id': 'new', 'user_requested': True}, self.ctx)
                self.assertFalse(any(c.args[1] == 'offer_ownership' for c in self.client.enrolled.call_args_list))

    def test_live_lookup_failure_or_binding_change_fails_closed(self):
        def rebound(*args):
            self.binding['oauth_id'] = 'replacement'
            return self.live
        self.client.enrolled.side_effect = rebound
        with self.assertRaises(BotSeshError): tools.create_room({}, self.ctx)
        self.client.create.assert_not_called()
        self.binding['oauth_id'] = 'grant'
        self.client.enrolled.side_effect = BotSeshError('invalid_grant', 401)
        with self.assertRaises(BotSeshError): tools.create_room({}, self.ctx)
        self.client.create.assert_not_called()

    def test_dm_does_not_unlock_account_connector_or_other_management(self):
        self.ctx.get_config.return_value = 'mcp__BotSesh__create_room'
        with self.assertRaises(BotSeshError): tools.create_room({'mode': 'account'}, self.ctx)
        self.ctx.dispatch_tool.assert_not_called()
        with self.assertRaises(BotSeshError): tools.offer_ownership({'room_id': 'new', 'user_requested': False}, self.ctx)
        with self.assertRaises(BotSeshError): tools.revoke_ownership_offer({'room_id': 'new', 'offer_id': 'offer'}, self.ctx)
        with self.assertRaises(BotSeshError): tools.scoped_room({'room_id': 'new'}, self.ctx)

    def test_local_operator_stays_allowed_and_other_platforms_do_not(self):
        for platform in ('cli', 'local', 'tui', 'desktop'):
            self.ctx.session_surface.return_value = platform
            tools.create_room({'name': 'Plants'}, self.ctx)
        for platform in ('telegram', 'discord', '', 'unknown'):
            self.ctx.session_surface.return_value = platform
            with self.assertRaises(BotSeshError): tools.create_room({}, self.ctx)
            with self.assertRaises(BotSeshError): tools.offer_ownership({'room_id': 'new', 'user_requested': True}, self.ctx)


if __name__ == '__main__':
    unittest.main()
