"""Fault-injection doubles through the actual adapter. No model/network calls."""
# Owner 2026-11-10 living chat: runtime errors use delivery status, not room narration.
import asyncio
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch
from test_plugin import Client,Store,BotSeshError
from hermes_botsesh_test.adapter import BotSeshAdapter,processed_turn,ended_turn
from hermes_botsesh_test.reliability import receipt,backoff
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry,platform_registry
from gateway.platforms.event import ProcessingOutcome

class SolidTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.client=Client(Store(Path(self.tmp.name)/'private'))
        self.client.store.update(lambda d:d['rooms'].update({rid:{'room_id':rid,'participant_id':'seat-'+rid,'active':True,'name':rid,'url':'https://botsesh.com/b/'+rid,'transport':'oauth'} for rid in ('a','b')}))
        platform_registry.register(PlatformEntry(name='botsesh',label='BotSesh',adapter_factory=BotSeshAdapter,check_fn=lambda:True))
        self.addCleanup(platform_registry.unregister,'botsesh')
        self.posts={};self.calls=[];self.targets={'a':[3,5],'b':[2]}
        def enrolled(room,op,**args):
            self.calls.append((room,op,args))
            if op=='check':return {'mentions':self.targets[room],'replies':[],'backlog':len(self.targets[room])}
            if op=='read':
                n=args['target_number'];return {'receipt_id':'receipt-'+str(n),'batch':{'nextCheckpoint':n,'messages':[{'id':n+100,'number':n,'isOwn':False,'text':'question '+str(n),'senderParticipantId':'owner','authorParticipant':{'id':'owner','verified':True,'name':'Owner'}}]}}
            if op=='tool_policy':
                return {'room_id':room,'participant_id':self.client.store.room(room)['participant_id'],
                        'number':args['target'],'batch_id':args['receipt_id'],'toolsAllowed':True,
                        'requester':{'id':'owner','isOwner':True}}
            if op=='post':
                prior=self.posts.setdefault(args['client_id'],args)
                self.assertEqual(prior,args)
                return {'number':100+len(self.posts)}
            return {}
        self.client.enrolled=Mock(side_effect=enrolled)
        self.adapter=self.make_adapter()
        self.adapter._is_sender_authorized=lambda *args:True
        self.adapter._admit_event=AsyncMock(return_value=True)
    def make_adapter(self):
        with patch('hermes_botsesh_test.adapter.Client',return_value=self.client):return BotSeshAdapter(PlatformConfig())
    async def test_listener_stop_is_metadata_only_and_inactive_binding_needs_no_cleanup_auth(self):
        self.adapter._running=False
        await self.adapter._room_loop('a')
        self.assertIn(('a','listener',{'state':'stopped'}),self.calls)
        self.adapter._admit_event.assert_not_called()
        self.client.store.update(lambda d:d['rooms']['a'].update(active=False))
        self.calls.clear()
        await self.adapter._room_loop('a')
        self.assertEqual(self.calls,[])

    async def test_durable_pickup_admission_and_two_messages_do_not_merge(self):
        await self.adapter._check_room('a')
        row=self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['state'],'admitted');self.assertEqual(row['frozen']['batch']['messages'][0]['text'],'question 3')
        await self.adapter._check_room('a');self.assertEqual(self.adapter._admit_event.await_count,1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['5']['state'],'received')
        self.assertTrue(any(op=='delivery' and args.get('target')==5 and args.get('state')=='received' for _,op,args in self.calls))
        self.adapter._current['a']['delivered']=True
        await self.adapter.on_processing_complete(self.adapter._admit_event.call_args.args[0],ProcessingOutcome.SUCCESS)
        await self.adapter._check_room('a')
        self.assertEqual(self.adapter._admit_event.call_args.args[0].message_id,'5')
        self.assertEqual(self.client.store.room('a')['processed_cursor'],3)
    async def test_restart_mid_turn_is_visible_once_without_repeating_tools(self):
        receipt(self.client.store,'a',3,state='started',instance='old-process',client_id=str(uuid.uuid4()),receipt_id='old')
        self.targets['a']=[]
        await self.adapter._check_room('a');self.adapter._admit_event.assert_not_called()
        self.assertEqual(self.posts,{})
        failures=[a for _,op,a in self.calls if op=='delivery' and a.get('state')=='failed']
        self.assertEqual(failures,[{'target':3,'state':'failed','failure_code':'interrupted'}])
        restarted=self.make_adapter();restarted._admit_event=AsyncMock()
        await restarted._check_room('a');self.assertEqual(self.posts,{})
        self.assertEqual(len([1 for _,op,a in self.calls if op=='delivery' and a.get('state')=='failed']),1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'],'failed')
    async def test_failure_notice_lost_response_retries_same_id_after_restart(self):
        receipt(self.client.store,'a',3,state='started',frozen={'batch':{'messages':[{'text':'private room context'}]}},text='private model input')
        self.adapter._queue_failure('a',3,'provider_budget')
        original=self.client.enrolled.side_effect
        def lose(room,op,**args):
            result=original(room,op,**args)
            if op=='delivery':raise BotSeshError('connection_failed_retry_same_intent')
            return result
        self.client.enrolled.side_effect=lose
        with self.assertRaises(BotSeshError):await self.adapter._flush_failures('a')
        self.assertTrue(self.client.store.room('a')['failure_outbox'])
        self.assertIn('frozen',self.client.store.room('a')['processing_receipts']['3'],'uncertain delivery remains retryable')
        self.client.enrolled.side_effect=original
        await self.make_adapter()._flush_failures('a')
        self.assertEqual(self.posts,{});self.assertFalse(self.client.store.room('a')['failure_outbox'])
        failures = [args for _, op, args in self.calls if op == 'delivery' and args.get('state') == 'failed']
        self.assertEqual(failures, [{'target':3,'state':'failed','failure_code':'provider_budget'}]*2)
        row=self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['state'],'failed');self.assertFalse(row['failure_pending'])
        self.assertNotIn('frozen',row);self.assertNotIn('text',row)
        self.assertNotIn('private room context',self.client.store.path.read_text())
        self.assertNotIn('private model input',self.client.store.path.read_text())
    async def test_legacy_uncertain_notice_is_not_retrofitted(self):
        payload = {'text': "Hermes couldn't answer: provider budget exceeded.", 'reply_to': 3, 'client_id': str(uuid.uuid4())}
        self.client.store.update(lambda d: d['rooms']['a'].update(failure_outbox={'3': {'payload': payload, 'code': 'provider_budget'}}, processing_receipts={'3': {'state': 'failed'}}))
        self.adapter._queue_failure('a', 3, 'provider_error')
        await self.adapter._flush_failures('a')
        self.assertEqual(self.posts, {}, 'even a legacy runtime notice must not enter the room')
        failure = next(args for _, op, args in self.calls if op == 'delivery' and args.get('state') == 'failed')
        self.assertNotIn('notice_client_id', failure)
        self.assertEqual(failure['failure_code'], 'provider_budget', 'retry retains the original queued failure code')
        self.assertFalse(self.client.store.room('a')['failure_outbox'])

    async def test_sender_prefixed_turn_posts_exact_attesting_reply(self):
        await self.adapter._check_room('a')
        prompt=self.adapter._current['a']['text']
        processed_turn(platform='botsesh',user_message='[Owner] '+prompt,session_id='prefixed',turn_id='turn')
        self.assertEqual(self.adapter._current['a']['hook_turn'],('prefixed','turn'))
        self.assertFalse(self.adapter._current['a']['model_processed'],'candidate alone cannot attest')
        ended_turn(platform='botsesh',session_id='other',turn_id='turn',completed=True,failed=False,interrupted=False)
        self.assertFalse(self.adapter._current['a']['model_processed'],'wrong native session cannot attest')
        ended_turn(platform='botsesh',session_id='prefixed',turn_id='turn',completed=True,failed=False,interrupted=False)
        result=await self.adapter.send('a','Useful answer to question 3.',metadata={'notify':True})
        self.assertTrue(result.success)
        post=next(iter(self.posts.values()))
        self.assertEqual(post['text'],'Useful answer to question 3.')
        self.assertEqual(post['reply_to'],3);self.assertEqual(post['receipt_id'],'receipt-3');self.assertTrue(post['processed'])
        await self.adapter.on_processing_complete(self.adapter._admit_event.call_args.args[0],ProcessingOutcome.SUCCESS)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'],'replied')
        self.assertEqual(len(self.posts),1)
    async def test_hook_requires_receipt_and_exact_addressed_number(self):
        await self.adapter._check_room('a')
        for receipt_id,target in [('receipt-3',30),('receipt-other',3)]:
            prompt='[Owner] '+json.dumps({'receipt_id':receipt_id,'addressed_message_number':target})
            processed_turn(platform='botsesh',user_message=prompt,session_id='wrong',turn_id='wrong')
            self.assertNotIn('hook_turn',self.adapter._current['a'])
        processed_turn(platform='telegram',user_message=self.adapter._current['a']['text'],session_id='wrong',turn_id='wrong')
        self.assertNotIn('hook_turn',self.adapter._current['a'])
    async def test_402_and_success_outcome_never_claim_processed_read(self):
        prompt=json.dumps({'receipt_id':'r','addressed_message_number':3})
        self.adapter._current['a']={'target':3,'receipt_id':'r','client_id':str(uuid.uuid4()),'delivered':False,'text':prompt}
        processed_turn(platform='botsesh',user_message='[Owner] '+prompt,session_id='s',turn_id='t')
        self.assertEqual(self.adapter._current['a']['hook_turn'],('s','t'))
        ended_turn(platform='botsesh',session_id='s',turn_id='t',completed=False,failed=True,interrupted=False)
        sent=await self.adapter.send('a','HTTP 402: credits exhausted (private vendor detail)',metadata={'notify':True})
        self.assertTrue(sent.success)
        self.assertEqual(self.posts,{})
        failures=[a for _,op,a in self.calls if op=='delivery' and a.get('state')=='failed']
        self.assertEqual(failures,[{'target':3,'state':'failed','failure_code':'provider_budget'}])
        self.assertNotIn('private vendor detail', self.client.store.path.read_text())
        await self.adapter.on_processing_complete(Mock(source=Mock(chat_id='a'),message_id='3'),ProcessingOutcome.SUCCESS)
        self.assertNotIn('handled',self.client.store.room('a'))
    async def test_iteration_budget_is_not_reported_as_provider_credit_failure(self):
        self.adapter._current['a']={'target':3,'receipt_id':'r','client_id':str(uuid.uuid4()),'delivered':False,'text':'exact input'}
        await self.adapter.send_or_update_status('a','error','⚠️ Iteration budget exhausted (4/4) — asking model to summarise')
        await self.adapter.send('a','Turn stopped.',metadata={'notify':True})
        self.assertEqual(self.posts,{})
        failures=[a for _,op,a in self.calls if op=='delivery' and a.get('state')=='failed']
        self.assertEqual(failures,[{'target':3,'state':'failed','failure_code':'runtime_error'}])

    async def test_native_diagnostic_status_does_not_post_a_duplicate_error(self):
        self.adapter._current['a']={'target':3,'receipt_id':'r','client_id':str(uuid.uuid4()),'delivered':False,'text':'exact input'}
        await self.adapter.send_or_update_status('a','error','HTTP 402: budget exceeded')
        self.assertFalse(self.posts)
        await self.adapter.send('a','Model failed.',metadata={'notify':True})
        self.assertEqual(self.posts,{})
        await self.adapter.on_processing_complete(Mock(source=Mock(chat_id='a'),message_id='3'),ProcessingOutcome.FAILURE)
        await self.adapter._notify_turn_error(Mock(source=Mock(chat_id='a'),message_id='3'),RuntimeError('tool detail'))
        self.assertEqual(self.posts,{})
        failures=[a for _,op,a in self.calls if op=='delivery' and a.get('state')=='failed']
        self.assertEqual(failures,[{'target':3,'state':'failed','failure_code':'provider_budget'}])
    async def test_exact_unicode_chunk_replay_and_only_last_chunk_attests(self):
        text='α🙂\n'*2200
        payload={'text':text,'client_id':str(uuid.uuid4()),'receipt_id':'frozen','processed':True,'reply_to':3}
        await self.adapter._post_payload('a',payload)
        self.assertEqual(''.join(part['text'] for part in self.posts.values()),text)
        self.assertTrue(all(len(part['text'].encode('utf-16-le'))//2<=4000 for part in self.posts.values()))
        parts=list(self.posts.values());self.assertTrue(parts[-1]['processed']);self.assertTrue(all('processed' not in p for p in parts[:-1]))
        before=len(self.posts);await self.make_adapter()._post_payload('a',payload);self.assertEqual(len(self.posts),before)
    async def test_multiple_rooms_are_independent_and_typing_is_coalesced(self):
        await asyncio.gather(self.adapter._check_room('a'),self.adapter._check_room('b'))
        self.assertEqual(set(self.adapter._current),{'a','b'})
        await self.adapter.send_typing('a');await self.adapter.send_typing('a')
        self.assertEqual(len([c for c in self.calls if c[:2]==('a','working')]),1)
        self.assertTrue(any(c[1]=='delivery' and c[2].get('state')=='started' for c in self.calls))
    async def test_long_turn_keeps_frozen_receipt_and_no_inference_retry(self):
        await self.adapter._check_room('a')
        self.client.store.update(lambda d:d['rooms']['a'].update(oauth_receipts={'receipt-3':{'expires':time.time()-7200,'numbers':[3]}}))
        for _ in range(3):await self.adapter._check_room('a')
        self.assertEqual(self.adapter._admit_event.await_count,1)
        self.assertIn('receipt-3',self.client.store.room('a')['oauth_receipts'])
    def test_backoff_jitter_and_retry_after(self):
        with patch('hermes_botsesh_test.reliability.random.uniform',return_value=3.25) as random:
            self.assertEqual(backoff(2),3.25);random.assert_called_once_with(1,4)
            self.assertEqual(backoff(2,retry_after=600),600)
    async def test_discovery_supervisor_recovers_without_stopping_rooms(self):
        self.adapter._running=True
        attempts=[]
        async def sync():
            attempts.append(True)
            if len(attempts)==1:raise OSError('temporary storage read failure')
            self.adapter._running=False
        self.adapter._sync_rooms_loop=sync
        with patch('hermes_botsesh_test.adapter.asyncio.sleep',new_callable=AsyncMock) as sleep:
            await self.adapter._supervise()
        self.assertEqual(len(attempts),2);sleep.assert_awaited_once()
    async def test_passive_poll_recovers_503_with_saved_hint_cursor(self):
        self.adapter._running=True
        self.client.store.update(lambda d:d['rooms']['a'].update(event_cursor={'after':7,'since':9}))
        attempts=[]
        heartbeats=[]
        def poll(room,op,**args):
            if op == 'listener':
                heartbeats.append((room,args))
                return {'read_effect':'none'}
            attempts.append(args)
            if len(attempts)==1:raise BotSeshError('temporarily_unavailable',503)
            self.adapter._running=False
            return {'next_after':10,'since':11}
        self.client.enrolled.side_effect=poll
        with patch('hermes_botsesh_test.adapter.asyncio.sleep',new_callable=AsyncMock):
            await self.adapter._stream('a',asyncio.Event())
        self.assertEqual(heartbeats,[('a',{'state':'listening'}),('a',{'state':'listening'})])
        self.assertEqual(attempts,[{'after':7,'since':9},{'after':7,'since':9}])
        self.assertEqual(self.client.store.room('a')['event_cursor'],{'after':10,'since':11})
