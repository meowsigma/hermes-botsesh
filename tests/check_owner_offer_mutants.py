"""Bounded exact-replay mutation checks; no source edits, network or model."""
import contextlib
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import test_owner_offer_model as test

ROOT = Path(__file__).resolve().parents[1]
MUTANTS = {
    'missing_owner_line': ('owner_operator', "return text\n", "return ''\n"),
    'context_accepts_foreign': ('owner_operator', "live.get('human_owns_grant') is not True", 'False'),
    'context_accepts_truthy': ('owner_operator', "live.get('human_owns_grant') is not True", "not live.get('human_owns_grant')"),
    'context_accepts_expired': ('owner_operator', "(context or {}).get('until', 0) <= time.monotonic()", 'False'),
    'discovery_current_room_only': ('tools', "or owner_dm and managed(r)", ''),
    'discovery_leaks_groups': ('tools', "or owner_dm and managed(r)", 'or managed(r)'),
    'omit_creation_provenance': ('managed_rooms', "room['created_from_owner_dm'] = source", "room['created_from_owner_dm'] = {}"),
    'recent_ignores_source': ('managed_rooms', "managed(r) and created_here(r, source)", 'managed(r)'),
    'pick_ambiguous_name': ('managed_rooms', "if len(matches) != 1:", 'if not matches:'),
    'inactive_name_allowed': ('managed_rooms', "binding.get('active') is True", 'True'),
}
results = {}
for label, spec in [('baseline', None), *MUTANTS.items()]:
    with contextlib.ExitStack() as stack:
        if spec:
            filename, old, new = spec
            text = (ROOT / (filename + '.py')).read_text()
            assert old in text, label
            text = text.replace(old, new, 1)
            name = 'hermes_botsesh_test.' + filename
            module = types.ModuleType(name); module.__package__ = 'hermes_botsesh_test'
            exec(compile(text, str(ROOT / (filename + '.py')), 'exec'), module.__dict__)
            stack.enter_context(patch.dict(sys.modules, {name: module}))
            if filename == 'owner_operator': stack.enter_context(patch.object(test, 'owner_turn_context', module.owner_turn_context))
            if filename == 'tools': stack.enter_context(patch.object(test, 'tools', module))
            if filename == 'managed_rooms': stack.enter_context(patch.object(test, 'resolve_room', module.resolve_room))
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream).run(test.load_tests(unittest.defaultTestLoader, None, None))
        if not spec: assert result.wasSuccessful(), stream.getvalue()
        else: assert not result.wasSuccessful(), 'SURVIVED: ' + label
        results[label] = 'pass' if not spec else 'killed'
print(json.dumps(results, indent=2))
