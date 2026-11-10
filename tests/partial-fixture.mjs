// OFFLINE synthetic HTTP fixture. No sockets, real room, account or model calls.
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {BotSeshAgent,SecretStore} from '../vendor/botsesh-agent.mjs';
import {runRequest,failure} from '../bridge.mjs';
export const room='https://botsesh.com/b/offline';
export const participant='c3549c36-e39a-44a4-9b09-2c89483b86c0';
export const author='7f5081ed-bb74-4304-9fb7-12bd396831f7';
export const originalId='5c1cfd9c-fdbe-4ef4-9f61-30a6561f41d9';
export const text='Exact final.\nUnicode: café 🐚\n  retained spacing\n';
export const request={op:'post',room,client_id:originalId,text,reply_to:7,processed:true,draft_ready:{title:'Synthetic draft',recipientId:author,blockers:[]}};
const person={id:author,name:'Fixture owner',verified:true,kind:'human'};
const message=n=>({id:n*10,number:n,text:'Question '+n,senderParticipantId:author,authorParticipant:person,isMentionToMe:true});
export async function seed(directory){
  const membership={id:originalId,participantId:participant,expiresAt:Date.now()+3600000,displayName:'Offline test'};
  const client=new BotSeshAgent({storeDirectory:directory,roomUrl:room,secretStore:new SecretStore({directory,mode:'file',notice:()=>{}})});
  await client.store.write({version:1,origin:'https://botsesh.com',roomId:'offline',accessToken:'bsa_'+'a'.repeat(43),membership,participantId:participant,readCheckpoint:0});
  await writeFile(join(directory,'fixture.json'),JSON.stringify({membership,checkpoint:0,claim:false,revoked:false,newer:false,messages:[],commands:[],acks:[],requests:[]}),{mode:0o600});
}
export async function load(directory){return JSON.parse(await readFile(join(directory,'fixture.json'),'utf8'));}
export async function save(directory,state){await writeFile(join(directory,'fixture.json'),JSON.stringify(state),{mode:0o600});}
export async function invoke(req,directory){
  const state=await load(directory);
  const fetchImpl=async(url,options)=>{
    const u=new URL(url),path=u.pathname.replace('/api/rooms/offline',''),method=options.method;
    const body=options.body?JSON.parse(options.body):null;
    state.requests.push({method,path,body}); // No request headers or credentials retained.
    if(state.revoked)return Response.json({code:'membership_revoked'},{status:403});
    if(path==='/agent-access')return Response.json({membership:state.membership,participantId:participant,readCheckpoint:state.checkpoint,accessExpiresAt:state.membership.expiresAt});
    if(path==='/sync')return Response.json({code:'not_found'},{status:404});
    if(path==='/catch_up')return Response.json({schema_version:1,room_id:'offline',text:'Synthetic complete briefing.',complete:true,next_page:null,read_effect:'none'});
    if(path==='/updates'){
      const all=[message(7),...state.messages,...(state.newer?[message(10)]:[])];
      const after=Number(u.searchParams.get('after') || state.checkpoint);
      const messages=all.filter(m=>m.id>after);
      return Response.json({messages,mentionsToMe:messages.filter(m=>m.isMentionToMe),repliesToMe:[],hasMore:false,nextCheckpoint:messages.at(-1)?.id ?? after,nextCheckpointNumber:messages.at(-1)?.number ?? null});
    }
    if(path==='/messages' && method==='GET')return Response.json({messages:[]});
    if(path==='/messages' && method==='POST'){
      let posted=state.messages.find(m=>m.clientId===body.clientId);
      if(!posted){posted={id:80,number:8,ref:'#8',clientId:body.clientId,text:body.text,senderParticipantId:participant,authorParticipant:{id:participant,verified:true},isOwn:true,replyPreview:{number:body.replyToNumber}};state.messages.push(posted);}
      return Response.json({message:posted});
    }
    if(path.startsWith('/messages/by-client/')){
      const posted=state.messages.find(m=>m.clientId===path.split('/').at(-1));
      return Response.json(posted?{message:posted}:{code:'not_found'},{status:posted?200:404});
    }
    if(path==='/ack'){
      if(state.denyAck)return Response.json({code:'agent_right_denied'},{status:403});
      state.acks.push(body);state.checkpoint=body.through;return Response.json({});
    }
    if(path==='/situation')return Response.json({situation:{complete:true,brief_revision:1,purpose_revision:1,snapshot_id:'fixture-unchanged-snapshot'}});
    if(path==='/purpose/commands' && method==='POST'){
      state.commands.push(body);
      return state.claim?Response.json({clientId:body.clientId,applied:true}):Response.json({code:'claim_required'},{status:403});
    }
    if(path.startsWith('/purpose/commands/by-client/'))return Response.json({clientId:path.split('/').at(-1),applied:true});
    if(path==='')return Response.json({room:{name:'Offline room',expiresAt:state.membership.expiresAt}});
    if(path==='/context')return Response.json({enabled:true,revision:1,markdown:'Fixture Brief',decisions:[]});
    if(path==='/participants')return Response.json({participants:[person,{id:participant,name:'Fixture agent',verified:true}]});
    if(path==='/wake/status')return Response.json({participants:[]});
    if(path==='/wake/quiet-turn')return Response.json({passed:true});
    if(path==='/continuity')return Response.json({});
    if(path==='/routines')return Response.json({routines:[]});
    throw new Error('Unexpected offline request '+method+' '+path);
  };
  try{return {ok:true,result:await runRequest(req,directory,{fetchImpl})};}
  catch(error){return failure(error);}
  finally{await save(directory,state);}
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href){
  const directory=process.argv[2];
  if(process.argv[3]==='seed'){await seed(directory);console.log('{}');}
  else{let input='';for await(const part of process.stdin)input+=part;console.log(JSON.stringify(await invoke(JSON.parse(input),directory)));}
}
