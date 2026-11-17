"""Ephemeral live frames for BotSesh (protocol v1, `hermes_live`): draft text, tool steps, status.

Presentation only. A frame is never a room message, read attestation, delivery receipt, model
input or permission. The server decides who may see it (DM human / agent owner only). Every
failure stays local: the durable final/failure path is unchanged and never waits on a frame.

Sources are Hermes's own observers, so the owner's config is untouched:
- drafts: plugin stream hooks on_stream_start / on_stream_delta(kind=text) / on_stream_end
  (fire on every streamed model response, whether or not gateway `streaming.enabled` is on);
- steps: pre_tool_call (after BotSesh's own tool policy allowed it) and post_tool_call;
- status: the gateway session entry, Hermes config and approval state, read after turns/commands.
Hook callbacks run on Hermes threads; all relay state is mutated on the adapter's event loop only.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid
from collections import OrderedDict

from .client import BotSeshError

logger = logging.getLogger(__name__)

DRAFT_INTERVAL = 0.25      # <= 4 draft frames/s (Telegram parity)
ACTIVITY_INTERVAL = 0.5    # <= 2 activity frames/s; with drafts stays under the server's 8/s
MAX_DRAFT_UNITS = 32000    # server cap is 32 768 UTF-16 units; keep headroom
MAX_STEPS = 50
DISABLE_SECONDS = 600      # a definitive non-rate refusal means "no live channel here" for 10 min
STATUS_COMMANDS = frozenset({'model', 'new', 'reset', 'compress', 'yolo', 'approvals', 'busy',
                             'resume', 'verbose', 'reasoning', 'fast', 'retry', 'undo', 'title'})


def _units(text):
    return len(text.encode('utf-16-le')) // 2


def _cut(value, limit):
    text = str(value or '')
    if _units(text) <= limit:
        return text
    return text.encode('utf-16-le')[:limit * 2].decode('utf-16-le', errors='ignore')


def _clean(text):
    # Server refuses NUL/control characters except \n and \t.
    return ''.join(c for c in str(text or '') if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127))


def _now_ms():
    return int(time.time() * 1000)


def tool_preview(tool_name, args):
    """Hermes's own bounded preview, then Hermes's egress secret scrub. Never full args."""
    try:
        from agent.display import prepare_tool_preview
        text = prepare_tool_preview(tool_name, args if isinstance(args, dict) else {}, fallback='', max_len=60).text
    except Exception:
        text = ''
    try:
        from agent.redact import redact_for_egress
        text = redact_for_egress(text)
    except Exception:
        return ''  # Fail closed: no preview rather than an unscrubbed one.
    return _cut(_clean(text).replace('\n', ' ').strip(), 200)


class _Turn:
    def __init__(self, target):
        self.target = target
        self.draft_id = str(uuid.uuid4())
        self.text = ''
        self.steps = OrderedDict()
        self.total = 0
        self.dirty = {}
        self.inflight = {}
        self.timer = {}
        self.last = {}
        self.sent = set()          # kinds actually sent for this turn
        self.draft_accepted = False
        self.final = False         # the durable final is being posted: no later draft frame


class LiveRelay:
    def __init__(self, adapter):
        self.adapter = adapter
        self.loop = None
        self._turns = {}
        self._seq = {}
        self._off_until = {}
        self._tasks = set()
        self._closed = set()       # (room, target) whose live view ended (Stop / new session)
        self._proven = set()       # rooms whose server answered hermes_live (DM P1 capability)

    # ── thread-safe entry points (Hermes hook threads) ──────────────────────────────────────
    def attach(self, loop):
        self.loop = loop

    def _call(self, fn, *args):
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass

    def stream_start(self, room_id, target):
        self._call(self._on_stream_start, room_id, target)

    def stream_delta(self, room_id, target, delta):
        self._call(self._on_delta, room_id, target, delta)

    def tool_started(self, room_id, target, call_id, tool_name, args):
        preview = tool_preview(tool_name, args)  # Compute on the hook thread; args never cross.
        self._call(self._on_tool, room_id, target, call_id, tool_name, preview, 'running', None)

    def tool_finished(self, room_id, target, call_id, tool_name, failed):
        self._call(self._on_tool, room_id, target, call_id, tool_name, None, 'failed' if failed else 'done', _now_ms())

    # ── loop-thread state ───────────────────────────────────────────────────────────────────
    def available(self, room_id):
        return self._off_until.get(room_id, 0) <= time.monotonic()

    def proven(self, room_id):
        """This room's server implements hermes_live (it accepted or seq-refused a frame)."""
        return room_id in self._proven and self.available(room_id)

    def draft_id_for(self, room_id, target):
        """draft_id for the final only after the server accepted a draft for this exact turn."""
        turn = self._turns.get(room_id)
        if turn and turn.target == target and turn.draft_accepted and self.available(room_id):
            return turn.draft_id
        return None

    def _turn(self, room_id, target, create=True):
        turn = self._turns.get(room_id)
        if turn is not None and turn.target == target:
            return turn
        if not create or (room_id, target) in self._closed or not self._eligible(room_id, target):
            return None
        if turn is not None:
            self._cancel_timers(turn)
        turn = self._turns[room_id] = _Turn(target)
        return turn

    def _eligible(self, room_id, target):
        if not self.available(room_id):
            return False
        try:
            room = self.adapter.client.store.room(room_id)
        except Exception:
            return False
        current = self.adapter._current.get(room_id)
        return (room.get('transport') == 'oauth' and room.get('active') is True
                and current is not None and current.get('target') == target)

    def _on_stream_start(self, room_id, target):
        turn = self._turn(room_id, target)
        if turn is None:
            return
        # A new model response (e.g. after a tool call) restarts the visible draft text.
        if turn.text:
            turn.text = ''
            turn.dirty['draft'] = True
            self._kick(room_id, 'draft')

    def _on_delta(self, room_id, target, delta):
        turn = self._turn(room_id, target)
        if turn is None or turn.final or not isinstance(delta, str) or not delta:
            return
        if _units(turn.text) >= MAX_DRAFT_UNITS:
            return
        turn.text = _cut(turn.text + _clean(delta), MAX_DRAFT_UNITS)
        turn.dirty['draft'] = True
        self._kick(room_id, 'draft')

    def _on_tool(self, room_id, target, call_id, tool_name, preview, state, ended_at):
        turn = self._turn(room_id, target)
        if turn is None:
            return
        key = _cut(call_id or ('step-%d' % (turn.total + 1)), 64)
        step = turn.steps.get(key)
        if step is None:
            if state != 'running':
                return  # A completion without its start is not reconstructed.
            turn.total += 1
            step = turn.steps[key] = {'id': key, 'tool': _cut(tool_name or 'tool', 64) or 'tool',
                                      'preview': preview or '', 'state': 'running', 'startedAt': _now_ms()}
            while len(turn.steps) > MAX_STEPS:
                turn.steps.popitem(last=False)
        else:
            step['state'] = state
            if ended_at:
                step['endedAt'] = max(ended_at, step['startedAt'])
        turn.dirty['activity'] = True
        self._kick(room_id, 'activity')

    def _cancel_timers(self, turn):
        for handle in list(turn.timer.values()):
            handle.cancel()
        turn.timer.clear()

    def _kick(self, room_id, kind):
        turn = self._turns.get(room_id)
        if turn is None or turn.inflight.get(kind) or turn.timer.get(kind):
            return
        interval = DRAFT_INTERVAL if kind == 'draft' else ACTIVITY_INTERVAL
        wait = turn.last.get(kind, 0) + interval - time.monotonic()
        if wait > 0:
            turn.timer[kind] = self.loop.call_later(wait, self._fire, room_id, turn, kind)
        else:
            self._fire(room_id, turn, kind)

    def _fire(self, room_id, turn, kind):
        turn.timer.pop(kind, None)
        if self._turns.get(room_id) is not turn or not turn.dirty.get(kind):
            return
        if kind == 'draft' and turn.final:
            return
        turn.dirty[kind] = False
        if kind == 'draft':
            frame = {'kind': 'draft', 'turnTarget': turn.target, 'draftId': turn.draft_id, 'text': turn.text}
        else:
            frame = {'kind': 'activity', 'turnTarget': turn.target, 'steps': [dict(s) for s in turn.steps.values()]}
            if turn.total > len(turn.steps):
                frame['total'] = turn.total
        turn.inflight[kind] = True
        turn.last[kind] = time.monotonic()
        self._spawn(self._post_turn_frame(room_id, turn, kind, frame))

    async def _post_turn_frame(self, room_id, turn, kind, frame):
        try:
            result = await self._post(room_id, frame)
            if result and result.get('accepted') is True:
                turn.sent.add(kind)
                if kind == 'draft':
                    turn.draft_accepted = True
        finally:
            turn.inflight[kind] = False
            if self._turns.get(room_id) is turn and turn.dirty.get(kind):
                self._kick(room_id, kind)

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _next_seq(self, room_id, kind, target):
        # Wall-clock based so a restarted gateway never looks stale to the server.
        key = (room_id, kind, target)
        value = max(self._seq.get(key, 0) + 1, _now_ms())
        self._seq[key] = value
        return value

    async def _post(self, room_id, frame):
        if not self.available(room_id):
            return None
        frame = {'version': 1, **frame}
        frame['seq'] = self._next_seq(room_id, frame['kind'], frame.get('turnTarget'))
        try:
            result = await asyncio.to_thread(self.adapter.client.enrolled, room_id, 'live', frame=frame)
            if isinstance(result, dict) and (result.get('accepted') is True or result.get('reason') in ('stale', 'stale_seq')):
                self._proven.add(room_id)
            return result
        except BotSeshError as error:
            if error.terminal:
                return None  # The room loop owns revocation handling.
            if error.status == 429 or error.code in ('live_rate', 'live_rate_limited'):
                return None  # Drop this frame; the next snapshot supersedes it.
            if error.definitive or error.status in (400, 403, 404, 405, 413) or error.code == 'mcp_operation_denied':
                if error.code not in ('live_forbidden', 'turn_not_addressed') and error.status != 403:
                    self._off_until[room_id] = time.monotonic() + DISABLE_SECONDS
                    logger.info('BotSesh live frames unavailable in room %s (%s); durable replies unchanged.', room_id, error.code)
            return None
        except Exception as error:
            logger.debug('BotSesh live frame not sent (%s).', type(error).__name__)
            return None

    # ── turn lifecycle (loop thread) ───────────────────────────────────────────────────────
    def begin_turn(self, room_id, target):
        if self._turns.get(room_id) is not None and self._turns[room_id].target != target:
            self._drop(room_id)

    def _drop(self, room_id):
        turn = self._turns.pop(room_id, None)
        if turn is not None:
            self._cancel_timers(turn)
        return turn

    async def end_turn(self, room_id, target):
        """After the durable final/failure settled: end this turn's live view, then publish status."""
        turn = self._turns.get(room_id)
        if turn is not None and turn.target == target:
            self._drop(room_id)
            # Flush the final step states once, then end the live view. The client folds the
            # finished steps into the final message; a cleared draft never becomes a bubble.
            if turn.steps and (turn.dirty.get('activity') or 'activity' in turn.sent):
                await self._post(room_id, {'kind': 'activity', 'turnTarget': turn.target,
                                           'steps': [dict(s) for s in turn.steps.values()],
                                           **({'total': turn.total} if turn.total > len(turn.steps) else {})})
            if turn.sent or turn.draft_accepted:
                await self._post(room_id, {'kind': 'clear', 'turnTarget': turn.target})
        await self.publish_status(room_id)

    def end_turn_soon(self, room_id, target):
        if self.loop is not None:
            self._spawn(self.end_turn(room_id, target))

    async def clear_now(self, room_id):
        """Stop / interrupt / new session: end the live view without waiting for the final."""
        turn = self._drop(room_id)
        current = self.adapter._current.get(room_id)
        target = turn.target if turn is not None else (current or {}).get('target')
        if target is not None:
            # Late deltas/tool events of the stopped turn must not reopen its live view.
            self._closed.add((room_id, target))
        if turn is not None and (turn.sent or turn.draft_accepted):
            await self._post(room_id, {'kind': 'clear', 'turnTarget': turn.target})

    def close_drafts(self, room_id, target):
        """The final is about to be posted: no later draft frame for this turn."""
        turn = self._turns.get(room_id)
        if turn is not None and turn.target == target:
            turn.final = True
            handle = turn.timer.pop('draft', None)
            if handle is not None:
                handle.cancel()

    async def publish_status_later(self, room_id, delays=(1.5, 5.0)):
        for delay in delays:
            await asyncio.sleep(delay)
            await self.publish_status(room_id)

    # ── status (DMs) ───────────────────────────────────────────────────────────────────────
    async def publish_status(self, room_id):
        try:
            room = self.adapter.client.store.room(room_id)
        except Exception:
            return None
        if room.get('room_type') != 'dm' or room.get('transport') != 'oauth' or not self.available(room_id):
            return None
        try:
            # Config/model-metadata reads can touch disk or a cached catalog: off the loop.
            status = await asyncio.to_thread(build_status, self.adapter, room_id)
        except Exception as error:
            logger.debug('BotSesh status frame unavailable (%s).', type(error).__name__)
            return None
        if status is None:
            return None
        return await self._post(room_id, {'kind': 'status', 'turnTarget': None, **status})


_CONTEXT_MAX = {}


# ── Hermes hook entry points (registered in __init__.register) ──────────────────────────────
def _locate(session_id='', turn_id=''):
    """(adapter, room, target) of the exact in-flight BotSesh turn this hook belongs to, else None.

    Bound to the native turn identity (`hook_turn`) recorded at pre_llm_call; falls back to the
    session's own platform/chat scope only when that room has exactly one in-flight turn."""
    try:
        from .tool_policy import _scope
        platform, room_id, _message = _scope()
    except Exception:
        platform, room_id = '', ''
    if platform and platform != 'botsesh':
        return None
    from .adapter import _ADAPTERS, _HOOK_LOCK
    with _HOOK_LOCK:
        if session_id and turn_id:
            for adapter in list(_ADAPTERS):
                for rid, current in list(adapter._current.items()):
                    if current.get('hook_turn') == (session_id, turn_id) and not current.get('hook_terminal'):
                        return adapter, rid, current['target']
        if platform == 'botsesh' and room_id:
            found = [(adapter, adapter._current[room_id]) for adapter in list(_ADAPTERS) if room_id in adapter._current]
            if len(found) == 1 and not found[0][1].get('hook_terminal'):
                return found[0][0], room_id, found[0][1]['target']
    return None


def stream_start_hook(session_id='', turn_id='', **_kwargs):
    where = _locate(session_id, turn_id)
    if where:
        where[0].live.stream_start(where[1], where[2])


def stream_delta_hook(delta='', kind='text', session_id='', turn_id='', **_kwargs):
    if kind != 'text' or not delta:
        return  # Reasoning deltas are never shown.
    where = _locate(session_id, turn_id)
    if where:
        where[0].live.stream_delta(where[1], where[2], delta)


def tool_started_hook(tool_name='', args=None, session_id='', turn_id='', tool_call_id='', **_kwargs):
    where = _locate(session_id, turn_id)
    if where:
        where[0].live.tool_started(where[1], where[2], tool_call_id, tool_name, args)


def tool_finished_hook(tool_name='', session_id='', turn_id='', tool_call_id='', status=None, **_kwargs):
    where = _locate(session_id, turn_id)
    if where:
        where[0].live.tool_finished(where[1], where[2], tool_call_id, tool_name, status == 'error')


def build_status(adapter, room_id):
    """What Hermes itself reports right now. Unknown fields are omitted, never invented."""
    context = getattr(adapter, '_dm_contexts', {}).get(room_id)
    if not context:
        return None
    session_key = context.get('session_key')
    out = {}
    entry = None
    store = getattr(adapter, '_session_store', None)
    lookup = getattr(store, 'lookup_by_session_key', None)
    if callable(lookup) and session_key:
        try:
            entry = lookup(session_key)
        except Exception:
            entry = None
    try:
        from hermes_cli.config import load_config_readonly
        config = load_config_readonly() or {}
    except Exception:
        config = {}
    model_cfg = config.get('model') if isinstance(config.get('model'), dict) else {}
    override = getattr(entry, 'model_override', None) if entry is not None else None
    override = override if isinstance(override, dict) else {}
    model = override.get('model') or model_cfg.get('default') or (config.get('model') if isinstance(config.get('model'), str) else None)
    provider = override.get('provider') or model_cfg.get('provider')
    if model:
        out['model'] = _cut(model, 80)
    if provider:
        out['provider'] = _cut(provider, 80)
    agent_cfg = config.get('agent') if isinstance(config.get('agent'), dict) else {}
    if isinstance(agent_cfg.get('reasoning_effort'), str) and agent_cfg['reasoning_effort']:
        out['reasoning'] = _cut(agent_cfg['reasoning_effort'], 32)
    if entry is not None:
        if isinstance(getattr(entry, 'session_id', None), str) and entry.session_id:
            out['sessionId'] = _cut(entry.session_id, 64)
        used = getattr(entry, 'last_prompt_tokens', 0)
        if isinstance(used, int) and used > 0:
            out['contextUsed'] = used
        total = getattr(entry, 'total_tokens', 0)
        if isinstance(total, int) and total > 0:
            out['sessionTokens'] = total
        cost = getattr(entry, 'estimated_cost_usd', 0.0)
        if isinstance(cost, (int, float)) and cost > 0 and getattr(entry, 'cost_status', 'unknown') != 'unknown':
            out['sessionCost'] = float(cost)
        created = getattr(entry, 'created_at', None)
        if created is not None and hasattr(created, 'timestamp'):
            out['sessionStartedAt'] = int(created.timestamp() * 1000)
        title = getattr(entry, 'display_name', None)
        if isinstance(title, str) and title.strip():
            out['title'] = _cut(title.strip(), 120)
    if model:
        key = (model, provider or '')
        if key not in _CONTEXT_MAX:
            try:
                from agent.model_metadata import get_model_context_length
                limit = get_model_context_length(model, provider=provider or '')
                _CONTEXT_MAX[key] = limit if isinstance(limit, int) and limit > 0 else None
            except Exception:
                _CONTEXT_MAX[key] = None
        if _CONTEXT_MAX[key]:
            out['contextMax'] = _CONTEXT_MAX[key]
    runner = getattr(adapter, 'gateway_runner', None)
    try:
        source = adapter.build_source(chat_id=room_id, chat_type='dm', user_id=context.get('human_id'), user_name='DM owner')
        mode = runner._effective_busy_input_mode(source) if runner is not None else None
        if mode in ('queue', 'steer', 'interrupt'):
            out['busyMode'] = mode
    except Exception:
        pass
    try:
        from gateway.display_config import resolve_tool_progress
        progress, _explicit = resolve_tool_progress(config, 'botsesh')
        if progress in ('off', 'new', 'all', 'verbose', 'log'):
            out['toolProgress'] = progress
    except Exception:
        pass
    approvals = approval_state(entry, session_key)
    if approvals is not None:
        out['approvals'] = approvals
    try:
        host = socket.gethostname().split('.')[0]
        if host:
            out['host'] = _cut(host, 64)
    except Exception:
        pass
    return out


def approval_state(entry, session_key):
    """Hermes's own approval setting: approvals.mode + this session's /yolo bypass."""
    try:
        from tools.approval_context import _get_approval_mode
        mode = _get_approval_mode()
    except Exception:
        return None
    if mode not in ('manual', 'smart', 'off'):
        return None
    yolo = None
    persisted = getattr(entry, 'yolo', None) if entry is not None else None
    try:
        from tools.approval import is_session_yolo_enabled
        live = is_session_yolo_enabled(session_key) if session_key else False
    except Exception:
        live = None
    if persisted is True or persisted is False:
        yolo = persisted
    if live is True:
        yolo = True
    if yolo is None:
        yolo = bool(live)
    return {'mode': mode, 'yolo': yolo}
