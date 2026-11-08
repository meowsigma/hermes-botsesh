"""The declared public CID needs no user export or profile credential write."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_plugin
from hermes_botsesh_test import oauth
from hermes_cli.plugin_oauth import _declared_clients, _pkce_config


class PublicClientTests(unittest.TestCase):
    def test_manifest_default_and_hint_need_no_environment_or_config_write(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'HERMES_HOME': temporary}, clear=True):
            client = _declared_clients(test_plugin.ROOT)[0]
            self.assertEqual(_pkce_config(client, 'botsesh:rooms').client_id, oauth.CLIENT_ID)
            guide = oauth.sign_in_instructions()
            self.assertIn('hermes auth add botsesh:rooms', guide)
            self.assertNotIn('export ', guide)
            self.assertFalse((Path(temporary)/'.env').exists())
