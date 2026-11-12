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
    if target is not None:
        scope = rows.get(str(target), {})
        if scope.get('state') not in OPEN:
            return []
    else:
        scope = room
    eligible = {}
    for key, row in rows.items():
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
        native_ids = {r.get('receipt_id') for r in rows.values()}
        for rid, receipt in room.get('oauth_receipts', {}).items():
            if (rid not in native_ids and receipt.get('participant_id') == room['participant_id']
                    and receipt.get('expires', 0) > time.time()):
                eligible[rid] = {'messages': receipt.get('numbers', []), 'input_message': None}
    mapping = scope.setdefault('model_receipt_handles', {})
    current = scope.get('receipt_id')
    ordered = sorted(eligible, key=lambda rid: (rid != current, rid))
    for rid in ordered:
        if rid not in mapping.values():
            mapping['r' + str(len(mapping) + 1)] = rid
    return [{'receipt_id': handle, 'uuid': rid, **eligible[rid]}
            for handle, rid in mapping.items() if rid in eligible]


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
