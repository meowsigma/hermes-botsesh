"""Merged profile/room/operations seams; local doubles, no service restart."""
import argparse

import os
import unittest
from unittest.mock import Mock, patch

import test_oauth as original
from botsesh_oauth_test import cli, ops_restart, room_lifecycle


class Integration050(unittest.TestCase):
    setUp = original.OAuthTests.setUp
    tearDown = original.OAuthTests.tearDown

    def test_reconnect_keeps_profile_paste_journal_and_restores_explicit_room(self):
        journal = {'delivered': {'complete': True}}
        self.store.update(lambda d: (d['rooms']['one'].update(oauth_posts=journal),
                                     d.setdefault('forgotten_rooms', {}).update(one=True, other=True)))
        room = dict(room_id='one', name='One', participant_id='bot', display_name='Scout',
                    gateway_profile='goonbot', password_required=False)
        fields = dict(access_token='B'*43, refresh_token='S'*43, expires_in=300)
        with patch.object(original.oauth, 'login', return_value=fields) as login, \
             patch.object(self.client, 'rpc', side_effect=[{'rooms': [room]},
                 {'participant': {'id': 'bot', 'name': 'Scout'}}, {'rooms': [room]}]):
            binding = self.client.connect(name='Scout', profile='goonbot', paste_redirect=True)
        self.assertEqual(login.call_args.kwargs['timeout'], 300)
        self.assertTrue(login.call_args.kwargs['paste_redirect'])
        self.assertEqual(login.call_args.kwargs['profile'], 'goonbot')
        self.assertEqual(login.call_args.kwargs['previous']['participant_id'], 'bot')
        self.assertEqual(binding['participant_id'], 'bot')
        self.assertEqual(self.store.room('one')['oauth_posts'], journal)
        self.assertEqual(self.store.read()['forgotten_rooms'], {'other': True})
        access = room_lifecycle.private_store(self.store).read()['rooms']['one']
        self.assertEqual(access['join_url'], 'https://botsesh.com/b/one')
        self.assertIsNone(access['password'])
        self.assertFalse(access['password_required'])
        self.assertEqual([r['id'] for r in original.oauth._rows()], [binding['oauth_id']])

    def test_selected_restart_home_crosses_subprocess_boundary_without_launcher_pin(self):
        target = self.home / 'profiles' / 'night'
        target.mkdir(parents=True)
        with patch.dict(os.environ, {'HERMES_PROFILE': 'old', 'HERMES_PROFILE_NAME': 'old'}), \
             patch.object(ops_restart, 'profile_context', return_value=(target, 'night')), \
             patch.object(ops_restart.shutil, 'which', return_value='/fixture/hermes'), \
             patch.object(ops_restart.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            ops_restart.lifecycle_restart(timeout=12)
        self.assertEqual(run.call_args.args[0], ['/fixture/hermes', '-p', 'night', 'gateway', 'restart', '--detach'])
        self.assertEqual(run.call_args.kwargs['env']['HERMES_HOME'], str(target))
        self.assertNotIn('HERMES_PROFILE', run.call_args.kwargs['env'])
        self.assertNotIn('HERMES_PROFILE_NAME', run.call_args.kwargs['env'])
        self.assertEqual(run.call_args.kwargs['timeout'], 12)

    def test_unified_cli_retains_every_command_and_flags(self):
        parser = argparse.ArgumentParser(prog='hermes botsesh')
        cli.setup(parser)
        cases = {
            'connect': ['--profile', 'goonbot', '--no-browser', '--paste-redirect', '--set-home'],
            'disconnect': ['one'], 'rename': ['Scout', '--room', 'one', '--profile', 'goonbot'],
            'seats': ['--prune', '--yes', '--profile', 'goonbot'],
            'create-room': ['Planning'], 'rooms': [], 'room-info': ['one', '--show-password'],
            'invite': ['one'], 'invites': ['one'], 'revoke-invite': ['one', 'invitation-id'],
            'rotate-password': ['one'], 'forget': ['--dead'],
            'send': ['--room', 'one', 'exact text'], 'status': ['--json'],
            'restart-gateway': ['--when-idle'],
        }
        for command, flags in cases.items():
            with self.subTest(command=command):
                parsed = parser.parse_args([command, *flags])
                self.assertEqual(parsed.botsesh_action, command)
                self.assertIn(command, parser.format_help())
        self.assertEqual(parser.parse_args(['connect']).timeout, 300)
        self.assertIn('Command groups:', parser.format_help())
        self.assertIn('hermes -p PROFILE', parser.format_help())

    def test_dash_prefixed_room_id_is_preserved_with_native_argparse_escaping(self):
        # Room IDs are base64url and can begin '-'. Do not change their bytes
        # or treat an argparse syntax failure as an access/lifecycle failure.
        parser = argparse.ArgumentParser()
        cli.setup(parser)
        room = '-' + 'a' * 31
        for args in (['invite', '--', room], ['invites', '--', room],
                     ['room-info', '--show-password', '--', room],
                     ['rotate-password', '--', room], ['forget', '--', room]):
            self.assertEqual(parser.parse_args(args).room, room)
        self.assertEqual(parser.parse_args(['send', '--room=' + room, 'exact']).room, room)


if __name__ == '__main__':
    unittest.main()
