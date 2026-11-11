"""Python-only client: real exact-image HTTP + scripted adapter, never paid inference.

BOTSESH_INVITATION_SERVER_COMMAND is a JSON argv for the isolated fixture server
(e.g. docker run --rm -i --network host ... node python-invitation-server.mjs /app/app.js).
Run with PATH empty; only the explicitly absolute server launcher may use Node.
"""
import asyncio
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from test_plugin import Client, Store, BotSeshError, StorageError, tools


class PythonInvitationUnits(unittest.TestCase):
    def setUp(self):
        from hermes_botsesh_test.invitation import InvitationClient
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.client = Client(Store(Path(self.tmp.name) / 'profile'))
        self.api = InvitationClient(self.client.store)
        self.id = str(uuid.uuid4())
        self.link = 'https://botsesh.com/agent-room/room/invite/' + self.id + '/bsi_' + 'a' * 43

    def test_new_and_legacy_parse_to_python_and_bad_origins_never_redeem(self):
        with patch.object(type(self.api), 'join', return_value={'room_id': 'room'}) as join, patch.object(Client, 'command', side_effect=AssertionError('Node forbidden')):
            self.assertEqual(self.client.join(self.link), {'room_id': 'room'})
            join.assert_called_once_with('https://botsesh.com', 'room', self.id, 'bsi_' + 'a' * 43)
            legacy = 'https://botsesh.com/b/room#agent-invite=' + self.id + '.bsi_' + 'a' * 43
            self.client.join(legacy)
            for bad in (self.link.replace('https://botsesh.com', 'https://evil.example'), self.link+'?secret=x', self.link+'#x', self.link.replace(self.id, str(uuid.uuid1()))):
                with self.assertRaises(BotSeshError): self.client.join(bad)
            self.assertEqual(join.call_count, 2)

    def test_uncertain_redemption_preserves_nonce_recovery_and_refuses_replacement(self):
        from hermes_botsesh_test import invitation
        bodies = []
        def lost(origin, room, method, suffix, **kw):
            bodies.append(kw['payload'])
            raise BotSeshError('connection_failed_retry_same_intent')
        with patch.object(invitation, '_http', side_effect=lost), patch.object(Client, 'command', side_effect=AssertionError('Node forbidden')):
            for _ in range(2):
                with self.assertRaises(BotSeshError): self.client.join(self.link)
            with self.assertRaises(BotSeshError) as error:
                self.client.join(self.link.replace(self.id, str(uuid.uuid4())))
        self.assertEqual(error.exception.code, 'pending_invitation_mismatch')
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])
        self.assertRegex(bodies[0]['claimNonce'], r'^[A-Za-z0-9_-]{43}$')
        self.assertRegex(bodies[0]['recoverySecret'], r'^bsr_[A-Za-z0-9_-]{43}$')
        self.assertEqual(self.client.store.read()['rooms'], {})
        self.assertEqual(self.api.private.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('bsi_', json.dumps(self.client.store.read()))

    def test_existing_detached_and_foreign_bindings_never_create_seats(self):
        from hermes_botsesh_test import invitation
        self.client.store.update(lambda d:d['rooms'].update(room={
            'url':'https://botsesh.com/b/room','origin':'https://botsesh.com','participant_id':'existing','active':False}))
        with patch.object(invitation, '_http') as request:
            with self.assertRaises(StorageError): self.client.join(self.link)
            request.assert_not_called()
        self.client.store.update(lambda d:d['rooms']['room'].update(active=True, origin='http://127.0.0.1:1'))
        with patch.object(invitation, '_http') as request:
            with self.assertRaises(BotSeshError): self.client.join(self.link)
            request.assert_not_called()

    def test_saved_sdk_pending_claim_is_not_forked(self):
        import hashlib
        name='botsesh-agent-'+hashlib.sha256(b'https://botsesh.com\0room').hexdigest()+'.json'
        path=self.client.store.credentials/name
        path.write_text('{}');path.chmod(0o600)
        with self.assertRaises(BotSeshError) as error:self.client.join(self.link)
        self.assertEqual(error.exception.code,'legacy_invitation_requires_original_advanced_store')
        self.assertEqual(self.api.private.read()['rooms'],{})

    def test_http_cookie_free_redirect_proxy_and_bounded_response(self):
        import httpx
        from hermes_botsesh_test import invitation
        original=httpx.Client
        requests=[]
        status,body,headers=200,b'{"ok":true}',{}
        def handler(req):
            requests.append(req)
            return httpx.Response(status,content=body,headers=headers)
        def factory(**kw):
            self.assertIs(kw['follow_redirects'],False)
            self.assertIs(kw['trust_env'],False)
            return original(**kw,transport=httpx.MockTransport(handler))
        with patch('httpx.Client',side_effect=factory):
            self.assertEqual(invitation._http('https://botsesh.com','room','GET','/agent-access',bearer='bsa_'+'a'*43),{'ok':True})
            self.assertNotIn('cookie',requests[-1].headers)
            status,body,headers=429,b'{"code":"rate_limit","private":"never echo"}',{'retry-after':'2.5'}
            with self.assertRaises(BotSeshError) as error:invitation._http('https://botsesh.com','room','POST','/agent-invites/redeem',payload={})
            self.assertEqual((error.exception.code,error.exception.retry_after_ms),('rate_limit',2500))
            self.assertNotIn('never echo',str(error.exception))
            status,body,headers=302,b'{}',{'location':'https://evil.example'}
            with self.assertRaises(BotSeshError):invitation._http('https://botsesh.com','room','POST','/agent-invites/redeem',payload={})
            status,body,headers=200,b'x'*(2*1024*1024+1),{}
            with self.assertRaises(BotSeshError) as error:invitation._http('https://botsesh.com','room','GET','/agent-access',bearer='bsa_'+'a'*43)
            self.assertEqual(error.exception.code,'response_limit')
            self.assertEqual(len(requests),4)
        for suffix in ('/../agent-access','/agent-access?token=x','/settings'):
            with self.assertRaises(BotSeshError):invitation._http('https://botsesh.com','room','GET',suffix,bearer='bsa_'+'a'*43)

    def test_wrong_enrollment_identity_is_not_adopted_and_capabilities_stay_scoped(self):
        from hermes_botsesh_test import invitation
        value={'room':{'id':'other'},'participantId':'seat',
               'membership':{'id':'membership','participantId':'seat','expiresAt':9999999999999},
               'access':{'token':'bsa_'+'a'*43,'expiresAt':9999999999999}}
        with patch.object(invitation,'_http',return_value=value):
            with self.assertRaises(BotSeshError) as error:self.client.join(self.link)
        self.assertEqual(error.exception.code,'invalid_enrollment_response')
        self.assertEqual(self.client.store.read()['rooms'],{})
        self.assertNotIn('access_token',self.api.private.read()['rooms']['room'])
        for capability in ('hermes_tool_policy','hermes_listener','hermes_initialize','list_rooms','create_room','offer_ownership'):
            with self.assertRaises(BotSeshError):self.api.rpc({},capability,{})

    def test_forget_scrubs_only_this_room_invitation_secrets(self):
        from hermes_botsesh_test.room_lifecycle import forget
        self.api.private.update(lambda d:d['rooms'].update({rid:{'access_token':'bsa_'+rid,'request':{'invitationToken':'bsi_'+rid}} for rid in ('room','other')}))
        self.client.store.update(lambda d:d['rooms'].update(room={'room_id':'room','transport':'invitation','active':True}))
        with patch('hermes_cli.config.load_config',return_value={}),patch('hermes_cli.config.set_config_value'):
            forget(self.client,['room'])
        self.assertEqual(set(self.api.private.read()['rooms']),{'other'})
        self.assertNotIn('room',self.client.store.read()['rooms'])
        self.assertTrue(self.client.store.read()['forgotten_rooms']['room'])


@unittest.skipUnless(os.environ.get('BOTSESH_INVITATION_SERVER_COMMAND'), 'explicit isolated real-server argv required')
class RealInvitationTests(unittest.IsolatedAsyncioTestCase):
    def record(self, name, **facts):
        path=os.environ.get('BOTSESH_INVITATION_EVIDENCE')
        if path:
            with open(path,'a',encoding='utf-8') as stream:
                stream.write(json.dumps({'test':name,'node_available':shutil.which('node') is not None,'nodejs_available':shutil.which('nodejs') is not None,**facts})+'\n')

    async def asyncSetUp(self):
        self.assertIsNone(shutil.which('node'))
        self.assertIsNone(shutil.which('nodejs'))
        self.tmp=tempfile.TemporaryDirectory(prefix='python-invitation-')
        self.addCleanup(self.tmp.cleanup)
        self.home_patch=patch.dict(os.environ,{'HERMES_HOME':self.tmp.name})
        self.home_patch.start();self.addCleanup(self.home_patch.stop)
        self.client=Client(Store(Path(self.tmp.name)/'plugin-data/botsesh'))
        self.server=subprocess.Popen(json.loads(os.environ['BOTSESH_INVITATION_SERVER_COMMAND']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop_server)
        with selectors.DefaultSelector() as selector:
            selector.register(self.server.stdout,selectors.EVENT_READ)
            self.assertTrue(selector.select(30),'local exact-image server startup timed out')
        line=self.server.stdout.readline()
        if not line:self.fail('server failed: '+self.server.stderr.read())
        self.origin=json.loads(line)['origin']
        self.fixture=self.command(op='create')

    def stop_server(self):
        if self.server.poll() is None:
            self.server.stdin.write('{"op":"stop"}\n');self.server.stdin.flush()
        _,err=self.server.communicate(timeout=35)
        self.assertEqual(self.server.returncode,0,err)

    def command(self,**value):
        self.server.stdin.write(json.dumps(value)+'\n');self.server.stdin.flush()
        line=self.server.stdout.readline()
        if not line:self.fail('fixture failed: '+self.server.stderr.read())
        return json.loads(line)

    async def test_paste_first_reply_and_automatic_peer_return(self):
        from hermes_botsesh_test.adapter import BotSeshAdapter,processed_turn,ended_turn
        from gateway.config import PlatformConfig
        from gateway.platform_registry import PlatformEntry,platform_registry
        from gateway.platforms.event import ProcessingOutcome
        room=self.fixture['room_id']
        q1=self.command(op='message',room_id=room,text='@Hermes first question: preserve café 🐚')['message']
        ctx=Mock();ctx.session_surface.return_value='cli';ctx.get_config.return_value=self.origin
        with patch.object(tools,'Client',return_value=self.client):
            # Exact model-facing paste tool, not a terminal join command.
            joined=await asyncio.to_thread(tools.join,{'invitation_url':self.fixture['link']},ctx)
            self.assertEqual(joined['room_id'],room)
            self.assertEqual(joined['read_effect'],'none')
            before=self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])
            self.assertEqual((before['checkpoint'],before['attestations']),(0,0))
            late=self.command(op='message',room_id=room,text='Later unaddressed data must remain unread')['message']
            first=await asyncio.to_thread(tools.post,{'room_id':room,'text':'First answer: café 🐚','client_id':str(uuid.uuid4()),'receipt_id':joined['receipt_id'],'reply_to':q1['number'],'processed':True},ctx)
        self.assertEqual(first['read_effect'],'processed_frozen_receipt')
        self.assertEqual(self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])['checkpoint'],q1['id'])
        self.assertLess(q1['id'],late['id'])
        # Legacy URL is also a fresh Python seat. Peer sends through real HTTP.
        peer=Client(Store(Path(self.tmp.name)/'peer'))
        peer_joined=await asyncio.to_thread(peer.join,self.fixture['peer'],dev_origin=self.origin)
        self.assertNotEqual(peer_joined['participant_id'],joined['participant_id'])
        platform_registry.register(PlatformEntry(name='botsesh',label='BotSesh',adapter_factory=BotSeshAdapter,check_fn=lambda:True))
        adapter=BotSeshAdapter(PlatformConfig())
        adapter.client=self.client
        adapter.set_authorization_check(lambda user,kind,chat:user==peer_joined['participant_id'] and chat==room)
        finished=asyncio.Event();calls=[];workers=[];failures=[]
        async def scripted_model(event):
            try:
                await adapter.on_processing_start(event)
                calls.append(event.message_id)
                processed_turn(platform='botsesh',user_message=event.text,session_id='scripted-local',turn_id='return')
                ended_turn(platform='botsesh',session_id='scripted-local',turn_id='return',completed=True,failed=False,interrupted=False)
                sent=await adapter.send(room,'Automatic second answer: exact peer return',metadata={'notify':True})
                self.assertTrue(sent.success,sent.error)
                await adapter.on_processing_complete(event,ProcessingOutcome.SUCCESS)
            except BaseException as error:failures.append(error)
            finally:finished.set()
        async def scripted_admission(event):
            workers.append(asyncio.create_task(scripted_model(event)))
            return True
        adapter._admit_event=scripted_admission  # Only model/admission seam doubled.
        adapter._running=True
        loop=asyncio.create_task(adapter._room_loop(room))
        try:
            await asyncio.sleep(0.1)
            self.assertFalse(finished.is_set(),'no inference before a real addressed peer message')
            second=await asyncio.to_thread(peer.enrolled,room,'post',text='Second question from Peer, without a mention',client_id=str(uuid.uuid4()),processed=False)
            await asyncio.wait_for(finished.wait(),20)
            await asyncio.gather(*workers)
            if failures:raise failures[0]
            self.assertEqual(calls,[str(second['number'])])
            await adapter._check_room(room)
            self.assertEqual(len(calls),1,'no self wake or duplicate inference')
            result=self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])
            self.assertEqual([m['text'] for m in result['messages'] if 'answer:' in m['text']],['First answer: café 🐚','Automatic second answer: exact peer return'])
            self.assertEqual(result['memberships'],2)
            self.assertTrue(self.client.store.room(room)['processing_receipts'][str(second['number'])]['state']=='replied')
            self.assertNotIn('pending_final',self.client.store.room(room))
            self.record('paste_first_reply_automatic_peer_return',new_style_tool_join=True,legacy_peer_join=True,
                        first_reply='First answer: café 🐚',second_reply='Automatic second answer: exact peer return',
                        scripted_background_model_invocations=len(calls),source_number=second['number'],
                        automatic_source='running adapter._room_loop; no second manual check',
                        peer_input_unmentioned=True,unallowlisted_human_not_admitted=True,
                        later_arrival_unread_after_first_reply=True,memberships=result['memberships'],
                        registered_native_gateway=False,provider_requests=0)
        finally:
            adapter._running=False;loop.cancel();await asyncio.gather(loop,return_exceptions=True)

    async def test_legacy_retry_no_read_and_revoked_foreign_denial(self):
        from hermes_botsesh_test import invitation
        room=self.fixture['room_id']
        original=invitation._http
        lost=[True]
        def loss(*args,**kw):
            value=original(*args,**kw)
            if args[3]=='/agent-invites/redeem' and lost[0]:
                lost[0]=False;raise BotSeshError('connection_failed_retry_same_intent')
            return value
        with patch.object(invitation,'_http',side_effect=loss):
            with self.assertRaises(BotSeshError):await asyncio.to_thread(self.client.join,self.fixture['legacy'],dev_origin=self.origin)
        joined=await asyncio.to_thread(self.client.join,self.fixture['legacy'],dev_origin=self.origin)
        self.assertEqual(self.client.join(self.fixture['link'],dev_origin=self.origin),joined)
        q=self.command(op='message',room_id=room,text='@Hermes exact receipt source')['message']
        frozen=self.client.enrolled(room,'read')
        operation=str(uuid.uuid4())
        post=self.client.enrolled(room,'post',text='Literal no-read authored text',client_id=operation,processed=False)
        self.assertEqual(self.client.enrolled(room,'post',text='Literal no-read authored text',client_id=operation,processed=False),post)
        self.assertEqual(self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])['checkpoint'],0)
        with self.assertRaises(BotSeshError):self.client.enrolled(room,'post',text='Changed retry',client_id=operation,processed=False)
        with self.assertRaises(BotSeshError):self.client.enrolled(room,'post',text='No-read threading refused',client_id=str(uuid.uuid4()),reply_to=q['number'],processed=False)
        with self.assertRaises(BotSeshError):self.client.enrolled(room,'post',text='Foreign receipt refused',client_id=str(uuid.uuid4()),receipt_id=str(uuid.uuid4()),processed=True)
        with self.assertRaises(BotSeshError):self.client.enrolled(room,'finish',output={'reply':'Unsupported structured invitation finish'},client_id=str(uuid.uuid4()),receipt_id=frozen['receipt_id'],processed=True)
        self.assertIsNone(self.client.enrolled(room,'pending')['pending'],'known local refusals must not strand later background replies')
        # Force finite MCP output paging, then lose a genuinely committed send.
        # The shared Python journal must replay the same UUID and frozen batch.
        for n in range(3):self.command(op='message',room_id=room,text=('Exact paged text '+str(n)+' café 🐚 ')*120)
        paged=self.client.enrolled(room,'read')
        self.assertGreater(len(json.dumps(paged).encode()),8192)
        late=self.command(op='message',room_id=room,text='Outside the frozen paged receipt')['message']
        operation=str(uuid.uuid4())
        api=invitation.InvitationClient
        rpc=api.rpc;lost_send=[True]
        def lose_send(instance,binding,name,args=None,**kw):
            value=rpc(instance,binding,name,args,**kw)
            if name=='send_message' and lost_send[0]:
                lost_send[0]=False;raise BotSeshError('connection_failed_retry_same_intent')
            return value
        with patch.object(api,'rpc',lose_send):
            with self.assertRaises(BotSeshError):self.client.enrolled(room,'post',text='Exactly once after lost response',client_id=operation,receipt_id=paged['receipt_id'],reply_to=q['number'],processed=True)
        self.client=Client(Store(self.client.store.root))
        recovered=self.client.enrolled(room,'retry',client_id=operation)
        self.assertEqual(recovered['read_effect'],'processed_frozen_receipt')
        observed=self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])
        self.assertEqual(len([m for m in observed['messages'] if m['text']=='Exactly once after lost response']),1)
        self.assertLess(observed['checkpoint'],late['id'])
        quiet=self.client.enrolled(room,'read')
        before_count=len(observed['messages'])
        passed=self.client.enrolled(room,'finish',output={'nothing_to_add':True},receipt_id=quiet['receipt_id'],client_id=str(uuid.uuid4()),processed=True)
        self.assertEqual(passed['read_effect'],'processed_frozen_receipt')
        observed=self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])
        self.assertEqual(len(observed['messages']),before_count)
        other=self.command(op='create',name='Foreign room')
        foreign=Client(Store(Path(self.tmp.name)/'foreign'))
        foreign.join(other['link'],dev_origin=self.origin)
        private=invitation.InvitationClient(self.client.store).private
        own=private.read()['rooms'][room]['access_token']
        stolen=invitation.InvitationClient(foreign.store).private.read()['rooms'][other['room_id']]['access_token']
        private.update(lambda d:d['rooms'][room].update(access_token=stolen))
        with self.assertRaises(BotSeshError):self.client.enrolled(room,'read')
        private.update(lambda d:d['rooms'][room].update(access_token=own))
        self.client.store.update(lambda d:d['rooms'][room].update(active=True))
        self.command(op='revoke',room_id=room,participant_id=joined['participant_id'])
        with self.assertRaises(BotSeshError):self.client.enrolled(room,'post',text='Revoked refused',client_id=str(uuid.uuid4()),receipt_id=frozen['receipt_id'],processed=True)
        self.assertFalse(self.client.store.read()['rooms'][room]['active'])
        self.assertEqual(self.command(op='inspect',room_id=room,participant_id=joined['participant_id'])['memberships'],1)
        self.record('legacy_retry_no_read_revoked_foreign',legacy_join=True,uncertain_redeem_same_seat=True,
                    no_read_post_checkpoint=0,no_read_threading_refused=True,foreign_receipt_refused=True,
                    paged_context_bytes=len(json.dumps(paged).encode()),lost_send_exactly_once=True,
                    frozen_later_arrival_unread=True,quiet_pass_no_message=True,foreign_token_denied=True,
                    revoked_send_denied=True,provider_requests=0)
