"""Model-only receipt aliases. Transport, frozen packets and journals keep IDs.

Aliases are stable within an exact native input (or local room tool scope),
never recycled in that scope, and usable only while the underlying receipt is
complete, live and belongs to this participant. No approximate matching.
"""
import copy
import time

OPEN = ('admitting', 'admitted', 'started')


def choices(room, target=None):
    if not room.get('active') or not room.get('participant_id'):
        return []
    rows = room.get('processing_receipts', {})
    native_rows = []
    if target is not None:
        scope = rows.get(str(target), {})
        if (scope.get('state') not in OPEN or str(scope.get('target')) != str(target)
                or sum(str(row.get('target')) == str(target) for row in rows.values()) != 1):
            return []
        # Only this immutable input was handed to this native turn. Other
        # queued/open inputs are NOT read candidates, even for this participant.
        native_rows = [(str(target), scope)]
    else:
        scope = room
    eligible = {}
    for key, row in native_rows:
        frozen = row.get('frozen') or {}
        batch = frozen.get('batch') or {}
        rid = row.get('receipt_id')
        receipt = room.get('oauth_receipts', {}).get(rid, {})
        messages = batch.get('messages', [])
        if (row.get('state') in OPEN and not row.get('explicit_pass') and not row.get('explicit_failure')
                and rid and frozen.get('receipt_id') == rid
                and receipt.get('participant_id') == room['participant_id']
                and receipt.get('expires', 0) > time.time()
                and batch.get('hasMore') is False
                and receipt.get('numbers') == [m.get('number') for m in messages]
                and any(str(m.get('number')) == str(row.get('target')) and not m.get('isOwn') for m in messages)):
            eligible[rid] = {'messages': receipt['numbers'], 'input_message': row.get('target')}
    if target is None:
        # Local join/read has no native processing row. The transport saves only
        # complete catchups; its receipt metadata is still the authority.
        native_ids = {rid for r in rows.values()
                      for rid in (r.get('receipt_id'), (r.get('frozen') or {}).get('receipt_id')) if rid}
        for rid, receipt in room.get('oauth_receipts', {}).items():
            if (rid not in native_ids and receipt.get('participant_id') == room['participant_id']
                    and receipt.get('expires', 0) > time.time()):
                eligible[rid] = {'messages': receipt.get('numbers', []), 'input_message': None}
    mapping = scope.setdefault('model_receipt_handles', {})
    # Migrate existing maps before pruning so expired labels are never reused.
    counter = max([scope.get('model_receipt_handle_counter', 0)] + [
        int(handle[1:]) for handle in mapping if handle.startswith('r') and handle[1:].isdecimal()])
    if target is None:
        for handle in list(mapping):
            if mapping[handle] not in eligible:
                del mapping[handle]
    current = scope.get('receipt_id')
    ordered = sorted(eligible, key=lambda rid: (rid != current, rid))
    for rid in ordered:
        if rid not in mapping.values():
            counter += 1
            mapping['r' + str(counter)] = rid
    scope['model_receipt_handle_counter'] = counter
    return [{'receipt_id': handle, 'uuid': rid, **eligible[rid]}
            for handle, rid in mapping.items() if rid in eligible]


def available(room, target, supplied):
    """Literal UUID compatibility has the SAME input boundary as a handle."""
    if not room.get('active'):
        return False
    if any(supplied in (c['receipt_id'], c['uuid']) for c in choices(room, target)):
        return True
    # The retained Advanced bridge owns receipt membership/expiry metadata in
    # its private SDK store, not oauth_receipts. Preserve its literal-UUID path,
    # but NEVER borrow another native input (or treat an unbound rN as a UUID).
    if room.get('transport') in ('oauth', 'invitation', 'guest_manager'):
        return False
    import uuid
    try:
        if str(uuid.UUID(supplied)) != supplied:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    rows = room.get('processing_receipts', {})
    if target is None:
        return all(supplied not in (row.get('receipt_id'), (row.get('frozen') or {}).get('receipt_id'))
                   for row in rows.values())
    row = rows.get(str(target), {})
    frozen = row.get('frozen') or {}
    batch = frozen.get('batch') or {}
    return (room.get('active') and row.get('state') in OPEN
            and str(row.get('target')) == str(target)
            and sum(str(r.get('target')) == str(target) for r in rows.values()) == 1
            and not row.get('explicit_pass') and not row.get('explicit_failure')
            and row.get('receipt_id') == frozen.get('receipt_id') == supplied
            and batch.get('hasMore') is False
            and any(str(m.get('number')) == str(target) and not m.get('isOwn')
                    for m in batch.get('messages', [])))


def resolve(room, target, supplied):
    return next((c['uuid'] for c in choices(room, target) if c['receipt_id'] == supplied), supplied)


def corrective(room, target=None):
    candidates = choices(room, target)[:3]
    listing = '; '.join(c['receipt_id'] + ': ' + ', '.join('message #' + str(n) for n in c['messages'])
                        for c in candidates)
    return ('Unknown or unavailable receipt. Do not guess or repair an ID. '
            + ('Valid open handles: ' + listing + '. ' if candidates else 'No valid open handles; call botsesh_read. ')
            + 'Use a listed handle only after consuming its complete briefing, and set processed:true yourself. '
            'Use a new post ID for a corrected rejected call; keep uncertain original send IDs and fields unchanged.')


def project(client, room_id, result, target=None):
    """Change protocol labels only, never message/file/context bytes."""
    found = []
    def bind(data):
        found.extend(choices(data['rooms'][room_id], target))
    client.store.update(bind)
    labels = {c['uuid']: c['receipt_id'] for c in found}
    value = copy.deepcopy(result)
    rid = value.get('receipt_id')
    if rid in labels:
        value['receipt_id'] = labels[rid]
        for briefing in value.get('briefing', []):
            if isinstance(briefing, dict) and briefing.get('batch_id') == rid:
                briefing['batch_id'] = labels[rid]
    return value
