// Opt-in LOCAL real-server conversation fixture. Synthetic people, no production.
import {createServer} from 'node:http';
import {randomBytes,randomUUID} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {pathToFileURL} from 'node:url';
const {createApp}=await import(pathToFileURL(process.argv[2]));
const dir=mkdtempSync(join(tmpdir(),'useful-chat-'));let board;
const server=createServer((req,res)=>board.app(req,res));
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
board=createApp({dataDir:dir,origin,secret:randomBytes(64).toString('base64url'),observability:{log(){}}});
const cookies={ari:'',bo:''},accounts={},rooms=[];
const headers={Origin:origin,'X-Board-Request':'1','User-Agent':'BotSesh-Agent/1 (+https://botsesh.com/agents)'};
async function request(actor,path,{method='GET',body}={}){
 const r=await fetch(origin+path,{method,redirect:'manual',headers:{...headers,...(cookies[actor]?{Cookie:cookies[actor]}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
 const jar=new Map(cookies[actor].split('; ').filter(Boolean).map(v=>[v.split('=')[0],v]));
 for(const c of r.headers.getSetCookie()){const part=c.split(';')[0];jar.set(part.split('=')[0],part);}cookies[actor]=[...jar.values()].join('; ');
 if(!r.ok)throw Error('fixture HTTP '+r.status+' '+path.split('?')[0]+' '+await r.text());
 return r.json();
}
for(const actor of ['ari','bo'])accounts[actor]=(await request(actor,'/api/account/register',{method:'POST',body:{username:'local-'+actor+'-'+randomUUID().slice(0,12),password:randomBytes(32).toString('base64url')}})).account;
console.log(JSON.stringify({origin,people:Object.fromEntries(Object.entries(accounts).map(([k,v])=>[k,{accountId:v.id}]))}));
const timer=setTimeout(()=>process.exit(124),9500000).unref();
try{
 for await(const line of createInterface({input:process.stdin})){
  const c=JSON.parse(line);let result;
  if(c.op==='create'){
   const password=randomBytes(32).toString('base64url');
   const room=(await request('ari','/api/rooms',{method:'POST',body:{name:'Useful conversation '+c.run,displayName:'Ari',password}})).room;
   await request('bo','/api/rooms/'+room.id+'/join',{method:'POST',body:{displayName:'Bo',password}});
   const invites={};for(const actor of ['ari','bo'])invites[actor]=(await request('ari','/api/rooms/'+room.id+'/agent-invites',{method:'POST',body:{clientId:randomUUID(),displayName:actor==='ari'?'Ari agent':'Bo agent',accessDays:1}})).browserInvitationUrl;
   rooms.push(room.id);result={room_id:room.id,invites};
  }else if(c.op==='message')result=await request(c.actor,'/api/rooms/'+c.room_id+'/messages',{method:'POST',body:{clientId:randomUUID(),text:c.text}});
  else if(c.op==='snapshot'){
   result={messages:await request('ari','/api/rooms/'+c.room_id+'/messages'),
    deliveries:board.db.prepare(`SELECT m.room_number number,a.participant_id,d.started_at,d.answered_at,d.completed_at,d.failed_at,d.failure_code,d.answer_message_id FROM agent_message_deliveries d JOIN messages m ON m.id=d.message_id JOIN agent_memberships a ON a.id=d.membership_id WHERE d.room_id=? ORDER BY d.message_id,a.participant_id`).all(c.room_id),
    members:board.db.prepare('SELECT id,participant_id,display_name FROM agent_memberships WHERE room_id=?').all(c.room_id),
    agreements:board.db.prepare('SELECT a.*,m.room_number AS number FROM message_agreements a JOIN messages m ON m.id=a.message_id WHERE a.room_id=? ORDER BY a.message_id,a.participant_id').all(c.room_id)};
  }else if(c.op==='delete')result=await request('ari','/api/rooms/'+c.room_id+'/delete',{method:'POST',body:{confirmName:'Useful conversation '+c.run}});
  else if(c.op==='stop')break;
  else throw Error('unknown fixture operation');
  console.log(JSON.stringify(result));
 }
}finally{clearTimeout(timer);server.closeAllConnections();await new Promise(r=>server.close(r));board.close();rmSync(dir,{recursive:true,force:true});}
process.exit(0);
