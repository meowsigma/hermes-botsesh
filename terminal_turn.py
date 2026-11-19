"""Explicit pass/cant_do may end a native turn; ordinary posts never do.

Hermes exposes a public scoped interrupt, not a successful complete-turn API.
Keep that native interrupted verdict honest; confirmed room outcomes own delivery
and read attestation independently. Never reinterpret an ordinary post as a stop.
"""
import json


def settled_turn(*, tool_name='', args=None, result='', **kwargs):
    if tool_name not in ('botsesh_cant_do', 'botsesh_pass'):
        return
    try:
        envelope = json.loads(result)
    except (TypeError, ValueError):
        return
    if not isinstance(envelope, dict) or envelope.get('ok') is not True:
        return
    outcome = envelope.get('result')
    if not isinstance(outcome, dict):
        return
    from .adapter import _HOOK_LOCK, bind_turn
    with _HOOK_LOCK:
        # 0.10.4 r3: the settled turn is named only through `bind_turn` (the gateway's own room and input of this running turn).
        bound = bind_turn(ambient='required', need='settle')
        if not bound:
            return
        room, target = bound.room_id, str(bound.current['target'])
        rows = [bound.adapter.client.store.room(room).get('processing_receipts', {}).get(target, {})]
        if tool_name == 'botsesh_cant_do':
            settled = [row for row in rows if row.get('failure_code') == 'cant_do'
                       and row.get('explicit_failure', {}).get('result') == outcome
                       and outcome.get('read_effect') == 'none']
        else:
            args = args or {}
            # The executor's observer may retain raw model args while dispatch
            # validates a coerced copy (for example processed="True"). Require
            # the handler's canonical attestation, bound to this same receipt
            # and confirmed result, rather than revalidating raw processed.
            settled = [row for row in rows if args.get('room_id') == room
                       and args.get('receipt_id')
                       and row.get('receipt_id') == row.get('model_receipt_handles', {}).get(args['receipt_id'], args['receipt_id'])
                       and row.get('explicit_pass', {}).get('payload', {}).get('processed') is True
                       and row.get('explicit_pass', {}).get('payload', {}).get('receipt_id') == row.get('receipt_id')
                       and row.get('explicit_pass', {}).get('result') == outcome
                       and outcome.get('read_effect') == 'processed_frozen_receipt']
        if len(settled) != 1:
            return
    from agent.subagent_lifecycle import get_active_subagent_parent
    agent = get_active_subagent_parent()
    if agent is not None and agent.platform == 'botsesh':
        # Do not mutate private loop state or fabricate a successful model final.
        # This stop targets only the current agent, not the backup's native turn.
        agent.interrupt(hard_cancel=True, tool_reason='botsesh_passed' if tool_name == 'botsesh_pass' else 'botsesh_task_settled')
