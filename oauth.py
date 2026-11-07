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
import unicodedata
import time
import uuid
from urllib.parse import urlsplit

from .client import BotSeshError
from .storage import Store, StorageError
from .room_turn import parse_room_turn_output, require_client_id, is_number

CLIENT_ID = 'https://botsesh.com/oauth/clients/hermes.json'
SCOPES = ('botsesh:chat', 'botsesh:rooms')
POOL = 'botsesh'
MAX_BYTES = 2 * 1024 * 1024


def preferred_name(value=None):
    if value is None:
        try:
            from hermes_cli.profiles import current_profile_name
            value = current_profile_name()
        except (ImportError, AttributeError):
            value = os.environ.get('HERMES_PROFILE_NAME') or os.environ.get('HERMES_PROFILE')
        if not value or value in ('custom', 'default'):
            value = 'Hermes'
    if (not isinstance(value, str) or not value.strip()
            or len(value.encode('utf-16-le')) > 80
            or '@' in value or any(unicodedata.category(c) in ('Cc', 'Cf') for c in value)):
        raise BotSeshError('invalid_agent_name')
    return value.strip()


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
    from .http_transport import post
    return post(origin, path, form=form, payload=payload, bearer=bearer)


def _token_request(origin, fields):
    result = _http(origin, '/oauth/token', form={**fields, 'client_id': CLIENT_ID, 'resource': origin + '/mcp'})
    if result.get('scope') != 'botsesh:rooms' or result.get('token_type', '').lower() != 'bearer':
        raise BotSeshError('unexpected_oauth_scope')
    if not all(isinstance(result.get(k), str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', result[k]) for k in ('access_token', 'refresh_token')):
        raise BotSeshError('invalid_token_response')
    return result


def _make_loopback_callback_handler(expected_path, *, origin, expected_state):
    """Capture the native callback fields, then leave the browser on BotSesh."""
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import parse_qs

    landing = checked_origin(origin) + '/connected?client=hermes'
    result = dict.fromkeys(('code', 'state', 'error', 'error_description'))

    class CallbackHandler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            # The native loopback server is single-threaded. Chromium may
            # preconnect without sending HTTP; bound that socket so neither a
            # queued callback nor native shutdown can wait forever on it.
            self.connection.settimeout(2)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != expected_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'Not found.')
                return
            params = parse_qs(parsed.query)
            for key in result:
                result[key] = params.get(key, [None])[0]
            import hmac
            failed = result['error'] or not str(result['code'] or '').strip() or not hmac.compare_digest(str(result['state'] or ''), expected_state)
            self.send_response(302)
            self.send_header('Location', landing + ('&error=1' if failed else ''))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, format, *args):
            pass  # The request URL contains the authorization code and state.

    return CallbackHandler, result


def _native_loopback_helpers():
    """Check private Hermes seams before binding; older cores keep their login."""
    import inspect
    try:
        from hermes_cli import auth_device_flow as flow, auth_oauth_pkce_plugin as pkce
        # Bind the exact calls we will make, without invoking any helper. A new
        # required argument, missing helper or uninspectable callable falls back.
        calls = {
            'bind': (flow._bind_loopback_callback_server, ('127.0.0.1', 0, object),
                     {'err': None, 'bind_failed_code': 'oauth_callback_bind_failed'}),
            'verifier': (flow._pkce_code_verifier, (), {}),
            'challenge': (flow._pkce_code_challenge, ('verifier',), {}),
            'hint': (flow._print_loopback_ssh_hint, ('http://127.0.0.1:1/callback',), {}),
            'serve': (flow._serve_loopback_callback, (None, {}),
                      {'timeout_seconds': 180, 'err': None, 'timeout_code': 'oauth_callback_timeout'}),
            'graphical': (flow._can_open_graphical_browser, (), {}),
            'post_token': (pkce._post_token, ('botsesh', None, {}), {'code': 'oauth_token_exchange_failed'}),
            'err': (pkce._err, ('botsesh', 'message', 'code'), {}),
            'validate': (pkce.validate_config, ('botsesh', None), {}),
        }
        for function, args, kwargs in calls.values():
            inspect.signature(function).bind(*args, **kwargs)
    except (ImportError, AttributeError, TypeError, ValueError):
        return None
    return {name: function for name, (function, _, _) in calls.items()}


def login(*, origin='https://botsesh.com', open_browser=True, timeout=180, room_hint=None):
    from hermes_cli.auth_oauth_pkce_plugin import OAuthPKCEConfig, login as hermes_login
    origin = checked_origin(origin)
    cfg = OAuthPKCEConfig(client_id=CLIENT_ID, authorize_url=origin + '/oauth/authorize',
                          token_url=origin + '/oauth/token', scopes=SCOPES,
                          extra_authorize_params={'resource': origin + '/mcp', **({'room_hint': room_hint} if room_hint else {})},
                          token_request=lambda fields: _token_request(origin, fields),
                          redirect_path='/callback', redirect_port=0,
                          timeout_seconds=timeout, label='BotSesh')
    helpers = _native_loopback_helpers()
    if helpers is None:
        return hermes_login('botsesh', cfg, open_browser=open_browser)

    import hmac
    import secrets
    import webbrowser
    from urllib.parse import urlencode

    helpers['validate']('botsesh', cfg)
    err = lambda message, code: helpers['err']('botsesh', message, code)
    verifier = helpers['verifier']()
    state = secrets.token_urlsafe(32)
    handler, result = _make_loopback_callback_handler(cfg.redirect_path, origin=origin, expected_state=state)
    server = helpers['bind']('127.0.0.1', cfg.redirect_port, handler,
                             err=err, bind_failed_code='oauth_callback_bind_failed')
    try:
        redirect_uri = f'http://127.0.0.1:{server.server_address[1]}{cfg.redirect_path}'
        params = {**cfg.extra_authorize_params, 'client_id': cfg.client_id, 'response_type': 'code',
                  'redirect_uri': redirect_uri, 'state': state,
                  'code_challenge': helpers['challenge'](verifier), 'code_challenge_method': 'S256',
                  'scope': ' '.join(cfg.scopes)}
        authorize_url = cfg.authorize_url + '?' + urlencode(params)
        # As in native login, show the authorize URL for manual authorization.
        # Never print the received callback query, error prose, verifier or tokens.
        print(f'\nOpen this URL to authorize Hermes with BotSesh:\n  {authorize_url}\n')
        print(f'Waiting for callback on {redirect_uri} (timeout {int(timeout)}s, Ctrl+C to cancel)...')
        helpers['hint'](redirect_uri)
        if open_browser and helpers['graphical']():
            try:
                webbrowser.open(authorize_url)
            except Exception:
                print('Could not open the browser automatically; use the URL above.')
        callback = helpers['serve'](server, result, timeout_seconds=timeout, err=err,
                                    timeout_code='oauth_callback_timeout')
        if callback.get('error'):
            # Do not reflect untrusted callback error prose into CLI logs.
            raise err('authorization failed.', 'oauth_authorization_denied')
        if not hmac.compare_digest(str(callback.get('state') or ''), state):
            raise err('callback state mismatch — aborting login.', 'oauth_state_mismatch')
        code = str(callback.get('code') or '').strip()
        if not code:
            raise err('callback carried no authorization code.', 'oauth_no_code')
        return helpers['post_token']('botsesh', cfg, {
            'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': redirect_uri, 'code_verifier': verifier,
        }, code='oauth_token_exchange_failed')
    except KeyboardInterrupt:
        print('\nLogin cancelled.')
        raise SystemExit(130)
    finally:
        server.server_close()  # Native serve closes too; cover pre-serve failures.


class OAuthClient:
    def __init__(self, store=None):
        self.store = store or Store()

    def _credential(self, binding):
        row = next((r for r in _rows() if r.get('id') == binding['oauth_id']), None)
        if not row or row.get('extra', {}).get('origin') != binding['origin'] or binding['room_id'] not in row.get('extra', {}).get('room_ids', [row.get('extra', {}).get('room_id')]):
            raise BotSeshError('authorization_revoked', 401)
        if row.get('extra', {}).get('client_id') != CLIENT_ID:
            raise BotSeshError('credential_binding_denied')
        return row

    def _access(self, binding, *, rejected=None):
        with _lock(self.store):
            row = self._credential(binding)
            if row.get('expires_at_ms', 0) <= int(time.time()*1000) + 45000 or rejected is not None and row['access_token'] == rejected:
                try:
                    result = _token_request(binding['origin'], {'grant_type': 'refresh_token', 'refresh_token': row['refresh_token']})
                except BotSeshError as e:
                    if e.code == 'invalid_grant':
                        _save(remove=row['id'])
                        def disable(data):
                            for room in data['rooms'].values():
                                if room.get('oauth_id') == binding['oauth_id']:
                                    room.update(active=False, state='authorization_revoked')
                        self.store.update(disable)
                    raise
                row = {**row, 'access_token': result['access_token'], 'refresh_token': result['refresh_token'],
                       'expires_at_ms': int(time.time()*1000) + result['expires_in']*1000}
                _save(row)
            return row['access_token']

    def rpc(self, binding, name, args=None, *, token=None):
        bearer = token or self._access(binding)
        payload = {
            'jsonrpc': '2.0', 'id': uuid.uuid4().hex, 'method': 'tools/call',
            'params': {'name': name, 'arguments': args or {}}}
        try:
            value = _http(binding['origin'], '/mcp', bearer=bearer, payload=payload)
        except BotSeshError as error:
            if token is not None or error.status != 401:
                raise
            # Clock drift or a sleep/wake token expiry gets exactly one serialized
            # refresh. A competing request's newer token is reused, not rotated.
            value = _http(binding['origin'], '/mcp', bearer=self._access(binding, rejected=bearer), payload=payload)
        if value.get('jsonrpc') != '2.0' or value.get('id') != payload['id']:
            raise BotSeshError('invalid_mcp_response')
        result = value.get('result')
        if value.get('error') or not isinstance(result, dict) or result.get('isError'):
            # Never echo server error prose, room content or the HTTP request.
            detail = result.get('structuredContent', {}) if isinstance(result, dict) else {}
            code = detail.get('code', 'mcp_operation_denied') if isinstance(detail, dict) else 'mcp_operation_denied'
            if not isinstance(code, str) or not re.fullmatch(r'[a-z0-9_-]{1,80}', code):
                code = 'mcp_operation_denied'
            status = detail.get('status') if isinstance(detail, dict) else None
            raise BotSeshError(code, status if isinstance(status, int) and 400 <= status < 600 else None,
                               rejected=isinstance(result, dict) and result.get('isError') is True)
        data = result.get('structuredContent')
        if not isinstance(data, dict):
            raise BotSeshError('invalid_mcp_response')
        if name != 'read_tool_page' and data.get('next_cursor'):
            cursor, fragments, seen, size = data['next_cursor'], [], set(), 0
            while cursor:
                if cursor in seen or len(seen) >= 100:
                    raise BotSeshError('context_limit')
                seen.add(cursor)
                page = self.rpc(binding, 'read_tool_page', {'room_id': args.get('room_id'), 'cursor': cursor}, token=token)
                if not isinstance(page.get('json_fragment'), str):
                    raise BotSeshError('invalid_mcp_page')
                fragments.append(page['json_fragment'])
                size += len(page['json_fragment'].encode('utf-8'))
                if size > 160000:
                    raise BotSeshError('context_limit')
                cursor = page.get('next_cursor')
            try:
                data = json.loads(''.join(fragments))
            except ValueError:
                raise BotSeshError('invalid_mcp_page') from None
            if not isinstance(data, dict):
                raise BotSeshError('invalid_mcp_page')
        return data

    def connect(self, *, origin='https://botsesh.com', open_browser=True, timeout=180, room_hint=None, name=None):
        name = preferred_name(name)  # Refuse invalid names before creating a grant.
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
            assigned = self.rpc(provisional, 'hermes_name', {'room_id': room_id, 'display_name': name, 'assign': True}, token=fields['access_token']).get('participant')
            verified = self.rpc(provisional, 'list_rooms', token=fields['access_token']).get('rooms', [])
            exact = next((r for r in verified if r.get('room_id') == room_id and r.get('participant_id') == room['participant_id']), None)
            if not assigned or assigned.get('id') != room['participant_id'] or not exact or exact.get('display_name') != assigned.get('name'):
                raise BotSeshError('agent_name_not_confirmed')
            room = exact
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
            if not binding.get('active') and binding.get('state') == 'revoked':
                return
            # A closed DM is not revocation of its still-live source grant.
            root = self.store.read()['rooms'].get(binding.get('oauth_root'), binding)
            row = self._credential(root)
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
            def disable(data):
                for room in data['rooms'].values():
                    if room.get('oauth_id') == binding['oauth_id']:
                        room.update(active=False, state='revoked')
            self.store.update(disable)
            _save(remove=row['id'])

    def discover(self, binding):
        """Only server-authorized exact DM descendants, never names/invitations."""
        rooms = self.rpc(binding, 'list_rooms').get('rooms', [])
        root = binding.get('oauth_root', binding['room_id'])
        accepted = [room for room in rooms if room.get('room_id') == root or room.get('source_room_id') == root and room.get('room_type') == 'dm']
        if not any(room.get('room_id') == root for room in accepted):
            raise BotSeshError('authorization_revoked', 401)
        for room in accepted:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', room.get('room_id', '')) or not room.get('participant_id'):
                raise BotSeshError('invalid_room_binding')
        with _lock(self.store):
            row = self._credential(binding)
            row['extra']['room_ids'] = [room['room_id'] for room in accepted]
            _save(row)
            def enroll(data):
                live = set(row['extra']['room_ids'])
                for saved in data['rooms'].values():
                    if saved.get('oauth_id') == binding['oauth_id'] and saved['room_id'] not in live:
                        saved.update(active=False, state='membership_revoked')
                for room in accepted:
                    if room['room_id'] in data['rooms']:
                        continue
                    rid = room['room_id']
                    data['rooms'][rid] = {'room_id': rid, 'url': binding['origin']+'/b/'+rid, 'origin': binding['origin'],
                        'name': room['name'], 'display_name': room['display_name'], 'participant_id': room['participant_id'],
                        'oauth_id': binding['oauth_id'], 'oauth_root': root, 'room_type': room.get('room_type', 'room'),
                        'active': True, 'state': 'connected', 'transport': 'oauth'}
            self.store.update(enroll)
        return {'rooms': accepted}

    def pending(self, room_id):
        room = self.store.room(room_id)
        for section in ('oauth_posts', 'oauth_turns'):
            for client_id, saved in room.get(section, {}).items():
                if not saved.get('complete') and not saved.get('failed'):
                    return {'client_id': client_id, 'number': None, 'read_effect': 'unconfirmed'}
        return None

    def _journal(self, room_id, section, client_id, exact):
        def save(data):
            rows = data['rooms'][room_id].setdefault(section, {})
            saved = rows.get(client_id)
            if saved:
                if saved['exact'] != exact:
                    raise BotSeshError('pending_send_mismatch')
                return
            # Recheck while holding the registry lock; concurrent gateway/tool
            # callers may not overwrite an earlier uncertain intention.
            other = 'oauth_turns' if section == 'oauth_posts' else 'oauth_posts'
            if client_id in data['rooms'][room_id].get(other, {}):
                raise BotSeshError('pending_send_mismatch')
            if len(rows) >= 1000:
                removable = next((key for key, row in rows.items() if row.get('complete') or row.get('failed')), None)
                if removable is None:
                    raise BotSeshError('operation_journal_full')
                del rows[removable]
            rows[client_id] = {'exact': exact}
        self.store.update(save)
        return self.store.room(room_id)[section][client_id]

    def _journal_rpc(self, binding, section, client_id, name, args):
        try:
            return self.rpc(binding, name, args)
        except BotSeshError as error:
            if error.definitive:
                # A server rejection is settled failure, not an uncertain send.
                # Retain its exact identity/error for replay, without attesting
                # any read or suppressing later inbox work. Unknown outcomes
                # (including process loss after journaling) remain pending.
                self.store.update(lambda d: d['rooms'][binding['room_id']][section][client_id].update(
                    failed={'code': error.code, 'status': error.status}))
            raise

    def finish(self, binding, *, output, receipt_id, client_id, processed=False):
        """One canonical resumable room turn, not another client-side executor.

        Used for explicit structured outputs only. Native prose replies keep
        send_message's exact reply_to semantics. B66's MCP catalog has no routine
        executor or reply_to on finish_room_turn; reject those before any effect.
        """
        require_client_id(client_id)
        intent = parse_room_turn_output(output)
        if 'routine_report' in intent:
            raise BotSeshError('oauth_routine_report_not_supported')
        if intent.get('file_refs'):
            raise BotSeshError('file_bytes_not_supplied')
        if 'failure' not in intent and processed is not True:
            raise BotSeshError('processed_receipt_required')
        room_id = binding['room_id']
        exact = {'output': intent, 'receipt_id': receipt_id, 'client_id': client_id, 'processed': processed}
        room = self.store.room(room_id)
        saved = room.get('oauth_turns', {}).get(client_id)
        if saved and saved['exact'] != exact:
            raise BotSeshError('pending_send_mismatch')
        if saved and saved.get('failed'):
            raise BotSeshError(**saved['failed'], rejected=True)
        if saved and saved.get('complete'):
            self.call(binding, 'verify')
            return saved['result']
        if not saved and receipt_id not in room.get('oauth_receipts', {}):
            raise BotSeshError('receipt_expired')
        if not saved:
            saved = self._journal(room_id, 'oauth_turns', client_id, exact)
        result = self._journal_rpc(binding, 'oauth_turns', client_id, 'finish_room_turn', {'room_id': room_id, 'batch_id': receipt_id,
                          'client_id': client_id, 'output': intent})
        if 'failure' in intent:
            failure = result.get('failure') or {}
            if (result.get('read_effect') != 'none' or failure.get('taskId') != intent['failure']['task_id']
                    or failure.get('status') != intent['failure']['status']):
                raise BotSeshError('task_failure_unconfirmed_retry_same_intent')
        else:
            number = result.get('reply_number')
            if ('reply' in intent and not is_number(number) or 'reply' not in intent and number is not None
                    or not is_number(result.get('read_checkpoint'))):
                raise BotSeshError('turn_completion_unconfirmed_retry_same_intent')
            result = {**result, 'read_effect': 'processed_frozen_receipt', **({'number': number} if number is not None else {})}
        def complete(data):
            room = data['rooms'][room_id]
            room['oauth_turns'][client_id].update(complete=True, result=result)
            if 'failure' not in intent:
                room.get('oauth_receipts', {}).pop(receipt_id, None)
        self.store.update(complete)
        return result

    def call(self, binding, op, **args):
        room_id = binding['room_id']
        if op == 'name':
            params = {'room_id': room_id}
            if args.get('display_name') is not None:
                params['display_name'] = preferred_name(args['display_name'])
            saved = self.rpc(binding, 'hermes_name', params).get('participant')
            verified = self.rpc(binding, 'hermes_name', {'room_id': room_id}).get('participant')
            if not saved or not verified or verified.get('id') != binding['participant_id'] or saved.get('id') != verified['id'] or saved.get('name') != verified.get('name'):
                raise BotSeshError('agent_name_not_confirmed')
            self.store.update(lambda d: d['rooms'][room_id].update(display_name=verified['name']))
            return {'display_name': verified['name']}
        if op == 'discover':
            return self.discover(binding)
        if op == 'dm_context':
            rows = self.rpc(binding, 'list_rooms').get('rooms', [])
            exact = [r for r in rows if r.get('room_id') == room_id
                     and r.get('participant_id') == binding['participant_id'] and r.get('room_type') == 'dm']
            if len(exact) != 1:
                raise BotSeshError('membership_revoked', 403)
            return exact[0]
        if op == 'dm_control':
            if args.get('action') not in ('manifest', 'send', 'update', 'retire'):
                raise BotSeshError('dm_control_action_denied')
            return self.rpc(binding, 'dm_controls', {'room_id': room_id, 'action': args['action'], 'payload': args['payload']})
        if op == 'edit':
            return self.rpc(binding, 'hermes_edit', {'room_id': room_id, 'number': args['number'], 'text': args['text'], 'client_id': args['client_id']})
        if op == 'wait':
            after = args.get('after', -1)
            since = args.get('since')
            if not is_number(after, -1) or since is not None and (not isinstance(since, str) or not 1 <= len(since) <= 256):
                raise BotSeshError('invalid_event_cursor')
            value = self.rpc(binding, 'wait_for_messages', {'room_id': room_id, 'after': after, 'timeout_seconds': 25, **({'since': since} if since else {})})
            if not is_number(value.get('next_after'), -1) or not isinstance(value.get('since'), str) or not 1 <= len(value['since']) <= 256:
                raise BotSeshError('invalid_event_cursor')
            return value  # The adapter persists BOTH cursors, never as a read checkpoint.
        if op == 'listener':
            if args.get('state') not in ('listening', 'stopped'):
                raise BotSeshError('listener_state_denied')
            return self.rpc(binding, 'hermes_listener', {'room_id': room_id, 'state': args['state']})
        if op == 'delivery':
            return self.rpc(binding, 'hermes_delivery', {'room_id': room_id, 'number': args['target'], 'state': args['state'], **({k: args[k] for k in ('failure_code', 'notice_client_id') if args.get(k)})})
        if op == 'working':
            return self.rpc(binding, 'working', {'room_id': room_id, 'status': 'Hermes is working…', **({'mention_number': args['target']} if args.get('target') is not None else {})})
        if op in ('verify', 'pending'):
            rooms = self.rpc(binding, 'list_rooms').get('rooms', [])
            if not any(room['room_id'] == room_id and room['participant_id'] == binding['participant_id'] for room in rooms):
                raise BotSeshError('identity_mismatch', 401)
            return {'pending': self.pending(room_id), 'participantId': binding['participant_id']}
        if op == 'check':
            pending = self.pending(room_id)
            if pending:
                self.call(binding, 'verify')
                return {'pending': pending, 'hasMore': False, 'mentions': [], 'replies': []}
            inbox = self.rpc(binding, 'hermes_inbox', {'room_id': room_id})
            return {'pending': None, 'hasMore': inbox['has_more'], 'mentions': [target['number'] for target in inbox['targets'] if not target.get('takeover')], 'takeover_targets': [target for target in inbox['targets'] if target.get('takeover')], 'offers': inbox.get('offers', []), 'replies': [], 'backlog': inbox['backlog']}
        if op in ('takeover_accept', 'takeover_pickup', 'takeover_claim', 'takeover_settle'):
            action = op.removeprefix('takeover_')
            payload = {'room_id': room_id, 'action': action, 'event_id': args['event_id']}
            if action == 'claim':
                if args.get('processed') is not True or args.get('receipt_id') not in self.store.room(room_id).get('oauth_receipts', {}):
                    raise BotSeshError('processed_receipt_required')
                payload.update(batch_id=args['receipt_id'], reason=args['reason'], processed=True)
            return self.rpc(binding, 'hermes_takeover', payload)
        if op == 'read':
            value = self.rpc(binding, 'catchup', {'room_id': room_id, 'max_chars': 150000, 'max_pages': 100, **({'target_number': args['target_number']} if args.get('target_number') is not None else {})})
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

            receipt_id = value['batch_id']
            # Preserve the whole exact transport result, not a clipped summary.
            result = {'receipt_id': receipt_id, 'read_effect': 'none', 'briefing': [value],
                      'batch': {'messages': messages, 'hasMore': False, 'readCheckpoint': value['read_checkpoint'],
                                'nextCheckpoint': value['next_checkpoint']}}
            if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 160000:
                raise BotSeshError('context_limit')
            def save_receipt(data):
                receipts = data['rooms'][room_id].setdefault('oauth_receipts', {})
                used = {r.get('receipt_id') for r in data['rooms'][room_id].get('processing_receipts', {}).values() if r.get('state') not in ('replied', 'failed')}
                for key in list(receipts):
                    if receipts[key]['expires'] <= time.time() and key not in used:
                        del receipts[key]
                if len(receipts) >= 90:
                    raise BotSeshError('receipt_limit_retry_pending_turn')
                receipts[receipt_id] = {'expires': time.time()+3600, 'numbers': [m['number'] for m in messages]}
            self.store.update(save_receipt)
            return result
        if op == 'fail_task':
            from .task_failure import validate_failure, confirm_failure
            if set(args) - {'failure', 'receipt_id', 'client_id'}:
                raise BotSeshError('invalid_task_failure')
            failure = validate_failure(args.get('failure'))
            client_id, receipt_id = args.get('client_id'), args.get('receipt_id')
            require_client_id(client_id)
            if not receipt_id or receipt_id not in self.store.room(room_id).get('oauth_receipts', {}):
                raise BotSeshError('receipt_scope')
            if 'task_id' in failure:
                result = self.finish(binding, output={'failure': failure}, receipt_id=receipt_id, client_id=client_id)
            else:
                if failure['message_number'] not in self.store.room(room_id)['oauth_receipts'][receipt_id]['numbers']:
                    raise BotSeshError('receipt_scope')
                result = self.rpc(binding, 'hermes_cant_do', {'room_id':room_id, 'batch_id':receipt_id,
                    'client_id':client_id, 'number':failure['message_number'], 'message_version':failure['message_version'],
                    'reason':failure['reason'], **({'partial_notes':failure['partial_notes']} if 'partial_notes' in failure else {})})
            return confirm_failure(result, failure.get('task_id', failure))
        if op == 'finish':
            return self.finish(binding, **args)
        if op == 'post':
            receipt_id, client_id = args.get('receipt_id'), args.get('client_id')
            require_client_id(client_id)
            text = args.get('text')
            intent = parse_room_turn_output({'reply': text, **{k: args[k] for k in ('draft_ready', 'routine_report') if k in args}})
            if 'draft_ready' in args or 'routine_report' in args:
                # Canonical MCP finish owns resumable structured effects; never
                # spawn the invitation SDK or silently drop a requested target.
                if args.get('reply_to') is not None:
                    raise BotSeshError('oauth_structured_reply_target_not_supported')
                result = self.finish(binding, output=intent, receipt_id=receipt_id, client_id=client_id, processed=args.get('processed'))
                return {'client_id': client_id, 'number': result['reply_number'], 'read_effect': result['read_effect']}
            # Native runtime notices use an explicit no-read server capability,
            # NEVER implicit-last-batch send_message semantics.
            if args.get('processed') is not True:
                result = self.rpc(binding, 'hermes_notice', {'room_id': room_id, 'text': text, 'client_id': client_id, **({'reply_to': args['reply_to']} if args.get('reply_to') is not None else {}), **({'failure_code': args['failure_code']} if args.get('failure_code') else {})})
                message = result.get('message') or {}
                if message.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest() or message.get('authorParticipant', {}).get('id') != binding['participant_id']:
                    raise BotSeshError('send_not_confirmed_retry_same_intent')
                return {'client_id': client_id, 'number': message['number'], 'read_effect': 'none'}
            if not receipt_id:
                raise BotSeshError('processed_receipt_required')
            exact = {k: args[k] for k in ('text', 'client_id', 'receipt_id', 'processed', 'reply_to') if k in args}
            current = self.store.room(room_id)
            saved = current.get('oauth_posts', {}).get(client_id)
            if saved and saved['exact'] != exact:
                raise BotSeshError('pending_send_mismatch')
            if saved and saved.get('failed'):
                raise BotSeshError(**saved['failed'], rejected=True)
            if saved and saved.get('complete'):
                self.call(binding, 'verify')
                return saved['result']
            receipt = current.get('oauth_receipts', {}).get(receipt_id)
            if not saved and not receipt:
                raise BotSeshError('receipt_expired')
            if not saved and args.get('reply_to') is not None and args['reply_to'] not in receipt['numbers']:
                raise BotSeshError('unread_reply_target')
            if not saved:
                self._journal(room_id, 'oauth_posts', client_id, exact)
            result = self._journal_rpc(binding, 'oauth_posts', client_id, 'send_message', {'room_id': room_id, 'text': text,
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
            turn = self.store.room(room_id).get('oauth_turns', {}).get(args.get('client_id'))
            if turn:
                return self.finish(binding, **turn['exact'])
            saved = self.store.room(room_id).get('oauth_posts', {}).get(args.get('client_id'))
            if not saved:
                raise BotSeshError('original_operation_required')
            return self.call(binding, 'post', **saved['exact'])
        raise BotSeshError('oauth_operation_not_supported')
