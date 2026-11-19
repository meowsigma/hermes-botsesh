"""Server-verified owner-DM exception, bound to one admitted native turn.

This authenticates the direct sender, not quoted/relayed prose or model intent.
There is deliberately no tool argument, environment flag, or persisted owner bit.
"""
from __future__ import annotations
from .client import BotSeshError

IDENTITY_KEYS = ('participant_id', 'oauth_id', 'oauth_root', 'origin')


def owner_turn_context(client, binding, context, message):
    """Display-only projection of the server lookup made for THIS admission.

    The line conveys no authority to a tool: owner_dm_client still revalidates.
    Display names are quoted data and never used for an identity comparison.
    """
    import json
    import time
    live = (context or {}).get('operator_projection') or {}
    author = message.get('authorParticipant') or {}
    human = message.get('senderParticipantId')
    if (binding.get('active') is not True or binding.get('transport') != 'oauth'
            or binding.get('room_type') != 'dm' or not binding.get('oauth_id')
            or (context or {}).get('until', 0) <= time.monotonic()
            or live.get('room_id') != binding.get('room_id') or live.get('room_type') != 'dm'
            or not live.get('membership_id') or live.get('membership_id') != context.get('membership_id')
            or live.get('participant_id') != binding.get('participant_id')
            or live.get('human_owns_grant') is not True or live.get('access_state') != 'authorized'
            or not human or human != live.get('human_participant_id') or human != context.get('human_id')
            or author.get('id') != human or author.get('verified') is not True or message.get('isOwn') is not False):
        return ''
    name = author.get('name') or 'your owner'
    text = ('You are in a private DM with your owner ' + json.dumps(name, ensure_ascii=False)
            + '. They can ask you to create rooms or to make them the owner of a room you manage.')
    from .managed_rooms import source_dm, recent_room
    recent = recent_room(client.store.read()['rooms'].values(), source_dm(client, binding['room_id']))
    if recent:
        text += '\nMost recent room created from this owner DM: ' + json.dumps(
            {'name': recent.get('name'), 'room_id': recent['room_id']}, ensure_ascii=False)
    return text


def pin_operator_binding(binding, context):
    if (binding.get('transport') != 'oauth' or binding.get('room_type') != 'dm'
            or not context or not context.get('membership_id') or not context.get('human_id')):
        return None
    return {**{key: binding.get(key) for key in IDENTITY_KEYS},
            'membership_id': context['membership_id'], 'human_id': context['human_id']}


def owner_dm_client(chat_id):
    from .adapter import _HOOK_LOCK, bind_turn
    # 0.10.4 r3: the native input of this running turn is named only through `bind_turn` (the gateway's own room and input).
    first = bind_turn(room_id=chat_id, ambient='required', need='settle') if chat_id else None
    if not first:
        raise BotSeshError('owner_dm_operator_required')
    target = str(first.current['target'])

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
        bound = bind_turn(room_id=chat_id, target=target, ambient='required', need='settle')
        if not bound:
            raise BotSeshError('owner_dm_operator_required')
        adapter, _room, current = bound
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
        if (bind_turn(adapter=adapter, room_id=chat_id, target=target, ambient='required', need='settle').current is not current
                or source(adapter, current) != before):
            raise BotSeshError('owner_dm_operator_required')
    return adapter.client
