"""Focused offline tests. These are doubles, never native-agent evidence."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hermes_botsesh_test', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from hermes_botsesh_test.storage import Store, StorageError
from hermes_botsesh_test.client import Client, BotSeshError, room_url, child_env
from hermes_botsesh_test import tools

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'plugin')
    def tearDown(self):
        self.tmp.cleanup()
    def test_private_atomic_registry(self):
        self.store.update(lambda d: d['rooms'].update({'one': {'active': True}}))
        self.assertEqual(self.store.read()['rooms']['one']['active'], True)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.credentials.stat().st_mode), 0o700)
    def test_reject_symlink_and_broad_mode(self):
        self.store.path.symlink_to(Path(self.tmp.name) / 'missing')
        with self.assertRaises(StorageError): self.store.read()
        self.store.path.unlink()
        self.store.path.write_text('{"version":1,"rooms":{}}')
        self.store.path.chmod(0o644)
        with self.assertRaises(StorageError): self.store.read()
    def test_profile_stores_do_not_leak(self):
        self.store.update(lambda d: d['rooms'].update({'private': {'active': True}}))
        other = Store(Path(self.tmp.name) / 'other')
        self.assertEqual(other.read()['rooms'], {})
    def test_revocation_disables_only_exact_room(self):
        self.store.update(lambda d: d['rooms'].update({'a': {'active': True, 'url': 'https://botsesh.com/b/a'}, 'b': {'active': True}}))
        client = Client(self.store)
        with patch.object(client, 'call', side_effect=BotSeshError('membership_revoked', 403)):
            with self.assertRaises(BotSeshError): client.enrolled('a', 'verify')
        self.assertFalse(self.store.read()['rooms']['a']['active'])
        self.assertTrue(self.store.read()['rooms']['b']['active'])
    def test_secrets_hooks_not_in_child_environment(self):
        with patch.dict(os.environ, {'NODE_OPTIONS': '--require=bad', 'OPENAI_API_KEY': 'test-only-secret', 'BOTSESH_STORE': '/foreign'}):
            env = child_env(self.store)
        for key in ('NODE_OPTIONS', 'OPENAI_API_KEY', 'BOTSESH_STORE'):
            self.assertNotIn(key, env)
    def test_logical_operation_ids_are_stable_and_payload_bound(self):
        import uuid
        client = Client(self.store)
        first = client.logical_id('post', {'room_id': 'a', 'text': 'exact'})
        self.assertEqual(uuid.UUID(first).version, 4)
        self.assertEqual(client.logical_id('post', {'text': 'exact', 'room_id': 'a'}), first)
        self.assertNotEqual(client.logical_id('post', {'room_id': 'a', 'text': 'changed'}), first)

    def test_creation_omits_missing_purpose_and_id_for_canonical_retry(self):
        client = Client(self.store)
        result = {'share_link': 'https://botsesh.com/b/abc', 'name': 'test', 'participant_id': 'participant'}
        with patch.object(client, 'call', return_value=result) as call:
            client.create('test')
        call.assert_called_once_with('create', name='test')

class Boundaries(unittest.TestCase):
    def test_origin_and_path_boundary(self):
        for url in ('http://botsesh.com/b/a', 'https://botsesh.com.evil/b/a', 'https://evil@botsesh.com/b/a', 'https://botsesh.com/b/a?token=x', 'https://botsesh.com/b/../a', 'https://botsesh.com/b/a%2Fb'):
            with self.assertRaises(BotSeshError): room_url(url)
        self.assertEqual(room_url('https://botsesh.com/b/abc#agent-invite=private')[1], 'abc')
    def test_remote_management_fails_closed(self):
        for verb in ('join https://botsesh.com/b/x#secret', 'rooms', 'leave x'):
            self.assertIn('local Hermes', tools.command(verb, platform='botsesh', source=Mock(chat_id='x')))
        self.assertIn('local Hermes', tools.command('status'))
    def test_cross_room_tool_denied(self):
        with patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            with self.assertRaises(BotSeshError): tools.scoped_room({'room_id': 'b'})
            self.assertEqual(tools.scoped_room({'room_id': 'a'}), 'a')
    def test_account_path_requires_existing_consent_and_public_dispatch(self):
        ctx = Mock()
        ctx.get_config.return_value = ''
        with patch.object(tools, 'calling_scope', return_value=('local', '')):
            with self.assertRaises(BotSeshError): tools.create_room({'mode': 'account'}, ctx)
            ctx.get_config.return_value = 'mcp__BotSesh__create_room'
            tools.create_room({'mode': 'account', 'name': 'named'}, ctx)
            self.assertEqual(ctx.dispatch_tool.call_args.args[0], 'mcp__BotSesh__create_room')
        with patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            with self.assertRaises(BotSeshError): tools.create_room({}, ctx)
    def test_schemas_only_five_non_destructive_tools(self):
        names = {s['name'] for s in tools.SCHEMAS}
        self.assertEqual(names, {'botsesh_create_room', 'botsesh_invite', 'botsesh_rooms', 'botsesh_read', 'botsesh_post'})
        for schema in tools.SCHEMAS:
            self.assertFalse(schema['parameters']['additionalProperties'])
    def test_guarded_error_never_echoes_request(self):
        result = tools.guarded(lambda args: (_ for _ in ()).throw(ValueError('test-only-secret')))({})
        self.assertNotIn('test-only-secret', result)
        self.assertFalse(json.loads(result)['ok'])

class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_final_only_and_failed_turn_does_not_ack(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            client.store.update(lambda d: d['rooms'].update({'a': {'active': True, 'url': 'https://botsesh.com/b/a'}}))
            client.enrolled = Mock(return_value={'number': 8})
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            adapter._current['a'] = {'receipt_id': 'receipt-a', 'target': 3, 'client_id': 'uuid-a', 'delivered': False}
            await adapter.send('a', 'progress')
            self.assertNotIn('receipt_id', client.enrolled.call_args.kwargs)
            # A native refusal/error can also be a notify-marked response: still no attestation.
            await adapter.send('a', 'native admission error', metadata={'notify': True})
            self.assertNotIn('receipt_id', client.enrolled.call_args.kwargs)
            from hermes_botsesh_test.adapter import processed_turn
            processed_turn(user_message='frozen receipt-a context', platform='botsesh')
            await adapter.send('a', 'exact final', metadata={'notify': True})
            self.assertEqual(client.enrolled.call_args.kwargs['receipt_id'], 'receipt-a')
            self.assertEqual(client.enrolled.call_args.kwargs['reply_to'], 3)
            self.assertTrue(client.enrolled.call_args.kwargs['processed'])
            self.assertTrue(adapter._current['a']['delivered'])
            adapter._current['a']['delivered'] = False
            from gateway.platforms.event import ProcessingOutcome
            await adapter.on_processing_complete(Mock(source=Mock(chat_id='a')), ProcessingOutcome.FAILURE)
            self.assertNotIn('handled', client.store.room('a'))

    async def test_durable_generated_final_replays_without_inference(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            payload = {'text': 'exact already generated answer', 'client_id': 'retained-id', 'receipt_id': 'frozen-receipt', 'processed': True, 'reply_to': 3}
            client.store.update(lambda d: d['rooms'].update({'a': {'active': True, 'url': 'https://botsesh.com/b/a', 'pending_final': {'target': 3, 'payload': payload}}}))
            client.enrolled = Mock(side_effect=[{'number': 8}, {'mentions': [], 'replies': []}])
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            adapter.handle_message = AsyncMock()
            await adapter._check_room('a')
            self.assertEqual(client.enrolled.call_args_list[0].kwargs, payload)
            adapter.handle_message.assert_not_called()
            self.assertNotIn('pending_final', client.store.room('a'))
            self.assertEqual(client.store.room('a')['handled'], [3])

    async def test_cron_transport_binds_exact_host_execution(self):
        from hermes_botsesh_test.adapter import standalone_send
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            client.enrolled = Mock(return_value={'number': 8})
            ctx = Mock()
            ctx.current_cron_execution.return_value = SimpleNamespace(execution_id='run-a', job_id='job-a')
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                self.assertTrue((await standalone_send(None, 'a', 'same exact text', ctx=ctx))['success'])
                first = client.enrolled.call_args.kwargs['client_id']
                await standalone_send(None, 'a', 'same exact text', ctx=ctx)
                self.assertEqual(client.enrolled.call_args.kwargs['client_id'], first)
                ctx.current_cron_execution.return_value = SimpleNamespace(execution_id='run-b', job_id='job-a')
                await standalone_send(None, 'a', 'same exact text', ctx=ctx)
                self.assertNotEqual(client.enrolled.call_args.kwargs['client_id'], first)
                self.assertIn('error', await standalone_send(None, 'a', 'text', media_files=['private-file'], ctx=ctx))

if __name__ == '__main__': unittest.main()
