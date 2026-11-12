"""Explicit no-reply processing of an exact frozen batch, never fetch-as-read."""
import uuid
from .client import BotSeshError


def pass_frozen(client, room_id, receipt_id, processed, *, target=None, client_id=None):
    if processed is not True or not receipt_id:
        raise BotSeshError('processed_receipt_required')
    row = client.store.room(room_id).get('processing_receipts', {}).get(str(target), {}) if target is not None else {}
    if target is not None:
        if row.get('receipt_id') != receipt_id or not row.get('frozen'):
            raise BotSeshError('receipt_scope')
        if row.get('takeover_offer') and not row.get('takeover_claim', {}).get('result'):
            raise BotSeshError('takeover_claim_required')
        if row.get('explicit_failure'):
            raise BotSeshError('task_already_settled')
        if row.get('explicit_post_calls'):
            # A deliberate reply attempt is not "nothing to add", even when
            # transport failed. Native settlement recovers its exact UUID or
            # reports delivery failure; never replace it with a quiet receipt.
            raise BotSeshError('reply_attempted_cannot_pass', message='A reply was attempted. Retry its exact saved operation or end the turn; delivery status will report the outcome, not nothing to add.')
    saved = row.get('explicit_pass')
    payload = {'output': {'nothing_to_add': True}, 'receipt_id': receipt_id,
               'processed': True, 'client_id': client_id or (saved or {}).get('payload', {}).get('client_id') or str(uuid.uuid4())}
    if saved and saved['payload'] != payload:
        raise BotSeshError('pending_send_mismatch')
    if target is not None and not saved:
        client.store.update(lambda d: d['rooms'][room_id]['processing_receipts'][str(target)].update(explicit_pass={'payload': payload}))
    # The transport validates the actual server read checkpoint; replay retains
    # the same UUID, even after a lost response or a native interrupted verdict.
    result = client.enrolled(room_id, 'finish', **payload)
    if result.get('read_effect') != 'processed_frozen_receipt':
        raise BotSeshError('turn_completion_unconfirmed_retry_same_intent')
    if target is not None:
        client.store.update(lambda d: d['rooms'][room_id]['processing_receipts'][str(target)]['explicit_pass'].update(result=result))
    return result
