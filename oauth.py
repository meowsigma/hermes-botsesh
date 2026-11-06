"""Room-scoped OAuth through Hermes PKCE and its profile credential store.

No token in argv, config.yaml, tool output, or the Node invitation bridge.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
from urllib.parse import urlsplit

from .client import BotSeshError
from .storage import Store, StorageError

CLIENT_ID = 'https://botsesh.com/oauth/clients/hermes.json'
SCOPES = ('botsesh:chat', 'botsesh:rooms')
POOL = 'botsesh'
MAX_BYTES = 2 * 1024 * 1024


def checked_origin(value):
    u = urlsplit(value)
    if value == 'https://botsesh.com':
        return value
    # Explicit local development only, never an arbitrary HTTP/HTTPS IdP.
    if (u.scheme == 'http' and u.hostname == '127.0.0.1' and u.port
            and u.netloc == f'127.0.0.1:{u.port}' and not u.path and not u.query and not u.fragment):
        return value
    raise BotSeshError('origin_denied')


def _auth_path():
    from hermes_cli.config import get_hermes_home
    return Path(get_hermes_home()) / 'auth.json'


def _check_auth_file():
    path = _auth_path()
    for item in (path, *path.parents):
        if item.is_symlink():
            raise StorageError('Auth storage must not use symlinks.')
    if path.exists():
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600 or (hasattr(os, 'getuid') and st.st_uid != os.getuid()):
            raise StorageError('Hermes auth.json must be an owner-only regular file (0600).')


def _rows():
    from hermes_cli.auth import _load_auth_store
    _check_auth_file()
    # Model pools inherit default-profile credentials. Room tokens must not.
    section = _load_auth_store().get('plugin_oauth', {})
    return [row for key, row in section.items() if key.startswith('botsesh:') and isinstance(row, dict)]


def _save(row=None, *, remove=None):
    from hermes_cli.plugin_oauth import _write_grant
    _check_auth_file()
    _write_grant('botsesh:' + (remove or row['id']), None if remove else row)
    _check_auth_file()


@contextlib.contextmanager
def _lock(store):
    # All connect/refresh/disconnect processes share this lock. Never spend a
    # rotating refresh token concurrently, or revive one after local disconnect.
    import fcntl
    path = store.root / '.oauth.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        Store._check(path)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _http(origin, path, *, form=None, payload=None, bearer=None):
    import httpx
    checked_origin(origin)
    if path not in ('/oauth/token', '/oauth/revoke', '/mcp'):
        raise BotSeshError('endpoint_denied')
    headers = {'Accept': 'application/json, text/event-stream' if path == '/mcp' else 'application/json'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', origin + path, headers=headers, data=form, json=payload) as response:
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > MAX_BYTES:
                        raise BotSeshError('response_limit')
                status, content_type = response.status_code, response.headers.get('content-type', '')
    except httpx.HTTPError:
        raise BotSeshError('connection_failed_retry_same_intent') from None
    if status == 200 and path == '/oauth/revoke':
        return {}
    try:
        if 'text/event-stream' in content_type:
            values = [json.loads(line[5:].strip()) for line in raw.decode().splitlines() if line.startswith('data:')]
            if len(values) != 1:
                raise ValueError()
            value = values[0]
        else:
            value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise BotSeshError('invalid_server_response', status) from None
    if not 200 <= status < 300:
        code = value.get('error')
        raise BotSeshError(code if isinstance(code, str) and re.fullmatch(r'[a-z0-9_-]{1,80}', code) else 'request_denied', status)
    return value


def _token_request(origin, fields):
    result = _http(origin, '/oauth/token', form={**fields, 'client_id': CLIENT_ID, 'resource': origin + '/mcp'})
    if result.get('scope') != 'botsesh:rooms' or result.get('token_type', '').lower() != 'bearer':
        raise BotSeshError('unexpected_oauth_scope')
    if not all(isinstance(result.get(k), str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', result[k]) for k in ('access_token', 'refresh_token')):
        raise BotSeshError('invalid_token_response')
    return result


def login(*, origin='https://botsesh.com', open_browser=True, timeout=180, room_hint=None):
    from hermes_cli.auth_oauth_pkce_plugin import OAuthPKCEConfig, login as hermes_login
    origin = checked_origin(origin)
    cfg = OAuthPKCEConfig(client_id=CLIENT_ID, authorize_url=origin + '/oauth/authorize',
                          token_url=origin + '/oauth/token', scopes=SCOPES,
                          extra_authorize_params={'resource': origin + '/mcp', **({'room_hint': room_hint} if room_hint else {})},
                          token_request=lambda fields: _token_request(origin, fields),
                          redirect_path='/callback', redirect_port=0,
                          timeout_seconds=timeout, label='BotSesh')
    # The native helper owns S256, random-port 127.0.0.1 callback, constant-time
    # state verification, cancellation and SSH loopback-forward instructions.
    return hermes_login('botsesh', cfg, open_browser=open_browser)


class OAuthClient:
    def __init__(self, store=None):
        self.store = store or Store()

    def _credential(self, binding):
        row = next((r for r in _rows() if r.get('id') == binding['oauth_id']), None)
        if not row or row.get('extra', {}).get('origin') != binding['origin'] or row.get('extra', {}).get('room_id') != binding['room_id']:
            raise BotSeshError('authorization_revoked', 401)
        if row.get('extra', {}).get('client_id') != CLIENT_ID:
            raise BotSeshError('credential_binding_denied')
        return row

    def _access(self, binding):
        with _lock(self.store):
            row = self._credential(binding)
            if row.get('expires_at_ms', 0) <= int(time.time()*1000) + 45000:
                try:
                    result = _token_request(binding['origin'], {'grant_type': 'refresh_token', 'refresh_token': row['refresh_token']})
                except BotSeshError as e:
                    if e.code == 'invalid_grant':
                        _save(remove=row['id'])
                        self.store.update(lambda d: d['rooms'][binding['room_id']].update(active=False, state='authorization_revoked'))
                    raise
                row = {**row, 'access_token': result['access_token'], 'refresh_token': result['refresh_token'],
                       'expires_at_ms': int(time.time()*1000) + result['expires_in']*1000}
                _save(row)
            return row['access_token']

    def rpc(self, binding, name, args=None, *, token=None):
        value = _http(binding['origin'], '/mcp', bearer=token or self._access(binding), payload={
            'jsonrpc': '2.0', 'id': uuid.uuid4().hex, 'method': 'tools/call',
            'params': {'name': name, 'arguments': args or {}}})
        result = value.get('result')
        if value.get('error') or not isinstance(result, dict) or result.get('isError'):
            # Never echo server error prose, room content or the HTTP request.
            raise BotSeshError('mcp_operation_denied')
        data = result.get('structuredContent')
        if not isinstance(data, dict):
            raise BotSeshError('invalid_mcp_response')
        if name != 'read_tool_page' and data.get('next_cursor'):
            cursor, fragments, seen = data['next_cursor'], [], set()
            while cursor:
                if cursor in seen or len(seen) >= 100:
                    raise BotSeshError('context_limit')
                seen.add(cursor)
                page = self.rpc(binding, 'read_tool_page', {'room_id': args.get('room_id'), 'cursor': cursor}, token=token)
                if not isinstance(page.get('json_fragment'), str):
                    raise BotSeshError('invalid_mcp_page')
                fragments.append(page['json_fragment'])
                if sum(len(p) for p in fragments) > 160000:
                    raise BotSeshError('context_limit')
                cursor = page.get('next_cursor')
            try:
                data = json.loads(''.join(fragments))
            except ValueError:
                raise BotSeshError('invalid_mcp_page') from None
        return data

    def connect(self, *, origin='https://botsesh.com', open_browser=True, timeout=180, room_hint=None):
        if room_hint and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', room_hint):
            raise BotSeshError('invalid_room_hint')
        fields = login(origin=origin, open_browser=open_browser, timeout=timeout, room_hint=room_hint)
        provisional = {'origin': origin}
        try:
            rooms = self.rpc(provisional, 'list_rooms', token=fields['access_token']).get('rooms')
            if not isinstance(rooms, list) or len(rooms) != 1:
                raise BotSeshError('one_room_required')
            room = rooms[0]
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', room.get('room_id', '')) or not room.get('participant_id'):
                raise BotSeshError('invalid_room_binding')
            room_id = room['room_id']
            prior = self.store.read()['rooms'].get(room_id)
            if prior and prior.get('active'):
                raise BotSeshError('room_already_connected_disconnect_first')
            credential_id = uuid.uuid4().hex
            binding = {'room_id': room_id, 'url': origin + '/b/' + room_id, 'origin': origin,
                       'name': room['name'], 'display_name': room['display_name'],
                       'participant_id': room['participant_id'], 'oauth_id': credential_id,
                       'active': True, 'state': 'connected', 'transport': 'oauth'}
            with _lock(self.store):
                # Recheck after the browser round trip and under the shared lock.
                current = self.store.read()['rooms'].get(room_id)
                if current and current.get('active'):
                    raise BotSeshError('room_already_connected_disconnect_first')
                _save({'id': credential_id, 'provider': POOL, 'auth_type': 'oauth',
                       'source': 'manual:loopback_pkce', 'label': 'BotSesh room', **fields,
                       'extra': {'origin': origin, 'room_id': room_id, 'client_id': CLIENT_ID, 'scope': 'botsesh:rooms'}})
                self.store.update(lambda d: d['rooms'].update({room_id: binding}))
            return binding
        except Exception:
            # Never leave an untracked grant after duplicate room / storage failure.
            _http(origin, '/oauth/revoke', form={'client_id': CLIENT_ID, 'token': fields['refresh_token']})
            raise

    def disconnect(self, room_id):
        binding = self.store.read()['rooms'].get(room_id)
        if not binding:
            raise BotSeshError('room_not_connected')
        if binding.get('transport') != 'oauth':
            raise BotSeshError('legacy_invitation_use_room_access_to_revoke')
        with _lock(self.store):
            if not binding.get('active') and binding.get('state') in ('authorization_revoked', 'revoked', 'invalid_token', 'membership_revoked'):
                _save(remove=binding['oauth_id'])
                self.store.update(lambda d: d['rooms'][room_id].update(active=False, state='revoked'))
                return
            row = self._credential(binding)
            _http(binding['origin'], '/oauth/revoke', form={'client_id': CLIENT_ID, 'token': row['refresh_token']})
            # RFC7009 always returns 200; read back the exact old access before
            # claiming revocation. A transient failure keeps credentials for retry.
            try:
                self.rpc(binding, 'list_rooms', token=row['access_token'])
            except BotSeshError as e:
                if e.status != 401:
                    raise
            else:
                raise BotSeshError('revocation_not_confirmed')
            self.store.update(lambda d: d['rooms'][room_id].update(active=False, state='revoked'))
            _save(remove=row['id'])

    def call(self, binding, op, **args):
        room_id = binding['room_id']
        if op in ('verify', 'pending'):
            rooms = self.rpc(binding, 'list_rooms').get('rooms', [])
            if len(rooms) != 1 or rooms[0]['room_id'] != room_id or rooms[0]['participant_id'] != binding['participant_id']:
                raise BotSeshError('identity_mismatch', 401)
            return {'pending': None, 'participantId': binding['participant_id']}
        if op in ('check', 'read'):
            value = self.rpc(binding, 'catchup', {'room_id': room_id, 'max_chars': 60000, 'max_pages': 100})
            if value.get('has_more') or value.get('truncated') or value.get('ack_eligible') is not True:
                raise BotSeshError('context_limit')
            messages = []
            for source in value['messages']:
                author = source.get('authorParticipant') or {}
                mentions = source.get('mentions') or []
                message = {**source, 'senderParticipantId': source.get('senderParticipantId'),
                           'isOwn': author.get('id') == binding['participant_id'],
                           'isMentionToMe': any((m.get('participant') or {}).get('id') == binding['participant_id'] for m in mentions),
                           'isReplyToMe': ((source.get('replyPreview') or {}).get('authorParticipant') or {}).get('id') == binding['participant_id']}
                messages.append(message)
            if op == 'check':
                return {'pending': None, 'hasMore': False,
                        'mentions': [m['number'] for m in messages if m['isMentionToMe'] and not m['isOwn']],
                        'replies': [m['number'] for m in messages if m['isReplyToMe'] and not m['isOwn']]}
            receipt_id = value['batch_id']
            # Preserve the whole exact transport result, not a clipped summary.
            result = {'receipt_id': receipt_id, 'read_effect': 'none', 'briefing': [value],
                      'batch': {'messages': messages, 'hasMore': False, 'readCheckpoint': value['read_checkpoint'],
                                'nextCheckpoint': value['next_checkpoint']}}
            if len(json.dumps(result, ensure_ascii=False)) > 160000:
                raise BotSeshError('context_limit')
            def save_receipt(data):
                receipts = data['rooms'][room_id].setdefault('oauth_receipts', {})
                for key in list(receipts):
                    if receipts[key]['expires'] <= time.time():
                        del receipts[key]
                if len(receipts) >= 90:
                    raise BotSeshError('receipt_limit_retry_pending_turn')
                receipts[receipt_id] = {'expires': time.time()+3600, 'numbers': [m['number'] for m in messages]}
            self.store.update(save_receipt)
            return result
        if op == 'post':
            receipt_id, client_id = args.get('receipt_id'), args.get('client_id')
            try:
                if str(uuid.UUID(client_id, version=4)) != client_id:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise BotSeshError('invalid_client_id') from None
            if 'draft_ready' in args or 'routine_report' in args:
                raise BotSeshError('oauth_structured_completion_not_supported_use_invitation')
            text = args.get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                raise BotSeshError('invalid_text')
            # MCP implicit-last-batch semantics can attest an unprocessed fetch.
            # OAuth posting therefore always requires an explicit frozen receipt.
            if not receipt_id or args.get('processed') is not True:
                raise BotSeshError('processed_receipt_required')
            exact = {k: args[k] for k in ('text', 'client_id', 'receipt_id', 'processed', 'reply_to') if k in args}
            current = self.store.room(room_id)
            saved = current.get('oauth_posts', {}).get(client_id)
            if saved and saved['exact'] != exact:
                raise BotSeshError('pending_send_mismatch')
            if saved and saved.get('complete'):
                self.call(binding, 'verify')
                return saved['result']
            receipt = current.get('oauth_receipts', {}).get(receipt_id)
            if not saved and (not receipt or receipt['expires'] <= time.time()):
                raise BotSeshError('receipt_expired')
            if not saved and args.get('reply_to') is not None and args['reply_to'] not in receipt['numbers']:
                raise BotSeshError('unread_reply_target')
            if not saved:
                def journal(data):
                    posts = data['rooms'][room_id].setdefault('oauth_posts', {})
                    if len(posts) >= 1000:
                        removable = next((key for key, post in posts.items() if post.get('complete')), None)
                        if removable is None:
                            raise BotSeshError('operation_journal_full')
                        del posts[removable]
                    posts[client_id] = {'exact': exact}
                self.store.update(journal)
            result = self.rpc(binding, 'send_message', {'room_id': room_id, 'text': text,
                'client_id': client_id, 'batch_id': receipt_id,
                **({'reply_to': args['reply_to']} if args.get('reply_to') is not None else {})})
            message = result.get('message') or {}
            if message.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest() or message.get('authorParticipant', {}).get('id') != binding['participant_id']:
                raise BotSeshError('send_not_confirmed_retry_same_intent')
            completed = {'client_id': client_id, 'number': message['number'], 'read_effect': 'processed_frozen_receipt'}
            self.store.update(lambda d: d['rooms'][room_id]['oauth_posts'][client_id].update(complete=True, result=completed))
            self.store.update(lambda d: d['rooms'][room_id].get('oauth_receipts', {}).pop(receipt_id, None))
            return completed
        if op == 'retry':
            saved = self.store.room(room_id).get('oauth_posts', {}).get(args.get('client_id'))
            if not saved:
                raise BotSeshError('original_operation_required')
            return self.call(binding, 'post', **saved['exact'])
        raise BotSeshError('oauth_operation_not_supported')
