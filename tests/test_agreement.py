"""Agreement is a room-only action, not a send/read/approval. Zero inference."""
import os
import unittest
from unittest.mock import Mock, patch
import test_living_chat as fixture
import test_confirmed_pass as core_fixture
import test_reply_argument_slip as dispatch
from hermes_botsesh_test import tools
from hermes_botsesh_test.oauth import OAuthClient
from hermes_botsesh_test.client import BotSeshError
from gateway.platforms.event import ProcessingOutcome

class AgreementTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LivingChatTests.asyncSetUp
    begin = fixture.LivingChatTests.begin
    model_ended = fixture.LivingChatTests.model_ended
    prepare = core_fixture.ConfirmedPassTests.prepare
    core_call = dispatch.ReplyArgumentSlipTests.core_call

    def wire(self):
        original = OAuthClient.rpc
        def rpc(api, binding, name, args=None, **kwargs):
            if name == 'agree':
                self.requests.append((binding['room_id'], name, args))
                return {'number': args['number'], 'version': args['version'], 'active': args['active'],
                        'participantId': binding['participant_id'], 'read_effect': 'none', 'wake_effect': 'none'}
            return original(api, binding, name, args, **kwargs)
        p = patch.object(OAuthClient, 'rpc', rpc);p.start();self.addCleanup(p.stop)

    async def test_registered_agree_suppresses_automatic_confirmation_without_read_or_interrupt(self):
        self.wire();adapter, _ = await self.prepare()
        result, agent, _ = self.core_call({'room_id': 'a', 'number': 3, 'version': 0, 'active': True}, name='botsesh_agree')
        self.assertTrue(result['ok'], result);agent.interrupt.assert_not_called()
        self.assertEqual(result['result']['read_effect'], 'none')
        self.assertNotIn('read_attested', self.client.store.room('a')['processing_receipts']['3'])
        self.model_ended(adapter)
        await adapter.send('a', 'I agree with the answer.', metadata={'notify': True})
        event=adapter._admit_event.call_args.args[0]
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.messages, {})
        self.assertNotIn('a', adapter._current)
        self.assertTrue(any(n == 'hermes_delivery' and a['state'] == 'completed_unread' for _, n, a in self.requests))
        self.assertNotIn('processed_cursor', self.client.store.room('a'))

    async def test_rejected_agreement_preserves_nonempty_automatic_final(self):
        adapter, _ = await self.prepare()
        original = OAuthClient.rpc
        def reject(api, binding, name, args=None, **kwargs):
            if name == 'agree':
                raise BotSeshError('message_version_conflict', status=409)
            return original(api, binding, name, args, **kwargs)
        with patch.object(OAuthClient, 'rpc', reject):
            result, _, _ = self.core_call({'room_id': 'a', 'number': 3, 'version': 0, 'active': True}, name='botsesh_agree')
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'], 'message_version_conflict')
        self.assertNotIn('explicit_agreement', self.client.store.room('a')['processing_receipts']['3'])
        self.model_ended(adapter)
        await adapter.send('a', 'A useful final after the stale agreement.', metadata={'notify': True})
        event = adapter._admit_event.call_args.args[0]
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual([m['text'] for m in self.messages.values()], ['A useful final after the stale agreement.'])
        self.assertFalse(any(n == 'hermes_delivery' and a['state'] == 'completed_unread' for _, n, a in self.requests))

    async def test_mismatched_agreement_response_is_not_journaled(self):
        await self.prepare()
        with patch.object(OAuthClient, 'rpc', return_value={'active': False}):
            result, _, _ = self.core_call({'room_id': 'a', 'number': 3, 'version': 0, 'active': True}, name='botsesh_agree')
        self.assertEqual(result['error'], 'invalid_agreement_response')
        self.assertNotIn('explicit_agreement', self.client.store.room('a')['processing_receipts']['3'])

    async def test_agreement_can_be_followed_by_explicit_processed_pass_or_new_useful_post(self):
        self.wire();adapter, args = await self.prepare()
        result, _, _ = self.core_call({'room_id': 'a', 'number': 3, 'version': 0, 'active': True}, name='botsesh_agree')
        self.assertTrue(result['ok'])
        result, agent, _ = self.core_call(args)
        self.assertTrue(result['ok'], result)
        agent.interrupt.assert_called_once_with(hard_cancel=True, tool_reason='botsesh_passed')
        self.assertEqual(self.messages, {})

    async def test_agreement_does_not_suppress_deliberate_new_contribution(self):
        self.wire();adapter, args = await self.prepare()
        result, _, _ = self.core_call({'room_id': 'a', 'number': 3, 'version': 0, 'active': True}, name='botsesh_agree')
        self.assertTrue(result['ok'])
        result, agent, _ = self.core_call({**args, 'text': 'A genuinely new fact.'}, name='botsesh_post')
        self.assertTrue(result['ok'], result);agent.interrupt.assert_not_called()
        self.assertEqual([m['text'] for m in self.messages.values()], ['A genuinely new fact.'])

    async def test_foreign_scope_and_invalid_values_do_not_reach_transport(self):
        self.wire();await self.prepare()
        result, _, _ = self.core_call({'room_id': 'b', 'number': 3, 'version': 0, 'active': True}, name='botsesh_agree')
        self.assertEqual(result['error'], 'cross_room_denied')
        self.assertFalse(any(n == 'agree' for _, n, _ in self.requests))
        with patch.object(tools, 'Client', return_value=self.client), patch.object(tools, 'calling_scope', return_value=('cli', '')):
            for value in (-1, True, None):
                with self.assertRaises(BotSeshError):tools.agree({'room_id': 'a', 'number': value, 'version': 0, 'active': True}, Mock())

    async def test_failed_runtime_after_agreement_never_attests_reading(self):
        self.wire();adapter, _ = await self.prepare()
        result, _, _ = self.core_call({'room_id': 'a', 'number': 3, 'version': 0, 'active': True}, name='botsesh_agree')
        self.assertTrue(result['ok'])
        self.model_ended(adapter, failed=True)
        await adapter._finish_current('a', adapter._current['a'], 'runtime_error')
        self.assertEqual(self.messages, {})
        self.assertTrue(any(n == 'hermes_delivery' and a['state'] == 'failed' for _, n, a in self.requests))
        self.assertNotIn('processed_cursor', self.client.store.room('a'))

import test_python_invitation as http_fixture
@unittest.skipUnless(os.environ.get('BOTSESH_INVITATION_SERVER_COMMAND'), 'explicit local server argv required')
class AgreementHTTPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = http_fixture.RealInvitationTests.asyncSetUp
    command = http_fixture.RealInvitationTests.command
    stop_server = http_fixture.RealInvitationTests.stop_server

    async def test_real_python_invitation_agrees_and_removes_without_node_send_or_read(self):
        room=self.fixture['room_id']
        message=self.command(op='message',room_id=room,text='An already supplied answer Ω')['message']
        joined=self.client.join(self.fixture['link'],dev_origin=self.origin)
        before=self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])
        ctx=Mock();ctx.session_surface.return_value='cli'
        args={'room_id':room,'number':message['number'],'version':0,'active':True}
        with patch.object(tools,'Client',return_value=self.client):
            a=tools.agree(args,ctx);self.assertTrue(a['active']);self.assertEqual(a['participantId'],joined['participant_id'])
            self.assertEqual(tools.agree(args,ctx)['agreements'],a['agreements'])
            after=self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])
            self.assertEqual((after['messages'],after['checkpoint'],after['attestations']),(before['messages'],before['checkpoint'],before['attestations']))
            removed=tools.agree({**args,'active':False},ctx);self.assertEqual(removed['agreements'],[])
            self.command(op='revoke',room_id=room,participant_id=joined['participant_id'])
            with self.assertRaises(BotSeshError):tools.agree(args,ctx)
