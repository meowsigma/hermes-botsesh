"""Optional local unchanged-server proof; no provider/model invocation.

Literal replay is in test_threaded_post_guidance. This HTTP proof changes only
room/receipt IDs to fresh locally owned fixtures, then follows the actual error.
"""
import json
import os
import unittest
from unittest.mock import Mock, patch

import test_python_invitation as fixture
from test_plugin import tools
from test_threaded_post_guidance import FIXTURE, correction
from hermes_botsesh_test.invitation import InvitationClient
from gateway.session_context import set_session_vars, clear_session_vars


@unittest.skipUnless(os.environ.get('BOTSESH_INVITATION_SERVER_COMMAND'), 'explicit local server argv required')
class RealThreadedPostGuidance(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.RealInvitationTests.asyncSetUp
    command = fixture.RealInvitationTests.command
    stop_server = fixture.RealInvitationTests.stop_server

    def call(self, args, room, target):
        tokens = set_session_vars(platform='botsesh', chat_id=room, message_id=str(target))
        try:
            ctx = Mock(); ctx.session_surface.return_value = 'botsesh'
            with patch.object(tools, 'Client', return_value=self.client):
                return json.loads(tools.guarded(tools.post)(args, ctx=ctx))
        finally:
            clear_session_vars(tokens)

    async def test_rejected_attempt_has_zero_read_then_explicit_corrected_thread_is_canonical_once(self):
        room = self.fixture['room_id']
        question = self.command(op='message', room_id=room, text='Saturday repair workshop?')['message']
        joined = self.client.join(self.fixture['link'], dev_origin=self.origin)
        frozen = self.client.enrolled(room, 'read')
        receipt = frozen['receipt_id']
        target = question['number']
        self.client.store.update(lambda d: d['rooms'][room].setdefault('processing_receipts', {}).update({str(target): {
            'state': 'started', 'target': target, 'receipt_id': receipt, 'frozen': frozen}}))
        before = self.command(op='inspect', room_id=room, participant_id=joined['participant_id'])
        self.assertEqual((before['checkpoint'], before['attestations']), (0, 0))
        request = {**FIXTURE['calls'][0], 'room_id': room, 'reply_to': target}
        denied = self.call(request, room, target)
        self.assertFalse(denied['ok'], denied)
        self.assertIn(receipt, denied['message'])
        after = self.command(op='inspect', room_id=room, participant_id=joined['participant_id'])
        self.assertEqual(after, before, 'Rejected tool call must not send or attest anything')
        fixed = correction(denied)  # This is the caller's explicit read attestation.
        self.assertEqual(fixed, {**request, 'receipt_id': receipt, 'processed': True})
        landed = self.call(fixed, room, target)
        self.assertTrue(landed['ok'], landed)
        self.assertEqual(landed['result']['read_effect'], 'processed_frozen_receipt')
        client_id = landed['result']['client_id']
        binding = self.client.store.room(room)
        message = InvitationClient(self.client.store).rest(binding, 'GET', '/messages/by-client/' + client_id)['message']
        self.assertEqual(message['text'].encode(), request['text'].encode())
        self.assertEqual(message['senderParticipantId'], joined['participant_id'])
        self.assertEqual(message['replyPreview']['number'], target)
        again = self.call({**fixed, 'client_id': client_id}, room, target)
        self.assertTrue(again['ok'], again)
        final = self.command(op='inspect', room_id=room, participant_id=joined['participant_id'])
        self.assertEqual(len(final['messages']), 2)
        self.assertEqual(final['checkpoint'], question['id'])
        self.assertGreater(final['attestations'], 0)
        self.command(op='revoke', room_id=room, participant_id=joined['participant_id'])
        revoked = self.call({**fixed, 'client_id': client_id}, room, target)
        self.assertFalse(revoked['ok'], revoked)
        evidence = {'basis': 'real loopback server, scripted caller, not native model evidence',
                    'provider_requests': 0, 'rejection': denied, 'landed': landed,
                    'exact_text_actor_thread_verified': True, 'retry_single_message': True,
                    'read_checkpoint_before': before['checkpoint'], 'read_checkpoint_after': final['checkpoint'],
                    'attestations_before': before['attestations'], 'attestations_after': final['attestations'],
                    'revoked_error': revoked['error']}
        path = os.environ.get('BOTSESH_THREADED_EVIDENCE')
        if path:
            from pathlib import Path
            Path(path).write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    unittest.main()
