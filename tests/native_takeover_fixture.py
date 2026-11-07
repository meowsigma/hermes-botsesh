"""Local protocol fixture: actual native adapter/tools, scripted model actions, NO inference.
The parent supplies only a test RPC transport via stdin/stdout (no credentials).
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock

ROOT=Path(sys.argv[1]).resolve()
spec=importlib.util.spec_from_file_location('native_fixture_plugin',ROOT/'__init__.py',submodule_search_locations=[str(ROOT)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package)
from native_fixture_plugin import oauth,tools
from native_fixture_plugin.client import Client,BotSeshError
from native_fixture_plugin.storage import Store
from native_fixture_plugin.adapter import BotSeshAdapter,processed_turn,ended_turn
from gateway.config import PlatformConfig
from gateway.platform_registry import PlatformEntry,platform_registry
from gateway.platforms.event import ProcessingOutcome
from gateway.session_context import set_session_vars,clear_session_vars

initial=json.loads(sys.stdin.readline())
def rpc(self,binding,name,args=None,**kwargs):
    print(json.dumps({'kind':'rpc','name':name,'args':args or {}}),flush=True)
    response=json.loads(sys.stdin.readline())
    if 'error' in response:raise BotSeshError(response['error'],response.get('status'))
    return response['result']
oauth.OAuthClient.rpc=rpc

async def run():
    with tempfile.TemporaryDirectory(prefix='native-plugin-takeover-') as directory:
        os.environ['HERMES_HOME']=directory
        store=Store(Path(directory)/'plugin-data/botsesh')
        binding={'room_id':initial['room_id'],'url':'https://botsesh.com/b/'+initial['room_id'],'origin':'https://botsesh.com',
                 'participant_id':initial['participant_id'],'oauth_id':'fixture-transport','transport':'oauth','active':True,'name':'LOCAL FIXTURE'}
        store.update(lambda d:d['rooms'].update({initial['room_id']:binding}))
        platform_registry.register(PlatformEntry(name='botsesh',label='BotSesh',adapter_factory=BotSeshAdapter,check_fn=lambda:True))
        adapter=BotSeshAdapter(PlatformConfig());adapter.client=Client(store)
        adapter.set_authorization_check(lambda user,kind,room: user==initial['human_id'] and room==binding['room_id'])
        captured=[]
        async def admitted(event):
            event._gateway_accepted=True;captured.append(event)
        adapter.handle_message=admitted
        await adapter._check_room(binding['room_id'])
        assert len(captured)==1, ('expected exactly one admitted native turn',len(captured))
        event=captured[0];assert event.message_id==str(initial['source_number']),event.message_id
        await adapter.on_processing_start(event)
        row=store.room(binding['room_id'])['processing_receipts'][event.message_id]
        assert row['takeover_offer']['task_id']==initial['task_id']
        assert row['frozen']['takeover_offer']['task']['context']['partialNotes'][0]['text']=='LOCAL unfinished draft notes'
        assert row['frozen']['read_effect']=='none'
        ctx=Mock();tools.register_tools(ctx)
        registered={call.kwargs['name']:call.kwargs for call in ctx.register_tool.call_args_list}
        assert event.source.message_id==event.message_id
        tokens=set_session_vars(platform=event.source.platform.value,chat_id=event.source.chat_id,message_id=event.source.message_id)
        try:
            before=json.loads(registered['botsesh_cant_do']['handler']({'reason':'No image tool is available to me.'}))
            assert before['ok'] is False and before['error']=='takeover_claim_required',before
            invalid=json.loads(registered['botsesh_takeover_claim']['handler']({'reason':'LOCAL can attempt text fallback','processed':False}))
            assert invalid['ok'] is False,invalid
            claim=json.loads(registered['botsesh_takeover_claim']['handler']({'reason':'LOCAL can attempt text fallback','processed':True}))
            assert claim['ok'] is True and claim['result']['granted'] is True,claim
            duplicate=json.loads(registered['botsesh_takeover_claim']['handler']({'reason':'LOCAL can attempt text fallback','processed':True}))
            assert duplicate==claim
            # Passive polling after canonical claim must NOT release the still-running native turn.
            checked=await asyncio.to_thread(adapter.client.enrolled,binding['room_id'],'check')
            if initial.get('mode')=='cant_do':
                failure=json.loads(registered['botsesh_cant_do']['handler']({'reason':'No image tool is available to me.','partial_notes':'LOCAL backup unfinished notes'}))
                assert failure['ok'] is True,failure
        finally:clear_session_vars(tokens)
        processed_turn(platform='botsesh',user_message=adapter._current[binding['room_id']]['text'],session_id='local-scripted',turn_id='one')
        ended_turn(platform='botsesh',session_id='local-scripted',turn_id='one',completed=True,failed=False,interrupted=False)
        final=await adapter.send(binding['room_id'],'LOCAL SCRIPTED native adapter final; retained unfinished draft notes.',metadata={'notify':True})
        assert final.success,final.error
        try:
            await adapter.on_processing_complete(event,ProcessingOutcome.SUCCESS)
        except BotSeshError as error:
            assert initial.get('mode')=='cant_do' and error.code=='connection_failed_retry_same_intent',error
            # Only completion metadata is replayed. No new native event/model is admitted.
            await adapter._check_room(binding['room_id'])
            assert len(captured)==1
        row=store.room(binding['room_id'])['processing_receipts'][event.message_id]
        assert row['state']==('failed' if initial.get('mode')=='cant_do' else 'replied'),row
        assert row.get('takeover_settled') is True,row
        print(json.dumps({'kind':'done','native_target':event.message_id,'claim_granted':claim['result']['granted'],'state':row['state'],'inference_calls':0}),flush=True)
        platform_registry.unregister('botsesh')
asyncio.run(run())
