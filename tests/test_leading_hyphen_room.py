"""Leading-hyphen selectors survive copied commands and profile re-entry."""
import argparse
import contextlib
import io
from pathlib import Path
import shlex
import unittest
from unittest.mock import Mock, patch

from parse_copied_connect import parse
from hyphen_botsesh import cli

ROOM = '-abc_DEF0123456789-abc_DEF01234567'


class LeadingHyphenRoom(unittest.TestCase):
    def test_connect_equals_preserves_exact_id(self):
        for room in (ROOM, '--no-browser', 'ordinary_room_123'):
            with self.subTest(room=room):
                args = parse('hermes botsesh connect ' + shlex.quote('--room=' + room))
                self.assertEqual(args.room, room)
                self.assertFalse(args.no_browser)

    def test_separate_negative_selector_reproduces_argparse_refusal(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parse('hermes botsesh connect --room ' + shlex.quote(ROOM))
        self.assertEqual(error.exception.code, 2)

    def test_connect_help_teaches_safe_equals_form(self):
        parser = argparse.ArgumentParser()
        cli.setup(parser)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            parser.parse_args(['connect', '--help'])
        self.assertIn('--room=ROOM_ID', output.getvalue())

    def test_profile_child_argv_parses_with_same_exact_room(self):
        args = parse('hermes botsesh connect --no-start --room=' + ROOM)
        with patch.object(cli.shutil, 'which', return_value='/native/hermes'), \
             patch.object(cli.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            cli.connect_in_profile(args, 'synthetic-profile', Path('/synthetic/profile'))
        argv = run.call_args.args[0]
        self.assertIn('--room=' + ROOM, argv)
        self.assertNotIn('--room', argv)
        self.assertNotIn('shell', run.call_args.kwargs)
        parser = argparse.ArgumentParser()
        cli.setup(parser)
        self.assertEqual(parser.parse_args(argv[4:]).room, ROOM)


if __name__ == '__main__':
    unittest.main()
