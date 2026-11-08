"""Lifecycle unit checks + actual hermes CLI against a real localhost server.

No production writes, credentials, inference or gateway starts. Native fixture
requires explicit executable/app paths and runs hermes with an empty PATH.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
from uuid import uuid4
from test_plugin import Client, Store, BotSeshError
from hermes_botsesh_test import room_lifecycle as lifecycle, tools, guest_manager

ROOM = 'L' * 32


class LifecycleUnit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'store')
        self.client = Client(self.store)
        self.store.update(lambda d: d['rooms'].update({ROOM: {'room_id': ROOM, 'active': True,
            'url': 'https://botsesh.com/b/' + ROOM, 'name': 'Local unit', 'participant_id': 'seat'}}))
        self.api = lifecycle.RoomLifecycle(self.client)

    def test_room_info_redaction_and_compaction_safe_creation_record(self):
        result = lifecycle.creation_result(self.store, {'room_id': ROOM, 'share_link': 'https://botsesh.com/b/' + ROOM})
        self.assertIsNone(result['password'])
        self.assertEqual(result['join_url'], 'https://botsesh.com/b/' + ROOM)
        self.assertIn('--show-password', result['credential_recovery'])
        restarted = Store(self.store.root)
        info = lifecycle.room_info(restarted, ROOM, True)
        self.assertIsNone(info['password'])
        self.assertFalse(info['password_required'])
        self.assertNotIn('password', lifecycle.room_info(restarted, ROOM))
        self.assertEqual(stat.S_IMODE(lifecycle.private_store(self.store).path.stat().st_mode), 0o600)

    def test_rotation_journal_before_network_and_loss_retry_same_password(self):
        calls = []
        def request(room, method, op, payload):
            saved = self.api.private.read()['rotations']
            self.assertTrue(any(r['password'] == payload['password'] and r['client_id'] == payload['client_id'] for r in saved.values()))
            calls.append(payload)
            if len(calls) == 1:
                raise BotSeshError('connection_failed_retry_same_intent')
            return {'room_id': ROOM, 'client_id': payload['client_id'], 'password_required': True}
        with patch.object(self.api, 'request', side_effect=request):
            with self.assertRaises(BotSeshError): self.api.rotate(ROOM)
            result = self.api.rotate(ROOM)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(result['password'], calls[0]['password'])
            self.assertEqual(lifecycle.room_info(self.store, ROOM, True)['password'], result['password'])
            self.assertNotIn(result['password'], json.dumps(lifecycle.room_info(self.store, ROOM)))
            self.api.rotate(ROOM)
            self.assertNotEqual(calls[1]['client_id'], calls[2]['client_id'])

    def test_send_uuid_payload_binding_and_no_read_effect(self):
        import hashlib
        sent = []
        def request(room, method, op, payload):
            sent.append(payload)
            return {'client_id': payload['client_id'], 'number': 0, 'participant_id': 'seat', 'read_effect': 'none',
                    'text_sha256': hashlib.sha256(payload['text'].encode()).hexdigest()}
        with patch.object(self.api, 'request', side_effect=request):
            a = self.api.send(ROOM, 'Exact 🐚\r\n')
            b = self.api.send(ROOM, 'Exact 🐚\r\n')
            self.assertEqual(a, b)
            self.assertEqual(sent[0], sent[1])
            with self.assertRaises(BotSeshError): self.api.send(ROOM, 'Changed', a['client_id'])
            self.assertEqual(len(sent), 2)

    def test_forget_cleans_exact_config_preserves_foreign_rooms_and_credential(self):
        other = 'O' * 32
        self.store.update(lambda d: d['rooms'].update({other: {'room_id': other, 'active': True, 'oauth_id': 'shared', 'transport': 'oauth'}}))
        self.store.update(lambda d: d['rooms'][ROOM].update(oauth_id='shared', transport='oauth'))
        lifecycle.remember_access(self.store, ROOM, 'https://botsesh.com/b/' + ROOM)
        cfg = {'platforms': {'botsesh': {'extra': {'group_allowed_chats': [ROOM, other]}, 'home_channel': {'chat_id': ROOM}}}}
        with patch('hermes_cli.config.load_config', return_value=cfg), patch('hermes_cli.config.set_config_value') as set_cfg, patch('hermes_botsesh_test.oauth._save') as save:
            lifecycle.forget(self.client, [ROOM])
        self.assertEqual(set_cfg.call_args_list[0].args, ('platforms.botsesh.extra.group_allowed_chats', json.dumps([other])))
        self.assertEqual(set_cfg.call_args_list[1].args, ('platforms.botsesh.home_channel', '{}'))
        save.assert_not_called()
        self.assertNotIn(ROOM, self.store.read()['rooms'])
        self.assertIn(other, self.store.read()['rooms'])
        self.assertNotIn(ROOM, lifecycle.private_store(self.store).read()['rooms'])

    def test_forget_dead_retains_transients_and_ordinary_forbidden(self):
        ids = ['A' * 32, 'B' * 32, 'C' * 32, 'D' * 32]
        self.store.update(lambda d: d.update(rooms={rid: {'room_id': rid, 'transport': 'oauth', 'active': True} for rid in ids}))
        errors = [BotSeshError('room_missing', 404), BotSeshError('room_deleted', 410), BotSeshError('invalid_grant', 400), BotSeshError('upstream_busy', 503)]
        with patch('hermes_botsesh_test.oauth.OAuthClient.call', side_effect=errors), patch.object(lifecycle, 'forget', return_value={}) as forget, contextlib.redirect_stdout(io.StringIO()):
            lifecycle.handle(argparse.Namespace(botsesh_action='forget', room=None, dead=True), self.client)
        self.assertEqual(forget.call_args.args[1], ids[:3])
        # A failed rotating refresh removes the shared token and marks all of
        # its rooms. Descendants must prune from that fresh durable marker.
        self.store.update(lambda d: d.update(rooms={rid: {'room_id': rid, 'transport': 'oauth', 'active': True} for rid in ids[:2]}))
        def invalidate(binding, op):
            self.store.update(lambda d: [r.update(active=False, state='authorization_revoked') for r in d['rooms'].values()])
            raise BotSeshError('invalid_grant', 400)
        with patch('hermes_botsesh_test.oauth.OAuthClient.call', side_effect=invalidate) as call, patch.object(lifecycle, 'forget', return_value={}) as forget, contextlib.redirect_stdout(io.StringIO()):
            lifecycle.handle(argparse.Namespace(botsesh_action='forget', room=None, dead=True), self.client)
        self.assertEqual(forget.call_args.args[1], ids[:2])
        self.assertEqual(call.call_count, 1)

    def test_passive_oauth_discovery_cannot_revive_a_forgotten_root(self):
        from hermes_botsesh_test.oauth import OAuthClient
        dm = 'D' * 32
        binding = {'room_id': dm, 'origin': 'https://botsesh.com', 'oauth_id': 'shared', 'oauth_root': ROOM}
        self.store.update(lambda d: d.update(rooms={dm: {**binding, 'active': True}}, forgotten_rooms={ROOM: True}))
        rooms = [{'room_id': ROOM, 'participant_id': 'root-seat', 'name': 'Root', 'display_name': 'Hermes'},
                 {'room_id': dm, 'participant_id': 'dm-seat', 'name': 'DM', 'display_name': 'Hermes', 'source_room_id': ROOM, 'room_type': 'dm'}]
        oauth = OAuthClient(self.store)
        with patch.object(oauth, 'rpc', return_value={'rooms': rooms}), patch.object(oauth, '_credential', return_value={'id':'shared','extra':{}}), patch('hermes_botsesh_test.oauth._save'):
            oauth.discover(binding)
        self.assertNotIn(ROOM, self.store.read()['rooms'])
        self.assertIn(dm, self.store.read()['rooms'])


@unittest.skipUnless(all(os.environ.get(k) for k in ('BOTSESH_TEST_SERVER_APP','BOTSESH_TEST_NODE','BOTSESH_TEST_HERMES')), 'explicit localhost app, node server and hermes executable required')
class NativeCLINoNode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'isolated-home'
        plugin = self.home / 'plugins' / 'botsesh'
        shutil.copytree(Path(__file__).resolve().parents[1], plugin, ignore=shutil.ignore_patterns('.git','__pycache__'))
        empty_path = self.root / 'empty-bin'; empty_path.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('HERMES_', 'OPENAI_', 'ANTHROPIC_', 'BOTSESH_'))}
        self.env.update(HERMES_HOME=str(self.home), PATH=str(empty_path), NO_COLOR='1')
        self.assertIsNone(shutil.which('node', path=self.env['PATH']))
        self.assertIsNone(shutil.which('nodejs', path=self.env['PATH']))
        self.hermes = os.environ['BOTSESH_TEST_HERMES']
        enabled = subprocess.run([self.hermes, 'plugins', 'enable', 'botsesh', '--no-allow-tool-override'], env=self.env, capture_output=True, text=True, timeout=40)
        self.assertEqual(enabled.returncode, 0, enabled.stderr)
        self.server = subprocess.Popen([os.environ['BOTSESH_TEST_NODE'], str(Path(__file__).with_name('hb050-lifecycle-server.mjs')), os.environ['BOTSESH_TEST_SERVER_APP']], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_server)
        line = self.server.stdout.readline()
        self.assertTrue(line, 'localhost server failed to start')
        self.origin = json.loads(line)['origin']

    def stop_server(self):
        if self.server.poll() is None:
            self.server.stdin.write('{"op":"stop"}\n'); self.server.stdin.flush()
        _, err = self.server.communicate(timeout=20)
        self.assertEqual(self.server.returncode, 0, err)

    def command(self, **args):
        self.server.stdin.write(json.dumps(args) + '\n'); self.server.stdin.flush()
        return json.loads(self.server.stdout.readline())

    def cli(self, *args, json_result=True):
        done = subprocess.run([self.hermes, 'botsesh', *args], env=self.env, capture_output=True, text=True, timeout=45)
        self.assertEqual(done.returncode, 0, 'CLI failed: ' + done.stderr)
        if json_result:
            # A create/info/rotate command prints exactly one JSON object; do not
            # log it because it may contain the intentionally revealed password.
            return json.loads(done.stdout)
        return done

    def test_actual_cli_lifecycle_compaction_recovery_exact_send_and_config_cleanup(self):
        made = self.cli('create-room', 'Local CLI room', '--dev-origin', self.origin)
        room = made['room_id']
        self.assertIsNone(made['password'])
        self.assertEqual(made['join_url'], self.origin + '/b/' + room)
        self.assertEqual(self.command(op='inspect', room_id=room)['seats'], 1)
        self.assertEqual(self.cli('create-room', 'Local CLI room', '--dev-origin', self.origin)['room_id'], room)
        self.assertEqual(self.cli('room-info', room, '--show-password')['password'], None)
        self.assertEqual(self.cli('rooms')['rooms'][0]['join_url'], made['join_url'])
        invite = self.cli('invite', room, '--email', 'local-fixture@example.invalid')
        self.assertEqual(invite['delivery'], 'link_only')
        self.assertEqual(self.cli('invites', room)['invitations'][0]['state'], 'waiting')
        self.cli('revoke-invite', room, invite['invitation_id'])
        self.assertEqual(self.cli('invites', room)['invitations'][0]['state'], 'revoked')
        rotated = self.cli('rotate-password', room)
        self.assertTrue(rotated['password'])
        self.assertEqual(self.cli('room-info', room, '--show-password')['password'], rotated['password'])
        self.assertNotIn(rotated['password'], json.dumps(self.cli('rooms')))
        self.assertNotIn(rotated['password'], json.dumps(self.cli('room-info', room)))
        self.assertEqual(self.cli('create-room', 'Local CLI room', '--dev-origin', self.origin)['password'], rotated['password'])
        send = self.cli('send', '--room', room, 'Local exactly-once café 🐚\r\n')
        self.assertEqual(self.cli('send', '--room', room, 'Local exactly-once café 🐚\r\n'), send)
        inspected = self.command(op='inspect', room_id=room)
        self.assertEqual((inspected['messages'], inspected['readCheckpoint'], inspected['passwordRequired']), (1, 0, 1))
        # Call the actual tool function, then reconstruct from a new Store after
        # throwing away its turn result (the compaction failure from feedback).
        store = Store(self.home / 'plugin-data' / 'botsesh')
        context = Mock(); context.session_surface.return_value = 'cli'
        with patch.object(tools, 'Client', return_value=Client(store)), patch.object(guest_manager, 'DEFAULT_ORIGIN', self.origin):
            tool_result = tools.create_room({'name': 'Local tool room', 'manage_admission': True}, context)
        tool_room = tool_result['room_id']
        self.assertIn('password', tool_result)
        del tool_result
        self.assertIsNone(lifecycle.room_info(Store(store.root), tool_room, True)['password'])
        self.assertEqual(self.cli('invite', tool_room)['room_id'], tool_room)
        dead = self.cli('create-room', 'Local expired room', '--dev-origin', self.origin)['room_id']
        for key, value in [('platforms.botsesh.extra.group_allowed_chats', json.dumps([room, tool_room, dead])), ('platforms.botsesh.home_channel', json.dumps({'platform':'botsesh','chat_id':room,'name':'Local CLI room'}))]:
            done = subprocess.run([self.hermes, 'config', 'set', key, value], env=self.env, capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.command(op='expire', room_id=dead)
        pruned = self.cli('forget', '--dead')
        self.assertEqual(pruned['forgotten'], [dead])
        self.assertNotIn(dead, store.read()['rooms'])
        forgotten = self.cli('forget', room)
        self.assertEqual(forgotten['forgotten'], [room])
        self.assertEqual(set(store.read()['rooms']), {tool_room})
        from hermes_yaml import safe_load
        cfg = safe_load((self.home / 'config.yaml').read_text())
        self.assertEqual(cfg['platforms']['botsesh']['extra']['group_allowed_chats'], [tool_room])
        self.assertFalse(cfg['platforms']['botsesh'].get('home_channel'))
        for file in store.root.rglob('*.json'):
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.assertNotIn(rotated['password'], json.dumps(lifecycle.private_store(store).read()))
        retry = subprocess.run([self.hermes, 'botsesh', 'create-room', 'Local CLI room', '--dev-origin', self.origin], env=self.env, capture_output=True, text=True, timeout=45)
        self.assertNotEqual(retry.returncode, 0)
        self.assertIn('room_forgotten_use_new_creation_client_id', retry.stderr)
