"""Native execution veto for one exact BotSesh request, never a prompt fence."""
from __future__ import annotations

DENIED = {'action': 'block', 'message': 'Tools are not authorized for this exact BotSesh request.'}


def validate_policy(value, binding, target, receipt_id, sender=None):
    from .client import BotSeshError
    if (not isinstance(value, dict) or type(value.get('toolsAllowed')) is not bool
            or value.get('room_id') != binding['room_id']
            or value.get('participant_id') != binding['participant_id']
            or value.get('number') != target or value.get('batch_id') != receipt_id
            or not isinstance(value.get('requester'), dict)
            or not value['requester'].get('id')
            or type(value['requester'].get('isOwner')) is not bool
            or sender is not None and value['requester']['id'] != sender):
        raise BotSeshError('native_tool_policy_scope_mismatch')
    return value


def enforce_tools(*, session_id='', turn_id='', **kwargs):
    from gateway.session_context import get_session_env
    from .adapter import _ADAPTERS, _HOOK_LOCK
    if get_session_env('HERMES_SESSION_PLATFORM', '') != 'botsesh':
        return None  # Never change another platform's tools or global approvals.
    room_id = get_session_env('HERMES_SESSION_CHAT_ID', '')
    target = get_session_env('HERMES_SESSION_MESSAGE_ID', '')
    try:
        with _HOOK_LOCK:
            matches = [(a, a._current[room_id]) for a in list(_ADAPTERS)
                       if room_id in a._current and str(a._current[room_id].get('target')) == target]
            if len(matches) != 1:
                return dict(DENIED)
            adapter, current = matches[0]
            if (not session_id or not turn_id or current.get('hook_turn') != (session_id, turn_id)
                    or current.get('hook_terminal') or current.get('delivered')
                    or not current.get('tool_policy', {}).get('toolsAllowed')):
                return dict(DENIED)
            binding = adapter.client.store.room(room_id)
            row = binding.get('processing_receipts', {}).get(target, {})
            if (not binding.get('active') or row.get('state') not in ('admitting', 'admitted', 'started')
                    or row.get('receipt_id') != current.get('receipt_id') or row.get('explicit_failure')):
                return dict(DENIED)
            frozen = row.get('frozen') or {}
            source = [m for m in frozen.get('batch', {}).get('messages', []) if str(m.get('number')) == target]
            if len(source) != 1 or not adapter._sender_identity_admitted(room_id, source[0]):
                return dict(DENIED)
            sender = source[0]['senderParticipantId']
            chat_type = 'dm' if binding.get('room_type') == 'dm' else 'group'
            if adapter._is_sender_authorized(sender, chat_type, room_id) is not True:
                return dict(DENIED)
        # Recheck authenticated server policy for EVERY attempted native tool:
        # changing Only me/tools-off or revoking membership stops later effects.
        live = adapter.client.enrolled(room_id, 'tool_policy', target=current['target'],
            receipt_id=current['receipt_id'], client_id=current['client_id'])
        validate_policy(live, binding, current['target'], current['receipt_id'], sender)
        with _HOOK_LOCK:
            if adapter._current.get(room_id) is not current or current.get('hook_terminal') or not live['toolsAllowed']:
                return dict(DENIED)
        return None  # The native resolver/explicit toolset/approval still applies.
    except Exception:
        return dict(DENIED)  # Unavailable/malformed policy cannot broaden capabilities.
