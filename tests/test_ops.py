"""HB050 offline OPS tests. Local HTTP fixtures/doubles, no inference/services."""
import argparse

import contextlib

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, HTTPServer
import io
import json
import os
from pathlib import Path
import select
import shutil

import subprocess
import tempfile
import threading
import time
import sys
import queue
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit, parse_qs

from test_plugin import Client, Store, BotSeshError, ROOT
from hermes_botsesh_test import cli, oauth, ops_status as status


class OpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)/'home'
        self.home.mkdir(mode=0o700)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'HERMES_HOME': str(self.home)}).start()
        self.store = Store(self.home/'plugin-data/botsesh')
        self.client = Client(self.store)
        self.cfg = {'platforms': {'botsesh': {'enabled': True, 'home_channel': {'chat_id': 'dead'},
                     'extra': {'group_allowed_chats': ['one', 'dead']}}}}
        for rid in ('one', 'dead', 'invalid'):
            binding = {'room_id': rid, 'name': rid, 'active': True, 'participant_id': 'bot', 'oauth_id': rid,
                       'origin': 'https://botsesh.com', 'transport': 'oauth', 'auth_version': 2,
                       'health': {'state': 'connected', 'last_check': time.time(), 'last_event': 10, 'last_reply': 20}}
            self.store.update(lambda d, r=rid, b=binding: d['rooms'].update({r: b}))
        self.runtime = {'running': True, 'channel': 'down', 'profile': 'fixture', 'pid': 123,
                        'instance': 'first', 'control_home': str(self.home)}
        def rpc(binding, name, args=None, **kw):
            if binding['room_id'] == 'invalid':
                raise BotSeshError('invalid_grant', 401)
            if name == 'list_rooms':
                return {'rooms': [] if binding['room_id'] == 'dead' else [{'room_id': 'one', 'participant_id': 'bot'}]}
            if name == 'hermes_inbox':
                return {'targets': [], 'has_more': False, 'backlog': 0}
            self.fail(name)
        patch.object(oauth.OAuthClient, 'rpc', side_effect=rpc).start()

    def test_signin_guidance_uses_core_auth_not_plugin_callback_flags(self):
        guide = oauth.sign_in_instructions()
        self.assertIn('hermes auth add botsesh:rooms', guide)
        self.assertIn('hermes botsesh connect', guide)
        self.assertIn('intended Hermes profile', guide)
        self.assertNotIn('export ', guide)
        self.assertNotIn('--paste-redirect', guide)
        self.assertNotIn('--dev-origin', guide)

    def test_connect_parser_rejects_plugin_owned_login_flags(self):
        parser = argparse.ArgumentParser(); cli.setup(parser)
        for flag in ('--no-browser', '--paste-redirect', '--timeout', '--dev-origin'):
            with self.subTest(flag=flag), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(['connect', flag])
        args = parser.parse_args(['connect', '--no-start'])
        self.assertTrue(args.no_start)
        self.assertFalse(hasattr(args, 'timeout'))

    def test_core_unsigned_request_is_actionable_without_login_side_effect(self):
        from hermes_cli.plugin_provider_requests import ProviderNotSignedIn
        from hermes_botsesh_test.http_transport import provider_request
        with patch('hermes_cli.plugin_provider_requests.credentialed_provider_request', side_effect=ProviderNotSignedIn('private-detail')), \
             patch('webbrowser.open') as browser:
            with self.assertRaises(BotSeshError) as error:
                provider_request('https://botsesh.com', 'POST', '/mcp')
        self.assertEqual((error.exception.code, error.exception.status), ('sign_in_required_run_hermes_auth_add_botsesh_rooms', 401))
        self.assertNotIn('private-detail', str(error.exception))
        browser.assert_not_called()

    def test_dead_room_home_and_channel_down_never_healthy(self):
        report = status.status_report(self.client, self.cfg, runtime=self.runtime)
        by_id = {r['room_id']: r for r in report['rooms']}
        self.assertEqual(by_id['one']['access'], 'alive')
        self.assertEqual(by_id['dead']['access'], 'deleted_or_inaccessible')
        self.assertEqual(by_id['invalid']['access'], 'invalid_grant')
        self.assertTrue(all(r['gateway'] == 'down' for r in report['rooms']))
        self.assertFalse(report['home_channel']['live'])
        self.assertEqual(report['home_channel']['candidates'], ['one'])
        self.assertIn('forget dead', by_id['dead']['forget_hint'])
        with patch('hermes_cli.config.set_config_value') as setter:
            with self.assertRaises(BotSeshError): status.set_home(self.client, self.cfg, 'dead')
            setter.assert_not_called()

    def test_adapter_lifecycle_since_bound_to_process_and_home_repair(self):
        status.record_channel(self.store, 'up', 'adapter-instance')
        stamp=self.store.read()['channel_runtime']
        runtime={**self.runtime, 'pid':stamp['pid'], 'process_start':stamp['process_start'], 'channel':'up'}
        report=status.status_report(self.client,self.cfg,runtime=runtime)
        self.assertEqual(report['gateway']['since'],stamp['since'])
        runtime['process_start']='recycled-pid'
        self.assertIsNone(status.status_report(self.client,self.cfg,runtime=runtime).get('gateway',{}).get('since'))
        from hermes_cli.config import set_config_value, load_config_readonly
        with contextlib.redirect_stdout(io.StringIO()):
            set_config_value('platforms.botsesh',json.dumps(self.cfg['platforms']['botsesh']))
            status.set_home(self.client,load_config_readonly(),'one')
        self.assertEqual(load_config_readonly()['platforms']['botsesh']['home_channel']['chat_id'],'one')

    def test_live_gateway_platform_requires_writer_and_exact_profile(self):
        from types import SimpleNamespace
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        live = {'pid':123, 'start_time':42, 'updated_at':stamp,
                'platforms':{'fixture:botsesh': {'state':'connected', 'writer_pid':123, 'writer_start_time':42, 'updated_at':stamp},
                             'botsesh': {'state':'disconnected', 'writer_pid':123, 'writer_start_time':42, 'updated_at':stamp}}}
        with patch.object(status, 'profile_context', return_value=(self.home, 'fixture')), \
             patch('gateway.status.resolve_gateway_liveness', return_value=SimpleNamespace(running=True, probe_error=False, pid=123, source='multiplexer', runtime=live)), \
             patch('gateway.status.get_process_start_time', return_value=42):
            self.assertEqual(status.gateway_snapshot()['channel'], 'up')
            live['platforms']['fixture:botsesh']['writer_pid'] = 999
            self.assertEqual(status.gateway_snapshot()['channel'], 'down')
            live['platforms']['fixture:botsesh']['writer_pid'] = 123
            live['updated_at'] = '2000-01-01T00:00:00Z'
            self.assertEqual(status.gateway_snapshot()['channel'], 'unknown')


class DeclaredCliContract(unittest.TestCase):
    def test_connect_and_status_via_public_provider_helper_without_node_or_login(self):
        """Offline public-helper contract; native HTTPS CLI proof is separate."""
        from hermes_cli.plugin_provider_requests import ProviderResponse
        from hermes_cli.config import load_config_readonly
        from hermes_botsesh_test import identity
        calls = []
        rooms = [{'room_id': rid, 'name': 'Fixture ' + rid, 'participant_id': 'bot-' + rid,
                  'display_name': 'fixture', 'gateway_profile': 'default', 'password_required': False}
                 for rid in ('one', 'two')]
        def request(provider, method, url, **kwargs):
            self.assertEqual((provider, method, url), ('rooms', 'POST', 'https://botsesh.com/mcp'))
            payload = kwargs['json']
            name = payload['params']['name']
            args = payload['params']['arguments']
            calls.append((name, args))
            if name == 'hermes_initialize':
                value = {'profile': 'default'}
            elif name == 'list_rooms':
                value = {'rooms': rooms}
            elif name == 'hermes_name':
                value = {'participant': {'id': 'bot-' + args['room_id'], 'name': 'fixture'}}
            elif name == 'hermes_seats':
                value = {'seats': []}
            elif name == 'hermes_inbox':
                value = {'targets': [], 'has_more': False, 'backlog': 0}
            else:
                self.fail(name)
            return ProviderResponse(200, {'content-type': 'application/json'}, json.dumps({
                'jsonrpc': '2.0', 'id': payload['id'], 'result': {'structuredContent': value}}).encode())
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'hermes'
            home.mkdir(mode=0o700)
            (home / 'config.yaml').write_text('platforms:\n  botsesh:\n    enabled: false\n    home_channel: {platform: botsesh, chat_id: dead, name: Dead}\n    extra:\n      group_allowed_chats: [dead]\n')
            client = Client(Store(home / 'plugin-data/botsesh'))
            client.store.update(lambda d: d['rooms'].update(dead={'room_id': 'dead', 'name': 'Dead',
                'active': False, 'state': 'deleted', 'transport': 'oauth', 'auth_version': 2}))
            empty = Path(tmp) / 'empty-path'
            empty.mkdir()
            parser = argparse.ArgumentParser(); cli.setup(parser)
            with patch.dict(os.environ, {'HERMES_HOME': str(home), 'PATH': str(empty)}), \
                 patch.object(cli, 'Client', return_value=client), \
                 patch.object(identity, 'current_profile', return_value='default'), \
                 patch('hermes_cli.plugin_provider_requests.credentialed_provider_request', side_effect=request), \
                 patch('webbrowser.open', side_effect=AssertionError('connect started signin')), \
                 patch.object(Client, 'command', side_effect=AssertionError('OAuth used Node')):
                self.assertIsNone(shutil.which('node'))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cli._handle_profile(parser.parse_args(['connect', '--no-start', '--name', 'fixture', '--room', 'two']), 'default')
                self.assertIn('Connected to Fixture two', out.getvalue())
                self.assertIn('home_channel=dead', out.getvalue())
                self.assertIn('status --set-home', out.getvalue())
                allowed = load_config_readonly()['platforms']['botsesh']['extra']['group_allowed_chats']
                self.assertEqual(allowed, ['dead', 'one', 'two'])
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cli._handle_profile(parser.parse_args(['status', '--json']), 'default')
                report = json.loads(out.getvalue())
                rows = {r['room_id']: r for r in report['rooms']}
                self.assertEqual(rows['one']['access'], 'alive')
                with self.subTest(retired_room_status='deleted'):
                    self.assertEqual(rows['dead']['access'], 'deleted')
                self.assertFalse(report['gateway']['running'])
                self.assertEqual(report['gateway']['channel'], 'down')
                self.assertFalse(report['home_channel']['live'])
                with contextlib.redirect_stdout(io.StringIO()):
                    cli._handle_profile(parser.parse_args(['status', '--set-home', 'two']), 'default')
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cli._handle_profile(parser.parse_args(['status', '--json']), 'default')
                self.assertTrue(json.loads(out.getvalue())['home_channel']['live'])
                self.assertEqual(calls[0][0], 'hermes_initialize')
                self.assertEqual([args['room_id'] for name, args in calls if name == 'hermes_name'], ['one', 'two'])
                self.assertFalse((home / 'auth.json').exists())


if __name__ == '__main__': unittest.main()
