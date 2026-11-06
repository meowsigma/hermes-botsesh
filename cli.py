"""Native `hermes botsesh` command. Registration is side-effect free."""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
from .client import Client, BotSeshError
from .storage import StorageError
from .oauth import OAuthClient


def setup(parser):
    sub = parser.add_subparsers(dest='botsesh_action', required=True)
    connect = sub.add_parser('connect', help='Sign in in your browser and approve one room')
    connect.add_argument('--no-browser', action='store_true', help='Print the browser URL (SSH loopback forwarding supported)')
    connect.add_argument('--no-start', action='store_true', help='Connect without ensuring a local gateway runtime')
    connect.add_argument('--room', help='Public room ID hint; access still requires the signed-in host’s Allow')
    connect.add_argument('--timeout', type=int, default=180, choices=range(15, 601), metavar='SECONDS')
    connect.add_argument('--dev-origin', default='https://botsesh.com', help=argparse.SUPPRESS)
    sub.add_parser('status', help='Check connected rooms without showing credentials')
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
            binding = oauth.connect(origin=args.dev_origin, open_browser=not args.no_browser, timeout=args.timeout, room_hint=args.room)
            configure(binding)
            print(f"Connected to {binding['name']}. Hermes will answer @mentions while its gateway runs.")
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
            if not rooms:
                print('No BotSesh rooms. Run `hermes botsesh connect`.')
            for room_id, binding in rooms.items():
                state = binding.get('state', 'unknown')
                if binding.get('active'):
                    try:
                        client.enrolled(room_id, 'verify')
                        state = 'connected'
                    except BotSeshError as e:
                        state = e.code
                print(f"{binding['name']} · {room_id} · {state}")
            print('Connected is access status, not proof of a running gateway. Use `hermes gateway status`.')
            return
        eligible = [room_id for room_id, room in rooms.items() if room.get('transport') == 'oauth' and room.get('state') != 'revoked']
        room_id = args.room_id
        if not room_id:
            if len(eligible) != 1:
                raise BotSeshError('choose_room_id_from_hermes_botsesh_status')
            room_id = eligible[0]
        oauth.disconnect(room_id)
        from hermes_cli.config import load_config, set_config_value
        cfg = load_config()
        allowed = cfg.get('platforms', {}).get('botsesh', {}).get('extra', {}).get('group_allowed_chats', [])
        set_config_value('platforms.botsesh.extra.group_allowed_chats', json.dumps([r for r in allowed if r != room_id]))
        print('Disconnected. BotSesh revocation verified; local tokens removed. Messages and attribution remain.')
    except KeyboardInterrupt:
        raise SystemExit('Connection cancelled; no new room connected.') from None
    except (BotSeshError, StorageError, AuthError) as error:
        raise SystemExit(f'BotSesh: {getattr(error, "code", "private_storage_error")}. Run `hermes botsesh status` to check existing connections.') from None


def register_cli(ctx):
    ctx.register_cli_command('botsesh', 'Connect Hermes to a BotSesh room', setup, handle)
