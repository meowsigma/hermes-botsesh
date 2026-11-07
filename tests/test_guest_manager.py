"""Narrow offline wire + real local server checks; not live/model/browser proof."""
import asyncio
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
from test_plugin import Client, Store, BotSeshError
from hermes_botsesh_test import guest_manager as guest

ROOM = 'A' * 32
OTHER = 'B' * 32
BEARER = 'bsa_' + 'a' * 43


def created():
    return {'state': 'created', 'room_id': ROOM, 'name': 'Plants',
            'share_link': 'https://botsesh.com/b/' + ROOM, 'creator_role': 'agent_manager',
            'participant_id': 'agent', 'owner_state': 'no_human_owner_yet',
            'expires_at': 1900000000000, 'requires_human': 'A human must accept.',
            'membership': {'id': 'membership', 'participantId': 'agent', 'expiresAt': 1900000000000},
            'access': {'token': BEARER, 'expiresAt': 1900000000000}}


def live():
    return {'membership': created()['membership'], 'participantId': 'agent',
            'readCheckpoint': 0, 'accessExpiresAt': 1900000000000}


class GuestWire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'profile')
        self.client = Client(self.store)
        self.httpx = httpx.Client
        self.requests = []
        self.creation = created()
        self.membership = live()
        self.status, self.body, self.headers = 200, {}, {}
        self.lose = False
        self.transport = patch('httpx.Client', side_effect=lambda **kw: self.httpx(**kw, transport=httpx.MockTransport(self.handle)))
        self.transport.start()
        self.addCleanup(self.transport.stop)
        bridge = patch.object(Client, 'command', side_effect=AssertionError('Python guest called Node'))
        bridge.start()
        self.addCleanup(bridge.stop)

    def handle(self, req):
        self.requests.append(req)
        if req.url.path == '/api/agent/rooms':
            self.assertNotIn('cookie', req.headers)
            self.assertNotIn('authorization', req.headers)
            request = json.loads(req.content)
            saved = Store(self.store.credentials / 'python-guest').read()
            self.assertTrue(any(row['request'] == request for row in saved['creations'].values()), 'intent durable BEFORE HTTP')
            if self.lose:
                self.lose = False
                raise httpx.ReadError('private response deliberately lost')
            return httpx.Response(201, json=self.creation)
        self.assertEqual(req.headers['authorization'], 'Bearer ' + BEARER)
        self.assertNotIn('cookie', req.headers)
        if req.url.path.endswith('/agent-access'):
            return httpx.Response(200, json=self.membership)
        return httpx.Response(self.status, json=self.body, headers=self.headers)

    def test_durable_loss_retry_uuid_payload_and_private_output(self):
        operation = str(uuid4()).upper()
        self.lose = True
        with self.assertRaises(BotSeshError): self.client.create('Plants', 'Exact café 🐚', operation)
        restarted = Client(Store(self.store.root))
        result = restarted.create('Plants', 'Exact café 🐚', operation)
        self.assertEqual(restarted.create('Plants', 'Exact café 🐚', operation), result)
        bodies = [r.content for r in self.requests if r.url.path == '/api/agent/rooms']
        self.assertEqual(bodies, [bodies[0]] * 3)
        self.assertEqual(json.loads(bodies[0])['client_id'], operation)
        self.assertEqual(json.loads(bodies[0])['purpose'], 'Exact café 🐚')
        private = Store(self.store.credentials / 'python-guest')
        self.assertEqual(stat.S_IMODE(private.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(private.root.stat().st_mode), 0o700)
        self.assertNotIn(BEARER, json.dumps(result))
        self.assertNotIn('connection_key', json.dumps(result))
        self.assertNotIn(BEARER, self.store.path.read_text())
        self.assertNotIn('oauth', json.dumps(self.store.read()))
        self.assertEqual(self.store.room(ROOM)['transport'], 'guest_manager')

    def test_default_intent_stable_changed_payload_explicit_uuid_rejected_before_http(self):
        self.client.create('Plants')
        self.client.create('Plants')
        bodies = [json.loads(r.content) for r in self.requests if r.url.path == '/api/agent/rooms']
        self.assertEqual(bodies[0], bodies[1])
        operation = str(uuid4())
        # Different room in this synthetic response for a distinct new intention.
        self.creation = {**created(), 'room_id': OTHER, 'share_link': 'https://botsesh.com/b/' + OTHER}
        self.client.create('Another', client_id=operation)
        before = len(self.requests)
        for bad in ('bad', '', str(uuid4()).replace('-', '')):
            with self.assertRaises(BotSeshError): self.client.create('Plants', client_id=bad)
        with self.assertRaises(BotSeshError) as error: self.client.create('Changed', client_id=operation)
        self.assertEqual(error.exception.code, 'client_id_conflict')
        self.assertEqual(len(self.requests), before)

    def test_creation_identity_validation_and_live_verification_before_share(self):
        variants = [lambda d: d.update(share_link='https://foreign.invalid/b/' + ROOM),
                    lambda d: d['membership'].update(participantId='foreign'),
                    lambda d: d.update(participant_id='foreign'),
                    lambda d: d['membership'].update(id=''),
                    lambda d: d['access'].update(token='not-a-bearer'),
                    lambda d: d.update(room_id='../foreign')]
        for mutate in variants:
            self.creation = copy.deepcopy(created()); mutate(self.creation)
            with self.subTest(response=self.creation.keys()), self.assertRaises(BotSeshError): self.client.create('Plants')
            self.assertEqual(self.store.read()['rooms'], {})
        self.creation = created()
        self.membership['membership'] = {**live()['membership'], 'id': 'wrong'}
        with self.assertRaises(BotSeshError): self.client.create('Plants')
        self.assertEqual(self.store.read()['rooms'], {})
        self.membership = live()
        self.assertEqual(self.client.create('Plants')['share_link'], created()['share_link'])
        self.store.update(lambda d: d['rooms'][ROOM].update(active=False))
        with self.assertRaises(BotSeshError): self.client.create('Plants')

    def test_manager_scoping_allowlist_safe_errors_and_expiry_not_detachment(self):
        self.client.create('Plants')
        for op in ('read', 'post', 'invite', 'listen', 'dm_context', 'accept_ownership'):
            with self.assertRaises(BotSeshError) as e: self.client.enrolled(ROOM, op)
            self.assertEqual(e.exception.code, 'guest_management_only_separate_room_connection_required')
        self.status, self.body, self.headers = 410, {'code': 'offer_ended', 'private': BEARER}, {'retry-after': '2'}
        with self.assertRaises(BotSeshError) as error:
            self.client.enrolled(ROOM, 'offer_ownership', client_id=str(uuid4()), user_requested=True)
        self.assertEqual((error.exception.code, error.exception.retry_after_ms), ('offer_ended', 2000))
        self.assertTrue(self.store.room(ROOM)['active'])
        self.assertNotIn(BEARER, str(error.exception))
        binding = self.store.room(ROOM)
        for field, value in [('origin', 'https://foreign.invalid'), ('room_id', OTHER), ('participant_id', 'foreign'), ('membership_id', 'foreign'), ('url', 'https://botsesh.com/b/' + OTHER)]:
            wrong = {**binding, field: value}
            before = len(self.requests)
            with self.assertRaises(BotSeshError): guest.GuestManagerClient(self.store).call(wrong, 'verify')
            self.assertEqual(len(self.requests), before)
        for path in ('/api/rooms/' + ROOM + '/ownership-offers/x/accept', '/oauth/token'):
            with self.assertRaises(BotSeshError): guest._http('https://botsesh.com', 'POST', path, bearer=BEARER)

    def test_tampered_saved_payload_and_uuid_fail_before_http(self):
        self.client.create('Plants')
        private = guest.GuestManagerClient(self.store).private
        intent_key = next(iter(private.read()['creations']))
        original = copy.deepcopy(private.read()['creations'][intent_key])
        before = len(self.requests)
        for field, value in [('name', 'Changed'), ('client_id', 'bad-uuid')]:
            private.update(lambda d: d['creations'][intent_key]['request'].update({field: value}))
            with self.assertRaises(BotSeshError): self.client.create('Plants')
            self.assertEqual(len(self.requests), before)
            private.update(lambda d: d['creations'].update({intent_key: copy.deepcopy(original)}))

    def test_legacy_sdk_intent_fails_closed_without_new_connection_or_http(self):
        payload = ['Plants', '', None]
        legacy_key = 'create-' + hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()
        filename = self.store.credentials / ('botsesh-agent-' + hashlib.sha256(('https://botsesh.com\0' + legacy_key).encode()).hexdigest() + '.json')
        filename.write_text('{}'); filename.chmod(0o600)
        with self.assertRaises(BotSeshError) as error: self.client.create('Plants')
        self.assertEqual(error.exception.code, 'legacy_creation_retry_requires_original_advanced_store')
        self.assertEqual(self.requests, [])
        self.assertFalse((self.store.credentials / 'python-guest' / 'rooms.json').exists())

    def test_cli_status_marks_management_only_and_public_room_omits_secrets(self):
        from hermes_botsesh_test import cli, tools
        self.client.create('Plants')
        public = tools.public_room(self.store.room(ROOM))
        self.assertTrue(public['management_only'])
        self.assertNotIn(BEARER, json.dumps(public))
        output = io.StringIO()
        args = Mock(botsesh_action='status', json=True)
        with patch.object(cli, 'Client', return_value=self.client), patch('sys.stdout', output):
            cli.handle(args)
        report = json.loads(output.getvalue())['rooms'][0]
        self.assertEqual((report['access'], report['gateway']), ('membership_verified', 'not_supported_management_only'))

    def test_guest_binding_does_not_start_gateway_listener(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        self.client.create('Plants')
        adapter = object.__new__(BotSeshAdapter)
        adapter.client, adapter._running, adapter._rooms = self.client, True, {}
        adapter.config = Mock(extra={})
        adapter._discover_at = {}
        async def stop(_): adapter._running = False
        with patch('hermes_cli.config.load_config_readonly', return_value={}), patch('asyncio.sleep', side_effect=stop), patch.object(adapter, '_room_loop', side_effect=AssertionError('management-only listener started')):
            asyncio.run(adapter._sync_rooms_loop())
        self.assertEqual(adapter._rooms, {})


@unittest.skipUnless(os.environ.get('BOTSESH_TEST_SERVER_APP') and os.environ.get('BOTSESH_TEST_NODE'), 'explicit real-server fixture paths required')
class RealServerNoNode(unittest.TestCase):
    def setUp(self):
        self.assertIsNone(shutil.which('node'))
        self.assertIsNone(shutil.which('nodejs'))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'profile')
        self.client = Client(self.store)
        self.server = subprocess.Popen([os.environ['BOTSESH_TEST_NODE'], str(Path(__file__).with_name('guest-manager-server.mjs')), os.environ['BOTSESH_TEST_SERVER_APP']], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        line = self.server.stdout.readline()
        if not line:
            self.fail('local server failed: ' + self.server.stderr.read())
        self.origin = json.loads(line)['origin']
        self.origin_patch = patch.object(guest, 'DEFAULT_ORIGIN', self.origin)
        self.origin_patch.start(); self.addCleanup(self.origin_patch.stop)
        self.bridge = patch.object(Client, 'command', side_effect=AssertionError('Node bridge forbidden'))
        self.bridge.start(); self.addCleanup(self.bridge.stop)

    def stop(self):
        if self.server.poll() is None:
            self.server.stdin.write('{"op":"stop"}\n'); self.server.stdin.flush()
        _, err = self.server.communicate(timeout=20)
        self.assertEqual(self.server.returncode, 0, err)

    def command(self, **args):
        self.server.stdin.write(json.dumps(args) + '\n'); self.server.stdin.flush()
        return json.loads(self.server.stdout.readline())

    def test_create_lost_output_same_retry_offer_expiry_human_acceptance(self):
        real_http = guest._http
        lost = [True]
        def loss(*args, **kw):
            result = real_http(*args, **kw)
            if args[2] == '/api/agent/rooms' and lost[0]:
                lost[0] = False
                raise BotSeshError('connection_failed_retry_same_intent')
            return result
        with patch.object(guest, '_http', side_effect=loss):
            with self.assertRaises(BotSeshError): self.client.create('Plant swap', 'Keep exact 🐚')
        result = self.client.create('Plant swap', 'Keep exact 🐚')
        self.assertEqual(self.client.create('Plant swap', 'Keep exact 🐚'), result)
        room = result['room_id']
        inspected = self.command(op='inspect', room_id=room)
        self.assertEqual((inspected['creations'], inspected['guestRequests'], inspected['authenticatedGuestRequests']), (1, 3, 0))
        operation = self.client.logical_id('ownership-offer', {'room_id': room})
        first = self.client.enrolled(room, 'offer_ownership', client_id=operation, user_requested=True)
        self.assertEqual(self.client.enrolled(room, 'offer_ownership', client_id=operation, user_requested=True), first)
        self.command(op='advance', ms=15*60000+1)
        with self.assertRaises(BotSeshError) as error: self.client.enrolled(room, 'offer_ownership', client_id=operation, user_requested=True)
        self.assertEqual(error.exception.code, 'offer_ended')
        self.assertTrue(self.store.room(room)['active'])
        # Exercise the existing tool-level automatic default-ID renewal.
        from hermes_botsesh_test import tools
        ctx = Mock(); ctx.session_surface.return_value = 'cli'
        with patch.object(tools, 'Client', return_value=self.client):
            offer = tools.offer_ownership({'room_id': room, 'user_requested': True}, ctx)
        self.assertNotEqual(offer['offer_id'], first['offer_id'])
        # Real human-browser REST fixture: local account + join cookie, no bearer.
        # Not a real owner browser walkthrough or a live product proof.
        headers = {'Origin': self.origin, 'X-Board-Request': '1'}
        base = '/api/rooms/' + room
        with httpx.Client(base_url=self.origin, headers=headers, trust_env=False) as human:
            account = human.post('/api/account/register', json={'username': 'localplants', 'password': 'local-only-password-strong'})
            self.assertEqual(account.status_code, 201)
            self.assertEqual(human.post(base + '/join', json={'displayName': 'Plants human'}).status_code, 200)
            token = offer['claim_link'].split('#offer=')[1].split('.')[1]
            path = base + '/ownership-offers/' + offer['offer_id']
            self.assertEqual(human.post(path + '/review', json={'token': token}).status_code, 200)
            accepted = human.post(path + '/accept', json={'token': token, 'account_id': account.json()['account']['id'], 'leave_agent': False})
            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()['state'], 'ownership_accepted')
        inspected = self.command(op='inspect', room_id=room)
        self.assertTrue(inspected['humanOwner']); self.assertTrue(inspected['managerRevoked'])
        self.assertEqual((inspected['accepted'], inspected['audit'], inspected['expires']), (1, 1, None))
        with self.assertRaises(BotSeshError) as error: self.client.enrolled(room, 'offer_ownership', client_id=str(uuid4()), user_requested=True)
        self.assertEqual(error.exception.code, 'agent_manager_revoked')
        self.assertEqual(self.client.enrolled(room, 'verify')['state'], 'verified')

    def test_wrong_revoked_manager_and_revoke_offer(self):
        first = self.client.create('First room', client_id=str(uuid4()))
        second = self.client.create('Second room', client_id=str(uuid4()))
        a, b = first['room_id'], second['room_id']
        operation = str(uuid4())
        offer = self.client.enrolled(a, 'offer_ownership', user_requested=True, client_id=operation)
        self.assertEqual(self.client.enrolled(a, 'revoke_ownership_offer', offer_id=offer['offer_id'])['state'], 'offer_revoked')
        with self.assertRaises(BotSeshError) as error: self.client.enrolled(a, 'offer_ownership', user_requested=True, client_id=operation)
        self.assertEqual(error.exception.code, 'offer_ended')
        private = guest.GuestManagerClient(self.store).private
        original = private.read()['rooms'][a]['access_token']
        foreign = private.read()['rooms'][b]['access_token']
        private.update(lambda d: d['rooms'][a].update(access_token=foreign))
        with self.assertRaises(BotSeshError): self.client.enrolled(a, 'offer_ownership', user_requested=True, client_id=str(uuid4()))
        private.update(lambda d: d['rooms'][a].update(access_token=original))
        self.store.update(lambda d: d['rooms'][a].update(active=True))
        self.command(op='manager_revoke', room_id=a)
        with self.assertRaises(BotSeshError) as error: self.client.enrolled(a, 'offer_ownership', user_requested=True, client_id=str(uuid4()))
        self.assertEqual(error.exception.code, 'agent_manager_revoked')
        self.command(op='member_revoke', room_id=b)
        with self.assertRaises(BotSeshError): self.client.enrolled(b, 'offer_ownership', user_requested=True, client_id=str(uuid4()))
        self.assertFalse(self.store.read()['rooms'][b]['active'])
