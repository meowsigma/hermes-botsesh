"""Real loopback native OAuth path, scripted tools, zero model/provider calls.

Set BOTSESH_STALE_SERVER_ROOT to the exact server checkout. No credential data
is written to evidence; the fixture token travels only over owned pipes.
"""
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from test_plugin import Client, Store, tools, ROOT
from hermes_botsesh_test.oauth import OAuthClient
from gateway.session_context import set_session_vars, clear_session_vars


@unittest.skipUnless(os.environ.get('BOTSESH_STALE_SERVER_ROOT'), 'explicit owned local server checkout required')
class RealStalePost(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.server = subprocess.Popen([shutil.which('node'), str(ROOT/'tests/stale-post-server.mjs'), os.environ['BOTSESH_STALE_SERVER_ROOT']], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        self.fixture = self.line()
        self.room = self.fixture['room_id']
        self.client = Client(Store(Path(temp.name)/'private'))
        self.client.store.update(lambda d: d['rooms'].update({self.room: {
            'room_id': self.room, 'origin': self.fixture['origin'], 'url': self.fixture['origin']+'/b/'+self.room,
            'participant_id': self.fixture['participant_id'], 'transport': 'oauth', 'active': True}}))
        access = patch.object(OAuthClient, '_access', return_value=self.fixture['access_token'])
        access.start(); self.addCleanup(access.stop)

    def line(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.server.stdout, selectors.EVENT_READ)
            if not selector.select(30):
                self.server.kill()
                self.fail('local fixture response timed out')
        line = self.server.stdout.readline()
        if not line:
            self.fail('local fixture exited: '+self.server.stderr.read()[-3000:])
        return json.loads(line)

    def command(self, **fields):
        self.server.stdin.write(json.dumps(fields)+'\n'); self.server.stdin.flush()
        return self.line()

    def stop(self):
        if self.server.poll() is None:
            self.server.stdin.write('{"op":"stop"}\n'); self.server.stdin.flush(); self.server.stdin.close()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill(); self.server.wait(timeout=10)
        self.server.stdout.close(); self.server.stderr.close()

    def post(self, args, target):
        tokens = set_session_vars(platform='botsesh', chat_id=self.room, message_id=str(target))
        try:
            ctx=Mock();ctx.session_surface.return_value='botsesh'
            with patch.object(tools, 'Client', return_value=self.client):
                return json.loads(tools.guarded(tools.post)(args, ctx=ctx))
        finally:
            clear_session_vars(tokens)

    def freeze(self, target):
        frozen=self.client.enrolled(self.room, 'read')
        self.client.store.update(lambda d: d['rooms'][self.room].setdefault('processing_receipts', {}).update({str(target): {
            'state':'started', 'target':target, 'receipt_id':frozen['receipt_id'], 'frozen':frozen}}))
        return frozen

    def test_definitive_stale_refusal_is_terminal_after_store_restart_for_every_native_send_kind(self):
        from test_plugin import BotSeshError
        q = self.command(op='human', text='Frozen input for restart proof')
        frozen = self.freeze(q['number'])
        self.command(op='human', text='A follow-up while the native agent thinks')
        before = self.command(op='inspect')
        for kind in ('authored', 'processed', 'structured'):
            with self.subTest(kind=kind):
                cid = str(uuid.uuid4())
                args = {'text': 'Must not post stale '+kind, 'client_id': cid}
                if kind != 'authored':
                    args.update(processed=True, receipt_id=frozen['receipt_id'])
                if kind == 'structured':
                    args['draft_ready'] = {'title': 'Draft for review', 'recipientId': str(uuid.uuid4())}
                denied = self.post(args, q['number'])
                self.assertEqual(denied['error'], 'room_moved_on', denied)
                self.assertNotIn('pending', denied)
                section = 'oauth_turns' if kind == 'structured' else 'oauth_posts'
                saved = self.client.store.room(self.room)[section][cid]
                self.assertEqual(saved['exact']['seen_through'], q['id'])
                self.assertEqual(saved['failed'], {'code': 'room_moved_on', 'status': 409})
                # Fresh Client AND Store, not a second call against cached state.
                self.client = Client(Store(self.client.store.root))
                self.assertIsNone(OAuthClient(self.client.store).pending(self.room))
                with patch.object(OAuthClient, 'rpc', side_effect=AssertionError('terminal send replayed on restart')) as rpc:
                    for _ in range(3):
                        with self.assertRaises(BotSeshError) as replay:
                            self.client.enrolled(self.room, 'retry', client_id=cid)
                        self.assertEqual(replay.exception.code, 'room_moved_on')
                        self.assertTrue(replay.exception.definitive)
                        self.assertIsNone(replay.exception.pending)
                    repeated = self.post(args, q['number'])
                    self.assertEqual(repeated['error'], 'room_moved_on', repeated)
                    self.assertNotIn('pending', repeated)
                    rpc.assert_not_called()
        self.assertEqual(self.command(op='inspect'), before, 'refusal and restart neither post nor attest reads')
        later = self.command(op='human', text='Distinct later input after restart')
        self.freeze(later['number'])
        result = self.post({'text': 'Fresh later reply', 'processed': True, 'client_id': str(uuid.uuid4())}, later['number'])
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.command(op='inspect')['messages'][-1]['text'], 'Fresh later reply')

    def test_native_conflict_reaches_tool_without_read_or_retry_flood_then_explicit_override_and_exact_replay(self):
        q=self.command(op='human', text='Choose option A or B.')
        frozen=self.freeze(q['number'])
        peer=self.command(op='peer', text='Option A fits the constraints.', seen_through=q['id'])
        before=self.command(op='inspect')
        rejected_id=str(uuid.uuid4())
        args={'text':'Option A fits the constraints.', 'client_id':rejected_id}
        refused=self.post(args,q['number'])
        self.assertFalse(refused['ok'],refused)
        self.assertEqual(refused['error'],'room_moved_on')
        self.assertEqual(refused['room_moved_on']['messages'][0]['id'],peer['id'])
        self.assertEqual(refused['room_moved_on']['messages'][0]['text'],'Option A fits the constraints.')
        self.assertIn('✓ agree',refused['message'])
        self.assertNotIn('pending',refused)
        self.assertEqual(self.command(op='inspect'),before)
        audit=self.client.store.room(self.room)['processing_receipts'][str(q['number'])]['explicit_post_calls'][rejected_id]
        self.assertEqual(audit['rejected']['code'],'room_moved_on')
        self.assertEqual(audit['payload']['seen_through'],q['id'])
        for _ in range(3):
            again=self.post(args,q['number']);self.assertFalse(again['ok']);self.assertNotIn('pending',again)
        self.assertEqual(self.command(op='inspect'),before)
        # An explicit override is a NEW authored operation, never an automatic retry.
        override={**args,'client_id':str(uuid.uuid4()),'allow_stale':True}
        sent=self.post(override,q['number']);self.assertTrue(sent['ok'],sent)
        self.assertEqual(sent['result']['read_effect'],'none')
        after=self.command(op='inspect');self.assertEqual(after['read'],before['read']);self.assertEqual(after['attestations'],before['attestations'])
        self.assertEqual(after['messages'][-1]['participant'],self.fixture['participant_id'])
        self.assertEqual(after['messages'][-1]['text'],override['text'])
        later=self.command(op='human',text='Distinct later input')
        self.assertTrue(self.post(override,q['number'])['ok'])
        self.assertEqual(self.command(op='inspect')['messages'][-1]['id'],later['id'])
        self.assertEqual(self.post({**override,'allow_stale':False},q['number'])['error'],'pending_send_mismatch')
        # Later receipt discovery must not widen the old native input's view or
        # resolve another input's attestation (23202a8b boundary).
        second=self.freeze(later['number'])
        room=self.client.store.room(self.room)
        self.assertGreater(room['latest_seen_id'],q['id'])
        stale_again=self.post({'text':'Still based only on first input'},q['number'])
        self.assertEqual(stale_again['error'],'room_moved_on')
        self.assertEqual(stale_again['room_moved_on']['seen_through'],q['id'])
        other=self.post({'text':'Must not borrow later receipt','processed':True,'receipt_id':second['receipt_id'],'allow_stale':True},q['number'])
        self.assertEqual(other['error'],'receipt_expired')
        processed=self.post({'text':'Adds an independent detail.','processed':True,'receipt_id':frozen['receipt_id'],'allow_stale':True},q['number'])
        self.assertTrue(processed['ok'],processed)
        final=self.command(op='inspect')
        self.assertEqual(final['read']['read_checkpoint'],q['id'])
        self.assertFalse(any(row['through_id']>=peer['id'] for row in final['attestations']))
        self.assertEqual(self.command(op='revoke'),200)
        revoked=self.post({**override,'client_id':str(uuid.uuid4())},q['number'])
        self.assertFalse(revoked['ok']);self.assertNotIn('room_moved_on',revoked)
        evidence={'basis':'real loopback server + native task-local tool/Client/OAuth journal; scripted caller, no model', 'provider_requests':0,'conflict':refused,'override':sent,'processed':processed,'read_checkpoint':final['read']['read_checkpoint'],'exact_replay_no_duplicate':True,'other_input_receipt_denied':True,'revoked_denied':True}
        if os.environ.get('BOTSESH_STALE_EVIDENCE'):
            Path(os.environ['BOTSESH_STALE_EVIDENCE']).write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')


class StalePostSettlement(unittest.IsolatedAsyncioTestCase):
    """Native terminal control flow; MCP rejection only is doubled."""
    from test_receipt_scope_finish import ReceiptScopeFinishTests as _Fixture
    asyncSetUp = _Fixture.asyncSetUp
    begin = _Fixture.begin
    model_ended = _Fixture.model_ended
    add_other = _Fixture.add_other
    post = _Fixture.post
    assert_finished_without_other_read = _Fixture.assert_finished_without_other_read

    async def test_rejected_stale_post_never_retries_or_posts_a_final_and_next_input_is_admitted(self):
        from test_plugin import BotSeshError
        adapter, event = await self.begin()
        other = self.add_other()
        original = OAuthClient.rpc
        calls = []
        def reject(api, binding, name, args=None, **kwargs):
            if name in ('send_message', 'hermes_authored'):
                calls.append(name)
                raise BotSeshError('room_moved_on', 409, rejected=True,
                    room_moved_on={'code':'room_moved_on','messages':[{'number':4,'text':'Peer conclusion'}],'read_effect':'none'})
            return original(api, binding, name, args, **kwargs)
        with patch.object(OAuthClient, 'rpc', reject):
            denied = self.post({'text':'Overlapping conclusion','processed':True,'client_id':str(uuid.uuid4())})
            self.assertEqual(denied['error'],'room_moved_on')
            self.assertNotIn('pending',denied)
            await self.assert_finished_without_other_read(adapter, event, other)
        self.assertEqual(calls,['send_message'],'no automatic send/override loop')


if __name__=='__main__':
    unittest.main()
