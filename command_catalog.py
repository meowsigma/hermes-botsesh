"""Plugin-owned DM shortcuts, not a claim to enumerate Hermes' live registry.

Core/plugin command discovery is not a public platform-plugin API. These common
commands are documented in Hermes' reference/slash-commands.md; /commands always
asks the native dispatcher for the current complete list. Configured quick
command labels are read through the public config API, never their shell bodies.
"""
from __future__ import annotations

import re

_NAME = re.compile(r'^[a-z0-9_-]{1,64}$')
_SHORTCUTS = (
    ('commands', 'Show the current native command list (including plugins)', ''),
    ('help', 'Show native Hermes help', ''),
    ('status', 'Show session, model, token and context info', ''),
    ('model', 'List models; switch using the native guarded text command', '[model] [--provider name]'),
    ('goal', 'Set or inspect this session’s standing goal', '[text | show | pause | resume | clear | status]'),
    ('new', 'Start a new session', '[name]'),
    ('reset', 'Start a new session', '[name]'),
    ('stop', 'Stop the current turn and background work', ''),
    ('approve', 'Approve the current native pending action', '[session|always]'),
    ('deny', 'Deny a native pending action', '[all] [reason]'),
    ('agents', 'Show active agents and running tasks', ''),
    ('queue', 'Queue a prompt or manage queued prompts', '[prompt | list | clear]'),
    ('steer', 'Steer the current turn', '<prompt>'),
    ('compress', 'Compress the conversation context', '[focus topic]'),
    ('context', 'Show detailed context usage', '[all]'),
    ('resume', 'Resume a saved session', '[name]'),
    ('sessions', 'Browse saved sessions', ''),
    ('sethome', 'Set this DM as the native home channel', ''),
    ('name', 'Show or rename this agent’s addressable room name', '[new name]'),
)


def _label(value, limit=160):
    return str(value or '').encode('utf-16-le')[:limit * 2].decode('utf-16-le', errors='ignore')


def build_manifest():
    from hermes_cli.config import load_config_readonly
    commands = {name: {'name': name, 'description': description, **({'args': args} if args else {})}
                for name, description, args in _SHORTCUTS}
    config = load_config_readonly()
    for name, command in (config.get('quick_commands') or {}).items():
        if (isinstance(name, str) and _NAME.fullmatch(name) and name not in commands
                and isinstance(command, dict) and command.get('type') in ('alias', 'exec')):
            commands[name] = {'name': name, 'description': _label(command.get('description')) or 'Configured quick command'}
    if len(commands) > 128:
        raise ValueError('DM shortcuts exceed v1 limit; native /commands remains available')
    return list(commands.values())
