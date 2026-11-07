"""Offline regressions: real adapter/tool journals, no inference/network."""
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch
from test_solid import SolidTests
from test_cant_do import CantDoNativeTests, REASON
from hermes_botsesh_test import tools
from hermes_botsesh_test.provider_text import visible_text
from hermes_botsesh_test.client import BotSeshError
from gateway.session_context import clear_session_vars

RAW = 'The headline is **Exact café 🐚**.\ue200cite\ue202turn636527view0\ue201\nhttps://news.ycombinator.com/\ue200cite\ue202second\ue201\ue202\U000f0001'
CLEAN = 'The headline is **Exact café 🐚**.\nhttps://news.ycombinator.com/'

class TextTests(unittest.TestCase):
    def test_closed_spans_and_lone_private_use_only(self):
        self.assertEqual(visible_text(RAW), CLEAN)
        self.assertEqual(visible_text('left\ue200\nprivate\ue201right'), 'leftright')
        self.assertEqual(visible_text('Keep \ue200unclosed prose\ue202 and café 🙂'), 'Keep unclosed prose and café 🙂')
        self.assertEqual(visible_text(CLEAN), CLEAN)

    def test_post_tool_sanitizes_before_identity_without_mutating_args(self):
        args = {'room_id': 'a', 'text': RAW, 'receipt_id': 'exact', 'processed': True, 'reply_to': 3}
        client = Mock()
        with patch.object(tools, 'Client', return_value=client), patch.object(tools, 'scoped_room', return_value='a'):
            tools.post(args, Mock())
        self.assertEqual(args['text'], RAW)
        client.logical_id.assert_called_once_with('post', {**args, 'text': CLEAN})
        client.enrolled.assert_called_once_with('a', 'post', text=CLEAN, client_id=client.logical_id.return_value, receipt_id='exact', processed=True, reply_to=3)

class FinalTests(SolidTests):
    async def test_final_is_clean_before_gateway_and_local_journals(self):
        await self.adapter._check_room('a')
        self.adapter._current['a']['model_processed'] = True
        event = self.adapter.handle_message.call_args.args[0]
        parent = next(cls for cls in type(self.adapter).__mro__[1:] if 'send_final_ledgered' in cls.__dict__)
        with patch.object(parent, 'send_final_ledgered', new_callable=AsyncMock, return_value=('sent', self.adapter)) as ledger:
            await self.adapter.send_final_ledgered(event, 'session', RAW, {}, reply_to='3')
        self.assertEqual(ledger.call_args.args[2], CLEAN)
        pending = self.client.store.room('a')['pending_final']['payload']
        self.assertEqual(pending['text'], CLEAN)
        self.assertTrue((await self.adapter.send('a', CLEAN, metadata={'notify': True})).success)
        self.assertEqual(next(iter(self.posts.values()))['text'], CLEAN)

    async def test_direct_final_and_retry_share_clean_payload(self):
        await self.adapter._check_room('a')
        self.adapter._current['a']['model_processed'] = True
        self.assertTrue((await self.adapter.send('a', RAW, metadata={'notify': True})).success)
        self.assertEqual(next(iter(self.posts.values()))['text'], CLEAN)
        pending = self.client.store.room('a')['pending_final']['payload']
        self.assertEqual(pending['text'], CLEAN)
        from hermes_botsesh_test.adapter import _GATEWAY_RECOVERY_MARKERS
        self.assertTrue((await self.adapter.send('a', _GATEWAY_RECOVERY_MARKERS[0] + CLEAN, metadata={})).success)
        self.assertEqual(len(self.posts), 1)

    async def test_pre_upgrade_uncertain_replay_keeps_original_bytes(self):
        payload = {'text': RAW, 'client_id': str(uuid.uuid4())}
        self.adapter._journal_gateway_delivery('a', payload)
        self.assertTrue((await self.adapter.send('a', RAW)).success)
        self.assertEqual(self.posts[payload['client_id']], payload)

class WorkingTests(CantDoNativeTests):
    async def test_confirmed_cant_do_never_renews_working(self):
        tokens = await self.prepare()
        try:
            await self.adapter.send_typing('a')
            from hermes_botsesh_test.task_failure import cant_do
            cant_do(REASON)
            before = len(self.calls)
            self.adapter._typing_at.clear()
            await self.adapter.send_typing('a')
            self.assertEqual(len(self.calls), before)
        finally:
            clear_session_vars(tokens)

    async def test_uncertain_cant_do_also_fences_typing(self):
        tokens = await self.prepare()
        original = self.client.enrolled.side_effect
        def lose(room, op, **args):
            if op == 'fail_task':
                raise BotSeshError('connection_failed_retry_same_intent')
            return original(room, op, **args)
        self.client.enrolled.side_effect = lose
        try:
            from hermes_botsesh_test.task_failure import cant_do
            with self.assertRaises(BotSeshError):
                cant_do(REASON)
            before = len(self.calls)
            await self.adapter.send_typing('a')
            self.assertEqual(len(self.calls), before)
        finally:
            clear_session_vars(tokens)

def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (TextTests, FinalTests, WorkingTests):
        for name in cls.__dict__:
            if name.startswith('test_'):
                suite.addTest(cls(name))
    return suite
