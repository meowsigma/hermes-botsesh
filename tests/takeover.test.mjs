// OFFLINE bridge + durable real store, canonical reportTaskFailure method seam.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {BotSeshAgent,SecretStore} from '../vendor/botsesh-agent.mjs';
import {runRequest} from '../bridge.mjs';
import {seed,load,room,participant} from './partial-fixture.mjs';
const task='9bac2041-781b-43b5-9fe6-7d1581f239aa', other='b80ccddf-dc40-48fe-8e49-42a0502b76bc';
const receipt='8dd68172-cacf-4f43-8ee0-fdfaf88f6079', id='98304035-ef02-45c2-a57a-096726bf0e83';
async function fixture(t){
  const dir=await mkdtemp(join(tmpdir(),'takeover-plugin-'));t.after(()=>rm(dir,{recursive:true,force:true}));
  await seed(dir);
  const client=new BotSeshAgent({storeDirectory:dir,roomUrl:room,secretStore:new SecretStore({directory:dir,mode:'file',notice:()=>{}})});
  await client.store.write({version:1,origin:'https://botsesh.com',roomId:'plugin-receipt-'+receipt,boundRoom:'offline',participantId:participant,createdAt:Date.now(),targetNumber:7,batch:{messages:[{number:7,addressing:{taskId:task}},{number:10,addressing:{taskId:other}}]}});
  const requests=[];
  const membership=(await load(dir)).membership;
  const fetchImpl=async(url,options)=>{
    requests.push({path:new URL(url).pathname,method:options.method});
    if(new URL(url).pathname.endsWith('/agent-access'))return Response.json({membership,participantId:participant,readCheckpoint:0,accessExpiresAt:membership.expiresAt});
    throw new Error('Unexpected request '+url);
  };
  return {dir,client,requests,fetchImpl};
}
test('failure journals exact selected task before reporting; restart retry has no send/ack',async t=>{
  const f=await fixture(t),attempts=[];
  const original=BotSeshAgent.prototype.reportTaskFailure;
  t.after(()=>{if(original)BotSeshAgent.prototype.reportTaskFailure=original;else delete BotSeshAgent.prototype.reportTaskFailure;});
  BotSeshAgent.prototype.reportTaskFailure=async function(taskId,payload){
    const saved=await this.store.read('https://botsesh.com','plugin-post-'+id);
    assert(saved?.exact,'Exact output is journaled before the outbound report');
    attempts.push({taskId,payload});
    if(attempts.length===1)throw Object.assign(new Error('lost response'),{code:'network_error'});
    return {applied:true};
  };
  const req={op:'fail',room,receipt_id:receipt,client_id:id,failure:{task_id:task,status:'cant_do',reason:'This requires physical access.',partial_notes:'Checked the supplied instructions.\nNo changes made.'}};
  await assert.rejects(runRequest(req,f.dir,{fetchImpl:f.fetchImpl}),{code:'network_error'});
  const pending=await runRequest({op:'pending',room},f.dir,{fetchImpl:f.fetchImpl});
  assert.equal(pending.pending.failure,true);assert.equal(pending.pending.read_effect,'none');
  await f.client.store.remove('https://botsesh.com','plugin-receipt-'+receipt); // Exact journal owns retry even after frozen context cleanup.
  const result=await runRequest({op:'retry',room,client_id:id},f.dir,{fetchImpl:f.fetchImpl});
  assert.deepEqual(attempts[0],attempts[1]);
  assert.deepEqual(attempts[0],{taskId:task,payload:{clientId:id,status:'cant_do',reason:req.failure.reason,partialNotes:req.failure.partial_notes}});
  assert.equal(result.read_effect,'none');assert.equal(result.failure,true);assert.equal(result.target_number,7);
  assert.equal((await runRequest({op:'pending',room},f.dir,{fetchImpl:f.fetchImpl})).pending,null);
  await assert.rejects(runRequest({...req,failure:{...req.failure,reason:'changed'}},f.dir,{fetchImpl:f.fetchImpl}),{code:'pending_send_mismatch'});
  assert(f.requests.every(r=>r.method==='GET'));assert(!f.requests.some(r=>r.path.endsWith('/ack')));
  const state=await f.client.store.read('https://botsesh.com','offline');assert.equal(state.readCheckpoint,0);
});
test('failure rejects foreign task, reply mixing and invalid reason before reporting',async t=>{
  const f=await fixture(t),base={op:'fail',room,receipt_id:receipt,client_id:id,failure:{task_id:task,status:'cant_do',reason:'Cannot access hardware.'}};
  for(const req of [{...base,failure:{...base.failure,task_id:other}}, {...base,text:'also reply'}, {...base,processed:true}, {...base,failure:{...base.failure,reason:'two\nlines'}}, {...base,failure:{...base.failure,reason:'x'.repeat(241)}}, {...base,failure:{...base.failure,partial_notes:'x'.repeat(4001)}}, {...base,failure:{...base.failure,accept:true}}]){
    await assert.rejects(runRequest(req,f.dir,{fetchImpl:f.fetchImpl}));
  }
  assert(!await f.client.store.read('https://botsesh.com','plugin-post-'+id));
});
