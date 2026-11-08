"""HB050 offline OPS tests. Local HTTP fixtures/doubles, no inference/services."""
import argparse
import base64
import contextlib
import copy
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, HTTPServer
import io
import json
import os
from pathlib import Path
import select
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import sys
import queue
import unittest
from unittest.mock import patch, Mock
from urllib.parse import urlencode, urlsplit, parse_qs

from test_plugin import Client, Store, BotSeshError, ROOT
from hermes_botsesh_test import cli, oauth, ops_status as status, ops_restart as restart


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

    def test_wait_active_then_inbox_then_restart_and_max_wait(self):
        self.store.update(lambda d: d['rooms'].clear())
        ticks = [0]
        work = iter([1, 0, 0])
        queue = iter([0, 1, 0])
        waited = []
        def sleep(s): ticks[0] += s; waited.append(s)
        result = restart.wait_idle(self.client, {}, max_wait=3, snapshot=lambda:self.runtime,
            work=lambda r:next(work), queues=lambda r:next(queue), clock=lambda:ticks[0], sleep=sleep)
        self.assertIs(result, self.runtime); self.assertEqual(len(waited), 2)
        with self.assertRaises(BotSeshError) as error:
            restart.wait_idle(self.client, {}, max_wait=1, snapshot=lambda:self.runtime,
                work=lambda r:1, queues=lambda r:0, clock=lambda:ticks[0], sleep=sleep)
        self.assertEqual(error.exception.code, 'idle_wait_timeout_no_restart')

    def test_idle_skips_dead_and_invalid_rooms_but_keeps_local_pending_gate(self):
        self.store.update(lambda d: d['rooms']['dead'].update(pending_final={'text': 'fixture'}))
        ticks = [0]; waited = []; out = io.StringIO()
        def sleep(seconds):
            ticks[0] += seconds; waited.append(seconds)
            self.store.update(lambda d: d['rooms']['dead'].pop('pending_final'))
        with contextlib.redirect_stdout(out):
            result = restart.wait_idle(self.client, self.cfg, max_wait=2,
                snapshot=lambda: self.runtime, work=lambda r: 0, queues=lambda r: 0,
                clock=lambda: ticks[0], sleep=sleep)
        self.assertIs(result, self.runtime)
        self.assertEqual(waited, [1])
        self.assertIn('Skipping server inbox for dead: deleted_or_inaccessible', out.getvalue())
        self.assertIn('Skipping server inbox for invalid: invalid_grant', out.getvalue())
        self.assertEqual(set(self.store.read()['rooms']), {'one', 'dead', 'invalid'})

    def test_private_queue_schema_and_zero_status_never_substitute_for_public_idle_probe(self):
        db = sqlite3.connect(self.home/'state.db')
        db.execute('CREATE TABLE session_admissions(status TEXT)')
        db.executemany('INSERT INTO session_admissions VALUES (?)', [('queued',), ('started',), ('terminal',)])
        db.commit(); db.close()
        with self.assertRaisesRegex(BotSeshError, 'needs_hermes_api'):
            restart.queued_admissions(self.runtime)
        with self.assertRaisesRegex(BotSeshError, 'needs_hermes_api'):
            restart.work_snapshot({**self.runtime, 'active_agents':0})
        args = argparse.Namespace(botsesh_action='restart-gateway', checkpoint=False)
        with patch.object(restart, 'lifecycle_command') as lifecycle:
            with self.assertRaises(SystemExit) as error:
                cli._handle_profile(args, 'default')
        self.assertIn('needs_hermes_api', str(error.exception))
        lifecycle.assert_not_called()

    def test_restart_cli_uses_lifecycle_no_signals_force_or_all(self):
        with patch.object(restart, 'profile_context', return_value=(self.home, 'fixture')), patch.object(restart.shutil, 'which', return_value='/fixture/hermes'), patch.object(restart.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            restart.lifecycle_restart(timeout=42)
        self.assertEqual(run.call_args.args[0], ['/fixture/hermes','-p','fixture','gateway','restart','--detach'])
        self.assertIs(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['env']['HERMES_HOME'], str(self.home))
        self.assertNotIn('HERMES_PROFILE', run.call_args.kwargs['env'])
        self.assertNotIn('HERMES_PROFILE_NAME', run.call_args.kwargs['env'])

    def test_failure_recovery_ensures_then_reads_back_exact_owner(self):
        live = {**self.runtime, 'process_start': 42}
        with patch.object(restart, 'lifecycle_command') as command:
            restart.ensure_running(timeout=12, snapshot=Mock(side_effect=[{'running': False}, live]))
        command.assert_called_once_with(['ensure', '--json', '--timeout', '12'], timeout=17)
        with patch.object(restart, 'lifecycle_command', side_effect=BotSeshError('gateway_restart_refused_or_failed')):
            with self.assertRaises(BotSeshError) as error:
                restart.ensure_running(timeout=12, snapshot=lambda: {'running': False})
        self.assertEqual(error.exception.code, 'gateway_recovery_running_unconfirmed')

    def test_restore_conflict_still_confirms_running_without_overwriting(self):
        restart.checkpoint(self.client, self.cfg)
        args = argparse.Namespace(checkpoint=False, when_idle=True, max_wait=2, restart_timeout=2, health_timeout=2)
        recover = Mock()
        rollback = Mock(side_effect=BotSeshError('rollback_conflict_preserved_concurrent_changes'))
        with self.assertRaises(BotSeshError) as error:
            restart.restart_gateway(self.client, self.cfg, args, idle=lambda *a, **kw: dict(self.runtime),
                restart=Mock(), health=lambda *a, **kw: False, rollback=rollback, recover=recover)
        self.assertEqual(error.exception.code, 'rollback_conflict_preserved_concurrent_changes')
        recover.assert_called_once_with(timeout=2)
        self.assertIn('restart_checkpoint', self.store.read())

    def test_restart_preserves_preexisting_dead_rooms_and_home_without_reverting(self):
        restart.checkpoint(self.client, self.cfg)
        args = argparse.Namespace(checkpoint=False, when_idle=True, max_wait=2, restart_timeout=2, health_timeout=2)
        ticks = [0]; busy = iter([1, 0]); events = []; out = io.StringIO()
        def sleep(seconds): ticks[0] += seconds; events.append('wait')
        def idle(client, config, **kwargs):
            return restart.wait_idle(client, config, **kwargs, snapshot=lambda: dict(self.runtime),
                work=lambda r: next(busy, 0), queues=lambda r: 0, clock=lambda: ticks[0], sleep=sleep)
        def relaunch(**kwargs):
            events.append('restart')
            self.runtime.update(instance='second', channel='up')
            self.store.update(lambda d: d['rooms']['one']['health'].update(last_check=time.time()))
        rollback = Mock(return_value=self.cfg)
        with contextlib.redirect_stdout(out), patch.object(status, 'gateway_snapshot', side_effect=lambda: self.runtime):
            restart.restart_gateway(self.client, self.cfg, args, idle=idle, restart=relaunch,
                health=lambda c, cfg, before, **kw: restart.wait_healthy(c, cfg, before, timeout=0), rollback=rollback)
        rollback.assert_not_called()
        self.assertEqual(events, ['wait', 'restart'])
        self.assertIn('Pre-existing home_channel warning: dead', out.getvalue())
        self.assertIn('Pre-existing room warning: invalid', out.getvalue())
        self.assertNotIn('restart_checkpoint', self.store.read())
        self.assertEqual(set(self.store.read()['rooms']), {'one', 'dead', 'invalid'})

    def test_auto_revert_and_second_restart_simulated(self):
        self.store.update(lambda d:d['rooms'].clear())
        restart.checkpoint(self.client, self.cfg)
        args = argparse.Namespace(checkpoint=False, when_idle=True, max_wait=2, restart_timeout=2, health_timeout=2)
        idle = Mock(side_effect=lambda *a, **kw: dict(self.runtime))
        relaunch = Mock()
        rollback = Mock(return_value={})
        health = Mock(side_effect=[False, True])
        with self.assertRaises(BotSeshError) as error:
            restart.restart_gateway(self.client, self.cfg, args, idle=idle, restart=relaunch, rollback=rollback, health=health, recover=Mock())
        self.assertEqual(error.exception.code, 'candidate_failed_auto_revert_healthy')
        self.assertEqual(relaunch.call_count, 2); rollback.assert_called_once()
        self.assertFalse(idle.call_args.kwargs['check_inbox']); self.assertTrue(idle.call_args.kwargs['allow_stopped'])
        self.assertNotIn('restart_checkpoint', self.store.read())

    def test_health_baseline_still_rejects_channel_and_live_room_regressions(self):
        before = dict(self.runtime)
        with contextlib.redirect_stdout(io.StringIO()):
            before['liveness_baseline'] = restart.liveness_baseline(self.client, self.cfg, before)
        before['restart_at'] = time.time()
        self.runtime.update(instance='second', channel='up')
        self.store.update(lambda d: d['rooms']['one']['health'].update(last_check=time.time()))
        report = status.status_report(self.client, self.cfg, runtime=self.runtime)
        with patch.object(restart, 'status_report', return_value=report):
            self.assertTrue(restart.healthy(self.client, self.cfg, before))
            self.assertFalse(restart.healthy(self.client, self.cfg, dict(self.runtime)))
        for failure in ('channel_down', 'old_incarnation', 'stale_poll', 'lost_room', 'removed_room'):
            with self.subTest(failure=failure):
                bad = copy.deepcopy(report)
                if failure == 'channel_down': bad['gateway']['channel'] = 'down'
                elif failure == 'old_incarnation': bad['gateway']['instance'] = before['instance']
                elif failure == 'stale_poll': bad['rooms'][0]['last_check'] = before['restart_at'] - 1
                elif failure == 'lost_room': bad['rooms'][0]['alive'] = False
                else: bad['rooms'] = [r for r in bad['rooms'] if r['room_id'] != 'one']
                with patch.object(restart, 'status_report', return_value=bad):
                    self.assertFalse(restart.healthy(self.client, self.cfg, before))
        live_home = copy.deepcopy(self.cfg)
        live_home['platforms']['botsesh']['home_channel']['chat_id'] = 'one'
        with contextlib.redirect_stdout(io.StringIO()):
            before['liveness_baseline'] = restart.liveness_baseline(self.client, live_home, before)
        with patch.object(restart, 'status_report', return_value=report):
            self.assertFalse(restart.healthy(self.client, live_home, before), 'previously live home must not regress')

    def test_health_allows_no_live_rooms_only_when_baseline_had_none(self):
        self.store.update(lambda d: d['rooms'].pop('one'))
        before = dict(self.runtime)
        with contextlib.redirect_stdout(io.StringIO()):
            before['liveness_baseline'] = restart.liveness_baseline(self.client, self.cfg, before)
        self.assertEqual(before['liveness_baseline']['rooms'], [])
        before['restart_at'] = time.time()
        self.runtime.update(instance='second', channel='up')
        with patch.object(status, 'gateway_snapshot', return_value=self.runtime):
            self.assertTrue(restart.healthy(self.client, self.cfg, before))
            before['liveness_baseline']['rooms'] = ['one']
            self.assertFalse(restart.healthy(self.client, self.cfg, before))

    def test_real_health_auto_revert_keeps_original_baseline_and_dead_rooms(self):
        restart.checkpoint(self.client, self.cfg)
        args = argparse.Namespace(checkpoint=False, when_idle=True, max_wait=2, restart_timeout=2, health_timeout=2)
        stages = []; baselines = []
        original_rpc = oauth.OAuthClient.rpc.side_effect
        def rpc(binding, name, *a, **kw):
            if binding['room_id'] == 'one' and name == 'list_rooms' and stages == ['candidate']:
                return {'rooms': []}
            return original_rpc(binding, name, *a, **kw)
        def relaunch(**kwargs):
            stages.append('candidate' if not stages else 'recovery')
            self.runtime.update(instance=stages[-1], channel='up')
            self.store.update(lambda d: d['rooms']['one']['health'].update(last_check=time.time()))
        def health(c, cfg, before, **kw):
            baselines.append(before['liveness_baseline'])
            return restart.wait_healthy(c, cfg, before, timeout=0)
        def idle(c, cfg, **kw):
            return restart.wait_idle(c, cfg, **kw, snapshot=lambda: dict(self.runtime), work=lambda r: 0, queues=lambda r: 0)
        rollback = Mock(return_value=self.cfg)
        with contextlib.redirect_stdout(io.StringIO()), patch.object(status, 'gateway_snapshot', side_effect=lambda: self.runtime), \
             patch.object(oauth.OAuthClient, 'rpc', side_effect=rpc):
            with self.assertRaises(BotSeshError) as error:
                restart.restart_gateway(self.client, self.cfg, args, idle=idle, restart=relaunch, rollback=rollback, health=health, recover=Mock())
        self.assertEqual(error.exception.code, 'candidate_failed_auto_revert_healthy')
        self.assertEqual(stages, ['candidate', 'recovery'])
        self.assertEqual([b['rooms'] for b in baselines], [['one'], ['one']])
        rollback.assert_called_once()
        self.assertEqual(set(self.store.read()['rooms']), {'one', 'dead', 'invalid'})
        self.assertNotIn('restart_checkpoint', self.store.read())

    def test_restart_happens_only_after_turn_and_server_inbox_drain(self):
        self.store.update(lambda d: [d['rooms'].pop(r) for r in ('dead','invalid')])
        restart.checkpoint(self.client,self.cfg)
        events=[]; ticks=[0]; work=iter([1,0,0]); backlog=iter([0,2,0,0])
        def sleep(s): ticks[0]+=s; events.append('wait')
        def idle(client,config,**kw):
            return restart.wait_idle(client,config,**kw,snapshot=lambda:dict(self.runtime),work=lambda r:next(work),
                queues=lambda r:0,clock=lambda:ticks[0],sleep=sleep)
        def report(*a,**kw):
            return {'rooms':[{'room_id':'one','alive':True,'backlog':next(backlog)}],
                    'home_channel': {'room_id':'dead', 'live':False, 'state':'deleted_or_inaccessible'}}
        args=argparse.Namespace(checkpoint=False,when_idle=True,max_wait=3,restart_timeout=2,health_timeout=2)
        with patch.object(restart,'status_report',side_effect=report):
            restart.restart_gateway(self.client,self.cfg,args,idle=idle,
                restart=lambda **kw:events.append('restart'),health=lambda *a,**kw:True)
        self.assertEqual(events,['wait','wait','restart'])

    def test_connect_refreshes_config_checkpoint_and_preserves_earlier_rooms_on_restore(self):
        from hermes_cli.config import load_config_readonly, set_config_value
        with contextlib.redirect_stdout(io.StringIO()), patch.object(cli, 'Client', return_value=self.client):
            set_config_value('platforms.botsesh', json.dumps(self.cfg['platforms']['botsesh']))
            cli.configure({'room_id': 'two', 'name': 'Two'})
            before_three = load_config_readonly()
            cli.configure({'room_id': 'three', 'name': 'Three'})
            saved = self.store.read()['restart_checkpoint']
            self.assertEqual(saved['config'], restart.config_part(before_three))
            self.assertIsNone(saved['source'])
            attempted = {'config': restart.config_part(load_config_readonly()), 'source': restart.source_snapshot()}
            restored = restart.restore(self.client, saved, attempted)
        self.assertIn('two', restored['platforms']['botsesh']['extra']['group_allowed_chats'])
        self.assertNotIn('three', restored['platforms']['botsesh']['extra']['group_allowed_chats'])
        self.assertEqual(set(self.store.read()['rooms']), {'one', 'dead', 'invalid'})

    def test_connect_never_replaces_explicit_source_checkpoint(self):
        from hermes_cli.config import load_config_readonly
        restart.checkpoint(self.client, self.cfg, source=True)
        saved = self.store.read()['restart_checkpoint']
        with contextlib.redirect_stdout(io.StringIO()), patch.object(cli, 'Client', return_value=self.client):
            cli.configure({'room_id': 'two', 'name': 'Two'})
            cli.configure({'room_id': 'three', 'name': 'Three'})
        self.assertEqual(self.store.read()['restart_checkpoint'], saved)
        self.assertIn('three', load_config_readonly()['platforms']['botsesh']['extra']['group_allowed_chats'])

    def test_restore_real_scoped_config_and_code_preserves_concurrent_edits(self):
        from hermes_cli.config import load_config_readonly, set_config_value
        source = self.home/'source'; source.mkdir(); (source/'plugin.yaml').write_text('version: old\n')
        with patch.object(restart, 'ROOT', source):
            with contextlib.redirect_stdout(io.StringIO()):
                set_config_value('platforms.botsesh', json.dumps(self.cfg['platforms']['botsesh']))
                set_config_value('platforms.telegram', '{"enabled":false}')
            original = load_config_readonly()
            restart.checkpoint(self.client, original, source=True)
            saved = self.store.read()['restart_checkpoint']
            (source/'plugin.yaml').write_text('version: candidate\n')
            with contextlib.redirect_stdout(io.StringIO()): set_config_value('platforms.botsesh.enabled', 'false')
            attempt = {'config':restart.config_part(load_config_readonly()), 'source':restart.source_snapshot()}
            with contextlib.redirect_stdout(io.StringIO()): recovered = restart.restore(self.client, saved, attempt)
            self.assertEqual(recovered['platforms']['botsesh'], original['platforms']['botsesh'])
            self.assertEqual(recovered['platforms']['telegram'], original['platforms']['telegram'])
            self.assertEqual((source/'plugin.yaml').read_text(), 'version: old\n')
            (source/'plugin.yaml').write_text('version: concurrent\n')
            with self.assertRaises(BotSeshError) as error: restart.restore(self.client, saved, attempt)
            self.assertEqual(error.exception.code, 'rollback_conflict_preserved_concurrent_changes')


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
