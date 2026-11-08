"""Repeatable real declared-OAuth smoke; run with the prepared Hermes Python.

No production traffic, model/gateway startup, core patches, credential doubles,
TLS exceptions or private Hermes API calls. All private state is removed on exit.
The stock working-tree plugin is copied unchanged into the native user-plugin
installation directory and admitted with `hermes plugins enable` (not catalog
installation, which would fetch different published bytes). Negative MCP probes
use the installed plugin's public OAuthClient.rpc and core credential custody.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

PLUGIN = Path(__file__).resolve().parents[1]
WORKSPACE = PLUGIN.parent
HERMES = Path('/home/sigma/.hermes/hermes-agent')
ORIGIN = 'https://botsesh.com'


def probe(operation, room_id):
    """Only public plugin APIs; real native credentials remain inside core."""
    installed = Path(os.environ['HERMES_HOME']) / 'plugins/botsesh'
    spec = importlib.util.spec_from_file_location('declared_smoke_installed', installed / '__init__.py',
                                                 submodule_search_locations=[str(installed)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    module = __import__(spec.name + '.oauth', fromlist=['OAuthClient'])
    error_module = __import__(spec.name + '.client', fromlist=['BotSeshError'])
    try:
        value = module.OAuthClient().rpc({'origin': ORIGIN}, operation,
                                        {'room_id': room_id} if room_id else {})
    except error_module.BotSeshError as exc:
        print(json.dumps({'denied': True, 'code': exc.code, 'status': exc.status}))
    else:
        if operation == 'list_rooms':
            print(json.dumps({'denied': False, 'rooms': value['rooms']}))
        else:
            print(json.dumps({'denied': False}))


class Relay(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(30)
        header = bytearray()
        while b'\r\n\r\n' not in header and len(header) < 16384:
            chunk = self.request.recv(1)
            if not chunk:
                return
            header.extend(chunk)
        first = bytes(header).split(b'\r\n')[0]
        if first != b'CONNECT botsesh.com:443 HTTP/1.1':
            self.server.denied += 1
            self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
            return
        self.server.allowed += 1
        with socket.create_connection(('127.0.0.1', self.server.tls_port), timeout=10) as upstream:
            self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            while True:
                ready, _, _ = select.select([self.request, upstream], [], [], 30)
                if not ready:
                    break
                for source in ready:
                    try:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.request else self.request).sendall(data)
                    except (ConnectionResetError, BrokenPipeError):
                        return


def run(result_path):
    import httpx
    import yaml
    result = {'schema': 1, 'ok': False, 'checks': {}, 'inference_calls': 0,
              'gateway_started': False, 'production_network': False,
              'installation_method': 'unchanged local user-plugin copy + native enable/show',
              'negative_probe_method': 'installed public OAuthClient.rpc; native credentialed request',
              'private_state_removed': False}
    stage = 'setup'
    private = Path(tempfile.mkdtemp(prefix='native-declared-', dir=os.environ.get('TMPDIR')))
    private.chmod(0o700)
    server = proxy = auth = None
    handles = []
    outputs = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        result['checks'][name] = True

    def safe_json(output):
        for line in reversed(output.splitlines()):
            try:
                return json.loads(line)
            except (ValueError, TypeError):
                continue
        raise AssertionError('command_json_missing')

    try:
        home, os_home, empty = private / 'hermes-home', private / 'os-home', private / 'empty-path'
        for path in (home, os_home, empty, private / 'runtime'):
            path.mkdir(mode=0o700)
        # Explicit allowlist: no inherited agent/session/provider keys or profile.
        env = {'HOME': str(os_home), 'HERMES_HOME': str(home), 'PATH': str(empty),
               'PYTHONPATH': str(HERMES), 'PYTHONNOUSERSITE': '1', 'PYTHONUNBUFFERED': '1',
               'TERM': 'dumb', 'LANG': 'C.UTF-8', 'TMPDIR': str(private),
               'XDG_RUNTIME_DIR': str(private / 'runtime'),
               'XDG_CONFIG_HOME': str(private / 'xdg-config'),
               'BOTSESH_OAUTH_CLIENT_ID': ORIGIN + '/oauth/clients/hermes.json'}
        check('clean_no_node_cli_environment', not shutil.which('node', path=env['PATH']) and
              not shutil.which('nodejs', path=env['PATH']) and
              not any(k.startswith('HERMES_SESSION') for k in env))
        git=shutil.which('git')
        result['hermes_source_head']=subprocess.check_output([git,'rev-parse','HEAD'],cwd=HERMES,text=True).strip()
        oauth_diff=subprocess.run([git,'diff','--quiet','21ae0e12','--','hermes_cli/plugin_oauth.py',
            'hermes_cli/auth_oauth_pkce_plugin.py','hermes_cli/plugin_provider_requests.py'],cwd=HERMES)
        check('native_oauth_apis_match_upstream_21ae0e12',oauth_diff.returncode==0)
        manifest_bytes = (PLUGIN / 'plugin.yaml').read_bytes()
        result['stock_manifest_sha256']=hashlib.sha256(manifest_bytes).hexdigest()
        manifest = yaml.safe_load(manifest_bytes)
        # Reuse prepared *dependency-only* material in a private copy. No shared
        # profile/config/auth files are mounted or modified. Explicit native
        # admission still resolves normally, offline, against its own cache.
        cache_source = HERMES.parent/'cache/uv'
        cache_target = home/'cache/uv'
        cache_target.parent.mkdir(parents=True)
        subprocess.run([shutil.which('cp'), '-a', '--reflink=auto', str(cache_source), str(cache_target)], check=True)
        install_key = hashlib.sha256(str(HERMES.resolve()).encode()).hexdigest()[:16]
        prepared_pm = HERMES.parent/'installs'/install_key/'pm-runtime'
        if prepared_pm.is_dir():
            shutil.copytree(prepared_pm, home/'installs'/install_key/'pm-runtime', symlinks=True)
        # PM verifies locked build-wheel hashes. Fetch original public wheel
        # archives (never repackage or alter a lock/hash); package bootstrap is
        # separate from BotSesh traffic and uses normal system trust.
        wheelhouse=private/'build-wheelhouse'; wheelhouse.mkdir()
        needed={'setuptools':'83.0.0','wheel':'0.48.0','packaging':'26.0'}
        with httpx.Client(trust_env=False,timeout=30) as package_http:
            for name, version in needed.items():
                metadata=package_http.get(f'https://pypi.org/pypi/{name}/{version}/json')
                metadata.raise_for_status()
                candidate=next(item for item in metadata.json()['urls'] if item['filename'].endswith('-py3-none-any.whl'))
                artifact=package_http.get(candidate['url']); artifact.raise_for_status()
                check('original_build_wheel_hash_'+name,hashlib.sha256(artifact.content).hexdigest()==candidate['digests']['sha256'])
                (wheelhouse/candidate['filename']).write_bytes(artifact.content)
        simple=wheelhouse/'simple'; simple.mkdir()
        for name in needed:
            page=simple/name; page.mkdir()
            links=[f'<a href="../../{p.name}">{p.name}</a>' for p in wheelhouse.glob(name+'-*.whl')]
            (page/'index.html').write_text('\n'.join(links))
        env.update(HERMES_DISABLE_LAZY_INSTALLS='1', UV_INDEX_URL=simple.as_uri(),
                   PIP_INDEX_URL=simple.as_uri(), UV_FIND_LINKS=str(wheelhouse))
        result['dependency_preparation']='private prepared PM/cache copy + real cached build wheels; local file package index'
        check('stock_https_manifest', manifest['oauth'][0]['hosts'] == ['botsesh.com'] and
              manifest['oauth'][0]['scopes'] == ['botsesh:rooms'] and
              manifest['oauth'][0]['authorize_params']['hermes_declared'] == 'v1')
        # Generate only an ephemeral CA and a leaf for the unchanged declared host.
        openssl = shutil.which('openssl')
        key, cert, ca = private / 'server.key', private / 'server.pem', private / 'ca.pem'
        ext = private / 'leaf.ext'
        ext.write_text('subjectAltName=DNS:botsesh.com\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
        commands = [
            [openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(private/'ca.key'),
             '-out', str(ca), '-days', '1', '-subj', '/CN=BotSesh isolated proof CA', '-addext', 'basicConstraints=critical,CA:TRUE',
             '-addext','keyUsage=critical,keyCertSign,cRLSign'],
            [openssl, 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(private/'leaf.csr'), '-subj', '/CN=botsesh.com'],
            [openssl, 'x509', '-req', '-in', str(private/'leaf.csr'), '-CA', str(ca), '-CAkey', str(private/'ca.key'),
             '-CAcreateserial', '-out', str(cert), '-days', '1', '-extfile', str(ext)],
        ]
        for command in commands:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        node = shutil.which('node')
        server_log = (private / 'server-private.log').open('w+')
        handles.append(server_log)
        server_env = {'PATH': str(empty), 'HOME': str(os_home), 'TMPDIR': str(private), 'NODE_ENV': 'test'}
        server = subprocess.Popen([node, str(PLUGIN/'tests/native-declared-https-server.mjs'),
            str(WORKSPACE/'server/app.js'), str(private/'server-data'), str(key), str(cert)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=server_log, text=True, env=server_env)
        def server_read():
            ready, _, _ = select.select([server.stdout], [], [], 20)
            if not ready:
                raise AssertionError('server_control_timeout')
            for _ in range(20):
                line = server.stdout.readline()
                if not line:
                    raise AssertionError('server_control_closed')
                try:
                    return json.loads(line)
                except ValueError:
                    server_log.write(line)
            raise AssertionError('server_control_not_json')
        ready = server_read()
        check('actual_local_https_server_started', ready.get('ready') is True)
        proxy = Relay(('127.0.0.1', 0), Tunnel)
        proxy.tls_port, proxy.allowed, proxy.denied = ready['port'], 0, 0
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        proxy_url = 'http://127.0.0.1:' + str(proxy.server_address[1])
        env.update(HTTPS_PROXY=proxy_url, NO_PROXY='localhost,127.0.0.1', SSL_CERT_FILE=str(ca))
        context = ssl.create_default_context(cafile=str(ca))
        with httpx.Client(proxy=proxy_url,verify=context,trust_env=False,timeout=10) as tls_probe:
            cimd=tls_probe.get(ORIGIN+'/oauth/clients/hermes.json')
        check('trusted_local_https_stock_cimd_readback',cimd.status_code==200 and
              cimd.json()['client_id']==env['BOTSESH_OAUTH_CLIENT_ID'])
        with socket.create_connection(proxy.server_address,timeout=5) as forbidden:
            forbidden.sendall(b'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n')
            check('proxy_denies_non_fixture_hosts',forbidden.recv(4096).startswith(b'HTTP/1.1 403'))
        def inspect():
            server.stdin.write(json.dumps({'op':'inspect'})+'\n'); server.stdin.flush()
            return server_read()
        def cli(*args, ok=True, extra=None, timeout=55):
            process = subprocess.run([sys.executable, '-m', 'hermes_cli.main', *args], env={**env, **(extra or {})},
                cwd=private, capture_output=True, text=True, timeout=timeout, input='')
            output = process.stdout + '\n' + process.stderr
            outputs.append(output)
            if ok and process.returncode:
                if stage == 'native_enable':
                    # Pre-account admission contains no grant, browser state or fixture identity.
                    print(output, file=sys.stderr)
                # Keep all diagnostics private and return only an allowlisted failure category.
                (private / 'last-cli-private.log').write_text(output)
                raise AssertionError('cli_nonzero_' + args[0] + '_' + (args[1] if len(args)>1 else ''))
            return process.returncode, output
        # Copy stock working bytes, not the published catalog version or test rewrites.
        installed = home/'plugins/botsesh'
        shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns('.git','__pycache__','.pytest_cache','node_modules'))
        check('installed_manifest_byte_identical', (installed/'plugin.yaml').read_bytes() == manifest_bytes)
        stage = 'native_enable'
        cli('plugins','enable','botsesh')
        _, shown = cli('plugins','show','botsesh')
        check('stock_plugin_native_admission_and_show', 'botsesh' in shown.lower())
        config = yaml.safe_load((home/'config.yaml').read_text())
        check('stock_plugin_enabled_readback', 'botsesh' in config.get('plugins',{}).get('enabled',[]))
        stage = 'no_signin_contract'
        before = inspect()['audit']['authorizationRequests']
        rc, missing = cli('botsesh','connect','--no-start',ok=False)
        if 'variable Hermes uses itself' in missing and 'invalid oauth:' in missing:
            # Preserve the real native blocker rather than changing the stock
            # manifest, clearing a core registry, or calling a private auth API.
            master, slave=pty.openpty()
            attempted=subprocess.run([sys.executable,'-m','hermes_cli.main','auth','add','botsesh:rooms',
                '--no-browser','--timeout','5'],env=env,cwd=private,stdin=slave,
                capture_output=True,text=True,timeout=20)
            os.close(slave); os.close(master)
            auth_failure=attempted.stdout+'\n'+attempted.stderr
            result['native_auth_add_attempted']=True
            result['native_auth_add_returncode']=attempted.returncode
            result['native_auth_terminal_stdin']=True
            result['native_auth_clean_human_context']=True
            result['failure_detail']=("Stock kind: platform manifest injects BOTSESH_OAUTH_CLIENT_ID into "
                "OPTIONAL_ENV_VARS; declared OAuth then rejects the same name as Hermes-owned.")
            check('native_auth_add_reproduces_platform_env_collision', attempted.returncode!=0 and
                  'BOTSESH_OAUTH_CLIENT_ID is a variable Hermes uses itself' in auth_failure)
            blocked=inspect()
            check('blocked_auth_no_authorization_network',blocked['audit']['authorizationRequests']==before)
            result['audit']={k:v for k,v in blocked['audit'].items() if k!='mcp'}
            result['audit']['mcp_counts']={}
            result['proxy']={'allowed_local_tunnels':proxy.allowed,'denied_foreign_tunnels':proxy.denied}
            result['blocked_requirements']=['PKCE callback/code exchange','connect multiroom','status/rooms/seats',
                'safe rename','native public refresh','foreign/revoked MCP denial','shared disconnect','native logout']
            raise AssertionError('native_platform_declared_oauth_env_collision')
        check('connect_without_grant_never_starts_signin', rc != 0 and 'hermes auth add botsesh:rooms' in missing and
              inspect()['audit']['authorizationRequests'] == before)
        rc, refusal = cli('auth','add','botsesh:rooms','--no-browser','--timeout','5',ok=False,
                          extra={'HERMES_SINGLE_QUERY_SESSION':'1','HERMES_SESSION_PLATFORM':'botsesh'})
        check('real_agent_context_refuses_signin', rc != 0 and 'own terminal' in refusal and
              inspect()['audit']['authorizationRequests'] == before)
        stage = 'create_private_local_accounts'
        client_options = {'proxy':proxy_url,'verify':context,'trust_env':False,'follow_redirects':False,'timeout':20}
        with httpx.Client(**client_options) as owner, httpx.Client(**client_options) as foreign:
            def post(client,path,body):
                return client.post(ORIGIN+path, json=body, headers={'Origin':ORIGIN,'X-Board-Request':'1'})
            for index, client in enumerate((owner,foreign)):
                response = post(client,'/api/account/register',{'username':'native-smoke-'+str(index),
                    'password':base64.urlsafe_b64encode(os.urandom(32)).decode()})
                check('local_account_'+str(index)+'_created', response.status_code == 201)
                check('local_account_'+str(index)+'_readback', client.get(ORIGIN+'/api/account').status_code == 200)
            def room(client,name):
                response=post(client,'/api/rooms',{'name':name,'displayName':'Local Host','password':'isolated-room-password'})
                if response.status_code != 201:
                    raise AssertionError('local_room_creation')
                return response.json()['room']['id']
            roots=[room(owner,'Native selected one'),room(owner,'Native selected two')]
            outside=room(foreign,'Native foreign room')
            stage = 'native_human_auth_pkce'
            auth_log_path=private/'auth-private.log'
            auth_log=auth_log_path.open('w+'); handles.append(auth_log)
            master, slave = pty.openpty()
            auth=subprocess.Popen([sys.executable,'-u','-m','hermes_cli.main','auth','add','botsesh:rooms',
                '--no-browser','--timeout','60'], env=env, cwd=private, stdin=slave, stdout=auth_log, stderr=auth_log, text=True)
            os.close(slave)
            authorize=None
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                text=auth_log_path.read_text()
                match=re.search(r'https://botsesh\.com/oauth/authorize\?[^\s\x1b]+',text)
                if match:
                    authorize=match.group(); break
                if auth.poll() is not None:
                    break
                time.sleep(.1)
            check('native_auth_add_printed_authorize_url', bool(authorize))
            query=parse_qs(urlsplit(authorize).query)
            check('native_authorize_declared_pkce', query.get('code_challenge_method')==['S256'] and
                query.get('hermes_declared')==['v1'] and query.get('resource')==[ORIGIN+'/mcp'] and
                query.get('scope')==['botsesh:rooms'])
            response=owner.get(authorize,headers={'Sec-Fetch-Mode':'navigate'})
            if response.status_code==303:
                response=owner.get(response.headers['location'])
            check('actual_consent_multiselect', response.status_code==200 and
                  'name="selected_room_ids"' in response.text and 'type="checkbox"' in response.text)
            def field(name):
                match=re.search(r'name="'+name+r'" value="([^"]+)"',response.text)
                if not match:
                    raise AssertionError('consent_field_missing_'+name)
                return html.unescape(match.group(1))
            consent={name:field(name) for name in ('consent_id','csrf','review_id')}
            consent.update(decision='allow',selected_room_ids=roots,agent_name='yes',room_management='yes',hermes_dms='yes')
            approved=owner.post(ORIGIN+'/api/agent/room-consent',data=consent,headers={'Origin':ORIGIN})
            check('actual_reviewed_consent_approved', approved.status_code==302)
            callback=approved.headers['location']
            target=urlsplit(callback)
            check('literal_loopback_callback', target.scheme=='http' and target.hostname=='127.0.0.1' and target.path=='/callback')
            with httpx.Client(trust_env=False,timeout=10) as loopback:
                returned=loopback.get(callback)
            check('native_callback_accepted', returned.status_code==200)
            auth.wait(timeout=30); os.close(master)
            check('human_context_native_auth_add_completed', auth.returncode==0 and 'Signed in' in auth_log_path.read_text())
            auth=None
            auth_store=home/'auth.json'
            check('native_auth_store_private_permissions', auth_store.stat().st_mode & 0o777 == 0o600)
            # Read only grant presence/metadata, never retrieve or reuse raw tokens.
            grant_metadata=json.loads(auth_store.read_text()).get('plugin_oauth',{})
            check('native_grant_present', 'botsesh:rooms' in grant_metadata)
            del grant_metadata
            _, auth_status=cli('auth','status','botsesh:rooms')
            check('native_auth_status_logged_in', 'logged in' in auth_status)
            audit=inspect()['audit']
            check('actual_native_pkce_code_exchange', audit['exchanges']==1 and audit['pkceVerified']==1 and
                  audit['tokenResourcesOmitted']==1 and audit['tokenStatuses']==[200])
            stage = 'native_connect_multiroom'
            cli('botsesh','connect','--no-start')
            registry_path=home/'plugin-data/botsesh/rooms.json'
            registry=json.loads(registry_path.read_text())
            check('two_selected_roots_connected', set(registry['rooms'])==set(roots) and
                  all(v['active'] and v['auth_version']==2 for v in registry['rooms'].values()))
            config=yaml.safe_load((home/'config.yaml').read_text())
            check('both_selected_roots_allowed_in_native_config',
                  set(config['platforms']['botsesh']['extra']['group_allowed_chats'])==set(roots))
            snapshot=inspect()
            check('declared_initialized_default_profile', len(snapshot['declared'])==1 and
                  snapshot['declared'][0]=={'profile':'default','initialized':1})
            _, rooms_output=cli('botsesh','rooms')
            check('native_rooms_lists_two', {r['room_id'] for r in safe_json(rooms_output)['rooms']}==set(roots))
            _, status_output=cli('botsesh','status','--json')
            status=safe_json(status_output)
            check('native_status_both_alive_gateway_not_started', len(status['rooms'])==2 and
                  all(r['alive'] for r in status['rooms']) and status['gateway']['running'] is False)
            for rid in roots:
                _, seats=cli('botsesh','seats','--room',rid)
                check('native_seats_'+str(roots.index(rid)), 'current' in seats and 'default' in seats)
            stage = 'safe_native_rename'
            original={m['room_id']:m['participant_id'] for m in snapshot['memberships']}
            cli('botsesh','rename','native-renamed','--room',roots[0])
            renamed=inspect()['memberships']
            check('safe_rename_preserves_participant_identity',
                  {m['room_id']:m['participant_id'] for m in renamed}==original and
                  next(m for m in renamed if m['room_id']==roots[0])['display_name']=='native-renamed')
            cli('botsesh','connect','--no-start')
            check('reconnect_preserves_deliberate_rename',
                  next(m for m in inspect()['memberships'] if m['room_id']==roots[0])['display_name']=='native-renamed')
            stage = 'native_public_refresh'
            cli('auth','refresh','botsesh:rooms')
            check('native_public_auth_refresh_exercised', inspect()['audit']['refreshes']==1)
            _, refreshed_status=cli('botsesh','status','--json')
            check('rooms_live_after_refresh', all(r['alive'] for r in safe_json(refreshed_status)['rooms']))
            def rpc(operation,rid=''):
                p=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--probe',operation,'--room',rid],
                    env=env,cwd=private,capture_output=True,text=True,timeout=40)
                outputs.append(p.stdout+'\n'+p.stderr)
                if p.returncode:
                    (private/'probe-private.log').write_text(p.stdout+'\n'+p.stderr)
                    raise AssertionError('public_plugin_probe_nonzero')
                return safe_json(p.stdout)
            stage = 'actual_foreign_denial'
            denied=rpc('catchup',outside)
            check('actual_foreign_room_mcp_denied',denied['denied'])
            stage = 'actual_membership_revoke'
            membership=next(m for m in inspect()['memberships'] if m['room_id']==roots[1])
            revoked=post(owner,'/api/rooms/'+roots[1]+'/agent-memberships/'+membership['membership_id']+'/revoke',{})
            check('actual_owner_membership_revoke',revoked.status_code==200)
            check('revoked_membership_readback',bool(next(m for m in inspect()['memberships'] if m['room_id']==roots[1])['revoked_at']))
            check('actual_revoked_room_mcp_denied',rpc('catchup',roots[1])['denied'])
            remaining=rpc('list_rooms')
            check('membership_revoke_keeps_shared_sibling_live',not remaining['denied'] and
                  {r['room_id'] for r in remaining['rooms']}=={roots[0]})
            stage = 'native_shared_disconnect'
            cli('botsesh','disconnect',roots[0])
            disconnected=inspect()
            check('shared_disconnect_server_readback',all(g['revoked'] for g in disconnected['grants']) and
                  all(m['revoked_at'] for m in disconnected['memberships']))
            check('shared_disconnect_native_bindings_disabled',all(not r['active'] and r['state']=='revoked'
                  for r in json.loads(registry_path.read_text())['rooms'].values()))
            denied=rpc('list_rooms')
            check('actual_disconnected_grant_denied',denied['denied'] and denied['status']==401)
            rc, failed_refresh=cli('auth','refresh','botsesh:rooms',ok=False)
            check('revoked_grant_native_refresh_denied',rc!=0 and 'Refresh failed' in failed_refresh)
            stage='native_logout'
            cli('auth','logout','botsesh:rooms')
            _, logged_out=cli('auth','status','botsesh:rooms')
            check('native_logout_readback','logged out' in logged_out and
                  'botsesh:rooms' not in json.loads(auth_store.read_text()).get('plugin_oauth',{}))
            final=inspect()
            check('foreign_keys_intact',final['foreignKeys']==0)
            check('all_fixture_requests_correct_host',final['audit']['wrongHost']==0)
            check('proxy_used_for_actual_authorize_token_mcp',proxy.allowed>0 and
                  all(tool in final['audit']['mcp'] for tool in ('hermes_initialize','list_rooms','hermes_name','hermes_seats','catchup','hermes_disconnect')))
            with socket.create_connection(proxy.server_address,timeout=5) as forbidden:
                forbidden.sendall(b'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n')
                check('proxy_denies_non_fixture_hosts',forbidden.recv(4096).startswith(b'HTTP/1.1 403'))
            result['audit']={k:v for k,v in final['audit'].items() if k!='mcp'}
            result['audit']['mcp_counts']={name:final['audit']['mcp'].count(name) for name in sorted(set(final['audit']['mcp']))}
            result['proxy']={'allowed_local_tunnels':proxy.allowed,'denied_foreign_tunnels':proxy.denied}
            result['ok']=True
    except Exception as exc:
        if stage == 'setup':
            print(str(exc),file=sys.stderr)  # pre-account TLS/package-bootstrap diagnostics only
        result['failed_stage']=stage
        result['failure_type']=type(exc).__name__
        # Only our identifier-like assertions may enter durable evidence.
        reason=str(exc)
        result['failure_check']=reason if re.fullmatch(r'[A-Za-z0-9_]+',reason) else 'private_diagnostic_suppressed'
    finally:
        if auth and auth.poll() is None:
            auth.terminate(); auth.wait(timeout=10)
        if server and server.poll() is None:
            try:
                server.stdin.write('{"op":"stop"}\n'); server.stdin.flush(); server.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                server.terminate(); server.wait(timeout=10)
        if proxy:
            proxy.shutdown(); proxy.server_close()
        for handle in handles:
            handle.close()
        # No credentials, accounts, stores, keys, certificates, cookies or CLI logs retained.
        outputs.clear()
        shutil.rmtree(private)
        result['private_state_removed']=not private.exists()
        result['check_count']=len(result['checks'])
        result_path.parent.mkdir(parents=True,exist_ok=True)
        result_path.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        print(json.dumps(result,sort_keys=True))
    return 0 if result['ok'] and result['private_state_removed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result',type=Path,default=WORKSPACE/'evidence/native-declared-oauth-smoke.json')
    parser.add_argument('--probe',choices=('catchup','list_rooms'))
    parser.add_argument('--room',default='')
    args=parser.parse_args()
    if args.probe:
        probe(args.probe,args.room)
    else:
        raise SystemExit(run(args.result.resolve()))
