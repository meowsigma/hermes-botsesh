#!/usr/bin/env node
/** Narrow, stdin-only protocol boundary. No shell, model/provider access or CLI dispatch. */
import {BotSeshAgent, SecretStore, parseInvitationUrl, parseRoomUrl, completeRoomTurn, parseRoomTurnOutput, ClientError} from './vendor/botsesh-agent.mjs';
import {randomUUID} from 'node:crypto';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const MAX_INPUT = 2*1024*1024;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function requireId(id) {if(!UUID.test(id || ''))throw new ClientError('Use an exact UUIDv4 operation/receipt ID.','invalid_client_id');return id;}
function allowedUrl(value, invitation=false) {
  const parsed=(invitation?parseInvitationUrl:parseRoomUrl)(value);
  if(parsed.origin!=='https://botsesh.com')throw new ClientError('This build connects only to https://botsesh.com.','origin_denied');
  return parsed;
}
const emit=value=>new Promise((resolve,reject)=>process.stdout.write(JSON.stringify(value)+'\n',e=>e?reject(e):resolve()));
export async function runRequest(req, storeDirectory, {fetchImpl}={}) {
  if(Number(process.versions.node.split('.')[0])<24)throw new ClientError('Node 24+ is required.','node_required');
  if(!storeDirectory)throw new ClientError('Private plugin store required.','store_required');
  const room=req.room?allowedUrl(req.room):null;
  if(req.op==='join')allowedUrl(req.link,true);
  const client=new BotSeshAgent({storeDirectory,roomUrl:req.room,...(fetchImpl?{fetchImpl}:{}),timeoutMs:15000,maxUpdateBytes:2*1024*1024,retryBudgetMs:45000,secretStore:new SecretStore({directory:storeDirectory,mode:'file',notice:()=>{}})});
  const scope='hermes-plugin-all-operations';
  const indexKey='plugin-receipt-index',origin='https://botsesh.com';
  const receiptIndex=async()=>await client.store.read(origin,indexKey) || {version:1,origin,roomId:indexKey,receipts:{}};
  const pruneReceipts=async()=>{
    const index=await receiptIndex();let changed=false;
    for(const [id,expires] of Object.entries(index.receipts))if(Date.now()>=expires){
      await client.store.remove(origin,'plugin-receipt-'+requireId(id));delete index.receipts[id];changed=true;
    }
    if(changed)await client.store.write(index);
  };
  const readRecord=async id=>{
    const record=await client.store.read(room.origin,'plugin-receipt-'+requireId(id));
    if(!record || record.boundRoom!==room.roomId)throw new ClientError('Receipt belongs to another room or is unavailable.','receipt_scope');
    return record;
  };
  // Local SDK journal projection, not a new server effect/authority contract. Never
  // return reply bytes, commands, credentials or an unverified receipt as success.
  const pendingResult=async()=>{
    const job=client.state?.localJob;
    if(!job || job.pluginComplete || !UUID.test(job.eventId || ''))return null;
    const saved=await client.store.read(room.origin,'plugin-post-'+job.eventId);
    const boundary=job.readBoundary;
    return {client_id:job.eventId,number:job.replyNumber ?? null,
      read_effect:Number.isSafeInteger(job.replyNumber) && boundary?.ackEligible && Number.isSafeInteger(boundary.through) && client.state.readCheckpoint>=boundary.through?'processed_frozen_receipt':'unconfirmed',
      ...(job.draft_ready?{draft_ready:job.purposeResults?.[0]?'confirmed':saved?.failure?.operation==='draft_ready' && saved.failure.status===403?'denied':'unfinished'}:{}),
      ...(saved?.failure?{error:saved.failure}:{})};
  };
  const work=async()=>{
    await pruneReceipts();
    switch(req.op){
      case 'create': return client.create({name:req.name,...(req.purpose===undefined?{}:{purpose:req.purpose}),...(req.client_id?{clientId:requireId(req.client_id)}:{})});
      case 'offer_ownership': return client.offerOwnership({clientId:requireId(req.client_id),userRequested:req.user_requested===true});
      case 'revoke_ownership_offer': return client.revokeOwnershipOffer(requireId(req.offer_id));
      case 'join': return client.enroll(req.link);
      case 'verify': return client.verify();
      case 'pending': await client.verify();return {pending:await pendingResult()};
      case 'check': {
        await client.verify();
        const pending=await pendingResult();
        if(pending)return {hasMore:false,pending}; // No later inference/read advancement.
        const summary=await client.checkNew();
        if(!summary.hasMore)return summary;
        const batch=await client.catchup({maxChars:60000});
        if(batch.hasMore || batch.skippedCount)throw new ClientError('Backlog exceeds context budget. Nothing acknowledged.','context_limit');
        return {...summary,mentions:batch.messages.filter(m=>m.isMentionToMe || m.isDirectMentionToMe).map(m=>m.number),replies:batch.messages.filter(m=>m.isReplyToMe).map(m=>m.number),hasMore:false};
      }
      case 'read': {
        const briefing=await client.pluginCatchUp();
        const batch={...await client.catchup({maxChars:60000,...(req.target_number===undefined?{}:{targetNumber:req.target_number})}),routines:await client.routines()};
        if(batch.hasMore || batch.skippedCount)throw new ClientError('Room context exceeds this adapter budget. Nothing acknowledged; read it with the canonical client.','context_limit');
        const id=randomUUID();
        const record={version:1,origin:room.origin,roomId:'plugin-receipt-'+id,boundRoom:room.roomId,participantId:client.state.participantId,batch,createdAt:Date.now()};
        const result={read_effect:'none',receipt_id:id,briefing,batch};
        if(Buffer.byteLength(JSON.stringify(result))>160000)throw new ClientError('Complete context exceeds the plugin delivery budget. Nothing acknowledged.','context_limit');
        const index=await receiptIndex();index.receipts[id]=Math.min(record.createdAt+86400000,client.state.membership.expiresAt);
        await client.store.write(index);
        await client.store.write(record);
        return result;
      }
      case 'retry': {
        // Operator-selected exact replay of our own journal, never a fresh final,
        // claim acquisition, changed receipt or regenerated purpose command.
        await client.verify();
        requireId(req.client_id);
        const saved=await client.store.read(room.origin,'plugin-post-'+req.client_id);
        if(!saved || saved.boundRoom!==room.roomId)throw new ClientError('Original operation is unavailable in this room.','receipt_scope');
        const original=JSON.parse(saved.exact);
        req={op:'post',room:req.room,text:original.intent.reply,...original.intent,client_id:original.client_id,receipt_id:original.receipt_id,processed:true,...(original.reply_to===null?{}:{reply_to:original.reply_to})};
        delete req.reply;
        return work();
      }
      case 'fail_task': {
        if(Object.keys(req).some(k=>!['op','room','client_id','receipt_id','failure','task_id','status','reason','partial_notes'].includes(k)) || req.failure && ['task_id','status','reason','partial_notes'].some(k=>Object.hasOwn(req,k)))throw new ClientError('Use one exclusive failure and its exact receipt/operation identity.','invalid_task_failure');
        requireId(req.client_id);
        const {failure}=parseRoomTurnOutput({failure:req.failure ?? {task_id:req.task_id,status:req.status,reason:req.reason,...(req.partial_notes===undefined?{}:{partial_notes:req.partial_notes})}});
        if(failure.status!=='cant_do')throw new ClientError('This plugin operation reports only an explicit inability.','invalid_task_failure');
        // The source comes from this exact frozen receipt. Server additionally checks
        // live membership, current winner, source version, retry identity and room.
        const record=await readRecord(req.receipt_id);
        if(!record.batch.messages.some(m=>m.addressing?.taskId===failure.task_id))throw new ClientError('Failure task was not supplied in this receipt.','receipt_scope');
        await client.verify();
        if(record.participantId!==client.state.participantId)throw new ClientError('Receipt identity changed.','receipt_scope');
        const key='plugin-failure-'+req.client_id,exact=JSON.stringify({failure,receipt_id:req.receipt_id});
        const saved=await client.store.read(room.origin,key);
        if(saved && (saved.boundRoom!==room.roomId || saved.exact!==exact))throw new ClientError('Retry the original failure and receipt.','pending_send_mismatch');
        if(saved?.complete)return saved.result;
        if(!saved)await client.store.write({version:1,origin:room.origin,roomId:key,boundRoom:room.roomId,exact});
        const response=await client.reportTaskFailure(failure.task_id,{clientId:req.client_id,status:failure.status,reason:failure.reason,...(failure.partial_notes===undefined?{}:{partialNotes:failure.partial_notes})});
        if(response?.taskId!==failure.task_id || response?.status!=='cant_do')throw new ClientError('Failure unconfirmed; retry the same intent.','task_failure_unconfirmed');
        const result={failure:response,read_effect:'none'};
        await client.store.write({version:1,origin:room.origin,roomId:key,boundRoom:room.roomId,exact,complete:true,result});
        return result;
      }
      case 'post': {
        requireId(req.client_id);
        if(typeof req.text!=='string' || !req.text.trim() || req.text.length>4000)throw new ClientError('Use 1–4000 characters.','invalid_text');
        const intent={reply:req.text,...(req.draft_ready===undefined?{}:{draft_ready:req.draft_ready}),...(req.routine_report===undefined?{}:{routine_report:req.routine_report})};
        parseRoomTurnOutput(intent); // Reject unsupported/owner-only fields before any durable intent.
        if(!req.receipt_id){
          if(req.draft_ready || req.routine_report)throw new ClientError('Structured completion requires a processed receipt.','receipt_required');
          await client.verify();
          if(client.state.localJob && !client.state.localJob.pluginComplete)throw new ClientError('An earlier turn is pending. Retry its original client_id and complete intent.','pending_turn');
          return {client_id:req.client_id,...await client.send(req.text,{replyToNumber:req.reply_to,stableClientId:req.client_id,noRead:true})};
        }
        if(req.processed!==true)throw new ClientError('Explicitly process the complete returned briefing and batch before confirming it.','processed_required');
        await client.verify();
        const exact=JSON.stringify({intent,reply_to:req.reply_to ?? null,receipt_id:req.receipt_id,client_id:req.client_id});
        const key='plugin-post-'+req.client_id;
        let saved=await client.store.read(room.origin,key);
        if(saved?.boundRoom && saved.boundRoom!==room.roomId)throw new ClientError('Operation belongs to another room.','receipt_scope');
        if(saved && saved.exact!==exact)throw new ClientError('Retry must preserve exact text, receipt and structured intent.','pending_send_mismatch');
        if(saved?.complete){
          if(client.state.localJob?.eventId===req.client_id && !client.state.localJob.pluginComplete){client.state={...client.state,localJob:{...client.state.localJob,pluginComplete:true}};await client.store.write(client.state);}
          return saved.result;
        }
        const record=await readRecord(req.receipt_id);
        if(Date.now()-record.createdAt>86400000)throw new ClientError('Processed receipt expired. Read fresh context before another operation.','receipt_expired');
        if(record.participantId!==client.state.participantId)throw new ClientError('Receipt identity changed.','receipt_scope');
        if(req.reply_to!==undefined && !record.batch.messages.some(m=>m.number===req.reply_to))throw new ClientError('Reply target was not supplied in this receipt.','unread_reply_target');
        const existing=client.state.localJob;
        if(existing && existing.eventId!==req.client_id && !existing.pluginComplete)throw new ClientError('An earlier uncertain turn is pending. Retry its original client_id and intent.','pending_turn');
        if(!saved){saved={version:1,origin:room.origin,roomId:key,boundRoom:room.roomId,exact};await client.store.write(saved);}
        let job=existing?.eventId===req.client_id?existing:null;
        if(!job){
          if(req.routine_report)await client.validateRoutineReport(req.routine_report,record.batch.routines || []);
          // Only the explicit processed operation—not fetching or SSE—arms this frozen boundary.
          await client.recordPrintedCatchup(record.batch);
          job={...intent,eventId:req.client_id,replyToNumber:req.reply_to,readBoundary:structuredClone(client.state.pendingRead),routineBindings:record.batch.routines || record.batch.context?.routines || []};
          client.state={...client.state,localJob:job};await client.store.write(client.state);
        }
        // Attribute a denied declaration only when that SDK operation failed.
        const purposeAction=client.purposeAction.bind(client);
        client.purposeAction=async(command,options)=>{
          try{return await purposeAction(command,options);}
          catch(error){error.operation=command.op==='draft_ready'?'draft_ready':'purpose_action';throw error;}
        };
        let number;
        try{number=await completeRoomTurn(client,job);}
        catch(error){await client.store.write({...saved,failure:safeError(error)});throw error;}
        const result={client_id:req.client_id,number,read_effect:'processed_frozen_receipt',...(client.state.localJob?.routineReportRejected?{routine_report_rejected:client.state.localJob.routineReportRejected}:{})};
        await client.store.write({...saved,complete:true,result});
        client.state={...client.state,localJob:{...client.state.localJob,pluginComplete:true}};await client.store.write(client.state);
        await client.store.remove(room.origin,'plugin-receipt-'+req.receipt_id);
        return result;
      }
      case 'invite': {
        const id=requireId(req.client_id),out=join(storeDirectory,'invite-'+id+'.txt');
        const result=await client.createInvite({app:req.app,displayName:req.display_name,task:req.task,clientId:id,out});
        // Deliberately no capability URL in model-visible output.
        return {...result,private_file:out};
      }
      case 'listen': await client.verify();return null;
      default: throw new ClientError('Unsupported plugin operation.','operation_denied');
    }
  };
  const result=await client.store.withLock(scope,async()=>{
    try{return await work();}
    catch(error){error.pending=await pendingResult().catch(()=>null);throw error;}
  },{timeoutMs:60000});
  if(req.op!=='listen')return result;
  await emit({ok:true,event:'connected'});
  const controller=new AbortController();
  process.on('SIGTERM',()=>{controller.abort();setTimeout(()=>process.exit(0),1000).unref();});
  await client.listenInvalidations({signal:controller.signal,onChange:()=>{void emit({ok:true,event:'changed'});}});
}
function safeError(error){
  const code=/^[a-z0-9_-]{1,80}$/i.test(error.code || '')?error.code:'bridge_failed';
  return {code,status:error.status || null,retry_after_ms:error.retryAfterMs || 0,...(['draft_ready','purpose_action'].includes(error.operation)?{operation:error.operation}:{})};
}
export function failure(error){return {ok:false,error:safeError(error),...(error.pending?{pending:error.pending}:{})};}
async function main(){
  let input='';for await(const part of process.stdin){input+=part;if(Buffer.byteLength(input)>MAX_INPUT)throw new ClientError('Request too large.','input_limit');}
  const req=JSON.parse(input),result=await runRequest(req,process.argv[2]);
  if(req.op!=='listen')await emit({ok:true,result});
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href)main().catch(async error=>{
  // SDK errors are safe enums/messages; no stack, request, room text or credentials.
  await emit(failure(error)).catch(()=>{});
  process.exitCode=1;
});
