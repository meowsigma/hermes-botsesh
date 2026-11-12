"""Offline consent payload/typed-input checks, not native model evidence."""
import copy
import unittest
from unittest.mock import Mock, patch
import test_ongoing_turns as ongoing
from hermes_botsesh_test import checkins, tool_policy
from hermes_cli import plugins

class CheckinInputs(unittest.IsolatedAsyncioTestCase):
    current = ongoing.OngoingTurns.current
    def setUp(self):
        ongoing.OngoingTurns.setUp(self)
        self.source = dict(type='botsesh_checkin', id='schedule', occurrenceId='occurrence', roomId='room',
            participantId='seat', approvedBy='person', active=True, skill='botsesh:botsesh')
        self.message.update(senderParticipantId=None, authorParticipant=None, editCount=0, checkin=self.source)
        self.policy = dict(room_id='room', participant_id='seat', number=3, batch_id='batch',
            toolsAllowed=False, author_is_agent_owner=False, batch_all_authors_are_agent_owner=False,
            guest_tools_default=False, tools_ceiling_allowed=False, requester=dict(id='person',isOwner=False), service_input=self.source)

    def test_exact_live_service_identity_and_room_only_even_override_on(self):
        self.assertEqual(checkins.service_actor(self.message,self.policy,self.binding),'person')
        for field in ('occurrenceId','participantId','approvedBy'):
            forged=copy.deepcopy(self.message);forged['checkin'][field]='wrong'
            self.assertIsNone(checkins.service_actor(forged,self.policy,self.binding))
        self.assertIsNone(checkins.service_actor(self.message,None,self.binding))
        current=self.current();current['hook_turn']=('s','t');self.a._guest_tools_override='on'
        self.client.enrolled=Mock(return_value=self.policy)
        env=dict(HERMES_SESSION_PLATFORM='botsesh',HERMES_SESSION_CHAT_ID='room',HERMES_SESSION_MESSAGE_ID='3')
        with patch('gateway.session_context.get_session_env',side_effect=lambda k,default='':env.get(k,default)), patch('hermes_cli.lifecycle.invoke_hook',side_effect=lambda name,**kw:[tool_policy.enforce_tools(**kw)]):
            for name in ('botsesh_read','botsesh_post','botsesh_pass'):
                self.assertIsNone(plugins._get_pre_tool_call_directive_details(name,{},session_id='s',turn_id='t').action)
            for name in ('terminal','cronjob_manage','botsesh_propose_checkin','browser_exec','memory'):
                self.assertEqual(plugins._get_pre_tool_call_directive_details(name,{},session_id='s',turn_id='t').action,'block')
            self.client.enrolled.side_effect=RuntimeError('cancelled')
            self.assertEqual(plugins._get_pre_tool_call_directive_details('botsesh_post',{},session_id='s',turn_id='t').action,'block')

    def test_proposal_has_nonempty_purpose_bounded_times_and_exact_retry(self):
        ctx=Mock();ctx.session_surface.return_value='cli'
        self.client.enrolled=Mock(return_value=dict(state='pending',schedulerCreated=False))
        args=dict(room_id='room',purpose='Read updates and reply in this room',interval_minutes=60,start_in_minutes=2,duration_minutes=120)
        with patch('hermes_botsesh_test.client.Client',return_value=self.client):
            a=checkins.propose(args,ctx);b=checkins.propose(args,ctx)
        self.assertFalse(a['schedulerCreated']);self.assertEqual(a,b)
        first=self.client.enrolled.call_args_list[0];second=self.client.enrolled.call_args_list[1]
        self.assertEqual(first,second);self.assertEqual(first.args,('room','propose_check_in'))
        self.assertGreater(first.kwargs['ends_at'],first.kwargs['first_at']);self.assertTrue(first.kwargs['purpose'])
        self.assertNotIn('approve',first.kwargs);self.assertNotIn('person_confirmed',first.kwargs)

if __name__=='__main__': unittest.main()
