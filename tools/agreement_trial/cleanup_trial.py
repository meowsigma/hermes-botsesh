"""Remove only this task's exited synthetic fixture stores; no owner data."""
import datetime,json,re,shutil,socket,subprocess
from pathlib import Path
W=Path(__file__).resolve().parent;E=W/'agreement-evidence'
containers={};ports=[]
for actor in ('ari','bo'):
    name='botsesh-agents11e-'+actor+'-'+W.name
    raw=subprocess.check_output(['docker','top',name,'-eo','pid,comm'],text=True)
    commands=[line.split(maxsplit=1)[1].strip() for line in raw.splitlines()[1:]]
    assert set(commands)<={'docker-init','sleep'},(name,commands)
    containers[name]={'processes':commands,'only_inert_entrypoint':True}
    conf=json.loads((E/f'config-{actor}.json').read_text())
    ports.extend(int(p) for p in re.findall(r'127\.0\.0\.1:(\d+)',conf['model.base_url']))
for port in sorted(set(ports)):
    with socket.socket() as s:
        s.settimeout(2);assert s.connect_ex(('127.0.0.1',port))!=0,('relay still alive',port)
removed=[]
for root in [W/'private',*W.glob('capture-*-codex-home')]:
    assert root.parent.resolve()==W and not root.is_symlink()
    if root.exists():shutil.rmtree(root)
    assert not root.exists();removed.append(root.name)
ledger=json.loads((E/'model-ledger.json').read_text()) if (E/'model-ledger.json').exists() else []
result={'checked_at':datetime.datetime.now().astimezone().isoformat(),'native_gateways_and_server_absent':True,'private_fixture_homes_removed':removed,'codex_auth_read_or_copied':False,'owner_provider_profile_gateway_configuration_changed':False,'production_accounts_created':0,'relay_ports_verified_closed':sorted(set(ports)),'inert_containers':containers,'counted_calls':len(ledger),'no_further_inference':True}
(E/'cleanup.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
