"""Regressions from the owner's named-profile trial; no inference."""
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch, Mock
from test_ops import OpsTests
from test_final_after_post import FinalAfterPostTests
from hermes_botsesh_test import ops_status as status, tools
from hermes_botsesh_test.oauth import OAuthClient
from gateway.session_context import set_session_vars, clear_session_vars


class RuntimeFixTests(OpsTests):
    def test_idle_restart_refusal_prints_exact_selected_profile_command(self):
        import argparse
        import contextlib
        import io
        from hermes_botsesh_test import cli
        args = argparse.Namespace(botsesh_action='restart-gateway', checkpoint=False)
        output = io.StringIO()
        with patch.object(cli, 'Client', return_value=self.client), \
             patch('hermes_botsesh_test.ops_restart.lifecycle_command') as lifecycle, \
             contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            cli._handle_profile(args, 'fixture')
        lifecycle.assert_not_called()
        self.assertIn('hermes -p fixture gateway restart --detach', output.getvalue())
        self.assertIn('hermes -p fixture botsesh status', output.getvalue())
        self.assertIn('not an automatic idle restart', output.getvalue())

    def test_pid_rung_can_be_a_multiplex_owner_without_local_runtime(self):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        live = {'pid': 123, 'start_time': 42, 'updated_at': stamp,
                'platforms': {'fixture:botsesh': {'state': 'connected', 'writer_pid': 123,
                    'writer_start_time': 42, 'updated_at': stamp}}}
        with patch.object(status, 'profile_context', return_value=(self.home, 'fixture')), \
             patch('gateway.status.resolve_gateway_liveness', return_value=SimpleNamespace(
                 running=True, probe_error=False, pid=123, source='pid', runtime=None)), \
             patch('gateway.status.read_runtime_status', return_value=None), \
             patch('gateway.status.multiplexer_liveness_for_profile', return_value=(123, live)), \
             patch('gateway.status.get_process_start_time', return_value=42):
            self.assertEqual(status.gateway_snapshot()['channel'], 'up')

    def test_connected_platform_transition_is_not_a_heartbeat(self):
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        live = {'pid': 123, 'start_time': 42, 'updated_at': stamp,
                'platforms': {'fixture:botsesh': {'state': 'connected', 'writer_pid': 123,
                    'writer_start_time': 42, 'updated_at': '2000-01-01T00:00:00Z'}}}
        with patch.object(status, 'profile_context', return_value=(self.home, 'fixture')), \
             patch('gateway.status.resolve_gateway_liveness', return_value=SimpleNamespace(
                 running=True, probe_error=False, pid=123, source='multiplexer', runtime=live)), \
             patch('gateway.status.get_process_start_time', return_value=42):
            self.assertEqual(status.gateway_snapshot()['channel'], 'up')
            live['updated_at'] = '2000-01-01T00:00:00Z'
            self.assertEqual(status.gateway_snapshot()['channel'], 'unknown')


class FinalReadFixTests(FinalAfterPostTests):
    async def test_read_during_turn_keeps_frozen_receipt_and_one_greeting(self):
        adapter, event = await self.begin()
        original = OAuthClient.rpc
        def wire(api, binding, name, args=None, **kwargs):
            if name == 'catchup':
                result = original(api, binding, name, {**(args or {}), 'target_number': 3}, **kwargs)
                return {**result, 'batch_id': str(uuid.uuid4())}
            return original(api, binding, name, args, **kwargs)
        tokens = set_session_vars(platform='botsesh', chat_id='a', message_id='3')
        try:
            with patch.object(OAuthClient, 'rpc', wire), patch.object(tools, 'Client', return_value=self.client), \
                 patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
                result = tools.read({'room_id': 'a'}, Mock())
                self.assertEqual(result['receipt_id'], adapter._current['a']['receipt_id'])
                posted = tools.post({'room_id': 'a', 'text': 'One greeting.', 'receipt_id': result['receipt_id'],
                                     'processed': True, 'reply_to': 3, 'client_id': str(uuid.uuid4())}, Mock())
        finally:
            clear_session_vars(tokens)
        self.model_ended(adapter)
        result = await adapter.send('a', 'One greeting.', metadata={'notify': True})
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.message_id, str(posted['number']))
        self.assertEqual(len(self.messages), 1)
        self.assertNotIn('pending_final', self.client.store.room('a'))


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite([
        RuntimeFixTests('test_connected_platform_transition_is_not_a_heartbeat'),
        RuntimeFixTests('test_idle_restart_refusal_prints_exact_selected_profile_command'),
        FinalReadFixTests('test_read_during_turn_keeps_frozen_receipt_and_one_greeting')])
