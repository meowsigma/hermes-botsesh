"""Stock lazy-call reachability regression; native gateway proof is separate."""
import json
import unittest
from unittest.mock import Mock, patch
import test_ongoing_turns as ongoing
from hermes_botsesh_test import tools, tool_policy


class LazyChatDispatch(unittest.IsolatedAsyncioTestCase):
    setUp = ongoing.OngoingTurns.setUp
    current = ongoing.OngoingTurns.current

    def test_cold_lazy_guest_schemas_are_executor_reachable(self):
        from tools.registry import registry
        from tools.tool_search import assemble_tool_defs, ToolSearchConfig
        from agent.conversation_loop import _invalid_tool_name_error_content
        ctx = Mock()
        ctx.register_tool.side_effect = lambda **kw: registry.register(**kw)
        tools.register_tools(ctx)
        eager = [{'type': 'function', 'function': s} for s in tools.SCHEMAS]
        eager.append({'type': 'function', 'function': {'name': 'terminal', 'parameters': {'type': 'object'}}})
        assembled = assemble_tool_defs(eager, config=ToolSearchConfig.from_raw({'enabled': 'on'})).tool_defs
        valid = {t['function']['name'] for t in assembled}
        self.assertIn('tool_call', valid)
        self.assertTrue(tool_policy.ROOM_TOOLS.isdisjoint(valid), 'must be cold, not pre-described')
        current = self.current()
        current['tool_policy'] = dict(self.policy, toolsAllowed=False)
        current['hook_turn'] = ('s', 't')
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
        request = {'tools': assembled, 'messages': []}
        before = json.dumps(request, sort_keys=True)
        with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default)):
            filtered = tool_policy.restrict_request(request=request, session_id='s', turn_id='t')['request']
        for spec in filtered['tools']:
            name = spec['function']['name']
            self.assertIn(name, valid, _invalid_tool_name_error_content(name, valid))
        self.assertEqual([s['function']['name'] for s in filtered['tools']], ['tool_call'])
        choices = filtered['tools'][0]['function']['parameters']['properties']['calls']['items']['oneOf']
        self.assertEqual({s['properties']['name']['enum'][0] for s in choices}, tool_policy.ROOM_TOOLS)
        self.assertEqual(json.dumps(request, sort_keys=True), before, 'never mutate cached toolsets')


    def test_bridge_cannot_bypass_independent_guest_veto(self):
        from model_tools import handle_function_call
        from tools.registry import registry
        ctx = Mock()
        ctx.register_tool.side_effect = lambda **kw: registry.register(**kw)
        tools.register_tools(ctx)
        current = self.current()
        current['tool_policy'] = dict(self.policy, toolsAllowed=False)
        current['hook_turn'] = ('s', 't')
        self.client.enrolled = Mock(return_value=current['tool_policy'])
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
        with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default)), patch('hermes_cli.lifecycle.invoke_hook', side_effect=lambda name, **kw: [tool_policy.enforce_tools(**kw)]):
            # Forge a call despite the constrained schema. Actual stock bridge
            # unwrap must still reach the underlying-tool veto before handler I/O.
            result = json.loads(handle_function_call('tool_call', {'calls': [
                {'name': 'botsesh_invite', 'arguments': {'room_id': 'room', 'app': 'Hermes'}}]},
                enabled_toolsets=['botsesh'], session_id='s', turn_id='t', tool_call_id='forged'))
            self.assertIn('Tools are off for guests', result['error'])
            result = json.loads(handle_function_call('terminal', {'command': 'ls ~'},
                session_id='s', turn_id='t', tool_call_id='guest-ls'))
            self.assertIn('Tools are off for guests', result['error'])

    def test_lazy_wire_formats_and_absent_executor_fail_closed(self):
        current = self.current()
        current['tool_policy'] = dict(self.policy, toolsAllowed=False)
        current['hook_turn'] = ('s', 't')
        env = dict(HERMES_SESSION_PLATFORM='botsesh', HERMES_SESSION_CHAT_ID='room', HERMES_SESSION_MESSAGE_ID='3')
        with patch('gateway.session_context.get_session_env', side_effect=lambda k, default='': env.get(k, default)):
            for mode in ('responses', 'codex_responses', 'anthropic_messages'):
                original = {'tools': [{'name': 'tool_call'}, {'name': 'terminal'}]}
                filtered = tool_policy.restrict_request(request=original, session_id='s', turn_id='t', api_mode=mode)['request']
                self.assertEqual([s['name'] for s in filtered['tools']], ['tool_call'])
            empty = tool_policy.restrict_request(request={'tools': []}, session_id='s', turn_id='t')['request']
            self.assertEqual(empty['tools'], [])
            current['tool_policy'] = dict(self.policy, toolsAllowed=True, requester={'id': 'guest', 'isOwner': True})
            self.assertIsNone(tool_policy.restrict_request(request=original, session_id='s', turn_id='t'))


if __name__ == '__main__':
    unittest.main()
