"""Keyless invitation membership in Python, not native OAuth consent.

Reuse the existing finite MCP batch/send journal for read truth and durable
replies. Enrollment, verification, passive pickup/wait and no-read posts use
the same room REST endpoints as the optional SDK. No shell or Node fallback.
"""
from __future__ import annotations
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode

from .client import BotSeshError
from .storage import Store
from .oauth import OAuthClient, checked_origin
from .room_turn import is_number, require_client_id

TOKEN = r'bsa_[A-Za-z0-9_-]{43}'


def _http(origin, room_id, method, suffix, *, payload=None, bearer=None):
    import httpx
    from .http_transport import MAX_BYTES, retry_after_ms
    checked_origin(origin)
    if not isinstance(room_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', room_id):
        raise BotSeshError('identity_mismatch')
    allowed = ((method == 'POST' and suffix == '/agent-invites/redeem' and bearer is None)
        or (bearer and (method == 'GET' and (
            suffix == '/agent-access' or re.fullmatch(r'/updates(?:\?after=[0-9]+)?', suffix)
            or re.fullmatch(r'/messages/(?:by-number/[0-9]+|by-client/[0-9a-fA-F-]{36})', suffix))
            or method == 'PUT' and re.fullmatch(r'/messages/by-number/[0-9]+/agreement', suffix)
            or method == 'POST' and (suffix in ('/messages', '/agents/me/wait_for_messages', '/ack', '/wake/quiet-turn')
                or re.fullmatch(r'/wake/tasks/[0-9a-fA-F-]{36}/failure', suffix)))))
    if not allowed or bearer is not None and not re.fullmatch(TOKEN, bearer):
        raise BotSeshError('endpoint_denied')
    headers = {'Accept': 'application/json', 'Origin': origin, 'X-Board-Request': '1',
               'User-Agent': 'BotSesh-Agent/1 (+https://botsesh.com/agents)'}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    try:
        with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
            with client.stream(method, origin + '/api/rooms/' + room_id + suffix,
                               headers=headers, json=payload) as response:
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
        raise BotSeshError(code if isinstance(code, str) and re.fullmatch(r'[a-z0-9_-]{1,80}', code)
                          else 'request_denied', status, retry)
    return value


class InvitationClient(OAuthClient):
    """Shared frozen-batch executor, with a separate exact-membership credential.

    The oauth_* registry keys are historical journal names, not OAuth authority.
    No OAuth credential is read, minted, refreshed or sent to invitation routes.
    """
    def __init__(self, store):
        super().__init__(store)
        self.private = Store(store.credentials / 'python-invitation')

    def join(self, origin, room_id, invitation_id, token):
        # Never fork an existing SDK seat or its uncertain claim on upgrade.
        legacy = self.store.credentials / ('botsesh-agent-' + hashlib.sha256(
            (origin + '\0' + room_id).encode()).hexdigest() + '.json')
        if legacy.exists() or legacy.is_symlink():
            raise BotSeshError('legacy_invitation_requires_original_advanced_store')
        def prepare(data):
            row = data['rooms'].get(room_id)
            if row:
                if row.get('origin') != origin:
                    raise BotSeshError('identity_mismatch')
                if row.get('access_token'):
                    return  # Lost verify/registry output recovers this exact seat.
                if row.get('request', {}).get('invitationId') != invitation_id or row['request']['invitationToken'] != token:
                    raise BotSeshError('pending_invitation_mismatch')
                return
            data['rooms'][room_id] = {'origin': origin, 'room_id': room_id, 'request': {
                'invitationId': invitation_id, 'invitationToken': token,
                'claimNonce': secrets.token_urlsafe(32), 'recoverySecret': 'bsr_' + secrets.token_urlsafe(32),
                'transport': 'api'}}
        saved = self.private.update(prepare)['rooms'][room_id]
        if not saved.get('access_token'):
            value = _http(origin, room_id, 'POST', '/agent-invites/redeem', payload=saved['request'])
            member, access = value.get('membership'), value.get('access')
            if (value.get('room', {}).get('id') != room_id or not isinstance(member, dict)
                    or not isinstance(access, dict) or not isinstance(member.get('id'), str) or not member['id']
                    or not isinstance(value.get('participantId'), str) or not value['participantId']
                    or member.get('participantId') != value['participantId']
                    or not isinstance(access.get('token'), str) or not re.fullmatch(TOKEN, access['token'])
                    or not is_number(member.get('expiresAt')) or not is_number(access.get('expiresAt'))):
                raise BotSeshError('invalid_enrollment_response')
            request = saved['request']
            saved = {'origin': origin, 'room_id': room_id, 'membership_id': member['id'],
                     'participant_id': value['participantId'], 'access_token': access['token'],
                     'recovery_secret': request['recoverySecret'], 'display_name': member.get('displayName', 'BotSesh')}
            def accept(data):
                old = data['rooms'][room_id]
                if old.get('access_token') and old != saved:
                    raise BotSeshError('identity_mismatch')
                data['rooms'][room_id] = saved
            self.private.update(accept)  # Before verify or user-visible output.
        binding = {'transport': 'invitation', 'origin': origin, 'room_id': room_id,
                   'url': origin + '/b/' + room_id, 'participant_id': saved['participant_id'],
                   'membership_id': saved['membership_id'], 'name': saved['display_name'],
                   'active': True, 'state': 'joined'}
        self.verify(binding)
        def adopt(data):
            previous = data['rooms'].get(room_id)
            if previous:
                if any(previous.get(k) != binding[k] for k in ('transport', 'origin', 'participant_id', 'membership_id')) or not previous.get('active'):
                    raise BotSeshError('identity_mismatch')
                return
            data['rooms'][room_id] = binding
        self.store.update(adopt)
        return {'state': 'joined', 'room_id': room_id, 'participant_id': binding['participant_id'], 'url': binding['url']}

    def _access(self, binding, **kwargs):
        checked_origin(binding.get('origin', ''))
        saved = self.private.read()['rooms'].get(binding.get('room_id'))
        if (binding.get('transport') != 'invitation' or not saved
                or binding.get('url') != binding['origin'] + '/b/' + binding['room_id']
                or any(saved.get(k) != binding.get(k) for k in ('origin', 'room_id', 'participant_id', 'membership_id'))
                or not isinstance(saved.get('access_token'), str) or not re.fullmatch(TOKEN, saved['access_token'])):
            raise BotSeshError('identity_mismatch')
        return saved['access_token']

    def rest(self, binding, method, suffix, payload=None):
        return _http(binding['origin'], binding['room_id'], method, suffix,
                     payload=payload, bearer=self._access(binding))

    def verify(self, binding):
        value = self.rest(binding, 'GET', '/agent-access')
        member = value.get('membership')
        if (not isinstance(member, dict) or member.get('id') != binding['membership_id']
                or member.get('participantId') != binding['participant_id']
                or value.get('participantId') != binding['participant_id']
                or not all(is_number(n) for n in (member.get('expiresAt'), value.get('accessExpiresAt'), value.get('readCheckpoint')))):
            raise BotSeshError('identity_mismatch', 401)
        return value

    def updates(self, binding, after=None):
        if after is None:
            after = self.verify(binding)['readCheckpoint']
        messages, size = [], 0
        for _ in range(100):
            value = self.rest(binding, 'GET', '/updates?' + urlencode({'after': after}))
            rows = value.get('messages')
            if not isinstance(rows, list) or type(value.get('hasMore')) is not bool or not is_number(value.get('nextCheckpoint')):
                raise BotSeshError('invalid_updates_page')
            for row in rows:
                if not isinstance(row, dict) or not is_number(row.get('id')) or not is_number(row.get('number')) or row['id'] <= after:
                    raise BotSeshError('invalid_updates_page')
                after = row['id']
            if value['nextCheckpoint'] != after or value['hasMore'] and not rows:
                raise BotSeshError('invalid_updates_page')
            messages.extend(rows)
            size += len(json.dumps(rows, ensure_ascii=False).encode())
            if size > 160000:
                raise BotSeshError('context_limit')
            if not value['hasMore']:
                return messages
        raise BotSeshError('context_limit')

    def rpc(self, binding, name, args=None, *, token=None):
        # Deliberately no hermes_* native/background/owner capabilities: an
        # invitation is not explicit native OAuth or background-spend consent.
        if (token is not None and token != self._access(binding)
                or name not in ('catchup', 'read_tool_page', 'send_message', 'finish_room_turn', 'working', 'hermes_authored')):
            raise BotSeshError('invitation_operation_denied')
        args = dict(args or {})
        if args.get('room_id') != binding['room_id']:
            raise BotSeshError('identity_mismatch')
        if name == 'finish_room_turn':
            # A scoped API bearer has room-chat consent, not the broader OAuth
            # finish capability. Use the exact existing SDK REST operations for
            # silence/failure, never mint/widen a grant to make MCP accept it.
            frozen = self.store.room(binding['room_id']).get('invitation_receipts', {}).get(args.get('batch_id'))
            if not frozen or frozen['expires'] <= time.time():
                raise BotSeshError('receipt_expired', rejected=True)
            self.verify(binding)
            output = args['output']
            if output == {'nothing_to_add': True}:
                self.rest(binding, 'POST', '/ack', {'through': frozen['through'], 'contextAck': frozen['context_ack']})
                verified = self.verify(binding)
                if verified['readCheckpoint'] < frozen['through']:
                    raise BotSeshError('acknowledgment_unconfirmed')
                for number in frozen['others']:
                    self.rest(binding, 'POST', '/wake/quiet-turn', {'throughNumber': number})
                return {'reply_number': None, 'read_checkpoint': verified['readCheckpoint']}
            failure = output.get('failure')
            if isinstance(failure, dict) and set(output) == {'failure'}:
                if failure.get('task_id') not in frozen['tasks']:
                    raise BotSeshError('receipt_scope', rejected=True)
                value = self.rest(binding, 'POST', '/wake/tasks/' + failure['task_id'] + '/failure', {
                    'clientId': require_client_id(args['client_id']), 'status': failure['status'], 'reason': failure['reason'],
                    **({'partialNotes': failure['partial_notes']} if 'partial_notes' in failure else {})})
                return {'failure': value, 'read_effect': 'none'}
            raise BotSeshError('invitation_structured_completion_requires_advanced_transport', rejected=True)
        if name == 'hermes_authored':
            # REST threading itself attests a sparse target. Match the Advanced
            # bridge's fail-closed no-read contract instead of claiming otherwise.
            if args.get('reply_to') is not None:
                raise BotSeshError('unattested_reply_target_not_supported', rejected=True)
            self.verify(binding)
            self.rest(binding, 'POST', '/messages', {'clientId': args['client_id'], 'text': args['text'], 'readThrough': 0})
            message = self.rest(binding, 'GET', '/messages/by-client/' + require_client_id(args['client_id'])).get('message')
            if (not isinstance(message, dict) or message.get('text') != args['text']
                    or message.get('senderParticipantId') != binding['participant_id'] or message.get('replyPreview')):
                raise BotSeshError('send_not_confirmed_retry_same_intent')
            return {'message': {**message, 'text_sha256': hashlib.sha256(message['text'].encode()).hexdigest()}}
        if name == 'catchup':
            target = args.pop('target_number', None)
            args.pop('turn_mode', None)  # Native targeted recovery requires OAuth.
            value = super().rpc(binding, 'catch_up', args, token=self._access(binding))
            if value.get('has_more') or value.get('truncated') or value.get('ack_eligible') is not True:
                raise BotSeshError('context_limit')
            # Public MCP intentionally omits native sender-admission fields. Get
            # them from the canonical room API, never names/kinds/tool labels.
            canonical = {m['number']: m for m in self.updates(binding, value['read_checkpoint'])}
            enriched = []
            for row in value['messages']:
                source = canonical.get(row.get('number'))
                if (not source or any(source.get(k) != row.get(k) for k in ('id', 'number', 'text', 'editCount'))
                        or source.get('authorParticipant', {}).get('id') != (row.get('authorParticipant') or {}).get('id')):
                    raise BotSeshError('context_changed_retry_read')
                enriched.append(source)
            if target is not None and not any(m['number'] == target for m in enriched):
                raise BotSeshError('context_limit')
            return {**value, 'messages': enriched}
        return super().rpc(binding, 'reply' if name == 'send_message' else name, args, token=self._access(binding))

    def call(self, binding, op, **args):
        if op == 'agree':
            self.verify(binding)
            return self.rest(binding, 'PUT', '/messages/by-number/' + str(args['number']) + '/agreement', {k: args[k] for k in ('active', 'version')})
        if op in ('verify', 'pending'):
            return {**self.verify(binding), 'pending': self.pending(binding['room_id'])}
        if op == 'check':
            pending = self.pending(binding['room_id'])
            if pending:
                self.verify(binding)
                return {'pending': pending, 'hasMore': False, 'mentions': [], 'replies': []}
            rows = self.updates(binding)
            # Broadcast is only candidate pickup. The existing native stable-ID
            # sender allowlist, toolsets and operator approvals still authorize
            # admission; a successful invitation never enables this channel.
            return {'pending': None, 'hasMore': False, 'backlog': sum(not m.get('isOwn') for m in rows),
                    'inputs': [m['number'] for m in rows if not m.get('isOwn')],
                    'mentions': [m['number'] for m in rows if not m.get('isOwn') and (m.get('isMentionToMe') or m.get('isDirectMentionToMe'))],
                    'replies': [m['number'] for m in rows if not m.get('isOwn') and m.get('isReplyToMe')]}
        if op == 'wait':
            after, since = args.get('after', -1), args.get('since')
            if not is_number(after, -1) or since is not None and (not isinstance(since, str) or not 1 <= len(since) <= 256):
                raise BotSeshError('invalid_event_cursor')
            value = self.rest(binding, 'POST', '/agents/me/wait_for_messages', {
                'after': after, 'timeout_seconds': 25, **({'since': since} if since else {})})
            if not is_number(value.get('next_after'), -1) or not isinstance(value.get('since'), str) or not 1 <= len(value['since']) <= 256:
                raise BotSeshError('invalid_event_cursor')
            return value
        if op not in ('read', 'post', 'finish', 'retry', 'working', 'fail_task'):
            raise BotSeshError('invitation_operation_denied')
        self.verify(binding)
        result = super().call(binding, op, **args)
        if op == 'read':
            packet = result['briefing'][0]
            messages = result['batch']['messages']
            context = packet.get('context') or {}
            manifest = {'messageVersions': [{'number': m['number'], 'version': m.get('editCount', 0)} for m in messages], 'fileIds': []}
            if context.get('enabled'):
                manifest.update(briefRevision=context['revision'], decisionIds=[d['id'] for d in context.get('decisions', [])])
            def retain(data):
                room = data['rooms'][binding['room_id']]
                receipts = room.setdefault('invitation_receipts', {})
                for old in list(receipts):
                    if old not in room['oauth_receipts']:
                        del receipts[old]
                receipts[result['receipt_id']] = {'through': packet['next_checkpoint'], 'context_ack': manifest,
                    'others': [m['number'] for m in messages if not m['isOwn']],
                    'tasks': [m['addressing']['taskId'] for m in messages if (m.get('addressing') or {}).get('taskId')],
                    'expires': room['oauth_receipts'][result['receipt_id']]['expires']}
            self.store.update(retain)
        return result
