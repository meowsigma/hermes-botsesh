"""Durable transport receipts. Receiving/starting is never a read attestation."""
from __future__ import annotations
import random
import time

TERMINAL = {'replied', 'failed', 'passed', 'completed_unread'}

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
