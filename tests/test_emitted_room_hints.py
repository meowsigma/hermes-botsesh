"""Parse actual emitted room hints and lifecycle recipes without executing them."""
import argparse
import contextlib
import io
from pathlib import Path
import re
import shlex
import tempfile
import unittest
from unittest.mock import Mock

from parse_copied_connect import package  # Registers the isolated plugin package.
from hyphen_botsesh import cli, ops_status, room_lifecycle
from hyphen_botsesh.storage import Store

ROOMS = ('-abc_DEF0123456789-abc_DEF01234567', '--no-browser', 'ordinary_room_123')


def parse_hint(command):
    argv = shlex.split(command)
    if argv[:3] == ['hermes', '-p', 'PROFILE']:
        # Core consumes the documented profile prefix before plugin dispatch.
        del argv[1:3]
    if argv[:2] != ['hermes', 'botsesh']:
        raise AssertionError(command)
    parser = argparse.ArgumentParser()
    cli.setup(parser)
    with contextlib.redirect_stderr(io.StringIO()):
        return parser.parse_args(argv[2:])


class EmittedRoomHints(unittest.TestCase):
    def test_dead_room_cleanup_hint_parses_from_json_and_printed_status(self):
        for room in ROOMS:
            with self.subTest(room=room):
                client = Mock()
                client.store.read.return_value = {'rooms': {
                    room: {'active': False, 'state': 'deleted'}
                }}
                report = ops_status.status_report(client, {}, runtime={
                    'running': False, 'channel': 'down', 'profile': 'fixture'
                })
                hint = report['rooms'][0]['forget_hint']
                args = parse_hint(hint)
                self.assertEqual((args.botsesh_action, args.room, args.dead), ('forget', room, False))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ops_status.print_status(report)
                printed = next(line.split(': ', 1)[1] for line in output.getvalue().splitlines()
                               if 'Local cleanup (does not revoke access): ' in line)
                self.assertEqual(parse_hint(printed).room, room)
                client.enrolled.assert_not_called()

    def test_creation_credential_recovery_hint_parses_with_show_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'plugin')
            for room in ROOMS:
                with self.subTest(room=room):
                    result = room_lifecycle.creation_result(store, {
                        'room_id': room, 'share_link': 'https://botsesh.com/b/' + room
                    })
                    args = parse_hint(result['credential_recovery'])
                    self.assertEqual((args.botsesh_action, args.room, args.show_password),
                                     ('room-info', room, True))

    def test_printed_home_repair_hint_parses_exact_option_value(self):
        for room in ROOMS:
            with self.subTest(room=room):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ops_status.print_home({'room_id': None, 'state': 'not_set',
                                           'live': False, 'candidates': [room]})
                hints = [line.split(': ', 1)[1] for line in output.getvalue().splitlines()
                         if 'Set it to a live room: ' in line]
                self.assertEqual(len(hints), 1)
                args = parse_hint(hints[0])
                self.assertEqual((args.botsesh_action, args.set_home), ('status', room))

    def test_documented_room_recipes_parse_exact_selectors(self):
        docs = (Path(__file__).resolve().parents[1] / 'docs/ROOM-LIFECYCLE.md').read_text()
        commands = [line for line in docs.splitlines()
                    if line.startswith('hermes botsesh ') and 'ROOM_ID' in line]
        self.assertGreaterEqual(len(commands), 8)
        for room in ROOMS:
            for command in commands:
                with self.subTest(room=room, command=command):
                    args = parse_hint(command.replace('ROOM_ID', room))
                    selector = (getattr(args, 'room', None) or getattr(args, 'room_id', None)
                                or getattr(args, 'set_home', None))
                    self.assertEqual(selector, room)

    def test_prose_room_recipes_parse_exact_selectors(self):
        root = Path(__file__).resolve().parents[1]
        recipe = re.compile(
            r'hermes (?:-p PROFILE )?botsesh '
            r'(?:disconnect|rotate-password|room-info|status) '
            r'[^`\n]*?\b(?:LIVE_)?ROOM_ID\b(?: --show-password)?'
        )
        for path, minimum in [('README.md', 5), ('docs/OPERATIONS.md', 1),
                              ('after-install.md', 1), ('skills/botsesh/SKILL.md', 1)]:
            commands = recipe.findall((root / path).read_text())
            self.assertGreaterEqual(len(commands), minimum, path)
            for room in ROOMS:
                for command in commands:
                    with self.subTest(path=path, room=room, command=command):
                        args = parse_hint(command.replace('LIVE_ROOM_ID', room)
                                          .replace('ROOM_ID', room))
                        selector = (getattr(args, 'room', None) or getattr(args, 'room_id', None)
                                    or getattr(args, 'set_home', None))
                        self.assertEqual(selector, room)
                        if args.botsesh_action == 'room-info':
                            self.assertTrue(args.show_password)


if __name__ == '__main__':
    unittest.main()
