// Exact-server, zero-inference regression. Node hosts the local server only;
// the real Python adapter/OAuth/tools run with Node absent from PATH.
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes,createHash,randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname} from 'node:path';
const server=process.env.BOTSESH_SERVER_SOURCE;
const plugin=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const CLIENT='https://botsesh.com/oauth/clients/hermes.json';
test('ambient pass then canonical takeover is a distinct native work item',{skip:!server,timeout:60000},async t=>{
 const {hermesSolidFixture}=await import(pathToFileURL(resolve(server,'tests/helpers/hermes-solid-fixture.mjs')));
 const f=await hermesSolidFixture(t,{dmConsent:false}),base='/api/rooms/'+f.room.id;
 const field=(html,name)=>{const v=html.match(new RegExp('name="'+name+'" value="([^"]+)"'))?.[1];assert.ok(v,name);return v;};
 const verifier=randomBytes(32).toString('base64url'),callback='http://127.0.0.1:39418/callback';
 const query=new URLSearchParams({client_id:CLIENT,redirect_uri:callback,response_type:'code',code_challenge:createHash('sha256').update(verifier).digest('base64url'),code_challenge_method:'S256',resource:f.origin+'/mcp',scope:'botsesh:chat botsesh:rooms',state:randomBytes(32).toString('base64url'),room_hint:f.room.id});
 const initial=await f.owner.get('/oauth/authorize?'+query).set('Sec-Fetch-Mode','navigate');
 const page=initial.status===303?await f.owner.get(new URL(initial.headers.location).pathname+new URL(initial.headers.location).search):initial;
 assert.equal(page.status,200,page.text);
 const approved=await f.owner.post('/api/agent/room-consent').set('Origin',f.origin).type('form').send({consent_id:field(page.text,'consent_id'),csrf:field(page.text,'csrf'),review_id:field(page.text,'review_id'),selected_room_id:f.room.id,decision:'allow'});
 assert.equal(approved.status,302,approved.text);
 const token=await f.post(f.owner,'/oauth/token',{client_id:CLIENT,grant_type:'authorization_code',code:new URL(approved.headers.location).searchParams.get('code'),redirect_uri:callback,resource:f.origin+'/mcp',code_verifier:verifier});assert.equal(token.status,200,token.text);
 const backup=f.db.prepare('SELECT * FROM agent_memberships WHERE room_id=? AND id<>?').get(f.room.id,f.membership.id);assert.ok(backup);
 f.db.prepare('UPDATE participant_checks SET name=? WHERE id=?').run('Muse',backup.participant_id);
 f.db.prepare('UPDATE agent_memberships SET display_name=? WHERE id=?').run('Muse',backup.id);
 const second=async(name,args={})=>{const r=await f.rawRpc(name,args,token.body.access_token);assert.equal(r.status,200,r.text);assert.ok(!r.body.result?.isError,JSON.stringify({name,args,response:r.body}));let value=r.body.result.structuredContent;if(name!=='read_tool_page'&&value.next_cursor){let cursor=value.next_cursor;const fragments=[];while(cursor){const p=await second('read_tool_page',{room_id:args.room_id,cursor});fragments.push(p.json_fragment);cursor=p.next_cursor;}value=JSON.parse(fragments.join(''));}return value;};
 const send=async text=>{const r=await f.post(f.owner,base+'/messages',{clientId:randomUUID(),text});assert.equal(r.status,201,r.text);return r.body.message;};
 for(const [name,rpc] of [['Hermes',f.rpc],['Muse',second]]){
  await rpc('hermes_listener',{room_id:f.room.id,state:'listening'});
  const q=await send('@'+name+' Suggest a preparation.');
  await rpc('hermes_delivery',{room_id:f.room.id,number:q.number,state:'received'});f.advance(1);
  await rpc('hermes_delivery',{room_id:f.room.id,number:q.number,state:'started'});f.advance(1);
  const b=await rpc('catchup',{room_id:f.room.id,target_number:q.number,max_chars:60000});
  await rpc('send_message',{room_id:f.room.id,text:'LOCAL scripted preparation.',reply_to:q.number,batch_id:b.batch_id,client_id:randomUUID()});
  for(const old of (await rpc('hermes_inbox',{room_id:f.room.id})).targets){const b=await rpc('catchup',{room_id:f.room.id,target_number:old.number,max_chars:60000});await rpc('send_message',{room_id:f.room.id,text:'LOCAL scripted startup receipt.',reply_to:old.number,batch_id:b.batch_id,client_id:randomUUID()});}
 }
 const cfg=await f.owner.put(base+'/wake/takeover').set('Origin',f.origin).set('X-Board-Request','1').send({enabled:true,order:[f.membership.participant_id,backup.participant_id]});assert.equal(cfg.status,200,cfg.text);assert.equal(cfg.body.effectiveEnabled,true);
 f.advance(10001);
 const q=await send('@Hermes Generate an image; backup may use text only.');
 const batch=await f.rpc('catchup',{room_id:f.room.id,target_number:q.number,max_chars:60000});
 const task=f.db.prepare('SELECT * FROM smart_wake_tasks WHERE room_id=? AND message_id=?').get(f.room.id,q.id);assert.ok(task);
 const human=f.db.prepare('SELECT id FROM participant_checks WHERE room_id=? AND session_hash=(SELECT sender_session_hash FROM messages WHERE id=?)').get(f.room.id,q.id).id;
 const child=spawn(process.env.PYTHON||'python3',[resolve(plugin,'tests/takeover_after_pass_fixture.py'),plugin],{env:{...process.env,PATH:''},stdio:['pipe','pipe','pipe']});
 t.after(()=>{if(child.exitCode===null)child.kill();});
 const errors=[];child.stderr.on('data',x=>errors.push(String(x)));
 let done,chain=Promise.resolve(),offerId,claims=0,accepts=0,harnessError;
 createInterface({input:child.stdout}).on('line',line=>{chain=chain.then(async()=>{
  const msg=JSON.parse(line);if(msg.kind==='done'){done=msg;return;}
  try{
   let result;
   if(msg.kind==='primary_failure'){
    const delivery=f.db.prepare('SELECT * FROM agent_message_deliveries WHERE membership_id=? AND message_id=?').get(backup.id,q.id);
    assert.ok(delivery.passed_at);assert.ok(delivery.completed_at);assert.equal(delivery.answered_at,null);
    result=await f.rpc('finish_room_turn',{room_id:f.room.id,batch_id:batch.batch_id,client_id:randomUUID(),output:{failure:{task_id:task.id,status:'cant_do',reason:'No image tool is available.',partial_notes:'LOCAL unfinished draft notes'}}});
    assert.equal(result.failure.state,'offered');f.advance(10001);
    const inbox=await second('hermes_inbox',{room_id:f.room.id});assert.equal(inbox.offers.length,1,JSON.stringify(inbox));offerId=inbox.offers[0].event_id;assert.equal(inbox.offers[0].number,q.number);assert.equal(inbox.offers[0].retry_after_ms,0);
    const foreign=await f.rawRpc('hermes_takeover',{room_id:f.room.id,action:'accept',event_id:offerId});assert.equal(foreign.body.result?.isError,true);
   }else{
    assert.equal(msg.kind,'rpc');
    if(msg.name==='hermes_takeover'&&msg.args.action==='accept')accepts++;
    if(msg.name==='hermes_takeover'&&msg.args.action==='claim')claims++;
    result=await second(msg.name,msg.args);
    if(msg.name==='hermes_takeover'&&msg.args.action==='accept'&&result.inference_authorized){
     const denied=await f.rawRpc('hermes_takeover',{room_id:f.room.id,action:'claim',event_id:offerId,batch_id:randomUUID(),reason:'Unread foreign receipt',processed:true},token.body.access_token);assert.equal(denied.body.result?.isError,true);
     const duplicate=await second('hermes_takeover',{room_id:f.room.id,action:'accept',event_id:offerId});assert.equal(duplicate.inference_authorized,false);
    }
   }
   child.stdin.write(JSON.stringify({result})+'\n');
  }catch(e){harnessError??=e;child.stdin.write(JSON.stringify({error:'fixture_rpc_failed',status:400})+'\n');}
 });});
 child.stdin.write(JSON.stringify({room_id:f.room.id,participant_id:backup.participant_id,human_id:human,source_number:q.number,task_id:task.id})+'\n');
 const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve);});await chain;assert.ifError(harnessError);
 assert.equal(code,0,errors.join(''));assert.ok(done,errors.join(''));assert.equal(done.claim_granted,true);assert.equal(done.ambient_duplicates_deduped,true);assert.equal(done.node_available,false);
 assert.equal(accepts,1);assert.equal(claims,1);
 const final=f.db.prepare('SELECT * FROM smart_wake_tasks WHERE id=?').get(task.id);assert.equal(final.state,'answered');assert.equal(final.winner_id,backup.participant_id);
 assert.equal(f.db.prepare('SELECT COUNT(*) n FROM wake_model_admissions WHERE event_id=?').get(offerId).n,1);
 assert.equal(f.db.prepare('SELECT state FROM hermes_takeover_turns WHERE event_id=?').get(offerId).state,'settled');
 console.log(JSON.stringify({boundary:'exact server main; scripted native execution; no model calls',...done,offer_id:offerId,accepts,claims,shared_offer_admissions:1,foreign_offer_denied:true,foreign_receipt_denied:true}));
});
