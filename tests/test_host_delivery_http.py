"""Real local BotSesh server (BOTSESH_INVITATION_SERVER_COMMAND), real Python invitation
seat, the shipped adapter.standalone_send. Zero inference.

Regression for the rc.3 blocker (review t_6748b652): a Hermes cron / send_message
delivery, or a direct Client.enrolled post, after anyone else posted must NOT be refused
with room_moved_on, because freshness is opt-in and only model-authored sends opt in.
Based on the reviewer's probe zz_review_probe_host_delivery.py.
"""
import asyncio
import uuid
from unittest.mock import Mock, patch

from test_python_invitation import RealInvitationTests
from test_plugin import BotSeshError, tools


class HostDeliveryReal(RealInvitationTests):
    # Only the host-delivery cases below belong here; the inherited fixture tests
    # run in test_python_invitation. A non-callable name is never collected.
    test_paste_first_reply_and_automatic_peer_return = None
    test_invitation_status_explicit_post_revoked_and_deleted = None
    test_legacy_retry_no_read_and_revoked_foreign_denial = None

    async def join(self):
        ctx = Mock()
        ctx.session_surface.return_value = 'cli'
        ctx.get_config.return_value = self.origin
        with patch.object(tools, 'Client', return_value=self.client):
            joined = await asyncio.to_thread(tools.join, {'invitation_url': self.fixture['link']}, ctx)
        self.assertEqual(joined['room_id'], self.fixture['room_id'])
        return joined, ctx

    def texts(self, room, joined):
        return [m['text'] for m in self.command(op='inspect', room_id=room, participant_id=joined['participant_id'])['messages']]

    async def test_host_delivery_after_a_human_message_is_posted(self):
        from hermes_botsesh_test import adapter as adapter_module
        room = self.fixture['room_id']
        joined, _ = await self.join()
        self.command(op='message', room_id=room, text='Human chat after the agent last read')
        with patch.object(adapter_module, 'Client', return_value=self.client):
            result = await adapter_module.standalone_send(None, room, 'Scheduled report: hello', ctx=None)
        self.assertTrue(result.get('success'), result)
        self.assertIn('Scheduled report: hello', self.texts(room, joined))

    async def test_direct_enrolled_post_after_a_human_message_is_posted(self):
        room = self.fixture['room_id']
        joined, _ = await self.join()
        self.command(op='message', room_id=room, text='Human chat before a direct SDK post')
        sent = await asyncio.to_thread(self.client.enrolled, room, 'post', text='Direct SDK post', client_id=str(uuid.uuid4()))
        self.assertIn('number', sent)
        self.assertIn('Direct SDK post', self.texts(room, joined))

    async def test_model_authored_post_is_still_refused_when_it_has_not_seen_the_room(self):
        room = self.fixture['room_id']
        joined, ctx = await self.join()
        self.command(op='message', room_id=room, text='A person spoke; this seat has read nothing')
        with patch.object(tools, 'Client', return_value=self.client):
            with self.assertRaises(BotSeshError) as refused:
                await asyncio.to_thread(tools.post, {'room_id': room, 'text': 'Unseen model words', 'client_id': str(uuid.uuid4())}, ctx)
            self.assertEqual(refused.exception.code, 'room_moved_on')
            self.assertEqual(refused.exception.status, 409)
            self.assertNotIn('Unseen model words', self.texts(room, joined))
            sent = await asyncio.to_thread(tools.post, {'room_id': room, 'text': 'Deliberate override', 'client_id': str(uuid.uuid4()),
                                                        'allow_stale': True}, ctx)
        self.assertIn('number', sent)
        self.assertIn('Deliberate override', self.texts(room, joined))


del RealInvitationTests
