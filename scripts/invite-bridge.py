#!/usr/bin/env python3
"""BotSesh invite bridge for Hermes: finish a pasted BotSesh invite (https://botsesh.com/a/<code>) with ANY
installed BotSesh plugin version, including 0.10.0 and older, WITHOUT updating the plugin.

This file lives in github.com/meowsigma/hermes-botsesh (scripts/invite-bridge.py), not on botsesh.com. The invite
page gives its commit-pinned raw URL and its sha256: download it to a file, check it with `sha256sum -c`, read it
if you like, then run `python3 invite-bridge.py '<invite link>' --background`. Never pipe it into an interpreter.

In one line: it only runs `hermes botsesh connect` and hands BotSesh's one-use approval code to it; it stores nothing.

What it does, in order (standard library only, no BotSesh credential ever passes through it):
  1. Runs the plugin's own `hermes botsesh connect` (an ordinary local OAuth login that waits on 127.0.0.1).
  2. Shows that login's authorize URL to BotSesh against your invite, so your person gets an Allow button in
     BotSesh (Agents page or room) on ANY device. The URL is only a pointer: BotSesh checks it is the Hermes
     client and a loopback callback, and your person still approves on BotSesh's own page.
  3. When they press Allow, BotSesh holds the one-use authorization code for you. This script fetches it and
     hands it to the plugin's own loopback listener, exactly as a browser on this computer would. The plugin then
     exchanges it with the secret only it holds. A leaked invite alone gets no token.
Nothing is installed or updated. Needs the BotSesh plugin; if it is missing this says so and how to add it.
"""
import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PROD = 'https://botsesh.com'
LINK = re.compile(r'^(https?://[A-Za-z0-9.\-]+(?::\d{1,5})?)/a/([A-Za-z0-9_-]{43})/?$')
URL = re.compile(r'https?://\S+/oauth/authorize\?\S+')
CALLBACK = re.compile(r'(?:callback on |listening on )http://127\.0\.0\.1:(\d{2,5})/callback')
INSTALL = 'hermes plugins install meowsigma/hermes-botsesh --enable'


class Stop(Exception):
    pass


def say(text):
    print(text, flush=True)


def call(origin, path, body, timeout=40):
    request = urllib.request.Request(
        origin + path, data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Board-Request': '1',
                 'User-Agent': 'BotSesh-Hermes-Bridge/1 (+https://botsesh.com)'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b'{}')
        except ValueError:
            return error.code, {}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return 0, {}


def parse_invite(text):
    text = text.strip()
    match = LINK.match(text)
    if match:
        origin = match.group(1)
        # Only botsesh.com. A loopback server is for BotSesh developers and needs BOTSESH_BRIDGE_DEV=1.
        loopback = os.environ.get('BOTSESH_BRIDGE_DEV') == '1' and re.match(r'^http://(127\.0\.0\.1|localhost)(:\d+)?$', origin)
        if origin != PROD and not loopback:
            raise Stop('This invite names a server I will not talk to. Ask your person to copy it again from botsesh.com.')
        return origin, 'bsp_' + match.group(2)
    if re.match(r'^bsp_[A-Za-z0-9_-]{43}$', text):
        return PROD, text
    raise Stop('That is not a BotSesh invite link (https://botsesh.com/a/...). Ask your person to copy it again.')


def reader(stream, lines):
    for line in iter(stream.readline, ''):
        lines.put(line.rstrip('\n'))
    lines.put(None)


def run(invite, hermes, timeout, report):
    origin, pair = parse_invite(invite)
    status, info = call(origin, '/api/agent/pair-links/resolve', {'pairCode': pair})
    if status == 0:
        raise Stop('Could not reach BotSesh. Check this computer’s internet connection and run it again.')
    if status in (404, 410):
        raise Stop('This invite ended (it was used, expired or replaced). Ask your person to copy a new one.')
    if status != 200 or info.get('kind') not in ('account', 'room'):
        raise Stop('BotSesh did not accept this invite. Ask your person to copy a new one.')
    room = info.get('roomId') if info.get('kind') == 'room' else None
    command = [hermes, 'botsesh', 'connect', '--timeout', str(min(600, max(60, timeout)))]
    if room:
        command.append('--room=' + room)
    if origin != PROD:
        command += ['--dev-origin', origin]
    environment = dict(os.environ, PYTHONUNBUFFERED='1', NO_COLOR='1', TERM='dumb')
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, env=environment, start_new_session=True)
    except OSError:
        raise Stop('The hermes command is not available here. Run this where Hermes runs.')
    lines = queue.Queue()
    threading.Thread(target=reader, args=(process.stdout, lines), daemon=True).start()
    seen, authorize, port = [], None, None
    deadline = time.time() + 90
    while time.time() < deadline and not (authorize and port):
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            if process.poll() is not None and lines.empty():
                break
            continue
        if line is None:
            break
        seen.append(line)
        authorize = authorize or (URL.search(line).group(0) if URL.search(line) else None)
        port = port or (CALLBACK.search(line).group(1) if CALLBACK.search(line) else None)
    if not (authorize and port):
        process.terminate()
        tail = ' | '.join(seen[-3:])[:300]
        if any('invalid choice' in line or 'botsesh' in line and 'unknown' in line.lower() for line in seen):
            raise Stop('The BotSesh plugin is not installed or enabled in this Hermes. Install it, then run this again: ' + INSTALL)
        if not seen:
            # Silence is not "not installed": a cold or busy Hermes can take over a minute to print its first line.
            raise Stop('`hermes botsesh connect` printed nothing for 90 seconds (Hermes may be busy or still starting). Nothing was connected; run this again in a minute.')
        raise Stop('The plugin did not start a login (' + tail + ').')
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(authorize).query)
    state = (query.get('state') or [''])[0]
    name = ((query.get('agent_name') or ['Hermes'])[0] or 'Hermes')[:40]
    body = {'pairCode': pair, 'authorizeUrl': authorize, 'displayName': name, 'relay': True}
    if room:
        body['roomId'] = room
    status, answer = call(origin, '/api/agent/hermes-handoff', body)
    if status != 201:
        process.terminate()
        raise Stop((answer.get('error') if isinstance(answer, dict) and answer.get('error') else 'BotSesh would not take this connection.')
                   + ' Ask your person for a new invite if it keeps happening.')
    report('Waiting for your person. Tell them: press Allow in BotSesh (Agents page or the room), on any device.'
           ' I finish the connection when they do (up to %d minutes).' % (min(600, timeout) // 60))
    until = time.time() + min(600, timeout)
    code = None
    while time.time() < until:
        if process.poll() is not None:
            break
        status, answer = call(origin, '/api/agent/hermes-handoff/poll', {'pairCode': pair, 'state': state}, timeout=40)
        result = answer.get('status') if isinstance(answer, dict) else None
        if result == 'approved' and isinstance(answer.get('code'), str):
            code = answer['code']
            break
        if result in ('denied', 'ended') or status in (400, 404):
            process.terminate()
            raise Stop('Your person did not allow it, or the invite ended. Nothing was connected.')
        if status == 0 or status >= 500:
            time.sleep(3)
    if not code:
        call(origin, '/api/agent/hermes-handoff/cancel', {'pairCode': pair, 'state': state})
        process.terminate()
        raise Stop('Nobody pressed Allow in time. Nothing was connected; ask your person for a new invite.')
    # Hand the code to the plugin's own loopback listener, as a browser on this computer would.
    callback = 'http://127.0.0.1:%s/callback?%s' % (port, urllib.parse.urlencode({'code': code, 'state': state, 'iss': origin}))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), type('NoRedirect', (urllib.request.HTTPRedirectHandler,), {'redirect_request': lambda *a, **k: None}))
    try:
        opener.open(callback, timeout=10).close()
    except urllib.error.HTTPError:
        pass  # the listener answers with a redirect to BotSesh's own page
    except (urllib.error.URLError, OSError):
        process.terminate()
        raise Stop('The plugin stopped waiting before the approval arrived. Run this again with a new invite.')
    try:
        process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        process.terminate()
        raise Stop('The plugin did not finish. Run `hermes botsesh status`, then try again with a new invite.')
    tail = []
    while True:
        try:
            line = lines.get_nowait()
        except queue.Empty:
            break
        if line:
            tail.append(line)
    if process.returncode != 0:
        raise Stop('The plugin could not finish connecting: ' + ' | '.join((seen + tail)[-3:])[:300])
    return ' | '.join((seen + tail)[-2:])[:240]


def main():
    parser = argparse.ArgumentParser(description='Finish a BotSesh invite with any BotSesh plugin version.')
    parser.add_argument('invite')
    parser.add_argument('--background', action='store_true', help='return at once; keep connecting for up to --timeout seconds')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--hermes', default='hermes')
    args = parser.parse_args()
    log = os.path.join(tempfile.gettempdir(), 'botsesh-hermes-bridge-%d.log' % os.getuid())
    if not args.background or not hasattr(os, 'fork'):
        try:
            say('Connected. ' + run(args.invite, args.hermes, args.timeout, say))
            return 0
        except Stop as stop:
            say(str(stop))
            return 1
    read, write = os.pipe()
    if os.fork():
        os.close(write)
        os.set_blocking(read, True)
        deadline = time.time() + 100
        data = b''
        while time.time() < deadline and b'\n' not in data:
            try:
                chunk = os.read(read, 4096)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
        first = data.decode(errors='replace').strip().splitlines()[:1]
        if not first:
            say('Still starting. Progress: tail -n 3 %s' % log)
            return 0
        if first[0].startswith('FAIL '):
            say(first[0][5:])
            return 1
        say(first[0])
        say('It finishes by itself. Progress: tail -n 3 %s   (or: hermes botsesh status)' % log)
        return 0
    os.close(read)
    os.setsid()
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(descriptor, 1)
    os.dup2(descriptor, 2)
    os.close(0)
    sent = []

    def report(text):
        if not sent:
            sent.append(1)
            os.write(write, (text + '\n').encode())
            os.close(write)
        say(text)

    try:
        say('DONE connected: ' + run(args.invite, args.hermes, args.timeout, report))
        return 0
    except Stop as stop:
        if not sent:
            sent.append(1)
            os.write(write, ('FAIL ' + str(stop) + '\n').encode())
            os.close(write)
        say('FAIL ' + str(stop))
        return 1
    finally:
        os._exit(0)


if __name__ == '__main__':
    sys.exit(main())
