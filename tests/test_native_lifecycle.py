"""Plugin connection never owns restart; native core owns lifecycle."""
import argparse
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import test_plugin
from hermes_botsesh_test import cli, ops_status


class NativeLifecycleTests(unittest.TestCase):
    def test_retired_command_has_no_parser_or_side_effects(self):
        parser = argparse.ArgumentParser()
        cli.setup(parser)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parser.parse_args(['restart-gateway', '--checkpoint'])
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn('restart-gateway', parser.format_help())

    def test_running_channel_needs_no_start_or_restart(self):
        out = io.StringIO()
        with patch.object(ops_status, 'gateway_snapshot', return_value={'running': True, 'channel': 'up'}), \
             patch.object(cli.subprocess, 'run') as run, contextlib.redirect_stdout(out):
            cli.connect_runtime('night')
        run.assert_not_called()
        self.assertIn('already running', out.getvalue())

    def test_missing_channel_prints_one_selected_profile_hint_without_restarting(self):
        out = io.StringIO()
        with patch.object(ops_status, 'gateway_snapshot', return_value={'running': True, 'channel': 'down'}), \
             patch.object(cli.shutil, 'which', return_value='/fixture/hermes'), \
             patch.object(cli.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
             contextlib.redirect_stdout(out):
            cli.connect_runtime('night')
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['/fixture/hermes', '-p', 'night', 'gateway', 'ensure', '--json', '--timeout', '30'])
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertIn('channel not ready', out.getvalue())
        self.assertIn('hermes -p night gateway restart --detach', out.getvalue())
        self.assertIn('hermes -p night botsesh status', out.getvalue())
        self.assertNotIn('--yes', out.getvalue())

    def test_successful_start_is_confirmed_from_channel_not_exit_code(self):
        with patch.object(ops_status, 'gateway_snapshot', side_effect=[
                {'running': False, 'channel': 'down'}, {'running': True, 'channel': 'up'}]), \
             patch.object(cli.shutil, 'which', return_value='/fixture/hermes'), \
             patch.object(cli.subprocess, 'run', return_value=Mock(returncode=0)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            cli.connect_runtime('night')
        self.assertIn('are ready', out.getvalue())
        self.assertNotIn('restart', out.getvalue())

    def test_unknown_runtime_does_not_claim_ready(self):
        with patch.object(ops_status, 'gateway_snapshot', return_value={'running': None, 'channel': 'unknown'}), \
             patch.object(cli.shutil, 'which', return_value=None), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            cli.connect_runtime('night')
        self.assertIn('not ready', out.getvalue())
        self.assertNotIn('are ready', out.getvalue())

    def test_connect_configuration_keeps_prior_rooms_without_checkpoint_writes(self):
        from hermes_cli.config import load_config_readonly
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'HERMES_HOME': home}), \
             patch.object(cli, 'Client') as client, contextlib.redirect_stdout(io.StringIO()):
            cli.configure({'room_id': 'one', 'name': 'One'})
            cli.configure({'room_id': 'two', 'name': 'Two'})
            config = load_config_readonly()
        self.assertEqual(config['platforms']['botsesh']['extra']['group_allowed_chats'], ['one', 'two'])
        self.assertEqual(config['platforms']['botsesh']['home_channel']['chat_id'], 'one')
        client.assert_not_called()
