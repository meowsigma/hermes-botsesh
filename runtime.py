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
    """Gateway only: record which copy serves BotSesh here (pid, version, mixed). Never raises."""
    if os.environ.get('_HERMES_GATEWAY') != '1':
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
        record = {**{k: v for k, v in saved.items() if k in ('swaps', 'last_swap')},
                  'pid': os.getpid(), 'version': saved.get('version') or LOADED_VERSION,
                  'loaded_version': LOADED_VERSION, 'mixed': mixed_here(),
                  'serving': bool(serving if serving is not None else saved.get('serving')), 'at': time.time(), **extra}
        if serving:
            record['version'] = LOADED_VERSION
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


async def _hot_swap(runner, old, idle_wait=SWAP_IDLE_WAIT):
    """On the gateway loop: let the old channel finish its in-flight turn, then retire it through
    the core's retryable-fatal path so the reconnect watcher builds the new copy's adapter."""
    from gateway.config import Platform
    platform = Platform('botsesh')
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
    for task in [*list(getattr(old, '_tasks', []) or []), *list((getattr(old, '_rooms', {}) or {}).values())]:
        task.cancel()
    retired_at = time.time()
    old._set_fatal_error(SWAP_CODE, 'BotSesh plugin updated; reloading only the BotSesh channel with the new code.', retryable=True)
    logger.info('BotSesh hot swap: old channel idle after %.1fs; handing over to the new plugin code.', time.monotonic() - started)
    publish(last_swap={'retired_at': retired_at, 'waited_s': round(time.monotonic() - started, 2), 'to': LOADED_VERSION})
    await old._notify_fatal_error()
    return True


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
            task = asyncio.ensure_future(_hot_swap(runner, old))
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
    """'current' | 'swapping' | 'old' for the gateway that would serve a seat created now.

    No running gateway: current (it starts with the files on disk). A fresh install whose gateway
    never ran BotSesh: current (the in-gateway hot start loads these files)."""
    if foreign_adapter() is not None:
        return 'swapping'
    if LOADED_VERSION is not None and disk_version() not in (None, LOADED_VERSION):
        return 'old'
    pid = _running_gateway_pid()
    if not pid:
        return 'current'
    marker = read_marker()
    if marker and marker.get('pid') == pid:
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
    if state == 'current':
        return True
    if state == 'old' and nudge and os.environ.get('_HERMES_GATEWAY') != '1':
        try:
            from .hot_start import nudge_running_gateway
            nudge_running_gateway()
        except Exception:
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if gateway_state(store) == 'current':
            return True
    return False
