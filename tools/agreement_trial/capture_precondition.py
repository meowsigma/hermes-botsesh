"""Hard zero-inference precondition using the EXACT live command builder.
Only the provider route/catalog are overridden; isolated HOME has no auth.
"""
import difflib, http.server, json, os, queue, re, threading, time, uuid
import hashlib
from pathlib import Path

CAPTURE_CATALOG_SHA256 = '9253c96212be2ac242177bf13f0d0dc64017dbc9f485ac38e6cc1136a4e0e79d'


def external_catalog():
    """Trial-only immutable input, deliberately not shipped with the plugin."""
    raw = os.environ.get('BOTSESH_TRIAL_MODEL_CATALOG', '')
    catalog = Path(raw)
    if not raw or not catalog.is_absolute() or not catalog.is_file():
        raise RuntimeError('Explicit absolute BOTSESH_TRIAL_MODEL_CATALOG required before capture; see the trial README.')
    if hashlib.sha256(catalog.read_bytes()).hexdigest() != CAPTURE_CATALOG_SHA256:
        raise RuntimeError('Trial catalog differs from the retained exact input; no capture started.')
    return catalog.resolve(strict=True)


def capture_preflight(data):
    # Fail before opening a server, making a private home or launching Codex.
    catalog = external_catalog()
    from agreement_bridge import W,E,MODEL,EFFORT,NAMES,App,build_call,configured,sha
    stem='capture-'+str(uuid.uuid4())
    (E/(stem+'-invocation.json')).write_text(json.dumps({'stem':stem,'entered':time.time()}))
    cmd,prompt,record=build_call(data,0,'dry')
    captured=[]
    class Capture(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
            captured.append({'path':self.path,'body':body})
            self.send_response(400);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(b'{"error":{"message":"local capture only","type":"invalid_request_error"}}')
        def do_GET(self): self.send_error(404)
        def log_message(self,*args): pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Capture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    home=W/(stem+'-codex-home');home.mkdir(mode=0o700,exist_ok=True)
    assert not (home/'auth.json').exists()
    overrides=['-c','model_provider="cap"','-c',f'model_providers.cap={{name="cap",base_url="http://127.0.0.1:{server.server_port}/v1",wire_api="responses",requires_openai_auth=false,request_max_retries=0,stream_max_retries=0}}','-c','model_catalog_json='+json.dumps(str(catalog))]
    env={'PATH':os.environ['PATH'],'HOME':str(home),'CODEX_HOME':str(home)}
    app=App(cmd+overrides,W,env,stem)
    try:
        cfg=configured(app,record)
        th=app.rpc('thread/start',{'cwd':str(W),'model':MODEL,'developerInstructions':record['developer_instructions'],'dynamicTools':record['dynamic_tools'],'ephemeral':True,'approvalPolicy':'never','sandbox':'read-only'})
        app.rpc('turn/start',{'threadId':th['thread']['id'],'input':[{'type':'text','text':prompt}],'model':MODEL,'effort':EFFORT,'summary':'none'})
        deadline=time.monotonic()+30
        while not captured and time.monotonic()<deadline:
            try:app.recv(1)
            except queue.Empty:pass
    finally:
        app.close();server.shutdown();server.server_close()
        (E/(stem+'-responses.json')).write_text(json.dumps(captured,indent=2,ensure_ascii=False))
        (E/(stem+'-events.json')).write_text(json.dumps(app.events,indent=2,ensure_ascii=False))
        invocations=sorted(p.name for p in E.glob('capture-*-invocation.json'))
        (E/'capture-invocation-count.json').write_text(json.dumps({'total_capture_invocations':len(invocations),'invocations':invocations,'one_shot':len(invocations)==1},indent=2))
    assert len(invocations)==1,invocations
    assert len(captured)==1 and captured[0]['path']=='/v1/responses',captured
    body=captured[0]['body']
    alltools=[t for item in body['input'] if item.get('type')=='additional_tools' for t in item['tools']]
    namespaces=[t for t in alltools if t.get('name')=='botsesh']
    assert len(namespaces)==1
    direct=namespaces[0]['tools'];assert {t['name'] for t in direct}==NAMES and len(direct)==4
    assert all(t['type']=='function' for t in direct)
    execs=[t for ns in alltools for t in ns.get('tools',[]) if t.get('name')=='exec']
    nested=re.findall(r'botsesh_\w+\s*\(args',json.dumps(execs))
    assert not nested,nested
    canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    native={t['function']['name']:t['function']['parameters'] for t in data['tools']}
    actual={t['name']:t['parameters'] for t in direct}
    diff=''.join(difflib.unified_diff(json.dumps(native,indent=2,sort_keys=True).splitlines(True),json.dumps(actual,indent=2,sort_keys=True).splitlines(True),fromfile='native-Hermes',tofile='captured-Codex'))
    (E/'schema-diff.txt').write_text(diff)
    def normalize(value):
        if isinstance(value,list):return [normalize(v) for v in value]
        if not isinstance(value,dict):return value
        result={k:normalize(v) for k,v in value.items() if k not in ('minimum','minLength','maxLength','const')}
        if 'const' in value:
            assert 'enum' not in value,'Ambiguous const + enum projection'
            result['enum']=[normalize(value['const'])]
        return result
    normalized_native=normalize(native);normalized_actual=normalize(actual)
    (E/'normalized-native-schemas.json').write_text(json.dumps(normalized_native,indent=2,sort_keys=True))
    (E/'normalized-captured-schemas.json').write_text(json.dumps(normalized_actual,indent=2,sort_keys=True))
    assert canonical(normalized_native)==canonical(normalized_actual),'Schemas differ beyond allowlisted projection'
    dev=[item for item in body['input'] if item.get('role')=='developer' and item.get('type')!='additional_tools']
    texts=[c.get('text','') for item in dev for c in item.get('content',[]) if c.get('type') in ('input_text','text')]
    assert record['developer_instructions'] in texts,'Developer instructions missing from captured body'
    literal=next(t for t in texts if t==record['developer_instructions'])
    assert sha(literal)==record['developer_instructions_sha256']
    result={**record,'passed':True,'inference_calls':0,'local_capture_requests':len(captured),'captured_path':captured[0]['path'],'live_argv':cmd,'capture_only_overrides':overrides,'capture_argv':cmd+overrides,'auth_absent':True,'native_schema_sha256':sha(canonical(native)),'captured_schema_sha256':sha(canonical(actual)),'captured_developer_sha256':sha(literal),'direct_function_names':sorted(actual),'exec_nested_declarations':len(nested),'visible_namespaces':[t.get('name') for t in alltools],'code_host_enabled':False,'schema_projection':['drop minimum','drop minLength','drop maxLength','const -> enum singleton'],'normalized_schema_sha256':sha(canonical(normalized_native)),'precondition_d_executed_and_passed':True,'total_capture_invocations':len(invocations),'unique_capture_stem':stem,'capture_body_file':stem+'-responses.json'}
    (E/'dry-run.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print('HARD PRECONDITION PASS: exact live argv, 4 direct functions, allowlisted schema equality, 0 exec-nested, developer SHA matches; one capture; 0 inference',flush=True)
    return result
