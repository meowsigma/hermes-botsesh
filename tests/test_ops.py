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
from hermes_botsesh_test import cli, oauth, oauth_callback, ops_status as status


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
                       'origin': 'http://127.0.0.1:12345', 'transport': 'oauth',
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

    def test_post_callback_query_and_form_no_secret_diagnostics(self):
        for path, body in (('/callback?code=private-code&state=private-state', ''),
                           ('/callback', 'code=private-code&state=private-state')):
            handler, result = oauth._make_loopback_callback_handler('/callback', origin='https://botsesh.com', expected_state='private-state')
            with HTTPServer(('127.0.0.1', 0), handler) as server:
                worker = threading.Thread(target=server.handle_request); worker.start()
                connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                connection.request('POST', path, body, {'Content-Type': 'application/x-www-form-urlencoded'})
                response = connection.getresponse()
                self.assertEqual(response.status, 302)
                self.assertNotIn('private', response.getheader('Location'))
                response.read(); connection.close(); worker.join(3)
                self.assertEqual(result['code'], 'private-code')
            out = io.StringIO()
            with contextlib.redirect_stdout(out): oauth_callback.timeout_diagnostic(handler.observations)
            self.assertIn('POST path="/callback"; code=present, state=present', out.getvalue())
            self.assertNotIn('private-code', out.getvalue()); self.assertNotIn('private-state', out.getvalue())
            self.assertIn('--paste-redirect', out.getvalue())

    def test_phone_hidden_paste_reprint_rejects_foreign_state_restores_echo(self):
        import pty
        import termios
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        output = io.StringIO(); result = {}; failure = []
        from hermes_cli.auth_constants import AuthError
        def err(message, code): return AuthError(message, provider='botsesh', code=code)
        with os.fdopen(os.dup(slave), 'r') as terminal:
            def prompt():
                try:
                    result.update(oauth_callback.paste_callback(redirect_uri='http://127.0.0.1:43111/callback',
                        authorize_url='https://botsesh.com/oauth/authorize?fixture=public', expected_state='expected-state', timeout=3, err=err))
                except Exception as error: failure.append(error)
            try:
                with patch('sys.stdin', terminal), contextlib.redirect_stdout(output):
                    worker=threading.Thread(target=prompt); worker.start()
                    deadline=time.monotonic()+2
                    while 'Redirect URL / url:' not in output.getvalue() and time.monotonic()<deadline: time.sleep(.005)
                    self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
                    os.write(master,b'http://127.0.0.1:43111/callback?code=private-code&state=foreign\n')
                    deadline=time.monotonic()+2
                    while 'Redirect rejected:' not in output.getvalue() and time.monotonic()<deadline: time.sleep(.005)
                    os.write(master,b'url\n')
                    deadline=time.monotonic()+2
                    while 'fixture=public' not in output.getvalue() and time.monotonic()<deadline: time.sleep(.005)
                    os.write(master,b'http://127.0.0.1:43111/callback?code=private-code&state=expected-state\n')
                    worker.join(4)
                self.assertFalse(worker.is_alive()); self.assertFalse(failure)
                self.assertEqual(result['code'],'private-code')
                self.assertNotIn('private-code',output.getvalue()); self.assertNotIn('expected-state',output.getvalue())
                self.assertEqual(termios.tcgetattr(slave),original)
                self.assertFalse(select.select([master],[],[],.05)[0], 'secret input was echoed')
            finally:
                os.close(master); os.close(slave)

    def test_actual_login_timeout_diagnostic_and_300_default(self):
        parser = argparse.ArgumentParser(); cli.setup(parser)
        self.assertEqual(parser.parse_args(['connect']).timeout, 300)
        from hermes_cli.auth_constants import AuthError
        def timeout(server, result, **kwargs):
            server.RequestHandlerClass.observations.append({'method':'POST', 'path':'/callback',
                'present': {'code': False, 'state': True, 'error': False, 'error_description': False}})
            raise AuthError('timed out', provider='botsesh', code='oauth_callback_timeout')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch('hermes_cli.auth_device_flow._serve_loopback_callback', side_effect=timeout):
            with self.assertRaises(AuthError): oauth.login(open_browser=False)
        self.assertIn('code=absent, state=present', out.getvalue())
        self.assertIn('Next:', out.getvalue())

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


class IsolatedNativeCli(unittest.TestCase):
    def test_no_node_cli_connect_post_and_status_local_server_only(self):
        """Actual installed Hermes CLI; isolated HOME/HERMES_HOME and local MCP/OAuth HTTP."""
        command = [sys.executable, '-m', 'hermes_cli.main']
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length','0')))
                calls.append(self.path)
                if self.path == '/oauth/token':
                    value = {'access_token':'A'*43,'refresh_token':'R'*43,'expires_in':3600,'scope':'botsesh:rooms','token_type':'Bearer'}
                elif self.path == '/mcp':
                    request = json.loads(body); name=request['params']['name']
                    # The integrated identity protocol confirms the chosen
                    # profile and inventory; retain the real POST/status proof.
                    if name == 'list_rooms': value={'rooms':[{'room_id':'one','name':'Fixture room','participant_id':'bot','display_name':'fixture','gateway_profile':'default','password_required':False}]}
                    elif name == 'hermes_name': value={'participant':{'id':'bot','name':'fixture'}}
                    elif name == 'hermes_seats': value={'seats':[]}
                    elif name == 'hermes_inbox': value={'targets':[],'has_more':False,'backlog':0}
                    else: raise AssertionError(name)
                    value={'jsonrpc':'2.0','id':request['id'],'result':{'structuredContent':value}}
                else: raise AssertionError(self.path)
                payload=json.dumps(value).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
            def log_message(self,*args): pass
        with tempfile.TemporaryDirectory() as tmp, ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
            home=Path(tmp)/'hermes'; home.mkdir(mode=0o700); (home/'plugins').mkdir(); (home/'plugins/botsesh').symlink_to(ROOT,target_is_directory=True)
            (home/'config.yaml').write_text('plugins:\n  enabled: [botsesh]\nplatforms:\n  botsesh:\n    enabled: false\n    home_channel: {platform: botsesh, chat_id: dead, name: Dead}\n    extra:\n      group_allowed_chats: [dead]\n')
            fixture_store=Store(home/'plugin-data/botsesh')
            fixture_store.update(lambda d:d['rooms'].update(dead={'room_id':'dead','name':'Dead','active':False,'state':'deleted','transport':'oauth'}))
            empty=Path(tmp)/'empty-path'; empty.mkdir()
            env={k:v for k,v in os.environ.items() if not (k.startswith(('HERMES_','OPENAI_','ANTHROPIC_','OPENROUTER_','BOTSESH_')) or k in ('PYTHONPATH','PYTHONHOME'))}
            import hermes_cli
            # A prepared dependency-only interpreter need not install this source
            # checkout. Pin the fixture child to the same actual core under test.
            env.update(HERMES_HOME=str(home),HOME=tmp,PATH=str(empty),PYTHONUNBUFFERED='1',NO_COLOR='1',
                       PYTHONPATH=str(Path(hermes_cli.__file__).resolve().parent.parent))
            self.assertIsNone(shutil.which('node',path=env['PATH']))
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            origin=f'http://127.0.0.1:{server.server_port}'
            proc=subprocess.Popen([*command,'botsesh','connect','--no-browser','--no-start','--name','fixture','--dev-origin',origin],env=env,cwd=tmp,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            lines=[]; authorize=None; deadline=time.monotonic()+40
            incoming=queue.Queue()
            def consume():
                for line in proc.stdout:
                    lines.append(line); incoming.put(line)
                incoming.put(None)
            reader=threading.Thread(target=consume,daemon=True); reader.start()
            try:
                while time.monotonic()<deadline:
                    try: line=incoming.get(timeout=.2)
                    except queue.Empty: continue
                    if line is None: break
                    if '/oauth/authorize?' in line: authorize=line.strip(); break
                self.assertTrue(authorize, ''.join(lines))
                params=parse_qs(urlsplit(authorize).query); callback=urlsplit(params['redirect_uri'][0])
                conn=http.client.HTTPConnection(callback.hostname,callback.port,timeout=3)
                conn.request('POST',callback.path+'?'+urlencode({'code':'fixture-code','state':params['state'][0]}),body='')
                response=conn.getresponse(); self.assertEqual(response.status,302); response.read(); conn.close()
                proc.wait(timeout=30); reader.join(3); output=''.join(lines)
                self.assertEqual(proc.returncode,0,output); self.assertIn('Connected to Fixture room',output)
                self.assertIn('home_channel=dead',output); self.assertIn('status --set-home one',output)
                self.assertNotIn('fixture-code',output); self.assertNotIn('R'*43,output)
                checked=subprocess.run([*command,'botsesh','status','--json'],env=env,cwd=tmp,capture_output=True,text=True,timeout=30)
                self.assertEqual(checked.returncode,0,checked.stderr+checked.stdout)
                report=json.loads(checked.stdout)
                rows={r['room_id']:r for r in report['rooms']}
                self.assertEqual(rows['one']['access'],'alive'); self.assertEqual(rows['dead']['access'],'deleted')
                self.assertFalse(report['gateway']['running']); self.assertEqual(report['gateway']['channel'],'down')
                self.assertFalse(report['home_channel']['live'])
                repaired=subprocess.run([*command,'botsesh','status','--set-home','one'],env=env,cwd=tmp,capture_output=True,text=True,timeout=30)
                self.assertEqual(repaired.returncode,0,repaired.stdout+repaired.stderr)
                checked=subprocess.run([*command,'botsesh','status','--json'],env=env,cwd=tmp,capture_output=True,text=True,timeout=30)
                self.assertTrue(json.loads(checked.stdout)['home_channel']['live'])
                self.assertIn('/oauth/token',calls); self.assertIn('/mcp',calls)
            finally:
                if proc.poll() is None: proc.kill(); proc.wait()
                reader.join(3)
                proc.stdout.close(); server.shutdown(); thread.join(3)


if __name__ == '__main__': unittest.main()
