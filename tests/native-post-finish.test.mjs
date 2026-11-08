// Real local server + native plugin; scripted retained Luna sequence, NO inference.
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname} from 'node:path';
const server=process.env.BOTSESH_SERVER_SOURCE;
const plugin=resolve(dirname(fileURLToPath(import.meta.url)),'..');
for(const loseResponse of [false,true])test('unthreaded processed tool post settles native delivery; lost response='+loseResponse,{skip:!server,timeout:60000},async t=>{
 const {hermesSolidFixture}=await import(pathToFileURL(resolve(server,'tests/helpers/hermes-solid-fixture.mjs')));
 const f=await hermesSolidFixture(t,{dmConsent:false}),room_id=f.room.id;
 const window={addEventListener(){}};
 vm.runInNewContext(readFileSync(resolve(server,'public/agent-evidence.js'),'utf8'),{window,document:{addEventListener(){}},Date,performance,setTimeout,clearTimeout,setInterval,clearInterval,AbortController,console});
 const face=async()=>{const r=await f.owner.get('/api/rooms/'+room_id+'/messages/by-number/'+q.body.message.number);assert.equal(r.status,200,r.text);return window.BoardAgentEvidence.questionState(r.body.message,f.membership.participant_id,{},f.time);};
 const name=f.membership.display_name;
 const q=await f.post(f.owner,'/api/rooms/'+room_id+'/messages',{clientId:randomUUID(),text:'@'+name+" Greet us once using botsesh_read then botsesh_post: 'Hello, plant-swap crew!' After the tool returns, finish with that same greeting."});
 assert.equal(q.status,201,q.text);
 const human=f.db.prepare('SELECT id FROM participant_checks WHERE room_id=? AND session_hash=(SELECT sender_session_hash FROM messages WHERE id=?)').get(room_id,q.body.message.id).id;
 const before=f.db.prepare('SELECT read_checkpoint FROM participant_checks WHERE id=?').get(f.membership.participant_id).read_checkpoint;
 const child=spawn(process.env.PYTHON||'python3',[resolve(plugin,'tests/native_post_finish_fixture.py'),plugin],{stdio:['pipe','pipe','pipe']});
 t.after(()=>{if(child.exitCode===null)child.kill();});
 const errors=[],requests=[];child.stderr.on('data',x=>errors.push(String(x)));
 let done,lost=false,chain=Promise.resolve();
 createInterface({input:child.stdout}).on('line',line=>{chain=chain.then(async()=>{
  const msg=JSON.parse(line);if(msg.kind==='done'){done=msg;return;}
  assert.equal(msg.kind,'rpc');requests.push(msg);
  assert.ok(!(msg.name==='hermes_delivery'&&msg.args.state==='handled'),'group inputs must never use DM-only handled state');
  try{
   const result=await f.rpc(msg.name,msg.args);
   if(msg.name==='working')assert.equal((await face()).state,'working','exercise genuine working face before settlement');
   if(msg.name==='catchup')assert.equal(f.db.prepare('SELECT read_checkpoint FROM participant_checks WHERE id=?').get(f.membership.participant_id).read_checkpoint,before,'fetch is not read attestation');
   if(msg.name==='send_message'&&loseResponse&&!lost){lost=true;child.stdin.write(JSON.stringify({error:'connection_failed_retry_same_intent'})+'\n');return;}
   child.stdin.write(JSON.stringify({result})+'\n');
  }catch(error){child.stdin.write(JSON.stringify({error:'fixture_rpc_failed',status:400})+'\n');throw error;}
 });});
 child.stdin.write(JSON.stringify({room_id,participant_id:f.membership.participant_id,human_id:human,source_number:q.body.message.number,lose_response:loseResponse})+'\n');
 const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve);});await chain;
 assert.equal(code,0,errors.join(''));assert.equal(done?.backlog,0);
 const delivery=f.db.prepare('SELECT * FROM agent_message_deliveries WHERE message_id=? AND membership_id=?').get(q.body.message.id,f.membership.id);
 assert.ok(delivery.received_at!==null&&delivery.started_at!==null&&delivery.answered_at!==null);
 assert.equal(delivery.handled_at,null);assert.equal(delivery.failed_at,null);
 const replies=f.db.prepare('SELECT * FROM messages WHERE room_id=? AND sender_session_hash=?').all(room_id,f.membership.actor_hash);
 assert.equal(replies.length,1,'exactly one visible post, never a settlement message');
 assert.equal(replies[0].text,'Hello, plant-swap crew!');assert.equal(replies[0].reply_to,q.body.message.id);
 assert.equal(delivery.answer_message_id,replies[0].id);
 const sends=requests.filter(r=>r.name==='send_message');assert.equal(sends.length,loseResponse?2:1);
 assert.equal(new Set(sends.map(r=>r.args.client_id)).size,1,'uncertain retries retain UUID');
 assert.equal(new Set(sends.map(r=>JSON.stringify(r.args))).size,1,'uncertain retries retain all wire fields');
 assert.equal(sends[0].args.reply_to,q.body.message.number);
 const inbox=await f.rpc('hermes_inbox',{room_id});assert.equal(inbox.backlog,0);
 const finalFace=await face();assert.equal(finalFace.state,'replied');assert.equal(finalFace.active,false);
 console.log(JSON.stringify({boundary:'LOCAL real server + scripted native adapter/tools, NOT model/production proof',lostResponse:loseResponse,visiblePosts:replies.length,delivery:'answered',backlog:inbox.backlog,uiFace:finalFace.state,inferenceCalls:0}));
});
