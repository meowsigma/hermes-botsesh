# Bounded native agreement trial

This is the retained 11d harness with 11e budget/delivery repairs. It is not a
published plugin feature and **has no inference authorization by default**.
Do not run a paid sample until the owner grants a new allowance and independent
source review accepts the exact plugin/core commits. No deployment is needed.

## Offline checks

Use an existing prepared Hermes interpreter; never a paid provider:

```sh
PYTHONPATH=tests:/path/to/reviewed-hermes-core /path/to/python -m unittest \
  test_receipt_failure_replay test_summary_recovery_replay test_trial_harness_replay -v
```

Core regression: use its canonical `scripts/run_tests.sh` with
`tests/agent/test_iteration_summary_request_policy.py` and an explicitly prepared
`HERMES_PYTHON`, `HERMES_HOME`, and `HERMES_RUNTIME_DIR`.

See `tests/fixtures/agents11d/README.md` for exact replay boundaries and the missing
raw summary envelope. Restore the old cleanup order in `stop_after_drain` to
verify the pass race is RED; restore the stock core summary helper to see the
12-tool regression; restore `tools.py` at e2c83389 for the receipt-guidance RED.

## New owner allowance requested: 36 calls

- Hard cap **12 calls per scenario**, across BOTH agents, including all retries,
  summaries, recovery and closing turns.
- The same native actor/frozen receipt may consume at most **4 calls**. A fifth
  request while still unsettled latches `STOP.json` before the CLI can start.
- Total hard cap **36 = 12 x 3 scenarios**. `--authorized-call-budget` defaults to
  zero and cannot exceed 36; its presence is not itself owner consent.
- Any invalid tool item, altered four-tool native inventory, missing receipt or
  exceeded cap stops the entire attempt. No automatic next-scenario rollover,
  hidden continuation, budget reset or silent retry.
- Reservations persist before CLI launch. A restart uses the same ledger and
  stop latch. Explicit posts remain nonterminal; agree still has no read/wake
  effect. No living-chat or attestation rule is changed.

## Runtime layout after approval (not exercised as model behavior on 11e)

Copy this directory's files to a **fresh task-owned trial workspace**. Put the
reviewed plugin clone at `plugin/`, the unchanged server clone at `server/` with
real prepared dependencies, and the reviewed core clone at `stock-core/`.
Required core patch branch: `meowsigma/hermes-agent`,
`fix/t_69137884-summary-policy` (pin the exact SHA from the card handoff).
The old stock core bf0203a7 is insufficient: it bypasses `llm_request` middleware
for iteration-limit summaries. The prepared interpreter paths in the retained
scripts are installation-specific; verify them before setup, not during a trial.
The catalog is only for the auth-free loopback preflight and is **not shipped
with the plugin**. Its unrelated provider instruction templates caused the
normal stock installer to require a security override. They are not part of
BotSesh runtime behavior; no scan bypass is needed by the packaged RC.

For a separately authorized trial, obtain the exact retained input from
`tools/agreement_trial/luna-catalog.json` at source commit
`23202a8bf10dd64fb172b181135af0ad1b86f711` (also preserved with this RC task's
evidence). Place it outside the plugin checkout and explicitly set
`BOTSESH_TRIAL_MODEL_CATALOG` to its absolute path. The SHA-256 must be
`9253c96212be2ac242177bf13f0d0dc64017dbc9f485ac38e6cc1136a4e0e79d`.
Missing/different input fails before opening a capture server or launching
Codex. This input is not an inference allowance; zero remains the default.

`prepare_agreement_runtime.py` uses two newly named containers under that
workspace's basename; it never reuses/stops 11d's containers. Run the native
`--dry-run` first with the one-shot guard. Remove only the dry fixture evidence
from the live ledger's namespace or preserve it separately, as in 11d; do not
carry a live STOP/INVALID latch into a fresh authorized cohort or erase a failed
live cohort to retry. Scenario/persona literals remain unchanged.

On failure, stop intake, drain already-reserved relay calls, observe the native
receipt states, then stop the gateways/server. Responses are retained before
writing. `response_written` is not canonical pass confirmation: inspect the
saved shutdown-native-state and server snapshot. A true disconnect is
`unconfirmed_disconnected`, retained with the original action and no replay.

Never publish native homes, auth files, room grants, private people stores or
reasoning. Preserve credential-free scripts and receipts before scratch cleanup.
