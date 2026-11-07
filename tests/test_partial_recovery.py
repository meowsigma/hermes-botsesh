"""Local Python -> Node bridge -> vendored SDK witness over offline HTTP fixture."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock
from test_plugin import ROOT, Client, Store, tools
from hermes_botsesh_test.client import node_binary, child_env

class PartialRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_partial_tool_send_holds_newer_and_recovers_exactly(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        from gateway.platforms.event import ProcessingOutcome
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            fixture = ROOT / 'tests' / 'partial-fixture.mjs'
            client.command = lambda: [node_binary(), str(fixture), str(client.store.credentials)]
            subprocess.run([*client.command(), 'seed'], check=True, capture_output=True, env=child_env(client.store))
            client.store.update(lambda d: d['rooms'].update({'offline': {'active': True, 'url': 'https://botsesh.com/b/offline'}}))
            read = client.enrolled('offline', 'read', target_number=7)
            original_id = '5c1cfd9c-fdbe-4ef4-9f61-30a6561f41d9'
            text = 'Exact final.\nUnicode: café 🐚\n  retained spacing\n'
            args = {'room_id': 'offline', 'text': text, 'client_id': original_id, 'receipt_id': read['receipt_id'], 'processed': True, 'reply_to': 7, 'draft_ready': {'title': 'Synthetic draft', 'recipientId': '7f5081ed-bb74-4304-9fb7-12bd396831f7', 'blockers': []}}
            with patch.object(tools, 'Client', return_value=client), patch.object(tools, 'calling_scope', return_value=('botsesh', 'offline')):
                failed = json.loads(tools.guarded(lambda a: tools.post(a, Mock()))(args))
            self.assertFalse(failed['ok'])
            self.assertEqual((failed['status'], failed['pending']['number'], failed['pending']['draft_ready']), (403, 8, 'denied'))
            self.assertIn('Reply #8 posted.', failed['notice'])
            self.assertNotIn('nothing posted', failed['notice'].lower())
            state_path = client.store.credentials / 'fixture.json'
            state = json.loads(state_path.read_text())
            state['newer'] = True
            state_path.write_text(json.dumps(state))
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            adapter._current['offline'] = {'receipt_id': read['receipt_id'], 'target': 7, 'client_id': 'b60d1d17-9daf-4bad-930a-e84abc1ae4cb', 'delivered': False, 'model_processed': True}
            # Simulate lost Python status write; the durable SDK journal survives.
            client.store.update(lambda d: d['rooms']['offline'].pop('pending_completion'))
            result = await adapter.send('offline', 'Nothing posted; start over.', metadata={'notify': True})
            self.assertFalse(result.success)
            self.assertIn('Reply #8 posted.', result.error)
            self.assertNotIn('pending_final', client.store.room('offline'))
            await adapter.on_processing_complete(Mock(source=Mock(chat_id='offline'), message_id='7'), ProcessingOutcome.FAILURE)
            adapter.handle_message = AsyncMock()
            await adapter._check_room('offline')
            adapter.handle_message.assert_not_called()
            self.assertNotIn('handled', client.store.room('offline'))
            with patch.object(tools, 'Client', return_value=client):
                status = json.loads(tools.command('status', platform='cli'))
                self.assertEqual(status['rooms'][0]['pending_completion']['number'], 8)
                self.assertIn('denied', tools.command(f'retry offline {original_id}', platform='cli'))
                self.assertIn('local Hermes', tools.command(f'retry offline {original_id}', platform='botsesh', source=Mock(chat_id='offline')))
            state = json.loads(state_path.read_text())
            self.assertEqual((len(state['messages']), state['checkpoint'], len(state['acks'])), (1, 70, 1))
            state['claim'] = True  # Legitimate fixture actor, never plugin-acquired authority.
            state_path.write_text(json.dumps(state))
            with patch.object(tools, 'Client', return_value=client):
                completed = json.loads(tools.command(f'retry offline {original_id}', platform='cli'))
            self.assertEqual(completed['number'], 8)
            self.assertNotIn('pending_completion', client.store.room('offline'))
            adapter._is_sender_authorized = lambda author, *a: author == '7f5081ed-bb74-4304-9fb7-12bd396831f7'
            async def admitted(event):
                event._gateway_accepted = True
            adapter.handle_message = AsyncMock(side_effect=admitted)
            await adapter._check_room('offline')
            self.assertEqual(adapter.handle_message.call_args.args[0].message_id, '10')
            state = json.loads(state_path.read_text())
            self.assertEqual((len(state['messages']), state['checkpoint'], len(state['acks'])), (1, 70, 1))
            self.assertEqual(state['messages'][0]['text'], text)
            self.assertEqual(state['commands'][0], state['commands'][1])
            self.assertEqual(state['commands'][0], state['commands'][2])
            self.assertFalse(any('/claim' in r['path'] for r in state['requests']))
            print('HACK5 Python/Node witness: tool403 reports post8; false recovery final suppressed; newer10 held then admitted after exact local retry; one reply, frozen ack70 only')
