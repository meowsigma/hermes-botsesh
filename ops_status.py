"""Read-only operational status. Server access is never gateway execution proof."""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone
from .client import BotSeshError


def profile_context():
    from hermes_cli.config import get_hermes_home
    from hermes_cli.profiles import current_profile_name
    return Path(get_hermes_home()), current_profile_name()


def gateway_snapshot():
    """Query the live owner, without ensure (which can start/replace a gateway)."""
    home, profile = profile_context()
    answer = {'running': None, 'profile': profile, 'channel': 'unknown', 'since': None,
              'pid': None, 'control_home': str(home), 'instance': None}
    try:
        from hermes_cli.gateway_runtime import discover_gateway_endpoint
        from gateway.control_socket import query_gateway_control
        from gateway.status import resolve_gateway_liveness
        liveness = resolve_gateway_liveness(profile_dir=home, use_cache=False)
        answer.update(running=liveness.running if not liveness.probe_error else None,
                      pid=liveness.pid, channel='down' if not liveness.running and not liveness.probe_error else 'unknown')
        discovery = discover_gateway_endpoint(home, timeout=2.0)
        if discovery.endpoint is None:
            return answer
        control = Path(discovery.endpoint.control_home)
        identify = query_gateway_control(control, 'identify', timeout=2.0)
        live = query_gateway_control(control, 'status', timeout=2.0)
        if not identify or not live or identify.get('pid') != live.get('answering_pid'):
            return answer
        answer.update(running=True, pid=identify['pid'], control_home=str(control),
                      process_start=identify.get('start_time'),
                      instance=identify.get('instance_id') or identify.get('start_time'),
                      served_profiles=identify.get('served_profiles') or [{'profile_id': str(home)}],
                      owner_profile=identify.get('profile'))
        platforms = live.get('platforms') or {}
        native = platforms.get(profile + ':botsesh')
        if native is None and (identify.get('profile') == profile or control.resolve() == home.resolve()):
            native = platforms.get('botsesh')
        native = native or {}
        # A live host with an old writer's platform record is not a live channel.
        writer_matches = (identify.get('start_time') is not None and native.get('writer_pid') == identify['pid']
                          and native.get('writer_start_time') == identify.get('start_time'))
        answer.update(channel='up' if native.get('state') == 'connected' and writer_matches else 'down',
                      native_state=native.get('state', 'not_loaded'), observed_at=native.get('updated_at'))
        return answer
    except (ImportError, AttributeError, OSError, ValueError, TypeError):
        return answer


def record_channel(store, state, instance):
    """Adapter lifecycle stamps, not a CLI claiming the adapter is connected."""
    def save(data):
        from gateway.status import get_process_start_time
        old = data.get('channel_runtime', {})
        key = (os.getpid(), instance, state)
        if (old.get('pid'), old.get('instance'), old.get('state')) != key:
            data['channel_runtime'] = {'pid': os.getpid(), 'process_start': get_process_start_time(os.getpid()),
                                      'instance': instance, 'state': state, 'since': time.time()}
    store.update(save)


def access_state(client, room_id, binding):
    if not binding.get('active'):
        return binding.get('state', 'detached'), False
    if binding.get('transport') == 'guest_manager':
        try:
            client.enrolled(room_id, 'verify')
            return 'membership_verified', False
        except BotSeshError as error:
            return error.code, False
    if binding.get('transport') != 'oauth':
        return 'advanced_transport_not_probed', False
    try:
        # list_rooms is a membership/access check; never read/ack room content.
        from .oauth import OAuthClient
        rows = OAuthClient(client.store).rpc(binding, 'list_rooms').get('rooms')
        if not isinstance(rows, list):
            return 'invalid_response', False
        if not any(r.get('room_id') == room_id and r.get('participant_id') == binding.get('participant_id') for r in rows):
            return 'deleted_or_inaccessible', False
        return 'alive', True
    except BotSeshError as error:
        state = 'deleted' if error.code in ('room_not_found', 'room_deleted', 'room_expired') or error.status in (404, 410) else error.code
        return state, False


def home_status(config, rows):
    platform = config.get('platforms', {}).get('botsesh', {})
    home = platform.get('home_channel') or {}
    rid = home.get('chat_id') if isinstance(home, dict) else str(home)
    row = next((r for r in rows if r['room_id'] == rid), None)
    allowed = platform.get('extra', {}).get('group_allowed_chats', [])
    live = bool(row and row['alive'] and rid in allowed)
    return {'room_id': rid, 'live': live, 'state': ('alive' if live else row['access'] if row else 'not_connected') if rid else 'not_set',
            'candidates': [r['room_id'] for r in rows if r['alive'] and r['room_id'] in allowed]}


def status_report(client, config, *, runtime=None):
    runtime = dict(gateway_snapshot() if runtime is None else runtime)
    data = client.store.read()
    channel_stamp = data.get('channel_runtime', {})
    if (channel_stamp.get('pid') == runtime.get('pid') and channel_stamp.get('state') == runtime.get('channel')
            and channel_stamp.get('process_start') == runtime.get('process_start')):
        runtime['since'] = channel_stamp.get('since')
    rows = []
    for rid, binding in data['rooms'].items():
        access, alive = access_state(client, rid, binding)
        h = binding.get('health', {})
        state = h.get('state', 'not_observed')
        if not alive or runtime.get('channel') != 'up':
            state = 'down'
        elif not h.get('last_check') or time.time() - h['last_check'] > 90:
            state = 'stale_or_offline'
        elif runtime.get('since') and h['last_check'] < runtime['since']:
            state = 'not_observed_for_current_runtime'
        if binding.get('transport') == 'guest_manager':
            state = 'not_supported_management_only'
        backlog = None
        if alive:
            try:
                backlog = client.enrolled(rid, 'check').get('backlog')
            except BotSeshError as error:
                access, alive, state = error.code, False, 'down'
        rows.append({'room_id': rid, 'name': binding.get('name', rid), 'access': access, 'alive': alive,
                     'gateway': state, 'last_event': h.get('last_event'), 'last_reply': h.get('last_reply'),
                     'last_received_event': h.get('last_received_event'),
                     'last_sent_event': h.get('last_reply'),
                     'last_check': h.get('last_check'), 'backlog': backlog,
                     'reply_pending': bool(binding.get('pending_final')),
                     'failure_notices_pending': len(binding.get('failure_outbox', {})),
                     'forget_hint': None if alive else f'hermes botsesh forget {rid}'})
    return {'gateway': runtime, 'profile': runtime.get('profile'), 'rooms': rows, 'home_channel': home_status(config, rows)}


def set_home(client, config, room_id):
    binding = client.store.read()['rooms'].get(room_id)
    if not binding or not access_state(client, room_id, binding)[1]:
        raise BotSeshError('home_channel_not_live')
    if room_id not in config.get('platforms', {}).get('botsesh', {}).get('extra', {}).get('group_allowed_chats', []):
        raise BotSeshError('home_channel_not_allowed')
    from hermes_cli.config import set_config_value
    value = {'platform': 'botsesh', 'chat_id': room_id, 'name': binding['name']}
    set_config_value('platforms.botsesh.home_channel', json.dumps(value))
    from hermes_cli.config import load_config_readonly
    if load_config_readonly().get('platforms', {}).get('botsesh', {}).get('home_channel') != value:
        raise BotSeshError('home_channel_write_unconfirmed')
    print(f'home_channel set to live room {room_id}.')


def print_home(home):
    print(f'home_channel={home["room_id"] or "not set"} · {home["state"]} · live={home["live"]}')
    if not home['live']:
        for rid in home['candidates']:
            print(f'  Set it to a live room: hermes botsesh status --set-home {rid}')


def print_status(report):
    def stamp(at):
        return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec='seconds') if isinstance(at, (int, float)) else at or 'unknown'
    g = report['gateway']
    print(f'Profile: {report["profile"]} · gateway process={g["running"]} · BotSesh channel={g["channel"]} · since={stamp(g.get("since"))}')
    if not g.get('since'):
        print(f'  Connection-start time unavailable on this runtime; last channel observation={stamp(g.get("observed_at"))}.')
    for r in report['rooms']:
        print(f'{r["name"]} · {r["room_id"]} · access={r["access"]} · gateway={r["gateway"]} · backlog={r["backlog"]}')
        print(f'  last observed received event={stamp(r["last_received_event"])} · last confirmed sent reply={stamp(r["last_sent_event"])}')
        if r['forget_hint']:
            print(f'  Local cleanup (does not revoke access): {r["forget_hint"]}')
    if not report['rooms']:
        print('No BotSesh rooms. Run `hermes botsesh connect`.')
    print_home(report['home_channel'])
