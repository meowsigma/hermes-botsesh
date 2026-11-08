"""Hermes-owned declared OAuth. This module handles room protocol, never tokens."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import re
import time
import unicodedata
import uuid

from .client import BotSeshError
from .storage import Store
from .room_turn import parse_room_turn_output, require_client_id, is_number

CLIENT_ID = 'https://botsesh.com/oauth/clients/hermes.json'
OAUTH_ID = 'rooms'
AUTH_VERSION = 2
SIGN_IN = 'hermes auth add botsesh:rooms'


def sign_in_instructions():
    return ('In your own terminal, in the intended Hermes profile:\n'
            f'  {SIGN_IN}\n'
            'Then run `hermes botsesh connect` to enable the explicitly approved rooms. '
            'Hermes owns sign-in, refresh and logout; this plugin never starts sign-in in a gateway or agent session.')


def preferred_name(value=None):
    if value is None:
        from .identity import current_profile
        value = current_profile()
        if not value or value == 'custom':
            value = 'default'
    if (not isinstance(value, str) or not value.strip()
            or len(value.encode('utf-16-le')) > 80
            or '@' in value or any(unicodedata.category(c) in ('Cc', 'Cf') for c in value)):
        raise BotSeshError('invalid_agent_name')
    return value.strip()


def checked_origin(value):
    # The declared provider is HTTPS-only. Local tests use a test-only HTTPS
    # manifest/transport; production never relaxes the origin to reach a fixture.
    if value != 'https://botsesh.com':
        raise BotSeshError('origin_denied')
    return value


@contextlib.contextmanager
def _lock(store):
    # Serialize local binding discovery/connect/disconnect, not credential refresh.
    import fcntl
    path = store.root / '.oauth.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        Store._check(path)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


class OAuthClient:
    def __init__(self, store=None):
        self.store = store or Store()

    def rpc(self, binding, name, args=None):
        from .http_transport import provider_request
        payload = {'jsonrpc': '2.0', 'id': uuid.uuid4().hex, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': args or {}}}
        value = provider_request(binding['origin'], 'POST', '/mcp', payload=payload)
        if value.get('jsonrpc') != '2.0' or value.get('id') != payload['id']:
            raise BotSeshError('invalid_mcp_response')
        result = value.get('result')
        if value.get('error') or not isinstance(result, dict) or result.get('isError'):
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
                page = self.rpc(binding, 'read_tool_page', {'room_id': (args or {}).get('room_id'), 'cursor': cursor})
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

    def connect(self, *, origin='https://botsesh.com', room_hint=None, name=None, profile=None):
        """Enable already-approved rooms, without invoking or bypassing auth add."""
        from .identity import current_profile, gateway_install_id
        checked_origin(origin)
        profile = profile or current_profile()
        name = preferred_name(name)
        if room_hint and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', room_hint):
            raise BotSeshError('invalid_room_hint')
        with _lock(self.store):
            provisional = {'origin': origin}
            initialized = self.rpc(provisional, 'hermes_initialize', {
                'gateway_id': gateway_install_id(), 'gateway_profile': profile})
            if initialized.get('profile') != profile:
                raise BotSeshError('gateway_identity_not_confirmed_update_server')
            rooms = self.rpc(provisional, 'list_rooms').get('rooms')
            if not isinstance(rooms, list) or not rooms:
                raise BotSeshError('no_approved_rooms_run_hermes_auth_add_botsesh_rooms')
            for room in rooms:
                if (not isinstance(room, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', room.get('room_id', ''))
                        or not isinstance(room.get('participant_id'), str) or not room['participant_id']
                        or room.get('gateway_profile') != profile):
                    raise BotSeshError('invalid_room_binding')
            roots = [r for r in rooms if r.get('room_type') != 'dm']
            if not roots or room_hint and not any(r['room_id'] == room_hint for r in roots):
                raise BotSeshError('room_not_approved_run_hermes_auth_add_botsesh_rooms')
            # Ordinary reconnect does not undo a deliberate rename. Assign only
            # for a new participant; the server allocates collision-safe suffixes.
            saved_rooms = self.store.read()['rooms']
            for room in roots:
                prior = saved_rooms.get(room['room_id'], {})
                if prior.get('participant_id') != room['participant_id'] or prior.get('auth_version') != AUTH_VERSION:
                    assigned = self.rpc(provisional, 'hermes_name', {
                        'room_id': room['room_id'], 'display_name': name, 'assign': True}).get('participant')
                    if not assigned or assigned.get('id') != room['participant_id']:
                        raise BotSeshError('agent_name_not_confirmed')
            verified = self.rpc(provisional, 'list_rooms').get('rooms', [])
            by_id = {r['room_id']: r for r in verified}
            for room in rooms:
                if by_id.get(room['room_id'], {}).get('participant_id') != room['participant_id']:
                    raise BotSeshError('room_identity_changed_retry_connect')
            def enroll(data):
                live = set(by_id)
                for rid, saved in data['rooms'].items():
                    if saved.get('transport') == 'oauth' and saved.get('active') and rid not in live:
                        saved.update(active=False, state='not_in_current_authorization')
                for rid, room in by_id.items():
                    if rid in data.get('forgotten_rooms', {}) and rid not in {r['room_id'] for r in roots}:
                        continue
                    saved = data['rooms'].get(rid, {})
                    same = saved.get('participant_id') == room['participant_id'] and saved.get('auth_version') == AUTH_VERSION
                    if saved and not same:
                        # Keep old pending UUIDs/receipts as recovery evidence;
                        # never replay them as a different participant.
                        data.setdefault('previous_oauth_bindings', {})[rid + ':' + str(uuid.uuid4())] = saved
                    binding = {'room_id': rid, 'url': origin + '/b/' + rid, 'origin': origin,
                               'name': room['name'], 'display_name': room['display_name'],
                               'participant_id': room['participant_id'], 'oauth_id': OAUTH_ID,
                               'auth_version': AUTH_VERSION, 'profile': profile,
                               'active': True, 'state': 'connected', 'transport': 'oauth',
                               'room_type': room.get('room_type', 'room')}
                    if room.get('source_room_id'):
                        binding['oauth_root'] = room['source_room_id']
                    data['rooms'][rid] = {**(saved if same else {}), **binding}
                    data.get('forgotten_rooms', {}).pop(rid, None)
            self.store.update(enroll)
            from .room_lifecycle import remember_access
            for room in verified:
                if room.get('password_required') is False:
                    remember_access(self.store, room['room_id'], origin + '/b/' + room['room_id'])
            return self.store.room(room_hint or roots[0]['room_id'])

    def disconnect(self, room_id):
        binding = self.store.read()['rooms'].get(room_id)
        if not binding or binding.get('transport') != 'oauth':
            raise BotSeshError('room_not_connected')
        if binding.get('auth_version') != AUTH_VERSION:
            raise BotSeshError('legacy_authorization_remove_in_botsesh_people')
        with _lock(self.store):
            if binding.get('state') == 'revoked':
                return
            result = self.rpc(binding, 'hermes_disconnect', {'confirm': True})
            if result.get('revoked') is not True:
                raise BotSeshError('revocation_not_confirmed')
            try:
                self.rpc(binding, 'list_rooms')
            except BotSeshError as error:
                if error.status != 401:
                    raise
            else:
                raise BotSeshError('revocation_not_confirmed')
            def disable(data):
                for room in data['rooms'].values():
                    if room.get('transport') == 'oauth' and room.get('auth_version') == AUTH_VERSION:
                        room.update(active=False, state='revoked')
            self.store.update(disable)

    def discover(self, binding):
        """Only exact server-authorized memberships of the active profile grant."""
        rooms = self.rpc(binding, 'list_rooms').get('rooms', [])
        for room in rooms:
            if (not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', room.get('room_id', ''))
                    or not room.get('participant_id')):
                raise BotSeshError('invalid_room_binding')
        with _lock(self.store):
            def enroll(data):
                live = {room['room_id'] for room in rooms}
                for saved in data['rooms'].values():
                    if saved.get('transport') == 'oauth' and saved.get('auth_version') == AUTH_VERSION and saved['room_id'] not in live:
                        saved.update(active=False, state='membership_revoked')
                for room in rooms:
                    rid = room['room_id']
                    if rid in data.get('forgotten_rooms', {}):
                        continue
                    current = data['rooms'].get(rid)
                    if current:
                        # Discovery cannot re-enable an explicit detach, switch
                        # participant identity, or replay its previous journal.
                        if current.get('participant_id') != room['participant_id']:
                            current.update(active=False, state='identity_changed_run_connect')
                        continue
                    source = room.get('source_room_id')
                    if room.get('room_type') != 'dm' or not source:
                        continue  # Root enrollment always requires explicit connect.
                    root = data['rooms'].get(source, {})
                    if not root.get('active') or root.get('auth_version') != AUTH_VERSION:
                        continue
                    data['rooms'][rid] = {'room_id': rid, 'url': binding['origin']+'/b/'+rid, 'origin': binding['origin'],
                        'name': room['name'], 'display_name': room['display_name'], 'participant_id': room['participant_id'],
                        'oauth_id': OAUTH_ID, 'auth_version': AUTH_VERSION, 'oauth_root': source, 'room_type': 'dm',
                        'active': True, 'state': 'connected', 'transport': 'oauth'}
            self.store.update(enroll)
        return {'rooms': rooms}

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
            other = 'oauth_turns' if section == 'oauth_posts' else 'oauth_posts'
            if client_id in data['rooms'][room_id].get(other, {}):
                raise BotSeshError('pending_send_mismatch')
            if len(rows) >= 1000:
                removable = next((key for key, row in rows.items() if row.get('complete') or row.get('failed')), None)
                if removable is None:
                    raise BotSeshError('operation_journal_full')
                del rows[removable]
            rows[client_id] = {'exact': exact}
            if section == 'oauth_posts' and exact.get('processed') is True and 'reply_to' not in exact:
                # Bind only NEW native sends to their exact active input. Keep
                # authored fields separate; old/uncertain UUIDs must never gain
                # a reply target on replay, even after the active turn changes.
                room = data['rooms'][room_id]
                numbers = room.get('oauth_receipts', {}).get(exact.get('receipt_id'), {}).get('numbers', [])
                targets = [row['target'] for row in room.get('processing_receipts', {}).values()
                           if row.get('receipt_id') == exact.get('receipt_id')
                           and row.get('state') in ('admitting', 'admitted', 'started')
                           and is_number(row.get('target')) and row['target'] in numbers]
                if len(targets) == 1:
                    rows[client_id]['native_reply_to'] = targets[0]
        self.store.update(save)
        return self.store.room(room_id)[section][client_id]

    def _journal_rpc(self, binding, section, client_id, name, args):
        try:
            return self.rpc(binding, name, args)
        except BotSeshError as error:
            if error.definitive:
                self.store.update(lambda d: d['rooms'][binding['room_id']][section][client_id].update(
                    failed={'code': error.code, 'status': error.status}))
            raise

    def finish(self, binding, *, output, receipt_id, client_id, processed=False):
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
        if binding.get('auth_version') != AUTH_VERSION:
            raise BotSeshError('oauth_upgrade_sign_in_required')
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
        if op == 'offer_ownership':
            if args.get('user_requested') is not True:
                raise BotSeshError('explicit_request_required')
            require_client_id(args.get('client_id'))
            return self.rpc(binding, 'offer_ownership', {'room_id': room_id, 'client_id': args['client_id'], 'user_requested': True})
        if op == 'revoke_ownership_offer':
            require_client_id(args.get('offer_id'))
            return self.rpc(binding, 'revoke_ownership_offer', {'room_id': room_id, 'offer_id': args['offer_id']})
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
            return value
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
                if args.get('reply_to') is not None:
                    raise BotSeshError('oauth_structured_reply_target_not_supported')
                result = self.finish(binding, output=intent, receipt_id=receipt_id, client_id=client_id, processed=args.get('processed'))
                return {'client_id': client_id, 'number': result['reply_number'], 'read_effect': result['read_effect']}
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
                saved = self._journal(room_id, 'oauth_posts', client_id, exact)
            reply_to = saved.get('native_reply_to', args.get('reply_to'))
            result = self._journal_rpc(binding, 'oauth_posts', client_id, 'send_message', {'room_id': room_id, 'text': text,
                'client_id': client_id, 'batch_id': receipt_id,
                **({'reply_to': reply_to} if reply_to is not None else {})})
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
