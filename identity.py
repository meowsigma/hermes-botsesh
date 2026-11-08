"""Operator-only profile selection and gateway seat lifecycle (no model routing)."""
from __future__ import annotations
import contextlib
import json
from pathlib import Path
import re
import sys
import uuid
from .client import BotSeshError
from .storage import Store


def current_profile():
    from hermes_cli.config import get_hermes_home
    home = Path(get_hermes_home())
    return home.name if home.parent.name == 'profiles' else 'default'


def profile_root():
    from hermes_cli.config import get_hermes_home
    home = Path(get_hermes_home())
    return home.parent.parent if home.parent.name == 'profiles' else home


def profile_dir(name):
    return profile_root() if name == 'default' else profile_root() / 'profiles' / name


def profile_names():
    # Documented profiles/<name> layout and identity-file presence contract.
    # Never open credentials/configs while enumerating profile names.
    parent = profile_root() / 'profiles'
    identities = ('config.yaml', '.env', 'SOUL.md', 'profile.yaml', 'auth.json', 'state.db')
    names = [p.name for p in parent.iterdir()
             if p.is_dir() and not p.is_symlink()
             and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', p.name)
             and any((p / name).is_file() for name in identities)] if parent.is_dir() else []
    return ['default', *sorted(set(names) - {'default'})]


def explicit_native_profile():
    # Hermes documents -p/--profile and leaves argv intact after pre-parsing.
    # Do not import/read the CLI main module or its private parser state.
    for arg in sys.argv[1:]:
        if arg == '--':
            break
        if (arg in ('-p', '--profile') or arg.startswith('--profile=')
                or arg.startswith('-p') and len(arg) > 2):
            return current_profile()
    return None


def persona_name():
    from hermes_cli.config import get_hermes_home
    path = Path(get_hermes_home()) / 'profile.yaml'
    if not path.is_file() or path.is_symlink():
        return None
    try:
        # Hermes ships ruamel.yaml on Python 3.14 and PyYAML on older
        # interpreters. Use the third-party safe parser, not core profile code.
        try:
            from ruamel.yaml import YAML
            parse = YAML(typ='safe', pure=True).load
        except ImportError:
            from yaml import safe_load
            parse = safe_load
        value = parse(path.read_text(encoding='utf-8'))
        return value.get('display_name') if isinstance(value, dict) else None
    except Exception:
        return None


def choose_profile(explicit=None, *, interactive=False):
    from hermes_cli.config import load_config
    extra = load_config().get('platforms', {}).get('botsesh', {}).get('extra', {})
    saved = extra.get('profile')
    if explicit is None:
        explicit = explicit_native_profile()
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
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    # Preserve arbitrary HERMES_HOME installs when staying in their active home.
    if name not in profile_names():
        raise BotSeshError('unknown_hermes_profile')
    home = Path(get_hermes_home()) if name == current_profile() else profile_dir(name)
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
    from hermes_cli.config import load_config
    from .oauth import preferred_name
    extra = load_config().get('platforms', {}).get('botsesh', {}).get('extra', {})
    persona = persona_name()
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
