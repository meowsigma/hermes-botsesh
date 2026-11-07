"""Native `hermes botsesh` command. Registration is side-effect free."""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from .client import Client, BotSeshError
from .storage import StorageError
from .oauth import OAuthClient


def setup(parser):
    sub = parser.add_subparsers(dest='botsesh_action', required=True)
    connect = sub.add_parser('connect', help='Sign in in your browser and approve one room')
    connect.add_argument('--no-browser', action='store_true', help='Print the browser URL (SSH loopback forwarding supported)')
    connect.add_argument('--no-start', action='store_true', help='Connect without ensuring a local gateway runtime')
    connect.add_argument('--room', help='Public room ID hint; access still requires the signed-in host’s Allow')
    connect.add_argument('--name', help='Addressable agent name (defaults to the Hermes profile name, then Hermes)')
    connect.add_argument('--timeout', type=int, default=180, choices=range(15, 601), metavar='SECONDS')
    connect.add_argument('--dev-origin', default='https://botsesh.com', help=argparse.SUPPRESS)
    status = sub.add_parser('status', help='Check access, per-room gateway health, replies and backlog')
    status.add_argument('--json', action='store_true', help='Content-free room health for monitoring')
    disconnect = sub.add_parser('disconnect', help='Revoke one room connection on BotSesh')
    disconnect.add_argument('room_id', nargs='?', help='Room ID; optional when there is only one OAuth connection')


def configure(binding):
    from hermes_cli.config import load_config, set_config_value
    cfg = load_config()
    platform = cfg.get('platforms', {}).get('botsesh', {})
    rooms = list(platform.get('extra', {}).get('group_allowed_chats', []))
    if binding['room_id'] not in rooms:
        rooms.append(binding['room_id'])
    # This exact room's Allow screen explicitly authorizes room members to
    # address the local agent and use its model allowance while the gateway runs.
    set_config_value('platforms.botsesh.extra.group_allowed_chats', json.dumps(rooms))
    set_config_value('platforms.botsesh.enabled', 'true')
    if not platform.get('home_channel'):
        set_config_value('platforms.botsesh.home_channel', json.dumps({
            'platform': 'botsesh', 'chat_id': binding['room_id'], 'name': binding['name']}))
    # Enable room tools only for this platform; keep the operator's other tools.
    tools = cfg.get('platform_toolsets', {}).get('botsesh', [])
    if 'botsesh' not in tools:
        set_config_value('platform_toolsets.botsesh', json.dumps([*tools, 'botsesh']))


def handle(args):
    from hermes_cli.auth_constants import AuthError
    try:
        client = Client()
        oauth = OAuthClient(client.store)
        if args.botsesh_action == 'connect':
            binding = oauth.connect(origin=args.dev_origin, open_browser=not args.no_browser, timeout=args.timeout, room_hint=args.room, name=args.name)
            configure(binding)
            print(f"Connected to {binding['name']} as @{binding['display_name']}. This exact name is addressable while the gateway runs.")
            print('This room’s members may address Hermes. Your existing model account and tool approvals apply.')
            if not args.no_start:
                binary = shutil.which('hermes')
                if binary:
                    result = subprocess.run([binary, 'gateway', 'ensure', '--json', '--timeout', '30'],
                                            capture_output=True, text=True, timeout=40)
                    if result.returncode == 0:
                        print('Hermes gateway runtime is ready. Send a question mentioning ' + binding['display_name'] + '.')
                        print('If this gateway was already running before the plugin was installed, restart it normally once to load the new channel.')
                    else:
                        print('Room connected; gateway is not ready. Check `hermes gateway status`, then run `hermes gateway run`.')
                else:
                    print('Start your gateway with `hermes gateway run`.')
            return
        rooms = client.store.read()['rooms']
        if args.botsesh_action == 'status':
            report = []
            if not rooms and not getattr(args, 'json', False):
                print('No BotSesh rooms. Run `hermes botsesh connect`.')
            for room_id, binding in rooms.items():
                state = binding.get('state', 'unknown')
                if binding.get('active'):
                    try:
                        client.enrolled(room_id, 'verify')
                        state = 'connected'
                    except BotSeshError as e:
                        state = e.code
                h = binding.get('health', {})
                gateway = h.get('state', 'not_observed')
                if time.time() - h.get('last_check', 0) > 90:
                    gateway = 'stale_or_offline'
                rows = binding.get('processing_receipts', {}).values()
                backlog = sum(row['state'] not in ('replied', 'failed') for row in rows)
                if state == 'connected' and binding.get('transport') == 'oauth':
                    try:
                        backlog = client.enrolled(room_id, 'check').get('backlog', backlog)
                    except BotSeshError as e:
                        state = e.code
                value = {'room_id': room_id, 'name': binding['name'], 'access': state, 'gateway': gateway,
                         'last_event': h.get('last_event'), 'last_check': h.get('last_check'), 'last_reply': h.get('last_reply'),
                         'backlog': backlog, 'failure_notices_pending': len(binding.get('failure_outbox', {})),
                         'reply_pending': bool(binding.get('pending_final')), 'error': h.get('error')}
                report.append(value)
                if not getattr(args, 'json', False):
                    stamp = lambda at: datetime.fromtimestamp(at, timezone.utc).isoformat(timespec='seconds') if at else 'never'
                    print(f"{binding['name']} · {room_id} · access={state} · gateway={gateway} · backlog={backlog}")
                    print(f"  last event={stamp(value['last_event'])} · last reply={stamp(value['last_reply'])} · pending notices={value['failure_notices_pending']} · pending reply={value['reply_pending']}")
            if getattr(args, 'json', False):
                print(json.dumps({'rooms': report}))
            else:
                print('Access is not execution. Stale/offline means no recent local polling receipt; check `hermes gateway status`.')
            return
        eligible = [room_id for room_id, room in rooms.items() if room.get('transport') == 'oauth' and room.get('state') != 'revoked']
        room_id = args.room_id
        if not room_id:
            grants = {rooms[r]['oauth_id'] for r in eligible}
            if len(grants) != 1:
                raise BotSeshError('choose_room_id_from_hermes_botsesh_status')
            room_id = next((rid for rid in eligible if not rooms[rid].get('oauth_root')), eligible[0])
        oauth.disconnect(room_id)
        from hermes_cli.config import load_config, set_config_value
        cfg = load_config()
        allowed = cfg.get('platforms', {}).get('botsesh', {}).get('extra', {}).get('group_allowed_chats', [])
        grant_id = rooms[room_id]['oauth_id']
        revoked_rooms = {rid for rid, room in rooms.items() if room.get('oauth_id') == grant_id}
        set_config_value('platforms.botsesh.extra.group_allowed_chats', json.dumps([r for r in allowed if r not in revoked_rooms]))
        print('Disconnected this OAuth connection and its consented DMs. BotSesh revocation verified; local tokens removed. Messages and attribution remain.')
    except KeyboardInterrupt:
        raise SystemExit('Connection cancelled; no new room connected.') from None
    except (BotSeshError, StorageError, AuthError) as error:
        raise SystemExit(f'BotSesh: {getattr(error, "code", "private_storage_error")}. Run `hermes botsesh status` to check existing connections.') from None


def register_cli(ctx):
    ctx.register_cli_command('botsesh', 'Connect Hermes to a BotSesh room', setup, handle)
