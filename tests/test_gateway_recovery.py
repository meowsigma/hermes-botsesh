"""Offline bridge/SDK + real Hermes ledger dispatcher; no live/model calls."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock
from test_plugin import ROOT, Client, Store, BotSeshError
from hermes_botsesh_test.client import node_binary, child_env


class GatewayRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_lost_ack_reconnect_and_boot_replay_exact_saved_operation(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        from gateway.platforms.event import ProcessingOutcome
        from gateway.delivery_ledger import RECOVERED_MARKER, RECONNECTED_MARKER, FLOOD_MARKER
        from gateway.run_startup import GatewayStartupMixin
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            fixture = ROOT / 'tests' / 'partial-fixture.mjs'
            client.command = lambda: [node_binary(), str(fixture), str(client.store.credentials)]
            subprocess.run([*client.command(), 'seed'], check=True, capture_output=True, env=child_env(client.store))
            client.store.update(lambda d: d['rooms'].update({'offline': {'active': True, 'url': 'https://botsesh.com/b/offline'}}))
            read = client.enrolled('offline', 'read', target_number=7)
            text = 'Preorder by Thursday noon.\nFriday boxes: $18.\nPickup Friday, 3–6 pm.\n'
            original_id = '41fe858d-6386-4cfb-9a8e-6be797733cdd'
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            adapter._current['offline'] = {'receipt_id': read['receipt_id'], 'target': 7, 'client_id': original_id, 'delivered': False, 'model_processed': True}
            real_enrolled = client.enrolled
            def lose_output(room, op, **args):
                result = real_enrolled(room, op, **args)
                if op == 'post':
                    raise BotSeshError('operation_timeout_retry_same_intent')
                return result
            with patch.object(client, 'enrolled', side_effect=lose_output):
                result = await adapter.send('offline', text, metadata={'notify': True})
            self.assertFalse(result.success)
            await adapter.on_processing_complete(Mock(source=Mock(chat_id='offline'), message_id='7'), ProcessingOutcome.FAILURE)
            # Fresh adapter: the gateway no longer supplies current/notify/receipt on replay.
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                restarted = BotSeshAdapter(PlatformConfig())
            runner = Mock()
            runner._obligation_adapter = AsyncMock(return_value=restarted)
            runner._arm_flood_timers_for_waiting_rows = AsyncMock()
            for marker in (RECONNECTED_MARKER, RECOVERED_MARKER, FLOOD_MARKER, ''):
                row = {'content': text, 'chat_id': 'offline', 'thread_id': None, 'needs_marker': bool(marker), 'marker': marker, 'obligation_id': original_id, 'platform': 'botsesh', 'attempts': 1}
                with patch('gateway.delivery_ledger.mark_delivered') as delivered, patch('gateway.delivery_ledger.mark_failed') as failed:
                    count = await GatewayStartupMixin._redeliver_claimed_obligations(runner, [row])
                self.assertEqual(count, 1)
                delivered.assert_called_once_with(original_id)
                failed.assert_not_called()
            state = json.loads((client.store.credentials / 'fixture.json').read_text())
            self.assertEqual(len(state['messages']), 1)
            self.assertEqual(state['messages'][0]['text'], text)
            self.assertEqual(len(state['acks']), 1)
            posts = [r['body'] for r in state['requests'] if r['path'] == '/messages' and r['method'] == 'POST']
            self.assertEqual(len(posts), 1)  # Completed bridge replay verifies, does not send again.
            self.assertEqual(client.store.room('offline')['handled'], [7])
            # Unknown or modified wrapped text never creates a substitute message.
            unknown = await restarted.send('offline', RECONNECTED_MARKER + text + 'changed')
            self.assertFalse(unknown.success)
            unknown_marker = await restarted.send('offline', '♻️ Recovered reply — unfamiliar transport marker:\n\n' + text)
            self.assertFalse(unknown_marker.success)
            self.assertEqual(client.store.room('offline')['gateway_deliveries'][original_id]['payload'], {'text': text, 'client_id': original_id, 'receipt_id': read['receipt_id'], 'processed': True, 'reply_to': 7})
            # Exact replay still verifies live membership; it is not a cached auth bypass.
            state['revoked'] = True
            (client.store.credentials / 'fixture.json').write_text(json.dumps(state))
            denied = await restarted.send('offline', RECOVERED_MARKER + text)
            self.assertFalse(denied.success)
            self.assertFalse(client.store.read()['rooms']['offline']['active'])
            print('HACK5 recovery: real core dispatcher + SDK replay retained one exact reply/ack through reconnect, restart and flood markers; changed text and revoked access denied')

    async def test_full_journal_posts_new_final_and_refuses_evicted_replay(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        from gateway.platforms.event import ProcessingOutcome
        from gateway.delivery_ledger import RECONNECTED_MARKER
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            fixture = ROOT / 'tests' / 'partial-fixture.mjs'
            client.command = lambda: [node_binary(), str(fixture), str(client.store.credentials)]
            subprocess.run([*client.command(), 'seed'], check=True, capture_output=True, env=child_env(client.store))
            # Descending IDs prove FIFO is insertion order, not lexical UUID order.
            records = {f'{1000-n:08x}-6386-4cfb-9a8e-6be797733cdd': {'payload': {'client_id': f'{1000-n:08x}-6386-4cfb-9a8e-6be797733cdd', 'text': f'Archived answer {n}'}, 'target': None} for n in range(1000)}
            oldest_id, second_id = list(records)[:2]
            oldest_text = records[oldest_id]['payload']['text']
            client.store.update(lambda d: d['rooms'].update({'offline': {'active': True, 'url': 'https://botsesh.com/b/offline', 'gateway_deliveries': records}}))
            read = client.enrolled('offline', 'read', target_number=7)
            current_id = '41fe858d-6386-4cfb-9a8e-6be797733cdd'
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            adapter._current['offline'] = {'receipt_id': read['receipt_id'], 'target': 7, 'client_id': current_id, 'delivered': False, 'model_processed': True}
            sent = await adapter.send('offline', 'The 1001st final.', metadata={'notify': True})
            self.assertTrue(sent.success, sent.error)
            room = client.store.room('offline')
            self.assertEqual(len(room['gateway_deliveries']), 1000)
            self.assertNotIn(oldest_id, room['gateway_deliveries'])
            self.assertIn(second_id, room['gateway_deliveries'])
            self.assertEqual(room['gateway_deliveries'][current_id], room['pending_final'])
            await adapter.on_processing_complete(Mock(source=Mock(chat_id='offline'), message_id='7'), ProcessingOutcome.SUCCESS)
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                restarted = BotSeshAdapter(PlatformConfig())
            refused = await restarted.send('offline', RECONNECTED_MARKER + oldest_text)
            self.assertFalse(refused.success)
            self.assertIn('gateway_replay_unbound_or_ambiguous', refused.error)
            self.assertNotIn('pending_final', client.store.room('offline'))
            state = json.loads((client.store.credentials / 'fixture.json').read_text())
            self.assertEqual([m['text'] for m in state['messages']], ['The 1001st final.'])
            self.assertEqual(len([r for r in state['requests'] if r['path'] == '/messages' and r['method'] == 'POST']), 1)
            self.assertEqual(len(state['acks']), 1)

    async def test_journal_refusal_does_not_create_pending_final(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            saved = {'payload': {'client_id': 'same-id', 'text': 'Original content'}, 'target': 3}
            client.store.update(lambda d: d['rooms'].update({'a': {'active': True, 'url': 'https://botsesh.com/b/a', 'gateway_deliveries': {'same-id': saved}}}))
            client.enrolled = Mock(return_value={})
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            adapter._current['a'] = {'receipt_id': 'receipt-a', 'target': 7, 'client_id': 'same-id', 'delivered': False, 'model_processed': True}
            refused = await adapter.send('a', 'Changed content', metadata={'notify': True})
            self.assertFalse(refused.success)
            self.assertIn('pending_send_mismatch', refused.error)
            room = client.store.room('a')
            self.assertNotIn('pending_final', room)
            self.assertEqual(room['gateway_deliveries'], {'same-id': saved})
            self.assertFalse(adapter._current['a']['delivered'])
            client.enrolled.assert_called_once_with('a', 'pending')  # No post.

    async def test_duplicate_text_across_distinct_turns_is_ambiguous(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry, platform_registry
        from gateway.delivery_ledger import RECONNECTED_MARKER
        platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, 'botsesh')
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'private'))
            client.store.update(lambda d: d['rooms'].update({'a': {'active': True, 'url': 'https://botsesh.com/b/a', 'gateway_deliveries': {k: {'payload': {'text': 'Same answer', 'client_id': k}, 'target': n} for k, n in [('first', 1), ('second', 2)]}}}))
            client.enrolled = Mock()
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            result = await adapter.send('a', RECONNECTED_MARKER + 'Same answer')
            self.assertFalse(result.success)
            client.enrolled.assert_not_called()
