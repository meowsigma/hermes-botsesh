"""Identity/profile boundary checks. No production account, model or Node."""
import argparse
import asyncio
import contextlib
import io
import json
import sys
import types
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch
import test_oauth as original
from botsesh_oauth_test import identity, cli


class ProfileSelection(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.config = {'platforms': {'botsesh': {'extra': {}}}}
        self.stack.enter_context(patch('hermes_cli.config.load_config', return_value=self.config))
        self.stack.enter_context(patch.object(identity, 'profile_names', return_value=['default', 'goonbot', 'evilgoonbot']))
        self.stack.enter_context(patch.object(identity, 'current_profile', return_value='default'))
        self.stack.enter_context(patch('hermes_cli.profiles.read_profile_meta', return_value={}))
        self.stack.enter_context(patch.dict(sys.modules, {'hermes_cli.main': types.SimpleNamespace()}))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_no_tty_never_prompts_uses_saved_then_default(self):
        with patch('sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=AssertionError('prompted')):
            self.assertEqual(identity.choose_profile(interactive=True), 'default')
            self.config['platforms']['botsesh']['extra']['profile'] = 'evilgoonbot'
            self.assertEqual(identity.choose_profile(interactive=True), 'evilgoonbot')
            self.assertEqual(identity.choose_profile('goonbot', interactive=True), 'goonbot')

    def test_interactive_enter_accepts_default_and_second_connect_does_not_ask(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='') as ask:
            chosen = identity.choose_profile(interactive=True)
            self.config['platforms']['botsesh']['extra']['profile'] = chosen
            self.assertEqual(identity.choose_profile(interactive=True), 'default')
        self.assertEqual(chosen, 'default')
        ask.assert_called_once()

    def test_profile_list_choice_and_explicit_native_preparse_wins_over_saved(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='3'):
            self.assertEqual(identity.choose_profile(interactive=True), 'evilgoonbot')
        self.config['platforms']['botsesh']['extra']['profile'] = 'goonbot'
        with patch.dict(sys.modules, {'hermes_cli.main': types.SimpleNamespace(explicit_cli_profile=lambda: 'evilgoonbot')}):
            self.assertEqual(identity.choose_profile(), 'evilgoonbot')
        for value in ('../escape', 'missing', '', False, [], ['goonbot']):
            with self.assertRaises(original.BotSeshError):
                identity.choose_profile(value)

    def test_configured_display_name_and_explicit_override(self):
        self.assertEqual(identity.configured_name('goonbot'), 'goonbot')
        self.config['platforms']['botsesh']['extra']['display_name'] = 'Garden keeper'
        self.assertEqual(identity.configured_name('goonbot'), 'Garden keeper')
        self.assertEqual(identity.configured_name('goonbot', 'Scout'), 'Scout')

    def test_profile_names_that_look_like_yaml_scalars_stay_strings(self):
        with patch('hermes_cli.config.set_config_value') as save:
            identity.save_profile('null')
        self.assertEqual(save.call_args.args[0], 'platforms.botsesh.extra')
        self.assertEqual(json.loads(save.call_args.args[1])['profile'], 'null')

    def test_default_profile_uses_its_authored_persona_label(self):
        with patch('hermes_cli.profiles.read_profile_meta', return_value={'display_name': 'goonbot'}):
            self.assertEqual(identity.configured_name('default'), 'goonbot')


class SeatLifecycle(unittest.TestCase):
    setUp = original.OAuthTests.setUp
    tearDown = original.OAuthTests.tearDown

    def test_full_connect_roundtrips_are_serialized(self):
        live, peak = 0, 0
        def connect(**kwargs):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            time.sleep(.02)
            live -= 1
            return {'ok': True}
        with patch.object(self.client, '_connect', side_effect=connect):
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(lambda _: self.client.connect(), range(2))), [{'ok': True}]*2)
        self.assertEqual(peak, 1)

    def test_wrong_native_profile_adapter_refuses_before_listening(self):
        from botsesh_oauth_test.adapter import BotSeshAdapter
        adapter = object.__new__(BotSeshAdapter)
        adapter.config = types.SimpleNamespace(extra={'profile': 'evilgoonbot'})
        adapter._running = False
        with patch.object(identity, 'current_profile', return_value='default'):
            self.assertFalse(asyncio.run(adapter.connect()))
        self.assertFalse(adapter._running)

    def test_reconnect_three_preserves_same_seat_journal_and_only_new_grant(self):
        self.store.update(lambda d: d['rooms']['one'].update(event_cursor={'after': 17, 'since': 'cursor'}, oauth_posts={'exact': {'complete': True}}))
        room = dict(room_id='one', name='One', participant_id='bot', display_name='goonbot', gateway_profile='goonbot')
        for index in range(3):
            fields = {'access_token': str(index)*43, 'refresh_token': str(index+3)*43, 'expires_in': 300}
            with patch.object(original.oauth, 'login', return_value=fields), patch.object(self.client, 'rpc', side_effect=[
                    {'rooms': [room]}, {'rooms': [room]}, {'rooms': [room]}]):
                saved = self.client.connect(name='goonbot', profile='goonbot')
            self.assertEqual(saved['participant_id'], 'bot')
            self.assertEqual(len(original.oauth._rows()), 1)
            self.assertEqual(original.oauth._rows()[0]['access_token'], fields['access_token'])
        self.assertEqual(self.store.room('one')['event_cursor'], {'after': 17, 'since': 'cursor'})
        self.assertEqual(self.store.room('one')['oauth_posts'], {'exact': {'complete': True}})

    def test_mismatched_gateway_profile_denies_before_name_or_local_overwrite(self):
        room = dict(room_id='one', name='One', participant_id='bot', display_name='other', gateway_profile='foreign')
        fields = {'access_token': 'B'*43, 'refresh_token': 'S'*43}
        with patch.object(original.oauth, 'login', return_value=fields), patch.object(original.oauth, '_http', return_value={}) as revoke, patch.object(self.client, 'rpc', return_value={'rooms': [room]}) as rpc:
            with self.assertRaises(original.BotSeshError) as error:
                self.client.connect(profile='goonbot')
        self.assertEqual(error.exception.code, 'gateway_identity_not_confirmed_update_server')
        self.assertEqual(rpc.call_count, 1)
        revoke.assert_called_once()
        self.assertEqual(original.oauth._rows()[0]['id'], 'owned')

    def test_gateway_install_id_stable_across_two_native_profile_scopes(self):
        from hermes_cli.config import get_hermes_home
        target = self.home / 'profiles' / 'evilgoonbot'
        target.mkdir(parents=True)
        with patch.object(identity, 'current_profile', return_value='default'), patch('hermes_cli.profiles.get_profile_dir', return_value=target):
            first = identity.gateway_install_id()
            with identity.profile_scope('evilgoonbot'):
                self.assertEqual(get_hermes_home(), target)
                self.assertEqual(identity.gateway_install_id(), first)
                self.assertEqual(original.oauth._auth_path(), target / 'auth.json')
            self.assertEqual(get_hermes_home(), self.home)
        self.assertEqual((self.home / 'plugin-data/botsesh-identity/rooms.json').stat().st_mode & 0o777, 0o600)

    def test_seats_prune_no_tty_requires_yes_and_readback(self):
        binding = self.binding
        seats = [{'membership_id': 'ghost', 'room_id': 'one', 'profile': 'goonbot', 'display_name': 'old', 'ghost': True}]
        api, client = Mock(), Mock(store=self.store)
        args = argparse.Namespace(botsesh_action='seats', room=None, prune=True, yes=False)
        api.rpc.return_value = {'seats': seats}
        with contextlib.redirect_stdout(io.StringIO()), patch('sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=AssertionError('prompted')):
            with self.assertRaises(original.BotSeshError):
                identity.handle_identity(args, client, api)
        self.assertEqual(api.rpc.call_count, 1)
        api.reset_mock()
        args.yes = True
        api.rpc.side_effect = [{'seats': seats}, {'pruned': ['ghost']}, {'seats': []}]
        with contextlib.redirect_stdout(io.StringIO()):
            identity.handle_identity(args, client, api)
        self.assertEqual(api.rpc.call_args_list[1].args[2], {'room_id': 'one', 'prune': ['ghost'], 'confirm': True})
        self.assertEqual(api.rpc.call_count, 3)


if __name__ == '__main__':
    unittest.main()
