"""Cross-profile connect uses a fresh native CLI, never two plugin registries."""
import argparse
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_plugin
from hermes_botsesh_test import cli, identity


class ProfileChild(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.home = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.target = self.home / 'profiles/goonbot'
        self.target.mkdir(parents=True)
        self.stack.enter_context(patch.dict(os.environ, {'HERMES_HOME': str(self.home)}))
        self.stack.enter_context(patch.object(identity, 'profile_names', return_value=['default', 'goonbot']))
        self.stack.enter_context(patch.object(identity, 'current_profile', return_value='default'))
        self.stack.enter_context(patch('hermes_cli.config.load_config', return_value={}))
        self.stack.enter_context(patch.object(identity, 'require_profile_install'))
        # The stock backport uses the native profile directory resolver.
        self.stack.enter_context(patch('hermes_cli.profiles.get_profile_dir', return_value=self.target))
        self.stack.enter_context(patch.object(cli.shutil, 'which', return_value='/native/hermes'))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        parser = argparse.ArgumentParser()
        cli.setup(parser)
        self.args = parser.parse_args(['connect', '--no-start', '--set-home', '--name=Scout; not a shell', '--room=-exact'])

    def test_prompt_runs_selected_profile_child_with_inherited_tty_and_fixed_argv(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', return_value='2') as prompt, \
             patch.object(cli, '_handle_profile') as local, patch.object(identity, 'save_profile') as save, \
             patch.object(cli.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            cli.handle(self.args)
        prompt.assert_called_once()
        local.assert_not_called()
        argv = run.call_args.args[0]
        self.assertEqual(argv[:5], ['/native/hermes', '-p', 'goonbot', 'botsesh', 'connect'])
        self.assertIn('--name=Scout; not a shell', argv)
        self.assertIn('--room=-exact', argv)
        self.assertIn('--set-home', argv)
        self.assertIn('--no-start', argv)
        for key in ('stdin', 'stdout', 'stderr', 'capture_output', 'shell'):
            self.assertNotIn(key, run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs['env']['HERMES_HOME'], str(self.target))
        save.assert_called_once_with('goonbot')

    def test_child_failure_is_propagated_without_saving_selection(self):
        with patch.object(identity, 'choose_profile', return_value='goonbot'), \
             patch.object(cli, '_handle_profile') as local, patch.object(identity, 'save_profile') as save, \
             patch.object(cli.subprocess, 'run', return_value=Mock(returncode=7)), self.assertRaises(SystemExit) as error:
            cli.handle(self.args)
        self.assertEqual(error.exception.code, 7)
        local.assert_not_called()
        save.assert_not_called()

    def test_same_active_profile_does_not_spawn_or_reprompt(self):
        with patch.object(identity, 'choose_profile', return_value='default'), \
             patch.object(cli, '_handle_profile') as local, patch.object(identity, 'save_profile'), \
             patch.object(cli.subprocess, 'run') as run:
            cli.handle(self.args)
        local.assert_called_once_with(self.args, 'default')
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
