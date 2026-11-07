"""Strict Python room-turn wire contract (B66), without a JS runtime.

Validation never repairs authored bytes, acquires authority or performs effects.
The native gateway still accepts ordinary prose finals; this parser is for
explicit structured intentions, not heuristic JSON detection in room messages.
"""
from __future__ import annotations
import json
import math
import re
import unicodedata
from datetime import datetime

from .client import BotSeshError

UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.I)
TASK_ID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', re.I)
MAX_SAFE = 2**53 - 1
# ECMAScript TrimString, not Python's broader str.strip (e.g. U+0085).
JS_SPACE = '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
FIELDS = {'failure', 'reply', 'decision', 'decision_support', 'brief_edit', 'purpose_actions', 'draft_ready', 'file_refs', 'status', 'nothing_to_add', 'builds_on', 'routine_report'}


def utf16_length(value):
    return len(value.encode('utf-16-le', errors='surrogatepass')) // 2


def is_text(value, maximum, *, empty=False):
    return isinstance(value, str) and utf16_length(value) <= maximum and (empty or bool(value.strip(JS_SPACE)))


def is_number(value, minimum=0, maximum=MAX_SAFE):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value == int(value) and minimum <= value <= maximum)


def is_id(value, pattern=UUID4):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def require_client_id(value):
    if not is_id(value):
        raise BotSeshError('invalid_client_id')
    return value


def _object(value, keys, required=None):
    keys = set(keys)
    return isinstance(value, dict) and not (set(value) - keys) and set(keys if required is None else required) <= set(value)


def _controls(value, *, lines=False):
    return any(unicodedata.category(c) in ('Cc', 'Cf') or lines and c in '\u2028\u2029' for c in value)


def valid_failure(value):
    return (_object(value, ('task_id', 'status', 'reason', 'partial_notes'), ('task_id', 'status', 'reason'))
            and is_id(value['task_id'], TASK_ID) and value['status'] in ('out_of_usage', 'cant_do', 'error')
            and is_text(value['reason'], 240) and not _controls(value['reason'], lines=True)
            and ('partial_notes' not in value or is_text(value['partial_notes'], 4000, empty=True)))


def _draft(value):
    return (_object(value, ('title', 'recipientId', 'blockers'), ('title', 'recipientId'))
            and is_text(value['title'], 120) and not _controls(value['title']) and is_id(value['recipientId'])
            and ('blockers' not in value or isinstance(value['blockers'], list) and len(value['blockers']) <= 10
                 and all(is_text(b, 240) for b in value['blockers'])))


def _ref(value):
    if not isinstance(value, dict) or not isinstance(value.get('digest'), str) or not re.fullmatch('[a-f0-9]{64}', value['digest']):
        return False
    kind = value.get('kind')
    if kind == 'message':
        return _object(value, ('kind', 'number', 'version', 'digest')) and is_number(value['number']) and is_number(value['version'])
    if kind == 'brief':
        return _object(value, ('kind', 'revision', 'digest')) and is_number(value['revision'])
    return kind == 'file' and _object(value, ('kind', 'id', 'digest')) and is_text(value['id'], 100)


def _refs(value, *, nonempty=False):
    return isinstance(value, list) and len(value) <= 20 and (not nonempty or bool(value)) and all(_ref(r) for r in value)


def _evidence(value):
    return (_object(value, ('refs', 'method', 'artifactRef')) and _refs(value['refs'])
            and (bool(value['refs']) or value['method'] is not None)
            and (value['method'] is None or is_text(value['method'], 160))
            and (value['artifactRef'] is None or _ref(value['artifactRef'])))


def _utc(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z', value, re.ASCII):
        return False
    # Date.parse permits day overflow (e.g. Feb 30), but not day 0/32.
    try:
        datetime.fromisoformat(value[:8] + '01' + value[10:].replace('Z', '+00:00'))
        return 1 <= int(value[8:10]) <= 31
    except ValueError:
        return False


def _contract(value):
    if not (_object(value, ('at', 'triggers', 'reason', 'expiresAt')) and (value['at'] is None or _utc(value['at']))
            and _utc(value['expiresAt']) and is_text(value['reason'], 160)
            and isinstance(value['triggers'], list) and len(value['triggers']) <= 8
            and (value['at'] is not None or bool(value['triggers']))):
        return False
    for trigger in value['triggers']:
        if not isinstance(trigger, dict):
            return False
        key = {'mentioned': None, 'item_changed': 'item_id', 'human_replied': 'request_id', 'decision_resolved': 'action_id'}.get(trigger.get('kind'), False)
        if key is False or not (_object(trigger, ('kind',)) if key is None else _object(trigger, ('kind', key)) and is_id(trigger[key])):
            return False
    return True


_PURPOSE_KEYS = {
    'draft_ready': ('title', 'recipientId', 'resultRefs', 'blockers'),
    'amend_decision': ('decisionId', 'messageNumber', 'decisionText'),
    'support_decision': ('decisionId', 'messageNumber'),
    'report_criterion_met': ('itemId', 'itemRevision', 'evidenceRefs', 'summary'),
    'claim': ('itemId', 'itemRevision', 'ttlSeconds', 'reason'),
    'release': ('claimId', 'fence'), 'renew': ('claimId', 'fence', 'ttlSeconds', 'evidence'),
    'propose_brief_edit': ('markdown', 'basis'),
    'propose_decision': ('messageNumber', 'decisionText', 'basis', 'supportsActionId', 'replacesDecisionId'),
    'answer_question': ('itemId', 'itemRevision', 'answerRefs', 'disposition'),
    'report_action_done': ('itemId', 'itemRevision', 'claimId', 'fence', 'evidence'),
    'done_for_now': ('summary', 'resultRefs', 'blockers', 'checkBack', 'stopReason'),
    'set_check_back': ('checkBack',), 'cancel_check_back': ('contractRevision',),
}
_PURPOSE_OPTIONAL = {'draft_ready': ('blockers',), 'claim': ('ttlSeconds', 'reason'), 'renew': ('ttlSeconds',),
                     'propose_decision': ('basis', 'supportsActionId', 'replacesDecisionId')}


def _purpose(value):
    if not isinstance(value, dict) or not isinstance(value.get('op'), str) or value['op'] not in _PURPOSE_KEYS:
        return False
    op = value['op']
    keys = ('op', *_PURPOSE_KEYS[op])
    if not _object(value, keys, set(keys) - set(_PURPOSE_OPTIONAL.get(op, ()))):
        return False
    for key in ('itemId', 'claimId', 'supportsActionId', 'replacesDecisionId', 'decisionId'):
        if key in value and not (op == 'report_action_done' and key == 'claimId' and value[key] is None) and not is_id(value[key]):
            return False
    for key in ('itemRevision', 'fence', 'contractRevision'):
        if key in value and not (op == 'report_action_done' and key == 'fence' and value[key] is None) and not is_number(value[key], 1):
            return False
    for key, check in (('ttlSeconds', lambda v: is_number(v, 60, 3600)), ('reason', lambda v: is_text(v, 160, empty=True)),
                       ('messageNumber', is_number), ('decisionText', lambda v: is_text(v, 2000)),
                       ('markdown', lambda v: is_text(v, 24000, empty=True)), ('basis', _refs),
                       ('answerRefs', lambda v: _refs(v, nonempty=True) and all(r['kind'] == 'message' for r in v)),
                       ('disposition', lambda v: v in ('answered', 'disputed')), ('evidence', _evidence)):
        if key in value and not check(value[key]):
            return False
    if op == 'report_criterion_met':
        def criterion_ref(r):
            return _ref(r) or isinstance(r, dict) and (
                r.get('kind') == 'message' and _object(r, ('kind', 'number')) and is_number(r['number'])
                or r.get('kind') in ('file', 'decision') and _object(r, ('kind', 'id'))
                and (is_id(r['id']) if r['kind'] == 'decision' else is_text(r['id'], 100)))
        if not (is_text(value['summary'], 1000) and isinstance(value['evidenceRefs'], list)
                and 1 <= len(value['evidenceRefs']) <= 20 and all(criterion_ref(r) for r in value['evidenceRefs'])):
            return False
    if op == 'done_for_now' and not (is_text(value['summary'], 1000) and _refs(value['resultRefs'])
            and isinstance(value['blockers'], list) and len(value['blockers']) <= 20 and all(is_id(b) for b in value['blockers'])
            and is_text(value['stopReason'], 160, empty=value['checkBack'] is not None)
            and (value['checkBack'] is None or _contract(value['checkBack']))):
        return False
    if op == 'draft_ready':
        def draft_ref(r):
            return isinstance(r, dict) and (r.get('kind') == 'message' and _ref(r)
                or r.get('kind') == 'file' and _object(r, ('kind', 'id', 'version', 'digest'))
                and is_text(r['id'], 100) and is_number(r['version'], 1, 100)
                and isinstance(r['digest'], str) and re.fullmatch('[a-f0-9]{64}', r['digest']) is not None)
        if not (_draft({k: value[k] for k in ('title', 'recipientId', 'blockers') if k in value})
                and isinstance(value['resultRefs'], list) and 1 <= len(value['resultRefs']) <= 20 and all(draft_ref(r) for r in value['resultRefs'])):
            return False
    return op != 'set_check_back' or _contract(value['checkBack'])


def _routine(value):
    return (_object(value, ('routineId', 'revision', 'stepIndex', 'scheduledFor', 'status'))
            and is_id(value['routineId']) and is_number(value['revision'], 1) and is_number(value['stepIndex'], 0, 1)
            and is_number(value['scheduledFor']) and value['status'] in ('working', 'completed', 'failed'))


def parse_room_turn_output(output):
    def bad():
        raise BotSeshError('invalid_runtime_answer')
    try:
        value = json.loads(output, parse_constant=lambda _: bad()) if isinstance(output, str) else output
        if not isinstance(value, dict) or set(value) - FIELDS:
            bad()
        if 'failure' in value:
            if len(value) != 1 or not valid_failure(value['failure']):
                bad()
            return value
        if 'routine_report' in value and ('reply' not in value or not _routine(value['routine_report'])):
            bad()
        if 'decision_support' in value and (not value.get('decision') or not is_id(value['decision_support'])):
            bad()
        actions = value.get('purpose_actions', [])
        if 'purpose_actions' in value and (not isinstance(actions, list) or not 1 <= len(actions) <= 10
                or len(json.dumps(actions, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 32000 or not all(_purpose(a) for a in actions)):
            bad()
        if 'draft_ready' in value and ('reply' not in value or not _draft(value['draft_ready'])
                or len(actions) >= 10 or any(a['op'] == 'draft_ready' for a in actions)):
            bad()
        for key, limit in (('reply', 4000), ('decision', 2000), ('status', 160)):
            if key in value and not is_text(value[key], limit):
                bad()
        if 'decision' in value and 'reply' not in value or 'status' in value and any(ord(c) <= 31 or ord(c) == 127 for c in value['status']):
            bad()
        if 'nothing_to_add' in value and value['nothing_to_add'] is not True:
            bad()
        if 'builds_on' in value:
            b = value['builds_on']
            if not ('reply' in value and _object(b, ('number', 'change', 'constraint', 'briefRevision'))
                    and is_number(b['number']) and is_number(b['briefRevision'], 1) and is_text(b['change'], 240) and is_text(b['constraint'], 240)):
                bad()
        if 'brief_edit' in value:
            b = value['brief_edit']
            if not (_object(b, ('base_revision', 'markdown')) and is_number(b['base_revision']) and is_text(b['markdown'], 24000, empty=True)):
                bad()
        if 'file_refs' in value:
            refs = value['file_refs']
            if not (isinstance(refs, list) and len(refs) <= 10 and all(isinstance(r, str) and re.fullmatch('[A-Za-z0-9_-]{1,100}', r) for r in refs) and len(set(refs)) == len(refs)):
                bad()
        if (value.get('nothing_to_add') and any(k in value for k in ('reply', 'decision', 'brief_edit', 'purpose_actions'))
                or not value.get('nothing_to_add') and not any(k in value for k in ('reply', 'status', 'brief_edit', 'purpose_actions'))):
            bad()
        if value.get('decision') and re.match(r'^\s*(?:\*\*)?(?:Exact\s+)?Plan\s*:', value['reply'], re.I):
            def first(text):
                line = re.split(r'\r?\n', text)[0]
                sentence = re.match(r'^.*?[.!?](?=\s|$)', line)
                return (sentence[0] if sentence else line).replace('**', '').lower()
            if first(value['decision']) not in first(value['reply']):
                bad()
        return value
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError):
        bad()


def runtime_output_schema():
    """B66 provider-compatible room-turn subset: no root allOf/oneOf/anyOf.

    Exclusive failure and cross-field constraints are enforced by the parser.
    This is not installed into the ordinary native Hermes prose/tool loop.
    """
    def obj(properties, required=()):
        return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(required)}
    def text(maximum):
        return {'type': 'string', 'minLength': 1, 'maxLength': maximum}
    return obj({'failure': obj({'task_id': {'type': 'string', 'pattern': '^' + TASK_ID.pattern + '$'},
            'status': {'type': 'string', 'enum': ['out_of_usage', 'cant_do', 'error']},
            'reason': text(240), 'partial_notes': {'type': 'string', 'maxLength': 4000}}, ('task_id', 'status', 'reason')),
        'reply': text(4000), 'decision': text(2000), 'brief_edit': obj({'base_revision': {'type': 'integer', 'minimum': 0},
            'markdown': {'type': 'string', 'maxLength': 24000}}, ('base_revision', 'markdown')),
        'file_refs': {'type': 'array', 'maxItems': 10, 'uniqueItems': True, 'items': {'type': 'string', 'pattern': '^F[1-9][0-9]*$'}},
        'status': text(160), 'nothing_to_add': {'const': True}})
