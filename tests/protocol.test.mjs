// OFFLINE protocol doubles. Never evidence of live service/agent behavior.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {BotSeshAgent,parseRoomTurnOutput,ApiError} from '../vendor/botsesh-agent.mjs';

async function clientFixture(t, pages) {
  const directory=await mkdtemp(join(tmpdir(),'hack1-unit-'));t.after(()=>rm(directory,{recursive:true,force:true}));
  const requests=[];
  const membership={id:'5c1cfd9c-fdbe-4ef4-9f61-30a6561f41d9',participantId:'c3549c36-e39a-44a4-9b09-2c89483b86c0',expiresAt:Date.now()+3600000,displayName:'Offline test'};
  const fetchImpl=async(url,options)=>{
    requests.push({url,options});
    const path=new URL(url).pathname;
    if(path.endsWith('/agent-access'))return Response.json({membership,participantId:membership.participantId,readCheckpoint:0,accessExpiresAt:membership.expiresAt});
    if(path.endsWith('/catch_up'))return Response.json(pages.shift());
    throw new Error('Unexpected offline request: '+path);
  };
  const client=new BotSeshAgent({storeDirectory:directory,roomUrl:'https://botsesh.com/b/offline',fetchImpl});
  client.state={version:1,origin:'https://botsesh.com',roomId:'offline',accessToken:'bsa_'+'a'.repeat(43),membership,participantId:membership.participantId,readCheckpoint:0};
  await client.store.write(client.state);
  return {client,requests};
}

test('plain catch_up follows all continuations and never POSTs acknowledgment',async t=>{
  const {client,requests}=await clientFixture(t,[{schema_version:1,room_id:'offline',text:'first exact page',complete:false,next_page:'opaque',read_effect:'none'},{schema_version:1,room_id:'offline',text:'second exact page',complete:true,next_page:null,read_effect:'none'}]);
  assert.deepEqual(await client.pluginCatchUp(),['first exact page','second exact page']);
  assert.match(requests.at(-1).url,/\?page=opaque$/);
  assert(requests.every(r=>r.options.method==='GET'));
  assert.equal(client.state.readCheckpoint,0);
});

test('malformed/cross-room/repeated paging fails closed',async t=>{
  for(const response of [{schema_version:1,room_id:'foreign',text:'x',complete:true,next_page:null,read_effect:'none'},{schema_version:1,room_id:'offline',text:'x',complete:false,next_page:null,read_effect:'none'},{schema_version:1,room_id:'offline',text:'x',complete:true,next_page:'unexpected',read_effect:'none'}]){
    const {client,requests}=await clientFixture(t,[response]);
    await assert.rejects(client.pluginCatchUp(),{code:'invalid_catch_up'});
    assert(requests.every(r=>r.options.method==='GET'));
  }
});

test('structured completion recognizes draft_ready and exact routine_report without approvals',()=>{
  const output={reply:'Exact retained reply.',draft_ready:{title:'Draft for human review',recipientId:'7f5081ed-bb74-4304-9fb7-12bd396831f7',blockers:[]},routine_report:{routineId:'29fdcdbb-ae7e-426e-8e58-e0d67cba0709',revision:1,stepIndex:0,scheduledFor:1000,status:'completed'}};
  assert.deepEqual(parseRoomTurnOutput(output),output);
  assert.throws(()=>parseRoomTurnOutput({...output,accept:true}),{code:'invalid_runtime_answer'});
  assert.throws(()=>parseRoomTurnOutput({...output,routine_report:{...output.routine_report,messageNumber:42}}),{code:'invalid_runtime_answer'});
});

test('canonical errors classify terminal authority loss, without tokens',()=>{
  for(const [status,code] of [[401,'authorization_expired'],[410,'room_expired'],[403,'membership_revoked']]){
    const error=new ApiError(status,code);
    assert.equal(error.status,status);assert.equal(error.retryable,false);
    assert.match(error.message,/Stop retrying/);
    assert.doesNotMatch(error.message,/bsa_/);
  }
});
