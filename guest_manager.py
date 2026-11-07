"""Python guest creation + exact-room management, NOT an invitation/listener port.

Secrets and pre-HTTP intentions live in a separate 0600 Store below the profile's
0700 credentials directory. No OAuth grant, cookie jar, proxy or JS fallback.
"""
from __future__ import annotations
import hashlib
import json
import re
import secrets
import unicodedata
import uuid

from .client import BotSeshError
from .storage import Store
from .room_turn import require_client_id, is_number

DEFAULT_ORIGIN = 'https://botsesh.com'
ROOM_ID = r'[A-Za-z0-9_-]{32}'
TOKEN = r'bsa_[A-Za-z0-9_-]{43}'
PUBLIC_CREATE = ('state', 'room_id', 'name', 'share_link', 'creator_role',
                 'participant_id', 'owner_state', 'expires_at', 'requires_human')


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _http(origin, method, path, *, payload=None, bearer=None):
    """Only the four existing guest/manager operations, bounded and cookie-free."""
    import httpx
    from .oauth import checked_origin
    from .http_transport import MAX_BYTES, retry_after_ms
    checked_origin(origin)
    if not ((method == 'POST' and path == '/api/agent/rooms' and bearer is None)
            or (bearer and method == 'GET' and _matches('/api/rooms/' + ROOM_ID + '/agent-access', path))
            or (bearer and method == 'POST' and _matches('/api/rooms/' + ROOM_ID +
                r'/manager/ownership-offers(?:/[0-9a-fA-F-]{36}/revoke)?', path))):
        raise BotSeshError('endpoint_denied')
    headers = {'Accept': 'application/json', 'Origin': origin, 'X-Board-Request': '1'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    try:
        with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, origin + path, headers=headers, json=payload) as response:
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > MAX_BYTES:
                        raise BotSeshError('response_limit')
                status = response.status_code
                retry = retry_after_ms(response.headers.get('retry-after', '0'))
    except httpx.HTTPError:
        raise BotSeshError('connection_failed_retry_same_intent') from None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise BotSeshError('invalid_server_response' if 200 <= status < 300 else 'request_denied', status, retry) from None
    if not 200 <= status < 300:
        code = value.get('code')
        raise BotSeshError(code if _matches(r'[a-z0-9_-]{1,80}', code) else 'request_denied', status, retry)
    return value


class GuestManagerClient:
    def __init__(self, store):
        self.store = store
        self.private = Store(store.credentials / 'python-guest')

    def create(self, name, purpose=None, client_id=None, *, origin=None):
        from .oauth import checked_origin
        origin = checked_origin(DEFAULT_ORIGIN if origin is None else origin)
        if (not isinstance(name, str) or not name.strip() or len(name.strip().encode('utf-16-le')) > 160
                or any(unicodedata.category(c) in ('Cc', 'Cf') for c in name)
                or purpose is not None and (not isinstance(purpose, str) or len(purpose.encode('utf-16-le')) > 48000)):
            raise BotSeshError('invalid_creation')
        if client_id is not None:
            require_client_id(client_id)
        payload = {'name': name, 'purpose': purpose if purpose is not None else ''}
        # Preserve pending/committed Advanced SDK intent across upgrades: a
        # different private connection would create a second room. Recover that
        # exact legacy operation through its original Advanced store instead.
        legacy_key = 'create-' + hashlib.sha256(json.dumps([name, payload['purpose'], client_id], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        legacy_file = self.store.credentials / ('botsesh-agent-' + hashlib.sha256((origin + '\0' + legacy_key).encode()).hexdigest() + '.json')
        if legacy_file.exists() or legacy_file.is_symlink():
            raise BotSeshError('legacy_creation_retry_requires_original_advanced_store')
        intent_key = _digest([origin, payload, client_id])
        def prepare(data):
            connections = data.setdefault('connections', {})
            key = connections.setdefault(origin, secrets.token_urlsafe(32))
            if not _matches(r'[A-Za-z0-9_-]{43}', key):
                raise BotSeshError('private_creation_state_invalid')
            intents = data.setdefault('creations', {})
            if intent_key not in intents:
                if len(intents) >= 10000:
                    raise BotSeshError('operation_journal_full')
                operation = client_id if client_id is not None else str(uuid.uuid4())
                request = {**payload, 'client_id': operation, 'connection_key': key}
                # An explicit UUID is one exact intent, even if a caller changes
                # the name/purpose and would otherwise select a different hash.
                for old in intents.values():
                    if old['origin'] == origin and old['request']['client_id'] == operation and old['request'] != request:
                        raise BotSeshError('client_id_conflict')
                intents[intent_key] = {'origin': origin, 'request': request}
            saved = intents[intent_key]
            operation = saved.get('request', {}).get('client_id')
            require_client_id(operation)
            expected = {**payload, 'client_id': client_id if client_id is not None else operation, 'connection_key': key}
            if saved.get('origin') != origin or saved.get('request') != expected:
                raise BotSeshError('private_creation_state_invalid')
        intent = self.private.update(prepare)['creations'][intent_key]
        if intent.get('room_id'):
            binding = self.store.read()['rooms'].get(intent['room_id'])
            if binding and not binding.get('active'):
                raise BotSeshError('room_detached')
        # Always retry the exact saved body. No automatic new UUID on any error.
        data = _http(origin, 'POST', '/api/agent/rooms', payload=intent['request'])
        room_id, member, access = data.get('room_id'), data.get('membership'), data.get('access')
        if (not _matches(ROOM_ID, room_id) or data.get('share_link') != origin + '/b/' + room_id
                or data.get('state') != 'created' or data.get('creator_role') != 'agent_manager'
                or data.get('owner_state') != 'no_human_owner_yet'
                or not isinstance(member, dict) or not isinstance(access, dict)
                or not _matches(TOKEN, access.get('token'))
                or not isinstance(member.get('id'), str) or not member['id']
                or not isinstance(data.get('participant_id'), str) or not data['participant_id']
                or member.get('participantId') != data['participant_id']
                or not all(is_number(n) for n in (member.get('expiresAt'), access.get('expiresAt'), data.get('expires_at')))
                or not isinstance(data.get('name'), str) or not isinstance(data.get('requires_human'), str)):
            raise BotSeshError('invalid_creation_response')
        saved = {'origin': origin, 'room_id': room_id, 'participant_id': data['participant_id'],
                 'membership_id': member['id'], 'access_token': access['token']}
        def adopt(private):
            old = private['rooms'].get(room_id)
            current = private['creations'][intent_key]
            if (old is not None and old != saved) or current.get('room_id', room_id) != room_id:
                raise BotSeshError('identity_mismatch')
            private['rooms'][room_id] = saved
            current['room_id'] = room_id
        self.private.update(adopt)  # Credential must survive verify/output loss.
        binding = {'transport': 'guest_manager', 'management_only': True,
                   'url': data['share_link'], 'room_id': room_id, 'origin': origin,
                   'participant_id': saved['participant_id'], 'membership_id': saved['membership_id'],
                   'name': data['name'], 'active': True, 'state': 'created',
                   'manager': True, 'expires_at': data['expires_at']}
        self.verify(binding)
        def enroll(registry):
            old = registry['rooms'].get(room_id)
            if old is not None:
                if any(old.get(k) != binding[k] for k in ('transport', 'origin', 'room_id', 'participant_id', 'membership_id', 'url')):
                    raise BotSeshError('identity_mismatch')
                if not old.get('active'):
                    raise BotSeshError('room_detached')
                return  # Do not reset local progress or state on a retry.
            registry['rooms'][room_id] = binding
        self.store.update(enroll)
        return {**{key: data[key] for key in PUBLIC_CREATE}, 'management_only': True}

    def _credential(self, binding):
        from .oauth import checked_origin
        origin, room_id = binding.get('origin'), binding.get('room_id')
        checked_origin(origin)
        if (binding.get('transport') != 'guest_manager' or not _matches(ROOM_ID, room_id)
                or binding.get('url') != origin + '/b/' + room_id):
            raise BotSeshError('identity_mismatch')
        saved = self.private.read()['rooms'].get(room_id)
        if (not saved or any(saved.get(k) != binding.get(k) for k in ('origin', 'room_id', 'participant_id', 'membership_id'))
                or not _matches(TOKEN, saved.get('access_token'))):
            raise BotSeshError('identity_mismatch')
        return saved

    def verify(self, binding):
        saved = self._credential(binding)
        value = _http(saved['origin'], 'GET', '/api/rooms/' + saved['room_id'] + '/agent-access', bearer=saved['access_token'])
        member = value.get('membership')
        if (not isinstance(member, dict) or member.get('id') != saved['membership_id']
                or member.get('participantId') != saved['participant_id'] or value.get('participantId') != saved['participant_id']
                or not all(is_number(n) for n in (member.get('expiresAt'), value.get('accessExpiresAt'), value.get('readCheckpoint')))):
            raise BotSeshError('identity_mismatch', 401)
        # Verification is not a read acknowledgment or an OAuth enrollment.
        return {'state': 'verified', 'room_id': saved['room_id'], 'participant_id': saved['participant_id']}

    def call(self, binding, op, **args):
        if op == 'verify':
            return self.verify(binding)
        if op == 'offer_ownership':
            if args.get('user_requested') is not True:
                raise BotSeshError('explicit_request_required')
            require_client_id(args.get('client_id'))
            suffix = '/manager/ownership-offers'
            payload = {'client_id': args['client_id'], 'user_requested': True}
        elif op == 'revoke_ownership_offer':
            require_client_id(args.get('offer_id'))
            suffix = '/manager/ownership-offers/' + args['offer_id'] + '/revoke'
            payload = {}
        else:
            raise BotSeshError('guest_management_only_separate_room_connection_required')
        saved = self._credential(binding)
        self.verify(binding)  # Fresh exact membership even on completed retries.
        value = _http(saved['origin'], 'POST', '/api/rooms/' + saved['room_id'] + suffix,
                      payload=payload, bearer=saved['access_token'])
        if op == 'revoke_ownership_offer':
            if value.get('state') != 'offer_revoked' or value.get('offer_id') != args['offer_id']:
                raise BotSeshError('invalid_ownership_response')
            return {'state': 'offer_revoked', 'offer_id': value['offer_id']}
        if value.get('room_id') != saved['room_id'] or not isinstance(value.get('requires_human'), str):
            raise BotSeshError('invalid_ownership_response')
        if value.get('state') == 'already_owned' and value.get('owner_link') == saved['origin'] + '/b/' + saved['room_id'] + '#settings':
            return {k: value[k] for k in ('state', 'room_id', 'owner_link', 'requires_human')}
        require_client_id(value.get('offer_id'))
        link = saved['origin'] + '/api/rooms/' + saved['room_id'] + '/ownership#offer=' + value['offer_id'] + '.'
        if (value.get('state') != 'ownership_offered' or not is_number(value.get('expires_at'))
                or not isinstance(value.get('claim_link'), str) or not value['claim_link'].startswith(link)
                or not _matches(r'[A-Za-z0-9_-]{43}', value['claim_link'][len(link):])):
            raise BotSeshError('invalid_ownership_response')
        return {k: value[k] for k in ('state', 'room_id', 'offer_id', 'claim_link', 'expires_at', 'requires_human')}
