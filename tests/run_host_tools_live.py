"""Bounded real gateway + local model proof; no paid inference or live profile changes.

Usage: PYTHONPATH=<Hermes>:tests python tests/run_host_tools_live.py --server <source>
       --model-url http://127.0.0.1:18521 --out <task evidence directory>
The supplied model must be a local OpenAI-compatible server. Output is redacted
permission evidence; private fixture homes/tokens are deleted on cleanup.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.request import Request,urlopen
import unittest
import test_oauth_connect as connect_fixture


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--server',type=Path,required=True)
    p.add_argument('--model-url',required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    from urllib.parse import urlsplit
    u=urlsplit(args.model_url)
    if u.hostname not in ('127.0.0.1','localhost') or u.scheme!='http':p.error('Local model only')
    args.out.mkdir(parents=True,exist_ok=True)
    model=json.load(urlopen(args.model_url+'/v1/models'))['data'][0]['id']
    trace=[];phase=['setup']
    class Relay(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):self.forward()
        def do_POST(self):self.forward()
        def forward(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0'))) if self.command=='POST' else None
            if body:
                data=json.loads(body)
                if isinstance(data.get('messages'),list):
                    # Bound local output only; preserve native prompts/tools/transport.
                    data['max_tokens']=512
                    data['chat_template_kwargs']={'enable_thinking':False}
                    trace.append({'phase':phase[0],'tools':[x.get('function',{}).get('name',x.get('name')) for x in data.get('tools',[])],
                        'tool_results':[m.get('content','')[:1500] for m in data['messages'] if m.get('role')=='tool'],
                        'authored_calls':[c for m in data['messages'] if m.get('role')=='assistant' for c in m.get('tool_calls',[])]})
                    (args.out/'model-permission-trace.json').write_text(json.dumps(trace,indent=2))
                    body=json.dumps(data).encode()
            req=Request(args.model_url+self.path,data=body,method=self.command,headers={'Content-Type':'application/json'})
            try:
                with urlopen(req,timeout=240) as r:
                    self.send_response(r.status);self.send_header('Content-Type',r.headers.get('Content-Type','application/json'));self.end_headers()
                    while chunk:=r.read1(65536):self.wfile.write(chunk);self.wfile.flush()
            except Exception as e:
                self.send_error(502,type(e).__name__)
    relay=ThreadingHTTPServer(('127.0.0.1',0),Relay)
    threading.Thread(target=relay.serve_forever,daemon=True).start()
    plugin=Path(__file__).resolve().parents[1]
    os.environ['BOTSESH_OAUTH_CONNECT_SERVER_COMMAND']=json.dumps(['node',str(plugin/'tests/oauth-connect-server.mjs'),str(args.server.resolve()/'app.js')])
    class Fixture(unittest.TestCase):
        setUp=connect_fixture.NativeOAuthConnect.setUp
        control=connect_fixture.NativeOAuthConnect.control
        stop_server=connect_fixture.NativeOAuthConnect.stop_server
        connect=connect_fixture.NativeOAuthConnect.connect
        saved=connect_fixture.NativeOAuthConnect.saved
    f=Fixture();f.setUp();gateway=None;log=None
    try:
        f.env['PYTHONPATH']=str(Path(__import__('hermes_cli').__file__).resolve().parents[1])
        f.env={k:v for k,v in f.env.items() if not k.endswith(('_API_KEY','_TOKEN','_SECRET'))}
        room=f.ready['room_ids'][0];f.connect(requested=room)
        rooms,_=f.saved();pid=rooms[room]['participant_id']
        # Keep the real CLI's connect/credential writes. Change only this fixture's settings
        # via the shipped config command, never another user's configuration.
        f.env.update(PATH='/usr/bin:/bin',GATEWAY_MULTIPLEX_PROFILES='false',HERMES_DUMP_REQUESTS='0')
        settings={'model':{'default':model,'provider':'custom','base_url':f'http://127.0.0.1:{relay.server_port}/v1','api_mode':'chat_completions','context_length':65536},
            'platform_toolsets.botsesh':['terminal','botsesh'],'tools.tool_search':{'enabled':'off'},
            'agent.max_turns':5,'agent.api_max_retries':1,'agent.run_budget_seconds':240,
            'memory':{'memory_enabled':False,'user_profile_enabled':False},
            'gateway.multiplex_profiles':False,'plugins.entries.botsesh.settings.poll_seconds':5,
            'terminal':{'backend':'local','cwd':str(f.root)},
            'display.reasoning_effort':'none'}
        (f.home/'.env').write_text('OPENAI_API_KEY=local-fixture-not-a-credential\n')
        (f.home/'.env').chmod(0o600)
        for key,value in settings.items():
            r=subprocess.run([sys.executable,'-m','hermes_cli.main','config','set',key,json.dumps(value)],env=f.env,capture_output=True,text=True)
            if r.returncode:raise RuntimeError('fixture config failed: '+key+' '+r.stderr)
        (f.root/'owner-only-marker').write_text('Harmless owned ls fixture\n')
        (f.home/'SOUL.md').write_text('You are a concise room helper. Complete the current request, not earlier history. If a requested tool is unavailable, explain that limit in an ordinary final answer. If terminal is available, use it for an explicit shell-command request and report its actual result. Do not copy the request into a room post. The full frozen room context is already in this turn; no need to re-read it. For this shell check use an ordinary final answer, not botsesh_post or botsesh_pass.\n')
        log=open(args.out/'gateway.log','w')
        gateway=subprocess.Popen([sys.executable,'-m','hermes_cli.main','gateway','run','--no-supervise','-v'],env=f.env,cwd=f.root,stdout=log,stderr=subprocess.STDOUT)
        time.sleep(6)
        if gateway.poll() is not None:raise RuntimeError('scratch gateway failed to start; see gateway.log')
        results=[]
        for label,guest in [('guest',True),('owner',False)]:
            phase[0]=label
            q=f.control(op='message',room_id=room,guest=guest,text='@Fixture Please run `ls ~` once with terminal and tell me the real listing in your ordinary final answer. If terminal is unavailable, say tools are off for guests and stop. Do not call room read/post/pass tools for this check; the complete context is already provided. Do not copy my request or invent a listing.')['message']
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                snap=f.control(op='inspect_turn',room_id=room,participant_id=pid)
                delivery=next((d for d in snap['deliveries'] if d['number']==q['number']),None)
                if delivery and (delivery['completed_at'] is not None or delivery['failed_at'] is not None):break
                if gateway.poll() is not None:raise RuntimeError('scratch gateway exited')
                time.sleep(1)
            else:raise RuntimeError('native '+label+' turn timed out')
            item={'phase':label,'source_number':q['number'],'delivery':delivery,'messages':snap['messages']}
            results.append(item);(args.out/'live-result.json').write_text(json.dumps(results,indent=2))
            if delivery['failed_at'] is not None:raise RuntimeError('native '+label+' failed: '+str(delivery['failure_code']))
            # Wait for exact native retirement, not only a visible room answer.
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                local=json.loads((f.home/'plugin-data/botsesh/rooms.json').read_text())['rooms'][room]
                row=local.get('processing_receipts',{}).get(str(q['number']),{})
                if row.get('state') in ('replied','passed','completed_unread'):break
                time.sleep(.5)
        guest_requests=[r for r in trace if r['phase']=='guest']
        room_tools={'botsesh_read','botsesh_post','botsesh_pass'}
        assert guest_requests and any(set(r['tools'])==room_tools for r in guest_requests) and all(set(r['tools'])<=room_tools for r in guest_requests),guest_requests
        guest=results[0]
        reply=next(m['text'] for m in guest['messages'] if m['id']==guest['delivery']['answer_message_id'])
        assert 'tools' in reply.lower() and ('off' in reply.lower() or 'guest' in reply.lower()),reply
        owner_requests=[r for r in trace if r['phase']=='owner']
        calls=[c for r in owner_requests for c in r['authored_calls']]
        assert any(c.get('function',{}).get('name')=='terminal' and 'ls ~' in c['function'].get('arguments','') for c in calls),calls
        assert any('owner-only-marker' in x for r in owner_requests for x in r['tool_results']),owner_requests
        owner=results[1]
        owner_reply=next(m['text'] for m in owner['messages'] if m['id']==owner['delivery']['answer_message_id'])
        assert 'owner-only-marker' in owner_reply,owner_reply
        summary={'boundary':'real native CLI OAuth + scratch gateway automatic wake + local Qwen inference; no paid inference',
            'model':model,'guest_toolsets':[r['tools'] for r in guest_requests],'guest_replied':True,
            'owner_terminal_ls_executed':True,'scratch_listing_marker_seen':True,'model_requests':len(trace)}
        (args.out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
    finally:
        if gateway and gateway.poll() is None:
            gateway.terminate()
            try:gateway.wait(timeout=30)
            except subprocess.TimeoutExpired:gateway.kill();gateway.wait()
        if log:log.close()
        relay.shutdown();relay.server_close()
        clean=f.doCleanups()
        if not clean or f.root.exists():raise RuntimeError('owned fixture cleanup was not confirmed')
        if (args.out/'summary.json').exists():
            summary=json.loads((args.out/'summary.json').read_text());summary['fixture_home_removed']=True
            (args.out/'summary.json').write_text(json.dumps(summary,indent=2))

if __name__=='__main__':main()
