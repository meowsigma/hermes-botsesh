"""Mutation check for the one turn-binding function (0.10.4 r3 t_af560587, r4 t_4b123f6e).

Each mutant weakens exactly ONE rule of `bind_turn` (or of a site that must go through it) in a copy of the plugin sources on disk;
the focused committed tests must then go RED.  The originals are restored after every mutant (a final `git diff --stat` must be empty).

  PYTHONPATH=<hermes core> MUTATE_PYTHON=<hermes venv python> python tests/check_turn_binding_mutants.py [name-prefix ...]

Run it in a scratch checkout of the commit under test: it rewrites adapter.py, live.py, tool_policy.py, native_turn.py, lean_group.py,
owner_operator.py in place for the duration of each mutant.  Never run it beside another test run of the same tree.

r4 adds the rules the table-driven `test_turn_binding_matrix` pins: a REQUIRED identity that is missing binds nothing (R1, R3), every
PRESENT gateway variable constrains the claim on its own (R4, R5, R7), and each callback states its event id as required (E1-E5).
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = os.environ.get('MUTATE_PYTHON', sys.executable)
TESTS = ['test_turn_binding_sources', 'test_turn_binding_matrix', 'test_failure_labels', 'test_turn_binding_callbacks', 'test_host_tools',
         'test_owner_dm_operator', 'test_solid', 'test_cant_do', 'test_confirmed_pass', 'test_lean_dm', 'test_lean_group']
# MUTATE_TESTS="test_turn_binding_matrix test_turn_binding_callbacks": run only these files (e.g. everything but the AST guard, to prove that a
# declaration mutant is also killed by BEHAVIOUR and not only by the guard that reads the declarations)
TESTS = os.environ.get('MUTATE_TESTS', '').split() or TESTS
FILES = {name: (ROOT / name).read_text() for name in
         ('adapter.py', 'live.py', 'tool_policy.py', 'native_turn.py', 'owner_operator.py', 'lean_group.py')}

M = {}


def mutant(name, file, old, new, *more):
    """One mutant = one rule weakened: `old` -> `new`, plus further (old, new) pairs when the rule is enforced at two places."""
    M[name] = (file, [(old, new), *zip(more[::2], more[1::2])])


# --- the rules of bind_turn -------------------------------------------------------------------------------------------------------
mutant('B1 missing/empty hook ids fall back to the chat scope', 'adapter.py',
       "            return Binding(reason='hook_ids')\n    claim_room, claim_target", "            hook = None\n    claim_room, claim_target")
mutant('B2 the hook ids need not equal the row\'s paired ids', 'adapter.py',
       "and (hook is None or r[2].get('hook_turn') == hook)]", "and True]")
mutant('B3 the gateway event need not equal the row\'s event', 'adapter.py',
       "and (event_id is None or str(r[2].get('event_id', r[2].get('target'))) == str(event_id))", "and True")
mutant('B4 the receipt need not equal the row\'s receipt', 'adapter.py',
       "and (receipt_id is None or r[2].get('receipt_id') == receipt_id)", "and True")
mutant('B5 the input number need not equal the row\'s', 'adapter.py',
       "and (claim_target is None or str(r[2].get('target')) == claim_target)", "and True")
mutant('B6 the room need not equal the row\'s', 'adapter.py',
       "rows = [r for r in pool if (claim_room is None or r[1] == claim_room)", "rows = [r for r in pool if True")
mutant('B7 an ambiguous claim (several rows, no hook ids) binds the first', 'adapter.py',
       "if len(rows) != 1 and hook is None:", "if False:")
mutant('B8 a claim that names nothing may bind', 'adapter.py',
       "    if not (claim_room is not None or claim_target is not None or event_id is not None or receipt_id is not None or hook is not None):",
       "    if False:")
mutant('B9 an ended turn still binds live activity', 'adapter.py',
       "usable = [r for r in rows if (ended or not r[2].get('hook_terminal')) and", "usable = [r for r in rows if True and")
mutant('B10 a compact row binds live activity without its positive native record', 'adapter.py',
       "(not r[2].get('bare') or native_record(r[2]))]", "True]")
mutant('B11 a conflicting gateway room is ignored', 'adapter.py',
       "            if claim_room not in (None, chat):\n                return Binding(reason='scope')", "            if False:\n                return Binding(reason='scope')")
mutant('B11b a conflicting gateway input is ignored', 'adapter.py',
       "            if claim_target not in (None, message):\n                return Binding(reason='scope')", "            if False:\n                return Binding(reason='scope')")
mutant('B12 a required gateway scope may be absent', 'adapter.py',
       "        if ambient == 'required' and not (platform == 'botsesh' and chat and message):\n            return Binding(reason='no_scope')",
       "        if False:\n            return Binding(reason='no_scope')")
mutant('B13 another platform\'s turn binds', 'adapter.py',
       "        if platform and platform != 'botsesh':\n            return Binding(reason='platform')", "        if False:\n            return Binding(reason='platform')")
# --- r4: required identities and partial gateway scope ---------------------------------------------------------------------------
mutant('R1 a required explicit identity that is missing binds as if it were omitted', 'adapter.py',
       "            if name in require:\n                return Binding(reason='missing_' + name)", "            if False:\n                return Binding(reason='missing_' + name)")
mutant('R2 a required hook that is missing binds as if it were omitted', 'adapter.py',
       "        if 'hook' in require:\n            return Binding(reason='missing_hook')", "        if False:\n            return Binding(reason='missing_hook')")
mutant('R3 `require` is optional: an unnamed requirement is silently nothing', 'adapter.py',
       "def bind_turn(*, require, adapter=None", "def bind_turn(*, require=(), adapter=None")
mutant('R4 a gateway chat without an input is not a constraint (partial scope ignored)', 'adapter.py',
       "        if chat:\n            if claim_room not in (None, chat):", "        if chat and message:\n            if claim_room not in (None, chat):")
mutant('R5 a gateway input without a chat is not a constraint (partial scope ignored)', 'adapter.py',
       "        if message:\n            if claim_target not in (None, message):", "        if chat and message:\n            if claim_target not in (None, message):")
mutant('R6 the platform of a complete gateway scope is not needed for a claim', 'adapter.py',
       "        if platform == 'botsesh' and chat and message:\n            claim = (chat, message)", "        if chat and message:\n            claim = (chat, message)")
mutant('R7 pairing ignores the present gateway scope', 'adapter.py',
       "paired = [(a, rid, cur) for a, rid, cur in pool if (claim_room is None or rid == claim_room)\n"
       "                      and (claim_target is None or str(cur.get('target')) == claim_target) and _pairs_row(rid, cur, pair, claim, sole)]",
       "paired = [(a, rid, cur) for a, rid, cur in pool if _pairs_row(rid, cur, pair, claim, sole)]")
# --- pairing ------------------------------------------------------------------------------------------------------------------------
mutant('P1 a compact row pairs without being the sole claimant of the room/input', 'adapter.py',
       "return bool(claim and sole is current and not current.get('hook_terminal')", "return bool(claim and not current.get('hook_terminal')")
mutant('P2 a compact row pairs without its text in the hook message', 'adapter.py',
       "current.get('text') and current['text'] in user_message)\n    if claim and current.get('lean_session')",
       "True)\n    if claim and current.get('lean_session')")
mutant('P3 a compact row pairs for an event id that is not its input', 'adapter.py',
       "and current.get('event_id') == claim[1]", "and True")
mutant('P4 pairing a compact row leaves no positive native record', 'adapter.py',
       "                if cur.get('bare'):\n                    cur['native_turn'] = hook", "                if False:\n                    cur['native_turn'] = hook")
mutant('P5 two rows that claim the same room/input pair the first of them', 'adapter.py',
       "sole = sole[0] if len(sole) == 1 else None", "sole = sole[0] if sole else None")
mutant('P6 a receipt-framed row pairs for any addressed input number', 'adapter.py',
       "re.escape(str(current['target']))", "r'\\d+'")
mutant('N1 the native record is not tied to the bound hook turn', 'adapter.py',
       "and current.get('hook_turn') == native and current.get('hermes_session_id') == native[0])",
       "and current.get('hermes_session_id') == native[0])")
mutant('N2 the native record is not tied to the reported session', 'adapter.py',
       "and current.get('hook_turn') == native and current.get('hermes_session_id') == native[0])",
       "and current.get('hook_turn') == native)")
# --- the sites ----------------------------------------------------------------------------------------------------------------------
mutant('S1 typing binds without the native-record rule', 'adapter.py',
       "bound = bind_turn(require=('room',), adapter=self, room_id=chat_id)  # a compact row only",
       "bound = bind_turn(require=('room',), adapter=self, room_id=chat_id, need='settle')  # a compact row only")
mutant('S2 typing of a compact row needs no established start', 'adapter.py',
       "if current.get('bare') and not (row.get('state') == 'started' or current.get('start_reported')):", "if False:")
mutant('S3 the processing-start callback ignores its event id', 'adapter.py',
       "bound = bind_turn(require=('room', 'event'), adapter=self, room_id=room_id, event_id=event.message_id)\n        if bound:",
       "bound = bind_turn(require=('room',), adapter=self, room_id=room_id)\n        if bound:")
mutant('S4 a chat-only status diagnostic labels the current turn\'s failure', 'adapter.py',
       "        if bind_turn(require=('room',), adapter=self, room_id=chat_id, need='settle'):\n            health(self.client.store, chat_id, last_status=time.time())\n            await self.send_typing(chat_id)",
       "        if bind_turn(require=('room',), adapter=self, room_id=chat_id, need='settle'):\n"
       "            bind_turn(require=('room',), adapter=self, room_id=chat_id, need='settle').current['final_failure_code'] = failure_code(content)\n"
       "            health(self.client.store, chat_id, last_status=time.time())\n            await self.send_typing(chat_id)")
mutant('S5 a chat-level send classifies the failure text', 'adapter.py',
       "else current.get('final_failure_code') or 'runtime_error'\n                self._queue_failure(chat_id, current['target'], code)", "else failure_code(content)\n                self._queue_failure(chat_id, current['target'], code)")
mutant('S6 the event-bound final no longer stores its classification', 'adapter.py',
       "            current['final_failure_code'] = failure_code(text_content)\n", "            pass\n")
mutant('S7 the native reconcile label invents a provider verdict', 'native_turn.py',
       "else current.get('final_failure_code') or 'runtime_error')", "else current.get('final_failure_code') or 'provider_error')")
mutant('S8 live frames ignore a conflicting gateway scope', 'live.py',
       "bound = bind_turn(require=('hook',), hook=(session_id, turn_id), ambient='optional')\n    return (bound.adapter",
       "bound = bind_turn(require=('hook',), hook=(session_id, turn_id))\n    return (bound.adapter")
mutant('S9 live frames fall back to the gateway room/input when the hook ids bind nothing', 'live.py',
       "bound = bind_turn(require=('hook',), hook=(session_id, turn_id), ambient='optional')\n    return (bound.adapter",
       "bound = bind_turn(require=('hook',), hook=(session_id, turn_id), ambient='optional') or bind_turn(require=(), ambient='required')\n    return (bound.adapter")
mutant('T1 tool authority ignores the hook\'s own ids', 'tool_policy.py',
       "granted = bind_turn(require=('hook',), hook=(session_id, turn_id), ambient='required')", "granted = bind_turn(require=(), ambient='required')")
mutant('T2 the tool veto accepts ids that disagree', 'tool_policy.py',
       "            if not bound or bound.current is not current:", "            if False:")
mutant('T3 the tool veto does not re-check the turn after verification', 'tool_policy.py',
       "if bind_turn(require=('hook',), adapter=adapter, hook=(session_id, turn_id), ambient='required').current is not current:", "if False:")
mutant('O1 owner-DM authority ignores the gateway scope (all three bindings)', 'owner_operator.py',
       "first = bind_turn(require=('room',), room_id=chat_id, ambient='required', need='settle')",
       "first = bind_turn(require=('room',), room_id=chat_id, need='settle')",
       "bound = bind_turn(require=('room', 'input'), room_id=chat_id, target=target, ambient='required', need='settle')",
       "bound = bind_turn(require=('room', 'input'), room_id=chat_id, target=target, need='settle')",
       "if (bind_turn(require=('room', 'input'), adapter=adapter, room_id=chat_id, target=target, ambient='required', need='settle').current is not current",
       "if (bind_turn(require=('room', 'input'), adapter=adapter, room_id=chat_id, target=target, need='settle').current is not current")
mutant('O2 owner-DM authority completes a missing room from the gateway\'s variables (all three bindings)', 'owner_operator.py',
       "first = bind_turn(require=('room',), room_id=chat_id, ambient='required', need='settle')",
       "first = bind_turn(require=(), room_id=chat_id, ambient='required', need='settle')",
       "bound = bind_turn(require=('room', 'input'), room_id=chat_id, target=target, ambient='required', need='settle')",
       "bound = bind_turn(require=('input',), room_id=chat_id, target=target, ambient='required', need='settle')",
       "if (bind_turn(require=('room', 'input'), adapter=adapter, room_id=chat_id, target=target, ambient='required', need='settle').current is not current",
       "if (bind_turn(require=('input',), adapter=adapter, room_id=chat_id, target=target, ambient='required', need='settle').current is not current")
# --- r4: every gateway callback states its event id as required ------------------------------------------------------------------
mutant('E1 the processing-start callback does not require its event id', 'adapter.py',
       "bound = bind_turn(require=('room', 'event'), adapter=self, room_id=room_id, event_id=event.message_id)\n        if bound:",
       "bound = bind_turn(require=('room',), adapter=self, room_id=room_id, event_id=event.message_id)\n        if bound:")
mutant('E2 the completion callback does not require its event id', 'adapter.py',
       "bound = bind_turn(require=('room', 'event'), adapter=self, room_id=room_id, event_id=event.message_id, need='settle')\n        if not bound:",
       "bound = bind_turn(require=('room',), adapter=self, room_id=room_id, event_id=event.message_id, need='settle')\n        if not bound:")
mutant('E3 the event-bound final names no work item without its ids but goes on', 'adapter.py',
       "        if _blank(event.source.chat_id) or _blank(event.message_id):\n            return SendResult(success=False, error='Native final names no BotSesh work item.'), self  # no room or event id: no work item\n",
       "")
mutant('E4 the late error notice acts on a missing or empty chat/event id', 'adapter.py',
       "        if _blank(chat_id) or _blank(event_id):\n            return\n", "")
mutant('E5 the late error notice acts when no receipt of that input exists', 'adapter.py',
       "        if not saved or saved.get('event_id', str(target)) != str(event_id):", "        if saved.get('event_id', str(target)) != str(event_id):")
mutant('E6 the typing callback names a room it cannot check before it reads the registry', 'adapter.py',
       "        if not bound:\n            return  # no room, an unknown one or no bound row: presentation of nothing\n", "")
mutant('E7 the late error notice raises for a room that is not enrolled', 'adapter.py',
       "        try:\n            saved = self.client.store.room(chat_id).get('processing_receipts', {}).get(str(target), {})\n"
       "        except StorageError:\n            return  # a room that is not locally enrolled has no receipt to fail\n",
       "        saved = self.client.store.room(chat_id).get('processing_receipts', {}).get(str(target), {})\n")
# --- the group-room sites (0.10.5): the same binding function, nothing of their own ---------------------------------------------------
# (named G1.. so that the prefix `R` still selects only the r4 rules above)
mutant('G1 the short group hint needs only the gateway scope, not the hook\'s own ids', 'tool_policy.py',
       "group_lean = bool(granted and granted.get('lean_group') is True)", "group_lean = bool(current and current.get('lean_group') is True)")
mutant('G2 the room ledger credits a session of a turn whose delivery is unproven', 'adapter.py',
       "        if not self._delivery_proves_read(current):\n            # Never credit a turn without positive proof", "        if False:\n            # Never credit a turn without positive proof",
       "return current.get('hermes_session_id') if BotSeshAdapter._delivery_proves_read(current) else None", "return current.get('hermes_session_id')")
mutant('G3 an unproven lean group row may attest a read through an explicit pass/post', 'tool_policy.py',
       "if (current.get('lean_group') is True and not adapter._delivery_proves_read(current)", "if (current.get('lean_group') is True and False")
# --- the safety framing of a lean group turn (0.10.5 r5): every statement of the full catch-up is in the system prompt, verbatim ---------
_TAIL = " + SERVER_POLICY + ' ' + HINT_SAFETY + PACKET_SAFETY + SERVER_SUMMARY + ' ' + SERVER_HUMANS)"
mutant('G4 the lean group prompt drops the plugin\'s own hint statements (untrusted data, grant owner only, quoted words cannot)', 'lean_group.py',
       _TAIL, " + SERVER_POLICY + ' ' + PACKET_SAFETY + SERVER_SUMMARY + ' ' + SERVER_HUMANS)")
mutant('G5 the lean group prompt drops the packet notice (replayed history, quoted room words)', 'lean_group.py',
       _TAIL, " + SERVER_POLICY + ' ' + HINT_SAFETY + SERVER_SUMMARY + ' ' + SERVER_HUMANS)")
mutant('G6 the lean group prompt drops the server\'s policy (intent labels, tags, fake roles, file provenance)', 'lean_group.py',
       _TAIL, " + HINT_SAFETY + PACKET_SAFETY + SERVER_SUMMARY + ' ' + SERVER_HUMANS)")
mutant('G7 the lean group prompt drops the server\'s summary and human-accept statements', 'lean_group.py',
       _TAIL, " + SERVER_POLICY + ' ' + HINT_SAFETY + PACKET_SAFETY)")
mutant('G8 a server that words its static framing differently still gets the lean text', 'lean_group.py',
       "        if not (isinstance(instructions, str) and SERVER_POLICY in instructions and SERVER_HUMANS in instructions\n"
       "                and isinstance(summary, str) and SERVER_SUMMARY in summary):", "        if False:")
mutant('G9 a key the lean text cannot show is dropped silently', 'lean_group.py',
       "        if any(part.get(key) for key in part if key not in DIGEST_KEYS and key not in IGNORED_KEYS):", "        if False:")
mutant('G10 the lean cant_do sentence loses \'not clarification\'', 'lean_group.py',
       "capability blocker, not clarification. '", "capability blocker. '")


def run(full=False):
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(p for p in (str(ROOT / 'tests'), env.get('PYTHONPATH', '')) if p)
    # one `discover` per file, exactly as the repository runs its suite (it puts tests/ on sys.path itself)
    text = ''
    for name in TESTS:
        out = subprocess.run([PY, '-m', 'unittest', 'discover', '-s', 'tests', '-p', name + '.py'], capture_output=True, text=True,
                             env=env, cwd=ROOT)
        text += out.stderr + out.stdout
        if 'FAILED' in out.stderr and not full:
            break  # a mutant is killed by its first red file; only the baseline runs every file
    failed = sorted(set(re.findall(r'^(?:FAIL|ERROR): (\S+)', text, re.M)))
    if os.environ.get('MUTATE_DEBUG'):
        print(text[-3000:])
    return failed, re.findall(r'^(Ran \d+ tests.*|FAILED.*)$', text, re.M)


def main(prefixes):
    failed, summary = run(full=True)
    print('baseline:', summary, flush=True)
    if failed:
        print('baseline is not green:', failed)
        return 2
    survivors = []
    for name, (file, edits) in M.items():
        if prefixes and not any(name.startswith(p) for p in prefixes):
            continue
        text = FILES[file]
        bad = [old for old, _new in edits if text.count(old) != 1]
        if bad:
            print('%s: pattern found %d times (fix the mutant)' % (name, text.count(bad[0])))
            survivors.append(name + ' [bad pattern]')
            continue
        path = ROOT / file
        mutated = text
        for old, new in edits:
            mutated = mutated.replace(old, new)
        try:
            path.write_text(mutated)
            failed, summary = run()
        finally:
            path.write_text(text)
        if failed:
            print('%s: KILLED by %d test(s): %s' % (name, len(failed), ', '.join(f.split('(')[0] for f in failed[:4]) + (' ...' if len(failed) > 4 else '')), flush=True)
        else:
            print('%s: SURVIVED %s' % (name, summary), flush=True)
            survivors.append(name)
    print('survivors:', survivors or 'none')
    return 1 if survivors else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
