"""Bring this profile's BotSesh channel up without restarting Hermes (0.8.2).

Installing/enabling the plugin and `hermes botsesh connect` usually happen while the Hermes
gateway already runs. Hermes's own `reload-plugins` control verb (sent by `hermes plugins
install/enable`, and by connect below) re-registers this plugin inside that gateway, but it only
re-wires adapters that already exist: a platform plugin that was not configured at gateway start
stays down until a restart.

1. In-gateway hot start: when this module runs inside the live gateway process and BotSesh is
   enabled in config but has no adapter, it queues the platform on the gateway's own reconnect
   watcher. That core path builds, connects and publishes a fresh adapter exactly as it recovers a
   failed platform: no restart, no interrupted turn, no shutdown notices to other platforms.
2. Served secondary profiles use the public `rescan-profiles` verb, which rebuilds a served
   profile's adapters after its config changed.
3. Last resort only: a detached waiter asks for the ordinary graceful restart, which Hermes refuses
   (without a terminal to ask on) while any turn or tool is live, so it happens only once Hermes is
   idle and never interrupts the turn that ran the setup command.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)
PLATFORM = 'botsesh'


def _runner():
    run = sys.modules.get('gateway.run')  # Never import the gateway into a CLI/TUI process.
    if run is None or os.environ.get('_HERMES_GATEWAY') != '1':
        return None
    try:
        runner = run._gateway_runner_ref()
    except Exception:
        return None
    return runner if runner is not None and getattr(runner, '_running', False) else None


def _loop(runner):
    for name in ('_reconnect_watcher_task', '_session_expiry_task', '_shutdown_watchdog_task'):
        task = getattr(runner, name, None)
        try:
            if task is not None and not task.done():
                return task.get_loop()
        except Exception:
            continue
    return None


def _queue_primary(runner, scoped_home):
    """On the gateway loop: queue a configured, missing primary-profile adapter (idempotent)."""
    from hermes_constants import get_hermes_home
    if str(get_hermes_home()) != scoped_home:
        return False  # A served secondary profile: rescan-profiles rebuilds it instead.
    from gateway.config import Platform, load_gateway_config
    platform = Platform(PLATFORM)
    if platform in (getattr(runner, 'adapters', None) or {}) or platform in (getattr(runner, '_failed_platforms', None) or {}):
        return False
    config = load_gateway_config().platforms.get(platform)
    if config is None or not getattr(config, 'enabled', False):
        return False
    runner.config.platforms[platform] = config
    runner._failed_platforms[platform] = runner._reconnect_queue_entry(platform, None, config, attempts=0, delay=0.0)
    runner._ensure_reconnect_watcher_running()
    logger.info('BotSesh channel queued to start in the running gateway (no restart).')
    return True


def schedule_in_gateway():
    """Called from register(): inside the running gateway only; never raises, never blocks."""
    runner = _runner()
    if runner is None:
        return False
    loop = _loop(runner)
    if loop is None:
        return False
    try:
        from hermes_constants import get_hermes_home
        scoped = str(get_hermes_home())

        def apply():
            try:
                _queue_primary(runner, scoped)
            except Exception:
                logger.warning('BotSesh hot start was not queued; a gateway restart still loads it.', exc_info=True)
        loop.call_soon_threadsafe(apply)
        return True
    except Exception:
        return False


def nudge_running_gateway():
    """From the CLI after connect: ask the gateway serving this profile to reload plugins (which runs
    schedule_in_gateway inside it) and, for a served secondary profile, to rescan its config."""
    answer = None
    try:
        from hermes_cli.plugins_activation import reload_running_gateway
        answer = reload_running_gateway()
    except Exception:
        answer = None
    try:
        from pathlib import Path
        from gateway.control_socket import query_gateway_control
        from hermes_constants import get_default_hermes_root, get_hermes_home
        root, home = Path(get_default_hermes_root()), Path(get_hermes_home())
        if root.resolve() != home.resolve():
            query_gateway_control(root, 'rescan-profiles', timeout=10.0)
    except Exception:
        pass
    return bool(answer and answer.get('reloaded'))


WAITER = ("n=0; until hermes -p \"$1\" gateway restart --detach </dev/null >/dev/null 2>&1; "
          "do n=$((n+1)); [ $n -ge 90 ] && exit 1; sleep 20; done")


def graceful_restart_when_idle(profile):
    """Detached: the ordinary graceful restart, retried every 20 s for up to 30 min. Without a terminal,
    Hermes refuses it while any turn, cron job or delegation is live, so it runs only once idle."""
    import shutil
    import subprocess
    if not shutil.which('hermes'):
        return False
    env = {k: v for k, v in os.environ.items() if k not in ('HERMES_PROFILE', 'HERMES_PROFILE_NAME')}
    try:
        subprocess.Popen(['sh', '-c', WAITER, 'botsesh-reload', profile], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                         start_new_session=True, close_fds=True)
        return True
    except OSError:
        return False
