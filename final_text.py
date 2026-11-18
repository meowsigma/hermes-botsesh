"""0.10.2: never post a raw internal object (failure/error JSON) as a room or DM message."""
import json
import logging
import re

logger = logging.getLogger(__name__)

_FENCE = re.compile(r'^```(?:json|JSON)?\s*\n?(.*?)\n?```$', re.S)
# Structured room-turn output (room_turn.FIELDS) is a protocol shape, never a chat message.
_TURN_FIELDS = {'failure', 'reply', 'decision', 'decision_support', 'brief_edit', 'purpose_actions', 'draft_ready',
                'file_refs', 'status', 'nothing_to_add', 'builds_on', 'routine_report'}
_ERROR_FIELDS = {'error', 'code', 'message', 'status', 'detail', 'details', 'type'}
FALLBACK = "I couldn't finish that — something went wrong on my side."


def _line(value, limit=200):
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def human_text(text):
    """Return the text to post for a model final.

    A {"failure": {...}} or {"error": ...} blob, or a structured room-turn reply wrapper, is not a
    message: post the wrapped reply's own text, or ONE human line "I couldn't finish that — <short
    reason>", and keep the exact object in the local log. Ordinary prose, and JSON a person asked
    for (any other shape), is returned unchanged."""
    if not isinstance(text, str):
        return text
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    if not (body.startswith('{') and body.endswith('}')):
        return text
    try:
        value = json.loads(body)
    except ValueError:
        return text
    if not isinstance(value, dict) or not value:
        return text
    keys = set(value)
    replacement = None
    if isinstance(value.get('failure'), dict):
        failure = value['failure']
        reason = _line(failure.get('reason') or failure.get('message') or failure.get('status'))
        replacement = "I couldn't finish that — " + reason if reason else FALLBACK
    elif 'error' in value and keys <= _ERROR_FIELDS:
        error = value['error']
        reason = _line((error.get('message') or error.get('code')) if isinstance(error, dict) else (error or value.get('message')))
        replacement = "I couldn't finish that — " + reason if reason else FALLBACK
    elif 'reply' in value and keys <= _TURN_FIELDS:
        reply = value['reply']
        inner = reply.get('text') if isinstance(reply, dict) else reply
        replacement = inner if isinstance(inner, str) and inner.strip() else FALLBACK
    if replacement is None:
        return text
    # The exact detail stays in the owner's own gateway log, never in the room.
    logger.warning('BotSesh final was an internal object; posting one human line instead. Detail: %s', body[:2000])
    return replacement
