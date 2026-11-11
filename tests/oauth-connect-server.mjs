// Exact-image localhost OAuth fixture. No model, production or database mutation.
// Only the server runs Node; the native Python CLI has an empty PATH.
import {createServer} from 'node:http';
import {randomBytes,randomUUID} from 'node:crypto';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {pathToFileURL} from 'node:url';
const {createApp}=await import(pathToFileURL(process.argv[2]));
const dir=mkdtempSync(join(tmpdir(),'oauth-connect-'));
let board,cookie='';
const lists=[],requests=[];
const server=createServer((req,res)=>{
 requests.push({method:req.method,path:req.url.split('?')[0]});
 if(req.url==='/mcp'){
  const chunks=[],write=res.write,end=res.end;
  res.write=function(chunk,...args){if(chunk)chunks.push(Buffer.from(chunk));return write.call(this,chunk,...args);};
  res.end=function(chunk,...args){
   if(chunk)chunks.push(Buffer.from(chunk));
   const raw=Buffer.concat(chunks).toString();
   for(const text of [raw,...raw.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim())]){
    try{const data=JSON.parse(text)?.result?.structuredContent;if(Array.isArray(data?.rooms))lists.push(data);}catch{}
   }
   return end.call(this,chunk,...args);
  };
 }
 board.app(req,res);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin='http://127.0.0.1:'+server.address().port;
board=createApp({dataDir:dir,origin,secret:randomBytes(64).toString('base64url'),observability:{log(){}}});
const headers={Origin:origin,'X-Board-Request':'1','User-Agent':'BotSesh-Agent/1 (+https://botsesh.com/agents)'};
async function request(path,{method='GET',body,form}={}){
 const response=await fetch(origin+path,{method,redirect:'manual',headers:{...headers,...(cookie?{Cookie:cookie}:{}),...(body?{'Content-Type':'application/json'}:form?{'Content-Type':'application/x-www-form-urlencoded'}:{})},...(body?{body:JSON.stringify(body)}:form?{body:new URLSearchParams(form)}:{})});
 for(const c of response.headers.getSetCookie())cookie+=(cookie?'; ':'')+c.split(';')[0];
 if(response.status>=400)throw Error('fixture HTTP '+response.status+' '+path.split('?')[0]);
 return response;
}
await request('/api/account/register',{method:'POST',body:{username:'local-'+randomUUID().slice(0,24),password:randomBytes(32).toString('base64url')}});
const roomIds=[];
for(const name of ['Connect target','Other selected room']){
 const response=await request('/api/rooms',{method:'POST',body:{name,displayName:'Fixture owner',password:randomBytes(32).toString('base64url')}});
 roomIds.push((await response.json()).room.id);
}
console.log(JSON.stringify({origin,room_ids:roomIds}));
const timer=setTimeout(()=>process.exit(124),240000).unref();
try{
 for await(const line of createInterface({input:process.stdin})){
  const command=JSON.parse(line);let result;
  if(command.op==='approve'){
   const url=new URL(command.url);
   if(url.origin!==origin)throw Error('foreign fixture origin');
   let response=await request(url.pathname+url.search);
   if(response.status===303){const next=new URL(response.headers.get('location'),origin);response=await request(next.pathname+next.search);}
   const html=await response.text();
   const field=name=>{const value=html.match(new RegExp('name="'+name+'" value="([^"]+)"'))?.[1];if(!value)throw Error('missing consent field '+name);return value;};
   const form={consent_id:field('consent_id'),csrf:field('csrf'),review_id:field('review_id'),decision:'allow'};
   const entries=Object.entries(form);
   if(command.legacy)entries.push(['selected_room_id',command.room_ids[0]]);
   else for(const id of command.room_ids)entries.push(['selected_room_ids',id]);
   response=await request('/api/agent/room-consent',{method:'POST',form:entries});
   if(response.status!==302)throw Error('consent did not redirect');
   const callback=new URL(response.headers.get('location'));
   if(callback.hostname!=='127.0.0.1' || callback.pathname!=='/callback')throw Error('foreign fixture callback');
   const completed=await fetch(callback,{redirect:'manual'});
   if(completed.status!==302)throw Error('callback did not complete');
   result={approved:true};
  }else if(command.op==='snapshot'){
   result={list_rooms:lists.splice(0),requests:requests.splice(0),grants:board.db.prepare('SELECT count(*) total,sum(revoked_at IS NOT NULL) revoked FROM mcp_oauth_grants').get()};
  }else if(command.op==='stop')break;
  else throw Error('unknown fixture command');
  console.log(JSON.stringify(result));
 }
}finally{
 clearTimeout(timer);server.closeAllConnections();await new Promise(r=>server.close(r));board.close();rmSync(dir,{recursive:true,force:true});
}
