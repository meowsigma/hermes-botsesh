"""0.9.0 real-server findings, as focused doubles (not native-model evidence).

Found against a local BotSesh server with a real scratch Hermes: one refused input (a target read
refused as already admitted/uncertain, or a definitive refusal of a status-only failure report)
used to raise out of the room check, so every later message, including /stop, waited behind a
growing backoff. Each now settles once, honestly, and the next input is still admitted.
"""
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_plugin import BotSeshError
import test_ongoing_turns


class RoomLoopUnwedge(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        test_ongoing_turns.OngoingTurns.setUp(self)  # Same real adapter fixture, not its tests.
        self.message4 = dict(self.message, number=4, text='Later question')
        self.frozen4 = dict(receipt_id='batch4', batch={'messages': [self.message4]})

    async def test_refused_target_read_settles_as_interrupted_and_later_input_is_admitted(self):
        reads = []
        def enrolled(room, op, **kw):
            if op == 'check':
                return {'mentions': [3, 4], 'replies': []}
            if op == 'read':
                reads.append(kw['target_number'])
                if kw['target_number'] == 3:
                    raise BotSeshError('mcp_operation_denied', rejected=True)  # "already admitted or uncertain"
                return self.frozen4
            if op == 'tool_policy':
                return dict(self.policy, number=4, batch_id='batch4')
            self.fail(op)
        self.client.enrolled = Mock(side_effect=enrolled)
        self.a._admit_event = AsyncMock(return_value=True)
        await self.a._check_room('room')
        self.assertEqual(reads, [3, 4])
        self.a._delivery.assert_any_await('room', 3, 'failed', failure_code='interrupted')
        self.a._admit_event.assert_awaited_once()
        self.assertEqual(self.a._current['room']['target'], 4)
        self.assertEqual(self.posts, [])  # Never replayed or answered with invented text.

    async def test_definitive_refusal_of_failure_report_does_not_raise_and_is_not_retried(self):
        self.a._queue_failure('room', 3, 'runtime_error')
        self.a._delivery = AsyncMock(side_effect=BotSeshError('mcp_operation_denied', rejected=True))
        await self.a._flush_failures('room')  # No exception: the room loop keeps going.
        room = self.client.store.room('room')
        self.assertEqual(room['failure_outbox'], {})
        self.assertEqual(room['processing_receipts']['3']['failure_report_rejected']['code'], 'mcp_operation_denied')
        # An uncertain transport failure is still retried with the same intent.
        self.a._queue_failure('room', 5, 'runtime_error')
        self.a._delivery = AsyncMock(side_effect=BotSeshError('connection_failed_retry_same_intent'))
        with self.assertRaises(BotSeshError):
            await self.a._flush_failures('room')
        self.assertIn('5', self.client.store.room('room')['failure_outbox'])

    async def test_a_refused_pass_settles_the_turn_once_instead_of_wedging(self):
        # Live finding: a definitively refused finish_room_turn pass was re-raised on every check.
        self.a._delivery = AsyncMock()
        current = {'target': 3, 'receipt_id': 'batch', 'client_id': 'c', 'batch_targets': [3],
                   'model_processed': True, 'intentional_silence': True}
        self.a._current['room'] = current
        from hermes_botsesh_test.reliability import receipt
        receipt(self.client.store, 'room', 3, state='started', receipt_id='batch', frozen=self.frozen)
        with patch('hermes_botsesh_test.room_pass.pass_frozen', side_effect=BotSeshError('mcp_operation_denied', rejected=True)), \
                patch('hermes_botsesh_test.task_failure.settle_takeover', return_value=None):
            await self.a._finish_current('room', current, 'empty_response')  # No exception.
        self.assertNotIn('room', self.a._current)
        self.a._delivery.assert_any_await('room', 3, 'failed', failure_code='runtime_error')
        # An uncertain transport failure still re-raises (same saved intent retried later).
        self.a._current['room'] = current
        with patch('hermes_botsesh_test.room_pass.pass_frozen', side_effect=BotSeshError('connection_failed_retry_same_intent')):
            with self.assertRaises(BotSeshError):
                await self.a._finish_current('room', current, 'empty_response')


if __name__ == '__main__':
    unittest.main()
