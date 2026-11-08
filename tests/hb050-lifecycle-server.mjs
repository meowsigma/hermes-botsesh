// Real localhost application fixture. The CLI under test has an empty PATH.
import {createServer} from 'node:http';
import {randomBytes} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
const {createApp}=await import(pathToFileURL(process.argv[2]));
const dir=mkdtempSync(join(tmpdir(),'hb050-real-server-'));
let board,time=Date.now();
const server=createServer((req,res)=>board.app(req,res));
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
board=createApp({dataDir:dir,origin,secret:randomBytes(64).toString('base64url'),now:()=>time,observability:{log(){}}});
console.log(JSON.stringify({origin}));
const input=createInterface({input:process.stdin});
for await(const line of input){
 const req=JSON.parse(line);let result;
 if(req.op==='expire'){
  board.db.prepare('UPDATE rooms SET expires_at=? WHERE id=?').run(time-1,req.room_id);result={expired:true};
 }else if(req.op==='inspect'){
  result={rooms:board.db.prepare('SELECT count(*) n FROM rooms').get().n,
   messages:board.db.prepare('SELECT count(*) n FROM messages WHERE room_id=?').get(req.room_id).n,
   seats:board.db.prepare('SELECT count(*) n FROM agent_memberships WHERE room_id=?').get(req.room_id).n,
   readCheckpoint:board.db.prepare('SELECT read_checkpoint FROM participant_checks WHERE room_id=? AND kind=\'bot\'').get(req.room_id)?.read_checkpoint,
   passwordRequired:board.db.prepare('SELECT password_required FROM room_join_policy WHERE room_id=?').get(req.room_id)?.password_required};
 }else if(req.op==='stop')break;
 else throw new Error('unknown fixture command');
 console.log(JSON.stringify(result));
}
server.closeAllConnections();await new Promise(r=>server.close(r));board.close();rmSync(dir,{recursive:true,force:true});
