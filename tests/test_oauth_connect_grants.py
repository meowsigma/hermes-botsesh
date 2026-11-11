"""Grant-custody negatives and historical single-room response compatibility.

These boundary tests use RPC/login doubles; native HTTP evidence is separately
provided by test_oauth_connect.py. No server or model calls occur here.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch
import test_oauth as original

oauth = original.oauth


class GrantBindingTests(unittest.TestCase):
    setUp = original.OAuthTests.setUp
    tearDown = original.OAuthTests.tearDown

    def room(self, rid='two', **changes):
        return {'room_id': rid, 'name': 'Fixture', 'participant_id': 'seat-' + rid,
                'display_name': 'Scout', 'gateway_profile': 'default', **changes}

    def connect(self, rooms, verified=None, hint=None):
        self.before = self.store.read()
        self.rows_before = oauth._rows()
        fields = {'access_token': 'B'*43, 'refresh_token': 'S'*43, 'expires_in': 300}
        def rpc(binding, name, args=None, **kwargs):
            self.assertEqual(name, 'list_rooms')
            if self.calls:
                result = rooms if verified is None else verified
            else:
                result = rooms
            self.calls += 1
            return {'rooms': deepcopy(result)}
        self.calls = 0
        with patch.object(oauth, 'login', return_value=fields), patch.object(oauth, '_http', return_value={}) as revoke, patch.object(self.client, 'rpc', side_effect=rpc):
            self.revoke = revoke
            return self.client.connect(room_hint=hint, name='Scout', profile='default')

    def assert_refused_without_persistence(self, code, rooms, verified=None, hint=None):
        with self.assertRaises(original.BotSeshError) as error:
            self.connect(rooms, verified, hint)
        self.assertEqual(error.exception.code, code)
        self.revoke.assert_called_once_with('https://botsesh.com', '/oauth/revoke',
            form={'client_id': oauth.CLIENT_ID, 'token': 'S'*43})
        self.assertEqual(self.store.read(), self.before)
        self.assertEqual(oauth._rows(), self.rows_before)

    def test_legacy_single_room_response_without_hint_unchanged(self):
        result = self.connect([self.room()])  # Historical response has no room_type.
        self.assertEqual(result['room_id'], 'two')
        self.assertEqual(result['participant_id'], 'seat-two')
        self.revoke.assert_not_called()
        self.assertTrue(self.client._credential(result))

    def test_legacy_single_room_cannot_ignore_a_different_requested_room(self):
        self.assert_refused_without_persistence('requested_room_not_authorized', [self.room()], hint='absent')

    def test_secondary_room_gateway_profile_must_match_too(self):
        self.assert_refused_without_persistence('gateway_identity_not_confirmed_update_server',
            [self.room(), self.room('dm', room_type='dm', gateway_profile='foreign')], hint='two')

    def test_secondary_room_identity_change_on_readback_revokes_atomically(self):
        rooms = [self.room(), self.room('dm', room_type='dm')]
        self.assert_refused_without_persistence('room_identity_changed_retry_connect', rooms,
            [rooms[0], {**rooms[1], 'participant_id': 'foreign-seat'}], 'two')

    def test_secondary_saved_seat_conflict_cannot_be_overwritten(self):
        rooms = [self.room(), self.room('dm', room_type='dm')]
        self.store.update(lambda d: d['rooms'].update(dm={**self.binding, 'room_id': 'dm'}))
        self.assert_refused_without_persistence('room_identity_changed_disconnect_first', rooms, hint='two')

    def test_inactive_different_participant_does_not_inherit_send_journals(self):
        self.store.update(lambda d: d['rooms'].update(two={**self.binding, 'room_id': 'two',
            'active': False, 'oauth_posts': {'old-seat': {'complete': True}}}))
        result = self.connect([self.room()], hint='two')
        self.assertEqual(result['participant_id'], 'seat-two')
        self.assertNotIn('oauth_posts', result)

    def test_readback_cannot_substitute_an_unknown_room_with_missing_identity(self):
        rooms = [self.room(), self.room('dm', room_type='dm')]
        unknown = self.room('unknown')
        unknown.pop('participant_id')
        self.assert_refused_without_persistence('room_identity_changed_retry_connect', rooms,
            [rooms[0], unknown], 'two')

    def test_reconnect_retains_exact_participant_journals_for_every_room(self):
        rooms = [self.room(), self.room('dm', room_type='dm')]
        first = self.connect(rooms, hint='two')
        self.store.update(lambda d: [d['rooms'][rid].update(after=7, oauth_posts={'old': {'complete': True}}) for rid in ('two', 'dm')])
        second = self.connect(rooms, hint='two')
        self.assertNotEqual(second['oauth_id'], first['oauth_id'])
        for rid in ('two', 'dm'):
            self.assertEqual(self.store.room(rid)['after'], 7)
            self.assertEqual(self.store.room(rid)['oauth_posts'], {'old': {'complete': True}})
        self.assertNotIn(first['oauth_id'], {r['id'] for r in oauth._rows()})


if __name__ == '__main__':
    unittest.main()
