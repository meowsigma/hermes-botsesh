"""Run with --server PATH against companion HB050 server; no production/inference.

Uses real `python -m hermes_cli.main botsesh ...`, real PKCE callback/token/MCP,
an isolated HERMES_HOME and PATH with neither Node nor a substitute `hermes`.
Only the separately launched local server fixture has access to Node.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler
from hermes_yaml import safe_load


def run(server):
    node = shutil.which('node')
    if not node:
        raise RuntimeError('The server test fixture needs Node; the CLI PATH does not.')
    plugin = Path(__file__).resolve().parents[1]
    http = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='identity-cli-') as temporary:
        root = Path(temporary) / 'hermes'
        root.mkdir(mode=0o700)
        empty_path = Path(temporary) / 'empty-path'
        empty_path.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'BOTSESH_', 'OPENAI_', 'ANTHROPIC_', 'OPENROUTER_', 'XAI_'))}
        env.update(HERMES_HOME=str(root), HOME=temporary, PATH=str(empty_path), PYTHONUNBUFFERED='1', HERMES_SKIP_UPDATE_CHECK='1')
        assert shutil.which('node', path=env['PATH']) is None
        for home in (root, root / 'profiles' / 'evilgoonbot'):
            home.mkdir(mode=0o700, parents=True, exist_ok=True)
            (home / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['botsesh']}}))
            (home / '.env').write_text('')
            shutil.copytree(plugin, home / 'plugins' / 'botsesh', ignore=shutil.ignore_patterns('.git', '__pycache__'))
        fixture = subprocess.Popen([node, str(server / 'tests/helpers/hermes-identity-cli-fixture.mjs')], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            ready = json.loads(fixture.stdout.readline())
            def control(path, data=None):
                request = Request(ready['controller'] + path, data=json.dumps(data).encode() if data is not None else None, headers={'Content-Type': 'application/json'})
                with http.open(request, timeout=10) as response:
                    return json.load(response)

            def command(args, connect=False):
                process = subprocess.Popen([sys.executable, '-m', 'hermes_cli.main', *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, bufsize=1)
                lines, approved, deadline = [], False, time.monotonic() + 40
                try:
                    pending = queue.Queue()
                    def collect():
                        for line in process.stdout:
                            pending.put(line)
                        pending.put(None)
                    reader = threading.Thread(target=collect, daemon=True)
                    reader.start()
                    while time.monotonic() < deadline:
                        try:
                            line = pending.get(timeout=.2)
                        except queue.Empty:
                            continue
                        if line is None:
                            break
                        lines.append(line)
                        if '/oauth/authorize?' in line and line.strip().startswith(ready['origin']):
                            control('/approve', {'url': line.strip()})
                            approved = True
                    process.wait(timeout=5)
                    output = ''.join(lines)
                    assert process.returncode == 0, 'isolated CLI failed: ' + next((l.strip() for l in lines if l.startswith('BotSesh:')), 'see local code, not raw OAuth output')
                    assert 'Profile [default]:' not in output, 'headless CLI prompted'
                    if connect:
                        assert approved, 'native authorization URL not observed'
                        assert 'Connected to Identity fixture as @' in output
                    return output
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            ids = []
            for _ in range(4):
                command(['botsesh', 'connect', '--no-start', '--no-browser', '--dev-origin', ready['origin']], connect=True)
                rooms = json.loads((root / 'plugin-data/botsesh/rooms.json').read_text())['rooms']
                ids.append(rooms[ready['room_id']]['participant_id'])
            assert len(set(ids)) == 1, 'reconnect changed the seat'
            assert len(control('/summary')['memberships']) == 1
            command(['botsesh', 'connect', '--profile', 'evilgoonbot', '--no-start', '--no-browser', '--dev-origin', ready['origin']], connect=True)
            summary = control('/summary')['memberships']
            assert len(summary) == 2
            assert {s['display_name'] for s in summary} == {'default', 'evilgoonbot'}
            selected_home = root / 'profiles/evilgoonbot'
            named = json.loads((selected_home / 'plugin-data/botsesh/rooms.json').read_text())['rooms'][ready['room_id']]
            assert named['participant_id'] != ids[0]
            assert named['profile'] == 'evilgoonbot'
            command(['config', 'set', 'platforms.botsesh.extra.profile', 'evilgoonbot'])
            command(['botsesh', 'connect', '--no-start', '--no-browser', '--dev-origin', ready['origin']], connect=True)
            assert len(control('/summary')['memberships']) == 2
            command(['botsesh', 'rename', 'Night keeper'])
            command(['botsesh', 'connect', '--no-start', '--no-browser', '--dev-origin', ready['origin']], connect=True)
            assert {s['display_name'] for s in control('/summary')['memberships']} == {'default', 'Night keeper'}
            output = command(['botsesh', 'seats'])
            assert 'Night keeper' in output and 'default' in output
            saved = safe_load((selected_home / 'config.yaml').read_text())
            assert saved['platforms']['botsesh']['extra']['profile'] == 'evilgoonbot'
            assert saved['platforms']['botsesh']['extra']['display_name'] == 'Night keeper'
            for home in (root, selected_home):
                assert (home / 'auth.json').stat().st_mode & 0o777 == 0o600
                assert (home / 'plugin-data/botsesh/rooms.json').stat().st_mode & 0o777 == 0o600
            print(json.dumps({'result': 'PASS', 'native_cli': True, 'node_in_cli_path': False, 'initial_plus_reconnects': 4, 'seats_after_reconnects': 1, 'profiles': 2, 'distinct_named_seats': True, 'saved_knob_routes_native_home': True, 'rename_survives_reconnect': True, 'headless_prompts': 0, 'private_credentials': True, 'production_writes': 0, 'inference_calls': 0}))
        finally:
            fixture.terminate()
            fixture.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', type=Path, required=True)
    run(parser.parse_args().server.resolve())
