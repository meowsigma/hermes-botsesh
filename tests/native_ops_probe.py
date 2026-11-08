"""Opt-in real native gateway probe in an isolated no-network/no-Node container.

Run only with HOME/HERMES_HOME under /task/native and the installed native
interpreter. Local OAuth/MCP responses are an explicit test fixture; lifecycle,
control discovery, channel writer identity and room polling are the real core.
No inference, production accounts, provider keys or host gateway operations.
"""
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HOME = Path('/task/native/home')
EVIDENCE = Path('/task/evidence')
MODE = sys.argv[1] if len(sys.argv) > 1 else 'verify'
assert Path(os.environ['HERMES_HOME']) == HOME
assert os.environ['HOME'] == '/task/native/os-home'
assert not shutil.which('node') and not shutil.which('nodejs')
assert not any(k.endswith(('API_KEY', 'AUTH_TOKEN')) for k in os.environ)
assert not (HOME / '.env').exists()
# Re-runs use only the auth journal minted by this loopback fixture.
if (HOME / 'auth.json').exists():
    assert set(json.loads((HOME / 'auth.json').read_text()).get('providers', {})) <= {'botsesh'}
PLUGIN = HOME / 'plugins/botsesh'
PLUGIN.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT, PLUGIN, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git', '__pycache__'))
(HOME / 'config.yaml').write_text('plugins:\n  enabled: [botsesh]\nauxiliary:\n  title_generation:\n    enabled: false\n')
command = ['/task/native/bin/hermes', '-p', 'default']
calls = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.path == '/mcp' else None
        if self.path == '/oauth/token':
            value = {'access_token': 'A' * 43, 'refresh_token': 'R' * 43, 'expires_in': 3600, 'scope': 'botsesh:rooms', 'token_type': 'Bearer'}
        elif self.path == '/mcp':
            name = request['params']['name']
            calls.append(name)
            if name == 'list_rooms':
                value = {'rooms': [{'room_id': 'native-ops', 'name': 'Local OPS fixture', 'participant_id': 'bot', 'display_name': 'default', 'gateway_profile': 'default', 'password_required': False}]}
            elif name == 'hermes_name': value = {'participant': {'id': 'bot', 'name': 'default'}}
            elif name == 'hermes_seats': value = {'seats': []}
            elif name == 'hermes_notice': value = {'ok': True}
            elif name == 'hermes_inbox': value = {'targets': [], 'has_more': False, 'backlog': 0}
            elif name == 'hermes_listener': value = {'state': request['params']['arguments']['state']}
            elif name == 'wait_for_messages':
                time.sleep(.5)
                value = {'next_after': 0, 'since': 'local-native-ops'}
            else:
                raise AssertionError(name)  # No content or inference operations are supplied.
            value = {'jsonrpc': '2.0', 'id': request['id'], 'result': {'structuredContent': value}}
        else: raise AssertionError(self.path)
        payload = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        with contextlib.suppress(BrokenPipeError): self.wfile.write(payload)

    def log_message(self, *args): pass


def run(args, label, *, ok=True, timeout=100):
    result = subprocess.run([*command, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    (EVIDENCE / (MODE + '-' + label + '.log')).write_text(result.stdout + result.stderr)
    if ok: assert result.returncode == 0, (label, result.returncode, result.stdout, result.stderr)
    return result


def report(label):
    result = run(['botsesh', 'status', '--json'], label)
    value = json.loads(result.stdout)
    (EVIDENCE / (MODE + '-' + label + '.json')).write_text(json.dumps(value, indent=2))
    return value


with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    proc = subprocess.Popen([*command, 'botsesh', 'connect', '--no-browser', '--no-start', '--name', 'default', '--dev-origin', origin], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    incoming = queue.Queue()
    lines = []
    def consume():
        for line in proc.stdout:
            lines.append(line)
            incoming.put(line)
        incoming.put(None)
    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 40
        authorize = None
        while time.monotonic() < deadline:
            try: line = incoming.get(timeout=.2)
            except queue.Empty: continue
            if line is None: break
            if '/oauth/authorize?' in line:
                authorize = line.strip()
                break
        assert authorize, ''.join(lines)
        params = parse_qs(urlsplit(authorize).query)
        callback = urlsplit(params['redirect_uri'][0])
        conn = http.client.HTTPConnection(callback.hostname, callback.port, timeout=3)
        conn.request('POST', callback.path + '?' + urlencode({'code': 'local-only-code', 'state': params['state'][0]}), body='')
        response = conn.getresponse()
        assert response.status == 302
        response.read()
        conn.close()
        proc.wait(timeout=40)
        reader.join(3)
        assert proc.returncode == 0, ''.join(lines)
        # Do not retain authorization URLs/codes in evidence.
        run(['gateway', 'ensure', '--json', '--timeout', '40'], 'ensure')
        time.sleep(2)
        from hermes_cli.gateway_runtime import discover_gateway_endpoint, control_home_for
        from gateway.control_socket import query_gateway_control
        endpoint = discover_gateway_endpoint(HOME).endpoint
        assert endpoint
        control = control_home_for(HOME, endpoint)
        identify = query_gateway_control(control, 'identify')
        live = query_gateway_control(control, 'status')
        (EVIDENCE / (MODE + '-native-control.json')).write_text(json.dumps({'endpoint': vars(endpoint), 'identify': identify, 'status': live}, indent=2, default=list))
        before = report('before')
        if MODE == 'reproduce':
            assert before['gateway']['running'] is True and before['gateway']['channel'] == 'unknown', before
            result = run(['botsesh', 'restart-gateway', '--when-idle', '--max-wait', '4', '--restart-timeout', '15', '--health-timeout', '2'], 'restart', ok=False)
            after = report('after')
            assert result.returncode != 0 and after['gateway']['running'] is False, after
            print('REPRODUCED: native live channel reported unknown; foreground restart timeout left gateway stopped.', flush=True)
        else:
            assert before['gateway']['channel'] == 'up', before
            assert before['rooms'][0]['gateway'] == 'connected', before
            run(['botsesh', 'restart-gateway', '--checkpoint'], 'checkpoint')
            started = time.time()
            run(['botsesh', 'restart-gateway', '--when-idle', '--max-wait', '10', '--restart-timeout', '40', '--health-timeout', '15'], 'restart')
            after = report('after')
            assert after['gateway']['instance'] != before['gateway']['instance']
            assert after['gateway']['running'] and after['gateway']['channel'] == 'up'
            assert after['rooms'][0]['gateway'] == 'connected' and after['rooms'][0]['last_check'] >= started
            print('VERIFIED: real idle restart, changed incarnation, up channel and fresh connected room polling.', flush=True)
            run(['botsesh', 'restart-gateway', '--checkpoint'], 'recovery-checkpoint')
            # Real bad candidate: channel disabled in config, not a health double.
            run(['config', 'set', 'platforms.botsesh.enabled', 'false'], 'disable-candidate')
            launcher = Path('/task/native/bin/hermes')
            original_launcher = launcher.read_text()
            for marker in ('recovery-restart-count', 'recovery-stop-injected'):
                Path('/task/native', marker).unlink(missing_ok=True)
            launcher.write_text('#!/bin/sh\nexec ' + sys.executable + ' /task/source/tests/native_ops_fault.py "$@"\n')
            try:
                failed = run(['botsesh', 'restart-gateway', '--when-idle', '--max-wait', '10', '--restart-timeout', '40', '--health-timeout', '4'], 'forced-unhealthy', ok=False)
            finally:
                launcher.write_text(original_launcher)
            assert failed.returncode == 1 and 'candidate_failed_auto_revert_healthy' in failed.stderr, failed
            assert 'gateway process CONFIRMED RUNNING' in failed.stdout
            assert 'recovering through supported `hermes gateway ensure`' in failed.stdout
            assert Path('/task/native/recovery-stop-injected').exists()
            recovered = report('recovered')
            assert recovered['gateway']['running'] is True and recovered['gateway']['channel'] == 'up'
            assert recovered['gateway']['instance'] != after['gateway']['instance']
            assert recovered['rooms'][0]['gateway'] == 'connected' and recovered['rooms'][0]['last_check'] > after['rooms'][0]['last_check']
            print('VERIFIED: real unhealthy candidate auto-reverted; injected supported recovery stop was repaired by ensure; exact gateway running/up at exit1.', flush=True)
            # An actual container-local manager owns the next gateway. --detach
            # must hand it back to that manager, not spawn an unsupervised peer.
            run(['gateway', 'stop'], 'manual-stop')
            incarnations = []
            def supervise():
                with (EVIDENCE / 'verify-external-supervisor.log').open('w') as log:
                    while True:
                        child = subprocess.Popen([*command, 'gateway', 'run', '--external-supervisor', '--quiet'], stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                        record = {'pid': child.pid}
                        incarnations.append(record)
                        record['exit'] = child.wait()
                        if record['exit'] != 75:
                            break
            supervisor = threading.Thread(target=supervise, daemon=True)
            supervisor.start()
            deadline = time.monotonic() + 40
            while True:
                supervised_before = report('supervised-before')
                if supervised_before['gateway']['channel'] == 'up':
                    break
                assert time.monotonic() < deadline, supervised_before
                time.sleep(1)
            assert supervised_before['gateway']['supervisor'] == 'external'
            run(['botsesh', 'restart-gateway', '--checkpoint'], 'supervised-checkpoint')
            run(['botsesh', 'restart-gateway', '--when-idle', '--max-wait', '10', '--restart-timeout', '40', '--health-timeout', '10'], 'supervised-restart')
            supervised_after = report('supervised-after')
            assert supervised_after['gateway']['supervisor'] == 'external'
            assert supervised_after['gateway']['channel'] == 'up' and supervised_after['rooms'][0]['gateway'] == 'connected'
            assert supervised_after['gateway']['instance'] != supervised_before['gateway']['instance']
            assert len(incarnations) == 2 and incarnations[0]['exit'] == 75
            assert supervised_after['gateway']['pid'] == incarnations[1]['pid']
            run(['gateway', 'stop'], 'supervised-stop')
            supervisor.join(10)
            assert not supervisor.is_alive()
            (EVIDENCE / 'verify-supervision.json').write_text(json.dumps(incarnations, indent=2))
            print('VERIFIED: external supervisor retained ownership; native graceful restart exited75 and manager relaunched exactly one new gateway.', flush=True)
        (EVIDENCE / (MODE + '-summary.json')).write_text(json.dumps({'mode': MODE, 'node': False, 'network': 'none (loopback fixture only)', 'provider_credentials': False, 'inference': 0, 'before': before, 'after': after, 'fixture_calls': sorted(set(calls))}, indent=2))
    finally:
        if proc.poll() is None:
            # Only the test connect subprocess, never a gateway or host process.
            proc.terminate()
            proc.wait(timeout=5)
        proc.stdout.close()
        run(['gateway', 'stop'], 'cleanup', ok=False)
        server.shutdown()
