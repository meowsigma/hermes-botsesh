"""Explicit model-authored failure of this native addressed task, never prose inference."""
from __future__ import annotations
import re
import unicodedata
from .client import BotSeshError

TASK_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.I)


def validate_failure(failure):
    if (not isinstance(failure, dict) or set(failure) - {'task_id', 'status', 'reason', 'partial_notes'}
            or not isinstance(failure.get('task_id'), str) or not TASK_ID.fullmatch(failure['task_id']) or failure.get('status') != 'cant_do'):
        raise BotSeshError('invalid_task_failure')
    reason = failure.get('reason')
    if (not isinstance(reason, str) or not reason.strip() or len(reason.encode('utf-16-le')) // 2 > 240
            or any(unicodedata.category(c) in ('Cc', 'Cf') or c in '\u2028\u2029' for c in reason)):
        raise BotSeshError('invalid_task_failure')
    notes = failure.get('partial_notes')
    if notes is not None and (not isinstance(notes, str) or len(notes.encode('utf-16-le')) // 2 > 4000):
        raise BotSeshError('invalid_task_failure')
    return failure


def confirm_failure(result, task_id):
    failure = result.get('failure') if isinstance(result, dict) else None
    if (not isinstance(result, dict) or result.get('read_effect') != 'none' or not isinstance(failure, dict)
            or failure.get('taskId') != task_id or failure.get('status') != 'cant_do'):
        raise BotSeshError('task_failure_unconfirmed_retry_same_intent')
    return result


def flush_failure(client, room_id, target):
    """Retry only the frozen operation; a confirmed failure never becomes a reply/read."""
    row = client.store.room(room_id).get('processing_receipts', {}).get(str(target), {})
    saved = row.get('explicit_failure')
    if not saved:
        return None
    result = saved.get('result')
    if result is None:
        result = client.enrolled(room_id, 'fail_task', **saved['payload'])
        confirm_failure(result, saved['payload']['failure']['task_id'])
    def finish(data):
        row = data['rooms'][room_id]['processing_receipts'][str(target)]
        row['explicit_failure']['result'] = result
        row.update(state='failed', failure_code='cant_do', failure_pending=False)
        row.pop('frozen', None)
        row.pop('text', None)
    client.store.update(finish)
    return result


def cant_do(reason, partial_notes=None):
    from gateway.session_context import get_session_env
    from .adapter import _ADAPTERS, _HOOK_LOCK
    room_id = get_session_env('HERMES_SESSION_CHAT_ID', '')
    message_id = get_session_env('HERMES_SESSION_MESSAGE_ID', '')
    if get_session_env('HERMES_SESSION_PLATFORM', '') != 'botsesh' or not room_id or not message_id:
        raise BotSeshError('addressed_botsesh_turn_required')
    with _HOOK_LOCK:
        candidates = [(a, a._current[room_id]) for a in list(_ADAPTERS) if room_id in a._current
                      and str(a._current[room_id]['target']) == message_id]
        if len(candidates) != 1:
            raise BotSeshError('addressed_botsesh_turn_required')
        adapter, current = candidates[0]
        target = current['target']
        row = adapter.client.store.room(room_id).get('processing_receipts', {}).get(str(target), {})
        saved = row.get('explicit_failure')
        if row.get('takeover_offer') and not row.get('takeover_claim', {}).get('result'):
            raise BotSeshError('takeover_claim_required')
        if current.get('delivered') or row.get('state') in ('replied', 'failed') and not saved:
            raise BotSeshError('task_already_settled')
        source = next((m for m in row.get('frozen', {}).get('batch', {}).get('messages', []) if m.get('number') == target), {})
        task_id = saved['payload']['failure']['task_id'] if saved else (source.get('addressing') or {}).get('taskId')
        failure = validate_failure({'task_id': task_id or '', 'status': 'cant_do', 'reason': reason,
                                    **({'partial_notes': partial_notes} if partial_notes is not None else {})})
        payload = {'failure': failure, 'receipt_id': current['receipt_id'],
                   'client_id': adapter.client.logical_id('cant-do', {'room': room_id, 'target': target, 'receipt_id': current['receipt_id']})}
        if saved and saved['payload'] != payload:
            raise BotSeshError('pending_send_mismatch')
        if not saved:
            def journal(data):
                row = data['rooms'][room_id]['processing_receipts'][str(target)]
                prior = row.get('explicit_failure')
                if prior and prior['payload'] != payload:
                    raise BotSeshError('pending_send_mismatch')
                row['explicit_failure'] = prior or {'payload': payload}
            adapter.client.store.update(journal)
    return flush_failure(adapter.client, room_id, target)


def settle_takeover(client, room_id, target):
    """Durably retry completion metadata after the native turn ended, never inference."""
    row = client.store.room(room_id).get('processing_receipts', {}).get(str(target), {})
    offer = row.get('takeover_offer')
    if not offer or row.get('takeover_settled') or row.get('state') not in ('replied', 'failed'):
        return
    if row.get('failure_pending') or row.get('explicit_failure') and not row['explicit_failure'].get('result'):
        raise BotSeshError('takeover_failure_completion_pending')
    result = client.enrolled(room_id, 'takeover_settle', event_id=offer['event_id'])
    if result.get('settled') is not True:
        raise BotSeshError('takeover_settlement_unconfirmed')
    client.store.update(lambda d: d['rooms'][room_id]['processing_receipts'][str(target)].update(takeover_settled=True))


def claim_takeover(reason, processed=False):
    """Model acknowledgment only after full native context delivery; never an auto-read."""
    from gateway.session_context import get_session_env
    from .adapter import _ADAPTERS, _HOOK_LOCK
    room_id = get_session_env('HERMES_SESSION_CHAT_ID', '')
    message_id = get_session_env('HERMES_SESSION_MESSAGE_ID', '')
    if (get_session_env('HERMES_SESSION_PLATFORM', '') != 'botsesh' or not room_id or not message_id
            or processed is not True or not isinstance(reason, str) or not reason.strip()
            or len(reason.encode('utf-16-le')) // 2 > 180 or any(unicodedata.category(c) in ('Cc', 'Cf') for c in reason)):
        raise BotSeshError('processed_takeover_turn_required')
    with _HOOK_LOCK:
        candidates = [(a, a._current[room_id]) for a in list(_ADAPTERS) if room_id in a._current
                      and str(a._current[room_id]['target']) == message_id]
        if len(candidates) != 1:
            raise BotSeshError('processed_takeover_turn_required')
        adapter, current = candidates[0]
        row = adapter.client.store.room(room_id).get('processing_receipts', {}).get(message_id, {})
        offer = row.get('takeover_offer')
        if not offer or row.get('explicit_failure') or current.get('delivered'):
            raise BotSeshError('live_takeover_offer_required')
        exact = {'event_id': offer['event_id'], 'receipt_id': current['receipt_id'], 'reason': reason, 'processed': True}
        saved = row.get('takeover_claim')
        if saved and saved['payload'] != exact:
            raise BotSeshError('pending_send_mismatch')
        if saved and saved.get('result'):
            return saved['result']
        adapter.client.store.update(lambda d: d['rooms'][room_id]['processing_receipts'][message_id].update(takeover_claim={'payload': exact}))
    result = adapter.client.enrolled(room_id, 'takeover_claim', **exact)
    binding = adapter.client.store.room(room_id)
    if (result.get('granted') is not True or result.get('task', {}).get('id') != offer['task_id']
            or result.get('task', {}).get('winner', {}).get('id') != binding.get('participant_id')):
        raise BotSeshError('takeover_claim_unconfirmed_retry_same_intent')
    # Keep the exact claim receipt, not another retained copy of private task text.
    confirmed = {'granted': True, 'task': {'id': result['task']['id'], 'winner': {'id': result['task']['winner']['id']}}, 'read_effect': result.get('read_effect')}
    adapter.client.store.update(lambda d: d['rooms'][room_id]['processing_receipts'][message_id]['takeover_claim'].update(result=confirmed))
    return confirmed
