"""Focused offline doubles; native production proof is separate."""
import json
import uuid
from unittest.mock import Mock, patch
from test_solid import SolidTests
from test_oauth import OAuthTests
from botsesh_oauth_test.client import BotSeshError as OAuthError
from hermes_botsesh_test import tools
from hermes_botsesh_test.task_failure import cant_do, validate_failure
from hermes_botsesh_test.client import BotSeshError
from gateway.session_context import set_session_vars, clear_session_vars

TASK = '11111111-1111-4111-8111-111111111111'
REASON = 'No image generation tool is available to me.'

class CantDoOAuthTests(OAuthTests):
    def test_missing_task_uses_exact_frozen_message_and_receipt(self):
        batch=str(uuid.uuid4());cid=str(uuid.uuid4())
        self.store.update(lambda d:d['rooms']['one'].update(oauth_receipts={batch:{'expires':9999999999,'numbers':[3]}}))
        failure={'message_number':3,'message_version':0,'status':'cant_do','reason':REASON}
        result={'failure':{'taskId':TASK,'status':'cant_do','source':{'number':3,'version':0}},'read_effect':'none'}
        with patch.object(self.client,'rpc',return_value=result) as rpc:
            self.assertEqual(self.client.call(self.binding,'fail_task',failure=failure,receipt_id=batch,client_id=cid),result)
            rpc.assert_called_once_with(self.binding,'hermes_cant_do',{'room_id':'one','batch_id':batch,'client_id':cid,'number':3,'message_version':0,'reason':REASON})
        with patch.object(self.client,'rpc') as rpc:
            with self.assertRaises(OAuthError):self.client.call(self.binding,'fail_task',failure={**failure,'message_number':4},receipt_id=batch,client_id=cid)
            rpc.assert_not_called()
        with patch.object(self.client,'rpc',return_value={**result,'failure':{**result['failure'],'source':{'number':4,'version':0}}}):
            with self.assertRaises(OAuthError):self.client.call(self.binding,'fail_task',failure=failure,receipt_id=batch,client_id=cid)

    def test_existing_finish_turn_no_scope_expansion_and_no_read(self):
        batch = str(uuid.uuid4())
        self.store.update(lambda d: d['rooms']['one'].update(oauth_receipts={batch: {'expires': 9999999999, 'numbers': [3]}}))
        failure = {'task_id': TASK, 'status': 'cant_do', 'reason': REASON}
        result = {'failure': {'taskId': TASK, 'status': 'cant_do', 'state': 'offered'}, 'read_effect': 'none'}
        cid = str(uuid.uuid4())
        with patch.object(self.client, 'rpc', return_value=result) as rpc:
            self.assertEqual(self.client.call(self.binding, 'fail_task', failure=failure, receipt_id=batch, client_id=cid), result)
            rpc.assert_called_once_with(self.binding, 'finish_room_turn', {'room_id':'one', 'batch_id':batch, 'client_id':cid, 'output':{'failure':failure}})
        self.assertEqual(self.store.room('one')['oauth_receipts'][batch]['numbers'], [3])
        with patch.object(self.client, 'rpc') as rpc:
            for bad in [{**failure, 'status':'error'}, {**failure, 'reason':'clarify\nplease'}, {**failure, 'task_id':'foreign'}]:
                with self.assertRaises(OAuthError):
                    self.client.call(self.binding, 'fail_task', failure=bad, receipt_id=batch, client_id=cid)
            with self.assertRaises(OAuthError):
                self.client.call(self.binding, 'fail_task', failure=failure, receipt_id='foreign', client_id=cid)
            rpc.assert_not_called()

class CantDoNativeTests(SolidTests):
    async def test_plain_ask_no_task_passes_exact_target_version_and_replays_once(self):
        tokens=await self.prepare();original=self.client.enrolled.side_effect
        self.client.store.update(lambda d:d['rooms']['a']['processing_receipts']['3']['frozen']['batch']['messages'][0].update(addressing={'reason':'mention'},editCount=2))
        def implicit(room,op,**args):
            if op=='fail_task':
                self.calls.append((room,op,args))
                self.assertEqual(args['failure'],{'message_number':3,'message_version':2,'status':'cant_do','reason':REASON})
                return {'failure':{'taskId':TASK,'status':'cant_do','source':{'number':3,'version':2}},'read_effect':'none'}
            return original(room,op,**args)
        self.client.enrolled.side_effect=implicit
        try:
            cant_do(REASON);cant_do(REASON)
            with self.assertRaises(BotSeshError):cant_do('Changed reason')
        finally:clear_session_vars(tokens)
        self.assertEqual(len([x for x in self.calls if x[1]=='fail_task']),1)
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'],'failed')
        self.assertEqual(self.posts,{})

    async def prepare(self):
        from hermes_botsesh_test.adapter import _ADAPTERS
        _ADAPTERS.clear(); _ADAPTERS.add(self.adapter)
        original = self.client.enrolled.side_effect
        def enriched(room, op, **args):
            if op == 'fail_task':
                self.calls.append((room, op, args))
                return {'failure': {'taskId':TASK, 'status':'cant_do', 'state':'offered'}, 'read_effect':'none'}
            value = original(room, op, **args)
            if op == 'read': value['batch']['messages'][0]['addressing'] = {'taskId':TASK}
            return value
        self.client.enrolled.side_effect = enriched
        await self.adapter._check_room('a')
        return set_session_vars(platform='botsesh', chat_id='a', message_id='3')

    async def test_registered_tool_failure_suppresses_final_and_never_marks_read(self):
        tokens = await self.prepare()
        try:
            ctx=Mock(); tools.register_tools(ctx)
            registered={call.kwargs['name']:call.kwargs for call in ctx.register_tool.call_args_list}
            spec=registered['botsesh_cant_do']
            self.assertEqual(spec['schema']['parameters']['required'], ['reason'])
            self.assertEqual(set(spec['schema']['parameters']['properties']), {'reason','partial_notes'})
            self.assertIn('Never for clarifying questions', spec['schema']['description'])
            result=json.loads(spec['handler']({'reason':REASON}))
            self.assertTrue(result['ok'],result)
            self.assertEqual(result['result']['read_effect'],'none')
            cant_do(REASON) # exact replay remains one remote operation
            with self.assertRaises(BotSeshError): cant_do('A changed failure reason')
        finally: clear_session_vars(tokens)
        sent=await self.adapter.send('a','I could not generate that image.',metadata={'notify':True})
        self.assertTrue(sent.success)
        await self.adapter.on_processing_complete(self.adapter.handle_message.call_args.args[0], __import__('gateway.platforms.event',fromlist=['ProcessingOutcome']).ProcessingOutcome.SUCCESS)
        row=self.client.store.room('a')['processing_receipts']['3']
        self.assertEqual(row['state'],'failed');self.assertEqual(row['failure_code'],'cant_do')
        self.assertNotIn('frozen',row);self.assertNotIn('processed_cursor',self.client.store.room('a'))
        self.assertEqual(len([x for x in self.calls if x[1]=='fail_task']),1)
        self.assertEqual(self.posts,{})

    async def test_wrong_native_target_local_session_and_missing_task_denied(self):
        tokens=await self.prepare()
        try:
            for platform,chat,msg in [('cli','a','3'),('botsesh','b','3'),('botsesh','a','5'),('botsesh','a','')]:
                set_session_vars(platform=platform,chat_id=chat,message_id=msg)
                with self.assertRaises(BotSeshError): cant_do(REASON)
            set_session_vars(platform='botsesh',chat_id='a',message_id='3')
            self.client.store.update(lambda d:d['rooms']['a']['processing_receipts']['3']['frozen']['batch']['messages'][0].pop('addressing'))
            with self.assertRaises(BotSeshError): cant_do(REASON)
        finally:clear_session_vars(tokens)
        self.assertFalse(any(x[1]=='fail_task' for x in self.calls))

    async def test_uncertain_failure_recovers_exactly_after_restart_without_inference(self):
        tokens=await self.prepare();original=self.client.enrolled.side_effect
        def lose(room,op,**args):
            result=original(room,op,**args)
            if op=='fail_task': raise BotSeshError('connection_failed_retry_same_intent')
            return result
        self.client.enrolled.side_effect=lose
        try:
            with self.assertRaises(BotSeshError):cant_do(REASON)
        finally:clear_session_vars(tokens)
        frozen=self.client.store.room('a')['processing_receipts']['3']['explicit_failure']['payload']
        self.client.enrolled.side_effect=original;self.targets['a']=[]
        restarted=self.make_adapter();restarted.handle_message=Mock()
        await restarted._check_room('a')
        restarted.handle_message.assert_not_called()
        self.assertEqual([x[2] for x in self.calls if x[1]=='fail_task'],[frozen,frozen])
        self.assertEqual(self.posts,{})
        self.assertEqual(self.client.store.room('a')['processing_receipts']['3']['state'],'failed')


def load_tests(loader, tests, pattern):
    # Reuse setup helpers without running every inherited base test twice.
    import unittest
    suite = unittest.TestSuite()
    for cls in (CantDoOAuthTests, CantDoNativeTests):
        for name in cls.__dict__:
            if name.startswith('test_'):
                suite.addTest(cls(name))
    return suite
