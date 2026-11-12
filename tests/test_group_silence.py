"""Ordinary group chat may deliberately stay quiet through stock core."""
import unittest
import test_living_chat as fixture
from gateway.response_filters import silence_allowed


class NativeGroupSilenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LivingChatTests.asyncSetUp
    begin = fixture.LivingChatTests.begin

    async def test_fresh_group_event_allows_stock_intentional_silence(self):
        _, event = await self.begin()
        self.assertTrue(silence_allowed('user', event.reply_expected),
                        'Stock core must not replace group NO_REPLY with a room notice')
        self.assertIs(event.reply_expected, False)

    async def test_restored_group_silence_preserves_dm_and_takeover_reply_requirement(self):
        adapter, _ = await self.begin()
        row = dict(self.client.store.room('a')['processing_receipts']['3'])
        # This is the existing offline fixture's native-ref placeholder, not a
        # claimed live admission. Restore only reconstructs event metadata here.
        row['native_admission'] = {'offline_fixture': True}
        for dm, takeover in ((False, False), (True, False), (False, True)):
            with self.subTest(dm=dm, takeover=takeover):
                self.client.store.update(lambda d: d['rooms']['a'].update(room_type='dm' if dm else 'group'))
                saved = {**row, **({'takeover_offer': {'event_id': 'offline-offer'}} if takeover else {})}
                adapter._restore_native('a', saved)
                event = adapter._current['a']['event']
                self.assertEqual(event.reply_expected, dm or takeover)
                self.assertEqual(silence_allowed('user', event.reply_expected), not (dm or takeover))


if __name__ == '__main__':
    unittest.main()
