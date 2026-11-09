"""Declared OAuth public-helper contracts. Offline doubles, not native proof."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('botsesh_oauth_test', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from botsesh_oauth_test import oauth, http_transport
from botsesh_oauth_test.client import BotSeshError, Client
from botsesh_oauth_test.storage import Store, StorageError
from hermes_cli.plugin_provider_requests import ProviderResponse, ProviderNotSignedIn

PUBLIC_REQUEST = 'hermes_cli.plugin_provider_requests.credentialed_provider_request'


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / 'hermes'
        self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {'HERMES_HOME': str(self.home)})
        self.env.start()
        self.store = Store(self.home / 'plugin-data/botsesh')
        self.client = oauth.OAuthClient(self.store)
        self.binding = {'room_id': 'one', 'origin': 'https://botsesh.com', 'url': 'https://botsesh.com/b/one',
                        'name': 'One', 'participant_id': 'bot', 'oauth_id': 'rooms', 'auth_version': 2,
                        'transport': 'oauth', 'active': True, 'state': 'connected'}
        self.store.update(lambda d: d['rooms'].update(one=self.binding))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_listener_reports_exact_membership_state_without_read_effect(self):
        with patch.object(self.client, 'rpc', return_value={'read_effect': 'none'}) as rpc:
            self.client.call(self.binding, 'listener', state='listening')
            rpc.assert_called_once_with(self.binding, 'hermes_listener', {'room_id': 'one', 'state': 'listening'})
            self.client.call(self.binding, 'listener', state='stopped')
            self.assertEqual(rpc.call_args.args[2], {'room_id': 'one', 'state': 'stopped'})
            with self.assertRaises(BotSeshError):
                self.client.call(self.binding, 'listener', state='invented')

    def test_read_requests_complete_command_rich_history_before_size_fence(self):
        value = {'messages': [{'number': 0, 'text': 'exact', 'senderParticipantId': 'human', 'authorParticipant': {'id': 'human', 'verified': True}}],
                 'batch_id': str(uuid.uuid4()), 'ack_eligible': True, 'has_more': False, 'truncated': False, 'read_checkpoint': 0, 'next_checkpoint': 1}
        def catchup(binding, name, args=None):
            self.assertEqual(name, 'catchup')
            self.assertGreaterEqual(args['max_chars'], 150000)
            return value
        with patch.object(self.client, 'rpc', side_effect=catchup):
            read = self.client.call(self.binding, 'read', target_number=0)
        self.assertEqual(read['batch']['messages'][0]['text'], 'exact')
        self.assertEqual(read['briefing'][0], value)
        self.assertEqual(read['read_effect'], 'none')

    def test_null_reply_preview_and_unprocessed_checks_do_not_ack(self):
        result = {'messages': [{'number': 0, 'text': 'exact', 'senderParticipantId': 'human', 'authorParticipant': {'id': 'human', 'verified': True},
                               'mentions': [{'participant': None}, {'participant': {'id': 'bot'}}], 'replyPreview': None}],
                  'batch_id': str(uuid.uuid4()), 'ack_eligible': True, 'has_more': False, 'truncated': False, 'read_checkpoint': 0, 'next_checkpoint': 1}
        with patch.object(self.client, 'rpc', side_effect=lambda binding, name, args=None: {'targets': [{'number': 0}], 'has_more': False, 'backlog': 1} if name == 'hermes_inbox' else result) as rpc:
            checked = self.client.call(self.binding, 'check')
            self.assertEqual(checked['mentions'], [0])
            self.assertEqual(checked['replies'], [])
            self.assertNotIn('oauth_receipts', self.store.room('one'))
            self.assertEqual(self.client.call(self.binding, 'read')['read_effect'], 'none')
            self.assertEqual([c.args[1] for c in rpc.call_args_list], ['hermes_inbox', 'catchup'])

    def test_foreign_receipts_and_changed_retry_fail_before_network(self):
        with patch.object(self.client, 'rpc') as rpc:
            with self.assertRaises(BotSeshError):
                self.client.call(self.binding, 'post', text='exact', client_id=str(uuid.uuid4()), receipt_id=str(uuid.uuid4()), processed=True)
            rpc.assert_not_called()
        client_id = str(uuid.uuid4())
        exact = {'text': 'exact', 'client_id': client_id, 'receipt_id': str(uuid.uuid4()), 'processed': True}
        self.store.update(lambda d: d['rooms']['one'].update(oauth_posts={client_id: {'exact': exact, 'complete': True, 'result': {'number': 5}}}))
        with patch.object(self.client, 'rpc', return_value={'rooms': [{'room_id': 'one', 'participant_id': 'bot'}]}) as rpc:
            self.assertEqual(self.client.call(self.binding, 'post', **exact), {'number': 5})
            self.assertEqual(rpc.call_args.args[1], 'list_rooms')
            with self.assertRaises(BotSeshError):
                self.client.call(self.binding, 'post', **{**exact, 'text': 'changed'})

    def test_rpc_uses_declared_helper_without_token_or_auth_store_access(self):
        def respond(provider, method, url, **kwargs):
            self.assertEqual((provider, method, url), ('rooms', 'POST', 'https://botsesh.com/mcp'))
            self.assertEqual(kwargs['json']['params'], {'name': 'list_rooms', 'arguments': {}})
            self.assertNotIn('Authorization', kwargs['headers'])
            return ProviderResponse(200, {'content-type': 'application/json'}, json.dumps({
                'jsonrpc': '2.0', 'id': kwargs['json']['id'], 'result': {'structuredContent': {'rooms': []}}}).encode())
        with patch(PUBLIC_REQUEST, side_effect=respond) as request, \
             patch('hermes_cli.auth._load_auth_store', side_effect=AssertionError('plugin read credentials')), \
             patch('webbrowser.open', side_effect=AssertionError('plugin started login')):
            self.assertEqual(self.client.rpc(self.binding, 'list_rooms'), {'rooms': []})
        request.assert_called_once()
        self.assertFalse((self.home / 'auth.json').exists())

    def test_disconnect_shared_grant_requires_401_and_leaves_core_store_untouched(self):
        sentinel = self.home / 'auth.json'
        sentinel.write_bytes(b'core-owned opaque fixture')
        self.store.update(lambda d: d['rooms'].update(two={**self.binding, 'room_id': 'two'}))
        with patch.object(self.client, 'rpc', side_effect=[{'revoked': True}, BotSeshError('invalid_token', 401)]) as rpc:
            self.client.disconnect('one')
        self.assertEqual([c.args[1:] for c in rpc.call_args_list], [('hermes_disconnect', {'confirm': True}), ('list_rooms',)])
        self.assertEqual(sentinel.read_bytes(), b'core-owned opaque fixture')
        self.assertTrue(all(r['state'] == 'revoked' and not r['active'] for r in self.store.read()['rooms'].values()))
        with patch.object(self.client, 'rpc') as rpc:
            self.client.disconnect('one')
            rpc.assert_not_called()

    def test_disconnect_unconfirmed_or_transient_denial_keeps_binding_active(self):
        for replies in ([{'revoked': False}], [{'revoked': True}, {'rooms': []}],
                        [{'revoked': True}, BotSeshError('unavailable', 503)]):
            with self.subTest(replies=replies), patch.object(self.client, 'rpc', side_effect=replies):
                with self.assertRaises(BotSeshError):
                    self.client.disconnect('one')
            self.assertTrue(self.store.room('one')['active'])

    def test_closed_dm_disconnect_still_revokes_the_live_source_grant(self):
        self.store.update(lambda d: d['rooms'].update(dm={**self.binding, 'room_id': 'dm', 'oauth_root': 'one', 'active': False, 'state': 'membership_revoked'}))
        with patch.object(self.client, 'rpc', side_effect=[{'revoked': True}, BotSeshError('invalid_token', 401)]) as rpc:
            self.client.disconnect('dm')
        self.assertEqual(rpc.call_args_list[0].args[1:], ('hermes_disconnect', {'confirm': True}))
        self.assertTrue(all(room['state'] == 'revoked' for room in self.store.read()['rooms'].values()))

    def test_v05_migration_fences_without_credentials_and_preserves_pending_metadata(self):
        pending_id = str(uuid.uuid4())
        legacy = {**self.binding, 'oauth_id': 'old-profile-grant',
                  'oauth_posts': {pending_id: {'exact': {'text': 'Exact café\r\n', 'client_id': pending_id}}},
                  'pending_completion': {'client_id': pending_id, 'number': None},
                  'processing_receipts': {'turn': {'receipt_id': 'old-receipt', 'state': 'processing'}}}
        legacy.pop('auth_version')
        self.store.update(lambda d: d['rooms'].update(one=legacy))
        before = copy.deepcopy(self.store.read())
        sentinel = self.home / 'auth.json'
        sentinel.write_bytes(b'opaque core fixture; must not be read')
        real_open = io.open
        def guarded_open(path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and Path(path) == sentinel:
                self.fail('migration accessed credential store')
            return real_open(path, *args, **kwargs)
        with patch('io.open', side_effect=guarded_open), patch('builtins.open', side_effect=guarded_open), patch(PUBLIC_REQUEST) as request, \
             patch('hermes_cli.auth._load_auth_store', side_effect=AssertionError('migration read credentials')):
            with self.assertRaisesRegex(StorageError, 'hermes auth add botsesh:rooms'):
                self.store.room('one')
            with self.assertRaisesRegex(StorageError, 'hermes botsesh connect'):
                Client(self.store).enrolled('one', 'check')
            with self.assertRaises(BotSeshError) as error:
                self.client.call(legacy, 'check')
            self.assertEqual(error.exception.code, 'oauth_upgrade_sign_in_required')
            request.assert_not_called()
        self.assertEqual(self.store.read(), before)
        self.assertEqual(sentinel.read_bytes(), b'opaque core fixture; must not be read')

    def test_changed_or_legacy_participant_archives_journals_never_replays_them(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                old = {**self.binding, 'oauth_posts': {'original': {'exact': {'text': 'pending'}}}, 'pending_completion': {'client_id': 'original'}}
                if legacy:
                    old.pop('auth_version')
                self.store.update(lambda d: d.update(rooms={'one': old}, previous_oauth_bindings={}))
                room = {'room_id': 'one', 'name': 'One', 'participant_id': 'bot' if legacy else 'new-bot', 'display_name': 'Scout', 'gateway_profile': 'default'}
                with patch.object(self.client, 'rpc', side_effect=[{'profile': 'default'}, {'rooms': [room]},
                     {'rooms': [room]}, {'rooms': [room]}]):
                    result = self.client.connect(name='Scout', profile='default')
                self.assertNotIn('oauth_posts', result)
                self.assertNotIn('pending_completion', result)
                self.assertEqual(list(self.store.read()['previous_oauth_bindings'].values()), [old])
                self.assertEqual((result['oauth_id'], result['auth_version']), ('rooms', 2))

    def test_room_hint_selects_home_not_authority_and_all_approved_roots_attach(self):
        roots = [{'room_id': rid, 'name': rid, 'participant_id': 'bot-' + rid,
                  'display_name': 'Scout', 'gateway_profile': 'default'} for rid in ('two', 'three')]
        dm = {'room_id': 'dm', 'name': 'Private', 'participant_id': 'bot-dm', 'display_name': 'Scout',
              'gateway_profile': 'default', 'room_type': 'dm', 'source_room_id': 'two'}
        records = [*roots, dm]
        with patch.object(self.client, 'rpc', side_effect=[{'profile': 'default'}, {'rooms': records},
             {'rooms': records}, {'rooms': records}, {'rooms': records}]) as rpc:
            selected = self.client.connect(name='Scout', room_hint='three', profile='default')
        self.assertEqual(selected['room_id'], 'three')
        self.assertEqual({rid for rid, r in self.store.read()['rooms'].items() if r['active']}, {'two', 'three', 'dm'})
        self.assertEqual(self.store.room('dm')['oauth_root'], 'two')
        self.assertFalse(any(c.args[1] == 'hermes_name' for c in rpc.call_args_list))
        before = self.store.read()
        with patch.object(self.client, 'rpc', side_effect=[{'profile': 'default'}, {'rooms': records}]) as rpc:
            with self.assertRaises(BotSeshError) as error:
                self.client.connect(room_hint='unapproved', profile='default')
        self.assertEqual(error.exception.code, 'room_not_approved_run_hermes_auth_add_botsesh_rooms')
        self.assertEqual(self.store.read(), before)
        self.assertEqual([c.args[1] for c in rpc.call_args_list], ['hermes_initialize', 'list_rooms'])

    def test_discovery_exact_memberships_dms_only_active_roots_and_tombstones(self):
        self.store.update(lambda d: d['rooms'].update(detached={**self.binding, 'room_id': 'detached', 'active': False}))
        root = {'room_id': 'one', 'participant_id': 'bot', 'name': 'One', 'display_name': 'Scout'}
        def dm(rid, source, participant=None):
            return {'room_id': rid, 'source_room_id': source, 'participant_id': participant or 'bot-' + rid,
                    'room_type': 'dm', 'name': rid, 'display_name': 'Scout'}
        self.store.update(lambda d: d.update(forgotten_rooms={'forgotten': True}))
        records = [root, dm('approved', 'one'), dm('forgotten', 'one'), dm('inactive', 'detached'),
                   dm('foreign', 'not-enabled'), {'room_id': 'new-root', 'participant_id': 'new', 'name': 'New', 'display_name': 'Scout'}]
        with patch.object(self.client, 'rpc', return_value={'rooms': records}) as rpc:
            self.assertEqual(self.client.discover(self.binding), {'rooms': records})
        rpc.assert_called_once_with(self.binding, 'list_rooms')
        saved = self.store.read()
        self.assertEqual(set(saved['rooms']), {'one', 'detached', 'approved'})
        self.assertEqual(saved['forgotten_rooms'], {'forgotten': True})
        self.assertEqual((saved['rooms']['approved']['oauth_root'], saved['rooms']['approved']['auth_version']), ('one', 2))
        self.store.update(lambda d: d['rooms']['approved'].update(oauth_posts={'old': {'exact': {'text': 'pending'}}}))
        with patch.object(self.client, 'rpc', return_value={'rooms': [root, dm('approved', 'one', 'replacement')]}):
            self.client.discover(self.binding)
        old = self.store.read()['rooms']['approved']
        self.assertFalse(old['active'])
        self.assertEqual(old['state'], 'identity_changed_run_connect')
        self.assertEqual(old['participant_id'], 'bot-approved')
        self.assertEqual(old['oauth_posts'], {'old': {'exact': {'text': 'pending'}}})


class ProviderContractTests(unittest.TestCase):
    def test_arbitrary_paths_methods_and_origins_denied_before_public_helper(self):
        cases = [('https://evil.test', 'POST', '/mcp'), ('http://127.0.0.1:43210', 'POST', '/mcp'),
                 ('https://botsesh.com', 'GET', '/mcp'), ('https://botsesh.com', 'POST', '/oauth/revoke'),
                 ('https://botsesh.com', 'POST', '/mcp?next=evil'), ('https://botsesh.com#evil', 'POST', '/mcp')]
        with patch(PUBLIC_REQUEST) as request:
            for origin, method, path in cases:
                with self.subTest(origin=origin, method=method, path=path), self.assertRaises(BotSeshError):
                    http_transport.provider_request(origin, method, path)
            request.assert_not_called()

    def test_core_auth_and_network_errors_translate_to_safe_codes(self):
        import httpx
        from hermes_cli.auth_constants import AuthError
        cases = [(ProviderNotSignedIn('private-detail'), 'sign_in_required_run_hermes_auth_add_botsesh_rooms', 401),
                 (AuthError('private-detail', provider='botsesh:rooms', code='invalid_grant'), 'invalid_grant', None),
                 (AuthError('private-detail', provider='botsesh:rooms', code='private detail!'), 'oauth_request_failed', None),
                 (httpx.ConnectError('private-detail'), 'connection_failed_retry_same_intent', None)]
        for failure, code, status in cases:
            with self.subTest(code=code), patch(PUBLIC_REQUEST, side_effect=failure):
                with self.assertRaises(BotSeshError) as error:
                    http_transport.provider_request('https://botsesh.com', 'POST', '/mcp')
                self.assertEqual((error.exception.code, error.exception.status), (code, status))
                self.assertNotIn('private-detail', str(error.exception))

    def test_declared_manifest_core_owns_signin_fixed_client_and_callback(self):
        from hermes_yaml import safe_load
        manifest = safe_load((ROOT / 'plugin.yaml').read_text())
        self.assertEqual(str(manifest['version']), '0.5.1-catalog.1')
        self.assertEqual(manifest['oauth'][0]['id'], 'rooms')
        self.assertEqual(manifest['oauth'][0]['client_id_env'], 'BOTSESH_OAUTH_CLIENT_ID')
        self.assertEqual(manifest['oauth'][0]['hosts'], ['botsesh.com'])
        self.assertEqual(manifest['oauth'][0]['scopes'], ['botsesh:rooms'])
        self.assertEqual(manifest['oauth'][0]['authorize_params'], {'resource': 'https://botsesh.com/mcp', 'hermes_declared': 'v1'})
        self.assertIn('hermes auth add botsesh:rooms', oauth.sign_in_instructions())
        self.assertEqual(manifest['oauth'][0]['client_id_default'], oauth.CLIENT_ID)
        self.assertNotIn('export ', oauth.sign_in_instructions())
        for deleted in ('login', '_rows', '_save', '_access', '_credential', '_make_loopback_callback_handler'):
            self.assertFalse(hasattr(oauth, deleted), deleted)


if __name__ == '__main__':
    unittest.main()
