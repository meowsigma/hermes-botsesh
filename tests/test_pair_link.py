"""0.10.1 (t_c47adfa3): pasted invite links and the remote-safe relay wait."""
import importlib
import threading
import types
import unittest
from unittest import mock

from test_plugin import ROOT  # loads the plugin as hermes_botsesh_test, like the other suites

PKG = 'hermes_botsesh_test'
CODE = 'A' * 43


class PairLinkTests(unittest.TestCase):
    def test_parse_accepts_only_botsesh_or_loopback_dev_links(self):
        oauth = importlib.import_module(PKG + '.oauth')
        self.assertEqual(oauth.parse_pair_link(f'https://botsesh.com/a/{CODE}'), ('https://botsesh.com', 'bsp_' + CODE))
        self.assertEqual(oauth.parse_pair_link(f"  'http://127.0.0.1:47331/a/{CODE}/' "), ('http://127.0.0.1:47331', 'bsp_' + CODE))
        for bad in (f'https://evil.example/a/{CODE}', f'https://botsesh.com/a/{CODE[:-1]}', f'https://botsesh.com/b/{CODE}',
                    f'https://botsesh.com/a/{CODE}?x=1', ''):
            with self.assertRaises(Exception, msg=bad):
                oauth.parse_pair_link(bad)

    def test_join_tool_starts_one_background_connect_for_an_invite_link(self):
        tools = importlib.import_module(PKG + '.tools')
        cli = importlib.import_module(PKG + '.cli')
        identity = importlib.import_module(PKG + '.identity')
        ctx = types.SimpleNamespace(session_surface=lambda: 'telegram', get_config=lambda *a: '')
        with mock.patch.object(cli, 'spawn_connect', return_value='started') as spawn, \
                mock.patch.object(identity, 'current_profile', return_value='default'):
            result = tools.join({'invitation_url': f'https://botsesh.com/a/{CODE}'}, ctx)
        spawn.assert_called_once_with('bsp_' + CODE, 'https://botsesh.com', 'default')
        self.assertEqual(result['state'], 'waiting_for_allow')
        self.assertEqual(result['tell_your_person'], 'Press Allow in BotSesh.')
        room = types.SimpleNamespace(session_surface=lambda: 'botsesh', get_config=lambda *a: '')
        with mock.patch.object(cli, 'spawn_connect') as spawn, \
                mock.patch('gateway.session_context.get_session_env', return_value='room', create=True):
            with self.assertRaises(Exception):
                tools.join({'invitation_url': f'https://botsesh.com/a/{CODE}'}, room)
            spawn.assert_not_called()

    def test_relay_code_wins_and_denial_stops(self):
        oauth = importlib.import_module(PKG + '.oauth')
        server = mock.MagicMock()
        replies = iter([(200, {'status': 'waiting'}), (200, {'status': 'approved', 'code': 'c' * 43})])
        with mock.patch.object(oauth, '_pair_post', side_effect=lambda *a, **k: next(replies)):
            callback, source = oauth._wait_callback_or_relay(server, {'code': None, 'error': None}, origin='https://botsesh.com',
                                                             pair_code='bsp_' + CODE, state='s' * 43, timeout=10, err=RuntimeError)
        self.assertEqual((callback['code'], source), ('c' * 43, 'relay'))
        with mock.patch.object(oauth, '_pair_post', return_value=(200, {'status': 'denied'})):
            callback, _ = oauth._wait_callback_or_relay(server, {'code': None, 'error': None}, origin='https://botsesh.com',
                                                        pair_code='bsp_' + CODE, state='s' * 43, timeout=10, err=RuntimeError)
        self.assertEqual(callback['error'], 'access_denied')

    def test_loopback_callback_still_wins_when_it_arrives_first(self):
        oauth = importlib.import_module(PKG + '.oauth')
        result = {'code': 'loop' * 11, 'state': 's', 'error': None}
        stop = threading.Event()
        def slow(*a, **k):
            stop.wait(2)
            return 200, {'status': 'waiting'}
        with mock.patch.object(oauth, '_pair_post', side_effect=slow):
            callback, source = oauth._wait_callback_or_relay(mock.MagicMock(), result, origin='https://botsesh.com',
                                                             pair_code='bsp_' + CODE, state='s', timeout=10, err=RuntimeError)
        stop.set()
        self.assertEqual((callback['code'], source), ('loop' * 11, 'loopback'))


if __name__ == '__main__':
    unittest.main()
