// Test-only TLS wrapper around the actual app. No credentials leave this process.
import {createServer} from 'node:https';
import {readFileSync} from 'node:fs';
import {randomBytes, createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createInterface} from 'node:readline';
const [appPath, dataDir, keyPath, certPath] = process.argv.slice(2);
const {createApp} = await import(pathToFileURL(appPath));
const board = createApp({dataDir, origin:'https://botsesh.com', production:false,
  secret:randomBytes(64).toString('base64url'), observability:{log(){}}});
const audit = {requests:0, wrongHost:0, authorizationRequests:0, exchanges:0,
  refreshes:0, pkceVerified:0, tokenResourcesOmitted:0, mcp:[], tokenStatuses:[]};
let challenge, redirect;
const server = createServer({key:readFileSync(keyPath), cert:readFileSync(certPath)}, (req,res)=>{
  audit.requests++;
  if(req.headers.host !== 'botsesh.com')audit.wrongHost++;
  const url = new URL(req.url,'https://botsesh.com');
  if(url.pathname === '/oauth/authorize'){
    audit.authorizationRequests++;
    challenge=url.searchParams.get('code_challenge'); redirect=url.searchParams.get('redirect_uri');
    audit.declared = url.searchParams.get('hermes_declared') === 'v1';
    audit.audience = url.searchParams.get('resource') === 'https://botsesh.com/mcp';
    audit.s256 = url.searchParams.get('code_challenge_method') === 'S256';
  }
  let chunks=[];
  req.on('data',chunk=>chunks.push(chunk));
  req.on('end',()=>{
    const raw=Buffer.concat(chunks).toString(); chunks=[];
    if(url.pathname === '/oauth/'+'token'){
      const body=new URLSearchParams(raw);
      if(body.get('grant_type')==='authorization_code'){
        audit.exchanges++;
        if(createHash('sha256').update(body.get('code_verifier')||'').digest('base64url')===challenge && body.get('redirect_uri')===redirect)audit.pkceVerified++;
        if(!body.has('resource'))audit.tokenResourcesOmitted++;
      }else if(body.get('grant_type')==='refresh_token')audit.refreshes++;
    }
    if(url.pathname === '/mcp'){
      try {const body=JSON.parse(raw); audit.mcp.push(body.params?.name || body.method);}catch{}
    }
  });
  res.on('finish',()=>{
    if(url.pathname === '/oauth/'+'token')audit.tokenStatuses.push(res.statusCode);
  });
  board.app(req,res);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
console.log(JSON.stringify({ready:true,port:server.address().port}));
const input=createInterface({input:process.stdin});
for await(const line of input){
  const command=JSON.parse(line);
  if(command.op==='stop')break;
  if(command.op!=='inspect')throw Error('Unknown test command');
  const memberships=board.db.prepare(`SELECT l.room_id,l.membership_id,m.participant_id,m.revoked_at,m.display_name
    FROM mcp_oauth_memberships l JOIN agent_memberships m ON m.id=l.membership_id
    ORDER BY l.room_id`).all();
  const declared=board.db.prepare('SELECT profile, gateway_hash IS NOT NULL initialized FROM hermes_declared_grants').all();
  const grants=board.db.prepare('SELECT revoked_at IS NOT NULL revoked FROM mcp_oauth_grants').all();
  console.log(JSON.stringify({audit,memberships,declared,grants,
    foreignKeys:board.db.pragma('foreign_key_check').length,
    connections:board.db.prepare('SELECT background_consent,spend_consent,revoked_at FROM agent_connections').all()}));
}
server.closeAllConnections(); await new Promise(resolve=>server.close(resolve)); board.close();
