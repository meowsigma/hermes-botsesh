"""0.10.4 r3: a failure label comes only from the bound turn's own record, never from a chat-only status hint."""
import unittest
from unittest.mock import AsyncMock

from test_solid import SolidTests as _SolidTests
from gateway.platforms.event import ProcessingOutcome


class FailureLabelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = _SolidTests.asyncSetUp
    make_adapter = _SolidTests.make_adapter

    def failures(self):
        return [a for _, op, a in self.calls if op == 'delivery' and a.get('state') == 'failed']

    async def admit(self, target):
        self.targets['a'] = [target]
        await self.adapter._check_room('a')
        return self.adapter._admit_event.call_args.args[0]

    async def test_a_status_diagnostic_is_room_local_and_the_later_failure_is_a_plain_runtime_error(self):
        await self.admit(3)
        current = self.adapter._current['a']
        await self.adapter.send_or_update_status('a', 'error', 'HTTP 402: budget exceeded')
        self.assertNotIn('provider_hint', current)
        self.assertFalse([k for k in current if 'hint' in k or k == 'final_failure_code'], 'nothing of the diagnostic is stored on the turn')
        await self.adapter.send('a', 'Model failed.', metadata={'notify': True})
        self.assertEqual(self.failures(), [{'target': 3, 'state': 'failed', 'failure_code': 'runtime_error'}])

    async def test_a_status_diagnostic_of_one_input_never_labels_the_failure_of_the_next(self):
        first = await self.admit(3)
        await self.adapter.send_or_update_status('a', 'error', 'HTTP 402: budget exceeded')
        self.adapter._current['a']['delivered'] = True
        await self.adapter.on_processing_complete(first, ProcessingOutcome.SUCCESS)
        await self.admit(5)
        self.assertEqual(self.adapter._current['a']['target'], 5)
        await self.adapter.send('a', 'Model failed.', metadata={'notify': True})
        self.assertEqual([f for f in self.failures() if f['target'] == 5], [{'target': 5, 'state': 'failed', 'failure_code': 'runtime_error'}])

    async def test_the_native_reconcile_label_ignores_a_status_diagnostic(self):
        await self.admit(3)
        current = self.adapter._current['a']
        current['native_admission'] = {'admission_id': 'x'}
        self.adapter._native_state = AsyncMock(return_value=({'status': 'terminal', 'outcome': 'failed'}, {'failed': True}))
        await self.adapter.send_or_update_status('a', 'error', 'HTTP 402: budget exceeded')
        await self.adapter._reconcile_native('a')
        self.assertEqual(self.failures(), [{'target': 3, 'state': 'failed', 'failure_code': 'runtime_error'}])

    async def test_only_the_event_bound_final_of_this_turn_may_classify_its_failure_text(self):
        event = await self.admit(3)
        current = self.adapter._current['a']
        # a final of ANOTHER event, or a chat-level send, is not this turn's own record
        other = AsyncMock()
        other.source.chat_id, other.message_id, other.metadata = 'a', '5', {}
        result, _ = await self.adapter.send_final_ledgered(other, 'key', 'HTTP 402: credits exhausted', {}, reply_to=None)
        self.assertFalse(result.success)
        self.assertNotIn('final_failure_code', current)
        # the exact event's own final is
        result, _ = await self.adapter.send_final_ledgered(event, 'key', 'HTTP 402: credits exhausted (private vendor detail)', {}, reply_to=None)
        self.assertTrue(result.success)
        self.assertEqual(current['final_failure_code'], 'provider_budget')
        self.assertEqual(self.failures(), [{'target': 3, 'state': 'failed', 'failure_code': 'provider_budget'}])
        self.assertNotIn('private vendor detail', self.client.store.path.read_text())

    async def test_a_chat_level_send_never_classifies_prose_or_reads_a_hint(self):
        await self.admit(3)
        await self.adapter.send('a', 'HTTP 402: credits exhausted (private vendor detail)', metadata={'notify': True})
        self.assertEqual(self.failures(), [{'target': 3, 'state': 'failed', 'failure_code': 'runtime_error'}])

    async def test_the_owners_stop_is_still_an_interruption(self):
        await self.admit(3)
        await self.adapter.send_or_update_status('a', 'error', 'HTTP 402: budget exceeded')
        from hermes_botsesh_test.adapter import bind_turn
        bind_turn(adapter=self.adapter, room_id='a', need='settle').current['interrupt_requested'] = True
        await self.adapter.send('a', 'Stopped.', metadata={'notify': True})
        self.assertEqual(self.failures(), [{'target': 3, 'state': 'failed', 'failure_code': 'interrupted'}])


del _SolidTests  # the alias only lends its fixture; discovery must not re-run every Solid test here


if __name__ == '__main__':
    unittest.main()
