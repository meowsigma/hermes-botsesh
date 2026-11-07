"""Durable transport receipts. Receiving/starting is never a read attestation."""
from __future__ import annotations
import random
import time
import re

# Exact, exclusive agent-authored give-up intent. Runtime failures use static
# failure_notice instead; never pass provider exception prose to this object.
def validate_failure(value):
    from .client import BotSeshError
    uuid4 = r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}'
    if (not isinstance(value, dict) or set(value) - {'task_id', 'status', 'reason', 'partial_notes'}
        or not isinstance(value.get('task_id'), str) or not re.fullmatch(uuid4, value['task_id'])
        or value.get('status') not in ('out_of_usage', 'cant_do', 'error')
        or not isinstance(value.get('reason'), str) or not value['reason'].strip()
        or len(value['reason'].encode('utf-16-le')) // 2 > 240
        or re.search(r'[\x00-\x1f\x7f\u2028\u2029]', value['reason'])
        or 'partial_notes' in value and (not isinstance(value['partial_notes'], str) or len(value['partial_notes'].encode('utf-16-le')) // 2 > 4000)):
        raise BotSeshError('invalid_task_failure')
    return value

TERMINAL = {'replied', 'failed'}

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
    if any(word in lower for word in ('402', 'budget', 'credit', 'quota', 'billing', 'payment')):
        return 'provider_budget'
    return 'provider_error'

def failure_notice(code):
    why = {'provider_budget': 'provider budget exceeded', 'provider_error': 'model/provider error',
           'interrupted': 'gateway restarted mid-turn; tool execution may be incomplete. Check before retrying',
           'cancelled': 'turn cancelled', 'empty_response': 'no answer was returned',
           'admission_denied': 'gateway did not admit this message', 'context_limit': 'room context is too large',
           'runtime_error': 'gateway processing error'}.get(code, 'gateway processing error')
    return "Hermes couldn't answer: " + why + '.'
