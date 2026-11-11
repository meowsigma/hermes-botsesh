"""Durable transport receipts. Receiving/starting is never a read attestation."""
from __future__ import annotations
import random
import time

TERMINAL = {'replied', 'failed', 'passed', 'completed_unread'}

def work_id(row, target=None):
    """A source number is an ambient delivery, not every later offer for it."""
    offer = row.get('takeover_offer') or {}
    return 'takeover:' + offer['event_id'] if offer else 'message:' + str(row.get('target', target))

def can_begin_takeover(room, target, event_id):
    key = 'takeover:' + event_id
    if key in room.get('work_receipts', {}):
        return False
    prior = room.get('processing_receipts', {}).get(str(target))
    if not prior:
        return True
    if work_id(prior, target) == key or prior.get('state') not in TERMINAL:
        return False
    # Terminal state alone cannot retire an uncertain operation or native turn.
    return not (prior.get('failure_pending')
        or prior.get('explicit_pass') and not prior['explicit_pass'].get('result')
        or prior.get('explicit_failure') and not prior['explicit_failure'].get('result')
        or prior.get('takeover_offer') and not prior.get('takeover_settled')
        or any(not (call.get('result') or call.get('rejected'))
               for call in prior.get('explicit_post_calls', {}).values())
        or room.get('pending_completion') or room.get('pending_final')
        or str(target) in room.get('failure_outbox', {}))

def begin_takeover(store, room_id, target, admitted):
    """Select a NEW accepted work item; preserve the previous terminal journal.

    processing_receipts remains the active per-source view used by tools and
    recovery. Archived identities dedupe older work without resurrecting it.
    """
    def save(data):
        room = data['rooms'][room_id]
        if not can_begin_takeover(room, target, admitted['event_id']):
            raise RuntimeError('BotSesh work item changed during takeover admission.')
        rows = room.setdefault('processing_receipts', {})
        prior = rows.get(str(target))
        if prior:
            history = room.setdefault('work_receipts', {})
            history[work_id(prior, target)] = prior
            for old in list(history)[:-1000]:
                del history[old]
        rows[str(target)] = {'target': target, 'state': 'received',
            'received_at': time.time(), 'takeover_offer': admitted}
    store.update(save)

def backoff(attempt, minimum=1, retry_after=0):
    """Full jitter with a floor; honor server Retry-After even above our cap."""
    return max(float(retry_after), random.uniform(minimum, min(300, minimum * 2 ** min(attempt, 8))))

def receipt(store, room_id, target, **fields):
    def save(data):
        room = data['rooms'][room_id]
        rows = room.setdefault('processing_receipts', {})
        key = str(target)
        row = rows.setdefault(key, {'target': target, 'received_at': time.time(), 'state': 'received'})
        if row['state'] in TERMINAL and fields.get('state', row['state']) != row['state']:
            raise RuntimeError('A terminal BotSesh receipt cannot be replaced.')
        row.update(fields, updated_at=time.time())
        # Never evict unresolved work to make space. Only terminal receipts age out.
        done = [key for key, value in rows.items() if value['state'] in TERMINAL]
        for old in done[:-1000]:
            del rows[old]
    return store.update(save)['rooms'][room_id]['processing_receipts'][str(target)]

def health(store, room_id, **fields):
    store.update(lambda data: data['rooms'][room_id].setdefault('health', {}).update(fields))

def failure_code(text):
    lower = str(text).lower()
    # Native execution diagnostics also have budgets. They are not evidence
    # that the provider rejected a request or ran out of money.
    if any(phrase in lower for phrase in ('iteration budget', 'turn budget', 'tool budget', 'run time budget', 'run budget', 'max_iterations_reached')):
        return 'runtime_error'
    if any(word in lower for word in ('402', 'budget', 'credit', 'quota', 'billing', 'payment')):
        return 'provider_budget'
    return 'provider_error'

def failure_notice(code, name='Hermes'):
    # Code/reason stays in the typed receipt and status. Only new transport
    # notices use this copy; old durable UUIDs retain their original bytes.
    return str(name) + " couldn't do it."
