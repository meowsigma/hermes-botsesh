"""Offline native Node invocation from a symlink-installed package; no model/network."""
import importlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class SymlinkBridgeTests(unittest.TestCase):
    def test_symlink_install_executes_real_bridge_and_preserves_origin_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory) / 'plugins' / 'botsesh'
            install.parent.mkdir()
            install.symlink_to(ROOT, target_is_directory=True)
            name = 'botsesh_symlink_regression'
            spec = importlib.util.spec_from_file_location(name, install / '__init__.py', submodule_search_locations=[str(install)])
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            spec.loader.exec_module(package)
            try:
                module = importlib.import_module(name + '.client')
                storage = importlib.import_module(name + '.storage')
                client = module.Client(storage.Store(Path(directory) / 'data'))
                # Invalid origin is rejected before SDK/network construction. A silent
                # symlink entrypoint instead raises invalid_bridge_response, the defect.
                with self.assertRaises(module.BotSeshError) as caught:
                    client.call('check', room='https://example.invalid/b/no-network')
                self.assertEqual(caught.exception.code, 'origin_denied')
                self.assertEqual(Path(client.command()[1]), (ROOT / 'bridge.mjs').resolve())
            finally:
                for key in list(sys.modules):
                    if key == name or key.startswith(name + '.'):
                        sys.modules.pop(key, None)

if __name__ == '__main__':
    unittest.main()
