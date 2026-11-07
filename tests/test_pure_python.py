"""Pure Python parity; only offline wire/server doubles, never native proof."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch, Mock

from test_plugin import Client, Store, BotSeshError
from hermes_botsesh_test import oauth, room_turn, http_transport
from hermes_botsesh_test.adapter import BotSeshAdapter
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry, platform_registry


class ParserParity(unittest.TestCase):
    def test_every_captured_sdk_golden(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/room-turn-sdk.json').read_text())
        self.assertEqual(len(fixture['cases']), 109)
        for row in fixture['cases']:
            with self.subTest(case=row['name']):
                if row['ok']:
                    self.assertEqual(room_turn.parse_room_turn_output(row['input']), row['result'])
                else:
                    with self.assertRaises(BotSeshError) as error:
                        room_turn.parse_room_turn_output(row['input'])
                    self.assertEqual(error.exception.code, row['code'])

    def test_b66_provider_schema_plain_root_failure_and_parser_exclusivity(self):
        schema = room_turn.runtime_output_schema()
        self.assertEqual(schema['type'], 'object')
        self.assertFalse(set(schema) & {'allOf', 'anyOf', 'oneOf', 'not'})
        self.assertEqual(schema['properties']['failure']['required'], ['task_id', 'status', 'reason'])
        self.assertIn('cant_do', schema['properties']['failure']['properties']['status']['enum'])
        failure = {'task_id': str(uuid.uuid1()), 'status': 'cant_do', 'reason': 'No tool.'}
        self.assertEqual(room_turn.parse_room_turn_output({'failure': failure}), {'failure': failure})
        with self.assertRaises(BotSeshError):
            room_turn.parse_room_turn_output({'failure': failure, 'nothing_to_add': True})

    def test_sse_multiline_crlf_comments_notifications_and_exact_id(self):
        raw = ('\ufeff: keepalive\r\nevent: message\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\n'
               'id: transport-cursor\r\ndata: {"jsonrpc":"2.0",\r\ndata: "id":"request", "result":{"structuredContent":{"exact":"café 🐚"}}}\r\n\r\n').encode()
        value = http_transport.decode_response(raw, 'text/event-stream', request_id='request')
        self.assertEqual(value['result']['structuredContent']['exact'], 'café 🐚')
        for bad in (raw + raw, raw.rstrip(b'\r\n'), raw.rstrip(b'\r\n') + b'\n', b'data: {"id":"foreign","result":{}}\n\n'):
            with self.subTest(bad=bad[:30]), self.assertRaises(ValueError):
                http_transport.decode_response(bad, 'text/event-stream', request_id='request')

    def test_http_bounded_stream_no_redirect_proxy_or_error_prose(self):
        import httpx
        original = httpx.Client
        requests = []
        status, body, headers = 200, b'{"hello":"world"}', {'content-type': 'application/json'}
        def handler(request):
            requests.append(request)
            return httpx.Response(status, content=body, headers=headers)
        def client(**kwargs):
            self.assertFalse(kwargs['follow_redirects'])
            self.assertFalse(kwargs['trust_env'])
            return original(**kwargs, transport=httpx.MockTransport(handler))
        with patch('httpx.Client', side_effect=client):
            self.assertEqual(oauth._http('https://botsesh.com', '/mcp', payload={'id': 'x'}, bearer='private'), {'hello': 'world'})
            self.assertEqual(requests[-1].headers['authorization'], 'Bearer private')
            status, body, headers = 429, b'{"error":"slow_down","private":"do not echo"}', {'retry-after': '2.5'}
            with self.assertRaises(BotSeshError) as error:
                oauth._http('https://botsesh.com', '/mcp')
            self.assertEqual((error.exception.code, error.exception.status, error.exception.retry_after_ms), ('slow_down', 429, 2500))
            self.assertNotIn('do not echo', str(error.exception))
            status, body = 401, b'<html>private</html>'
            with self.assertRaises(BotSeshError) as error:
                oauth._http('https://botsesh.com', '/mcp')
            self.assertTrue(error.exception.terminal)
            status, body = 200, b'x' * (http_transport.MAX_BYTES + 1)
            with self.assertRaises(BotSeshError) as error:
                oauth._http('https://botsesh.com', '/mcp')
            self.assertEqual(error.exception.code, 'response_limit')
            status, body, headers = 302, b'', {'location': 'https://foreign.invalid'}
            with self.assertRaises(BotSeshError):
                oauth._http('https://botsesh.com', '/mcp')
        for origin, path in [('https://foreign.invalid', '/mcp'), ('https://botsesh.com', '/arbitrary')]:
            with self.assertRaises(BotSeshError):
                oauth._http(origin, path)


class OAuthPython(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'private')
        self.client = Client(self.store)
        self.api = oauth.OAuthClient(self.store)
        self.binding = {'room_id': 'one', 'participant_id': 'bot', 'transport': 'oauth', 'active': True,
                        'url': 'https://botsesh.com/b/one', 'origin': 'https://botsesh.com'}
        self.store.update(lambda d: d['rooms'].update(one=self.binding))
        self.batch = str(uuid.uuid4())
        self.store.update(lambda d: d['rooms']['one'].update(oauth_receipts={self.batch: {'numbers': [0, 3], 'expires': 0}}))
        self.exact = {'text': 'Exact café 🐚\n', 'client_id': str(uuid.uuid4()), 'receipt_id': self.batch, 'processed': True, 'reply_to': 0}
        self.posts = {}
        self.calls = []
        self.lose = False
        def rpc(binding, name, args=None, **kwargs):
            self.calls.append((name, args))
            if name == 'list_rooms':
                return {'rooms': [{'room_id': 'one', 'participant_id': 'bot'}]}
            if name in ('send_message', 'hermes_notice'):
                message = self.posts.setdefault(args['client_id'], {'number': 0, 'authorParticipant': {'id': 'bot'},
                    'text_sha256': hashlib.sha256(args['text'].encode()).hexdigest()})
                if self.lose:
                    self.lose = False
                    raise BotSeshError('connection_failed_retry_same_intent')
                return {'message': message}
            if name in ('hermes_listener', 'hermes_takeover', 'hermes_delivery'):
                return {'read_effect': 'none'}
            self.fail('unexpected ' + name)
        self.rpc = patch.object(oauth.OAuthClient, 'rpc', side_effect=rpc)
        self.rpc.start()
        self.addCleanup(self.rpc.stop)
        self.no_node = patch.object(Client, 'command', side_effect=AssertionError('OAuth tried the JS bridge'))
        self.no_node.start()
        self.addCleanup(self.no_node.stop)

    def test_duplicate_delivery_expired_complete_receipt_and_live_cached_replay(self):
        # Server allows an expired COMPLETE batch for sending. Local timestamp
        # is pruning guidance, not authority to invent or renew an attestation.
        first = self.client.enrolled('one', 'post', **self.exact)
        self.assertEqual(first['number'], 0)
        self.assertEqual(first['read_effect'], 'processed_frozen_receipt')
        self.assertEqual(self.client.enrolled('one', 'retry', client_id=self.exact['client_id']), first)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual([n for n, _ in self.calls], ['send_message', 'list_rooms'])
        self.assertNotIn(self.batch, self.store.room('one')['oauth_receipts'])
        with self.assertRaises(BotSeshError):
            self.client.enrolled('one', 'post', **{**self.exact, 'text': 'changed'})

    def test_uncertain_send_keeps_uuid_and_blocks_later_inference_until_exact_retry(self):
        self.lose = True
        with self.assertRaises(BotSeshError):
            self.client.enrolled('one', 'post', **self.exact)
        check = self.client.enrolled('one', 'check')
        self.assertEqual(check['pending']['client_id'], self.exact['client_id'])
        self.assertEqual(check['mentions'], [])
        result = self.client.enrolled('one', 'retry', client_id=self.exact['client_id'])
        self.assertEqual(result['number'], 0)
        self.assertEqual(len(self.posts), 1)
        self.assertIsNone(self.api.pending('one'))
        self.assertEqual([a['client_id'] for n, a in self.calls if n == 'send_message'], [self.exact['client_id']] * 2)

    def test_missing_foreign_receipt_and_malformed_identity_fail_without_effect(self):
        for changed in ({'receipt_id': 'foreign'}, {'client_id': str(uuid.uuid1())}, {'client_id': ' ' + self.exact['client_id']}, {'reply_to': 5}):
            with self.subTest(changed=changed), self.assertRaises(BotSeshError):
                self.client.enrolled('one', 'post', **{**self.exact, **changed})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.store.room('one').get('oauth_posts'))
        # UUID case is exact authored input, and the JS SDK accepts upper case.
        self.client.enrolled('one', 'post', **{**self.exact, 'client_id': self.exact['client_id'].upper()})
        self.assertIn(self.exact['client_id'].upper(), self.posts)

    def test_atomic_journal_refuses_concurrent_changed_or_cross_operation_identity(self):
        cid = self.exact['client_id']
        self.api._journal('one', 'oauth_posts', cid, self.exact)
        with self.assertRaises(BotSeshError):
            self.api._journal('one', 'oauth_posts', cid, {**self.exact, 'text': 'changed'})
        with self.assertRaises(BotSeshError):
            self.api._journal('one', 'oauth_turns', cid, {'output': {'reply': 'changed'}})
        self.assertEqual(self.store.room('one')['oauth_posts'][cid]['exact'], self.exact)

    def test_no_read_notice_never_falls_back_to_latest_batch(self):
        result = self.client.enrolled('one', 'post', text='Notice', client_id=str(uuid.uuid4()))
        self.assertEqual(result['read_effect'], 'none')
        self.assertEqual(self.calls[0][0], 'hermes_notice')
        self.assertNotIn('batch_id', self.calls[0][1])
        self.assertIn(self.batch, self.store.room('one')['oauth_receipts'])

    def test_takeover_claim_needs_supplied_processed_receipt_and_heartbeat_is_separate(self):
        for extra in ({'processed': False, 'receipt_id': self.batch}, {'processed': True, 'receipt_id': 'foreign'}):
            with self.assertRaises(BotSeshError):
                self.client.enrolled('one', 'takeover_claim', event_id='offer', reason='I can help', **extra)
        self.assertEqual(self.calls, [])
        self.client.enrolled('one', 'takeover_claim', event_id='offer', reason='I can help', processed=True, receipt_id=self.batch)
        self.assertEqual(self.calls[-1], ('hermes_takeover', {'room_id': 'one', 'action': 'claim', 'event_id': 'offer', 'batch_id': self.batch, 'reason': 'I can help', 'processed': True}))
        self.client.enrolled('one', 'listener', state='listening')
        self.assertEqual(self.calls[-1], ('hermes_listener', {'room_id': 'one', 'state': 'listening'}))
        self.assertIn(self.batch, self.store.room('one')['oauth_receipts'])

    def test_structured_finish_retry_owned_by_server_and_message_zero_is_valid(self):
        cid = str(uuid.uuid4())
        output = {'reply': 'Draft', 'draft_ready': {'title': 'Deliverable', 'recipientId': str(uuid.uuid4())}}
        received = []
        def finish(binding, name, args=None, **kwargs):
            if name == 'list_rooms':
                return {'rooms': [{'room_id': 'one', 'participant_id': 'bot'}]}
            self.assertEqual(name, 'finish_room_turn')
            received.append(args)
            if len(received) == 1:
                raise BotSeshError('connection_failed_retry_same_intent')
            return {'reply_number': 0, 'read_checkpoint': 0, 'nothing_to_add': False}
        with patch.object(self.api, 'rpc', side_effect=finish):
            with self.assertRaises(BotSeshError):
                self.api.call(self.binding, 'finish', output=output, receipt_id=self.batch, client_id=cid, processed=True)
            with self.assertRaises(BotSeshError):
                self.api.call(self.binding, 'finish', output={'reply': 'Changed'}, receipt_id=self.batch, client_id=cid, processed=True)
            result = self.api.call(self.binding, 'retry', client_id=cid)
            self.assertEqual(result['number'], 0)
            self.assertEqual(result['read_effect'], 'processed_frozen_receipt')
            self.assertEqual(received[0], received[1])
            self.assertEqual(self.api.call(self.binding, 'retry', client_id=cid), result)
            self.assertEqual(len(received), 2)
        self.assertIsNone(self.api.pending('one'))

    def test_failure_finish_has_no_read_and_keeps_exact_failure_receipt(self):
        failure = {'task_id': str(uuid.uuid4()), 'status': 'cant_do', 'reason': 'I have no image tool.', 'partial_notes': ''}
        result = {'failure': {'taskId': failure['task_id'], 'status': 'cant_do', 'state': 'offered'}, 'read_effect': 'none'}
        with patch.object(self.api, 'rpc', return_value=result) as rpc:
            self.assertEqual(self.api.call(self.binding, 'fail_task', failure=failure, receipt_id=self.batch, client_id=str(uuid.uuid4())), result)
            self.assertEqual(rpc.call_args.args[1], 'finish_room_turn')
        self.assertIn(self.batch, self.store.room('one')['oauth_receipts'])
        self.assertNotIn('processed_cursor', self.store.room('one'))

    def test_supported_schema_does_not_imply_missing_mcp_execution_support(self):
        draft = {'title': 'Draft', 'recipientId': str(uuid.uuid4())}
        with self.assertRaises(BotSeshError) as error:
            self.client.enrolled('one', 'post', **self.exact, draft_ready=draft)
        self.assertEqual(error.exception.code, 'oauth_structured_reply_target_not_supported')
        routine = {'routineId': str(uuid.uuid4()), 'revision': 1, 'stepIndex': 0, 'scheduledFor': 0, 'status': 'completed'}
        with self.assertRaises(BotSeshError) as error:
            self.api.call(self.binding, 'finish', output={'reply': 'Report', 'routine_report': routine}, receipt_id=self.batch, client_id=str(uuid.uuid4()), processed=True)
        self.assertEqual(error.exception.code, 'oauth_routine_report_not_supported')
        self.assertEqual(self.calls, [])


class PagingAndCursor(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_keeps_after_and_since_not_a_read_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'private')
            store.update(lambda d: d['rooms'].update(one={'room_id': 'one', 'transport': 'oauth', 'active': True}))
            client = Client(store)
            platform_registry.register(PlatformEntry(name='botsesh', label='BotSesh', adapter_factory=BotSeshAdapter, check_fn=lambda: True))
            self.addCleanup(platform_registry.unregister, 'botsesh')
            with patch('hermes_botsesh_test.adapter.Client', return_value=client):
                adapter = BotSeshAdapter(PlatformConfig())
            waits = []
            def enrolled(room_id, op, **args):
                if op == 'listener':
                    return {'read_effect': 'none'}
                self.assertEqual(op, 'wait')
                waits.append(args)
                adapter._running = False
                return {'next_after': len(waits) - 1, 'since': 'exact-cursor-' + str(len(waits))}
            client.enrolled = Mock(side_effect=enrolled)
            for _ in range(2):
                adapter._running = True
                await adapter._stream('one', asyncio.Event())
            self.assertEqual(waits, [{}, {'after': 0, 'since': 'exact-cursor-1'}])
            self.assertEqual(store.room('one')['event_cursor'], {'after': 1, 'since': 'exact-cursor-2'})
            self.assertNotIn('processed_cursor', store.room('one'))
            self.assertNotIn('oauth_receipts', store.room('one'))

    async def test_rpc_exact_paging_full_fragments_and_cursor_loop_fence(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = oauth.OAuthClient(Store(Path(tmp) / 'private'))
            value = {'messages': [{'number': 0, 'text': 'Exact 🐚'}], 'batch_id': 'frozen', 'ack_eligible': True}
            encoded = json.dumps(value, ensure_ascii=False)
            parts = [encoded[:20], encoded[20:]]
            calls = []
            def wire(origin, path, **kwargs):
                payload = kwargs['payload']
                args = payload['params']['arguments']
                calls.append(args)
                if payload['params']['name'] == 'catchup':
                    data = {'next_cursor': 'first'}
                elif args['cursor'] == 'first':
                    data = {'json_fragment': parts[0], 'next_cursor': 'second'}
                else:
                    data = {'json_fragment': parts[1], 'next_cursor': None}
                return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'structuredContent': data}}
            with patch.object(oauth, '_http', side_effect=wire):
                self.assertEqual(api.rpc({'origin': 'https://botsesh.com'}, 'catchup', {'room_id': 'one'}, token='private'), value)
            self.assertEqual(calls, [{'room_id': 'one'}, {'room_id': 'one', 'cursor': 'first'}, {'room_id': 'one', 'cursor': 'second'}])
            def loop(origin, path, **kwargs):
                payload = kwargs['payload']
                return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {'structuredContent': {'next_cursor': 'first', 'json_fragment': '{}'}}}
            with patch.object(oauth, '_http', side_effect=loop), self.assertRaises(BotSeshError) as error:
                api.rpc({'origin': 'https://botsesh.com'}, 'catchup', {'room_id': 'one'}, token='private')
            self.assertEqual(error.exception.code, 'context_limit')
            with patch.object(oauth, '_http', return_value={'jsonrpc': '2.0', 'id': 'foreign', 'result': {}}), self.assertRaises(BotSeshError) as error:
                api.rpc({'origin': 'https://botsesh.com'}, 'catchup', {'room_id': 'one'}, token='private')
            self.assertEqual(error.exception.code, 'invalid_mcp_response')


if __name__ == '__main__':
    unittest.main()
