"""Transport router: Python OAuth/invitations/guest management; optional SDK.

No OAuth path calls command()/call() or sends its grant to the JS bridge.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from urllib.parse import urlsplit
from .storage import Store

# Node canonicalizes import.meta.url; use the same path when the plugin install is
# a symlink, otherwise bridge.mjs's CLI guard exits silently without running main.
BRIDGE = (Path(__file__).parent / 'bridge.mjs').resolve()
TERMINAL_CODES = {'invalid_grant', 'authorization_revoked', 'membership_revoked', 'access_revoked', 'authorization_expired', 'access_credential_expired', 'refresh_reuse', 'refresh_revoked', 'key_revoked', 'room_expired'}

class BotSeshError(RuntimeError):
    def __init__(self, code, status=None, retry_after_ms=0, pending=None, *, rejected=False, message=None, room_moved_on=None):
        self.code, self.status, self.retry_after_ms = code, status, retry_after_ms
        self.pending, self.rejected = pending, rejected
        self.message = message
        self.room_moved_on = room_moved_on
        super().__init__(f'BotSesh: {code}' + (f' (HTTP {status})' if status else '') + (f'; {pending_notice(pending)}' if pending else ''))

    @property
    def definitive(self):
        # A parsed MCP rejection need not carry an HTTP status. Transport loss,
        # malformed/unconfirmed success and retryable server failures are NOT a
        # verdict on a mutation, so their original durable intent must survive.
        if self.status in (408, 429) or isinstance(self.status, int) and self.status >= 500:
            return False
        return self.rejected or isinstance(self.status, int) and 400 <= self.status < 500

    @property
    def terminal(self):
        return self.status in (401, 410) or self.code in TERMINAL_CODES


def pending_notice(pending):
    number = pending.get('number')
    posted = f'Reply #{number} posted.' if isinstance(number, int) else 'Reply posting unconfirmed.'
    draft = pending.get('draft_ready')
    declaration = f' Draft declaration {draft}.' if draft in ('denied', 'unfinished', 'confirmed') else ''
    return posted + declaration + ' Completion pending; retry the original operation locally after authorization. Later work remains unacknowledged.'


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

    def logical_id(self, kind, payload, *, replace=None):
        """Reuse exact intent; CAS-rotate only a definitively ended default ID."""
        import hashlib
        key = kind + ':' + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        def remember(data):
            ids = data.setdefault('operation_ids', {})
            if key not in ids:
                if len(ids) >= 10000:
                    raise BotSeshError('operation_journal_full_explicit_client_id_required')
                ids[key] = str(uuid.uuid4())
            elif replace is not None and ids[key] == replace:
                # A concurrent renewal wins; never fork it with another UUID.
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
            raise BotSeshError(e.get('code', 'bridge_failed'), e.get('status'), e.get('retry_after_ms', 0), pending=result.get('pending'))
        return result['result']

    def enrolled(self, room_id, op, *, _input_target=None, **args):
        if room_id not in self.store.read()['rooms']:
            raise BotSeshError('not_enrolled', message='Not in this room yet. Use botsesh_join with the invitation link.')
        binding = self.store.room(room_id)
        if _input_target is not None:
            # Adapter recovery has no task-local native session. Carry its
            # ORIGINAL target explicitly, never infer it from a supplied UUID.
            from .receipt_handles import require_bound
            scoped_args = args
            if op == 'retry':
                saved = [row for section in ('oauth_posts', 'oauth_turns')
                         if (row := binding.get(section, {}).get(args.get('client_id')))]
                if len(saved) != 1:
                    raise BotSeshError('original_operation_required', rejected=True)
                scoped_args = saved[0]['exact']
            require_bound(binding, _input_target, scoped_args.get('receipt_id') or scoped_args.get('batch_id'))
            if op == 'post' and not any(args.get('client_id') in binding.get(section, {}) for section in ('oauth_posts', 'oauth_turns')):
                # Freshness belongs to THIS input, not another open native read.
                frozen = binding.get('processing_receipts', {}).get(str(_input_target), {}).get('frozen') or {}
                args.setdefault('seen_through', frozen.get('batch', {}).get('nextCheckpoint', 0))
        try:
            if binding.get('transport') == 'oauth':
                from .oauth import OAuthClient
                result = OAuthClient(self.store).call(binding, op, **args)
            elif binding.get('transport') == 'guest_manager':
                from .guest_manager import GuestManagerClient
                result = GuestManagerClient(self.store).call(binding, op, **args)
            elif binding.get('transport') == 'invitation':
                from .invitation import InvitationClient
                result = InvitationClient(self.store).call(binding, op, **args)
            else:
                result = self.call(op, room=binding['url'], **({'dev_origin': binding['origin']} if binding.get('origin', '').startswith('http:') else {}), **args)
            def progress(data):
                room = data['rooms'][room_id]
                from .room_turn import is_number
                if op in ('post', 'finish', 'retry') and is_number(result.get('number'), 1):
                    # All transports confirm exact authored bytes/seat before
                    # returning a message number. Explicit tools, gateway chunks
                    # and recovered sends share this boundary; quiet/failure
                    # settlement and unconfirmed attempts are not sent replies.
                    room.setdefault('health', {})['last_reply'] = time.time()
                if op in ('post', 'finish') and args.get('processed') is True and result.get('read_effect') == 'processed_frozen_receipt' and 'number' in result:
                    # Tool and gateway sends share a receipt, not necessarily a UUID.
                    # Remember only a confirmed reply to this exact addressed turn.
                    for row in room.get('processing_receipts', {}).values():
                        if _input_target is not None and str(row.get('target')) != str(_input_target):
                            continue
                        if row.get('receipt_id') == args.get('receipt_id'):
                            row['read_attested'] = True
                        if (row.get('receipt_id') == args.get('receipt_id')
                                and args.get('reply_to', row['target']) == row['target']):
                            row['posted_reply'] = result
                if result.get('pending'):
                    room['pending_completion'] = result['pending']
                elif op in ('check', 'pending'):
                    room.pop('pending_completion', None)  # Verified SDK journal, not a discard.
                elif result.get('client_id') == room.get('pending_completion', {}).get('client_id') and result.get('read_effect') == 'processed_frozen_receipt':
                    room.pop('pending_completion', None)
            self.store.update(progress)
            return result
        except BotSeshError as e:
            if e.pending:
                self.store.update(lambda d: d['rooms'][room_id].update(pending_completion=e.pending))
            if binding.get('transport') == 'guest_manager' and e.code == 'agent_manager_revoked':
                # Human acceptance ends management, not ordinary membership.
                self.store.update(lambda d: d['rooms'][room_id].update(manager=False, state=e.code))
            if e.terminal and not (op == 'offer_ownership' and e.code == 'offer_ended'):
                self.store.update(lambda d: d['rooms'][room_id].update(active=False, state=e.code))
            raise

    def join(self, link, *, dev_origin=None):
        from .oauth import checked_origin
        u = urlsplit(link)
        origin = u.scheme + '://' + u.netloc
        if origin != 'https://botsesh.com' and (not dev_origin or origin != checked_origin(dev_origin)):
            raise BotSeshError('origin_denied')
        if u.query or u.username or u.password:
            raise BotSeshError('invalid_invitation_url')
        browser = re.fullmatch(r'/agent-room/([A-Za-z0-9_-]+)/invite/([^/]+)/([^/]+)', u.path)
        fragment = re.fullmatch(r'#agent-invite=([^.]*)\.([^.]*)', '#' + u.fragment)
        room = re.fullmatch(r'/b/([A-Za-z0-9_-]+)', u.path)
        if browser and not u.fragment:
            room_id, invitation_id, token = browser.groups()
        elif room and fragment:
            room_id = room[1]
            invitation_id, token = fragment.groups()
        else:
            raise BotSeshError('invalid_invitation_url')
        from .room_turn import require_client_id
        require_client_id(invitation_id)
        if not re.fullmatch(r'bsi_[A-Za-z0-9_-]{43}', token):
            raise BotSeshError('invalid_invitation_url')
        url = origin + '/b/' + room_id
        previous = self.store.read()['rooms'].get(room_id)
        if previous:
            # Existing/detached/revoked seats never silently become a replacement.
            if previous.get('url') != url or previous.get('origin', origin) != origin:
                raise BotSeshError('identity_mismatch')
            self.enrolled(room_id, 'verify')
            return {'state': 'joined', 'room_id': room_id, 'participant_id': previous['participant_id'], 'url': previous['url']}
        from .invitation import InvitationClient
        return InvitationClient(self.store).join(origin, room_id, invitation_id, token)

    def create(self, name='BotSesh room', purpose=None, client_id=None, *, lifecycle_scopes=None):
        from .guest_manager import GuestManagerClient
        if lifecycle_scopes is not None:
            return GuestManagerClient(self.store).create(name, purpose, client_id, lifecycle_scopes=lifecycle_scopes)
        return GuestManagerClient(self.store).create(name, purpose, client_id)
