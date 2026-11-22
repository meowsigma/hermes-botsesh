"""B13 (LAUNCH FIX): one Hermes pass that covers several pending inputs threads its reply to the input that addressed it.

Only the MCP wire is doubled (same composed adapter/tools/Client/OAuth fixture as ``test_final_after_post``). The real
dual-seat proof (stock Hermes images, native gateway, scripted model, two seats in one room) is the harness in the B13 report.

* N1: the frozen read of a turn is keyed by its lowest pending input, so a room message that was not meant for this agent
  (for example the other seat's own reply, delivered to every connected agent) used to become the thread of the answer to
  the later message that did address it. The question then closed as ``completed`` (shown as "Processed") with no
  answer attributed, and the reply hung under somebody else's message.
* N3: a room that waits for Hermes to publish its platforms must not wait silently without bound.
"""
import asyncio
import logging
import time
import types
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

import test_final_after_post as fx
from test_plugin import BotSeshError, Client, Store, tools
from hermes_botsesh_test import adapter as mod, ops_status
from hermes_botsesh_test.oauth import OAuthClient
from gateway.platforms.event import ProcessingOutcome

import test_ongoing_turns


def message(number, *, me=False, reply_to_me=False, direct=False, sender='peer'):
    """A frozen-read message as the server projects it for this seat (``bot-a``)."""
    return {'number': number, 'text': 'Message %d' % number, 'senderParticipantId': sender,
            'authorParticipant': {'id': sender, 'verified': True},
            'mentions': [{'participant': {'id': 'bot-a'}}] if me or direct else [],
            'replyPreview': {'authorParticipant': {'id': 'bot-a'}} if reply_to_me else None}


class BatchAnswerTarget(unittest.IsolatedAsyncioTestCase):
    begin = fx.FinalAfterPostTests.begin
    model_ended = fx.FinalAfterPostTests.model_ended

    async def asyncSetUp(self):
        await fx.FinalAfterPostTests.asyncSetUp(self)
        self.batch = []
        self.targets['a'] = [4, 5]
        stock = OAuthClient.rpc

        def rpc(client, binding, name, args=None, **kwargs):
            if name == 'catchup' and binding['room_id'] == 'a' and self.batch:
                self.requests.append(('a', name, args))
                numbers = [m['number'] for m in self.batch]
                return {'batch_id': 'receipt-a-%d' % args['target_number'], 'ack_eligible': True, 'has_more': False,
                        'truncated': False, 'read_checkpoint': 0, 'next_checkpoint': max(numbers), 'messages': self.batch}
            value = stock(client, binding, name, args, **kwargs)
            if name == 'hermes_tool_policy' and binding['room_id'] == 'a' and self.batch:
                # The policy names the author of the exact target input, whoever that is.
                author = next(m for m in self.batch if m['number'] == args['number'])['senderParticipantId']
                value = {**value, 'requester': {'id': author, 'isOwner': author == 'owner'}}
            return value
        patcher = patch.object(OAuthClient, 'rpc', rpc)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sends(self):
        return [args for room, name, args in self.requests if room == 'a' and name == 'send_message']

    def completions(self):
        return [args for room, name, args in self.requests
                if room == 'a' and name == 'hermes_delivery' and args.get('state') == 'completed']

    async def answered(self, text='Answer for the batch.'):
        adapter, event = await self.begin()
        self.model_ended(adapter)
        result = await adapter.send('a', text, metadata={'notify': True})
        self.assertTrue(result.success, result.error)
        return adapter, event

    # ---- N1: the reply threads to the input that addressed this agent --------------------------------------------

    async def test_a_later_addressed_input_is_answered_not_the_other_seats_reply_before_it(self):
        self.batch = [message(4), message(5, me=True)]          # 4: the peer's own reply (ambient), 5: the question for us
        adapter, event = await self.answered()
        self.assertEqual(adapter._current['a']['target'], 4, 'the frozen read, admission and receipts stay keyed to the first input')
        self.assertEqual(adapter._current['a']['batch_targets'], [4, 5])
        self.assertEqual([(s['reply_to'], s['batch_id']) for s in self.sends()], [(5, 'receipt-a-4')])
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        # The whole frozen batch still settles through its own receipt: both inputs are covered, the reply is attributed to 5.
        self.assertEqual([(d['number'], d['batch_id']) for d in self.completions()], [(4, 'receipt-a-4')])
        row = self.client.store.room('a')['processing_receipts']['4']
        self.assertEqual((row['state'], row['answer_to']), ('replied', 5))
        self.assertTrue(row['read_attested'] and row['posted_reply'])
        self.assertNotIn('a', adapter._current)
        self.assertNotIn('pending_final', self.client.store.room('a'))

    async def test_the_newest_addressed_input_gets_the_single_reply(self):
        self.batch = [message(4, me=True), message(5, me=True)]
        await self.answered()
        self.assertEqual([s['reply_to'] for s in self.sends()], [5])

    async def test_a_reply_to_our_own_message_counts_as_addressed(self):
        self.batch = [message(4), message(5, reply_to_me=True)]
        await self.answered()
        self.assertEqual([s['reply_to'] for s in self.sends()], [5])

    async def test_a_direct_mention_flag_counts_as_addressed(self):
        self.batch = [message(4), message(5, direct=True)]
        await self.answered()
        self.assertEqual([s['reply_to'] for s in self.sends()], [5])

    async def test_the_first_input_stays_the_thread_when_it_is_the_one_addressed_to_us(self):
        self.batch = [message(4, me=True), message(5)]
        await self.answered()
        self.assertEqual([s['reply_to'] for s in self.sends()], [4])

    async def test_nothing_changes_when_no_input_addresses_us(self):
        self.batch = [message(4), message(5)]
        await self.answered()
        self.assertEqual([s['reply_to'] for s in self.sends()], [4])

    async def test_a_single_input_is_unchanged(self):
        self.targets['a'] = [4]
        self.batch = [message(4, me=True)]
        adapter, event = await self.answered()
        self.assertEqual([s['reply_to'] for s in self.sends()], [4])
        self.assertNotIn('answer_to', self.client.store.room('a')['processing_receipts']['4'])

    async def test_a_tool_post_without_reply_to_threads_to_the_addressed_input_too(self):
        self.batch = [message(4), message(5, me=True)]
        adapter, event = await self.begin()
        result = self.tool_post(adapter)
        self.assertNotIn('error', result)
        self.assertEqual([s['reply_to'] for s in self.sends()], [5])

    # ---- N1b: an input that arrives between the inbox check and the frozen read ---------------------------------------

    async def test_a_message_posted_between_the_inbox_check_and_the_read_is_an_input_of_the_turn(self):
        # The inbox listed only 4 (the other seat's reply that started this pass); our question, 5, landed right after it and
        # inside the read. The server settles the whole frozen batch with this turn, so 5 must be an input of it too.
        self.targets['a'] = [4]
        self.batch = [message(4), message(5, me=True)]
        adapter, event = await self.answered()
        current = adapter._current['a']
        self.assertEqual((current['target'], current['batch_targets'], current['answer_to']), (4, [4, 5], 5))
        self.assertEqual([a['number'] for r, n, a in self.requests if r == 'a' and n == 'hermes_delivery' and a['state'] == 'received'], [4, 5])
        inputs = current['text'].split('CURRENT BOTSESH INPUTS')[1]
        self.assertIn('Message 4', inputs)
        self.assertIn('Message 5', inputs, 'the late question is a current input, not only replayed history')
        self.assertEqual([s['reply_to'] for s in self.sends()], [5])
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['4']['batch_targets'], [4, 5])
        self.assertEqual(self.client.store.room('a')['handled'], [4, 5])

    async def test_a_late_message_the_server_never_targeted_at_this_seat_stays_history(self):
        self.targets['a'] = [4]
        self.batch = [message(4), message(5, me=True)]
        stock = OAuthClient.rpc

        def rpc(client, binding, name, args=None, **kwargs):
            if name == 'hermes_delivery' and args.get('number') == 5:
                raise BotSeshError('not_addressed', 404)
            return stock(client, binding, name, args, **kwargs)
        with patch.object(OAuthClient, 'rpc', rpc):
            adapter, event = await self.begin()
        current = adapter._current['a']
        self.assertEqual(current['batch_targets'], [4])
        self.assertNotIn('answer_to', current)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['4']['batch_targets'], [4])
        self.assertNotEqual(self.client.store.room('a')['processing_receipts']['4']['state'], 'failed')

    async def test_only_new_peer_messages_inside_the_read_join(self):
        self.targets['a'] = [4]
        own = message(5, sender='bot-a')                          # our own earlier reply: never an input
        self.batch = [message(3), message(4), own, message(6, me=True), message(7), message(8)]
        self.client.store.update(lambda d: d['rooms']['a'].update(handled=[7]))   # 7: already settled locally; 8: past the read
        stock = OAuthClient.rpc

        def rpc(client, binding, name, args=None, **kwargs):
            value = stock(client, binding, name, args, **kwargs)
            if name == 'catchup':
                value = {**value, 'next_checkpoint': 7}
            return value
        with patch.object(OAuthClient, 'rpc', rpc):
            adapter, event = await self.begin()
        self.assertEqual(adapter._current['a']['batch_targets'], [4, 6])
        self.assertEqual(adapter._current['a']['answer_to'], 6)

    async def test_a_late_message_that_is_already_skipped_or_settled_is_not_taken_again(self):
        self.targets['a'] = [4]
        self.batch = [message(4), message(5, me=True), message(6)]
        adapter = self.adapters['a']
        adapter._skipped_targets.add(('a', 5))
        self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={'6': {'target': 6, 'state': 'replied', 'received_at': 1}}))
        adapter, event = await self.begin()
        self.assertEqual(adapter._current['a']['batch_targets'], [4])

    def test_the_late_input_rule_only_reports_what_the_check_could_not_have_listed(self):
        adapter = self.adapters['a']
        batch = {'messages': [message(2), message(4), message(5), message(6, sender='bot-a'), message(7)], 'nextCheckpoint': 7}
        flagged = {**batch, 'messages': [{**m, 'isOwn': m['senderParticipantId'] == 'bot-a'} for m in batch['messages']]}
        self.assertEqual(adapter._late_inputs('a', flagged, [4], [4], []), [5, 7])
        self.assertEqual(adapter._late_inputs('a', flagged, [], [], []), [], 'nothing was checked, nothing is late')
        self.assertEqual(adapter._late_inputs('a', {**flagged, 'nextCheckpoint': 5}, [4], [4], []), [5])
        self.assertEqual(adapter._late_inputs('a', flagged, [4], [4, 5], []), [7], 'already inputs of this turn')

    def tool_post(self, adapter):
        import json
        current = adapter._current['a']
        args = {'room_id': 'a', 'text': 'Tool answer.', 'receipt_id': current['receipt_id'], 'processed': True,
                'client_id': str(uuid.uuid4())}
        with fx.native_input('a', current['target']), patch.object(tools, 'Client', return_value=self.client), \
                patch.object(tools, 'calling_scope', return_value=('botsesh', 'a')):
            return json.loads(tools.guarded(lambda a: tools.post(a, Mock()))(args))

    async def test_the_reply_is_still_recognised_after_a_restart(self):
        self.batch = [message(4), message(5, me=True)]
        adapter, event = await self.answered()
        receipt_id = adapter._current['a']['receipt_id']
        # Core replay / restart recovery re-resolves the confirmed reply from the transport journal, not from memory.
        self.client.store.update(lambda d: d['rooms']['a']['processing_receipts']['4'].pop('posted_reply', None))
        posted = await adapter._posted_reply('a', 4, receipt_id)
        self.assertTrue(posted and posted['read_effect'] == 'processed_frozen_receipt', posted)
        # ... without sending it again.
        self.assertEqual(len(self.sends()), 1)

    async def test_a_restored_turn_keeps_its_answer_target(self):
        self.batch = [message(4), message(5, me=True)]
        adapter, event = await self.begin()
        row = dict(self.client.store.room('a')['processing_receipts']['4'])
        row['native_admission'] = dict(admission_id='admission', ref=dict(profile_id='profile', session_id='s'))
        adapter._current.clear()
        adapter._restore_native('a', row)
        self.assertEqual(adapter._current['a']['answer_to'], 5)
        self.assertEqual(adapter._final_options('a', adapter._current['a'])['reply_to'], 5)

    # ---- who may move the thread -----------------------------------------------------------------------------

    def test_only_an_ordinary_group_turn_moves_the_thread(self):
        pick = mod.BotSeshAdapter._answer_target
        batch = [message(4), message(5, me=True)]
        flagged = [{**m, 'isMentionToMe': bool(m['mentions']), 'isReplyToMe': bool(m['replyPreview'])} for m in batch]
        self.assertEqual(pick(flagged, [4, 5], 4), 5)
        self.assertEqual(pick(flagged, [4, 5], 4, dm=True), 4, 'owner DM threading is unchanged')
        self.assertEqual(pick(flagged, [4, 5], 4, exact=True), 4, 'a takeover or check-in answers its exact source')
        self.assertEqual(pick(flagged, [4], 4), 4, 'only pending inputs of this turn are candidates')
        own = [flagged[0], {**flagged[1], 'isOwn': True}]
        self.assertEqual(pick(own, [4, 5], 4), 4, 'never our own message')
        self.assertEqual(pick(flagged, [4, 5], 9), 9, 'a target outside the batch is left alone')
        self.assertEqual(pick(batch, [4, 5], 4), 4, 'raw mentions without the projected flags prove nothing')


class RunnerWaitIsNotSilent(unittest.IsolatedAsyncioTestCase):
    """N3: Hermes publishes adapters on its runner after every platform connect returned; a platform that never finishes
    connecting must not leave a room waiting forever with one INFO line and a healthy-looking status."""

    class Passed(Exception):
        pass

    def setUp(self):
        test_ongoing_turns.OngoingTurns.setUp(self)
        self.registered = [False]
        runner = types.SimpleNamespace(_owning_profile=lambda adapter, platform: (self.registered[0] and adapter is self.a, None))
        self.a._message_handler = types.SimpleNamespace(__self__=runner)
        self.a._running = True
        self.a._refresh_dm_controls = AsyncMock(side_effect=self.Passed)
        self.a.RUNNER_WAIT_WARN_SECONDS = 0.4

    def room_health(self):
        return self.client.store.room('room').get('health', {})

    async def test_a_long_wait_warns_once_and_reports_room_health_without_changing_what_the_room_does(self):
        with self.assertLogs(mod.logger, logging.INFO) as logs:
            task = asyncio.create_task(self.a._check_room('room'))
            await asyncio.sleep(1.2)
            self.assertFalse(task.done(), 'still waiting: control flow is unchanged')
            self.a._refresh_dm_controls.assert_not_awaited()
            warnings = [r for r in logs.records if r.levelno == logging.WARNING]
            self.assertEqual(len(warnings), 1, [r.getMessage() for r in logs.records])
            self.assertIn('room', warnings[0].getMessage())
            self.assertIn('Hermes', warnings[0].getMessage())
            self.assertEqual((self.room_health().get('state'), self.room_health().get('error')), ('waiting_for_runner', 'runner_not_ready'))
            self.registered[0] = True
            with self.assertRaises(self.Passed):
                await asyncio.wait_for(task, 3)
        self.assertNotEqual(self.room_health().get('state'), 'waiting_for_runner', 'the wait is over: the room is no longer reported waiting')
        self.assertEqual(len([r for r in logs.records if r.levelno == logging.WARNING]), 1)

    async def test_a_short_wait_is_quiet(self):
        self.a.RUNNER_WAIT_WARN_SECONDS = 5
        with self.assertLogs(mod.logger, logging.INFO) as logs:
            task = asyncio.create_task(self.a._check_room('room'))
            await asyncio.sleep(0.5)
            self.registered[0] = True
            with self.assertRaises(self.Passed):
                await asyncio.wait_for(task, 3)
        self.assertEqual([r for r in logs.records if r.levelno >= logging.WARNING], [])
        self.assertNotEqual(self.room_health().get('state'), 'waiting_for_runner')

    async def test_a_stopped_channel_still_ends_the_wait(self):
        task = asyncio.create_task(self.a._check_room('room'))
        await asyncio.sleep(0.8)
        self.a._running = False
        await asyncio.wait_for(task, 2)
        self.a._refresh_dm_controls.assert_not_awaited()


class WaitingRoomStatus(unittest.TestCase):
    def test_status_names_the_wait_instead_of_calling_the_room_stale(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Store(Path(tmp) / 'profile'))
            binding = {'transport': 'invitation', 'origin': 'https://botsesh.com', 'url': 'https://botsesh.com/b/room',
                       'room_id': 'room', 'participant_id': 'seat', 'membership_id': 'member', 'active': True, 'name': 'Hermes',
                       'health': {'state': 'waiting_for_runner', 'error': 'runner_not_ready', 'last_check': time.time() - 300}}
            client.store.update(lambda d: d['rooms'].update(room=binding))
            with patch.object(ops_status, 'access_state', return_value=('alive', True)), \
                 patch.object(Client, 'enrolled', return_value={'backlog': 0}):
                row = ops_status.status_report(client, {}, runtime={'running': True, 'channel': 'up', 'profile': 'fixture'})['rooms'][0]
                self.assertEqual(row['gateway'], 'waiting_for_runner')
                client.store.update(lambda d: d['rooms']['room']['health'].update(state='connected'))
                row = ops_status.status_report(client, {}, runtime={'running': True, 'channel': 'up', 'profile': 'fixture'})['rooms'][0]
                self.assertEqual(row['gateway'], 'stale_or_offline', 'an ordinary stale room keeps its label')


if __name__ == '__main__':
    unittest.main()
