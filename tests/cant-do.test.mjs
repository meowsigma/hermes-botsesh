// Offline doubles, not a real model or production takeover proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {BotSeshAgent,SecretStore,parseRoomTurnOutput,completeRoomTurn} from '../vendor/botsesh-agent.mjs';
import {runRequest} from '../bridge.mjs';
const task='11111111-1111-4111-8111-111111111111';
const failure={task_id:task,status:'cant_do',reason:'No image tool is available to me.',partial_notes:'Unfinished local fixture draft.'};

test('strict failure parser accepts exact cant_do and rejects mixed/unsafe output',()=>{
 assert.deepEqual(parseRoomTurnOutput({failure}),{failure});
 for(const value of [{failure,reply:'pretend success'},{failure,status:'working'},{failure:{...failure,task_id:'not-a-task'}},{failure:{...failure,reason:'two\nlines'}},{failure:{...failure,reason:'\u200bhidden'}},{failure:{...failure,status:'clarify'}},{failure:{...failure,reason:'x'.repeat(241)}}])assert.throws(()=>parseRoomTurnOutput(value),{code:'invalid_runtime_answer'});
});

test('structured completion journals failure identity without send/read/purpose effects',async()=>{
 const calls=[],writes=[];const client={state:{pendingRead:{printed:true,through:99}},store:{write:async value=>writes.push(structuredClone(value))},reportTaskFailure:async(id,body)=>{calls.push({id,body});return {taskId:id,status:body.status};}};
 const job={failure,eventId:randomUUID(),readBoundary:{through:999,printed:true}};
 assert.equal(await completeRoomTurn(client,job),null);
 assert.equal(calls.length,1);assert.equal(calls[0].body.clientId,job.eventId);
 assert.deepEqual(client.state.pendingRead,{printed:true,through:99});
 assert.equal(await completeRoomTurn(client,client.state.localJob),null);assert.equal(calls.length,1);assert.ok(writes.length>=2);
});

test('bridge typed failure preserves receipt/task/id, exact replay and revoked denial',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'cant-do-'));t.after(()=>rm(directory,{recursive:true,force:true}));
 const room='https://botsesh.com/b/offline',origin='https://botsesh.com';
 const participant=randomUUID(),membership={id:randomUUID(),participantId:participant,displayName:'Offline native',expiresAt:Date.now()+3600000};
 const client=new BotSeshAgent({storeDirectory:directory,roomUrl:room,secretStore:new SecretStore({directory,mode:'file',notice:()=>{}})});
 await client.store.write({version:1,origin,roomId:'offline',participantId:participant,accessToken:'bsa_'+'a'.repeat(43),membership,readCheckpoint:0});
 const receipt=randomUUID();await client.store.write({version:1,origin,roomId:'plugin-receipt-'+receipt,boundRoom:'offline',participantId:participant,createdAt:Date.now(),batch:{messages:[{number:3,addressing:{taskId:task}}]}});
 const requests=[];let revoked=false;
 const fetchImpl=async(url,options)=>{const path=new URL(url).pathname;requests.push({path,method:options.method,body:options.body?JSON.parse(options.body):null});
  if(revoked)return Response.json({code:'membership_revoked'},{status:403});
  if(path.endsWith('/agent-access'))return Response.json({membership,participantId:participant,readCheckpoint:0,accessExpiresAt:membership.expiresAt});
  if(path.endsWith('/wake/tasks/'+task+'/failure'))return Response.json({taskId:task,status:'cant_do',state:'offered',winnerId:null});
  throw Error('Unexpected effect '+path);
 };
 const req={op:'fail_task',room,client_id:randomUUID(),receipt_id:receipt,failure};
 const first=await runRequest(req,directory,{fetchImpl});assert.equal(first.read_effect,'none');assert.equal(first.failure.status,'cant_do');
 assert.deepEqual(await runRequest(req,directory,{fetchImpl}),first);
 assert.equal(requests.filter(r=>r.method==='POST').length,1);
 assert.deepEqual(requests.find(r=>r.method==='POST').body,{clientId:req.client_id,status:'cant_do',reason:failure.reason,partialNotes:failure.partial_notes});
 await assert.rejects(runRequest({...req,failure:{...failure,reason:'changed'}},directory,{fetchImpl}),{code:'pending_send_mismatch'});
 await assert.rejects(runRequest({...req,receipt_id:randomUUID()},directory,{fetchImpl}),{code:'receipt_scope'});
 assert.equal((await client.store.read(origin,'offline')).readCheckpoint,0);
 revoked=true;await assert.rejects(runRequest(req,directory,{fetchImpl}),{status:403});
 assert.ok(requests.every(r=>r.path.endsWith('/agent-access')||r.path.endsWith('/failure')));
});
