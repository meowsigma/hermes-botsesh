"""Operator-only room lifecycle. No model call, Node, cookies or global config.

Admission secrets and pending mutations are fsynced before network/output. The
normal room/tool listings contain only public URLs, never admission credentials.
"""
from __future__ import annotations
import hashlib
import contextlib
import io
import json
import re
import secrets
import uuid
from .client import BotSeshError
from .storage import Store
from .room_turn import require_client_id

SCOPES = ['people:invite', 'password:rotate']
NO_ROOM_PASSWORD = 'No room password: anyone with the link can join. Run rotate-password to set one.'
ACTIONS = {'create-room', 'rooms', 'room-info', 'invite', 'invites', 'revoke-invite',
           'rotate-password', 'forget', 'send'}


def private_store(store):
    return Store(store.credentials / 'room-lifecycle')


def remember_access(store, room_id, join_url):
    def remember(data):
        data['rooms'].setdefault(room_id, {'room_id': room_id, 'join_url': join_url,
                                         'password': None, 'password_required': False})
    return private_store(store).update(remember)['rooms'][room_id]


def creation_result(store, result):
    saved = remember_access(store, result['room_id'], result['share_link'])
    return {**result, 'join_url': saved['join_url'], 'password': saved['password'],
            'password_required': saved['password_required'],
            **({'password_notice': NO_ROOM_PASSWORD} if saved['password_required'] is False else {}),
            'credential_recovery': 'hermes botsesh room-info ' + result['room_id'] + ' --show-password'}


def resolve(store, reference):
    rooms = store.read()['rooms']
    if reference in rooms:
        return reference
    matches = [rid for rid, r in rooms.items()
               if r.get('url') == reference or r.get('name', '').casefold() == reference.casefold()]
    if len(matches) != 1:
        raise BotSeshError('room_ambiguous_use_id' if matches else 'room_not_stored')
    return matches[0]


def room_info(store, room_id, show_password=False):
    binding = store.read()['rooms'][room_id]
    result = {key: binding[key] for key in ('room_id', 'name', 'active', 'state', 'management_only') if key in binding}
    result['join_url'] = binding['url']
    saved = private_store(store).read()['rooms'].get(room_id)
    result['password_stored'] = saved is not None
    if saved is not None:
        result['password_required'] = saved.get('password_required')
        if saved.get('password_required') is False:
            result['password_notice'] = NO_ROOM_PASSWORD
        if show_password:
            result['password'] = saved['password']
    elif show_password:
        result['password'] = None
        result['password_state'] = 'unknown_not_created_or_rotated_by_this_profile'
    return result


def _http(origin, room_id, method, operation, *, bearer, payload=None):
    import httpx
    from .guest_manager import checked_guest_origin
    from .http_transport import MAX_BYTES, retry_after_ms
    checked_guest_origin(origin)
    if (not re.fullmatch(r'[A-Za-z0-9_-]{32}', room_id)
            or not ((operation == 'people-invites' and method in ('GET', 'POST'))
                    or (operation in ('rotate-password', 'messages') and method == 'POST')
                    or (re.fullmatch(r'people-invites/[0-9a-fA-F-]{36}/revoke', operation) and method == 'POST'))):
        raise BotSeshError('endpoint_denied')
    try:
        with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, origin + '/api/agent/rooms/' + room_id + '/lifecycle/' + operation,
                               headers={'Authorization': 'Bearer ' + bearer, 'Accept': 'application/json'},
                               json=payload) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
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
        code = value.get('code', value.get('error'))
        raise BotSeshError(code if isinstance(code, str) and re.fullmatch('[a-z0-9_-]{1,80}', code) else 'request_denied', status, retry)
    return value


class RoomLifecycle:
    def __init__(self, client):
        self.client, self.store = client, client.store
        self.private = private_store(self.store)

    def request(self, room_id, method, operation, payload=None):
        binding = self.store.room(room_id)
        if binding.get('transport') == 'guest_manager':
            from .guest_manager import GuestManagerClient
            manager = GuestManagerClient(self.store)
            manager.verify(binding)
            token = manager._credential(binding)['access_token']
            return _http(binding['origin'], room_id, method, operation, bearer=token, payload=payload)
        if binding.get('transport') == 'oauth':
            from .oauth import AUTH_VERSION
            from .http_transport import provider_request
            if binding.get('auth_version') != AUTH_VERSION:
                raise BotSeshError('oauth_upgrade_sign_in_required')
            return provider_request(binding['origin'], method,
                '/api/agent/rooms/' + room_id + '/lifecycle/' + operation, payload=payload)
        raise BotSeshError('oauth_or_python_created_room_required')

    def create(self, title, purpose=None, client_id=None, origin=None):
        from .guest_manager import GuestManagerClient
        return GuestManagerClient(self.store).create(title, purpose, client_id, origin=origin, lifecycle_scopes=SCOPES)

    def send(self, room_id, text, client_id=None):
        if not isinstance(text, str) or not text.strip() or len(text.encode('utf-16-le')) > 16000:
            raise BotSeshError('invalid_message')
        operation = client_id if client_id is not None else self.client.logical_id('cli-send', {'room_id': room_id, 'text': text})
        require_client_id(operation)
        # The server owns deduplication; a durable intent survives output loss.
        exact = {'client_id': operation, 'text': text}
        key = room_id + ':' + operation
        def prepare(data):
            rows = data.setdefault('sends', {})
            if key in rows and rows[key]['payload'] != exact:
                raise BotSeshError('pending_send_mismatch')
            rows.setdefault(key, {'room_id': room_id, 'payload': exact})
        self.private.update(prepare)
        result = self.request(room_id, 'POST', 'messages', exact)
        if (result.get('client_id') != operation or result.get('read_effect') != 'none'
                or result.get('participant_id') != self.store.room(room_id)['participant_id']
                or result.get('text_sha256') != hashlib.sha256(text.encode()).hexdigest()
                or not isinstance(result.get('number'), int)):
            raise BotSeshError('send_not_confirmed_retry_same_intent')
        return result

    def invite(self, room_id, email=None, client_id=None):
        payload = {'room_id': room_id, **({'email': email} if email is not None else {})}
        operation = client_id if client_id is not None else self.client.logical_id('people-invite', payload)
        require_client_id(operation)
        def create():
            return self.request(room_id, 'POST', 'people-invites', {'client_id': operation, **({'email': email} if email is not None else {})})
        try:
            result = create()
        except BotSeshError as error:
            if client_id is not None or error.code != 'invite_ended' or error.status != 410:
                raise
            # Only a definitive ended link can renew its default intention.
            # A timeout/unknown response must never manufacture a second invite.
            operation = self.client.logical_id('people-invite', payload, replace=operation)
            result = create()
        require_client_id(result.get('invitation_id'))
        binding = self.store.room(room_id)
        expected = binding['url'] + '#people-invite=' + result['invitation_id'] + '.'
        if (result.get('room_id') != room_id or not isinstance(result.get('join_url'), str)
                or not result['join_url'].startswith(expected)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', result['join_url'][len(expected):])):
            raise BotSeshError('invite_not_confirmed_retry_same_intent')
        self.private.update(lambda d: d.setdefault('invites', {}).update({room_id + ':' + operation: result}))
        return result

    def rotate(self, room_id, client_id=None):
        if client_id is not None:
            require_client_id(client_id)
        binding = self.store.room(room_id)
        selected = []
        def prepare(data):
            pending = data.setdefault('rotations', {})
            key = room_id + ':' + client_id if client_id else data.setdefault('pending_rotations', {}).get(room_id)
            if not key:
                key = room_id + ':' + str(uuid.uuid4())
                data['pending_rotations'][room_id] = key
            if key not in pending:
                pending[key] = {'room_id': room_id, 'client_id': key.split(':', 1)[1], 'password': secrets.token_urlsafe(24)}
            selected.append(key)
        data = self.private.update(prepare)
        key = selected[0]
        saved = data['rotations'][key]
        try:
            result = self.request(room_id, 'POST', 'rotate-password', {'client_id': saved['client_id'], 'password': saved['password']})
        except BotSeshError as error:
            if error.code in ('rotation_conflict', 'rotation_superseded', 'client_id_conflict') and error.status == 409:
                def ended(data):
                    data['rotations'][key]['failed'] = error.code
                    if data.get('pending_rotations', {}).get(room_id) == key:
                        del data['pending_rotations'][room_id]
                self.private.update(ended)
            raise
        if result.get('room_id') != room_id or result.get('client_id') != saved['client_id'] or result.get('password_required') is not True:
            raise BotSeshError('rotation_not_confirmed_retry_same_intent')
        def finish(data):
            data['rooms'][room_id] = {'room_id': room_id, 'join_url': binding['url'], 'password': saved['password'], 'password_required': True}
            data['rotations'][key]['complete'] = True
            if data.get('pending_rotations', {}).get(room_id) == key:
                del data['pending_rotations'][room_id]
        self.private.update(finish)
        return {**result, 'join_url': binding['url'], 'password': saved['password']}


def forget(client, room_ids):
    """Local cleanup, never revocation. Preserve unrelated grant/DM credentials."""
    from hermes_cli.config import load_config, set_config_value
    from .guest_manager import GuestManagerClient
    rooms = client.store.read()['rooms']
    removed = set(room_ids)
    cfg = load_config()
    platform = cfg.get('platforms', {}).get('botsesh', {})
    allowed = platform.get('extra', {}).get('group_allowed_chats', [])
    with contextlib.redirect_stdout(io.StringIO()):
        set_config_value('platforms.botsesh.extra.group_allowed_chats', json.dumps([r for r in allowed if r not in removed]))
    home = platform.get('home_channel')
    if (isinstance(home, dict) and home.get('chat_id') in removed) or (isinstance(home, str) and home in removed):
        # Hermes validates this field as a mapping. {} clears its target;
        # None/null is rejected by the native config writer.
        with contextlib.redirect_stdout(io.StringIO()):
            set_config_value('platforms.botsesh.home_channel', '{}')
    private = private_store(client.store)
    def scrub(data):
        for rid in removed:
            data['rooms'].pop(rid, None)
            data.get('pending_rotations', {}).pop(rid, None)
        for section in ('sends', 'rotations', 'invites'):
            for key in list(data.get(section, {})):
                if key.split(':', 1)[0] in removed:
                    del data[section][key]
    private.update(scrub)
    guest = GuestManagerClient(client.store).private
    def scrub_guest(data):
        for rid in removed:
            data['rooms'].pop(rid, None)
        for key, intent in list(data.get('creations', {}).items()):
            if intent.get('room_id') in removed:
                # Retain only the deduplication tombstone, not room content or
                # a reconstructable per-operation creation credential.
                data['creations'][key] = {'forgotten': True, 'room_id': intent['room_id'],
                    'origin': intent['origin'], 'request': {'client_id': intent['request']['client_id']}}
    guest.update(scrub_guest)
    remaining = {rid: r for rid, r in rooms.items() if rid not in removed}
    unused = {r['oauth_id'] for rid, r in rooms.items() if rid in removed and r.get('oauth_id')} - {r['oauth_id'] for r in remaining.values() if r.get('oauth_id')}
    if unused:
        from .oauth import _save
        for grant in unused:
            _save(remove=grant)
    def remove(data):
        for rid in removed:
            data['rooms'].pop(rid, None)
            data.setdefault('forgotten_rooms', {})[rid] = True
    client.store.update(remove)
    return {'forgotten': sorted(removed), 'server_membership': 'unchanged; local cleanup is not revocation'}


def setup(sub):
    create = sub.add_parser('create-room', help='Create and join a passwordless guest room; privately save access details')
    create.add_argument('title')
    create.add_argument('--purpose')
    create.add_argument('--client-id')
    create.add_argument('--dev-origin', default=None, help=__import__('argparse').SUPPRESS)
    sub.add_parser('rooms', help='List this profile’s saved rooms and public URLs')
    info = sub.add_parser('room-info', help='Show a saved room; explicitly recover its admission password')
    info.add_argument('room')
    info.add_argument('--show-password', action='store_true')
    invite = sub.add_parser('invite', help='Create a person-only invitation; optionally prepare an email draft (not sent)')
    invite.add_argument('room')
    invite.add_argument('--email')
    invite.add_argument('--client-id')
    invites = sub.add_parser('invites', help='List this exact connection’s people invitations')
    invites.add_argument('room')
    revoke = sub.add_parser('revoke-invite', help='Revoke this exact connection’s people invitation')
    revoke.add_argument('room')
    revoke.add_argument('invite_id')
    rotate = sub.add_parser('rotate-password', help='Enable password admission; print and privately save a new random password')
    rotate.add_argument('room')
    rotate.add_argument('--client-id')
    prune = sub.add_parser('forget', help='Forget local room credentials and config, without server revocation')
    prune.add_argument('room', nargs='?')
    prune.add_argument('--dead', action='store_true', help='Prune only confirmed 404/410/invalid_grant rooms')
    send = sub.add_parser('send', help='Send exactly once as this seat; no inference or read acknowledgment')
    send.add_argument('--room', required=True)
    send.add_argument('--client-id')
    send.add_argument('text')


def handle(args, client):
    action = args.botsesh_action
    lifecycle = RoomLifecycle(client)
    if action == 'create-room':
        result = lifecycle.create(args.title, args.purpose, args.client_id, args.dev_origin)
    elif action == 'rooms':
        result = {'rooms': [room_info(client.store, rid) for rid in client.store.read()['rooms']]}
    elif action == 'forget':
        if bool(args.room) == bool(args.dead):
            raise BotSeshError('choose_one_room_or_dead')
        if args.dead:
            dead, retained = [], []
            for rid, binding in client.store.read()['rooms'].items():
                # One invalid_grant refresh disables the whole consented grant,
                # including DMs later in this snapshot. Read the current marker
                # so those descendants are not stranded behind missing tokens.
                binding = client.store.read()['rooms'].get(rid, binding)
                if binding.get('state') in ('invalid_grant', 'room_deleted', 'room_expired', 'authorization_revoked', 'revoked'):
                    dead.append(rid)
                    continue
                try:
                    # Detached rooms may still have a valid membership. Do not
                    # call store.room(), which would mistake local state for 404.
                    if binding.get('transport') == 'oauth':
                        from .oauth import OAuthClient
                        OAuthClient(client.store).call(binding, 'verify')
                    elif binding.get('transport') == 'guest_manager':
                        from .guest_manager import GuestManagerClient
                        GuestManagerClient(client.store).verify(binding)
                    else:
                        retained.append({'room_id': rid, 'reason': 'advanced_invitation_not_probed'})
                except BotSeshError as error:
                    if error.status in (404, 410) or error.code == 'invalid_grant':
                        dead.append(rid)
                    else:
                        retained.append({'room_id': rid, 'reason': error.code})
            result = {**forget(client, dead), 'retained': retained}
        else:
            result = forget(client, [resolve(client.store, args.room)])
    else:
        rid = resolve(client.store, args.room)
        if action == 'room-info':
            result = room_info(client.store, rid, args.show_password)
        elif action == 'send':
            result = lifecycle.send(rid, args.text, args.client_id)
        elif action == 'invite':
            result = lifecycle.invite(rid, args.email, args.client_id)
        elif action == 'invites':
            result = lifecycle.request(rid, 'GET', 'people-invites')
        elif action == 'revoke-invite':
            require_client_id(args.invite_id)
            result = lifecycle.request(rid, 'POST', 'people-invites/' + args.invite_id + '/revoke', {})
        elif action == 'rotate-password':
            result = lifecycle.rotate(rid, args.client_id)
        else:
            raise BotSeshError('unknown_room_action')
    print(json.dumps(result, ensure_ascii=False))
