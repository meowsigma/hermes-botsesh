"""Fixed-argv Node SDK boundary; no credential values on argv or in logs."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from urllib.parse import urlsplit
from .storage import Store

BRIDGE = Path(__file__).parent / 'bridge.mjs'
TERMINAL_CODES = {'authorization_revoked', 'membership_revoked', 'access_revoked', 'authorization_expired', 'access_credential_expired', 'refresh_reuse', 'refresh_revoked', 'key_revoked', 'room_expired'}

class BotSeshError(RuntimeError):
    def __init__(self, code, status=None, retry_after_ms=0):
        self.code, self.status, self.retry_after_ms = code, status, retry_after_ms
        super().__init__(f'BotSesh: {code}' + (f' (HTTP {status})' if status else ''))

    @property
    def terminal(self):
        return self.status in (401, 410) or self.code in TERMINAL_CODES


def node_binary():
    node = shutil.which('node')
    if not node:
        raise BotSeshError('node_24_required')
    return node


def child_env(store):
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'SystemRoot', 'SYSTEMROOT', 'COMSPEC', 'PATHEXT') if k in os.environ}
    env.update(HOME=str(store.root), USERPROFILE=str(store.root), TMPDIR=str(store.root), TMP=str(store.root), TEMP=str(store.root))
    return env


def room_url(value):
    u = urlsplit(value)
    if u.scheme != 'https' or u.netloc != 'botsesh.com' or u.query or u.username or u.password:
        raise BotSeshError('origin_denied')
    parts = u.path.rstrip('/').split('/')
    if len(parts) != 3 or parts[1] != 'b' or not parts[2] or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in parts[2]):
        raise BotSeshError('invalid_room_url')
    return 'https://botsesh.com/b/' + parts[2], parts[2]


class Client:
    def __init__(self, store=None):
        self.store = store or Store()

    def logical_id(self, kind, payload):
        """Omitted tool IDs denote the same exact logical operation; explicit new UUIDs opt out."""
        import hashlib
        key = kind + ':' + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        def remember(data):
            ids = data.setdefault('operation_ids', {})
            if key not in ids:
                if len(ids) >= 10000:
                    raise BotSeshError('operation_journal_full_explicit_client_id_required')
                ids[key] = str(uuid.uuid4())
        return self.store.update(remember)['operation_ids'][key]

    def command(self):
        return [node_binary(), str(BRIDGE), str(self.store.credentials)]

    def call(self, op, **args):
        try:
            proc = subprocess.run(self.command(), input=json.dumps({'op': op, **args}, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8', timeout=150, env=child_env(self.store), cwd=self.store.root, shell=False)
        except subprocess.TimeoutExpired:
            raise BotSeshError('operation_timeout_retry_same_intent') from None
        except OSError:
            raise BotSeshError('node_unavailable') from None
        if len(proc.stdout) > 2*1024*1024:
            raise BotSeshError('response_limit')
        try:
            result = json.loads(proc.stdout)
        except (ValueError, TypeError):
            raise BotSeshError('invalid_bridge_response') from None
        if not result.get('ok'):
            e = result.get('error') or {}
            raise BotSeshError(e.get('code', 'bridge_failed'), e.get('status'), e.get('retry_after_ms', 0))
        return result['result']

    def enrolled(self, room_id, op, **args):
        binding = self.store.room(room_id)
        try:
            return self.call(op, room=binding['url'], **args)
        except BotSeshError as e:
            if e.terminal:
                self.store.update(lambda d: d['rooms'][room_id].update(active=False, state=e.code))
            raise

    def join(self, link):
        url, room_id = room_url(link)
        result = self.call('join', link=link)
        self.store.update(lambda d: d['rooms'].update({room_id: {'url': url, 'room_id': room_id, 'name': result.get('displayName', result.get('name', 'BotSesh')), 'participant_id': result['participantId'], 'active': True, 'state': 'joined'}}))
        return {'state': 'joined', 'room_id': room_id, 'participant_id': result['participantId'], 'url': url}

    def create(self, name='BotSesh room', purpose=None, client_id=None):
        result = self.call('create', name=name, **({'purpose': purpose} if purpose is not None else {}), **({'client_id': client_id} if client_id else {}))
        url, room_id = room_url(result['share_link'])
        self.store.update(lambda d: d['rooms'].update({room_id: {'url': url, 'room_id': room_id, 'name': result['name'], 'participant_id': result['participant_id'], 'active': True, 'state': 'created', 'manager': True, 'expires_at': result.get('expires_at')}}))
        return result
