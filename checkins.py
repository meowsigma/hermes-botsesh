"""Typed, live server-consented service input; never room-authored authority."""
from __future__ import annotations


def service_actor(message, policy, binding):
    """Return the approving person's stable ID for gateway authorization ONLY.

    The actual source remains BotSesh/system (no sender participant). A live
    exact-turn server policy must match the canonical typed occurrence. This
    does not turn service input into a human message or enable host tools.
    """
    if not isinstance(message, dict) or not isinstance(policy, dict):
        return None
    source = message.get('checkin')
    live = policy.get('service_input')
    if (not isinstance(source, dict) or not isinstance(live, dict)
            or source != live or source.get('type') != 'botsesh_checkin'
            or source.get('active') is not True
            or source.get('roomId') != binding.get('room_id')
            or source.get('participantId') != binding.get('participant_id')
            or not source.get('occurrenceId') or not source.get('approvedBy')
            or message.get('senderParticipantId') is not None
            or message.get('authorParticipant') is not None
            or message.get('editCount', 0) != 0
            or any(policy.get(k) is not False for k in ('toolsAllowed', 'author_is_agent_owner',
                'batch_all_authors_are_agent_owner', 'guest_tools_default', 'tools_ceiling_allowed'))
            or policy.get('requester') != {'id': source['approvedBy'], 'isOwner': False}):
        return None
    return source['approvedBy']


def propose(args, ctx):
    import time

    from .client import Client, BotSeshError
    from .tools import scoped_room
    client = Client()
    room = scoped_room(args, ctx, infer=True)
    binding = client.store.room(room)
    if binding.get('transport') != 'oauth':
        raise BotSeshError('native_checkin_connection_required', message='Use an existing native Hermes OAuth connection. No local cron or check-in was created.')
    for key, low, high in (('interval_minutes', 15, 1440), ('start_in_minutes', 2, 60), ('duration_minutes', 2, 10080)):
        v = args.get(key)
        if type(v) is not int or not low <= v <= high:
            raise BotSeshError('invalid_checkin_bounds')
    purpose = args.get('purpose')
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 1000:
        raise BotSeshError('invalid_checkin_purpose')
    intent = {k: args[k] for k in ('purpose', 'interval_minutes', 'start_in_minutes', 'duration_minutes')}
    logical_intent = {'room_id': room, **intent}
    ident = args.get('client_id') or client.logical_id('checkin-proposal', logical_intent)
    from .room_turn import require_client_id
    require_client_id(ident)
    def save(data):
        rows = data['rooms'][room].setdefault('checkin_proposals', {})
        if ident not in rows:
            if len(rows) >= 100:
                raise BotSeshError('checkin_proposal_journal_full')
            first = int(time.time() * 1000) + intent['start_in_minutes'] * 60000
            rows[ident] = {'intent': intent, 'payload': {'client_id': ident, 'purpose': purpose,
                'interval_minutes': intent['interval_minutes'], 'first_at': first,
                'ends_at': first + intent['duration_minutes'] * 60000}}
        if rows[ident]['intent'] != intent:
            raise BotSeshError('checkin_proposal_id_conflict')
    for attempt in range(2):
        row = client.store.update(save)['rooms'][room]['checkin_proposals'][ident]
        result = client.enrolled(room, 'propose_check_in', **row['payload'])
        if result.get('state') not in ('declined', 'expired', 'cancelled'):
            return result
        # Only a definitive server terminal state rotates a default ID. Network
        # uncertainty and explicit IDs keep their original exact terms.
        if args.get('client_id') or attempt:
            raise BotSeshError('checkin_proposal_ended_new_id_required')
        ident = client.logical_id('checkin-proposal', logical_intent, replace=ident)
