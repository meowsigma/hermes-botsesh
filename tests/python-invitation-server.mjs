// Local exact-image HTTP server only. The Python client has an empty PATH.
// No production traffic or inference; stdin controls own fixture data/cleanup.
import {createServer} from 'node:http';
import {randomBytes,randomUUID} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {pathToFileURL} from 'node:url';
const {createApp}=await import(pathToFileURL(process.argv[2]));
const dir=mkdtempSync(join(tmpdir(),'python-invitation-'));
let board;
const server=createServer((req,res)=>board.app(req,res));
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
board=createApp({dataDir:dir,origin,secret:randomBytes(64).toString('base64url'),observability:{log(){}}});
const headers={Origin:origin,'X-Board-Request':'1','Content-Type':'application/json','User-Agent':'BotSesh-Agent/1 (+https://botsesh.com/agents)'};
let cookie='',base;
async function api(method,path,body){
 const r=await fetch(origin+path,{method,headers:{...headers,...(cookie?{Cookie:cookie}:{})},...(body===undefined?{}:{body:JSON.stringify(body)})});
 const value=await r.json();
 if(!r.ok)throw Error(method+' '+path+' '+r.status+' '+JSON.stringify(value));
 for(const c of r.headers.getSetCookie())cookie+=(cookie?'; ':'')+c.split(';')[0];
 return value;
}
async function create(name){
 const room=(await api('POST','/api/rooms',{name,displayName:'Fixture human',password:'local-only-room-password'})).room;
 base='/api/rooms/'+room.id;
 const invite=await api('POST',base+'/agent-invites',{clientId:randomUUID(),displayName:'Hermes',accessDays:1});
 const peer=await api('POST',base+'/agent-invites',{clientId:randomUUID(),displayName:'Peer',accessDays:1});
 return {room_id:room.id,link:invite.browserInvitationUrl,legacy:invite.invitationUrl,peer:peer.invitationUrl};
}
console.log(JSON.stringify({origin}));
const timer=setTimeout(()=>process.exit(124),180000).unref();
try{
 for await(const line of createInterface({input:process.stdin})){
  const req=JSON.parse(line);let result;
  if(req.op==='create'){
   if(!cookie)await api('POST','/api/account/register',{username:'local-'+randomUUID().slice(0,24),password:'local-only-account-password-strong'});
   result=await create(req.name || 'No Node invitation');
  }else if(req.op==='message')result=await api('POST','/api/rooms/'+req.room_id+'/messages',{clientId:randomUUID(),text:req.text});
  else if(req.op==='inspect'){
   const pid=req.participant_id;
   result={messages:board.db.prepare('SELECT room_number number,text,client_id clientId FROM messages WHERE room_id=? ORDER BY id').all(req.room_id),
    memberships:board.db.prepare('SELECT count(*) n FROM agent_memberships WHERE room_id=?').get(req.room_id).n,
    checkpoint:board.db.prepare('SELECT read_checkpoint FROM participant_checks WHERE id=?').get(pid)?.read_checkpoint ?? 0,
    attestations:board.db.prepare('SELECT count(*) n FROM agent_read_attestations WHERE participant_id=?').get(pid).n,
    listeners:board.db.prepare('SELECT count(*) n FROM agent_connection_heartbeats WHERE participant_id=? AND listening_until>0').get(pid).n};
  }else if(req.op==='revoke'){
   const member=board.db.prepare('SELECT id FROM agent_memberships WHERE room_id=? AND participant_id=?').get(req.room_id,req.participant_id);
   result=await api('POST','/api/rooms/'+req.room_id+'/agent-memberships/'+member.id+'/revoke',{});
  }else if(req.op==='stop')break;
  else throw Error('unknown fixture command');
  console.log(JSON.stringify(result));
 }
}finally{
 clearTimeout(timer);server.closeAllConnections();await new Promise(r=>server.close(r));board.close();rmSync(dir,{recursive:true,force:true});
}
