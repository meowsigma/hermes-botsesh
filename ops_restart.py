"""Bounded idle-aware restart and scoped, compare-before-restore rollback.

Use the Hermes lifecycle CLI, never signals/service-name guesses. Read-only live
work is a preflight, not an atomic global admission lock (see docs/OPERATIONS.md).
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from .client import BotSeshError
from .ops_status import gateway_snapshot, status_report, profile_context

ROOT = Path(__file__).resolve().parent


def source_files():
    return sorted([*ROOT.glob('*.py'), *ROOT.glob('*.mjs'), ROOT / 'plugin.yaml',
                   *ROOT.joinpath('vendor').glob('*')])


def source_snapshot():
    return {str(p.relative_to(ROOT)): base64.b64encode(p.read_bytes()).decode('ascii')
            for p in source_files() if p.is_file() and not p.is_symlink()}


def config_part(config):
    return {'platform': config.get('platforms', {}).get('botsesh'),
            'toolsets': config.get('platform_toolsets', {}).get('botsesh')}


def checkpoint(client, config, *, source=False):
    """Call before config writes; --checkpoint before a plugin update captures code too."""
    item = {'created_at': time.time(), 'home': str(profile_context()[0]), 'root': str(ROOT),
            'config': config_part(config), 'source': source_snapshot() if source else None}
    def save(data):
        # Do not overwrite an operator-prepared pre-update source recovery point.
        previous = data.get('restart_checkpoint')
        if source or not previous or previous.get('source') is None:
            data['restart_checkpoint'] = item
    client.store.update(save)


def work_snapshot(runtime):
    from gateway.control_socket import query_gateway_control
    work = query_gateway_control(Path(runtime['control_home']), 'active-work', timeout=2.0)
    if (not isinstance(work, dict) or type(work.get('active_agents')) is not int
            or work['active_agents'] < 0 or not isinstance(work.get('active_work'), list)
            or work.get('pid') != runtime.get('pid')):
        raise BotSeshError('idle_work_unavailable_no_restart')
    return max(work['active_agents'], len(work['active_work']))


def queued_admissions(runtime):
    """Read all served authorities' durable FIFOs, without opening a writer DB."""
    import sqlite3
    import contextlib
    homes = {str(profile_context()[0]), runtime['control_home']}
    for profile in runtime.get('served_profiles', []):
        if not isinstance(profile, dict) or not isinstance(profile.get('profile_id'), str):
            raise BotSeshError('idle_queue_scope_unavailable_no_restart')
        homes.add(profile['profile_id'])
    count = 0
    for home in homes:
        path = Path(home) / 'state.db'
        try:
            with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                count += db.execute("SELECT COUNT(*) FROM session_admissions WHERE status IN ('queued','started','unknown')").fetchone()[0]
        except (sqlite3.Error, ValueError):
            raise BotSeshError('idle_queue_unavailable_no_restart') from None
    return count


def pending_local(client):
    total = 0
    for room in client.store.read()['rooms'].values():
        # Preserve unfinished sends/receipts even on revoked/deleted rooms.
        total += sum(r.get('state') not in ('replied', 'failed') for r in room.get('processing_receipts', {}).values())
        total += bool(room.get('pending_final')) + len(room.get('failure_outbox', {}))
        for section in ('oauth_posts', 'oauth_turns'):
            total += sum(not r.get('complete') and not r.get('failed') for r in room.get(section, {}).values())
    return total


def wait_idle(client, config, *, max_wait, interval=1, check_inbox=True, allow_stopped=False,
              snapshot=gateway_snapshot, work=work_snapshot, queues=queued_admissions,
              clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + max_wait
    while True:
        runtime = snapshot()
        if runtime.get('running') is False and allow_stopped:
            print('Gateway proven stopped; no process work to interrupt during recovery.', flush=True)
            return runtime
        if runtime.get('running') is not True:
            raise BotSeshError('gateway_liveness_unknown_no_restart')
        count = work(runtime)
        queued = queues(runtime)
        pending = pending_local(client)
        inbox = 0
        if check_inbox:
            report = status_report(client, config, runtime=runtime)
            for row in report['rooms']:
                binding = client.store.read()['rooms'][row['room_id']]
                if not binding.get('active') or binding.get('transport') != 'oauth':
                    continue
                if not row['alive']:
                    # Deleted/inaccessible/revoked rooms have no accessible
                    # server inbox to drain. Their local pending work still gates.
                    print(f"Skipping server inbox for {row['room_id']}: {row['access']} (not alive; local pending still gates).", flush=True)
                    continue
                if type(row['backlog']) is not int or row['backlog'] < 0:
                    raise BotSeshError('inbox_drain_unavailable_no_restart')
                inbox += row['backlog']
        print(f'Waiting for idle: active work={count}, queued admissions={queued}, local pending={pending}, inbox={inbox}.', flush=True)
        if count == queued == pending == inbox == 0:
            # The lifecycle CLI checks live work once more, noninteractively.
            return runtime
        if clock() >= deadline:
            raise BotSeshError('idle_wait_timeout_no_restart')
        sleep(min(interval, max(0, deadline-clock())))


def lifecycle_restart(*, timeout):
    binary = shutil.which('hermes')
    if not binary:
        raise BotSeshError('hermes_cli_unavailable')
    print('Restarting via supported `hermes gateway restart` (no --yes/--interrupt, no --all).', flush=True)
    try:
        result = subprocess.run([binary, 'gateway', 'restart'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise BotSeshError('gateway_restart_unconfirmed') from None
    # Do not reflect arbitrary gateway output, which can include private session names.
    if result.returncode:
        raise BotSeshError('gateway_restart_refused_or_failed')


def liveness_baseline(client, config, runtime):
    """Preserve the pre-restart live room set, not a failed candidate's state."""
    report = status_report(client, config, runtime=runtime)
    bindings = client.store.read()['rooms']
    live = []
    for row in report['rooms']:
        binding = bindings[row['room_id']]
        if not binding.get('active') or binding.get('transport') != 'oauth':
            continue
        if row['alive']:
            live.append(row['room_id'])
        else:
            print(f"Pre-existing room warning: {row['room_id']} ({row['access']}); not a restart regression.", flush=True)
    home = report['home_channel']
    if not home['live']:
        print(f"Pre-existing home_channel warning: {home['room_id'] or 'not set'} ({home['state']}); not a restart regression. Use `hermes botsesh status --set-home LIVE_ROOM_ID`.", flush=True)
    return {'rooms': live, 'home_channel': home}


def healthy(client, config, before):
    baseline = before.get('liveness_baseline')
    if baseline is None:
        return False
    report = status_report(client, config)
    runtime = report['gateway']
    if runtime.get('running') is not True or runtime.get('channel') != 'up':
        return False
    # A process restart must show a new incarnation. A profile-only hot restart
    # keeps PID but gets a fresh adapter lifecycle stamp.
    changed = runtime.get('instance') != before.get('instance')
    since = runtime.get('since')
    if not changed and not (isinstance(since, (int, float)) and since >= before.get('restart_at', float('inf'))):
        return False
    rows = {r['room_id']: r for r in report['rooms']}
    for rid in baseline['rooms']:
        row = rows.get(rid)
        if (not row or not row['alive'] or row['gateway'] != 'connected'
                or (row['last_check'] or 0) < before.get('restart_at', 0)):
            return False
    home = baseline['home_channel']
    return not home['live'] or (report['home_channel']['live']
                               and report['home_channel']['room_id'] == home['room_id'])


def wait_healthy(client, config, before, *, timeout, check=healthy, clock=time.monotonic, sleep=time.sleep):
    deadline = clock()+timeout
    while True:
        if check(client, config, before):
            return True
        if clock() >= deadline:
            return False
        sleep(min(1, max(0, deadline-clock())))


def restore(client, saved, attempted):
    from hermes_cli.config import load_config_readonly, set_config_value, unset_config_value
    if (saved.get('home') != str(profile_context()[0]) or saved.get('root') != str(ROOT)
            or config_part(load_config_readonly()) != attempted['config']
            or source_snapshot() != attempted['source']):
        raise BotSeshError('rollback_conflict_preserved_concurrent_changes')
    # Validate every path and payload before changing anything, even a damaged checkpoint.
    recovered = saved.get('source')
    if recovered is not None:
        for name, blob in recovered.items():
            path = ROOT / name
            if (path.is_symlink() or path.resolve().parent not in (ROOT, ROOT/'vendor')
                    or not (name.endswith(('.py', '.mjs')) or name == 'plugin.yaml' or name.startswith('vendor/'))):
                raise BotSeshError('rollback_source_scope_denied')
            base64.b64decode(blob, validate=True)
        # No delete of newly introduced files: an old plugin does not import them.
        for name, blob in recovered.items():
            path = ROOT / name
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.botsesh-restore-')
            try:
                mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(base64.b64decode(blob, validate=True))
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), mode)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    for key, value in (('platforms.botsesh', saved['config']['platform']),
                       ('platform_toolsets.botsesh', saved['config']['toolsets'])):
        if value is None:
            if attempted['config']['platform' if key == 'platforms.botsesh' else 'toolsets'] is not None:
                unset_config_value(key)
        else:
            set_config_value(key, json.dumps(value))
    if config_part(load_config_readonly()) != saved['config']:
        raise BotSeshError('rollback_config_unconfirmed')
    if recovered is not None:
        current = source_snapshot()
        if any(current.get(name) != blob for name, blob in recovered.items()):
            raise BotSeshError('rollback_source_unconfirmed')
    return load_config_readonly()


def restart_gateway(client, config, args, *, idle=wait_idle, restart=lifecycle_restart,
                    health=wait_healthy, rollback=restore):
    if args.checkpoint:
        checkpoint(client, config, source=True)
        print('Saved private pre-change BotSesh config and plugin-source checkpoint. Apply the update, then use restart-gateway --when-idle.')
        return
    if not args.when_idle:
        raise BotSeshError('use_restart_gateway_when_idle')
    saved = client.store.read().get('restart_checkpoint')
    if not saved:
        raise BotSeshError('checkpoint_required_before_change_use_restart_gateway_checkpoint')
    attempted = {'config': config_part(config), 'source': source_snapshot()}
    print('Idle gate covers live gateway work, local BotSesh sends and server inbox. No forced interruption. New admissions can race this preflight; Hermes rechecks work at restart.', flush=True)
    before = idle(client, config, max_wait=args.max_wait)
    before['liveness_baseline'] = liveness_baseline(client, config, before)
    before['restart_at'] = time.time()
    try:
        restart(timeout=args.restart_timeout)
        print(f'Checking BotSesh channel for up to {args.health_timeout}s...', flush=True)
        success = health(client, config, before, timeout=args.health_timeout)
    except BotSeshError:
        success = False
    if success:
        client.store.update(lambda d: d.pop('restart_checkpoint', None))
        print('Gateway restarted; new channel lifecycle and pre-restart live room/home_channel baseline verified. Pre-existing warnings remain visible in `hermes botsesh status`.')
        return
    print('Candidate not healthy. Waiting for active work to finish before scoped auto-revert...', flush=True)
    # No server inbox gate here: the unhealthy candidate may be unable to drain
    # it. Never drop or restore the durable room/token/session stores.
    recovery_before = idle(client, config, max_wait=args.max_wait, check_inbox=False, allow_stopped=True)
    recovery_before['liveness_baseline'] = before['liveness_baseline']
    restored = rollback(client, saved, attempted)
    print('Reverted checkpointed BotSesh config' + (' and plugin source.' if saved.get('source') else ' (config-only checkpoint; plugin code was not changed).'), flush=True)
    recovery_before['restart_at'] = time.time()
    restart(timeout=args.restart_timeout)
    if health(client, restored, recovery_before, timeout=args.health_timeout):
        client.store.update(lambda d: d.pop('restart_checkpoint', None))
        raise BotSeshError('candidate_failed_auto_revert_healthy')
    raise BotSeshError('auto_revert_done_channel_still_unhealthy_check_gateway_status')
