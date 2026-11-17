"""Live gateway command discovery, independent of BotSesh transport.

Discovery is called in the running gateway after plugin registration, not by a
web request and never as an authorization decision. Values remain exact; labels
are bounded for the v1 UI transport.
"""
from __future__ import annotations

import re

_NAME = re.compile(r'^[a-z0-9_-]{1,64}$')

# Manifest v1.1 `kind` (protocol v1 §4). `info` = read-only output that the palette may run on
# Enter; `picker` = opens a native choice card in the DM; everything else is an `action` that only
# fills the composer. Read-only means: no session, config or tool side effect with no arguments.
_INFO = frozenset({'status', 'usage', 'help', 'commands', 'context', 'whoami', 'profile', 'insights',
                   'platforms', 'version', 'history', 'agents', 'sessions', 'egress'})
_PICKER = frozenset({'model', 'resume'})


def _kind(command):
    if command.name in _PICKER:
        return 'picker'
    if command.name in _INFO:
        return 'info'
    return 'action'


def _v11(row, command=None, *, category=None, kind=None, subcommands=()):
    """Additive v1.1 fields. Older servers get the v1.0 shape through `v10()` on refusal."""
    if command is not None:
        category = getattr(command, 'category', None)
        kind = _kind(command)
        subcommands = getattr(command, 'subcommands', ()) or ()
    if isinstance(category, str) and category.strip():
        row['category'] = _label(category.strip(), 40)
    if kind in ('info', 'action', 'picker'):
        row['kind'] = kind
    chips = []
    for value in subcommands:
        value = str(value)
        if value and len(value) <= 32 and value not in chips and not value.startswith('--'):
            chips.append(value)
    if chips:
        row['subcommands'] = chips[:32]
    return row


def v10(commands):
    return [{k: v for k, v in row.items() if k in ('name', 'description', 'args', 'options')} for row in commands]


def _label(value, limit=160):
    # BotSesh's bound uses JavaScript UTF-16 units. Avoid splitting a surrogate.
    return str(value or '').encode('utf-16-le')[:limit * 2].decode('utf-16-le', errors='ignore')


def build_manifest():
    from hermes_cli.commands import SUBCOMMANDS
    from hermes_cli.commands_platforms import _gateway_available_commands
    from hermes_cli.config import load_config_readonly
    from hermes_cli.plugins import get_plugin_commands

    commands = {}

    def add(name, description, args='', options=(), command=None, **extra):
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in commands:
            return
        values = []
        seen = set()
        for value in options:
            value = str(value)
            if not value or len(value.encode('utf-16-le')) > 256 or value in seen:
                continue
            seen.add(value)
            values.append({'id': value, 'label': _label(value)})
        # No silent partial option catalog; the native command's text help stays
        # available if a dynamic command has more than the UI's 64 finite options.
        row = {'name': name, 'description': _label(description) or 'Run /' + name}
        if args:
            row['args'] = _label(args)
        if 0 < len(values) <= 64:
            row['options'] = values
        commands[name] = _v11(row, command, **extra)

    for command in _gateway_available_commands():
        # /start acknowledges platform bot pings; /topic manages Telegram's
        # native DM topics. Neither is an actionable BotSesh DM menu entry.
        if command.name in ('start', 'topic'):
            continue
        options = SUBCOMMANDS.get('/' + command.name, ())
        description = command.describe()
        add(command.name, description, command.args_hint, options, command)
        for alias in command.aliases:
            add(alias, description, command.args_hint, options, command)

    for name, command in get_plugin_commands().items():
        add(name, command.get('description') or 'Plugin command', command.get('args_hint') or '',
            category='Plugins', kind='action')

    config = load_config_readonly()
    for name, command in (config.get('quick_commands') or {}).items():
        if isinstance(command, dict) and command.get('type') in ('alias', 'exec'):
            # Do not publish shell command bodies or alias targets as labels.
            add(name, command.get('description') or 'Configured quick command', category='Quick commands', kind='action')

    # BotSesh overrides native /name (conversation naming) with the exact room
    # participant label; it never changes a Hermes session title.
    commands['name'] = {'name': 'name', 'description': 'Show or rename this agent’s addressable room name', 'args': '[new name]',
                        'category': 'BotSesh', 'kind': 'action'}
    # Canonical built-ins precede aliases/plugins/quick commands without name
    # repair, ellipsis, or pretending an incomplete manifest is complete.
    if len(commands) > 128:
        raise ValueError('DM command catalog exceeds v1 limit; native /commands remains available')
    return list(commands.values())
