"""Focused offline tests. These are doubles, never native-agent evidence."""
# Owner 2026-11-10 living chat: runtime notices never go to rooms; read truth stays exact.
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

    def test_new_guest_creation_routes_python_legacy_bindings_keep_bridge(self):
        client = Client(self.store)
        result = {'share_link': 'https://botsesh.com/b/abc', 'management_only': True}
        with patch('hermes_botsesh_test.guest_manager.GuestManagerClient') as python, patch.object(client, 'call') as bridge:
            python.return_value.create.return_value = result
            self.assertEqual(client.create('test'), result)
            python.return_value.create.assert_called_once_with('test', None, None)
            bridge.assert_not_called()
        self.store.update(lambda d: d['rooms'].update(legacy={'active': True, 'url': 'https://botsesh.com/b/legacy'}))
        with patch.object(client, 'call', return_value={'state': 'verified'}) as bridge:
            client.enrolled('legacy', 'verify')
            bridge.assert_called_once_with('verify', room='https://botsesh.com/b/legacy')

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
        ctx = Mock()
        with patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            with self.assertRaises(BotSeshError): tools.scoped_room({'room_id': 'b'}, ctx)
            self.assertEqual(tools.scoped_room({'room_id': 'a'}, ctx), 'a')
    def test_public_session_surface_and_missing_chat_fail_closed(self):
        ctx = Mock()
        ctx.session_surface.return_value = 'botsesh'
        with patch('gateway.session_context.get_session_env', return_value='') as get_env:
            with self.assertRaises(BotSeshError): tools.scoped_room({'room_id': 'a'}, ctx)
            get_env.assert_called_once_with('HERMES_SESSION_CHAT_ID', '')
        ctx.session_surface.assert_called_once_with()
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
    def test_schemas_only_scoped_non_destructive_tools(self):
        names = {s['name'] for s in tools.SCHEMAS}
        self.assertEqual(names, {'botsesh_join', 'botsesh_create_room', 'botsesh_offer_ownership', 'botsesh_revoke_ownership_offer', 'botsesh_invite', 'botsesh_rooms', 'botsesh_read', 'botsesh_post', 'botsesh_pass', 'botsesh_cant_do', 'botsesh_takeover_claim'})
        for schema in tools.SCHEMAS:
            self.assertEqual(schema['parameters']['additionalProperties'], schema['name'] in ('botsesh_post', 'botsesh_read'))
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
            client.enrolled.assert_not_called()  # Native progress is local only.
            # A native refusal/error can also be a notify-marked response: still no attestation.
            await adapter.send('a', 'native admission error', metadata={'notify': True})
            client.enrolled.assert_not_called()  # Advanced runtime failures are local status only.
            self.assertEqual(client.store.room('a')['processing_receipts']['3']['state'], 'failed')
            from hermes_botsesh_test.adapter import processed_turn, ended_turn
            prompt = json.dumps({'receipt_id': 'receipt-a', 'addressed_message_number': 4, 'context': 'frozen context'})
            adapter._current['a'] = {'receipt_id': 'receipt-a', 'target': 4, 'client_id': 'uuid-b', 'delivered': False, 'text': prompt}
            processed_turn(user_message=prompt, platform='botsesh', session_id='s', turn_id='t')
            self.assertNotIn('model_processed', adapter._current['a'])
            ended_turn(platform='botsesh', session_id='s', turn_id='t', completed=True, failed=False, interrupted=False)
            await adapter.send('a', 'exact final', metadata={'notify': True})
            self.assertEqual(client.enrolled.call_args.kwargs['receipt_id'], 'receipt-a')
            self.assertEqual(client.enrolled.call_args.kwargs['reply_to'], 4)
            self.assertTrue(client.enrolled.call_args.kwargs['processed'])
            self.assertTrue(adapter._current['a']['delivered'])
            adapter._current['a']['delivered'] = False
            from gateway.platforms.event import ProcessingOutcome
            await adapter.on_processing_complete(Mock(source=Mock(chat_id='a'), message_id='4'), ProcessingOutcome.FAILURE)
            self.assertNotIn('handled', client.store.room('a'))

    async def test_skipped_targets_do_not_starve_later_mentions(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        from gateway.platforms.event import ProcessingOutcome
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        for reason in ('unauthorized', 'own', 'dedup', 'rejected', 'failed', 'unverified', 'mismatched', 'missing_author', 'context_limit'):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                client = Client(Store(Path(tmp) / 'private'))
                client.store.update(lambda d: d['rooms'].update({'a': {'active': True, 'url': 'https://botsesh.com/b/a'}}))
                reads, dispatched = [], []
                def enrolled(room_id, operation, **kwargs):
                    if operation == 'check':
                        return {'mentions': [3, 5], 'replies': [3]}
                    if operation == 'post':
                        # New contract: failures are visible, but NEVER attest reads.
                        self.assertNotIn('receipt_id', kwargs)
                        self.assertNotIn('processed', kwargs)
                        self.assertEqual("Hermes couldn't do it.", kwargs['text'])
                        return {'number': 8}
                    self.assertEqual(operation, 'read')
                    target = kwargs['target_number']
                    reads.append(target)
                    author = 'stranger' if target == 3 and reason == 'unauthorized' else 'owner'
                    return {'receipt_id': f'receipt-{target}', 'batch': {'messages': [{
                        'id': f'message-{target}', 'number': target,
                        'isOwn': target == 3 and reason == 'own',
                        'senderParticipantId': None if target == 3 and reason == 'missing_author' else author,
                        'authorParticipant': {
                            'id': 'different' if target == 3 and reason == 'mismatched' else author,
                            'verified': not (target == 3 and reason == 'unverified'), 'name': author,
                        },
                        'text': 'x' * 160001 if target == 3 and reason == 'context_limit' else 'question',
                    }]}}
                client.enrolled = Mock(side_effect=enrolled)
                with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                    adapter = BotSeshAdapter(PlatformConfig())
                adapter._is_sender_authorized = lambda author, *args: author == 'owner'
                async def handle(event):
                    accepted = not (reason == 'rejected' and event.message_id == '3')
                    if accepted:
                        dispatched.append(event)
                    return accepted
                adapter._admit_event = AsyncMock(side_effect=handle)
                if reason == 'dedup':
                    client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={'3': {'target': 3, 'state': 'replied'}}))
                await adapter._check_room('a')
                if reason == 'failed':
                    self.assertEqual([e.message_id for e in dispatched], ['3'])
                    await adapter.on_processing_complete(dispatched[-1], ProcessingOutcome.FAILURE)
                    await adapter._check_room('a')
                self.assertEqual([e.message_id for e in dispatched], ['3', '5'] if reason == 'failed' else ['5'])
                # Both failed turns and skips remain unhandled, with no automatic inference retry.
                await adapter.on_processing_complete(dispatched[-1], ProcessingOutcome.FAILURE)
                for _ in range(4):
                    await adapter._check_room('a')
                self.assertEqual(reads, [5] if reason == 'dedup' else [3, 5])
                self.assertNotIn('handled', client.store.room('a'))
                self.assertNotIn('pending_final', client.store.room('a'))
                self.assertNotIn('a', adapter._current)

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
            adapter._skipped_targets.add(('a', 3))  # A failed turn must not suppress its saved final.
            adapter._admit_event = AsyncMock()
            await adapter._check_room('a')
            self.assertEqual(client.enrolled.call_args_list[0].kwargs, payload)
            adapter._admit_event.assert_not_called()
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
