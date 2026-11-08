"""Name behavior at the plugin/credential boundary; no model or real account."""
import argparse
import contextlib
import io
import unittest
from unittest.mock import patch
import test_oauth as original

oauth = original.oauth
from botsesh_oauth_test import cli


class NameTests(unittest.TestCase):
    setUp = original.OAuthTests.setUp
    tearDown = original.OAuthTests.tearDown

    def test_cli_accepts_exact_name_with_spaces(self):
        parser = argparse.ArgumentParser()
        cli.setup(parser)
        args = parser.parse_args(['connect', '--name', 'Hermes 2', '--no-start'])
        self.assertEqual(args.name, 'Hermes 2')
        self.assertTrue(args.no_start)

    def test_explicit_profile_then_host_fallback_and_validation(self):
        with patch('botsesh_oauth_test.identity.current_profile', return_value='garden-profile'):
            self.assertEqual(oauth.preferred_name(), 'garden-profile')
            self.assertEqual(oauth.preferred_name('Scout'), 'Scout')
        for profile in ('custom', 'default', None):
            with self.subTest(profile=profile), patch('botsesh_oauth_test.identity.current_profile', return_value=profile):
                self.assertEqual(oauth.preferred_name(), 'default')
        # Explicit --name is authored input, even if it matches a profile sentinel.
        self.assertEqual(oauth.preferred_name('default'), 'default')
        for value in ('', 'a'*41, '@Scout', 'Bad\u200bname', 'line\nfeed', '🪴'*21):
            with self.assertRaises(original.BotSeshError):
                oauth.preferred_name(value)

    def test_connect_uses_scoped_server_assignment_and_readback(self):
        room = dict(room_id='two', name='Garden', participant_id='second', display_name='Hermes 2', gateway_profile='default')
        assigned = dict(room, display_name='Scout')
        with patch.object(self.client, 'rpc', side_effect=[{'profile': 'default'},
                {'rooms':[room]}, {'participant':{'id':'second','name':'Scout'}}, {'rooms':[assigned]}]) as rpc:
            result = self.client.connect(name='Scout', profile='default')
        self.assertEqual(result['display_name'], 'Scout')
        self.assertEqual(self.store.room('two')['display_name'], 'Scout')
        self.assertEqual(rpc.call_args_list[2].args[1:], ('hermes_name', {'room_id':'two', 'display_name':'Scout', 'assign':True}))
        self.assertEqual(rpc.call_args_list[2].kwargs, {})
        self.assertEqual((result['auth_version'], result['oauth_id']), (2, 'rooms'))
        self.assertFalse(self.store.read()['rooms']['one']['active'])

    def test_name_read_and_rename_verify_canonical_identity_before_storage(self):
        with patch.object(self.client,'rpc',return_value={'participant':{'id':'bot','name':'Sage'}}) as rpc:
            self.assertEqual(self.client.call(self.binding,'name',display_name='Sage'), {'display_name':'Sage'})
            self.assertEqual(rpc.call_args_list[0].args[2], {'room_id':'one','display_name':'Sage'})
            self.assertNotIn('participant_id',rpc.call_args_list[0].args[2])
        self.assertEqual(self.store.room('one')['display_name'], 'Sage')
        with patch.object(self.client,'rpc',side_effect=[{'participant':{'id':'bot','name':'Changed'}}, {'participant':{'id':'foreign','name':'Changed'}}]):
            with self.assertRaises(original.BotSeshError):self.client.call(self.binding,'name',display_name='Changed')
        self.assertEqual(self.store.room('one')['display_name'], 'Sage')

    def test_unconfirmed_connect_never_persists_unverified_identity(self):
        before = self.store.read()
        with patch.object(self.client,'rpc',side_effect=[{'profile': 'default'},
                {'rooms':[dict(room_id='two',name='Garden',participant_id='second',display_name='Hermes',gateway_profile='default')]},
                {'participant':{'id':'second','name':'Scout'}}, {'rooms':[]}]):
            with self.assertRaises(original.BotSeshError) as error:self.client.connect(name='Scout', profile='default')
            self.assertEqual(error.exception.code, 'room_identity_changed_retry_connect')
        self.assertNotIn('two',self.store.read()['rooms'])
        self.assertEqual(self.store.read(), before)


if __name__ == '__main__':
    unittest.main()
