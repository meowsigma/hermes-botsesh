"""DM attention + wake bundle (t_40325be8). Only the MCP wire is doubled (the test_final_after_post fixture); no provider or model calls.

attention: an explicit post in the owner's DM is a quiet `progress` update unless the model sets another level; the native final
never carries a level (the server reads that as `done`); nothing is sent until the room's server advertised the capability.
bundle: the idle inbox call asks for the first pending input's target read and tool policy, so a normal DM turn starts with zero
lookup calls; anything unusual falls back to the plain path.
"""
import json
import unittest
import uuid
from unittest.mock import AsyncMock, Mock, patch

import test_final_after_post as fixture
from hermes_botsesh_test import tools
from hermes_botsesh_test.adapter import BotSeshAdapter
from hermes_botsesh_test.client import BotSeshError
from hermes_botsesh_test.oauth import OAuthClient
from gateway.platforms.event import ProcessingOutcome
from gateway.session_context import set_session_vars, clear_session_vars


class DmAttention(unittest.IsolatedAsyncioTestCase):
    begin = fixture.FinalAfterPostTests.begin
    model_ended = fixture.FinalAfterPostTests.model_ended

    async def asyncSetUp(self):
        await fixture.FinalAfterPostTests.asyncSetUp(self)
        self.caps = None  # what the double server advertises on every inbox call
        inner = OAuthClient.rpc

        def rpc(api, binding, name, args=None, **kwargs):
            result = inner(api, binding, name, args, **kwargs)
            if name == 'hermes_inbox' and self.caps is not None:
                result = {**result, 'capabilities': self.caps}
            return result
        patcher = patch.object(OAuthClient, 'rpc', rpc)
        patcher.start()
        self.addCleanup(patcher.stop)
        for adapter in self.adapters.values():
            # The DM-controls refresh is covered by its own suite; here it would need a full dm_context double.
            adapter._refresh_dm_controls = AsyncMock()

    def room(self, room='a', *, dm=True, caps=None):
        """Set the stored room kind and what this server advertises (the client remembers what it last saw)."""
        self.caps = caps

        def change(data):
            row = data['rooms'][room]
            row.pop('room_type', None)
            row.pop('server_caps', None)
            if dm:
                row['room_type'] = 'dm'
            if caps:
                row['server_caps'] = dict(caps)
        self.client.store.update(change)

    def post(self, payload, *, room='a', target='3'):
        tokens = set_session_vars(platform='botsesh', chat_id=room, message_id=target)
        try:
            with patch.object(tools, 'Client', return_value=self.client):
                ctx = Mock()
                ctx.session_surface.return_value = 'botsesh'
                return json.loads(tools.guarded(lambda args: tools.post(args, ctx))(payload))
        finally:
            clear_session_vars(tokens)

    def wire(self, name='hermes_authored'):
        return [args for _, n, args in self.requests if n == name]

    # --- attention ---------------------------------------------------------------------------------------------

    async def test_nothing_is_sent_until_the_server_advertises_attention(self):
        self.room(dm=True, caps=None)
        await self.begin()
        self.assertTrue(self.post({'text': 'Quiet or not?'})['ok'])
        self.assertTrue(self.post({'text': 'Model chose a level.', 'attention': 'done'})['ok'])
        self.assertEqual(len(self.wire()), 2)
        self.assertTrue(all('attention' not in args for args in self.wire()), 'an older server never sees the field')

    async def test_dm_explicit_post_is_progress_unless_the_model_says_otherwise(self):
        self.room(dm=True, caps={'attention': 1})
        await self.begin()
        self.assertTrue(self.post({'text': 'Reading your files.'})['ok'])
        self.assertTrue(self.post({'text': 'All done: here it is.', 'attention': 'done'})['ok'])
        self.assertTrue(self.post({'text': 'Which one?', 'attention': 'choice'})['ok'])
        self.assertEqual([args['attention'] for args in self.wire()], ['progress', 'done', 'choice'])

    async def test_only_the_four_levels_exist(self):
        self.room(dm=True, caps={'attention': 1})
        await self.begin()
        before = len(self.requests)
        bad = self.post({'text': 'Hmm', 'attention': 'urgent'})
        self.assertEqual((bad['ok'], bad['error']), (False, 'invalid_attention'))
        self.assertEqual(len(self.requests), before, 'refused locally, no wire call')
        self.assertEqual(sorted(tools.ATTENTION_LEVELS), ['choice', 'done', 'needs_you', 'progress'])

    async def test_group_rooms_keep_their_normal_behaviour(self):
        self.room(dm=False, caps={'attention': 1})
        await self.begin()
        self.assertTrue(self.post({'text': 'A group contribution.'})['ok'])
        self.assertTrue(self.post({'text': 'Heads up.', 'attention': 'needs_you'})['ok'])
        self.assertEqual([args.get('attention') for args in self.wire()], [None, 'needs_you'],
                         'no default outside an owner DM; an explicit level is honored')

    async def test_receipt_bound_post_carries_the_level_too(self):
        self.room(dm=True, caps={'attention': 1})
        await self.begin()
        receipt = self.client.store.room('a')['processing_receipts']['3']['receipt_id']
        result = self.post({'text': 'Threaded step.', 'receipt_id': receipt, 'processed': True, 'reply_to': 3})
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.wire('send_message')[0].get('attention'), 'progress')

    async def test_retry_replays_the_exact_level_and_refuses_a_changed_one(self):
        self.room(dm=True, caps={'attention': 1})
        await self.begin()
        payload = {'text': 'Same bytes.', 'client_id': str(uuid.uuid4())}
        first = self.post(payload)
        self.assertTrue(first['ok'], first)
        again = self.post(payload)
        self.assertTrue(again['ok'], again)
        self.assertEqual(len(self.messages), 1, 'one message')
        self.assertEqual({args['attention'] for args in self.wire()}, {'progress'})
        changed = self.post({**payload, 'attention': 'done'})
        self.assertEqual(changed['error'], 'pending_send_mismatch')

    async def test_native_final_is_never_labelled(self):
        self.room(dm=True, caps={'attention': 1})
        adapter, event = await self.begin()
        self.model_ended(adapter)
        result = await adapter.send('a', 'The finished answer.', metadata={'notify': True})
        self.assertTrue(result.success, result.error)
        await adapter.on_processing_complete(event, ProcessingOutcome.SUCCESS)
        finals = self.wire('send_message')
        self.assertEqual(len(finals), 1)
        self.assertNotIn('attention', finals[0], 'no level: the server reads the native final as done')

    async def test_dm_cards_carry_only_the_levels_that_need_the_person(self):
        adapter = self.adapters['a']
        sent = []

        async def deliver(self_, card):
            sent.append(dict(card.payload))
            return Mock(success=True)
        bound = lambda self_, chat_id, session_key=None: ('human', 'member', 'generation', None)  # noqa: E731
        self.room(dm=True, caps={'attention': 1})
        with patch.object(type(adapter), '_deliver_card', deliver), patch.object(type(adapter), '_binding', bound):
            for kind in ('approval', 'clarify', 'slash', 'model', 'choice'):
                await adapter._publish('a', 'session', kind, (kind,), 'Text', [('Yes', 'y', None)], lambda *_: None)
        self.assertEqual([c['attention'] for c in sent], ['needs_you', 'choice', 'progress', 'progress', 'progress'])
        sent.clear()
        self.room(dm=True, caps=None)
        with patch.object(type(adapter), '_deliver_card', deliver), patch.object(type(adapter), '_binding', bound):
            await adapter._publish('a', 'session', 'approval', ('x',), 'Text', [('Yes', 'y', None)], lambda *_: None)
        self.assertNotIn('attention', sent[0], 'older server: the card is unchanged')

    # --- wake bundle -------------------------------------------------------------------------------------------

    def bundle_server(self, *, bundle=True, caps=None, drop_policy=False, other_target=None):
        """Answer hermes_inbox like the new server: capabilities always, a bundle when one was asked for."""
        original = OAuthClient.rpc
        seen = self.inbox_args = []

        def rpc(api, binding, name, args=None, **kwargs):
            if name == 'hermes_inbox':
                seen.append(args)
                base = original(api, binding, name, args, **kwargs)
                base = {**base, 'capabilities': caps if caps is not None else {'attention': 1, 'bundle': 1}}
                if args.get('bundle') and bundle:
                    target = other_target if other_target is not None else base['targets'][0]['number']
                    batch = 'receipt-' + binding['room_id'] + '-' + str(target)
                    catchup = {'batch_id': batch, 'ack_eligible': True, 'has_more': False, 'truncated': False,
                               'read_checkpoint': 0, 'next_checkpoint': target,
                               'messages': [{'number': target, 'text': 'Bundled question ' + str(target), 'senderParticipantId': 'owner',
                                             'authorParticipant': {'id': 'owner', 'verified': True}}]}
                    policy = {'room_id': binding['room_id'], 'participant_id': binding['participant_id'], 'number': target,
                              'batch_id': batch, 'toolsAllowed': True, 'requester': {'id': 'owner', 'isOwner': True}}
                    base['bundle'] = {'target': target, 'launch_id': args['bundle']['launch_id'], 'catchup': catchup,
                                      **({} if drop_policy else {'tool_policy': policy})}
                return base
            return original(api, binding, name, args, **kwargs)
        return patch.object(OAuthClient, 'rpc', rpc)

    async def test_first_check_learns_the_capability_then_every_turn_starts_with_zero_lookups(self):
        self.room(dm=True, caps=None)
        self.caps = {'attention': 1, 'bundle': 1}  # the store knows nothing yet; the first inbox call teaches it
        adapter = self.adapters['a']
        with self.bundle_server():
            await adapter._check_room('a')  # capability not known yet: plain path
            self.assertNotIn('bundle', self.inbox_args[0])
            self.assertEqual(self.client.store.room('a')['server_caps'], {'attention': 1, 'bundle': 1})
            self.assertTrue(any(name == 'catchup' for _, name, _ in self.requests))
            self.requests.clear()
            self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={}))
            adapter._current.clear()
            adapter._admit_event.reset_mock()
            await adapter._check_room('a')
        asked = self.inbox_args[-1]['bundle']
        self.assertTrue(uuid.UUID(asked['launch_id']))
        names = [name for _, name, _ in self.requests]
        self.assertNotIn('catchup', names, 'the target read rode the inbox call')
        self.assertNotIn('hermes_tool_policy', names, 'so did the tool policy')
        event = adapter._admit_event.call_args.args[0]
        self.assertIn('Bundled question 3', event.text, 'the model gets exactly the bundled read')
        row = self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['client_id'], asked['launch_id'], 'the turn uses the launch id the policy was derived for')
        self.assertEqual(adapter._current['a']['tool_policy']['batch_id'], 'receipt-a-3')

    async def test_a_server_without_the_bundle_is_never_asked_for_it(self):
        self.room(dm=True, caps={'attention': 1})  # attention yes, bundle no
        adapter = self.adapters['a']
        with self.bundle_server(bundle=False):
            await adapter._check_room('a')
        self.assertNotIn('bundle', self.inbox_args[0])
        self.assertTrue(any(name == 'catchup' for _, name, _ in self.requests))

    async def test_a_server_rolled_back_past_the_bundle_recovers_without_a_stuck_loop(self):
        self.room(dm=True, caps={'attention': 1, 'bundle': 1})
        self.caps = None  # the server was rolled back: it advertises nothing and refuses the field
        adapter = self.adapters['a']
        original = OAuthClient.rpc
        asked = []

        def old_server(api, binding, name, args=None, **kwargs):
            if name == 'hermes_inbox':
                asked.append(args)
                if 'bundle' in args:
                    raise BotSeshError('mcp_operation_denied', 400, rejected=True)
            return original(api, binding, name, args, **kwargs)
        with patch.object(OAuthClient, 'rpc', old_server):
            await adapter._check_room('a')
        self.assertEqual(['bundle' in a for a in asked], [True, False])
        self.assertNotIn('server_caps', self.client.store.room('a'), 'the capability is forgotten')
        adapter._admit_event.assert_awaited_once()

    async def test_bundle_is_not_requested_while_local_work_could_be_resumed_first(self):
        self.room(dm=True, caps={'attention': 1, 'bundle': 1})
        adapter = self.adapters['a']
        self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={'3': {'target': 3, 'state': 'admitted'}}))
        self.assertIsNone(adapter._bundle_request('a'), 'a live local receipt keeps the plain path')
        self.client.store.update(lambda d: d['rooms']['a'].update(processing_receipts={'3': {'target': 3, 'state': 'replied'}, '4': {'target': 4, 'state': 'withdrawn'}}, handled=[1, 3]))
        adapter._skipped_targets.add(('a', 9))
        request = adapter._bundle_request('a')
        self.assertEqual(request['skip'], [1, 3, 4, 9], 'handled, skipped and settled numbers are never chosen by the server')
        self.client.store.update(lambda d: d['rooms']['a'].update(pending_final={'target': 5}))
        self.assertIsNone(adapter._bundle_request('a'), 'an unsent final keeps the plain path')
        self.client.store.update(lambda d: d['rooms']['a'].pop('pending_final'))
        self.client.store.update(lambda d: d['rooms']['a'].update(room_type='group'))
        self.assertIsNone(adapter._bundle_request('a'), 'bundles are for the owner DM only')

    async def test_a_bundle_for_another_target_or_launch_is_ignored_not_trusted(self):
        self.room(dm=True, caps={'attention': 1, 'bundle': 1})
        adapter = self.adapters['a']
        with self.bundle_server(other_target=5):
            await adapter._check_room('a')
        names = [name for _, name, _ in self.requests]
        self.assertIn('catchup', names, 'target 3 is read the plain way when the bundle is for 5')
        event = adapter._admit_event.call_args.args[0]
        self.assertNotIn('Bundled question', event.text)

    async def test_a_missing_bundled_policy_is_fetched_the_plain_way(self):
        self.room(dm=True, caps={'attention': 1, 'bundle': 1})
        adapter = self.adapters['a']
        with self.bundle_server(drop_policy=True):
            await adapter._check_room('a')
        names = [name for _, name, _ in self.requests]
        self.assertNotIn('catchup', names)
        self.assertEqual(names.count('hermes_tool_policy'), 1)


if __name__ == '__main__':
    unittest.main()
