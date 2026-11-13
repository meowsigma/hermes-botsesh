"""Prepare the two task-owned native runtimes; no model calls."""
import os, subprocess
from pathlib import Path
W=Path(__file__).resolve().parent
PY='/home/sigma/.hermes/installs/27164fdd4ba4535f/environments/1b89bc3d9e1d422484fdeacc775984d9/venv/bin/python'
CORE='/home/sigma/.hermes/hermes-agent'
assert Path(PY).exists()
DEPS=(W/'server/node_modules').resolve()
assert (DEPS/'express/package.json').exists()
for actor in ('ari','bo'):
    root=W/'private'/actor
    for sub in ('os','home','run','tmp','bin'):(root/sub).mkdir(parents=True,exist_ok=True,mode=0o700)
    launcher=root/'bin/hermes'
    launcher.write_text('#!/bin/sh\nexec '+PY+' -m hermes_cli.main "$@"\n');launcher.chmod(0o700)
    env={'HOME':f'/trial/private/{actor}/os','HERMES_HOME':f'/trial/private/{actor}/home','XDG_RUNTIME_DIR':f'/trial/private/{actor}/run','TMPDIR':f'/trial/private/{actor}/tmp','PYTHONPATH':CORE,'PATH':f'/trial/private/{actor}/bin:/usr/local/bin:/usr/bin:/bin','HERMES_SKIP_UPDATE_CHECK':'1','HERMES_NO_AUTO_UPDATE':'1','HERMES_DISABLE_LAZY_INSTALLS':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1','GATEWAY_MULTIPLEX_PROFILES':'false'}
    cmd=['docker','run','-d','--rm','--init','--name','botsesh-agents11e-'+actor+'-'+W.name,'--network','host','--user',f'{os.getuid()}:{os.getgid()}','--mount',f'type=bind,src={W},dst=/trial','--mount',f'type=bind,src={(W/"stock-core").resolve()},dst={CORE},readonly','--mount',f'type=bind,src={DEPS},dst=/trial/server/node_modules,readonly']
    for p in ('/home/sigma/.hermes/installs','/home/sigma/.hermes/tools'):
        cmd+=['--mount',f'type=bind,src={p},dst={p},readonly']
    for k,v in env.items():cmd+=['-e',k+'='+v]
    cmd+=['--workdir',f'/trial/private/{actor}/os','node:24-bookworm','sleep','9000']
    subprocess.run(cmd,check=True,capture_output=True)
    result=subprocess.run(['docker','exec',cmd[cmd.index('--name')+1],PY,'-c','import pydantic_core, openai; print("native dependencies ready")'],capture_output=True,text=True,check=True)
    print(actor,result.stdout.strip())
