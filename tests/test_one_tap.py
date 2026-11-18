"""0.8.2 one-tap connect (t_97b4e776): no browser, in-room handoff, hot start, graceful idle fallback."""
import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from test_plugin import ROOT  # loads the plugin as hermes_botsesh_test, like the other suites
hot_start = importlib.import_module('hermes_botsesh_test.hot_start')
PKG = 'hermes_botsesh_test'


class FakeRunner:
    def __init__(self, adapters=None, failed=None):
        self._running = True
        self.adapters = adapters or {}
        self._failed_platforms = failed or {}
        self.config = types.SimpleNamespace(platforms={})
        self.watcher = 0

    def _reconnect_queue_entry(self, platform, adapter, config, *, attempts, delay):
        return {'config': config, 'attempts': attempts, 'delay': delay, 'predecessor': adapter}

    def _ensure_reconnect_watcher_running(self):
        self.watcher += 1


class HotStartTests(unittest.TestCase):
    def setUp(self):
        # A plugin platform's enum member exists only once the registry knows it; stand in for it.
        self.platform = ('platform', 'botsesh')
        patcher = mock.patch('gateway.config.Platform', side_effect=lambda value: ('platform', value))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = types.SimpleNamespace(enabled=True, extra={'group_allowed_chats': ['R']})
        self.loaded = types.SimpleNamespace(platforms={self.platform: self.cfg})

    def queue(self, runner, scoped='/home/x/.hermes', active='/home/x/.hermes', loaded=None):
        with mock.patch('hermes_constants.get_hermes_home', return_value=Path(active)), \
             mock.patch('gateway.config.load_gateway_config', return_value=loaded or self.loaded):
            return hot_start._queue_primary(runner, scoped)

    def test_configured_missing_channel_is_queued_on_the_gateways_own_watcher(self):
        runner = FakeRunner()
        self.assertTrue(self.queue(runner, scoped=str(Path('/home/x/.hermes'))))
        self.assertIs(runner.config.platforms[self.platform], self.cfg)
        self.assertEqual(runner._failed_platforms[self.platform], {'config': self.cfg, 'attempts': 0, 'delay': 0.0, 'predecessor': None})
        self.assertEqual(runner.watcher, 1)

    def test_live_queued_disabled_or_secondary_profiles_are_left_alone(self):
        self.assertFalse(self.queue(FakeRunner(adapters={self.platform: object()})))
        self.assertFalse(self.queue(FakeRunner(failed={self.platform: {}})))
        off = types.SimpleNamespace(platforms={self.platform: types.SimpleNamespace(enabled=False)})
        self.assertFalse(self.queue(FakeRunner(), loaded=off))
        self.assertFalse(self.queue(FakeRunner(), loaded=types.SimpleNamespace(platforms={})))
        runner = FakeRunner()
        self.assertFalse(self.queue(runner, scoped='/home/x/.hermes/profiles/work'))  # rescan-profiles owns it
        self.assertEqual(runner._failed_platforms, {})

    def test_outside_a_running_gateway_registration_has_no_side_effect(self):
        with mock.patch.dict(os.environ, {'_HERMES_GATEWAY': ''}):
            self.assertFalse(hot_start.schedule_in_gateway())
        run = sys.modules.pop('gateway.run', None)
        try:
            with mock.patch.dict(os.environ, {'_HERMES_GATEWAY': '1'}):
                self.assertFalse(hot_start.schedule_in_gateway(), 'never imports the gateway into a CLI process')
                self.assertNotIn('gateway.run', sys.modules)
        finally:
            if run is not None:
                sys.modules['gateway.run'] = run

    def test_fallback_waits_for_idle_detached_without_shell_backgrounding(self):
        with mock.patch('shutil.which', return_value='/usr/bin/hermes'), mock.patch('subprocess.Popen') as popen:
            self.assertTrue(hot_start.graceful_restart_when_idle('default'))
        argv, kwargs = popen.call_args.args[0], popen.call_args.kwargs
        self.assertEqual(argv[:2], ['sh', '-c'])
        self.assertIn('until hermes -p "$1" gateway restart --detach </dev/null', argv[2])
        self.assertNotIn('--yes', argv[2])
        self.assertEqual(argv[-1], 'default')
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'], __import__('subprocess').DEVNULL, 'no terminal: Hermes refuses while work is live')


class HandoffTests(unittest.TestCase):
    def test_handoff_posts_only_the_authorize_url_with_the_api_header(self):
        # t_c47adfa3 (0.10.1): the handoff also asks for the remote-safe relay and returns what
        # BotSesh confirmed (carded / relay / ended) instead of a bare bool.
        oauth = importlib.import_module(PKG + '.oauth')
        response = types.SimpleNamespace(status_code=201, json=lambda: {'relay': True})
        client = mock.MagicMock()
        client.__enter__.return_value.post.return_value = response
        with mock.patch('httpx.Client', return_value=client):
            result = oauth._room_handoff('http://127.0.0.1:3897', 'R' * 32, 'bsp_' + 'A' * 43, 'http://127.0.0.1:3897/oauth/authorize?x=1', 'Fairy')
        self.assertTrue(result['carded'])
        self.assertTrue(result['relay'])
        call = client.__enter__.return_value.post.call_args
        self.assertEqual(call.args[0], 'http://127.0.0.1:3897/api/agent/hermes-handoff')
        self.assertEqual(set(call.kwargs['json']), {'pairCode', 'roomId', 'authorizeUrl', 'displayName', 'relay'})
        self.assertIs(call.kwargs['json']['relay'], True)
        self.assertEqual(call.kwargs['headers']['X-Board-Request'], '1')
        self.assertNotIn('Authorization', call.kwargs['headers'])
        response.status_code = 410
        with mock.patch('httpx.Client', return_value=client):
            result = oauth._room_handoff('http://127.0.0.1:3897', 'R' * 32, 'bsp_' + 'A' * 43, 'u', 'Fairy')
        self.assertFalse(result['carded'])
        self.assertTrue(result['ended'])

    def test_no_module_opens_a_browser(self):
        for path in ROOT.glob('*.py'):
            self.assertNotIn('webbrowser', path.read_text(encoding='utf-8'), path.name)


if __name__ == '__main__':
    unittest.main()
