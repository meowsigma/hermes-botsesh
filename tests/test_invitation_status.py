"""Invitation status and confirmed-send health; no Node or model calls."""
import contextlib
import io
import json
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from test_plugin import Client, Store, BotSeshError
from hermes_botsesh_test import invitation, ops_status


class InvitationStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = Client(Store(Path(self.tmp.name) / 'profile'))
        self.api = invitation.InvitationClient(self.client.store)
        self.binding = {'transport': 'invitation', 'origin': 'https://botsesh.com',
                        'url': 'https://botsesh.com/b/room', 'room_id': 'room',
                        'participant_id': 'seat', 'membership_id': 'member',
                        'active': True, 'name': 'Hermes',
                        'health': {'state': 'connected', 'last_check': time.time()}}
        self.client.store.update(lambda d: d['rooms'].update(room=self.binding))
        self.api.private.update(lambda d: d['rooms'].update(room={
            **{k: self.binding[k] for k in ('origin', 'room_id', 'participant_id', 'membership_id')},
            'access_token': 'bsa_' + 's' * 43}))
        self.value = {'membership': {'id': 'member', 'participantId': 'seat', 'expiresAt': 9999999999999},
                      'participantId': 'seat', 'accessExpiresAt': 9999999999999, 'readCheckpoint': 0}

    def test_exact_invitation_access_probe_is_read_only_and_secret_free(self):
        import httpx
        original = httpx.Client
        requests = []
        def handler(request):
            requests.append(request)
            self.assertEqual(request.method, 'GET')
            self.assertEqual(str(request.url), 'https://botsesh.com/api/rooms/room/agent-access')
            self.assertEqual(request.headers['Authorization'], 'Bearer bsa_' + 's' * 43)
            return httpx.Response(200, json=self.value)
        before = self.client.store.read(), self.api.private.read()
        with patch('httpx.Client', side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(handler))), \
             patch('hermes_botsesh_test.oauth.OAuthClient.rpc', side_effect=AssertionError('OAuth forbidden')), \
             patch.object(Client, 'command', side_effect=AssertionError('Node forbidden')):
            self.assertEqual(ops_status.access_state(self.client, 'room', self.binding), ('alive', True))
        self.assertEqual(len(requests), 1)
        self.assertEqual((self.client.store.read(), self.api.private.read()), before)

    def test_invitation_denied_revoked_deleted_and_identity_mismatch(self):
        for code, http_status, expected in (
                ('request_denied', 403, 'request_denied'),
                ('membership_revoked', 403, 'membership_revoked'),
                ('access_revoked', 401, 'access_revoked'),
                ('room_not_found', 404, 'deleted'),
                ('room_deleted', 410, 'deleted'),
                ('room_expired', 410, 'deleted')):
            with self.subTest(code=code), patch.object(invitation, '_http', side_effect=BotSeshError(code, http_status)):
                self.assertEqual(ops_status.access_state(self.client, 'room', self.binding), (expected, False))
        for field, value in (('participantId', 'foreign'), ('membership', {**self.value['membership'], 'id': 'foreign'})):
            with self.subTest(field=field), patch.object(invitation, '_http', return_value={**self.value, field: value}):
                self.assertEqual(ops_status.access_state(self.client, 'room', self.binding), ('identity_mismatch', False))
        self.api.private.update(lambda d: d['rooms']['room'].update(participant_id='foreign'))
        with patch.object(invitation, '_http') as request:
            self.assertEqual(ops_status.access_state(self.client, 'room', self.binding), ('identity_mismatch', False))
            self.assertEqual(ops_status.access_state(self.client, 'foreign-room', self.binding), ('identity_mismatch', False))
            request.assert_not_called()

    def test_healthy_invitation_no_forget_and_access_is_not_execution_proof(self):
        for channel, check, expected in (('up', time.time(), 'connected'), ('down', time.time(), 'down'),
                                         ('up', time.time() - 100, 'stale_or_offline')):
            self.client.store.update(lambda d: d['rooms']['room']['health'].update(last_check=check, last_reply=123))
            with self.subTest(channel=channel, expected=expected), \
                 patch.object(invitation, '_http', return_value=self.value), \
                 patch.object(Client, 'enrolled', return_value={'backlog': 0}):
                report = ops_status.status_report(self.client, {}, runtime={'running': True, 'channel': channel, 'profile': 'fixture'})
                row = report['rooms'][0]
                self.assertEqual((row['access'], row['gateway'], row['forget_hint']), ('alive', expected, None))
                out = io.StringIO()
                with contextlib.redirect_stdout(out): ops_status.print_status(report)
                self.assertNotIn('forget', out.getvalue())
                self.assertNotIn('bsa_', out.getvalue())

    def test_guest_manager_stays_management_only(self):
        binding = {**self.binding, 'transport': 'guest_manager'}
        self.client.store.update(lambda d: d['rooms'].update(room=binding))
        with patch.object(Client, 'enrolled', return_value={'state': 'verified'}) as call:
            report = ops_status.status_report(self.client, {}, runtime={'channel': 'up'})
        row = report['rooms'][0]
        self.assertEqual((row['access'], row['alive'], row['gateway']), ('membership_verified', False, 'not_supported_management_only'))
        call.assert_called_once_with('room', 'verify')

    def test_confirmed_explicit_post_finish_and_recovery_stamp_health(self):
        for op, result in (('post', {'number': 1, 'read_effect': 'none'}),
                           ('post', {'number': 2, 'read_effect': 'processed_frozen_receipt'}),
                           ('finish', {'number': 3, 'reply_number': 3, 'read_effect': 'processed_frozen_receipt'}),
                           ('retry', {'number': 4, 'read_effect': 'processed_frozen_receipt'})):
            with self.subTest(op=op, result=result), patch.object(invitation.InvitationClient, 'call', return_value=result), \
                 patch('time.time', return_value=1234567890):
                self.client.enrolled('room', op)
            self.assertEqual(self.client.store.room('room')['health'].get('last_reply'), 1234567890)
            self.client.store.update(lambda d: d['rooms']['room']['health'].pop('last_reply'))

    def test_failures_quiet_pass_checks_and_handling_do_not_claim_sent_reply(self):
        for op, result in (('post', {'number': None}), ('post', {'number': True}),
                           ('finish', {'reply_number': None, 'nothing_to_add': True}),
                           ('finish', {'failure': {'status': 'cant_do'}, 'read_effect': 'none'}),
                           ('check', {'number': 8}), ('verify', {'participantId': 'seat'})):
            with self.subTest(op=op, result=result), patch.object(invitation.InvitationClient, 'call', return_value=result):
                self.client.enrolled('room', op)
            self.assertNotIn('last_reply', self.client.store.room('room')['health'])
        for code in ('connection_failed_retry_same_intent', 'send_not_confirmed_retry_same_intent', 'request_denied'):
            with self.subTest(code=code), patch.object(invitation.InvitationClient, 'call', side_effect=BotSeshError(code)):
                with self.assertRaises(BotSeshError): self.client.enrolled('room', 'post')
            self.assertNotIn('last_reply', self.client.store.room('room')['health'])
        from hermes_botsesh_test.adapter import BotSeshAdapter
        adapter = object.__new__(BotSeshAdapter)
        adapter.client = self.client
        adapter._mark_handled('room', 1, state='passed')
        self.assertNotIn('last_reply', self.client.store.room('room')['health'])
