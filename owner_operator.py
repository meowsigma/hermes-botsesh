"""Server-verified owner-DM exception, bound to one admitted native turn.

This authenticates the direct sender, not quoted/relayed prose or model intent.
There is deliberately no tool argument, environment flag, or persisted owner bit.
"""
from __future__ import annotations
from .client import BotSeshError

IDENTITY_KEYS = ('participant_id', 'oauth_id', 'oauth_root', 'origin')


def pin_operator_binding(binding, context):
    if (binding.get('transport') != 'oauth' or binding.get('room_type') != 'dm'
            or not context or not context.get('membership_id') or not context.get('human_id')):
        return None
    return {**{key: binding.get(key) for key in IDENTITY_KEYS},
            'membership_id': context['membership_id'], 'human_id': context['human_id']}


def owner_dm_client(chat_id):
    from gateway.session_context import get_session_env
    from .adapter import _ADAPTERS, _HOOK_LOCK
    target = get_session_env('HERMES_SESSION_MESSAGE_ID', '')
    if (get_session_env('HERMES_SESSION_PLATFORM', '') != 'botsesh'
            or not chat_id or get_session_env('HERMES_SESSION_CHAT_ID', '') != chat_id or not target):
        raise BotSeshError('owner_dm_operator_required')

    def source(adapter, current):
        binding = adapter.client.store.room(chat_id)
        pin = current.get('operator_binding')
        row = binding.get('processing_receipts', {}).get(target, {})
        frozen = row.get('frozen', {})
        messages = [m for m in frozen.get('batch', {}).get('messages', [])
                    if str(m.get('number')) == target]
        if (not binding.get('active') or binding.get('transport') != 'oauth' or binding.get('room_type') != 'dm'
                or not pin or not pin.get('oauth_id') or not pin.get('participant_id')
                or any(binding.get(key) != pin.get(key) for key in IDENTITY_KEYS)
                or str(current.get('target')) != target or current.get('delivered')
                or row.get('state') not in ('admitting', 'admitted', 'started') or row.get('explicit_failure')
                or not current.get('receipt_id') or row.get('receipt_id') != current['receipt_id']
                or frozen.get('receipt_id') != current['receipt_id'] or len(messages) != 1):
            raise BotSeshError('owner_dm_operator_required')
        message = messages[0]
        author = message.get('authorParticipant') or {}
        human = message.get('senderParticipantId')
        if (message.get('isOwn') is not False or human != pin.get('human_id') or not human
                or author.get('id') != human or author.get('verified') is not True
                or adapter._is_sender_authorized(human, 'dm', chat_id) is not True):
            raise BotSeshError('owner_dm_operator_required')
        return dict(pin), current['receipt_id'], human

    with _HOOK_LOCK:
        candidates = [(a, a._current[chat_id]) for a in list(_ADAPTERS)
                      if chat_id in a._current and str(a._current[chat_id].get('target')) == target]
        if len(candidates) != 1:
            raise BotSeshError('owner_dm_operator_required')
        adapter, current = candidates[0]
        before = source(adapter, current)
    # Fresh authenticated server projection on EVERY invocation. No cached DM
    # menu context or old frozen payload is proof of current grant ownership.
    live = adapter.client.enrolled(chat_id, 'dm_context')
    pin, _, human = before
    if (live.get('room_id') != chat_id or live.get('room_type') != 'dm'
            or live.get('participant_id') != pin['participant_id']
            or live.get('membership_id') != pin['membership_id']
            or live.get('human_participant_id') != human
            or live.get('access_state') != 'authorized' or live.get('human_owns_grant') is not True):
        raise BotSeshError('owner_dm_operator_required')
    with _HOOK_LOCK:
        if adapter._current.get(chat_id) is not current or source(adapter, current) != before:
            raise BotSeshError('owner_dm_operator_required')
    return adapter.client
