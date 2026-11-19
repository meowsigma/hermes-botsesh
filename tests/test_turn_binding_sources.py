"""0.10.4 r3: nothing is attributed to a turn except through `adapter.bind_turn`.

A static check of the plugin's own sources (no Hermes core, no inference).  The turn-binding keys a hook writes or a callback trusts
(`hook_turn`, `native_turn`, the gateway's `HERMES_SESSION_*` variables, the adapter's in-flight rows `_current`) may be touched only in
the places listed here, each with the reason it is not an attribution of an outside signal.  A new reader of one of them is a new
attribution path: it fails here until it is routed through `bind_turn` (or, rarely, justified in this list on review).
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# keys that carry a turn attribution -> {file: {function that may name it}}
KEYS = {
    'hook_turn': {'adapter.py': {'bind_turn', 'native_record', 'BotSeshAdapter._delivery_proves_read'}},
    'native_turn': {'adapter.py': {'bind_turn', 'native_record'}},
    'hermes_session_id': {'adapter.py': {'bind_turn', 'native_record', 'BotSeshAdapter._delivery_proves_read',
                                         'BotSeshAdapter._remember_dm_session', 'BotSeshAdapter._proven_session'}},
    'lean_proof': {'adapter.py': {'_dm_catchup_context', 'BotSeshAdapter._delivery_proves_read'}},
    # a failure label: written only by the EVENT-BOUND final, read only where a failure is settled for the bound row
    'final_failure_code': {'adapter.py': {'BotSeshAdapter.send', 'BotSeshAdapter.send_final_ledgered'},
                           'native_turn.py': {'NativeTurnMixin._reconcile_native'}},
    'provider_hint': {},  # the chat-only status hint no longer exists anywhere
}

# the gateway's own session variables: read by `_ambient` (the one reader behind `bind_turn(ambient=...)`).  `tools.py` handlers key
# the STORE row of the running native input by that variable and then require the receipt handle the model was shown (capability-bound,
# no in-memory row, no hook id, no presence); they never touch `_current`.
SESSION_VARIABLES = {'adapter.py': {'_ambient'},
                     'tools.py': {'_agreement_available', 'calling_scope', 'agree', 'pass_turn', 'post', 'read'}}

# the adapter's in-flight rows: only its own room loop creates, advances and retires them, by object identity
OWN_LOOP = {'BotSeshAdapter.__init__', 'BotSeshAdapter._check_room', 'BotSeshAdapter._receive_while_busy',
            'BotSeshAdapter._apply_busy_mode', 'BotSeshAdapter._turn_running', 'BotSeshAdapter._steer_point',
            'BotSeshAdapter._steer_message', 'BotSeshAdapter._heartbeat', 'BotSeshAdapter._flush_failures.finish'}
CURRENT = {'adapter.py': OWN_LOOP,
           'native_turn.py': {'NativeTurnMixin._admit_event', 'NativeTurnMixin._restore_native',
                              'NativeTurnMixin._reconcile_native', 'NativeTurnMixin._finish_current'}}
# the registry of live adapters: defined at module level, joined by each adapter, and read by `bind_turn` only (hooks and tools find
# their adapter there)
ADAPTERS = {'adapter.py': {'', 'BotSeshAdapter.__init__', 'bind_turn'}}


def occurrences():
    found = []
    for path in sorted(ROOT.rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(('tests/', 'vendor/', 'scripts/', 'docs/', 'tools/')):
            continue

        def walk(node, scope):
            for child in ast.iter_child_nodes(node):
                inner = scope + [child.name] if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else scope
                where = '.'.join(inner)
                if isinstance(child, ast.Attribute) and child.attr == '_current':
                    found.append(('_current', rel, where, child.lineno))
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    if child.value in KEYS:
                        found.append((child.value, rel, where, child.lineno))
                    elif child.value.startswith('HERMES_SESSION_'):
                        found.append(('session variable', rel, where, child.lineno))
                if isinstance(child, ast.Name) and child.id == '_ADAPTERS':
                    found.append(('_ADAPTERS', rel, where, child.lineno))
                if isinstance(child, ast.ImportFrom):
                    found.extend(('_ADAPTERS', rel, where, child.lineno) for alias in child.names if alias.name == '_ADAPTERS')
                walk(child, inner)
        walk(ast.parse(path.read_text()), [])
    return found


class TurnBindingSources(unittest.TestCase):
    def test_every_turn_attribution_key_is_touched_only_where_it_is_listed(self):
        allowed = {**{name: files for name, files in KEYS.items()}, 'session variable': SESSION_VARIABLES, '_current': CURRENT,
                   '_ADAPTERS': ADAPTERS}
        rogue = [(key, rel, where, line) for key, rel, where, line in occurrences() if where not in allowed[key].get(rel, ())]
        self.assertEqual(rogue, [], 'an attribution path outside bind_turn: route it through adapter.bind_turn')

    def test_the_binding_function_and_its_two_helpers_are_the_only_writers_of_a_turn_pairing(self):
        writes = []
        for path in sorted(ROOT.glob('*.py')):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) and isinstance(node.slice, ast.Constant)
                        and node.slice.value in ('hook_turn', 'native_turn', 'hermes_session_id')):
                    writes.append((path.name, node.slice.value))
        self.assertEqual(sorted(writes), [('adapter.py', 'hermes_session_id'), ('adapter.py', 'hook_turn'), ('adapter.py', 'native_turn')])


if __name__ == '__main__':
    unittest.main()
