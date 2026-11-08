"""Real native tool loop with scripted model responses, no provider calls."""
import contextlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from test_final_after_post import FinalAfterPostTests as _Base
from hermes_botsesh_test import tools
from hermes_botsesh_test.adapter import _ADAPTERS
from gateway.platforms.event import ProcessingOutcome
from gateway.session_context import set_session_vars, clear_session_vars


def response(name=None, args=None):
    call = SimpleNamespace(id='terminal-call', type='function',
        function=SimpleNamespace(name=name, arguments=json.dumps(args))) if name else None
    msg = SimpleNamespace(content='' if name else 'Backup started.', tool_calls=[call] if call else None,
                          reasoning=None, reasoning_content=None, reasoning_details=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason='tool_calls' if name else 'stop')],
                           model='scripted/no-network', usage=None)


class TerminalLoopTests(_Base):
    def native_turn(self, name, args, room):
        import hermes_cli.plugins as plugins
        from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
        from run_agent import AIAgent
        manager = PluginManager()
        manager._discovered = True  # Tools/hooks below are this fixture's complete plugin inventory.
        ctx = PluginContext(PluginManifest(name='botsesh', source='user'), manager)
        with contextlib.ExitStack() as stack:
            import os
            stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.client.store.root.parent),
                'HOME': str(self.client.store.root.parent), 'PATH': os.environ.get('PATH', ''),
                'TMPDIR': os.environ.get('TMPDIR', str(self.client.store.root.parent))}, clear=True))
            stack.enter_context(patch.object(plugins, 'get_plugin_manager', return_value=manager))
            stack.enter_context(patch.object(plugins, 'discover_plugins'))
            stack.enter_context(patch.object(tools, 'Client', return_value=self.client))
            stack.enter_context(patch.object(tools, 'calling_scope', return_value=('botsesh', room)))
            # Register real plugin tool handlers and settlement hook.
            registered = {}
            def register_tool(**kw): registered[kw['name']] = kw
            stack.enter_context(patch.object(ctx, 'register_tool', side_effect=register_tool))
            stack.enter_context(patch.object(ctx, 'register_command'))
            stack.enter_context(patch.object(ctx, 'register_skill'))
            tools.register_tools(ctx)
            definitions = [{'type': 'function', 'function': entry['schema']} for entry in registered.values()]
            stack.enter_context(patch('model_tools.get_tool_definitions', return_value=definitions))
            stack.enter_context(patch('model_tools.check_toolset_requirements', return_value={}))
            stack.enter_context(patch('agent.process_bootstrap.OpenAI'))
            agent = AIAgent(api_key='scripted-not-a-provider-key', base_url='https://example.invalid/v1',
                            quiet_mode=True, skip_context_files=True, skip_memory=True,
                            platform='botsesh', max_iterations=6)
            agent.client = MagicMock()
            agent._cached_system_prompt = 'Scripted fixture.'
            agent._use_prompt_caching = False
            agent.compression_enabled = False
            agent.save_trajectories = False
            agent.skip_background_review = True
            agent.client.chat.completions.create.side_effect = [response(name, args), *[response() for _ in range(5)]]
            for method in ('_persist_session', '_save_trajectory', '_cleanup_task_resources', '_flush_messages_to_session_db'):
                stack.enter_context(patch.object(agent, method))
            stack.enter_context(patch('agent.turn_context.start_deferred_title_upgrade'))
            stack.enter_context(patch('model_tools.handle_function_call',
                side_effect=lambda function_name, function_args, *unused, **kw: registered[function_name]['handler'](function_args)))
            tokens = set_session_vars(platform='botsesh', chat_id=room, message_id='3')
            try:
                result = agent.run_conversation('Synthetic native addressed turn')
            finally:
                clear_session_vars(tokens)
            return result, agent.client.chat.completions.create.call_count

    async def test_confirmed_cant_do_stops_after_one_model_call_and_backup_starts(self):
        import uuid
        adapter, event = await self.begin()
        _ADAPTERS.clear(); _ADAPTERS.add(adapter)
        self.addCleanup(_ADAPTERS.discard, adapter)
        task = str(uuid.uuid4())
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['3']['frozen']['batch']['messages'][0].update(addressing={'taskId': task}))
        original = __import__('hermes_botsesh_test.oauth', fromlist=['OAuthClient']).OAuthClient.rpc
        def rpc(api, binding, name, args=None, **kw):
            if name == 'finish_room_turn':
                return {'failure': {'taskId': task, 'status': 'cant_do', 'state': 'offered'}, 'read_effect': 'none'}
            return original(api, binding, name, args, **kw)
        with patch('hermes_botsesh_test.oauth.OAuthClient.rpc', rpc):
            result, calls = self.native_turn('botsesh_cant_do', {'reason': 'No image tool.'}, 'a')
        self.assertEqual(calls, 1)
        self.assertEqual(result['api_calls'], 1)
        self.assertTrue(result['interrupted'])  # Public stop API, never forged completed/read attestation.
        await adapter.on_processing_complete(event, ProcessingOutcome.CANCELLED)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['failure_code'], 'cant_do')
        self.assertEqual(self.messages, {})
        backup, backup_event = await self.begin('b')
        result, calls = self.native_turn(None, None, 'b')
        self.assertEqual(calls, 1)
        self.assertIn('Backup started.', result['final_response'])
        self.assertTrue(result['completed'])

    async def test_unprocessed_post_does_not_stop_native_inference(self):
        adapter, event = await self.begin()
        _ADAPTERS.clear(); _ADAPTERS.add(adapter)
        self.addCleanup(_ADAPTERS.discard, adapter)
        result, calls = self.native_turn('botsesh_post', {'room_id': 'a', 'text': 'Progress only.'}, 'a')
        self.assertEqual(calls, 2)
        self.assertTrue(result['completed'])
        self.assertFalse(result.get('interrupted', False))

    async def test_rejected_cant_do_does_not_stop_native_inference(self):
        adapter, event = await self.begin()
        _ADAPTERS.clear(); _ADAPTERS.add(adapter)
        self.addCleanup(_ADAPTERS.discard, adapter)
        result, calls = self.native_turn('botsesh_cant_do', {'reason': 'No image tool.'}, 'a')
        self.assertEqual(calls, 2)
        self.assertFalse(result.get('interrupted', False))

    async def test_confirmed_processed_post_stops_after_one_model_call_without_duplicate(self):
        adapter, event = await self.begin()
        _ADAPTERS.clear(); _ADAPTERS.add(adapter)
        self.addCleanup(_ADAPTERS.discard, adapter)
        result, calls = self.native_turn('botsesh_post', {'room_id': 'a', 'text': 'Exact answer.',
            'processed': True, 'receipt_id': adapter._current['a']['receipt_id'], 'reply_to': 3}, 'a')
        self.assertEqual(calls, 1)
        self.assertEqual(result['api_calls'], 1)
        await adapter.on_processing_complete(event, ProcessingOutcome.CANCELLED)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'], 'replied')
