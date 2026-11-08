"""Public API migration regressions, isolated files only; no host profile reads."""
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from test_plugin import package
from hermes_botsesh_test import identity, command_catalog
from hermes_constants import set_hermes_home_override, reset_hermes_home_override


class PublicApiContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        token = set_hermes_home_override(self.home)
        self.addCleanup(reset_hermes_home_override, token)

    def test_documented_profile_layout_and_real_display_metadata(self):
        named = self.home / 'profiles' / 'scout'
        named.mkdir(parents=True)
        (named / 'config.yaml').write_text('{}')
        (self.home / 'profiles' / 'empty').mkdir()
        self.assertEqual(identity.profile_names(), ['default', 'scout'])
        (self.home / 'profile.yaml').write_text('display_name: "Garden keeper 🪴"\n')
        self.assertEqual(identity.persona_name(), 'Garden keeper 🪴')
        with patch.dict(os.environ, {'HERMES_PROFILE_NAME':'foreign', 'HERMES_PROFILE':'foreign'}):
            self.assertEqual(identity.current_profile(), 'default')
            with identity.profile_scope('scout') as target:
                self.assertEqual(target, named)
                self.assertEqual(identity.current_profile(), 'scout')
        self.assertEqual(identity.current_profile(), 'default')
        (self.home / 'profile.yaml').write_text('display_name: [broken')
        self.assertIsNone(identity.persona_name())

    def test_custom_home_does_not_escape_to_os_default_root(self):
        self.assertEqual(identity.profile_dir('default'), self.home)
        self.assertEqual(identity.profile_dir('scout'), self.home / 'profiles' / 'scout')
        with patch.object(identity.sys, 'argv', ['hermes','--profile=default','botsesh','status']):
            self.assertEqual(identity.explicit_native_profile(), 'default')
        with patch.object(identity.sys, 'argv', ['hermes','-pdefault','botsesh','status']):
            self.assertEqual(identity.explicit_native_profile(), 'default')
        with patch.object(identity.sys, 'argv', ['hermes','botsesh','send','--','--profile=foreign']):
            self.assertIsNone(identity.explicit_native_profile())

    def test_shortcuts_never_disclose_quick_exec_body_or_alias_target(self):
        config = {'quick_commands': {
            'privateexec': {'type':'exec', 'command':'SECRET SHELL BODY'},
            'privatealias': {'type':'alias', 'target':'SECRET ALIAS TARGET', 'description':'Useful shortcut'},
        }}
        with patch('hermes_cli.config.load_config_readonly', return_value=config):
            rows = command_catalog.build_manifest()
        values = repr(rows)
        self.assertNotIn('SECRET', values)
        names = {r['name'] for r in rows}
        self.assertTrue({'commands','status','privateexec','privatealias'}.issubset(names))
        self.assertTrue({'topic','start'}.isdisjoint(names))

    def test_migration_map_enumerates_all_seventeen_original_sites(self):
        guide = Path(__file__).resolve().parents[1] / 'docs/PUBLIC-API.md'
        rows = re.findall(r'^\| (\d+) \|', guide.read_text(), re.M)
        self.assertEqual([int(n) for n in rows], list(range(1, 18)))
