"""Bounded implementation mutation checks; never changes source on disk."""
import contextlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import test_owner_dm_operator as test

name = 'owner_dm_test.owner_operator'
text = (Path(__file__).resolve().parents[1] / 'owner_operator.py').read_text()
mutants = {
    'skip_grant_owner': ("live.get('human_owns_grant') is not True", 'False'),
    'accept_truthy_owner': ("live.get('human_owns_grant') is not True", "not live.get('human_owns_grant')"),
    'allow_paused': ("live.get('access_state') != 'authorized'", 'False'),
    'ignore_live_member': ("live.get('membership_id') != pin['membership_id']", 'False'),
    'ignore_verified_author': ("author.get('verified') is not True", 'False'),
    'ignore_credential_rebind': ("any(binding.get(key) != pin.get(key) for key in IDENTITY_KEYS)", 'False'),
}
results = {}
for label, (old, new) in {'baseline': ('', ''), **mutants}.items():
    source = text
    if label != 'baseline':
        assert old in text, label
        # Mutate the authorization function, not the separate display projection.
        before, after = text.rsplit(old, 1)
        source = before + new + after
    module = types.ModuleType(name)
    module.__package__ = 'owner_dm_test'
    exec(compile(source, str(Path(__file__).resolve().parents[1] / 'owner_operator.py'), 'exec'), module.__dict__)
    stream = io.StringIO()
    with patch.dict(sys.modules, {name: module}):
        result = unittest.TextTestRunner(stream=stream).run(unittest.defaultTestLoader.loadTestsFromTestCase(test.OwnerDmTests))
    if label == 'baseline':
        assert result.wasSuccessful(), stream.getvalue()
    else:
        assert not result.wasSuccessful(), 'SURVIVED: ' + label
    results[label] = 'pass' if label == 'baseline' else 'killed'
print(__import__('json').dumps(results, indent=2))
