"""Ownership offer boundaries; offline doubles are not native host proof."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hermes_ownership_test', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from hermes_ownership_test import tools
from hermes_ownership_test.client import BotSeshError
from hermes_ownership_test.oauth import OAuthClient

class OwnershipTests(unittest.TestCase):
    def test_offer_requires_operator_request_and_denies_room_messages(self):
        ctx = Mock()
        with patch.object(tools, 'calling_scope', return_value=('botsesh', 'room')):
            with self.assertRaises(BotSeshError):
                tools.offer_ownership({'room_id': 'room', 'user_requested': True}, ctx)
            with self.assertRaises(BotSeshError):
                tools.revoke_ownership_offer({'room_id': 'room', 'offer_id': str(uuid4())}, ctx)
        with patch.object(tools, 'calling_scope', return_value=('cli', '')):
            for request in (None, False, 'true', 1):
                with self.assertRaises(BotSeshError):
                    tools.offer_ownership({'room_id': 'room', 'user_requested': request}, ctx)

    def test_offer_preserves_room_id_uuid_and_private_return(self):
        client, ctx = Mock(), Mock()
        client.enrolled.return_value = {'claim_link': 'https://botsesh.com/api/rooms/room/ownership#private'}
        operation = str(uuid4())
        with patch.object(tools, 'calling_scope', return_value=('cli', '')), patch.object(tools, 'Client', return_value=client):
            result = tools.offer_ownership({'room_id': 'room', 'client_id': operation, 'user_requested': True}, ctx)
        client.enrolled.assert_called_once_with('room', 'offer_ownership', user_requested=True, client_id=operation)
        self.assertEqual(result, client.enrolled.return_value)
        client.logical_id.assert_not_called()

    def test_default_id_is_stable_logical_operation_not_auto_renewal(self):
        client = Mock()
        client.logical_id.return_value = str(uuid4())
        with patch.object(tools, 'calling_scope', return_value=('cli', '')), patch.object(tools, 'Client', return_value=client):
            tools.offer_ownership({'room_id': 'room', 'user_requested': True}, Mock())
        client.logical_id.assert_called_once_with('ownership-offer', {'room_id': 'room'})

    def test_oauth_offer_and_revoke_are_pure_python_exact_scoped_rpc(self):
        client = OAuthClient(Mock())
        client.rpc = Mock(return_value={'state': 'ownership_offered'})
        binding = {'room_id': 'exact-room'}
        operation = str(uuid4())
        client.call(binding, 'offer_ownership', client_id=operation, user_requested=True)
        client.rpc.assert_called_once_with(binding, 'offer_ownership', {'room_id': 'exact-room', 'client_id': operation, 'user_requested': True})
        client.rpc.reset_mock()
        client.call(binding, 'revoke_ownership_offer', offer_id=operation)
        client.rpc.assert_called_once_with(binding, 'revoke_ownership_offer', {'room_id': 'exact-room', 'offer_id': operation})
        for args in ({'client_id': 'not-a-uuid', 'user_requested': True}, {'client_id': operation, 'user_requested': 'true'}):
            with self.assertRaises(BotSeshError):
                client.call(binding, 'offer_ownership', **args)

    def test_registered_handlers_and_manifest_cover_both_tools(self):
        ctx = Mock()
        tools.register_tools(ctx)
        registered = {call.kwargs['name']: call.kwargs for call in ctx.register_tool.call_args_list}
        self.assertEqual(set(registered), {row['name'] for row in tools.SCHEMAS})
        for name in ('botsesh_offer_ownership', 'botsesh_revoke_ownership_offer'):
            self.assertIn('  - ' + name, (ROOT / 'plugin.yaml').read_text())
            self.assertFalse(registered[name]['schema']['parameters']['additionalProperties'])
        with patch.object(tools, 'offer_ownership', return_value={'state': 'ownership_offered'}) as call:
            result = json.loads(registered['botsesh_offer_ownership']['handler']({'room_id': 'room', 'user_requested': True}))
        self.assertTrue(result['ok'])
        call.assert_called_once()

if __name__ == '__main__':
    unittest.main()
