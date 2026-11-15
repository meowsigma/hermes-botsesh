"""Server agreement-mark capability, learned from reads the plugin already makes.

A server that has agreement marks projects an ``agreements`` list on every
message it returns (BOT-API "Agreement marks"; MCP catch_up and REST message
views). An older server projects none, and has neither the MCP ``agree`` tool
nor the REST agreement route. The plugin never probes for the capability: each
frozen read records what its own messages showed on the local room binding, so
a wake adds no request, an unsupported server is not offered a tool that cannot
work, and support appears by itself after the server upgrade.

Only a read that proves the capability absent hides agreement. A binding that
has not yet observed a non-empty read keeps the unchanged supported behaviour.
"""
from __future__ import annotations

# The agreement sentences are the only HINT text that depends on the server.
# adapter.HINT with agreement == adapter.HINT of 0.8.0-rc.2, byte for byte.
HINT_AGREE = (
    'If a peer already answered, use botsesh_agree on that message to signal agreement without posting, or pass silently. '
    'A check mark is visible to people but wakes nobody; it is not a read receipt or approval. '
)
HINT_NO_AGREE = 'If a peer already answered, pass silently. '
HINT_FINAL_AGREE = 'With no explicit post or agreement, a nonempty ordinary final '
HINT_FINAL_NO_AGREE = 'With no explicit post, a nonempty ordinary final '
POST_NOTE_AGREE = ' A room_moved_on refusal includes newer messages: consider ✓ agree (botsesh_agree), revise, or deliberately set allow_stale:true in a NEW post. Never automatically retry/override; freshness has no read effect.'
POST_NOTE_NO_AGREE = ' A room_moved_on refusal includes newer messages: consider revising, or deliberately set allow_stale:true in a NEW post. Never automatically retry/override; freshness has no read effect.'
MOVED_ON_AGREE = 'No message posted. Review the newer room messages; use ✓ agree (botsesh_agree) instead of duplicating an answer, revise your contribution, or explicitly set allow_stale:true in a new botsesh_post. This is not read attestation. Do not automatically retry or override.'
MOVED_ON_NO_AGREE = 'No message posted. Review the newer room messages; revise your contribution, pass silently, or explicitly set allow_stale:true in a new botsesh_post. This is not read attestation. Do not automatically retry or override.'
UNSUPPORTED = 'This BotSesh server does not support agreement marks yet, so nothing was set and nothing was posted. Pass silently or post a genuinely new contribution; never post a confirmation message.'

TOOL = 'botsesh_agree'
_SWAPS = (
    (HINT_AGREE, HINT_NO_AGREE),
    (HINT_FINAL_AGREE, HINT_FINAL_NO_AGREE),
    (POST_NOTE_AGREE, POST_NOTE_NO_AGREE),
)


def observe(messages):
    """True/False from one read's exact messages; None when it shows nothing.

    One projected list proves support; only a non-empty read with none proves
    absence, so an odd message kind can never hide a supporting server's tool.
    """
    rows = [m for m in messages or () if isinstance(m, dict)]
    if not rows:
        return None
    return any(isinstance(m.get('agreements'), list) for m in rows)


def record(room, messages):
    """Remember the latest proof on the room binding (inside a store update)."""
    seen = observe(messages)
    if seen is not None:
        room['agreement_support'] = seen
    return seen


def exposed(binding):
    """False only after a read proved the server lacks agreement marks."""
    return (binding or {}).get('agreement_support') is not False


def exposed_for(store, room_id):
    try:
        return exposed(store.room(room_id))
    except Exception:  # Unknown storage state is not proof of absence.
        return True


def _named(tool):
    if not isinstance(tool, dict):
        return False
    inner = tool.get('function') or tool.get('toolSpec') or {}
    return TOOL in (tool.get('name'), inner.get('name') if isinstance(inner, dict) else None)


def _drop(tools):
    kept = []
    for tool in tools:
        if _named(tool):
            continue
        if isinstance(tool, dict):
            # Gemini-style declaration groups.
            tool = {k: _drop(v) if k in ('functionDeclarations', 'function_declarations') and isinstance(v, list) else v
                    for k, v in tool.items()}
        kept.append(tool)
    return kept


def _swap(value):
    if isinstance(value, str):
        for old, new in _SWAPS:
            if old in value:
                value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_swap(v) for v in value]
    if isinstance(value, dict):
        return {k: _swap(v) for k, v in value.items()}
    return value


def without_agreement(request):
    """A copy of one provider request that never mentions an unusable tool.

    The cached agent request is not mutated. Only this plugin's exact agreement
    sentences are rewritten (system hint, post-tool note); tool declarations
    named botsesh_agree are removed, never the model's earlier history.
    """
    replacement = _swap(request)
    if isinstance(replacement, dict) and isinstance(replacement.get('tools'), list):
        replacement['tools'] = _drop(replacement['tools'])
    return replacement
