"""Merged profile/room/operations seams; local doubles, no service restart."""
import argparse
import contextlib
import io


import unittest
from unittest.mock import patch

import test_oauth as original
from botsesh_oauth_test import cli, room_lifecycle


class Integration050(unittest.TestCase):
    setUp = original.OAuthTests.setUp
    tearDown = original.OAuthTests.tearDown

    def test_reconnect_keeps_profile_journal_and_restores_explicit_room(self):
        journal = {'delivered': {'complete': True}}
        self.store.update(lambda d: (d['rooms']['one'].update(oauth_posts=journal),
                                     d.setdefault('forgotten_rooms', {}).update(one=True, other=True)))
        room = dict(room_id='one', name='One', participant_id='bot', display_name='Scout',
                    gateway_profile='goonbot', password_required=False)
        with patch.object(self.client, 'rpc', side_effect=[{'profile': 'goonbot'}, {'rooms': [room]}, {'rooms': [room]}]) as rpc:
            binding = self.client.connect(name='Scout', profile='goonbot')
        self.assertEqual([c.args[1] for c in rpc.call_args_list], ['hermes_initialize', 'list_rooms', 'list_rooms'])
        self.assertEqual(rpc.call_args_list[0].args[2]['gateway_profile'], 'goonbot')
        self.assertEqual(binding['participant_id'], 'bot')
        self.assertEqual(self.store.room('one')['oauth_posts'], journal)
        self.assertEqual(self.store.read()['forgotten_rooms'], {'other': True})
        access = room_lifecycle.private_store(self.store).read()['rooms']['one']
        self.assertEqual(access['join_url'], 'https://botsesh.com/b/one')
        self.assertIsNone(access['password'])
        self.assertFalse(access['password_required'])
        self.assertEqual((binding['oauth_id'], binding['auth_version']), ('rooms', 2))


    def test_unified_cli_retains_every_command_and_flags(self):
        parser = argparse.ArgumentParser(prog='hermes botsesh')
        cli.setup(parser)
        cases = {
            'connect': ['--profile', 'goonbot', '--set-home'],
            'disconnect': ['one'], 'rename': ['Scout', '--room', 'one', '--profile', 'goonbot'],
            'seats': ['--prune', '--yes', '--profile', 'goonbot'],
            'create-room': ['Planning'], 'rooms': [], 'room-info': ['one', '--show-password'],
            'invite': ['one'], 'invites': ['one'], 'revoke-invite': ['one', 'invitation-id'],
            'rotate-password': ['one'], 'forget': ['--dead'],
            'send': ['--room', 'one', 'exact text'], 'status': ['--json'],
        }
        for command, flags in cases.items():
            with self.subTest(command=command):
                parsed = parser.parse_args([command, *flags])
                self.assertEqual(parsed.botsesh_action, command)
                self.assertIn(command, parser.format_help())
        for removed in ('--no-browser', '--paste-redirect', '--timeout', '--dev-origin'):
            with self.subTest(removed=removed), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(['connect', removed])
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
