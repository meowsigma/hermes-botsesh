"""Native no-Node OAuth regressions against an explicitly supplied local image.

No inference. All sign-up, consent, callback, token, list_rooms and revocation
traffic is real localhost HTTP. No callback URLs or credentials are retained.
"""
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
import unittest


@unittest.skipUnless(os.environ.get('BOTSESH_OAUTH_CONNECT_SERVER_COMMAND'),
                     'explicit exact-image local OAuth server command required')
class NativeOAuthConnect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-oauth-connect-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        plugin = Path(__file__).resolve().parents[1]
        shutil.copytree(plugin, self.home / 'plugins/botsesh',
                        ignore=shutil.ignore_patterns('.git', '__pycache__'))
        (self.home / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['botsesh']}}))
        (self.home / '.env').write_text('')
        empty = self.root / 'empty-path'
        empty.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(
            ('HERMES_', 'BOTSESH_', 'OPENAI_', 'ANTHROPIC_', 'OPENROUTER_', 'XAI_'))}
        self.env.update(HERMES_HOME=str(self.home), HOME=str(self.root), PATH=str(empty),
                        PYTHONUNBUFFERED='1', HERMES_SKIP_UPDATE_CHECK='1')
        for executable in ('node', 'nodejs'):
            self.assertIsNone(shutil.which(executable, path=self.env['PATH']))
        self.server = subprocess.Popen(json.loads(os.environ['BOTSESH_OAUTH_CONNECT_SERVER_COMMAND']),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_server)
        ready = self.server.stdout.readline()
        self.assertTrue(ready, 'exact-image fixture failed to start')
        self.ready = json.loads(ready)

    def control(self, **command):
        self.server.stdin.write(json.dumps(command) + '\n')
        self.server.stdin.flush()
        line = self.server.stdout.readline()
        self.assertTrue(line, 'exact-image fixture command failed')
        return json.loads(line)

    def stop_server(self):
        if self.server.poll() is None:
            self.server.stdin.write('{"op":"stop"}\n')
            self.server.stdin.flush()
        _, error = self.server.communicate(timeout=20)
        self.assertEqual(self.server.returncode, 0, error)

    def connect(self, *, requested=None, selected=None, legacy=False, success=True):
        args = ['botsesh', 'connect', '--no-browser', '--no-start', '--name', 'Fixture',
                '--dev-origin', self.ready['origin']]
        if requested is not None:
            args.append('--room=' + requested)
        process = subprocess.Popen([sys.executable, '-m', 'hermes_cli.main', *args], env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines, pending = [], queue.Queue()
        def collect():
            for line in process.stdout:
                pending.put(line)
            pending.put(None)
        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        approved = False
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                try:
                    line = pending.get(timeout=.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                # The OAuth URL remains in process memory only, never evidence.
                if line.strip().startswith(self.ready['origin'] + '/oauth/authorize?'):
                    self.control(op='approve', url=line.strip(),
                                 room_ids=selected if selected is not None else [self.ready['room_ids'][0]],
                                 legacy=legacy)
                    approved = True
                else:
                    lines.append(line)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.fail('native CLI did not exit; last non-authorization output: ' + ''.join(lines[-5:]))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            reader.join(timeout=2)
            process.stdout.close()
        self.assertTrue(approved, 'native OAuth authorization was not reached')
        snapshot = self.control(op='snapshot')
        output = ''.join(lines)
        error = next((l.strip() for l in lines if l.startswith('BotSesh:')), None)
        evidence = {'test': self._testMethodName, 'cli_exit': process.returncode,
                    'cli_error': error, 'node_and_nodejs_absent': True, **snapshot}
        path = os.environ.get('BOTSESH_OAUTH_CONNECT_EVIDENCE')
        if path:
            with open(path, 'a') as stream:
                stream.write(json.dumps(evidence) + '\n')
        self.assertEqual(process.returncode, 0 if success else 1, error or 'native CLI exit mismatch')
        if success:
            self.assertIn('Connected to ', output)
        return snapshot, output

    def saved(self):
        rooms = json.loads((self.home / 'plugin-data/botsesh/rooms.json').read_text())['rooms']
        rows = json.loads((self.home / 'auth.json').read_text())['plugin_oauth']
        row = next(iter(rows.values()))
        return rooms, row

    def assert_grant_saved(self, snapshot, primary):
        returned = snapshot['list_rooms'][0]['rooms']
        rooms, row = self.saved()
        self.assertEqual(set(rooms), {r['room_id'] for r in returned})
        self.assertEqual(set(row['extra']['room_ids']), set(rooms))
        self.assertEqual(row['extra']['room_id'], primary)
        for remote in returned:
            saved = rooms[remote['room_id']]
            self.assertEqual(saved['participant_id'], remote['participant_id'])
            self.assertEqual(saved['oauth_id'], row['id'])
            self.assertEqual(saved.get('room_type'), remote['room_type'])
            self.assertTrue(saved['active'])
        self.assertEqual(snapshot['grants']['revoked'], 0)
        # Actual passive refresh must not prune owner DM or other selected rooms.
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('native_connect_probe', Path(__file__).resolve().parents[1] / '__init__.py',
            submodule_search_locations=[str(Path(__file__).resolve().parents[1])])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        from native_connect_probe.oauth import OAuthClient
        from native_connect_probe.storage import Store
        with patch.dict(os.environ, {'HERMES_HOME': str(self.home)}):
            client = OAuthClient(Store(self.home / 'plugin-data/botsesh'))
            client.discover(rooms[primary])
            self.assertEqual(set(client._credential(rooms[primary])['extra']['room_ids']), set(rooms))
            self.assertTrue(all(r['active'] for r in client.store.read()['rooms'].values()))
        self.assertEqual(self.home.joinpath('auth.json').stat().st_mode & 0o777, 0o600)
        return rooms

    def test_requested_room_plus_owner_dm_saves_both(self):
        target = self.ready['room_ids'][0]
        snapshot, _ = self.connect(requested=target)
        rooms = self.assert_grant_saved(snapshot, target)
        self.assertEqual(len(rooms), 2)
        self.assertEqual(sum(r['room_type'] == 'dm' for r in rooms.values()), 1)

    def test_requested_room_plus_other_selected_room_and_dm_keeps_all(self):
        target = self.ready['room_ids'][1]
        snapshot, _ = self.connect(requested=target, selected=self.ready['room_ids'])
        self.assertEqual(len(self.assert_grant_saved(snapshot, target)), 3)

    def test_no_hint_chooses_only_shared_room_not_owner_dm(self):
        snapshot, _ = self.connect()
        self.assert_grant_saved(snapshot, self.ready['room_ids'][0])

    def test_requested_room_absent_fails_and_revokes(self):
        snapshot, output = self.connect(requested=self.ready['room_ids'][0],
            selected=[self.ready['room_ids'][1]], success=False)
        self.assertIn('BotSesh: requested_room_not_authorized', output)
        self.assertEqual(snapshot['grants'], {'total': 1, 'revoked': 1})
        self.assertFalse((self.home / 'auth.json').exists())
        path = self.home / 'plugin-data/botsesh/rooms.json'
        self.assertFalse(json.loads(path.read_text())['rooms'] if path.exists() else {})

    def test_no_hint_multiple_shared_rooms_remains_ambiguous_and_revokes(self):
        snapshot, output = self.connect(selected=self.ready['room_ids'], success=False)
        self.assertIn('BotSesh: one_room_required', output)
        self.assertEqual(snapshot['grants'], {'total': 1, 'revoked': 1})

    def test_single_room_form_keeps_the_authorized_owner_dm(self):
        snapshot, _ = self.connect(legacy=True)
        self.assertEqual(len(self.assert_grant_saved(snapshot, self.ready['room_ids'][0])), 2)

    def test_dm_only_grant_without_hint_connects(self):
        snapshot, _ = self.connect(selected=[])
        returned = snapshot['list_rooms'][0]['rooms']
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0]['room_type'], 'dm')
        self.assert_grant_saved(snapshot, returned[0]['room_id'])


if __name__ == '__main__':
    unittest.main()
