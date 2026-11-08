"""Operator-only profile selection and gateway seat lifecycle (no model routing)."""
from __future__ import annotations
import contextlib
import json
import re
import sys
import uuid
from pathlib import Path
from .client import BotSeshError
from .storage import Store


def current_profile():
    from hermes_cli.profiles import current_profile_name
    value = current_profile_name() or 'default'
    return value if value != 'custom' else 'default'


def profile_names():
    from hermes_cli.profiles import list_profiles
    return list(dict.fromkeys(['default', *(p.name for p in list_profiles())]))


def choose_profile(explicit=None, *, interactive=False):
    from hermes_cli.config import load_config
    extra = load_config().get('platforms', {}).get('botsesh', {}).get('extra', {})
    saved = extra.get('profile')
    # Hermes pre-parses --profile even after plugin subcommands. Do not import
    # main here (startup side effects); honor its explicit choice when loaded.
    main = sys.modules.get('hermes_cli.main')
    if explicit is None and callable(getattr(main, 'explicit_cli_profile', None)):
        explicit = main.explicit_cli_profile()
    names = profile_names()
    value = explicit if explicit is not None else saved
    if value is None and interactive and sys.stdin.isatty():
        print('Choose the Hermes profile for this seat (saved once):')
        for index, name in enumerate(names, 1):
            print(f'  {index}. {name}' + (' [default; Enter]' if name == 'default' else ''))
        answer = input('Profile [default]: ').strip()
        value = names[int(answer)-1] if answer.isdigit() and 1 <= int(answer) <= len(names) else answer or 'default'
    value = 'default' if value is None else value
    if not isinstance(value, str) or value not in names or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value):
        raise BotSeshError('unknown_hermes_profile')
    return value


@contextlib.contextmanager
def profile_scope(name):
    """Select the real native home, including credentials/config/tools isolation."""
    from hermes_cli.config import get_hermes_home
    from hermes_cli.profiles import get_profile_dir
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    # Preserve arbitrary HERMES_HOME installs when staying in their active home.
    home = Path(get_hermes_home()) if name == current_profile() else Path(get_profile_dir(name))
    if not home.is_dir() or home.is_symlink():
        raise BotSeshError('unknown_hermes_profile')
    token = set_hermes_home_override(home)
    try:
        yield home
    finally:
        reset_hermes_home_override(token)


def save_profile(name):
    save_identity_config(profile=name)


def save_identity_config(**fields):
    from hermes_cli.config import load_config, set_config_value
    extra = load_config().get('platforms', {}).get('botsesh', {}).get('extra', {})
    # Native config set coerces open scalar leaves (null/true/numbers), while
    # quoted scalars may be saved literally. A typed mapping preserves strings
    # and all unrelated existing platform extras on both old and current cores.
    set_config_value('platforms.botsesh.extra', json.dumps({**extra, **fields}))


def require_profile_install(home):
    from hermes_cli.config import load_config
    if not (home / 'plugins' / 'botsesh' / 'plugin.yaml').is_file():
        raise BotSeshError('install_botsesh_plugin_in_selected_profile_first')
    if 'botsesh' in load_config().get('plugins', {}).get('disabled', []):
        raise BotSeshError('enable_botsesh_plugin_in_selected_profile_first')


def gateway_install_id():
    from hermes_cli.config import get_hermes_home
    # Native root helpers intentionally collapse *all* paths beneath ~/.hermes,
    # including an isolated scratch HERMES_HOME. Derive only the documented
    # immediate profiles/<name> layout; never escape a custom/fixture home.
    home = Path(get_hermes_home())
    root = home.parent.parent if home.parent.name == 'profiles' else home
    store = Store(root / 'plugin-data' / 'botsesh-identity')
    def assign(data):
        if 'gateway_install_id' not in data:
            data['gateway_install_id'] = uuid.uuid4().hex
    value = store.update(assign)['gateway_install_id']
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise BotSeshError('invalid_gateway_install_identity')
    return value


def configured_name(profile, explicit=None):
    from hermes_cli.config import load_config, get_hermes_home
    from .oauth import preferred_name
    extra = load_config().get('platforms', {}).get('botsesh', {}).get('extra', {})
    try:
        from hermes_cli.profiles import read_profile_meta
        persona = read_profile_meta(get_hermes_home()).get('display_name')
    except ImportError:
        persona = None
    return preferred_name(explicit if explicit is not None else extra.get('display_name') or persona or profile)


def root_binding(store, room_id=None):
    rooms = store.read()['rooms']
    eligible = [b for b in rooms.values() if b.get('transport') == 'oauth' and b.get('active') and not b.get('oauth_root')]
    if room_id:
        eligible = [b for b in eligible if b['room_id'] == room_id]
    if len(eligible) != 1:
        raise BotSeshError('choose_room_with_--room')
    return eligible[0]


def handle_identity(args, client, oauth):
    binding = root_binding(client.store, getattr(args, 'room', None))
    if args.botsesh_action == 'rename':
        result = oauth.call(binding, 'name', display_name=args.name)
        save_identity_config(display_name=result['display_name'])
        print(f"Renamed {binding['room_id']} to @{result['display_name']}.")
        return
    report = oauth.rpc(binding, 'hermes_seats', {'room_id': binding['room_id']})
    seats = report.get('seats')
    if not isinstance(seats, list):
        raise BotSeshError('invalid_seat_response')
    for seat in seats:
        print(f"{seat['profile']} · {seat['room_id']} · @{seat['display_name']} · {seat['membership_id']} · {'ghost' if seat['ghost'] else 'current'}")
    print('Only this gateway + BotSesh account is listed. Older untracked seats require People → Remove agent access.')
    ghosts = [s['membership_id'] for s in seats if s.get('ghost') is True]
    if not args.prune or not ghosts:
        return
    confirmed = args.yes
    if not confirmed and sys.stdin.isatty():
        confirmed = input(f'Revoke {len(ghosts)} stale seat(s) from this gateway? [y/N]: ').strip().lower() in ('y', 'yes')
    if not confirmed:
        raise BotSeshError('seat_prune_requires_confirmation_use_--yes')
    result = oauth.rpc(binding, 'hermes_seats', {'room_id': binding['room_id'], 'prune': ghosts, 'confirm': True})
    verified = oauth.rpc(binding, 'hermes_seats', {'room_id': binding['room_id']})
    if set(result.get('pruned', [])) != set(ghosts) or any(s['membership_id'] in ghosts for s in verified.get('seats', [])):
        raise BotSeshError('seat_prune_not_confirmed')
    print(f'Revoked {len(ghosts)} stale seat(s); messages and attribution remain.')
