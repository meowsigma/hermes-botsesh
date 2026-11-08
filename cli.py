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
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = (
        'Command groups:\n'
        '  Connection & identity: connect, disconnect, rename, seats\n'
        '  Rooms & access: create-room, rooms, room-info, invite, invites, revoke-invite, rotate-password, forget\n'
        '  Operations: send, status, restart-gateway\n\n'
        'Commands use the saved profile; hermes -p PROFILE botsesh COMMAND overrides it.\n'
        'Use COMMAND --help for room selectors and explicit mutation options.'
    )
    sub = parser.add_subparsers(dest='botsesh_action', required=True, title='commands', metavar='COMMAND')
    connect = sub.add_parser('connect', help='Sign in in your browser and approve one room')
    connect.add_argument('--no-browser', action='store_true', help='Print the browser URL (SSH loopback forwarding supported)')
    connect.add_argument('--paste-redirect', action='store_true', help='Phone fallback: paste the redirect URL in a hidden terminal prompt; type url to re-print')
    connect.add_argument('--set-home', action='store_true', help='Set home_channel to this newly connected, verified room')
    connect.add_argument('--no-start', action='store_true', help='Connect without ensuring a local gateway runtime')
    connect.add_argument('--room', help='Public room ID hint; access still requires the signed-in host’s Allow')
    connect.add_argument('--name', help='Addressable agent name (defaults to the selected profile name)')
    connect.add_argument('--profile', help='Hermes profile for this seat; saved after connection')
    connect.add_argument('--timeout', type=int, default=300, choices=range(15, 601), metavar='SECONDS')
    connect.add_argument('--dev-origin', default='https://botsesh.com', help=argparse.SUPPRESS)
    status = sub.add_parser('status', help='Check access, live gateway/channel, home_channel, replies and backlog')
    status.add_argument('--json', action='store_true', help='Content-free room health for monitoring')
    status.add_argument('--set-home', metavar='ROOM_ID', help='Explicitly replace home_channel with a live, allowed room')
    restart = sub.add_parser('restart-gateway', help='Save a recovery checkpoint or explain the manual restart fallback')
    restart.add_argument('--when-idle', action='store_true', help='Refuse automatic restart until Hermes exposes a complete public idle gate; print manual guidance')
    restart.add_argument('--max-wait', type=int, default=300, choices=range(1, 3601), metavar='SECONDS')
    restart.add_argument('--health-timeout', type=int, default=60, choices=range(1, 601), metavar='SECONDS')
    restart.add_argument('--restart-timeout', type=int, default=120, choices=range(1, 601), metavar='SECONDS')
    restart.add_argument('--checkpoint', action='store_true', help='Before updating: save private BotSesh config/plugin-source rollback point; does not restart')
    disconnect = sub.add_parser('disconnect', help='Revoke one room connection on BotSesh')
    disconnect.add_argument('room_id', nargs='?', help='Room ID; optional when there is only one OAuth connection')
    for command in ('rename', 'seats'):
        item = sub.add_parser(command, help='Rename this seat' if command == 'rename' else 'List this gateway’s seats; optionally revoke its stale duplicates')
        item.add_argument('--profile', help='Hermes profile (otherwise use the saved choice)')
        item.add_argument('--room', help='Room ID; required if several rooms are connected')
        if command == 'rename':
            item.add_argument('name', help='Exact addressable name')
        else:
            item.add_argument('--prune', action='store_true', help='Revoke only verified ghosts belonging to this gateway/account')
            item.add_argument('--yes', action='store_true', help='Explicitly confirm ghost revocation without a prompt')
    from .room_lifecycle import setup as setup_lifecycle
    setup_lifecycle(sub)


def configure(binding):
    from hermes_cli.config import load_config, set_config_value
    from .ops_restart import checkpoint
    cfg = load_config()
    checkpoint(Client(), cfg)
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
    tools = cfg.get('platform_toolsets', {}).get('botsesh', [])
    if 'botsesh' not in tools:
        set_config_value('platform_toolsets.botsesh', json.dumps([*tools, 'botsesh']))
    enabled = cfg.get('plugins', {}).get('enabled', [])
    if 'botsesh' not in enabled:
        set_config_value('plugins.enabled', json.dumps([*enabled, 'botsesh']))


def restart_guidance(profile):
    import shlex
    prefix = shlex.join(['hermes', '-p', profile])
    print('This is not an automatic idle restart: public Hermes status omits asynchronous delegations and a complete queued-admission gate.')
    print('No restart or auto-revert was attempted. In your own terminal, quiesce new input and finish active turns, delegations, queued requests and pending sends, then run:')
    print(f'  {prefix} gateway restart --detach')
    print(f'  {prefix} gateway status')
    print(f'  {prefix} botsesh status')
    print('Core refuses live work noninteractively; do not add --yes, --interrupt or --all. Check the selected profile’s channel and rooms afterward.')


def connect_runtime(profile):
    from .ops_status import gateway_snapshot
    def ready():
        live = gateway_snapshot()
        return live.get('running') is True and live.get('channel') == 'up'
    if ready():
        print('Hermes gateway and this profile’s BotSesh channel are already running.')
        return
    binary = shutil.which('hermes')
    if binary:
        try:
            subprocess.run([binary, '-p', profile, 'gateway', 'ensure', '--json', '--timeout', '30'],
                           capture_output=True, text=True, timeout=40)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if ready():
            print('Hermes gateway and this profile’s BotSesh channel are ready.')
            return
    print('Room connected; this profile’s BotSesh channel is not yet confirmed ready. Check the selected profile, not another gateway:')
    restart_guidance(profile)


def connect_in_profile(args, profile, home):
    """Re-enter native profile parsing before a second plugin registry is loaded."""
    import os
    binary = shutil.which('hermes')
    if not binary:
        raise BotSeshError('hermes_cli_unavailable')
    argv = [binary, '-p', profile, 'botsesh', 'connect']
    for flag in ('no_browser', 'paste_redirect', 'set_home', 'no_start'):
        if getattr(args, flag, False):
            argv.append('--' + flag.replace('_', '-'))
    for field in ('room', 'name', 'timeout', 'dev_origin'):
        value = getattr(args, field, None)
        if value is not None:
            argv.append('--' + field.replace('_', '-') + '=' + str(value))
    env = {key: value for key, value in os.environ.items()
           if key not in ('HERMES_PROFILE', 'HERMES_PROFILE_NAME')}
    env['HERMES_HOME'] = str(home)
    try:
        # Inherit the operator's terminal for native OAuth/approval prompts.
        result = subprocess.run(argv, env=env)
    except OSError:
        raise BotSeshError('hermes_cli_unavailable') from None
    if result.returncode:
        raise SystemExit(result.returncode)


def handle(args):
    from .identity import choose_profile, profile_scope, save_profile
    from hermes_cli.config import get_hermes_home
    starting_home = get_hermes_home()
    try:
        explicit = getattr(args, 'profile', None) if args.botsesh_action in ('connect', 'rename', 'seats') else None
        profile = choose_profile(explicit, interactive=args.botsesh_action == 'connect')
        with profile_scope(profile) as home:
            if args.botsesh_action == 'connect' and home != starting_home:
                from .identity import require_profile_install
                require_profile_install(home)
                connect_in_profile(args, profile, home)
            else:
                _handle_profile(args, profile)
        if args.botsesh_action == 'connect':
            save_profile(profile)
    except BotSeshError as error:
        raise SystemExit(f'BotSesh: {error.code}.') from None


def _handle_profile(args, profile):
    from hermes_cli.auth_constants import AuthError
    try:
        client = Client()
        from .room_lifecycle import ACTIONS, handle as handle_lifecycle
        if args.botsesh_action in ACTIONS:
            return handle_lifecycle(args, client)
        oauth = OAuthClient(client.store)
        if args.botsesh_action in ('rename', 'seats'):
            from .identity import handle_identity
            return handle_identity(args, client, oauth)
        if args.botsesh_action == 'connect':
            from .identity import configured_name, save_identity_config
            binding = oauth.connect(origin=args.dev_origin, open_browser=not args.no_browser, timeout=args.timeout,
                                    room_hint=args.room, name=configured_name(profile, args.name), profile=profile,
                                    **({'paste_redirect': True} if getattr(args, 'paste_redirect', False) else {}))
            configure(binding)
            save_identity_config(profile=profile, display_name=binding['display_name'])
            print(f"Connected to {binding['name']} as @{binding['display_name']}. This exact name is addressable while the gateway runs.")
            print(f'Hermes profile: {profile}. Credentials and room tools stay in that profile’s home.')
            try:
                seats = oauth.rpc(binding, 'hermes_seats', {'room_id': binding['room_id']}).get('seats', [])
                if any(s.get('ghost') for s in seats):
                    print('Stale seats detected. Review `hermes botsesh seats`, then `hermes botsesh seats --prune`.')
            except BotSeshError:
                print('Seat inventory unavailable; check `hermes botsesh seats` later.')
            print('This room’s members may address Hermes. Your existing model account and tool approvals apply.')
            from hermes_cli.config import load_config_readonly
            from .ops_status import status_report, print_home, set_home
            if getattr(args, 'set_home', False):
                set_home(client, load_config_readonly(), binding['room_id'])
            print_home(status_report(client, load_config_readonly())['home_channel'])
            if not args.no_start:
                connect_runtime(profile)
            return
        if args.botsesh_action == 'restart-gateway':
            from hermes_cli.config import load_config_readonly
            from .ops_restart import restart_gateway
            if not args.checkpoint:
                restart_guidance(profile)
                raise BotSeshError('safe_idle_restart_unavailable_no_restart')
            restart_gateway(client, load_config_readonly(), args)
            return
        if args.botsesh_action == 'status':
            from hermes_cli.config import load_config_readonly
            from .ops_status import status_report, print_status, set_home
            if isinstance(getattr(args, 'set_home', None), str):
                set_home(client, load_config_readonly(), args.set_home)
            report = status_report(client, load_config_readonly())
            if getattr(args, 'json', False):
                print(json.dumps(report))
            else:
                print_status(report)
            return
        rooms = client.store.read()['rooms']
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
