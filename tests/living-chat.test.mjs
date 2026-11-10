// Offline Advanced transport parity; no sockets/providers/real credentials.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {seed,invoke,load,save,room} from './partial-fixture.mjs';

async function fixture(t){const dir=await mkdtemp(join(tmpdir(),'living-chat-'));t.after(()=>rm(dir,{recursive:true,force:true}));await seed(dir);return dir;}

test('Advanced multiple posts reuse one receipt and explicit UUID retry',async t=>{
 const dir=await fixture(t),read=await invoke({op:'read',room},dir);
 assert.equal(read.ok,true);
 const args={op:'post',room,receipt_id:read.result.receipt_id,processed:true,text:'Deliberate repeat.',client_id:randomUUID()};
 assert.equal((await invoke(args,dir)).ok,true);
 assert.equal((await invoke(args,dir)).ok,true);
 assert.equal((await invoke({...args,client_id:randomUUID()},dir)).ok,true);
 assert.equal((await load(dir)).messages.length,2);
});

test('Advanced receipt alone posts without acknowledgment',async t=>{
 const dir=await fixture(t),read=await invoke({op:'read',room},dir);
 const post=await invoke({op:'post',room,receipt_id:read.result.receipt_id,text:'Unattested words.',client_id:randomUUID()},dir);
 assert.equal(post.ok,true);assert.equal(post.result.read_effect,'none');
 const state=await load(dir);assert.equal(state.acks.length,0);assert.equal(state.checkpoint,0);
});

test('Advanced pass quiets only frozen inputs, never later arrivals',async t=>{
 const dir=await fixture(t),read=await invoke({op:'read',room},dir);
 const state=await load(dir);state.newer=true;await save(dir,state);
 const args={op:'finish',room,receipt_id:read.result.receipt_id,processed:true,output:{nothing_to_add:true},client_id:randomUUID()};
 const passed=await invoke(args,dir);assert.equal(passed.ok,true,JSON.stringify(passed));
 assert.equal((await invoke(args,dir)).ok,true);
 const after=await load(dir);assert.equal(after.messages.length,0);assert.equal(after.checkpoint,70);
 const quiet=after.requests.filter(r=>r.path==='/wake/quiet-turn');
 assert.equal(quiet.length,1);assert.deepEqual(quiet[0].body,{throughNumber:7});
});

test('Advanced unattested threaded send fails closed before sparse target ack',async t=>{
 const dir=await fixture(t);
 const result=await invoke({op:'post',room,text:'No read permission.',reply_to:7,client_id:randomUUID()},dir);
 assert.equal(result.ok,false);assert.equal(result.error.code,'unattested_reply_target_not_supported');
 assert.equal((await load(dir)).messages.length,0);
});
