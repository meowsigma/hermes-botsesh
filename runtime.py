"""0.10.2: in-place plugin updates under a running gateway, without a gateway restart.

`hermes plugins install --force` / an update replaces this plugin's files while the gateway keeps
running and asks it to reload plugins (`reload-plugins`). Hermes then imports a NEW copy of this
package (tools, hooks, middleware, platform factory) but keeps the gateway's live BotSesh channel,
an object of the OLD copy. Each copy has its own adapter registry, so the new hooks could not find
the old channel's turns and refused every tool call with the guest text (the owner's 2026-11-18
Dell failure); the old copy's lazy `from .x import` lines also start loading new files.

The new copy therefore hands BotSesh over to itself, BotSesh only (hot swap):

1. register() inside the gateway sees a live BotSesh channel whose class is not this copy's.
2. On the gateway loop it waits until that old channel has no turn in flight (its own code
   finishes and delivers it), then, in the same loop tick, cancels the old room loops and raises
   the core's ordinary *retryable* fatal error on the old channel.
3. The core removes only that adapter and its reconnect watcher builds a fresh adapter from the
   newly registered factory (this copy). Other platforms (Telegram, ...) are untouched; no gateway
   shutdown notice is sent. Unread BotSesh input stays queued on the server and in the durable
   receipts, so the fresh channel answers it once.

While a swap is pending, tools of an old-channel turn are refused with an accurate "updating"
message, connect and botsesh_join wait for the new channel before creating any seat, and only if
the swap cannot finish do they fall back to one honest restart line.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)
PLUGIN_DIR = Path(__file__).resolve().parent
SWAP_CODE = 'botsesh_plugin_updated'
SWAP_IDLE_WAIT = 600  # seconds an in-flight BotSesh turn may take to finish before the swap proceeds
RESTART_MESSAGE = 'BotSesh was updated; restart Hermes to finish connecting.'
RESTART_STEP = ('Tell your person exactly: "BotSesh was updated; restart Hermes to finish connecting." '
                'After Hermes restarts (`hermes gateway restart`, or quit and start Hermes again), paste or '
                'call the same invite link again. Do not reinstall the plugin.')


def _read_version():
    try:
        text = (PLUGIN_DIR / 'plugin.yaml').read_text(encoding='utf-8')
    except OSError:
        return None
    match = re.search(r'(?m)^version:\s*["\']?([0-9][0-9A-Za-z.+-]*)', text)
    return match.group(1) if match else None


LOADED_VERSION = _read_version()


def disk_version():
    return _read_version()


def _gateway_runner():
    from .hot_start import _runner
    return _runner()


def _gateway_process():
    """True only inside the process that constructed a GatewayRunner (also while it is starting).

    A child spawned by the gateway (the background `hermes botsesh connect`) inherits
    `_HERMES_GATEWAY=1` but never builds a runner, so it is not the gateway."""
    if os.environ.get('_HERMES_GATEWAY') != '1':
        return False
    run = sys.modules.get('gateway.run')
    try:
        return run is not None and run._gateway_runner_ref() is not None
    except Exception:
        return False


def _live_adapters(runner):
    """Every BotSesh channel this gateway runs for THIS module name (one profile home)."""
    from .adapter import BotSeshAdapter
    pools = [getattr(runner, 'adapters', None) or {}]
    pools += list((getattr(runner, '_profile_adapters', None) or {}).values())
    found = []
    for pool in pools:
        for platform, adapter in list(dict(pool).items()):
            if getattr(platform, 'value', platform) != 'botsesh' or adapter is None:
                continue
            # Another profile's copy has its own module name and is not ours to judge.
            if type(adapter).__module__ == BotSeshAdapter.__module__:
                found.append(adapter)
    return found


def foreign_adapter(runner=None):
    """Inside a running gateway: a live channel of an older copy of this plugin (pre-reload)."""
    try:
        runner = runner or _gateway_runner()
        if runner is None:
            return None
        from .adapter import BotSeshAdapter
        return next((a for a in _live_adapters(runner) if not isinstance(a, BotSeshAdapter)), None)
    except Exception:
        return None


def mixed_here():
    """True when THIS process runs BotSesh code that no longer matches itself or its files."""
    if LOADED_VERSION is not None and disk_version() not in (None, LOADED_VERSION):
        return True
    return foreign_adapter() is not None


def _marker_path():
    from hermes_cli.config import get_hermes_home
    return Path(get_hermes_home()) / 'plugin-data' / 'botsesh' / 'runtime.json'


def publish(*, serving=None, **extra):
    """Gateway only: record which copy serves BotSesh here (pid, version, mixed). Never raises.

    Only the process that actually hosts the running GatewayRunner writes. A child process spawned by
    the gateway (the background `hermes botsesh connect`) inherits `_HERMES_GATEWAY=1` and loads this
    plugin too; it must never overwrite the gateway's record with its own pid."""
    if not _gateway_process():
        return False
    try:
        path = _marker_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict) or saved.get('pid') != os.getpid():
            saved = {}
        record = {**{k: v for k, v in saved.items() if k in ('swaps', 'last_swap', 'swap_blocked')},
                  'pid': os.getpid(), 'version': saved.get('version') or LOADED_VERSION,
                  'loaded_version': LOADED_VERSION, 'mixed': mixed_here(),
                  'serving': bool(serving if serving is not None else saved.get('serving')), 'at': time.time(), **extra}
        if serving:
            record['version'] = LOADED_VERSION
            record.pop('swap_blocked', None)  # this copy's own channel serves now
        tmp = path.with_name(path.name + '.%d.tmp' % os.getpid())
        tmp.write_text(json.dumps(record))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def read_marker():
    try:
        value = json.loads(_marker_path().read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# Set in THIS process when the hot swap could not (or must not) run: the old channel keeps serving and
# only a gateway restart loads the update. Other processes read it from the marker (`swap_blocked`).
SWAP_BLOCKED = None


def swap_blocked(foreign=False):
    """True when this gateway's hot swap could not run (only a restart finishes the update)."""
    if SWAP_BLOCKED is not None:
        return True
    foreign = foreign_adapter() if foreign is False else foreign
    return bool(getattr(foreign, '_botsesh_swap_blocked', None))


def swap_missing(runner, old, platform):
    """Hermes internals the hot swap relies on that this gateway lacks (empty = safe to swap).

    Checked BEFORE anything is cancelled: a Hermes that renamed one of these must leave the old
    channel fully running and fall back to the restart line, never a registered-but-silent channel."""
    missing = [name for name in ('_set_fatal_error', '_notify_fatal_error', '_fatal_error_handler', '_supervise', '_mark_connected')
               if not callable(getattr(old, name, None))]
    missing += [name for name in ('_tasks', '_rooms') if not isinstance(getattr(old, name, None), (list, dict))]
    missing += ['runner.' + name for name in ('_queue_retryable_fatal_platform', '_reconnect_failed_platform')
                if not callable(getattr(runner, name, None))]
    if not isinstance(getattr(runner, '_failed_platforms', None), dict):
        missing.append('runner._failed_platforms')
    try:  # the reconnect queue only takes a platform that has a gateway config entry
        configured = runner.config.platforms.get(platform)
    except Exception:
        configured = None
    if not configured:
        missing.append('runner.config.platforms[botsesh]')
    return missing


def _block_swap(old, reason):
    global SWAP_BLOCKED
    SWAP_BLOCKED = {'reason': reason, 'at': time.time()}
    try:  # on the old channel too, so a later reloaded copy knows without trying again
        old._botsesh_swap_blocked = SWAP_BLOCKED
    except Exception:
        pass
    publish(swap_blocked=SWAP_BLOCKED)
    logger.warning('BotSesh hot swap not done (%s); the old BotSesh channel keeps serving. %s', reason, RESTART_MESSAGE)


def _revive(old, *, restart_loops):
    """Undo a failed retire on the same loop tick: the old channel serves again (no half-state)."""
    try:
        old._mark_connected()  # base: _running = True, fatal fields cleared, status 'connected'
    except Exception:
        old._running = True
        old._fatal_error_code = old._fatal_error_message = None
        old._fatal_error_retryable = True
    if restart_loops:  # its supervisor re-creates every active room loop (BotSesh adapters >= 0.8)
        old._tasks.append(asyncio.ensure_future(old._supervise()))


async def _hot_swap(runner, old, idle_wait=SWAP_IDLE_WAIT):
    """On the gateway loop: let the old channel finish its in-flight turn, then retire it through
    the core's retryable-fatal path so the reconnect watcher builds the new copy's adapter.

    Fail-safe: if this Hermes lacks an internal the swap needs, nothing is cancelled and the old
    channel keeps answering; if retiring raises, the old channel is revived. Both leave the one
    restart line as the only fallback."""
    from gateway.config import Platform
    platform = Platform('botsesh')
    missing = swap_missing(runner, old, platform)
    if missing:
        _block_swap(old, 'this Hermes lacks ' + ', '.join(missing))
        return False
    started = time.monotonic()
    while getattr(old, '_current', None) and time.monotonic() - started < idle_wait:
        await asyncio.sleep(0.25)
    if (getattr(runner, 'adapters', None) or {}).get(platform) is not old:
        logger.info('BotSesh hot swap: the old channel is already gone; nothing to hand over.')
        return False
    if getattr(old, '_current', None):
        logger.warning('BotSesh hot swap: a turn is still running after %ss; handing over anyway '
                       '(its durable receipt is recovered by the new channel).', idle_wait)
    # Same loop tick from here to the fatal report: no await, so the old channel cannot admit a new
    # turn in between. Cancelled room loops stop before admission; their queued input stays durable.
    cancelled = []
    try:
        old._set_fatal_error(SWAP_CODE, 'BotSesh plugin updated; reloading only the BotSesh channel with the new code.', retryable=True)
        for task in [*list(old._tasks), *list(old._rooms.values())]:
            task.cancel()
            cancelled.append(task)
    except Exception:
        logger.warning('BotSesh hot swap: retiring the old channel raised; reviving it.', exc_info=True)
        _revive(old, restart_loops=bool(cancelled))
        _block_swap(old, 'retire failed')
        return False
    retired_at = time.time()
    logger.info('BotSesh hot swap: old channel idle after %.1fs; handing over to the new plugin code.', time.monotonic() - started)
    publish(last_swap={'retired_at': retired_at, 'waited_s': round(time.monotonic() - started, 2), 'to': LOADED_VERSION})
    try:
        await old._notify_fatal_error()
    except Exception:
        if runner.adapters.get(platform) is old and platform not in runner._failed_platforms:
            # The core never took the old channel: its loops are cancelled, so bring it back up.
            logger.warning('BotSesh hot swap: the gateway did not take the old channel; reviving it.', exc_info=True)
            _revive(old, restart_loops=True)
            _block_swap(old, 'handover failed')
            return False
        logger.warning('BotSesh hot swap: the fatal report raised after the gateway took the old channel; '
                       'its reconnect watcher builds the new one.', exc_info=True)
    return True


async def _hot_swap_logged(runner, old):
    """The scheduled task body: an unexpected error is logged and leaves the restart line, never silence."""
    try:
        return await _hot_swap(runner, old)
    except Exception:
        logger.warning('BotSesh hot swap failed unexpectedly.', exc_info=True)
        try:
            _block_swap(old, 'unexpected error')
        except Exception:
            pass
        return False


def schedule_hot_swap():
    """Called from register(): inside the live gateway only. Never raises or blocks."""
    try:
        runner = _gateway_runner()
        if runner is None:
            return False
        old = foreign_adapter(runner)
        if old is None or getattr(old, '_botsesh_hot_swap', False):
            return False
        from .hot_start import _loop
        loop = _loop(runner)
        if loop is None:
            return False
        old._botsesh_hot_swap = True
        logger.info('BotSesh plugin files changed under the running gateway; hot-swapping the BotSesh channel (no gateway restart).')

        def start():
            task = asyncio.ensure_future(_hot_swap_logged(runner, old))
            keep = getattr(runner, '_background_tasks', None)
            if isinstance(keep, set):
                keep.add(task)
                task.add_done_callback(keep.discard)
        loop.call_soon_threadsafe(start)
        return True
    except Exception:
        logger.warning('BotSesh hot swap was not scheduled; a gateway restart still loads the update.', exc_info=True)
        return False


def _running_gateway_pid():
    try:
        from gateway.status import get_running_pid
        return get_running_pid(cleanup_stale=False)
    except Exception:
        return None


def gateway_state(store=None):
    """'current' | 'swapping' | 'old' | 'restart' for the gateway that would serve a seat created now.

    'restart': the hot swap could not run here, so the old channel keeps serving and only a gateway
    restart loads the update. No running gateway: current (it starts with the files on disk). A fresh
    install whose gateway never ran BotSesh: current (the in-gateway hot start loads these files)."""
    foreign = foreign_adapter()
    if swap_blocked(foreign):
        return 'restart'
    if foreign is not None:
        return 'swapping'
    if LOADED_VERSION is not None and disk_version() not in (None, LOADED_VERSION):
        return 'old'
    pid = _running_gateway_pid()
    if not pid:
        return 'current'
    marker = read_marker()
    if marker and marker.get('pid') == pid:
        if marker.get('swap_blocked'):
            return 'restart'
        if marker.get('mixed'):
            return 'swapping'
        if marker.get('serving') and marker.get('version') != disk_version():
            return 'old'
        return 'current'
    # This gateway never published a 0.10.2+ marker. If it already serves BotSesh rooms, those
    # are answered by a plugin copy older than 0.10.2 that is still in memory.
    try:
        rooms = store.read().get('rooms', {}) if store is not None else {}
    except Exception:
        rooms = {}
    return 'old' if any(b.get('active') and b.get('transport') == 'oauth' for b in rooms.values()) else 'current'


def gateway_needs_restart(store=None):
    return gateway_state(store) != 'current'


def wait_until_current(store=None, timeout=90, nudge=True):
    """Before any seat is created: make sure the running gateway serves THESE files.

    An old gateway (no 0.10.2+ marker, or a stale one) is asked once to reload plugins, which loads
    this copy and hot-swaps the BotSesh channel; then wait for the fresh channel. True when current,
    False when only a restart can finish (old Hermes without reload-plugins, or a stuck swap)."""
    state = gateway_state(store)
    if state in ('current', 'restart'):
        return state == 'current'  # 'restart': the swap cannot run here; say the restart line now
    if state == 'old' and nudge and not _gateway_process():
        try:
            from .hot_start import nudge_running_gateway
            nudge_running_gateway()
        except Exception:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(1.0)
        state = gateway_state(store)
        if state in ('current', 'restart'):
            return state == 'current'
    return False
