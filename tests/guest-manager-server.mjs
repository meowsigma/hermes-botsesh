// Local real-server fixture only. Node serves the application; the Python
// plugin process has neither node nor nodejs on PATH and cannot invoke a bridge.
import {createServer} from 'node:http';
import {randomBytes} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
const {createApp}=await import(pathToFileURL(process.argv[2]));
const dir=mkdtempSync(join(tmpdir(),'python-guest-manager-'));
let board,time=Date.now(),guestRequests=0,authenticatedGuestRequests=0;
const server=createServer((req,res)=>{
  if(req.url==='/api/agent/rooms'){
    guestRequests++;
    if(req.headers.cookie || req.headers.authorization)authenticatedGuestRequests++;
  }
  board.app(req,res);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
board=createApp({dataDir:dir,origin,secret:randomBytes(64).toString('base64url'),now:()=>time,observability:{log(){}}});
console.log(JSON.stringify({origin}));
const input=createInterface({input:process.stdin});
for await(const line of input){
  const req=JSON.parse(line);let result;
  if(req.op==='advance'){time+=req.ms;result={advanced:true};}
  else if(req.op==='manager_revoke'){
    board.db.prepare('UPDATE agent_room_managers SET revoked_at=? WHERE room_id=?').run(time,req.room_id);
    result={revoked:true};
  }else if(req.op==='member_revoke'){
    board.db.prepare('UPDATE agent_memberships SET revoked_at=? WHERE room_id=?').run(time,req.room_id);
    result={revoked:true};
  }else if(req.op==='inspect'){
    const room=board.db.prepare('SELECT owner_account_id,expires_at FROM rooms WHERE id=?').get(req.room_id);
    result={guestRequests,authenticatedGuestRequests,creations:board.db.prepare('SELECT count(*) n FROM agent_room_creations').get().n,
      humanOwner:Boolean(room?.owner_account_id),expires:room?.expires_at,
      accepted:board.db.prepare("SELECT count(*) n FROM agent_ownership_events WHERE kind='accepted' AND room_id=?").get(req.room_id).n,
      managerRevoked:Boolean(board.db.prepare('SELECT revoked_at FROM agent_room_managers WHERE room_id=?').get(req.room_id)?.revoked_at),
      audit:board.db.prepare("SELECT count(*) n FROM messages WHERE room_id=? AND text LIKE '%is now the owner%'").get(req.room_id).n};
  }else if(req.op==='stop')break;
  else throw new Error('unknown fixture command');
  console.log(JSON.stringify(result));
}
server.closeAllConnections();await new Promise(r=>server.close(r));board.close();rmSync(dir,{recursive:true,force:true});
