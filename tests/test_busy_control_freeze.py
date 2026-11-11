"""Busy native commands inspect later input without mutating its frozen turn."""
from copy import deepcopy
import unittest

import test_commands as fixture


class BusyControlFreezeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.NativeCommandTests.asyncSetUp
    asyncTearDown = fixture.NativeCommandTests.asyncTearDown
    drain = fixture.NativeCommandTests.drain
    command = fixture.NativeCommandTests.command
    reply = fixture.NativeCommandTests.reply
    occupy_normal_turn = fixture.NativeCommandTests.occupy_normal_turn

    async def test_later_dm_control_preserves_frozen_receipt_policy_and_queued_input(self):
        active = dict(receipt_id='original-receipt', target=50, client_id='normal-turn',
                      delivered=False, model_processed=False, text='immutable normal prompt')
        self.occupy_normal_turn(active)
        frozen = dict(receipt_id='original-receipt', batch=dict(messages=[
            dict(number=50, text='Original exact input')]))
        row = dict(target=50, state='started', receipt_id='original-receipt',
                   frozen=frozen, tool_policy=dict(toolsAllowed=False), text=active['text'])
        self.client.store.update(lambda d: d['rooms']['native-dm'].update(processing_receipts={'50':deepcopy(row)}))
        await self.command(51, 'Later ordinary question, not part of the current turn')
        await self.command(52, '/status')
        self.assertIn('botsesh', self.reply(52))
        self.assertEqual(self.adapter._current['native-dm'], active)
        room = self.client.store.room('native-dm')
        self.assertEqual(room['processing_receipts']['50'], row)
        self.assertEqual(room['processing_receipts']['51']['state'], 'received')
        self.assertNotIn('frozen', room['processing_receipts']['51'])
        self.assertNotIn('52', room['processing_receipts'])
        self.assertNotIn('handled', room)
        self.assertNotIn('processed_cursor', room)
        controls = [c.args[0] for c in self.ingress.call_args_list if c.args[0].text == '/status']
        self.assertEqual(len(controls), 1)
        self.assertTrue(controls[0].metadata['botsesh_control'])
        self.assertFalse(any(d.get('state') in ('started', 'completed') for d in self.transport.deliveries))
