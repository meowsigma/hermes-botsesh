"""Focused offline tests for 0.9.0 live frames (doubles, never native-agent evidence).

Draft coalescing and seq, activity mapping/redaction, status frame from Hermes's own state,
draft_id binding only after an accepted draft, capability fallback, steer/withdraw handling and
manifest v1.1 fields with a v1.0 fallback.
"""
import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_plugin import package, Store
from hermes_botsesh_test import live as live_mod
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test import command_catalog

ROOM = 'dmroom'


class FakeClient:
    def __init__(self, store):
        self.store = store
        self.frames = []
        self.result = {'accepted': True}
        self.error = None

    def enrolled(self, room_id, op, **args):
        assert op == 'live', op
        self.frames.append((time.monotonic(), args['frame']))
        if self.error:
            raise self.error
        return dict(self.result, seq=args['frame']['seq'])


def adapter_double(room_type='dm'):
    tmp = tempfile.TemporaryDirectory()
    store = Store(Path(tmp.name) / 'plugin')
    store.update(lambda d: d['rooms'].update({ROOM: {'room_id': ROOM, 'active': True, 'transport': 'oauth', 'room_type': room_type}}))
    adapter = SimpleNamespace(client=FakeClient(store), _current={ROOM: {'target': 7}}, _tmp=tmp)
    adapter.live = live_mod.LiveRelay(adapter)
    return adapter


async def settle(relay, seconds=0.05):
    await asyncio.sleep(seconds)
    while relay._tasks:
        await asyncio.gather(*list(relay._tasks), return_exceptions=True)
        await asyncio.sleep(0.01)


class DraftFrames(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = adapter_double()
        self.relay = self.adapter.live
        self.relay.attach(asyncio.get_running_loop())

    async def asyncTearDown(self):
        self.adapter._tmp.cleanup()

    async def test_deltas_coalesce_to_at_most_four_full_snapshots_per_second(self):
        self.relay._on_stream_start(ROOM, 7)
        for i in range(40):
            self.relay._on_delta(ROOM, 7, 'w%d ' % i)
            await asyncio.sleep(0.02)
        await settle(self.relay, 0.4)
        drafts = [(at, f) for at, f in self.adapter.client.frames if f['kind'] == 'draft']
        self.assertGreaterEqual(len(drafts), 2)
        span = drafts[-1][0] - drafts[0][0]
        self.assertLessEqual(len(drafts), int(span / live_mod.DRAFT_INTERVAL) + 2)  # <= 4/s
        # Full snapshot, one draftId per turn, strictly increasing seq, exact final text.
        self.assertEqual(drafts[-1][1]['text'], ''.join('w%d ' % i for i in range(40)))
        self.assertEqual({f['draftId'] for _, f in drafts}, {drafts[0][1]['draftId']})
        seqs = [f['seq'] for _, f in drafts]
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertTrue(all(f['version'] == 1 and f['turnTarget'] == 7 for _, f in drafts))

    async def test_new_model_response_restarts_text_and_other_turns_are_ignored(self):
        self.relay._on_delta(ROOM, 7, 'Let me check.')
        await settle(self.relay, 0.3)
        self.relay._on_stream_start(ROOM, 7)
        self.relay._on_delta(ROOM, 7, 'Answer.')
        self.relay._on_delta(ROOM, 8, 'not the current turn')
        await settle(self.relay, 0.6)
        texts = [f['text'] for _, f in self.adapter.client.frames if f['kind'] == 'draft']
        self.assertEqual(texts[-1], 'Answer.')
        self.assertNotIn('not the current turn', ''.join(texts))

    async def test_draft_id_binds_the_final_only_after_an_accepted_draft(self):
        self.assertIsNone(self.relay.draft_id_for(ROOM, 7))
        self.adapter.client.result = {'accepted': False, 'reason': 'stale_seq'}
        self.relay._on_delta(ROOM, 7, 'x')
        await settle(self.relay, 0.3)
        self.assertIsNone(self.relay.draft_id_for(ROOM, 7))
        self.adapter.client.result = {'accepted': True}
        self.relay._on_delta(ROOM, 7, 'y')
        await settle(self.relay, 0.3)
        draft_id = self.relay.draft_id_for(ROOM, 7)
        self.assertEqual(draft_id, self.relay._turns[ROOM].draft_id)
        self.relay.close_drafts(ROOM, 7)
        before = len(self.adapter.client.frames)
        self.relay._on_delta(ROOM, 7, 'late')
        await settle(self.relay, 0.3)
        self.assertEqual(len(self.adapter.client.frames), before)  # No draft after the final.
        await self.relay.end_turn(ROOM, 7)
        kinds = [f['kind'] for _, f in self.adapter.client.frames[before:]]
        self.assertEqual(kinds[0], 'clear')

    async def test_older_server_disables_live_without_touching_the_durable_path(self):
        self.adapter.client.error = BotSeshError('mcp_operation_denied')
        self.relay._on_delta(ROOM, 7, 'x')
        await settle(self.relay, 0.3)
        self.assertFalse(self.relay.available(ROOM))
        count = len(self.adapter.client.frames)
        self.relay._on_delta(ROOM, 7, 'y')
        await settle(self.relay, 0.3)
        self.assertEqual(len(self.adapter.client.frames), count)
        self.assertIsNone(self.relay.draft_id_for(ROOM, 7))

    async def test_rate_limit_and_forbidden_do_not_disable_live(self):
        for error in (BotSeshError('live_rate', 429), BotSeshError('live_forbidden', 403, rejected=True)):
            self.adapter.client.error = error
            self.relay._on_delta(ROOM, 7, 'x')
            await settle(self.relay, 0.3)
            self.assertTrue(self.relay.available(ROOM))

    async def test_stop_clears_and_late_events_do_not_reopen(self):
        self.relay._on_delta(ROOM, 7, 'working on it')
        await settle(self.relay, 0.3)
        await self.relay.clear_now(ROOM)
        self.assertEqual(self.adapter.client.frames[-1][1]['kind'], 'clear')
        count = len(self.adapter.client.frames)
        self.relay._on_delta(ROOM, 7, 'more')
        self.relay._on_tool(ROOM, 7, 'c1', 'terminal', 'sleep 9', 'running', None)
        await settle(self.relay, 0.6)
        self.assertEqual(len(self.adapter.client.frames), count)


class ActivityFrames(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = adapter_double()
        self.relay = self.adapter.live
        self.relay.attach(asyncio.get_running_loop())

    async def asyncTearDown(self):
        self.adapter._tmp.cleanup()

    async def test_steps_map_start_and_finish_with_bounded_redacted_preview(self):
        secret = 'sk-or-v1-' + 'a' * 48
        preview = live_mod.tool_preview('terminal', {'command': 'curl -H "Authorization: Bearer %s" https://x.test' % secret})
        self.assertNotIn(secret, preview)
        self.assertLessEqual(len(preview), 200)
        self.relay._on_tool(ROOM, 7, 'call-1', 'terminal', preview, 'running', None)
        self.relay._on_tool(ROOM, 7, 'call-2', 'read_file', 'notes.md', 'running', None)
        await settle(self.relay, 0.7)
        self.relay._on_tool(ROOM, 7, 'call-1', 'terminal', None, 'done', int(time.time() * 1000))
        self.relay._on_tool(ROOM, 7, 'call-2', 'read_file', None, 'failed', int(time.time() * 1000))
        await settle(self.relay, 0.7)
        frames = [f for _, f in self.adapter.client.frames if f['kind'] == 'activity']
        last = frames[-1]['steps']
        self.assertEqual([(s['id'], s['tool'], s['state']) for s in last],
                         [('call-1', 'terminal', 'done'), ('call-2', 'read_file', 'failed')])
        self.assertTrue(all('endedAt' in s and s['endedAt'] >= s['startedAt'] for s in last))
        self.assertNotIn(secret, json.dumps(frames))
        for frame in frames:  # Never full arguments or results.
            self.assertEqual(set(frame), {'version', 'kind', 'turnTarget', 'steps', 'seq'})

    async def test_completion_without_start_is_not_reconstructed_and_steps_are_capped(self):
        self.relay._on_tool(ROOM, 7, 'ghost', 'terminal', None, 'done', 1)
        for i in range(60):
            self.relay._on_tool(ROOM, 7, 'c%d' % i, 'terminal', '', 'running', None)
        await settle(self.relay, 0.7)
        last = [f for _, f in self.adapter.client.frames if f['kind'] == 'activity'][-1]
        self.assertEqual(len(last['steps']), live_mod.MAX_STEPS)
        self.assertEqual(last['total'], 60)
        self.assertNotIn('ghost', [s['id'] for s in last['steps']])


class StatusFrame(unittest.IsolatedAsyncioTestCase):
    async def test_status_reports_hermes_own_approval_state_and_omits_unknowns(self):
        adapter = adapter_double()
        try:
            entry = SimpleNamespace(session_id='s-1', last_prompt_tokens=1200, total_tokens=5000, estimated_cost_usd=0.0,
                                    cost_status='unknown', created_at=None, display_name=None, model_override=None, yolo=None)
            adapter._dm_contexts = {ROOM: {'human_id': 'h', 'session_key': 'k'}}
            adapter._session_store = SimpleNamespace(lookup_by_session_key=lambda key: entry)
            adapter.build_source = lambda **kw: SimpleNamespace(**kw)
            adapter.gateway_runner = SimpleNamespace(_effective_busy_input_mode=lambda source: 'queue')
            config = {'model': {'default': 'nvidia/nemotron', 'provider': 'openrouter'}, 'agent': {'reasoning_effort': 'medium'}}
            approval = SimpleNamespace(_get_approval_mode=lambda: 'manual')
            hermes_tools = {'tools': SimpleNamespace(), 'tools.approval_context': approval,
                            'tools.approval': SimpleNamespace(is_session_yolo_enabled=lambda key: False)}
            with patch('hermes_cli.config.load_config_readonly', return_value=config), \
                    patch.dict(sys.modules, hermes_tools), \
                    patch('agent.model_metadata.get_model_context_length', return_value=131072):
                status = live_mod.build_status(adapter, ROOM)
                self.assertEqual(status['approvals'], {'mode': 'manual', 'yolo': False})
                entry.yolo = True  # The session's own /yolo toggle.
                self.assertEqual(live_mod.build_status(adapter, ROOM)['approvals'], {'mode': 'manual', 'yolo': True})
                approval._get_approval_mode = Mock(side_effect=RuntimeError)
                self.assertIsNone(live_mod.approval_state(entry, 'k'))  # Unknown, not invented.
            self.assertEqual((status['model'], status['provider'], status['reasoning']), ('nvidia/nemotron', 'openrouter', 'medium'))
            self.assertEqual((status['contextUsed'], status['contextMax'], status['sessionTokens']), (1200, 131072, 5000))
            self.assertEqual(status['busyMode'], 'queue')
            self.assertNotIn('sessionCost', status)  # Unknown cost is omitted, never 0.
        finally:
            adapter._tmp.cleanup()

    async def test_status_is_dm_only(self):
        adapter = adapter_double(room_type='group')
        try:
            adapter.live.attach(asyncio.get_running_loop())
            self.assertIsNone(await adapter.live.publish_status(ROOM))
            self.assertEqual(adapter.client.frames, [])
        finally:
            adapter._tmp.cleanup()


class BusyModeParity(unittest.IsolatedAsyncioTestCase):
    """Telegram parity: Hermes's own /busy mode decides what a plain DM message does mid-turn."""

    def make(self, mode):
        from hermes_botsesh_test import adapter as adapter_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'plugin')
        store.update(lambda d: d['rooms'].update({ROOM: {'room_id': ROOM, 'active': True, 'transport': 'oauth', 'room_type': 'dm', 'name': 'Fairy · DM'}}))
        a = adapter_mod.BotSeshAdapter.__new__(adapter_mod.BotSeshAdapter)
        a.client = SimpleNamespace(store=store)
        a._current = {ROOM: {'target': 7}}
        a._dm_contexts = {ROOM: {'human_id': 'owner', 'generation': 'g'}}
        a._control_context = lambda room: {'human_id': 'owner'} if room == ROOM else None
        a._is_sender_authorized = lambda user, kind, room: user == 'owner'
        a.build_source = lambda **kw: SimpleNamespace(**kw)
        a.gateway_runner = SimpleNamespace(_effective_busy_input_mode=lambda source: mode)
        a.handle_message = AsyncMock()
        a._delivery = AsyncMock()
        a._control_invalidate = AsyncMock()
        return a

    def message(self, text='also check the logs', author='owner'):
        return {'number': 8, 'text': text, 'senderParticipantId': author,
                'authorParticipant': {'id': author, 'verified': True, 'name': 'Owner'}}

    async def test_queue_keeps_it_queued(self):
        a = self.make('queue')
        self.assertFalse(await a._apply_busy_mode(ROOM, 8, self.message()))
        a.handle_message.assert_not_awaited()

    async def test_steer_hands_exact_text_to_the_running_turn_once(self):
        a = self.make('steer')
        self.assertTrue(await a._apply_busy_mode(ROOM, 8, self.message()))
        event = a.handle_message.await_args.args[0]
        self.assertEqual(event.text, '/steer also check the logs')
        self.assertTrue(event.allow_gateway_control)
        a._delivery.assert_awaited_with(ROOM, 8, 'steered')
        self.assertTrue(await a._apply_busy_mode(ROOM, 8, self.message()))  # consumed once
        self.assertEqual(a.handle_message.await_count, 1)

    async def test_interrupt_stops_once_and_the_message_still_runs_next(self):
        a = self.make('interrupt')
        a._live_relay = SimpleNamespace(_spawn=lambda coro: coro.close(), clear_now=AsyncMock())
        self.assertFalse(await a._apply_busy_mode(ROOM, 8, self.message()))
        self.assertFalse(await a._apply_busy_mode(ROOM, 9, self.message()))
        self.assertEqual(a.handle_message.await_count, 1)
        self.assertEqual(a.handle_message.await_args.args[0].text, '/stop')
        self.assertEqual(a._current[ROOM]['busy_interrupt_by'], 8)

    async def test_commands_foreign_authors_and_rooms_are_untouched(self):
        a = self.make('steer')
        self.assertFalse(await a._apply_busy_mode(ROOM, 8, self.message('/status')))
        self.assertFalse(await a._apply_busy_mode(ROOM, 8, self.message(author='someone-else')))
        a.client.store.update(lambda d: d['rooms'][ROOM].update(room_type='group'))
        self.assertFalse(await a._apply_busy_mode(ROOM, 8, self.message()))
        a.handle_message.assert_not_awaited()


class LongDmFinal(unittest.IsolatedAsyncioTestCase):
    """DM P1 server: a long DM final is one message (≤ 32 000), never on an unproven server."""

    def make(self, proven, room_type='dm'):
        from hermes_botsesh_test import adapter as adapter_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'plugin')
        store.update(lambda d: d['rooms'].update({ROOM: {'room_id': ROOM, 'active': True, 'transport': 'oauth', 'room_type': room_type}}))
        a = adapter_mod.BotSeshAdapter.__new__(adapter_mod.BotSeshAdapter)
        calls = []
        a.client = SimpleNamespace(store=store, logical_id=lambda kind, data: '%s-%s' % (kind, data.get('index')),
                                   enrolled=lambda room, op, **kw: calls.append(kw) or {'number': len(calls)})
        if proven:
            a.live._proven.add(ROOM)
        return a, calls

    async def test_proven_dm_sends_one_message_unproven_or_room_keeps_4000_chunks(self):
        text = 'x' * 9000
        a, calls = self.make(True)
        await a._post_payload(ROOM, {'text': text, 'processed': True, 'client_id': 'c1'}, 7)
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]['text'], calls[0]['reply_limit']), (text, 32000))
        for proven, kind in ((False, 'dm'), (True, 'group')):
            a, calls = self.make(proven, kind)
            await a._post_payload(ROOM, {'text': text, 'processed': True, 'client_id': 'c1'}, 7)
            self.assertEqual([len(c['text']) for c in calls], [4000, 4000, 1000])
            self.assertTrue(all('reply_limit' not in c for c in calls))


class ManifestV11(unittest.TestCase):
    def test_v11_fields_and_exact_v10_fallback_shape(self):
        command = SimpleNamespace(name='busy', category='Configuration', subcommands=('queue', 'steer', 'interrupt', '--global'))
        row = command_catalog._v11({'name': 'busy', 'description': 'd', 'options': [{'id': 'queue', 'label': 'queue'}]}, command)
        self.assertEqual((row['category'], row['kind'], row['subcommands']), ('Configuration', 'action', ['queue', 'steer', 'interrupt']))
        self.assertEqual(command_catalog._kind(SimpleNamespace(name='status')), 'info')
        self.assertEqual(command_catalog._kind(SimpleNamespace(name='model')), 'picker')
        self.assertEqual(command_catalog.v10([row]), [{'name': 'busy', 'description': 'd', 'options': [{'id': 'queue', 'label': 'queue'}]}])


if __name__ == '__main__':
    unittest.main()
