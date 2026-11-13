"""Opt-in three local real-model conversations through two native Hermes gateways.

Requires prepare_agreement_runtime.py's two owned containers and an explicit
new owner allowance for the bounded Luna/low CLI relay.
Synthetic people Ari/Bo are distinct room accounts; each supplies a separate
agent home and sharing-authorized facts. The host issues their participant-only
invitations. This is not a two-account OAuth ownership/consent test.
"""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import Request,urlopen
from pathlib import Path
import json,os,queue,shutil,subprocess,threading,time,sqlite3
W=Path(__file__).resolve().parent;os.chdir(W)
p=argparse.ArgumentParser();p.add_argument('--runs',type=int,default=3);p.add_argument('--start',type=int,default=1);p.add_argument('--reuse-config',action='store_true');p.add_argument('--dry-run',action='store_true');p.add_argument('--authorized-call-budget',type=int,default=0);args=p.parse_args()
if args.start not in (1,2,3) or args.runs < 1 or args.start + args.runs > 4:
 p.error('Only the original three scenarios are supported.')
if not args.dry_run and args.authorized_call_budget == 0:
 p.error('ZERO inference by default. A new explicit owner allowance is required; maximum 36 calls (12 x 3).')
CANDIDATE_SHA=subprocess.check_output(['git','-C',str(W/'plugin'),'rev-parse','HEAD'],text=True).strip()
PY='/home/sigma/.hermes/installs/27164fdd4ba4535f/environments/1b89bc3d9e1d422484fdeacc775984d9/venv/bin/python'
ACTORS=('ari','bo');E=W/'agreement-evidence';E.mkdir(exist_ok=True)
dry_counts={'ari':0,'bo':0};phase=['setup'];lock=threading.Lock();ledger_path=E/'model-ledger.json';ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else []
from dry_capture_guard import DryCaptureGuard
dry_guard=DryCaptureGuard(E)
from trial_limits import TrialBudget, SampleStopped
from relay_lifecycle import RelayDrain, stop_after_drain, deliver_response
budget=TrialBudget(E,ledger,args.authorized_call_budget)
activity=RelayDrain()
def persist_ledger():
 with lock:ledger_path.write_text(json.dumps(ledger,indent=2))
class Relay(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def do_GET(self):self.forward()
 def do_POST(self):
  try:
   with activity.request():self.forward()
  except SampleStopped:
   try:self.send_error(429,'Sample stopped; no retry')
   except (BrokenPipeError,ConnectionResetError):pass
 def forward(self):
  actor,path=self.path[1:].split('/',1);raw=self.rfile.read(int(self.headers.get('Content-Length',0))) if self.command=='POST' else None;row=None
  if raw:
   data=json.loads(raw)
   if isinstance(data.get('messages'),list):
    names={x['function']['name'] for x in data.get('tools',[])}
    if names!={'botsesh_read','botsesh_post','botsesh_pass','botsesh_agree'}:
     with lock:
      (E/'STOP.json').write_text(json.dumps({'reason':'native_toolset_changed','names':sorted(names),'calls':len(ledger)}))
     self.send_error(502,'Exact four-tool precondition failed; no inference');return
    if args.dry_run:
     from agreement_bridge import preflight, envelope
     with lock:
      dry_counts[actor]+=1
      (E/f'dry-{actor}-request-{dry_counts[actor]}.json').write_text(json.dumps(data,indent=2))
     try:dry_guard.run(data,preflight)
     except Exception:
      self.send_error(502,'Zero-inference preflight failed/latched');return
     if dry_counts[actor]==1:
      result=envelope(0,{'role':'assistant','content':None,'tool_calls':[{'id':'scripted-dry-agree-'+actor,'type':'function','function':{'name':'botsesh_agree','arguments':json.dumps({'room_id':rooms[-1][1],'number':0,'version':0,'active':True})}}]})
     else:result=envelope(0,{'role':'assistant','content':'NO_REPLY'})
     result=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(result);return
    with lock:
     data.update(stream=False)
     row=budget.reserve(data,actor,phase[0],CANDIDATE_SHA)
     raw=json.dumps(data).encode()
  try:
   from agreement_bridge import infer
   if row:
    result=json.dumps(infer(json.loads(raw),row)).encode();status=200
   else:
    result=json.dumps({'object':'list','data':[{'id':'Codex-CLI-actual','object':'model','context_length':65536}]}).encode();status=200
   if row:
    with lock:row.update(status=status,finished=time.time());ledger_path.write_text(json.dumps(ledger,indent=2))
    deliver_response(self,result,row,persist_ledger)
   else:
    self.send_response(status);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(result)
  except Exception as exc:
   if row:
    (E/'STOP.json').write_text(json.dumps({'reason':str(exc),'call':row['n']}))
    with lock:row.update(error=type(exc).__name__,error_detail=str(exc)[-1500:],finished=time.time());ledger_path.write_text(json.dumps(ledger,indent=2))
   try:self.send_error(502,type(exc).__name__)
   except BrokenPipeError:pass
relay=ThreadingHTTPServer(('127.0.0.1',0),Relay);threading.Thread(target=relay.serve_forever,daemon=True).start()
def name(actor):return 'botsesh-agents11e-'+actor+'-'+W.name
def native(actor,*argv,stdin=None,timeout=180):
 r=subprocess.run(['docker','exec','-i',name(actor),PY,*argv],input=stdin,capture_output=True,text=True,timeout=timeout)
 if r.returncode:raise RuntimeError('Native '+actor+' '+str(argv[:3])+' failed: '+r.stdout[-1200:]+r.stderr[-1200:])
 return r.stdout
def cli(actor,*argv):return native(actor,'-m','hermes_cli.main',*argv)
def local(actor,**cmd):return json.loads(native(actor,'/trial/local_actor.py',stdin=json.dumps(cmd)))
server_error=open(E/'server-stderr.log','a')
server=subprocess.Popen(['docker','exec','-i',name('ari'),'node','/trial/useful-chat-server.mjs','/trial/server/app.js'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=server_error,text=True)
def control(**cmd):
 server.stdin.write(json.dumps(cmd)+'\n');server.stdin.flush();line=server.stdout.readline()
 if not line:raise RuntimeError('Local server exited; see server-stderr.log')
 return json.loads(line)
ready=json.loads(server.stdout.readline());(E/'people.json').write_text(json.dumps(ready['people'],indent=2))
gateways={};logs={};outcomes=json.loads((E/'runs.json').read_text()) if (E/'runs.json').exists() and not args.dry_run else [];rooms=[]
try:
 for actor in ACTORS:
  home=W/'private'/actor/'home';dest=home/'plugins/botsesh'
  shutil.copytree(W/'plugin',dest,ignore=shutil.ignore_patterns('.git','__pycache__'),dirs_exist_ok=True)
  cli(actor,'plugins','enable','botsesh')
  (home/'.env').write_text('OPENAI_API_KEY=local-fixture-not-a-credential\n');(home/'.env').chmod(0o600)
  settings={'model':{'default':'Codex-CLI-actual','provider':'custom','base_url':f'http://127.0.0.1:{relay.server_port}/{actor}/v1','api_mode':'chat_completions','context_length':65536},'platform_toolsets.botsesh':['botsesh'],'known_plugin_toolsets.botsesh':['botsesh'],'tools.tool_search.enabled':'off','agent.max_turns':4,'agent.api_max_retries':1,'agent.run_budget_seconds':1800,'memory':{'memory_enabled':False,'user_profile_enabled':False},'gateway.multiplex_profiles':False,'plugins.entries.botsesh.settings.poll_seconds':5,'platforms.botsesh.enabled':True,'plugins.entries.botsesh.settings.dev_origin':ready['origin'],'model.streaming':False,'providers.custom.stale_timeout_seconds':1800,'providers.custom.request_timeout_seconds':1800,'auxiliary.title_generation.enabled':False}
  if args.reuse_config:settings={k:v for k,v in settings.items() if k in ('model','plugins.entries.botsesh.settings.dev_origin','tools.tool_search.enabled','platform_toolsets.botsesh','known_plugin_toolsets.botsesh')}
  def value_arg(v):return v if isinstance(v,str) else json.dumps(v)
  for k,v in settings.items():
   if isinstance(v,dict):
    for sub,value in v.items():cli(actor,'config','set',k+'.'+sub,value_arg(value))
   else:cli(actor,'config','set',k,value_arg(v))
  (E/('config-'+actor+'.json')).write_text(json.dumps({k:cli(actor,'config','get',k).strip() for k in ('model.provider','model.default','model.base_url','model.context_length','tools.tool_search.enabled')},indent=2))
  selected=local(actor,op='config');assert selected['toolsets']==['botsesh'],selected
  (E/('toolsets-'+actor+'.json')).write_text(json.dumps(selected))
  # Each room below receives only its current project's sharing-authorized
  # facts. The earlier broad persona mixed unrelated projects and is retained
  # as failed evidence, not silently pooled with this cohort.
 cases={1:('ari','For our Saturday repair workshop, can we use Harbour Room, and at what time? Ari agent has the group requirements; Bo agent has the venue details.'),2:('bo','Where and when should we book our Thursday team supper so everyone can eat? Ari agent has the group requirements; Bo agent has the venue details.'),3:('ari','Can Studio B host our Friday demo? Ari agent has the group requirements; Bo agent has the venue details.')}
 for run in range(args.start,args.start+args.runs):
  phase[0]='agreement-cli/run-'+str(run);fixture=control(op='create',run=run);room=fixture['room_id'];rooms.append((run,room));ids={}
  project_facts={1:{'ari':'The repair workshop has 12 attendees and needs 90 minutes. You do not know venue availability or facilities.','bo':'Harbour Room is available Saturday 14:00-16:00, with 16 seats and workbenches. You do not know the group size or duration.'},2:{'ari':'The supper has 8 diners and needs a vegan option. You do not know restaurant availability.','bo':'Cedar Cafe has a Thursday 18:30 table for 10 and a vegan menu. You do not know the group size or dietary requirements.'},3:{'ari':'The demo has 6 attendees and requires a projector. You do not know venue availability or equipment.','bo':'Studio B is free Friday 10:00-11:00, has 8 seats, but NO projector. You do not know attendance or equipment requirements.'}}[run]
  for actor in ACTORS:
   home=W/'private'/actor/'home'
   (home/'SOUL.md').write_text('You are '+actor.title()+' agent, helping '+actor.title()+'. Your operator authorizes sharing these synthetic facts for this current project: '+project_facts[actor]+' Only the room read, post, pass, and agree tools are enabled in this profile. Give an ordinary final answer for a useful room contribution; the gateway posts it automatically. If you have no new contribution, your entire final answer must be exactly NO_REPLY; this is native intentional silence, not a room message. The complete frozen context is already provided. Do not narrate a tool call or access private files. Follow the BotSesh contribution guidance.\n')
  for actor in ACTORS:ids[actor]=local(actor,op='join',invitation=fixture['invites'][actor],origin=ready['origin'])['participant_id']
  snap=control(op='snapshot',room_id=room)
  # Allow only the four canonical local fixture authors. No wildcard sender authority.
  humans=snap['messages'].get('participants',[])
  for actor in ACTORS:
   old=json.loads((W/'private'/actor/'home/plugin-data/botsesh/rooms.json').read_text())['rooms']
   cli(actor,'config','set','platforms.botsesh.extra.group_allowed_chats',json.dumps([room]))
   # Stable human participant IDs are obtained below from the real post response;
   # both agent IDs are already independently read back from redemption.
  question=control(op='message',actor=cases[run][0],room_id=room,text=cases[run][1])['message']
  human_id=question['authorParticipant']['id'];senders=[human_id,*ids.values()]
  for actor in ACTORS:
   cli(actor,'config','set','platforms.botsesh.extra.allowed_users',json.dumps(senders))
   log=open(E/f'run-{run}-{actor}-gateway.log','w');logs[actor]=log
   gateways[actor]=subprocess.Popen(['docker','exec',name(actor),PY,'-m','hermes_cli.main','gateway','run','--no-supervise','-v'],stdout=log,stderr=subprocess.STDOUT,text=True)
  (E/f'run-{run}-identity.json').write_text(json.dumps({'people':ready['people'],'agents':ids,'human_source':human_id,'question_number':question['number'],'source_copy_registration':True,'ownership_boundary':'two separate synthetic people/operator homes; host-issued invitation seats, not OAuth owner binding'},indent=2))
  deadline=time.monotonic()+2400;stable=None;last_count=0
  while time.monotonic()<deadline:
   if (E/'STOP.json').exists():raise RuntimeError((E/'STOP.json').read_text())
   if (E/'dry-error.txt').exists():raise RuntimeError((E/'dry-error.txt').read_text())
   snap=control(op='snapshot',room_id=room);(E/f'run-{run}-snapshot.json').write_text(json.dumps(snap,indent=2))
   messages=snap['messages']['messages'];agent_messages=[m for m in messages if m.get('authorParticipant',{}).get('id') in ids.values()]
   states={actor:local(actor,op='state',room_id=room) for actor in ACTORS}
   compact={actor:{'receipts':{k:{kk:vv for kk,vv in v.items() if kk in ('state','target','batch_targets','explicit_pass','explicit_agreement','explicit_post_calls','posted_reply','native_admission')} for k,v in state['receipts'].items()},'health':state['health']} for actor,state in states.items()}
   (E/f'run-{run}-native-state.json').write_text(json.dumps(compact,indent=2))
   busy=any(v.get('state') not in ('replied','passed','failed','completed_unread') for s in states.values() for v in s['receipts'].values())
   failed=[v for s in states.values() for v in s['receipts'].values() if v.get('state')=='failed']
   if failed:raise RuntimeError('Native failed turn; retained run '+str(run))
   if len(agent_messages)>8:raise RuntimeError('Conversation did not stay bounded; retained run '+str(run))
   if len(agent_messages)!=last_count:
    print('Run',run,'canonical agent posts:',len(agent_messages),flush=True);last_count=len(agent_messages);stable=None
   # Both agents must have handled an input and all posted peer inputs, not just
   # the initial question. Require actual terminal native receipts before idle.
   handled=all(s['receipts'] for s in states.values())
   latest_covered=all(all(str(m['number']) in s['receipts'] or m['number'] in s['handled'] for m in messages if m.get('authorParticipant',{}).get('id')!=ids[a]) for a,s in states.items())
   if (agent_messages or args.dry_run) and handled and not busy and latest_covered:
    stable=stable or time.monotonic()
    if time.monotonic()-stable>=20:break
   else:stable=None
   if any(g.poll() is not None for g in gateways.values()):raise RuntimeError('Native gateway exited unexpectedly')
   time.sleep(3)
  else:raise RuntimeError('Conversation native settlement timeout run '+str(run))
  if args.dry_run:
   captures=json.loads((E/'capture-invocation-count.json').read_text())
   assert dry_guard.invocations==captures['total_capture_invocations']==1
   assert len(snap['agreements'])==2 and len(messages)==1, snap['agreements']
   assert dry_counts=={'ari':2,'bo':2},dry_counts
  transcript='\n\n'.join(f"#{m['number']} {m.get('authorParticipant',{}).get('name',m.get('author','?'))}: {m['text']}" for m in messages)
  (E/f"{'dry' if args.dry_run else 'run'}-{run}-transcript.md").write_text(transcript+'\n')
  outcomes.append({'run':run,'agent_posts':len(agent_messages),'settled':True,'agent_ids':ids})
  (E/('dry-runs.json' if args.dry_run else 'runs.json')).write_text(json.dumps(outcomes,indent=2))
  for actor in ACTORS:
   (E/f'run-{run}-{actor}-stop.log').write_text(cli(actor,'gateway','stop'));gateways[actor].wait(timeout=45);logs[actor].close()
  gateways.clear();logs.clear();control(op='delete',room_id=room,run=run)
  print('Run',run,'finished; canonical transcript saved.',flush=True)
finally:
 # A failed peer must not close the other native HTTP client while a paid
 # action is still returning. Close intake first, drain only reserved work,
 # then observe native tool settlement before stopping either gateway.
 try:stop_after_drain(activity,lambda:None,timeout=270)
 except TimeoutError as exc:(E/'drain-timeout.json').write_text(json.dumps({'error':str(exc),'delivery':'unconfirmed'}))
 try:
  drain_deadline=time.monotonic()+30
  while gateways and rooms and time.monotonic()<drain_deadline:
   final_states={actor:local(actor,op='state',room_id=rooms[-1][1]) for actor in gateways}
   compact={actor:{key:{k:v for k,v in row.items() if k in ('state','target','explicit_pass','explicit_agreement','posted_reply')}
           for key,row in state['receipts'].items()} for actor,state in final_states.items()}
   (E/f'run-{rooms[-1][0]}-shutdown-native-state.json').write_text(json.dumps(compact,indent=2))
   if all(row.get('state') in ('replied','passed','failed','completed_unread') for state in final_states.values() for row in state['receipts'].values()):break
   time.sleep(.25)
  if gateways and rooms:
   (E/f'run-{rooms[-1][0]}-shutdown-snapshot.json').write_text(json.dumps(control(op='snapshot',room_id=rooms[-1][1]),indent=2))
 except Exception as exc:
  (E/'shutdown-observation-error.json').write_text(json.dumps({'error':type(exc).__name__,'delivery':'unconfirmed'}))
 for actor,g in gateways.items():
  if g.poll() is None:
   try:(E/f'failed-{actor}-stop.log').write_text(cli(actor,'gateway','stop','--yes'));g.wait(timeout=45)
   except Exception as exc:print('Owned gateway stop unconfirmed:',actor,type(exc).__name__,flush=True)
 for log in logs.values():log.close()
 if server.poll() is None:server.stdin.write('{"op":"stop"}\n');server.stdin.flush();server.stdin.close()
 try:server.wait(timeout=30)
 except subprocess.TimeoutExpired:print('Owned server stop unconfirmed',flush=True)
 server_error.close();relay.shutdown();relay.server_close()
 print('Cumulative model reservations:',len(ledger),flush=True)
