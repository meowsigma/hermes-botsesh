"""Exact recorded 11d tool/message objects through the native summary owner."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_receipt_failure_replay import capture
from hermes_botsesh_test import tools, tool_policy
from agent import chat_completion_helpers as helpers

# Literal inventory from live-run.log line28. The failed request itself was
# never saved by 11d; schemas are reconstructed from its pinned SCHEMAS, not
# represented as a recovered raw envelope.
RECOVERY_NAMES = {'botsesh_offer_ownership','botsesh_pass','botsesh_create_room',
    'botsesh_post','botsesh_invite','botsesh_join','botsesh_cant_do','botsesh_rooms',
    'botsesh_takeover_claim','botsesh_revoke_ownership_offer','botsesh_agree','botsesh_read'}


class NativeSummaryReplay(unittest.TestCase):
    def test_summary_replays_recorded_history_with_only_four_room_tools(self):
        recorded = capture(3)
        request = dict(messages=copy.deepcopy(recorded['native_messages']),
            tools=[{'type':'function','function':s} for s in tools.SCHEMAS if s['name'] in RECOVERY_NAMES])
        cached = copy.deepcopy(request)
        wire=[]
        agent = SimpleNamespace(api_mode='chat_completions', platform='botsesh', session_id='recorded-session',
            _current_turn_id='recorded-turn', _current_task_id='recorded-task', provider='custom',
            model='offline-replay', base_url='http://127.0.0.1:1',
            _build_api_kwargs=lambda _:copy.deepcopy(request),
            _interruptible_api_call=lambda req:wire.append(req) or req,
            _get_transport=lambda:SimpleNamespace(normalize_response=lambda response,**kw:SimpleNamespace(content='offline',tool_calls=[])))
        def middleware(req, **context):
            from hermes_cli.middleware import RequestMiddlewareResult
            value = tool_policy.restrict_request(request=req, **context)['request']
            return RequestMiddlewareResult(value, req)
        with patch.object(tool_policy, '_scope', return_value=('botsesh', 'recorded-room', '0')), \
             patch.object(tool_policy, '_current', return_value=(None,None)), \
             patch('hermes_cli.middleware.apply_llm_request_middleware', side_effect=middleware), \
             patch('agent.relay_llm.execute_current', side_effect=lambda req,callback,**kw:callback(req)), \
             patch.object(helpers, 'sanitize_outbound_kwargs'):
            self.assertEqual(helpers._chat_summary_attempt(agent, request['messages'], 'summary-replay')(0), 'offline')
        self.assertEqual({s['function']['name'] for s in wire[0]['tools']}, tool_policy.ROOM_TOOLS)
        self.assertEqual(wire[0]['messages'], recorded['native_messages'])
        self.assertEqual(request,cached,'Never mutate cached owner tool inventory or recorded history')


if __name__ == '__main__': unittest.main()
