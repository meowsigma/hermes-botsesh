"""Local management discovery, never a room-reading or ownership grant."""
from __future__ import annotations
import time
from .client import BotSeshError

RECENT = {'the room you just made', 'the room you just created', 'the recent room', 'recent', 'latest'}


def managed(binding):
    return (binding.get('active') is True and binding.get('manager') is True
            and binding.get('management_only') is True
            and binding.get('transport') == 'guest_manager')


def source_dm(client, chat):
    binding = client.store.room(chat)
    return {key: binding.get(key) for key in ('room_id', 'participant_id', 'oauth_id', 'oauth_root', 'origin')}


def remember_creation(client, result, chat):
    """Provenance only. Authorization always rechecks the current server turn."""
    source = source_dm(client, chat)
    def save(data):
        room = data['rooms'].get(result.get('room_id'))
        if room and managed(room) and 'created_from_owner_dm' not in room:
            room['created_from_owner_dm'] = source
            room['owner_dm_created_order'] = time.time_ns()
    client.store.update(save)


def created_here(binding, source):
    return bool(source and binding.get('created_from_owner_dm') == source)


def recent_room(bindings, source):
    candidates = [r for r in bindings if managed(r) and created_here(r, source)]
    if not candidates:
        return None
    latest = max(r.get('owner_dm_created_order', 0) for r in candidates)
    winners = [r for r in candidates if r.get('owner_dm_created_order', 0) == latest]
    return winners[0] if len(winners) == 1 else None


def resolve_room(client, args, chat=''):
    # Exact IDs remain literal. Names/aliases must resolve within active management
    # bindings; model-supplied create-result objects never supply authority.
    if args.get('room_id'):
        if args.get('room') and args['room'] != args['room_id']:
            raise BotSeshError('conflicting_room_reference')
        return args['room_id']
    query = args.get('room', '')
    if not isinstance(query, str):
        raise BotSeshError('invalid_room_reference')
    bindings = [r for r in client.store.read()['rooms'].values() if managed(r)]
    if not query or query.casefold().strip() in RECENT:
        room = recent_room(bindings, source_dm(client, chat)) if chat else None
        if room:
            return room['room_id']
        raise BotSeshError('room_ambiguous_ask_which_room')
    matches = [r for r in bindings if r['room_id'] == query or r.get('name', '').casefold() == query.casefold()]
    if len(matches) != 1:
        raise BotSeshError('room_ambiguous_ask_which_room' if matches else 'managed_room_not_found')
    return matches[0]['room_id']
