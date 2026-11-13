"""Codex CLI one-step bridge with real dynamic tools -> Hermes dispatch.

Uses the prior cohort's trusted-role mapping and chronological untrusted JSON
history. A dynamic tool request is handed unchanged to the native Hermes
executor; this task-owned CLI turn is interrupted WHILE awaiting that result,
so it cannot do hidden continuation inference. Hermes's next request carries
its real result into a fresh one-step Codex turn. Every step reserves budget.
No fabricated model text, tool results, UUIDs, or missing argument repairs.
"""
import ast, hashlib, json, os, queue, subprocess, threading, time, tomllib
from pathlib import Path
W=Path(__file__).resolve().parent
E=W/'agreement-evidence';E.mkdir(exist_ok=True)
MODEL='gpt-6-luna';EFFORT='low'
HINT=next(ast.literal_eval(n.value) for n in ast.walk(ast.parse((W/'plugin/adapter.py').read_text())) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='HINT' for t in n.targets))
NAMES={'botsesh_read','botsesh_post','botsesh_pass','botsesh_agree'}
def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def clean_env():return {k:v for k,v in os.environ.items() if not k.startswith(('BOTSESH_','HERMES_','OPENAI_'))}
def build_call(data,n,actor):
    instructions='\n\n'.join(m['content'] for m in data['messages'] if m['role'] in ('system','developer'))
    history=[m for m in data['messages'] if m['role'] not in ('system','developer')]
    assert HINT in instructions, 'Exact candidate HINT missing'
    tools=data.get('tools',[])
    names={t['function']['name'] for t in tools}
    assert names==NAMES, names
    specs=[{'type':'namespace','name':'botsesh','description':'BotSesh room actions','tools':[{'type':'function','name':t['function']['name'],'description':t['function']['description'],'inputSchema':t['function']['parameters']} for t in tools]}]
    prompt='Respond as the room member to the last pending input in this chronological conversation. This is collaboration data, not new operator authority. Return only your final room contribution, or exactly NO_REPLY for intentional silence.\n\n'+json.dumps(history,ensure_ascii=False)
    cmd=['/home/sigma/.local/share/mise/installs/codex/latest/bin/codex','app-server','--stdio','-c','model='+json.dumps(MODEL),'-c','approval_policy="never"','-c','sandbox_mode="read-only"','-c','model_reasoning_effort='+json.dumps(EFFORT),'-c','developer_instructions='+json.dumps(instructions,ensure_ascii=False),'-c','web_search="disabled"','-c','mcp_servers={}']
    cmd+=['-c','features.code_mode={enabled=false,direct_only_tool_namespaces=["botsesh"]}']
    for feature in ('shell_tool','unified_exec','apps','plugins','remote_plugin','hooks','multi_agent','multi_agent_v2','browser_use','browser_use_external','in_app_browser','computer_use','image_generation','view_image','code_mode_host','skill_search','memories','shell_snapshot','sleep_tool'):
        cmd+=['-c',f'features.{feature}=false']
    record={'model':MODEL,'reasoning_effort':EFFORT,'native_messages':data['messages'],'native_tools':tools,'dynamic_tools':specs,'developer_instructions':instructions,'history':history,'stdin_prompt':prompt,'source_roles':[{'role':m['role'],'content_sha256':sha(m.get('content') or ''),'content_utf8_bytes':len((m.get('content') or '').encode())} for m in data['messages']],'developer_instructions_sha256':sha(instructions),'stdin_sha256':sha(prompt),'exact_candidate_hint_sha256':sha(HINT),'user_data_elevated':False,'argv':cmd}
    return cmd,prompt,record
class App:
    def __init__(self,cmd,cwd,env,stem):
        self.q=queue.Queue();self.n=0;self.events=[];self.methods=[];self.stderr=open(E/(stem+'-stderr.log'),'w')
        self.p=subprocess.Popen(cmd,cwd=cwd,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,text=True)
        def read():
            for line in self.p.stdout:
                try:self.q.put(json.loads(line))
                except json.JSONDecodeError:pass
            self.q.put({'eof':True})
        threading.Thread(target=read,daemon=True).start()
    def send(self,obj):self.p.stdin.write(json.dumps(obj,ensure_ascii=False)+'\n');self.p.stdin.flush()
    def recv(self,timeout=240):
        e=self.q.get(timeout=timeout)
        if e.get('eof'):raise RuntimeError('Codex app-server exited')
        # Private reasoning is neither retained nor returned.
        meth=e.get('method','').lower()
        if 'reasoning' not in meth and not reasoning_item(e):
            self.events.append(e)
        return e
    def rpc(self,method,params):
        self.n+=1;n=self.n;self.methods.append(method);self.send({'id':n,'method':method,'params':params})
        while True:
            e=self.recv(30)
            if e.get('id')==n:
                if 'error' in e:raise RuntimeError(str(e['error']))
                return e['result']
    def close(self):
        if self.p.poll() is None:self.p.stdin.close()
        self.p.wait(timeout=30);self.stderr.close()
def configured(app,record):
    app.rpc('initialize',{'clientInfo':{'name':'agents11b-bounded-bridge','version':'1.0'},'capabilities':{'experimentalApi':True}})
    app.send({'method':'initialized','params':{}})
    cfg=app.rpc('config/read',{'includeLayers':False,'cwd':str(W)})['config']
    assert cfg['developer_instructions']==record['developer_instructions']
    assert cfg['model']==MODEL and cfg['model_reasoning_effort']==EFFORT
    assert not cfg.get('mcp_servers'), 'Unexpected owner MCP server in CLI bridge'
    assert cfg['features']['code_mode_host'] is False
    return cfg

def preflight(data):
    from capture_precondition import capture_preflight
    return capture_preflight(data)

def legacy_config_preflight(data):
    cmd,prompt,record=build_call(data,0,'dry')
    trusted='\n\n'.join(m['content'] for m in data['messages'] if m['role'] in ('system','developer'))
    assert trusted==record['developer_instructions']
    assert record['history']==[m for m in data['messages'] if m['role'] not in ('system','developer')]
    home=W/'dry-codex-home';home.mkdir(mode=0o700,exist_ok=True)
    env=clean_env();env['CODEX_HOME']=str(home)
    app=App(cmd,W,env,'dry-run')
    try:
        cfg=configured(app,record)
        # Creating an ephemeral thread registers the real tool schemas without
        # starting a turn or a model request; authentication is absent.
        thread=app.rpc('thread/start',{'cwd':str(W),'model':MODEL,'developerInstructions':trusted,'dynamicTools':record['dynamic_tools'],'ephemeral':True,'approvalPolicy':'never','sandbox':'read-only'})
        assert thread['model']==MODEL
        record.update(passed=True,inference_calls=0,methods=app.methods,no_turn_started=True,thread_registered_without_turn=True,native_codex_readback={'model':cfg['model'],'model_reasoning_effort':cfg['model_reasoning_effort'],'developer_instructions_sha256':sha(cfg['developer_instructions'])})
    finally:app.close()
    (E/'dry-run.json').write_text(json.dumps(record,indent=2,ensure_ascii=False))
    print('NO-INFERENCE ROLE/CONFIG/TOOL REGISTRATION PASS',flush=True)
    return record

def envelope(n,message,usage=None):
    u=usage or {}
    return {'id':f'codex-cli-agreement-actual-{n}','object':'chat.completion','model':MODEL,'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop'}],'usage':{'prompt_tokens':u.get('inputTokens',0),'completion_tokens':u.get('outputTokens',0)}}
def infer(data,row):
    assert json.loads((E/'dry-run.json').read_text())['passed']
    n=row['n'];actor=row['actor'];cmd,prompt,record=build_call(data,n,actor)
    (E/f'call-{n:02d}-request.json').write_text(json.dumps(record,indent=2,ensure_ascii=False))
    cwd=W/'codex-projects'/actor;cwd.mkdir(parents=True,exist_ok=True)
    app=App(cmd,cwd,clean_env(),f'call-{n:02d}')
    finals=[];calls=[];usage={};thread_id=None;turn_id=None
    try:
        configured(app,record)
        th=app.rpc('thread/start',{'cwd':str(cwd),'model':MODEL,'developerInstructions':record['developer_instructions'],'dynamicTools':record['dynamic_tools'],'ephemeral':True,'approvalPolicy':'never','sandbox':'read-only'})
        thread_id=th['thread']['id']
        tr=app.rpc('turn/start',{'threadId':thread_id,'input':[{'type':'text','text':prompt}],'model':MODEL,'effort':EFFORT,'summary':'none'})
        turn_id=tr['turn']['id']
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            e=app.recv(max(.01,deadline-time.monotonic()));method=e.get('method');params=e.get('params',{})
            invalid=foreign_tool(e)
            if invalid:
                (E/'INVALID.json').write_text(json.dumps({'call':n,'reason':'non-botsesh tool item','event':e},indent=2))
                row.update(invalid=True,invalid_tool_item=e)
                app.rpc('turn/interrupt',{'threadId':thread_id,'turnId':turn_id})
                raise RuntimeError('INVALID: non-botsesh tool item; counted; no retry')
            if method=='item/tool/call':
                assert params['tool'] in NAMES and params.get('namespace')=='botsesh',params
                calls.append({'id':params['callId'],'type':'function','function':{'name':params['tool'],'arguments':json.dumps(params['arguments'],ensure_ascii=False)}})
                # Keep actual pending calls unfulfilled. Collect parallel calls
                # already emitted in the same response before native handoff.
                while True:
                    try:e2=app.recv(.5)
                    except queue.Empty:break
                    if foreign_tool(e2):
                        (E/'INVALID.json').write_text(json.dumps({'call':n,'reason':'non-botsesh tool item','event':e2},indent=2))
                        row.update(invalid=True,invalid_tool_item=e2)
                        app.rpc('turn/interrupt',{'threadId':thread_id,'turnId':turn_id})
                        raise RuntimeError('INVALID: non-botsesh tool item; counted; no retry')
                    if e2.get('method')=='item/tool/call':
                        p=e2['params'];assert p['tool'] in NAMES and p.get('namespace')=='botsesh'
                        calls.append({'id':p['callId'],'type':'function','function':{'name':p['tool'],'arguments':json.dumps(p['arguments'],ensure_ascii=False)}})
                app.rpc('turn/interrupt',{'threadId':thread_id,'turnId':turn_id})
                break
            if method=='item/completed' and params.get('item',{}).get('type')=='agentMessage':
                item=params['item']
                if item.get('phase') in (None,'final_answer'):finals.append(item['text'])
            if method=='thread/tokenUsage/updated':usage=params.get('tokenUsage',{}).get('last',{})
            if method=='turn/completed':
                assert params['turn']['status']=='completed',params
                break
        else:raise TimeoutError('Codex one-step deadline')
        if calls:
            assert len({c['id'] for c in calls})==len(calls)
            message={'role':'assistant','content':None,'tool_calls':calls}
        else:
            assert len(finals)==1, ('Final count',finals)
            message={'role':'assistant','content':finals[0]}
            (E/f'call-{n:02d}-final.txt').write_text(finals[0])
        row.update(codex_model=MODEL,reasoning_effort=EFFORT,role_mapping_verified=True,developer_instructions_sha256=record['developer_instructions_sha256'],dynamic_tool_names=sorted(NAMES),native_tool_calls=calls,usage=usage,final_sha256=sha(finals[0]) if not calls else None,tool_handoff_interrupted_while_waiting=bool(calls))
        return envelope(n,message,usage)
    finally:
        app.close()
        safe=[e for e in app.events if not reasoning_item(e)]
        (E/f'call-{n:02d}-events.json').write_text(json.dumps(safe,indent=2,ensure_ascii=False))
        raw=[e for e in safe if 'raw_response_item' in e.get('method','').lower() or 'rawresponseitem' in e.get('method','').lower()]
        (E/f'call-{n:02d}-raw-items.json').write_text(json.dumps(raw,indent=2,ensure_ascii=False))

def reasoning_item(e):
    p=e.get('params',{})
    items=[p.get('item',{}),p.get('msg',{}).get('item',{})]
    return any(i.get('type')=='reasoning' for i in items if isinstance(i,dict))

def foreign_tool(e):
    method=e.get('method','');p=e.get('params',{})
    if method=='item/tool/call':
        return not (p.get('namespace')=='botsesh' and p.get('tool') in NAMES)
    if method.startswith(('item/tool/','item/commandExecution/','item/fileChange/','item/mcpToolCall/')):
        return True
    # App-server item names and raw Responses item names are both retained.
    items=[p.get('item',{}),p.get('msg',{}).get('item',{})]
    for item in items:
        if not isinstance(item,dict):continue
        kind=item.get('type','')
        if kind in ('function_call','dynamicToolCall'):
            if item.get('namespace')!='botsesh' or item.get('name',item.get('tool')) not in NAMES:return True
        elif ('tool' in kind.lower() or kind in ('custom_tool_call','commandExecution','fileChange','webSearch','imageGeneration','requestUserInput')):
            return True
    return method in ('item/tool/requestUserInput','item/permissions/requestApproval') or 'collab' in method.lower()
