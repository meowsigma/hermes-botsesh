"""Combined check-in/agree/receipt seams. Offline, never model evidence."""
import copy
import json
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_checkins as checkin_fixture
from hermes_botsesh_test import checkins, receipt_handles, tool_policy, tools
from hermes_botsesh_test.oauth import OAuthClient


class CheckinAgreementSeams(unittest.IsolatedAsyncioTestCase):
    setUp = checkin_fixture.CheckinInputs.setUp
    current = checkin_fixture.CheckinInputs.current

    def scope(self):
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
        return patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default))

    def test_scheduled_inventory_and_independent_veto_stay_three_not_four(self):
        current = self.current()
        current['hook_turn'] = ('s', 't')
        self.a._guest_tools_override = 'on'
        self.client.enrolled = Mock(return_value=self.policy)
        expected = {'botsesh_read', 'botsesh_post', 'botsesh_pass'}
        for override in ('server', 'on', 'off'):
            self.a._guest_tools_override = override
            with self.scope():
                for lazy in (False, True):
                    request = {'tools': [{'type': 'function', 'function': {'name': name}} for name in
                                        (['tool_call'] if lazy else list(expected | {'botsesh_agree', 'terminal'}))]}
                    filtered = tool_policy.restrict_request(request=request, session_id='s', turn_id='t')['request']['tools']
                    if lazy:
                        branches = filtered[0]['function']['parameters']['properties']['calls']['items']['oneOf']
                        actual = {branch['properties']['name']['enum'][0] for branch in branches}
                    else:
                        actual = {spec['function']['name'] for spec in filtered}
                    self.assertEqual(actual, expected)
                for name in expected:
                    self.assertIsNone(tool_policy.enforce_tools(tool_name=name, session_id='s', turn_id='t'))
                for name in ('botsesh_agree', 'botsesh_propose_checkin', 'terminal'):
                    self.assertEqual(tool_policy.enforce_tools(tool_name=name, session_id='s', turn_id='t')['action'], 'block')
            text = tool_policy.turn_context(self.policy, override)
            self.assertNotIn('botsesh_agree', text)
            self.assertIn('botsesh_pass', text)

    def test_ordinary_guest_keeps_agree_and_owner_tools_restore_after_service(self):
        service = copy.deepcopy(self.policy)
        self.current()['hook_turn'] = ('s', 't')
        ordinary = copy.deepcopy(service)
        ordinary.pop('service_input')
        ordinary['requester'] = {'id': 'guest', 'isOwner': False}
        self.assertIn('botsesh_agree', tool_policy.turn_context(ordinary))
        owner = dict(ordinary, requester={'id': 'person', 'isOwner': True}, author_is_agent_owner=True,
                     batch_all_authors_are_agent_owner=True, toolsAllowed=True)
        self.a._current['room']['tool_policy'] = owner
        request = {'tools': [{'type': 'function', 'function': {'name': 'terminal'}}]}
        with self.scope():
            self.assertIsNone(tool_policy.restrict_request(request=request, session_id='s', turn_id='t'))
        self.assertFalse(tool_policy.host_tools_allowed(service, 'on'))

    async def test_service_admission_binds_receipt_before_live_policy_and_keeps_raw_author(self):
        self.frozen['batch'].update(hasMore=False, nextCheckpoint=3)
        self.client.store.update(lambda d: d['rooms']['room'].update(oauth_receipts={
            'batch': {'participant_id': 'seat', 'numbers': [3], 'expires': time.time() + 300}}))
        policies = []
        def wire(api, binding, op, **args):
            if op == 'check':
                return {'mentions': [3], 'replies': []}
            if op == 'read':
                return self.frozen
            if op == 'tool_policy':
                # Exercise the same original-input identity fence as Client,
                # including the FIRST service-identity authorization lookup.
                receipt_handles.require_bound(self.client.store.room('room'), 3, args['receipt_id'])
                self.assertEqual(args['target'], 3)
                policies.append(dict(args))
                return self.policy
            self.fail('Unexpected transport operation: ' + op)
        self.a._admit_event = AsyncMock(return_value=True)
        with patch.object(OAuthClient, 'call', wire):
            await self.a._check_room('room')
        self.a._admit_event.assert_awaited_once()
        event = self.a._admit_event.call_args.args[0]
        self.assertEqual(event.user_id, 'person')  # Gateway authorization only.
        self.assertIsNone(event.raw_message['senderParticipantId'])
        self.assertIsNone(event.raw_message['authorParticipant'])
        self.assertTrue(event.reply_expected)  # Do not weaken the service contract.
        self.assertFalse(event.allow_gateway_control)
        self.assertEqual(len(policies), 2)
        self.assertEqual(policies[0]['client_id'], policies[1]['client_id'])
        row = self.client.store.room('room')['processing_receipts']['3']
        self.assertEqual(row['state'], 'admitted')
        self.assertNotIn('read_attested', row)
        self.assertEqual(json.loads(row['text'].split('\n\n')[0])['receipt_id'], 'r1')
        self.a._restore_native('room', {**row, 'native_admission': {'admission_id': 'id', 'ref': {}}})
        restored = self.a._current['room']['event']
        self.assertEqual(restored.user_id, 'person')
        self.assertIsNone(restored.raw_message['senderParticipantId'])
        self.assertTrue(restored.reply_expected)

    def test_registered_proposal_and_agreement_handlers_are_not_shifted(self):
        ctx = Mock()
        with patch.object(checkins, 'propose', return_value={'marker': 'proposal'}) as proposal, \
                patch.object(tools, 'agree', return_value={'marker': 'agreement'}) as agreement:
            tools.register_tools(ctx)
            registered = {call.kwargs['name']: call.kwargs['handler'] for call in ctx.register_tool.call_args_list}
            self.assertEqual(len(registered), len(tools.SCHEMAS))
            self.assertEqual(json.loads(registered['botsesh_propose_checkin']({'purpose': 'read updates'}))['result']['marker'], 'proposal')
            self.assertEqual(json.loads(registered['botsesh_agree']({'number': 3}))['result']['marker'], 'agreement')
            proposal.assert_called_once()
            agreement.assert_called_once()
