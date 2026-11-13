# Plugin 0.8.0 release-candidate seams

## Exact scope and authorization

`rel/plugin-0.8.0-rc` is an unpublished stock-track candidate, version `0.8.0-rc.2`.

- Base: published `v0.7.2`, `9b97495278bcad562a67d9761ef5b65d6e0279bf`.
- Check-ins: `f44737e576a4aed57b9a0a2de91f64132787de7d` → `c1e27e0b984695dcd3dabcaffb0cc8e48dc334a6` (Opus chain `t_64a46e44` → `t_b1b76921`).
- Agreement/receipt handles: `9fd1266124f74beb329fa97716c7b57f5aa4bd43` → `c22f316dd893b5f7301eb0a965696dc3cf5f52ca` → `c56b7701b2ce34dfedf2a4226acad8e59419794d` → `23202a8bf10dd64fb172b181135af0ad1b86f711` (Opus `t_c8667f7b`). The parent explicitly added this source before completion; it is now included, superseding this card's original exclusion.

The earlier check-in-only `0.8.0-rc.1` tip `8d7ba14783beed000328b312e2d59277a4e64c72` remains in ancestry, with its separate passing receipts retained in the task evidence. The combined RC is an ordinary additive merge, not a rewrite of that pushed ref.

Check-in support **requires botsesh.com server with check-ins** (planned B81.15). This branch does not deploy or certify the server or claim check-ins are live. A proposal is pending-only until its own person clicks Allow in the original room browser. Connection consent is not schedule consent. Scheduled inputs retain system authorship and read/post/pass-only ceilings.

Agreement requires the matching server capability. The real Codex acceptance rerun is still pending owner authorization: independent code review and offline replays do **not** make this candidate field-verified.

## Resolved overlaps and retained boundaries

Six textual conflicts were reconciled hunk by hunk, with both source deltas retained:

- `adapter.py`: keep service-policy admission and its exact approving-person gateway authorization, but preserve BotSesh/system raw authorship. Bind the first service-policy lookup to its frozen original processing row before the shared receipt fence; both lookups reuse the same client ID. Retain receipt-handle projection and the final full-context size check (the older partial-text check is superseded). Ordinary group silence remains optional; typed scheduled inputs retain their prior reply-expected contract.
- `native_turn.py`: compose restored service sender selection with ordinary group/direct-DM/takeover reply expectations. Keep all reviewed current-input availability checks, definitive rejected-post handling and immutable original replay fences.
- `oauth.py`: union the independent `propose_check_in` and `agree` routes; neither changes credential custody.
- `tools.py`: keep the approved check-in schema, the newer receipt-handle pass schema and the agreement schema. Pair proposal/pass/join/ownership handlers in exactly that order, then append the agreement handler. Registration/handler witnesses cover both additions.
- `tests/test_plugin.py`: union the two intentionally added tools in the still-exhaustive equality assertion.
- `CHANGELOG.md`: one honest combined RC entry preserves the source contribution/silence/threading details and explicitly disclaims field verification.

Clean textual merges were also inspected:

- `tool_policy.py`: an ordinary chat-only guest may use read/post/pass/agree, but scheduled service inputs may use **only read/post/pass**. Model-schema filtering (direct and lazy `tool_call`) and the independent live execution veto enforce the narrower service set even under local `guest_tools:on`. Service policy cannot enable host tools, another schedule or agreement. An ordinary owner's next turn retains its configured tools. Runtime policy text matches the actual surface.
- `plugin.yaml`: declare both proposal and agreement tools; the agreement source had not yet added its manifest declaration. Native validation must match the actual registration inventory.
- `skills/botsesh/SKILL.md`, `README.md`, after-install and release metadata: retain both own-person pending-only check-in consent and agreement/receipt guidance; versions agree on `0.8.0-rc.2`.

The new `tests/test_checkin_agreement_integration.py` covers service versus ordinary schema/veto boundaries under every local override, owner restoration, first-policy original-input binding, system-authored native intake/restore, receipt-handle projection and proposal/agreement handler ordering. The mechanical union exposed agreement on scheduled schemas and unbound first service-policy lookup in its RED witness; the combined integration fixes retain the narrower existing check-in contract.

The stock 0.23.1 approval-control fixture now inherits the real base template (`_EA_*` helpers) with unsupported network/chat methods asserting if called. Its exact action/deny assertions remain intact; product approval behavior is unchanged. Existing agreement, Ari/Bo, handle/scope and terminal replay regressions remain in full discovery.

## Trial-input packaging fix

The first combined stock install refused its normal security scan: the non-runtime `tools/agreement_trial/luna-catalog.json` copied unrelated provider instruction templates into the plugin, producing four HIGH findings. No force/trust/scan override was used. The exact original is retained in this task's evidence (SHA-256 `9253c96212be2ac242177bf13f0d0dc64017dbc9f485ac38e6cc1136a4e0e79d`) and at the approved source commit; the distributed plugin no longer includes it.

Only the opt-in auth-free trial preflight consumed this input. It now requires an explicit absolute `BOTSESH_TRIAL_MODEL_CATALOG` outside the plugin and verifies that exact hash before opening a capture server, creating private state or launching Codex. The trial README documents custody; a no-inference regression retains missing/changed-input denial. Runtime/schema behavior and the original recorded evidence are unchanged. Native validation reports a safe scan on the resulting package; compatibility warnings remain visible.

## Verification and publication boundary

Run both Python suites from the repository root:

```sh
PYTHONPATH=/path/to/stock-hermes python3 -m unittest discover -s tests -v
PYTHONPATH=/path/to/stock-hermes python3 -P -m pytest --import-mode=append tests/controls -q
```

Use a fresh private HOME/HERMES_HOME and unmodified stock CLI for an exact-commit native install, then read back installed version, registration and every tracked source byte. No connection, account, model call, gateway start or browser gate is needed. Retain compatibility warnings and explicit real-server skips honestly.

`t_b15d695c` reviews the exact final combined SHA and these seams. A full Python suite plus install smoke is not natural due-time native-model proof or real Codex acceptance. This card authorizes only the candidate branch push: no tag, release, main advance, catalog change, server deployment or installed-user-profile update. Publication requires separate authorization and final review.

### Known stock summary-core dependency

Latest stock v0.23.1 (`3b421578588243f920886e6fe79a6faa87ea026e`) does not call request-policy middleware from its iteration-summary owner. The unmodified `tests/test_summary_recovery_replay.py` therefore exposes the broader cached schema inventory on that stock path. Do not remove/skip its assertion or present stock installation as proof that it passed. The independent native execution veto remains enforced, but model-visible summary inventory is a real outstanding stock compatibility gap.

The source review used core `7b74b110cf771b68a4217360a513ad1f4371be8f` (the summary-owner fix). Run the final full Python suites against that exact separately labelled source archive, and retain the stock failure/focused rerun plus the untouched-stock install smoke as separate rows. No stock or owner-core patch is made by this card; no future released `requires_hermes` floor is guessed. This dependency and the pending real Codex acceptance prevent any stock field-verification claim.
