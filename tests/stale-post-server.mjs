// Owned loopback OAuth fixture. JSON control protocol; ZERO model calls.
import {createServer} from 'node:http';
import {createInterface} from 'node:readline';
import {pathToFileURL} from 'node:url';
import {randomBytes,randomUUID} from 'node:crypto';
const {hermesSolidFixture}=await import(pathToFileURL(process.argv[2]+'/tests/helpers/hermes-solid-fixture.mjs'));
let f;
const cleanup=[],server=createServer((req,res)=>f.board.app(req,res));
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
f=await hermesSolidFixture({after:fn=>cleanup.push(fn)},{origin});
const base='/api/rooms/'+f.room.id;
const invited=await f.post(f.owner,base+'/agent-invites',{displayName:'Peer',clientId:randomUUID()});
const [invitationId,invitationToken]=new URL(invited.body.invitationUrl).hash.slice('#agent-invite='.length).split('.');
const joined=await f.post(f.owner,base+'/agent-invites/redeem',{invitationId,invitationToken,transport:'api',claimNonce:randomBytes(32).toString('base64url')});
if(joined.status!==201)throw Error('peer fixture refused');
await f.rpc('hermes_listener',{room_id:f.room.id,state:'listening'});
console.log(JSON.stringify({origin,room_id:f.room.id,participant_id:f.membership.participant_id,access_token:f.tokens.access_token}));
const timer=setTimeout(()=>process.exit(124),180000).unref();
try{
 for await(const line of createInterface({input:process.stdin})){
  const q=JSON.parse(line);let value;
  if(q.op==='human'){
   const r=await f.post(f.owner,base+'/messages',{text:q.text,clientId:randomUUID()});if(r.status!==201)throw Error('human fixture failed');value=r.body.message;
  }else if(q.op==='peer'){
   const r=await fetch(origin+base+'/messages',{method:'POST',headers:{'Content-Type':'application/json',Origin:origin,'X-Board-Request':'1',Authorization:'Bearer '+joined.body.access.token},body:JSON.stringify({text:q.text,clientId:randomUUID(),seenThrough:q.seen_through})});
   if(r.status!==201)throw Error('peer fixture failed '+r.status);value=(await r.json()).message;
  }else if(q.op==='inspect')value={messages:f.db.prepare('SELECT id,room_number number,text,client_id clientId,send_actor_id participant FROM messages WHERE room_id=? ORDER BY id').all(f.room.id),read:f.db.prepare('SELECT read_checkpoint,ack_at FROM participant_checks WHERE id=?').get(f.membership.participant_id),attestations:f.db.prepare('SELECT message_id,from_id,through_id FROM agent_read_attestations WHERE participant_id=? ORDER BY message_id').all(f.membership.participant_id),receipts:f.db.prepare('SELECT message_id FROM agent_context_receipts WHERE participant_id=? ORDER BY message_id').all(f.membership.participant_id)};
  else if(q.op==='revoke')value=(await f.post(f.owner,base+'/agent-memberships/'+f.membership.id+'/revoke',{})).status;
  else if(q.op==='stop')break;
  else throw Error('unknown fixture operation');
  console.log(JSON.stringify(value));
 }
}finally{clearTimeout(timer);server.closeAllConnections();await new Promise(r=>server.close(r));for(const fn of cleanup.reverse())await fn();}
