"""Literal Bo 11g requests through registered Hermes dispatch, zero inference.

Only credentials/verification and the MCP wire are doubled. The corrected call
is extracted from the actual error, not silently manufactured by the tool.
"""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from test_plugin import Client, Store, tools
from hermes_botsesh_test.invitation import InvitationClient
from hermes_botsesh_test.oauth import OAuthClient
from gateway.session_context import set_session_vars, clear_session_vars

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/agents11g/bo-posts.json').read_text())
ROOM = FIXTURE['calls'][0]['room_id']
RECEIPT = FIXTURE['receipt_id']


def correction(result):
    message = result['message']
    return json.JSONDecoder().raw_decode(message.split('botsesh_post(', 1)[1])[0]


class ThreadedPostGuidance(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.client = Client(Store(Path(temp.name) / 'private'))
        self.client.store.update(lambda d: d['rooms'].update({ROOM: {
            'room_id': ROOM, 'active': True, 'transport': 'invitation',
            'participant_id': 'offline-Bo', 'origin': 'https://botsesh.com'}}))
        self.add_receipt(RECEIPT, 0)
        self.wire = []
        self.credential = patch.object(InvitationClient, '_access', return_value='fixture-not-a-credential')
        self.credential.start(); self.addCleanup(self.credential.stop)
        self.verify = patch.object(InvitationClient, 'verify', return_value={'readCheckpoint': 0})
        self.verify.start(); self.addCleanup(self.verify.stop)
        def rpc(api, binding, name, args=None, **kwargs):
            self.wire.append((name, copy.deepcopy(args)))
            self.assertEqual(name, 'reply')
            return {'message': {'number': 1, 'text': args['text'],
                'text_sha256': hashlib.sha256(args['text'].encode()).hexdigest(),
                'authorParticipant': {'id': 'offline-Bo'}}}
        self.rpc = patch.object(OAuthClient, 'rpc', rpc)
        self.rpc.start(); self.addCleanup(self.rpc.stop)
        self.rest = patch.object(InvitationClient, 'rest', side_effect=AssertionError('No REST mutation'))
        self.rest.start(); self.addCleanup(self.rest.stop)

    def add_receipt(self, receipt, target, *, number=0):
        def add(data):
            room = data['rooms'][ROOM]
            room.setdefault('oauth_receipts', {})[receipt] = {
                'numbers': [number], 'participant_id': 'offline-Bo', 'expires': time.time() + 3600}
            room.setdefault('processing_receipts', {})[str(target)] = {
                'target': target, 'state': 'started', 'receipt_id': receipt,
                'frozen': {'receipt_id': receipt, 'read_effect': 'none',
                           'batch': {'messages': [{'number': number, 'isOwn': False}], 'hasMore': False}}}
        self.client.store.update(add)

    def call(self, args, *, target='0'):
        import hermes_cli.plugins as plugins
        from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
        from agent.tool_executor import _ToolCallRef, _resolve_sequential_dispatch
        from tools.tool_search_validation import validate_deferred_call_args
        original = copy.deepcopy(args)
        agent = SimpleNamespace(platform='botsesh', quiet_mode=False,
            _context_engine_tool_names=set(), _memory_manager=None,
            session_id='threaded-replay', valid_tool_names={'botsesh_post'},
            enabled_toolsets=['botsesh'], disabled_toolsets=None, interrupt=Mock())
        with contextlib.ExitStack() as stack:
            manager = PluginManager(); manager._discovered = True
            ctx = PluginContext(PluginManifest(name='botsesh', source='user'), manager)
            stack.enter_context(patch.object(plugins, 'get_plugin_manager', return_value=manager))
            stack.enter_context(patch.object(plugins, 'discover_plugins'))
            stack.enter_context(patch.object(tools, 'Client', return_value=self.client))
            stack.enter_context(patch.object(tools, 'calling_scope', return_value=('botsesh', ROOM)))
            stack.enter_context(patch.object(ctx, 'register_command'))
            stack.enter_context(patch.object(ctx, 'register_skill'))
            tools.register_tools(ctx)
            self.assertIsNone(validate_deferred_call_args('botsesh_post', args))
            stack.enter_context(patch('agent.subagent_lifecycle.get_active_subagent_parent', return_value=agent))
            tokens = set_session_vars(platform='botsesh', chat_id=ROOM, message_id=target)
            try:
                ref = _ToolCallRef('botsesh_post', dict(args), 'replay-task', 'replay-call', [])
                result = json.loads(_resolve_sequential_dispatch(agent, ref, []).execute(ref.args))
            finally:
                clear_session_vars(tokens)
        self.assertEqual(args, original, 'Authored request bytes/options stay unchanged')
        agent.interrupt.assert_not_called()
        return result

    def assert_unread(self):
        room = self.client.store.room(ROOM)
        for row in room['processing_receipts'].values():
            self.assertNotIn('read_attested', row)
        self.assertNotIn('processed_cursor', room)
        self.assertFalse(self.wire)

    def test_both_literal_bo_calls_teach_exact_receipt_and_corrected_followup_threads(self):
        original_room = copy.deepcopy(self.client.store.room(ROOM))
        for request in FIXTURE['calls']:
            with self.subTest(request=request):
                self.client.store.update(lambda d: d['rooms'].update({ROOM: copy.deepcopy(original_room)}))
                result = self.call(request)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['error'], 'unattested_reply_target_not_supported')
                self.assertIn('r1', result['message'])
                self.assertIn('only if you actually read', result['message'])
                self.assertNotIn('pending', result)
                self.assert_unread()
                fixed = correction(result)
                self.assertEqual(fixed, {**request, 'receipt_id': 'r1', 'processed': True})
                landed = self.call(fixed)
                self.assertTrue(landed['ok'], landed)
                self.assertEqual(landed['result']['read_effect'], 'processed_frozen_receipt')
                self.assertEqual(self.wire[-1][1]['reply_to'], 0)
                self.assertEqual(self.wire[-1][1]['batch_id'], RECEIPT)
                self.assertEqual(self.wire[-1][1]['text'], request['text'])
                self.wire.clear()

    def test_explicit_processed_binds_omitted_receipt_but_other_incomplete_shapes_teach(self):
        request = FIXTURE['calls'][0]
        original_room = copy.deepcopy(self.client.store.room(ROOM))
        for additions in ({'processed': True}, {'receipt_id': RECEIPT},
                          {'receipt_id': RECEIPT, 'processed': False}):
            with self.subTest(additions=additions):
                self.client.store.update(lambda d: d['rooms'].update({ROOM: copy.deepcopy(original_room)}))
                self.wire.clear()
                result = self.call({**request, **additions})
                if additions == {'processed': True}:
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['result']['read_effect'], 'processed_frozen_receipt')
                    self.assertEqual(self.wire[-1][1]['reply_to'], request['reply_to'])
                    self.assertEqual(self.wire[-1][1]['batch_id'], RECEIPT)
                    self.assertEqual(self.wire[-1][1]['text'], request['text'])
                    room = self.client.store.room(ROOM)
                    call, = room['processing_receipts']['0']['explicit_post_calls'].values()
                    self.assertEqual(call['payload']['receipt_id'], RECEIPT)
                    self.assertEqual(call['authored_options'], {'reply_to': 0, 'processed': True})
                else:
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(correction(result)['receipt_id'], RECEIPT)
                    self.assert_unread()

    def test_supplied_stale_receipt_is_preserved_in_missing_processed_guidance(self):
        supplied = 'supplied-stale-receipt'
        result = self.call({**FIXTURE['calls'][0], 'receipt_id': supplied})
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'], 'unattested_reply_target_not_supported')
        self.assertEqual(correction(result), {
            **FIXTURE['calls'][0], 'receipt_id': supplied, 'processed': True})
        self.assertIn('Keep the supplied receipt_id', result['message'])
        self.assertNotIn(RECEIPT, result['message'])
        self.assert_unread()
        refused = self.call(correction(result))
        self.assertEqual(refused['error'], 'receipt_expired')
        self.assert_unread()

    def test_ambiguous_receipts_list_at_most_three_message_candidates_without_binding(self):
        for i in range(1, 5):
            self.add_receipt('candidate-' + str(i), i)
            def cover_input(data):
                room = data['rooms'][ROOM]
                room['processing_receipts'][str(i)]['frozen']['batch']['messages'].append({'number': i, 'isOwn': False})
                room['oauth_receipts']['candidate-' + str(i)]['numbers'] = [0, i]
            self.client.store.update(cover_input)
        self.client.store.update(lambda d: d['rooms'][ROOM]['processing_receipts'].update(
            duplicate=copy.deepcopy(d['rooms'][ROOM]['processing_receipts']['0'])))
        original_room = copy.deepcopy(self.client.store.room(ROOM))
        for additions in ({}, {'processed': True}):
            with self.subTest(additions=additions):
                self.client.store.update(lambda d: d['rooms'].update({ROOM: copy.deepcopy(original_room)}))
                result = self.call({**FIXTURE['calls'][0], **additions})
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['error'], 'unattested_reply_target_not_supported')
                listing = result['message'].split('briefing you read: ', 1)[1]
                candidates = json.JSONDecoder().raw_decode(listing)[0]
                self.assertEqual(len(candidates), 3)
                self.assertTrue(all(c['message'] == 0 and 'input_message' in c for c in candidates))
                self.assertIn('<choose a listed receipt_id>', correction(result)['receipt_id'])
                self.assert_unread()

    def test_candidate_filter_excludes_foreign_expired_incomplete_terminal_and_other_message(self):
        for i in range(1, 6):
            self.add_receipt('invalid-' + str(i), i)
        def mutate(data):
            room = data['rooms'][ROOM]
            room['oauth_receipts']['invalid-1']['participant_id'] = 'foreign'
            room['oauth_receipts']['invalid-2']['expires'] = 0
            room['processing_receipts']['3']['frozen']['batch']['hasMore'] = True
            room['processing_receipts']['4']['state'] = 'completed'
            room['processing_receipts']['5']['frozen']['batch']['messages'][0]['number'] = 99
            room['oauth_receipts']['invalid-5']['numbers'] = [99]
        self.client.store.update(mutate)
        result = self.call(FIXTURE['calls'][0])
        self.assertEqual(correction(result)['receipt_id'], 'r1')
        for i in range(1, 6):
            self.assertNotIn('invalid-' + str(i), result['message'])
        self.assert_unread()

    def test_no_candidate_does_not_borrow_receipt_or_silently_unthread(self):
        self.client.store.update(lambda d: d['rooms'][ROOM]['oauth_receipts'].clear())
        result = self.call(FIXTURE['calls'][0])
        self.assertFalse(result['ok'])
        self.assertIn('botsesh_read', result['message'])
        self.assertEqual(correction(result)['reply_to'], 0)
        self.assertNotIn(RECEIPT, result['message'])
        self.assert_unread()

    def test_corrected_post_retry_is_idempotent_and_changed_retry_is_refused(self):
        result = self.call(FIXTURE['calls'][0])
        fixed = {**correction(result), 'client_id': str(uuid.uuid4())}
        first = self.call(fixed)
        self.assertTrue(first['ok'], first)
        before = copy.deepcopy(self.client.store.room(ROOM)['oauth_posts'])
        again = self.call(fixed, target='1')
        self.assertTrue(again['ok'], again)
        self.assertEqual(first['result']['number'], again['result']['number'])
        self.assertEqual(len(self.wire), 1)
        self.assertEqual(self.client.store.room(ROOM)['oauth_posts'], before)
        changed = self.call({k: v for k, v in fixed.items() if k != 'processed'})
        self.assertEqual(changed['error'], 'pending_send_mismatch')
        self.assertEqual(self.client.store.room(ROOM)['oauth_posts'], before)

    def test_rejected_uuid_is_not_reused_for_corrected_intent(self):
        request = {**FIXTURE['calls'][0], 'client_id': str(uuid.uuid4())}
        first = self.call(request)
        again = self.call(request)
        self.assertFalse(again['ok'], again)
        self.assertIn('r1', again['message'])
        changed = self.call({**correction(first), 'client_id': request['client_id']})
        self.assertEqual(changed['error'], 'pending_send_mismatch')
        self.assert_unread()

    def test_explicit_unknown_receipt_is_never_replaced(self):
        result = self.call({**FIXTURE['calls'][0], 'receipt_id': 'unknown', 'processed': True})
        self.assertEqual(result['error'], 'receipt_expired')
        self.assert_unread()

    def test_schema_preteaches_thread_fields_without_adding_recovery_tools(self):
        post = next(s for s in tools.SCHEMAS if s['name'] == 'botsesh_post')
        self.assertIn('reply_to + receipt_id + processed:true', post['description'])
        self.assertIn('processed:true', post['parameters']['properties']['reply_to']['description'])
        self.assertEqual(len(tools.SCHEMAS), 12)


if __name__ == '__main__':
    unittest.main()
