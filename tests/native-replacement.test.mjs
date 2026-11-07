// Local executable replacement regression. Actual native adapter/tool code with
// scripted model actions over a local HTTP fixture. NO inference/production proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,randomUUID} from 'node:crypto';
import {writeFileSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname} from 'node:path';
const server=process.env.BOTSESH_SERVER_SOURCE;
const plugin=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const CLIENT='https://botsesh.com/oauth/clients/hermes.json';
for(const mode of ['reply','cant_do'])test('native replacement receives + explicitly claims + '+mode,{skip:!server,timeout:60000},async t=>{
 const {hermesSolidFixture}=await import(pathToFileURL(resolve(server,'tests/helpers/hermes-solid-fixture.mjs')));
 const f=await hermesSolidFixture(t,{dmConsent:false}),base='/api/rooms/'+f.room.id;
 const field=(html,name)=>{const value=html.match(new RegExp('name="'+name+'" value="([^"]+)"'))?.[1];assert.ok(value,name);return value;};
 const verifier=randomBytes(32).toString('base64url'),callback='http://127.0.0.1:39418/callback';
 const query=new URLSearchParams({client_id:CLIENT,redirect_uri:callback,response_type:'code',code_challenge:createHash('sha256').update(verifier).digest('base64url'),code_challenge_method:'S256',resource:f.origin+'/mcp',scope:'botsesh:chat botsesh:rooms',state:randomBytes(32).toString('base64url'),room_hint:f.room.id});
 const initial=await f.owner.get('/oauth/authorize?'+query).set('Sec-Fetch-Mode','navigate');
 const page=initial.status===303?await f.owner.get(new URL(initial.headers.location).pathname+new URL(initial.headers.location).search):initial;
 assert.equal(page.status,200,page.text);
 const approved=await f.owner.post('/api/agent/room-consent').set('Origin',f.origin).type('form').send({consent_id:field(page.text,'consent_id'),csrf:field(page.text,'csrf'),review_id:field(page.text,'review_id'),selected_room_id:f.room.id,decision:'allow'});
 assert.equal(approved.status,302,approved.text);
 const token=await f.post(f.owner,'/oauth/token',{client_id:CLIENT,grant_type:'authorization_code',code:new URL(approved.headers.location).searchParams.get('code'),redirect_uri:callback,resource:f.origin+'/mcp',code_verifier:verifier});assert.equal(token.status,200,token.text);
 const m2=f.db.prepare('SELECT * FROM agent_memberships WHERE room_id=? AND id<>?').get(f.room.id,f.membership.id);assert.ok(m2);
 f.db.prepare('UPDATE participant_checks SET name=? WHERE id=?').run('Muse',m2.participant_id);
 f.db.prepare('UPDATE agent_memberships SET display_name=? WHERE id=?').run('Muse',m2.id);
 const second=async(name,args={})=>{const r=await f.rawRpc(name,args,token.body.access_token);assert.equal(r.status,200,r.text);assert.ok(!r.body.result?.isError,JSON.stringify(r.body));let value=r.body.result.structuredContent;if(name!=='read_tool_page'&&value.next_cursor){const fragments=[];let cursor=value.next_cursor;while(cursor){const p=await second('read_tool_page',{room_id:args.room_id,cursor});fragments.push(p.json_fragment);cursor=p.next_cursor;}value=JSON.parse(fragments.join(''));}return value;};
 const send=async text=>{const r=await f.post(f.owner,base+'/messages',{clientId:randomUUID(),text});assert.equal(r.status,201,r.text);return r.body.message;};
 for(const [name,rpc] of [['Hermes',f.rpc],['Muse',second]]){
  await rpc('hermes_listener',{room_id:f.room.id,state:'listening'});
  const q=await send('@'+name+' Suggest one useful plant-swap preparation.');
  await rpc('hermes_delivery',{room_id:f.room.id,number:q.number,state:'received'});f.advance(1);
  await rpc('hermes_delivery',{room_id:f.room.id,number:q.number,state:'started'});f.advance(1);
  const batch=await rpc('catchup',{room_id:f.room.id,target_number:q.number,max_chars:60000});
  await rpc('send_message',{room_id:f.room.id,text:'SCRIPTED LOCAL FIXTURE: label plants with care needs.',reply_to:q.number,batch_id:batch.batch_id,client_id:randomUUID()});
  // Drain any fixture invitation/startup input through the same authenticated send.
  const inbox=await rpc('hermes_inbox',{room_id:f.room.id});
  for(const old of inbox.targets){const b=await rpc('catchup',{room_id:f.room.id,target_number:old.number,max_chars:60000});await rpc('send_message',{room_id:f.room.id,text:'LOCAL scripted startup receipt.',reply_to:old.number,batch_id:b.batch_id,client_id:randomUUID()});}
 }
 const cfg=await f.owner.put(base+'/wake/takeover').set('Origin',f.origin).set('X-Board-Request','1').send({enabled:true,order:[f.membership.participant_id,m2.participant_id]});assert.equal(cfg.status,200,cfg.text);assert.equal(cfg.body.effectiveEnabled,true);
 const q=await send('@Hermes Generate an image for the plant swap; backup may use text only.');
 const batch=await f.rpc('catchup',{room_id:f.room.id,target_number:q.number,max_chars:60000});
 const task=f.db.prepare('SELECT * FROM smart_wake_tasks WHERE room_id=? AND message_id=?').get(f.room.id,q.id);assert.ok(task);
 const before=f.db.prepare('SELECT read_checkpoint FROM participant_checks WHERE id=?').get(f.membership.participant_id).read_checkpoint;
 const failure=await f.rpc('finish_room_turn',{room_id:f.room.id,batch_id:batch.batch_id,client_id:randomUUID(),output:{failure:{task_id:task.id,status:'cant_do',reason:'No image tool is available to me.',partial_notes:'LOCAL unfinished draft notes'}}});
 assert.equal(failure.read_effect,'none');assert.equal(failure.failure.state,'offered');
 assert.equal(f.db.prepare('SELECT read_checkpoint FROM participant_checks WHERE id=?').get(f.membership.participant_id).read_checkpoint,before);
 const inbox=await second('hermes_inbox',{room_id:f.room.id});assert.equal(inbox.offers.length,1,JSON.stringify(inbox));
 const event=inbox.offers[0];assert.equal(event.number,q.number);
 const foreign=await f.rawRpc('hermes_takeover',{room_id:f.room.id,action:'accept',event_id:event.event_id});
 assert.equal(foreign.body.result?.isError,true,'another participant cannot accept Muse’s offer');
 // Shared 10-second floor is honored without consuming/inventing an admission.
 const execution=await import(pathToFileURL(resolve(server,'wake-execution.js')));
 const fences=execution.createWakeExecution(f.db,()=>f.time);
 const prior={event_id:'evt_'+randomUUID(),recipient_id:m2.participant_id,room_id:f.room.id};
 f.db.prepare('INSERT INTO wake_model_admissions VALUES(?,?,?,?,?)').run(prior.event_id,1,prior.room_id,prior.recipient_id,f.time);
 const floor=await second('hermes_takeover',{room_id:f.room.id,action:'accept',event_id:event.event_id});
 assert.equal(floor.accepted,false);assert.equal(floor.inference_authorized,false);assert.equal(floor.retry_after_ms,10000);
 assert.equal(f.db.prepare('SELECT 1 FROM hermes_takeover_turns WHERE event_id=?').get(event.event_id),undefined);
 f.advance(10001);
 const human=f.db.prepare('SELECT id FROM participant_checks WHERE room_id=? AND session_hash=(SELECT sender_session_hash FROM messages WHERE id=?)').get(f.room.id,q.id).id;
 const child=spawn(process.env.PYTHON||'python3',[resolve(plugin,'tests/native_takeover_fixture.py'),plugin],{stdio:['pipe','pipe','pipe']});
 t.after(()=>{if(child.exitCode===null)child.kill();});
 const errors=[];child.stderr.on('data',x=>errors.push(String(x)));
 let done,chain=Promise.resolve(),settlementResponseLost=false;
 const lines=createInterface({input:child.stdout});
 lines.on('line',line=>{chain=chain.then(async()=>{const msg=JSON.parse(line);if(msg.kind==='done'){done=msg;return;}assert.equal(msg.kind,'rpc');
  try{const result=await second(msg.name,msg.args);
   if(msg.name==='hermes_takeover'&&msg.args.action==='accept'&&result.inference_authorized){
    const unread=await f.rawRpc('hermes_takeover',{room_id:f.room.id,action:'claim',event_id:event.event_id,batch_id:randomUUID(),reason:'LOCAL unread attempt',processed:true},token.body.access_token);
    assert.equal(unread.body.result?.isError,true,'admission/fetch is not source read');
    const duplicate=await second('hermes_takeover',{room_id:f.room.id,action:'accept',event_id:event.event_id});assert.equal(duplicate.inference_authorized,false);
   }
   if(msg.name==='hermes_inbox'&&f.db.prepare('SELECT state FROM hermes_takeover_turns WHERE event_id=?').get(event.event_id)?.state==='claimed')assert.equal(fences.current(m2.participant_id)?.event_id,event.event_id,'passive check cannot settle the running native turn');
   if(mode==='cant_do'&&msg.name==='hermes_takeover'&&msg.args.action==='settle'&&!settlementResponseLost){settlementResponseLost=true;child.stdin.write(JSON.stringify({error:'connection_failed_retry_same_intent'})+'\n');return;}
   child.stdin.write(JSON.stringify({result})+'\n');}
  catch(error){child.stdin.write(JSON.stringify({error:'fixture_rpc_failed',status:400})+'\n');throw error;}
 });});
 child.stdin.write(JSON.stringify({room_id:f.room.id,participant_id:m2.participant_id,human_id:human,source_number:q.number,task_id:task.id,mode})+'\n');
 const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve);});await chain;
 assert.equal(code,0,errors.join(''));assert.equal(done?.claim_granted,true);assert.equal(done.native_target,String(q.number));
 const delivery=f.db.prepare('SELECT * FROM agent_message_deliveries WHERE membership_id=? AND message_id=?').get(m2.id,q.id);assert.ok(delivery);
 const claim=f.db.prepare('SELECT * FROM smart_wake_offers WHERE task_id=? AND recipient_id=?').get(task.id,m2.participant_id);assert.ok(claim.decided_at);
 const updated=f.db.prepare('SELECT * FROM smart_wake_tasks WHERE id=?').get(task.id);
 assert.equal(updated.winner_id,m2.participant_id);assert.equal(updated.state,mode==='reply'?'answered':'claimed');
 const turns=f.db.prepare('SELECT * FROM hermes_takeover_turns WHERE event_id=?').get(event.event_id);assert.equal(turns.state,'settled');
 assert.equal(fences.current(m2.participant_id),undefined);
 assert.equal(f.db.prepare('SELECT COUNT(*) n FROM wake_model_admissions WHERE event_id=?').get(event.event_id).n,1);
 const revoked=await f.post(f.owner,'/oauth/revoke',{client_id:CLIENT,token:token.body.refresh_token});assert.equal(revoked.status,200);
 const denied=await f.rawRpc('hermes_takeover',{room_id:f.room.id,action:'accept',event_id:event.event_id},token.body.access_token);assert.equal(denied.status,401);
 const source=await f.owner.get(base+'/messages/by-number/'+q.number);assert.equal(source.status,200);
 const receipt={boundary:'LOCAL scripted native adapter/tool + real server fixture; NOT inference or production proof',mode,backupNativeTargetPresent:true,claimGranted:true,nativeResult:done,delivery:{received:delivery.received_at!==null,started:delivery.started_at!==null,answered:delivery.answered_at!==null,failed:delivery.failed_at!==null},sharedAdmissionCount:1,floorHonored:true,negativeChecks:['foreign-offer denied','unread claim denied','duplicate accept cannot authorize inference','passive check preserves active native slot','revoked token401'],trail:source.body.message.addressing.takeover.trail,upstreamCalls:0};
 if(process.env.NATIVE_EVIDENCE_DIR)writeFileSync(resolve(process.env.NATIVE_EVIDENCE_DIR,'native-replacement-'+mode+'.json'),JSON.stringify(receipt,null,2)+'\n');
 console.log(JSON.stringify(receipt));
});
