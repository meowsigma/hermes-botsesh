"""0.10.4 r3: gateway callbacks that name an event (start, complete, final) act only on the row of THAT event."""
import unittest
from unittest.mock import Mock

from test_solid import SolidTests as _SolidTests
from gateway.platforms.event import ProcessingOutcome


class CallbackEventTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = _SolidTests.asyncSetUp
    make_adapter = _SolidTests.make_adapter

    def deliveries(self, state):
        return [a for _, op, a in self.calls if op == 'delivery' and a.get('state') == state]

    @staticmethod
    def event(message_id):
        return Mock(source=Mock(chat_id='a'), message_id=str(message_id), metadata={})

    async def admit(self, target):
        self.targets['a'] = [target]
        await self.adapter._check_room('a')
        return self.adapter._admit_event.call_args.args[0]

    async def test_a_start_callback_of_another_event_never_starts_the_open_row(self):
        await self.admit(3)
        await self.adapter.on_processing_start(self.event(5))
        self.assertEqual(self.deliveries('started'), [], 'event 5 started nothing: the open row is event 3')
        await self.adapter.on_processing_start(self.event(3))
        self.assertEqual(self.deliveries('started'), [{'target': 3, 'state': 'started'}])

    async def test_a_completion_callback_of_another_event_never_settles_the_open_row(self):
        await self.admit(3)
        await self.adapter.on_processing_complete(self.event(5), ProcessingOutcome.FAILURE)
        self.assertEqual(self.deliveries('failed'), [], 'a failure of event 5 is not a failure of event 3')
        self.assertIn('a', self.adapter._current, 'the open row is untouched')
        await self.adapter.on_processing_complete(self.event(3), ProcessingOutcome.FAILURE)
        self.assertEqual([f['target'] for f in self.deliveries('failed')], [3])

    async def test_a_callback_whose_event_has_no_id_never_starts_or_settles_the_open_row(self):
        # r4: Hermes' `MessageEvent.message_id` is optional.  A missing or empty id is not "the room's current event": it names none.
        await self.admit(3)
        for label, missing in (('missing', None), ('empty', '')):
            with self.subTest(label):
                event = Mock(source=Mock(chat_id='a'), message_id=missing, metadata={})
                await self.adapter.on_processing_start(event)
                await self.adapter.on_processing_complete(event, ProcessingOutcome.FAILURE)
                self.assertEqual(self.deliveries('started'), [])
                self.assertEqual(self.deliveries('failed'), [])
                self.assertIn('a', self.adapter._current, 'the open row is untouched')
        await self.adapter.on_processing_start(self.event(3))
        self.assertEqual(self.deliveries('started'), [{'target': 3, 'state': 'started'}], 'its own id still starts it')
        await self.adapter.on_processing_complete(self.event(3), ProcessingOutcome.FAILURE)
        self.assertEqual([f['target'] for f in self.deliveries('failed')], [3], 'and settles it')

    async def test_the_late_error_notice_fails_only_a_receipt_that_exists_for_that_exact_event(self):
        # a durable-receipt path (documented as an exception of bind_turn): it names no row, so it needs an enrolled room, a nonempty
        # event id and the persisted receipt of that very event
        await self.admit(3)
        error = RuntimeError('escaped the turn')
        for label, chat, message in (('a room that is not enrolled', 'nowhere', '3'), ('an enrolled room without that input', 'b', '3'),
                                     ('another event of the same room', 'a', '5'), ('no event id', 'a', None), ('an empty event id', 'a', ''),
                                     ('no room', None, '3'), ('an empty room', '', '3')):
            with self.subTest(label):
                await self.adapter._notify_turn_error(Mock(source=Mock(chat_id=chat, message_id='3'), message_id=message, metadata={}), error)
                self.assertEqual(self.deliveries('failed'), [])
        await self.adapter._notify_turn_error(Mock(source=Mock(chat_id='a', message_id='3'), message_id='3', metadata={}), error)
        self.assertEqual([f['target'] for f in self.deliveries('failed')], [3], 'the receipt of its own event is failed')


del _SolidTests  # the alias only lends its fixture; discovery must not re-run every Solid test here


if __name__ == '__main__':
    unittest.main()
