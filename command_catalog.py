"""Live gateway command discovery, independent of BotSesh transport.

Discovery is called in the running gateway after plugin registration, not by a
web request and never as an authorization decision. Values remain exact; labels
are bounded for the v1 UI transport.
"""
from __future__ import annotations

import re

_NAME = re.compile(r'^[a-z0-9_-]{1,64}$')


def _label(value, limit=160):
    # BotSesh's bound uses JavaScript UTF-16 units. Avoid splitting a surrogate.
    return str(value or '').encode('utf-16-le')[:limit * 2].decode('utf-16-le', errors='ignore')


def build_manifest():
    from hermes_cli.commands import SUBCOMMANDS
    from hermes_cli.commands_platforms import _gateway_available_commands
    from hermes_cli.config import load_config_readonly
    from hermes_cli.plugins import get_plugin_commands

    commands = {}

    def add(name, description, args='', options=()):
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
        commands[name] = row

    for command in _gateway_available_commands():
        # /start acknowledges platform bot pings; /topic manages Telegram's
        # native DM topics. Neither is an actionable BotSesh DM menu entry.
        if command.name in ('start', 'topic'):
            continue
        options = SUBCOMMANDS.get('/' + command.name, ())
        description = command.describe()
        add(command.name, description, command.args_hint, options)
        for alias in command.aliases:
            add(alias, description, command.args_hint, options)

    for name, command in get_plugin_commands().items():
        add(name, command.get('description') or 'Plugin command', command.get('args_hint') or '')

    config = load_config_readonly()
    for name, command in (config.get('quick_commands') or {}).items():
        if isinstance(command, dict) and command.get('type') in ('alias', 'exec'):
            # Do not publish shell command bodies or alias targets as labels.
            add(name, command.get('description') or 'Configured quick command')

    # Canonical built-ins precede aliases/plugins/quick commands without name
    # repair, ellipsis, or pretending an incomplete manifest is complete.
    if len(commands) > 128:
        raise ValueError('DM command catalog exceeds v1 limit; native /commands remains available')
    return list(commands.values())
