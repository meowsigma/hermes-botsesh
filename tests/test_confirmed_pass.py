"""Zero-model regression through Hermes registry coercion and its terminal hook.

The sequential executor suppresses the dispatcher's inner observer and publishes
its own retained args. Do not assume these are the handler's coerced copy.
Only the MCP wire is doubled; no provider client is created.
"""
import contextlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_living_chat as fixture
from hermes_botsesh_test import tools
from hermes_botsesh_test.adapter import _ADAPTERS
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test.oauth import OAuthClient
from hermes_botsesh_test.terminal_turn import settled_turn
from gateway.session_context import clear_session_vars, set_session_vars


class ConfirmedPassTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LivingChatTests.asyncSetUp
    begin = fixture.LivingChatTests.begin

    async def prepare(self):
        adapter, _ = await self.begin()
        _ADAPTERS.clear()
        _ADAPTERS.add(adapter)
        self.addCleanup(_ADAPTERS.discard, adapter)
        return adapter, {'room_id': 'a', 'receipt_id': adapter._current['a']['receipt_id'],
                         'processed': True}

    def core_call(self, args, *, name='botsesh_pass'):
        import hermes_cli.plugins as plugins
        from agent.tool_executor import _ToolCallRef, _resolve_sequential_dispatch
        from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
        from tools.arg_coercion import coerce_tool_args

        agent = SimpleNamespace(platform='botsesh', quiet_mode=False,
            _context_engine_tool_names=set(), _memory_manager=None,
            session_id='confirmed-pass-fixture', valid_tool_names={name},
            enabled_toolsets=['botsesh'], disabled_toolsets=None, interrupt=Mock())
        observed = {'handler': [], 'hook': []}
        original_pass = tools.pass_turn

        def handler(value, context):
            observed['handler'].append(dict(value))
            return original_pass(value, context)

        def observer(**kwargs):
            observed['hook'].append(dict(kwargs['args']))

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {
                'HERMES_HOME': str(self.client.store.root.parent),
                'HOME': str(self.client.store.root.parent)}, clear=False))
            manager = PluginManager()
            manager._discovered = True
            ctx = PluginContext(PluginManifest(name='botsesh', source='user'), manager)
            stack.enter_context(patch.object(plugins, 'get_plugin_manager', return_value=manager))
            stack.enter_context(patch.object(plugins, 'discover_plugins'))
            stack.enter_context(patch.object(tools, 'Client', return_value=self.client))
            stack.enter_context(patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')))
            stack.enter_context(patch.object(tools, 'pass_turn', side_effect=handler))
            stack.enter_context(patch.object(ctx, 'register_command'))
            stack.enter_context(patch.object(ctx, 'register_skill'))
            tools.register_tools(ctx)
            ctx.register_hook('post_tool_call', observer)
            # Actual registered schema, not a hand-written approximation.
            normalized = coerce_tool_args(name, dict(args))
            stack.enter_context(patch('agent.subagent_lifecycle.get_active_subagent_parent', return_value=agent))
            tokens = set_session_vars(platform='botsesh', chat_id='a', message_id='3')
            try:
                ref = _ToolCallRef(name, dict(args), 'fixture-task', 'fixture-call', [])
                dispatch = _resolve_sequential_dispatch(agent, ref, [])
                result = dispatch.execute(ref.args)
                self.assertEqual(observed['hook'], [])  # Inner observer is suppressed.
                ref.emit_post(agent, result)  # Same publication used by the native executor.
            finally:
                clear_session_vars(tokens)
        self.assertEqual(len(observed['hook']), 1)
        self.assertEqual(observed['hook'][0], args)
        if name == 'botsesh_pass':
            self.assertEqual(observed['handler'], [normalized], result)
        return json.loads(result), agent, observed

    async def test_string_true_confirmed_pass_interrupts_once_after_core_coercion(self):
        _, args = await self.prepare()
        args['processed'] = 'True'
        result, agent, observed = self.core_call(args)
        self.assertIs(observed['handler'][0]['processed'], True)
        self.assertEqual(observed['hook'][0]['processed'], 'True')
        self.assertIs(result['ok'], True)
        self.assertEqual(result['result']['read_effect'], 'processed_frozen_receipt')
        agent.interrupt.assert_called_once_with(hard_cancel=True, tool_reason='botsesh_passed')
        self.assertEqual(self.messages, {})

    async def test_boolean_confirmed_pass_still_interrupts_once(self):
        _, args = await self.prepare()
        result, agent, _ = self.core_call(args)
        self.assertIs(result['ok'], True)
        agent.interrupt.assert_called_once_with(hard_cancel=True, tool_reason='botsesh_passed')

    async def test_invalid_processed_values_never_pass_or_interrupt(self):
        _, args = await self.prepare()
        for value in (False, 'False', 'yes', '1', 'true-ish', '', 1, None):
            with self.subTest(processed=value):
                result, agent, _ = self.core_call({**args, 'processed': value})
                self.assertIs(result['ok'], False)
                self.assertEqual(result['error'], 'processed_receipt_required')
                agent.interrupt.assert_not_called()
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))
        self.assertNotIn('explicit_pass', self.client.store.room('a')['processing_receipts']['3'])

    async def test_unconfirmed_pass_never_interrupts(self):
        _, args = await self.prepare()
        original = OAuthClient.rpc
        def unconfirmed(api, binding, name, payload=None, **kwargs):
            if name == 'finish_room_turn':
                return {'reply_number': None}  # No confirmed checkpoint.
            return original(api, binding, name, payload, **kwargs)
        with patch.object(OAuthClient, 'rpc', unconfirmed):
            result, agent, _ = self.core_call(args)
        self.assertIs(result['ok'], False)
        self.assertEqual(result['error'], 'turn_completion_unconfirmed_retry_same_intent')
        agent.interrupt.assert_not_called()
        self.assertNotIn('result', self.client.store.room('a')['processing_receipts']['3']['explicit_pass'])

    async def test_failed_pass_never_interrupts(self):
        _, args = await self.prepare()
        original = OAuthClient.rpc
        def rejected(api, binding, name, payload=None, **kwargs):
            if name == 'finish_room_turn':
                raise BotSeshError('authorization_revoked', 401)
            return original(api, binding, name, payload, **kwargs)
        with patch.object(OAuthClient, 'rpc', rejected):
            result, agent, _ = self.core_call(args)
        self.assertIs(result['ok'], False)
        self.assertEqual(result['error'], 'authorization_revoked')
        agent.interrupt.assert_not_called()
        row = self.client.store.read()['rooms']['a']
        self.assertFalse(row['active'])
        self.assertNotIn('result', row['processing_receipts']['3']['explicit_pass'])

    async def test_foreign_room_or_wrong_receipt_never_interrupts(self):
        _, args = await self.prepare()
        for change, error in (({'room_id': 'b'}, 'cross_room_denied'),
                              ({'receipt_id': 'foreign-receipt'}, 'receipt_scope')):
            with self.subTest(change=change):
                result, agent, _ = self.core_call({**args, **change})
                self.assertIs(result['ok'], False)
                self.assertEqual(result['error'], error)
                agent.interrupt.assert_not_called()
        self.assertFalse(any(name == 'finish_room_turn' for _, name, _ in self.requests))

    async def test_ordinary_processed_reply_never_interrupts(self):
        _, args = await self.prepare()
        result, agent, _ = self.core_call({**args, 'text': 'Ordinary authored reply.', 'reply_to': 3},
                                          name='botsesh_post')
        self.assertIs(result['ok'], True)
        self.assertEqual(result['result']['read_effect'], 'processed_frozen_receipt')
        agent.interrupt.assert_not_called()
        self.assertEqual(len(self.messages), 1)

    async def test_hook_requires_exact_current_confirmed_pass_journal(self):
        adapter, args = await self.prepare()
        result, _, _ = self.core_call(args)
        baseline = self.client.store.room('a')['processing_receipts']['3']
        agent = Mock(platform='botsesh')
        tokens = set_session_vars(platform='botsesh', chat_id='a', message_id='3')
        try:
            with patch('agent.subagent_lifecycle.get_active_subagent_parent', return_value=agent):
                payload = baseline['explicit_pass']['payload']
                for change in ({'explicit_pass': {}},
                               {'explicit_pass': {'payload': payload}},
                               {'explicit_pass': {**baseline['explicit_pass'], 'payload': {**payload, 'processed': False}}},
                               {'explicit_pass': {**baseline['explicit_pass'], 'payload': {**payload, 'processed': 'True'}}},
                               {'explicit_pass': {**baseline['explicit_pass'], 'payload': {**payload, 'receipt_id': 'other-receipt'}}},
                               {'receipt_id': 'other-receipt'}):
                    with self.subTest(change=change):
                        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts'].update(
                            {'3': {**baseline, **change}}))
                        settled_turn(tool_name='botsesh_pass', args=args, result=json.dumps(result))
                        agent.interrupt.assert_not_called()
                self.client.store.update(lambda d: d['rooms']['a']['processing_receipts'].update({'3': baseline}))
                for outcome in ({'ok': False, 'result': result['result']},
                                {'ok': True, 'result': {**result['result'], 'read_effect': 'none'}}):
                    settled_turn(tool_name='botsesh_pass', args=args, result=json.dumps(outcome))
                    agent.interrupt.assert_not_called()
                adapter._current['a']['target'] = 5
                settled_turn(tool_name='botsesh_pass', args=args, result=json.dumps(result))
                agent.interrupt.assert_not_called()
        finally:
            clear_session_vars(tokens)


if __name__ == '__main__':
    unittest.main()
