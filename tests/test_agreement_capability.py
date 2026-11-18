"""Agreement is offered only when the server's own reads show it exists. Zero inference."""
import copy
import hashlib
import json
import os
import uuid
from pathlib import Path
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_ongoing_turns as ongoing
import test_python_invitation as http_fixture
import test_native_started_at as native_fixture
from hermes_botsesh_test import adapter, agreement, tool_policy, tools
from hermes_botsesh_test import oauth as oauth_mod
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test.oauth import OAuthClient

# sha256 digests of the supported model-visible surface of 54e9f80d (0.8.0-rc.2 + stale-post), computed
# from that commit: HINT; json.dumps(SCHEMAS, sort_keys=True, ensure_ascii=False); ERROR_HINTS without
# agreement_unsupported (same dump); turn_context(GUEST); json.dumps(DENIED, sort_keys=True).
RC2 = {
    'HINT': '4c3d0e47b10774742afd16f96371b0e9c478b8d65a069612a704d8ef5ad66b8e',
    # DM attention (t_40325be8): botsesh_post gains one optional enum parameter, `attention`; its digest is pinned here deliberately.
    'SCHEMAS': 'fd6402683579653b3d65e35373cb84e0b5500e85de82dc8fb14084c3ec49ddd9',
    'ERROR_HINTS': 'eb0921eb2cbcf270d8489e695be3f9e258aac0fd7ab6ecac401d72c459b2d78a',
    'TURN_CONTEXT': 'd0fa4d71a75555e31353fa93a4fe805c13b00a2363d15961a67159be64828c6b',
    'DENIED': 'd7ffa536019ecf2e051eac59185402c16e0ec1091332e6ef6cf855e6af8a9d54',
}
GUEST = dict(room_id='room', participant_id='seat', number=3, batch_id='batch', toolsAllowed=False,
             requester=dict(id='guest', isOwner=False))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Capability(unittest.IsolatedAsyncioTestCase):
    setUp = ongoing.OngoingTurns.setUp
    current = ongoing.OngoingTurns.current

    def mark(self, value):
        self.client.store.update(lambda d: d['rooms']['room'].update(agreement_support=value))

    def scope(self):
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
        return patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default))

    def guest(self):
        current = self.current()
        current['hook_turn'] = ('s', 't')
        current['tool_policy'] = dict(self.policy, toolsAllowed=False)
        return current

    def owner(self):
        current = self.current()
        current['hook_turn'] = ('s', 't')
        current['tool_policy'] = dict(self.policy, toolsAllowed=True, author_is_agent_owner=True,
                                      batch_all_authors_are_agent_owner=True, requester=dict(id='person', isOwner=True))
        return current

    def request(self, lazy=False):
        names = ['tool_call'] if lazy else ['botsesh_read', 'botsesh_post', 'botsesh_pass', 'botsesh_agree', 'terminal']
        declared = [{'type': 'function', 'function': next((dict(s) for s in tools.SCHEMAS if s['name'] == n), {'name': n})} for n in names]
        return {'messages': [{'role': 'system', 'content': adapter.HINT}, {'role': 'user', 'content': 'hello'}], 'tools': declared}

    def names(self, result):
        return [t['function']['name'] for t in result['request']['tools']]

    # --- supported behaviour is the unchanged rc.2 surface -------------------
    def test_supported_surface_is_byte_identical_to_rc2(self):
        self.assertEqual(sha(adapter.HINT), RC2['HINT'])
        self.assertEqual(sha(json.dumps(tools.SCHEMAS, sort_keys=True, ensure_ascii=False)), RC2['SCHEMAS'])
        hints = {k: v for k, v in tools.ERROR_HINTS.items() if k != 'agreement_unsupported'}
        self.assertEqual(sha(json.dumps(hints, sort_keys=True, ensure_ascii=False)), RC2['ERROR_HINTS'])
        self.assertEqual(sha(tool_policy.turn_context(GUEST)), RC2['TURN_CONTEXT'])
        self.assertEqual(sha(json.dumps(tool_policy.DENIED, sort_keys=True)), RC2['DENIED'])
        self.assertIn('botsesh_agree', adapter.HINT)
        self.assertIn('botsesh_agree', next(s for s in tools.SCHEMAS if s['name'] == 'botsesh_post')['description'])

    def test_supported_and_unobserved_keep_agree_in_guest_and_owner_requests(self):
        for state in (True, None):
            if state is not None:
                self.mark(state)
            for build in (self.guest, self.owner):
                build()
                with self.scope():
                    result = tool_policy.restrict_request(request=self.request(), session_id='s', turn_id='t')
                if build == self.owner:
                    self.assertIsNone(result)  # Owner request untouched: nothing rewritten.
                else:
                    self.assertEqual(self.names(result), ['botsesh_read', 'botsesh_post', 'botsesh_pass', 'botsesh_agree'])
                    self.assertEqual(result['request']['messages'], self.request()['messages'])
                    self.assertIn('botsesh_agree', json.dumps(result['request']['tools']))

    # --- proven-absent server --------------------------------------------------
    def test_unsupported_guest_request_has_no_agree_schema_or_text(self):
        self.mark(False)
        self.guest()
        original = self.request()
        before = copy.deepcopy(original)
        with self.scope():
            result = tool_policy.restrict_request(request=original, session_id='s', turn_id='t')
        self.assertEqual(self.names(result), ['botsesh_read', 'botsesh_post', 'botsesh_pass'])
        self.assertEqual(original, before)  # The cached request is never mutated.
        sent = result['request']
        self.assertNotIn('botsesh_agree', json.dumps(sent))
        no_agree = adapter.HINT.replace(agreement.HINT_AGREE, agreement.HINT_NO_AGREE).replace(
            agreement.HINT_FINAL_AGREE, agreement.HINT_FINAL_NO_AGREE)
        self.assertEqual(sent['messages'][0]['content'], no_agree)
        self.assertIn('If a peer already answered, pass silently. ', no_agree)
        self.assertEqual(sent['messages'][1], original['messages'][1])

    def test_unsupported_lazy_guest_wrapper_lists_only_three_branches(self):
        self.mark(False)
        self.guest()
        with self.scope():
            result = tool_policy.restrict_request(request=self.request(lazy=True), session_id='s', turn_id='t')
        wrapper = result['request']['tools'][0]['function']
        branches = wrapper['parameters']['properties']['calls']['items']['oneOf']
        self.assertEqual({b['properties']['name']['enum'][0] for b in branches}, {'botsesh_read', 'botsesh_post', 'botsesh_pass'})
        self.assertNotIn('botsesh_agree', json.dumps(result['request']))

    def test_unsupported_owner_request_drops_agree_in_every_provider_shape(self):
        self.mark(False)
        self.owner()
        shapes = {
            'chat': [{'type': 'function', 'function': {'name': 'botsesh_agree'}}, {'type': 'function', 'function': {'name': 'terminal'}}],
            'responses': [{'type': 'function', 'name': 'botsesh_agree'}, {'type': 'function', 'name': 'terminal'}],
            'anthropic': [{'name': 'botsesh_agree', 'input_schema': {}}, {'name': 'terminal', 'input_schema': {}}],
            'gemini': [{'functionDeclarations': [{'name': 'botsesh_agree'}, {'name': 'terminal'}]}],
        }
        for label, declared in shapes.items():
            with self.subTest(label), self.scope():
                request = {'messages': [{'role': 'system', 'content': adapter.HINT},
                                        {'role': 'assistant', 'content': [{'type': 'tool_use', 'name': 'botsesh_agree'}]}],
                           'tools': declared}
                result = tool_policy.restrict_request(request=request, session_id='s', turn_id='t')
                dumped = json.dumps(result['request']['tools'])
                self.assertNotIn('botsesh_agree', dumped)
                self.assertIn('terminal', dumped)  # Owner host tools are untouched.
                self.assertNotIn('botsesh_agree', result['request']['messages'][0]['content'])
                # Earlier model history is evidence, never rewritten.
                self.assertEqual(result['request']['messages'][1], request['messages'][1])

    def test_unsupported_owner_post_description_stops_naming_agree(self):
        self.mark(False)
        self.owner()
        request = self.request()
        with self.scope():
            result = tool_policy.restrict_request(request=request, session_id='s', turn_id='t')
        post = next(t['function'] for t in result['request']['tools'] if t['function']['name'] == 'botsesh_post')
        self.assertIn('consider revising, or deliberately set allow_stale:true', post['description'])
        self.assertNotIn('agree', post['description'])

    def test_turn_context_and_blocked_notice_stop_advertising_agree(self):
        self.assertIn('botsesh_pass and botsesh_agree are available', tool_policy.turn_context(GUEST))
        text = tool_policy.turn_context(GUEST, agree=False)
        self.assertIn('Only botsesh_read, botsesh_post and botsesh_pass are available. No terminal', text)
        self.assertNotIn('agree', text)
        self.guest()
        self.client.enrolled = Mock(return_value=dict(self.policy, toolsAllowed=False))
        with self.scope():
            self.assertIn('read, post, pass and agree', tool_policy.enforce_tools(tool_name='terminal', session_id='s', turn_id='t')['message'])
            self.mark(False)
            blocked = tool_policy.enforce_tools(tool_name='terminal', session_id='s', turn_id='t')
        self.assertEqual(blocked['action'], 'block')
        self.assertEqual(blocked['message'], 'Tools are off for guests in this BotSesh turn. Only room read, post and pass are available.')

    def test_stale_refusal_text_follows_capability(self):
        def refused(args):
            def fn(a, **kw):
                raise BotSeshError('room_moved_on', 409, rejected=True)
            with patch.object(tools, 'Client', return_value=self.client):
                return json.loads(tools.guarded(fn)(args))
        args = {'room_id': 'room'}
        self.assertIn('botsesh_agree', refused(args)['message'])
        self.mark(False)
        message = refused(args)['message']
        self.assertEqual(message, agreement.MOVED_ON_NO_AGREE)
        self.assertNotIn('agree', message)
        self.assertIn('allow_stale:true', message)

    # --- one cached observation per read, never a request --------------------
    def test_observation_comes_from_the_read_messages_only(self):
        for rows, expected in (([{'number': 1, 'agreements': []}], True),
                               ([{'number': 1, 'agreements': [{'participantId': 'p'}]}, {'number': 2}], True),
                               ([{'number': 1}, {'number': 2, 'agreements': None}], False),
                               ([], None), (None, None)):
            self.assertIs(agreement.observe(rows), expected, rows)
        room = {}
        agreement.record(room, [])
        self.assertNotIn('agreement_support', room)  # An empty read proves nothing.
        agreement.record(room, [{'number': 1}])
        self.assertIs(room['agreement_support'], False)
        agreement.record(room, [])
        self.assertIs(room['agreement_support'], False)  # Retained until a read shows otherwise.
        agreement.record(room, [{'number': 1, 'agreements': []}])
        self.assertIs(room['agreement_support'], True)

    def read_value(self, with_agreements, number=3):
        message = dict(id=number, number=number, text='Question', editCount=0, senderParticipantId='guest',
                       authorParticipant=dict(id='guest', verified=False, active=True, name='Guest'))
        if with_agreements:
            message['agreements'] = []
        return dict(has_more=False, truncated=False, ack_eligible=True, batch_id='b%d' % number, messages=[message],
                    read_checkpoint=number - 1, next_checkpoint=number)

    def test_each_wake_makes_only_its_read_and_gates_without_any_extra_request(self):
        binding = self.client.store.room('room')
        calls = []
        value = {'with': True}

        def rpc(api, b, name, args=None, **kw):
            calls.append(name)
            return self.read_value(value['with'], len(calls) + 2)
        self.guest()
        self.client.store.update(lambda d: d['rooms']['room'].update(oauth_receipts={}))
        with patch.object(OAuthClient, 'rpc', rpc), patch.object(oauth_mod, '_http', side_effect=AssertionError('no extra request')):
            api = OAuthClient(self.client.store)
            for with_agreements, expected in ((False, False), (False, False), (True, True), (False, False)):
                value['with'] = with_agreements
                api.call(binding, 'read')
                self.assertIs(self.client.store.room('room').get('agreement_support'), expected)
                # Many model requests in the same wake read only the local store.
                before = len(calls)
                with self.scope():
                    for _ in range(5):
                        result = tool_policy.restrict_request(request=self.request(), session_id='s', turn_id='t')
                        self.assertEqual('botsesh_agree' in self.names(result), expected)
                self.assertEqual(len(calls), before)
        # Exactly one catch-up per wake: no tools/list, probe or capability call.
        self.assertEqual(calls, ['catchup'] * 4)

    async def test_agree_never_reaches_the_server_after_an_unsupported_read(self):
        self.current()
        reached = []

        def rpc(api, b, name, args=None, **kw):
            reached.append(name)
            return {'number': 3, 'version': 0, 'active': True, 'read_effect': 'none', 'wake_effect': 'none'}
        args = {'room_id': 'room', 'number': 3, 'version': 0, 'active': True}
        ctx = Mock()
        ctx.session_surface.return_value = 'botsesh'

        def call():
            with self.scope(), patch.object(tools, 'Client', return_value=self.client), patch.object(OAuthClient, 'rpc', rpc):
                return json.loads(tools.guarded(lambda a, **kw: tools.agree(a, ctx))(args))
        self.mark(False)
        result = call()
        self.assertEqual((result['ok'], result['error']), (False, 'agreement_unsupported'))
        self.assertEqual(result['message'], agreement.UNSUPPORTED)
        self.assertEqual(reached, [])
        self.assertNotIn('explicit_agreement', self.client.store.room('room')['processing_receipts']['3'])
        for state in (None, True):
            self.mark(state)
            self.assertTrue(call()['ok'])
        self.assertEqual(reached, ['agree', 'agree'])

    async def test_adapter_turn_text_reflects_the_cached_capability(self):
        self.frozen['batch'].update(hasMore=False, nextCheckpoint=3)
        self.client.store.update(lambda d: d['rooms']['room'].update(oauth_receipts={
            'batch': {'participant_id': 'seat', 'numbers': [3], 'expires': time.time() + 300}}))
        policy = dict(GUEST, guest_tools_default=False, tools_ceiling_allowed=False, author_is_agent_owner=False,
                      batch_all_authors_are_agent_owner=False)

        def wire(api, binding, op, **args):
            return {'check': {'mentions': [3], 'replies': []}, 'read': self.frozen, 'tool_policy': policy}[op]
        for state, advertised in ((False, False), (True, True)):
            self.mark(state)
            self.client.store.update(lambda d: d['rooms']['room'].update(processing_receipts={}))
            self.a._current.clear()
            self.a._admit_event = AsyncMock(return_value=True)
            with patch.object(OAuthClient, 'call', wire):
                await self.a._check_room('room')
            text = self.a._admit_event.call_args.args[0].text
            self.assertEqual('botsesh_pass and botsesh_agree are available' in text, advertised)
            self.assertIn('Only botsesh_read, botsesh_post', text)


@unittest.skipUnless(os.environ.get('BOTSESH_INVITATION_SERVER_COMMAND') and os.environ.get('BOTSESH_EXPECT_AGREEMENT') in ('0', '1'),
                     'explicit local server argv and its expected agreement capability (0/1) required')
class CapabilityHTTPTests(unittest.IsolatedAsyncioTestCase):
    """A real server's own wake read decides what the model is offered."""
    asyncSetUp = http_fixture.RealInvitationTests.asyncSetUp
    command = http_fixture.RealInvitationTests.command
    stop_server = http_fixture.RealInvitationTests.stop_server

    async def test_real_wake_read_proves_capability_and_model_surface_follows_it(self):
        import asyncio
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        expected = os.environ['BOTSESH_EXPECT_AGREEMENT'] == '1'
        room = self.fixture['room_id']
        joined = self.client.join(self.fixture['link'], dev_origin=self.origin)
        peer = http_fixture.Client(http_fixture.Store(Path(self.tmp.name) / 'peer'))
        peer_joined = await asyncio.to_thread(peer.join, self.fixture['peer'], dev_origin=self.origin)
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=adapter.BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        live = adapter.BotSeshAdapter(PlatformConfig())
        live.client = self.client
        live.set_authorization_check(lambda user, kind, chat: user == peer_joined['participant_id'] and chat == room)
        live._admit_event = AsyncMock(return_value=True)
        question = await asyncio.to_thread(peer.enrolled, room, 'post', text='Question from Peer, without a mention',
                                           client_id=str(uuid.uuid4()), processed=False)
        self.assertIsNone(self.client.store.room(room).get('agreement_support'))  # Nothing observed before a wake.
        await live._check_room(room)  # The shipped invitation transport's real wake read.
        live._admit_event.assert_awaited_once()
        self.assertIs(self.client.store.room(room).get('agreement_support'), expected)
        event_text = live._admit_event.call_args.args[0].text
        self.assertEqual('botsesh_pass and botsesh_agree are available' in event_text, expected)
        self.assertIn('Only botsesh_read, botsesh_post', event_text)

        target = str(question['number'])
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID=room, HERMES_SESSION_MESSAGE_ID=target)
        scope = patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default))
        declared = [{'type': 'function', 'function': spec} for spec in tools.SCHEMAS]
        with scope:
            sent = tool_policy.restrict_request(request={'messages': [{'role': 'system', 'content': adapter.HINT}], 'tools': declared},
                                                session_id='s', turn_id='t')['request']
        offered = {t['function']['name'] for t in sent['tools']}
        self.assertEqual(offered, {'botsesh_read', 'botsesh_post', 'botsesh_pass'} | ({'botsesh_agree'} if expected else set()))
        self.assertEqual('botsesh_agree' in sent['messages'][0]['content'], expected)
        self.assertEqual('botsesh_agree' in json.dumps(sent['tools']), expected)
        self.assertEqual(sent['messages'][0]['content'] == adapter.HINT, expected)

        args = {'room_id': room, 'number': question['number'], 'version': 0, 'active': True}
        ctx = Mock()
        ctx.session_surface.return_value = 'botsesh'
        with scope, patch.object(tools, 'Client', return_value=self.client):
            native = json.loads(tools.guarded(lambda a, **kw: tools.agree(a, ctx))(args))
        cli = Mock()
        cli.session_surface.return_value = 'cli'
        with patch.object(tools, 'Client', return_value=self.client):
            if expected:
                self.assertTrue(native['ok'], native)
                self.assertTrue(native['result']['active'])
            else:
                # The unsupported server really lacks the route; a native turn never asks it.
                self.assertEqual((native['ok'], native['error']), (False, 'agreement_unsupported'))
                with self.assertRaises(BotSeshError):
                    tools.agree(args, cli)


@unittest.skipUnless(os.environ.get('BOTSESH_OAUTH_CONNECT_SERVER_COMMAND') and os.environ.get('BOTSESH_EXPECT_AGREEMENT') in ('0', '1'),
                     'explicit local OAuth server argv and its expected agreement capability (0/1) required')
class CapabilityOAuthTests(unittest.IsolatedAsyncioTestCase):
    """Native OAuth: the MCP catch-up projection of a real server decides the model surface."""
    setUp = native_fixture.connect_fixture.NativeOAuthConnect.setUp
    control = native_fixture.connect_fixture.NativeOAuthConnect.control
    stop_server = native_fixture.connect_fixture.NativeOAuthConnect.stop_server
    connect = native_fixture.connect_fixture.NativeOAuthConnect.connect
    begin = native_fixture.NativeStartedAt.begin

    async def test_real_oauth_wake_read_proves_capability_and_model_surface_follows_it(self):
        import asyncio
        expected = os.environ['BOTSESH_EXPECT_AGREEMENT'] == '1'
        event = await self.begin()  # Real connect, real addressed wake read, scripted admission only.
        self.assertIs(self.client.store.room(self.room).get('agreement_support'), expected)
        await asyncio.to_thread(self.hooks['pre_llm_call'], **self.kwargs)  # The real pre-model hook.
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID=self.room, HERMES_SESSION_MESSAGE_ID=str(self.question['number']))
        declared = [{'type': 'function', 'function': spec} for spec in tools.SCHEMAS]
        with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default)):
            result = tool_policy.restrict_request(request={'messages': [{'role': 'system', 'content': adapter.HINT}], 'tools': declared},
                                                  session_id=self.kwargs['session_id'], turn_id=self.kwargs['turn_id'])
        if result is None:
            # Unrestricted owner turn: nothing was hidden, which is only right when the server has agreement.
            self.assertTrue(expected)
        else:
            sent = result['request']
            self.assertEqual('botsesh_agree' in {t['function']['name'] for t in sent['tools']}, expected)
            self.assertEqual(sent['messages'][0]['content'] == adapter.HINT, expected)
            self.assertEqual('botsesh_agree' in json.dumps(sent), expected)
        self.assertEqual('botsesh_agree are available' in event.text, expected and 'Only botsesh_read' in event.text)


if __name__ == '__main__':
    unittest.main()
