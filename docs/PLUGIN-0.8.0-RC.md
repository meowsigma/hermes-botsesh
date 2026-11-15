# Plugin 0.8.0 release-candidate seams

## Exact scope and authorization

`rel/plugin-0.8.0-rc3` is an unpublished stock-track candidate, version `0.8.0-rc.3` (its direct predecessors are `rel/plugin-0.8.0-rc` at `0.8.0-rc.2`, `3d311ff1790657bf9fd6997d8773a7e651757f93`, plus the independently reviewed stale-post plugin `54e9f80dbdac482727b7300439aa87b7f89ddeec`).

- Base: published `v0.7.2`, `9b97495278bcad562a67d9761ef5b65d6e0279bf`.
- Check-ins: `f44737e576a4aed57b9a0a2de91f64132787de7d` → `c1e27e0b984695dcd3dabcaffb0cc8e48dc334a6` (Opus chain `t_64a46e44` → `t_b1b76921`).
- Agreement/receipt handles: `9fd1266124f74beb329fa97716c7b57f5aa4bd43` → `c22f316dd893b5f7301eb0a965696dc3cf5f52ca` → `c56b7701b2ce34dfedf2a4226acad8e59419794d` → `23202a8bf10dd64fb172b181135af0ad1b86f711` (Opus `t_c8667f7b`). The parent explicitly added this source before completion; it is now included, superseding this card's original exclusion.

The earlier check-in-only `0.8.0-rc.1` tip `8d7ba14783beed000328b312e2d59277a4e64c72` remains in ancestry, with its separate passing receipts retained in the task evidence. The combined RC is an ordinary additive merge, not a rewrite of that pushed ref.

Check-ins are **live on botsesh.com**; this branch does not deploy or certify the server. A proposal is pending-only until its own person clicks Allow in the original room browser. Connection consent is not schedule consent. Scheduled inputs retain system authorship and read/post/pass-only ceilings.

Agreement requires the matching server capability and turns on automatically when the connected server supports it (see rc.3 below). The real Codex acceptance rerun is still pending owner authorization: independent review and offline replays do **not** make this candidate field-verified.

## rc.3: agreement gated on the server's own reads

The server that serves botsesh.com can lag the plugin's agreement source: an older server has neither the MCP `agree` tool nor the REST `PUT /messages/by-number/:n/agreement` route, so an ungated `botsesh_agree` and the group-chat hint that recommends it would fail on every wake. rc.3 changes only what the model is offered; transports, credentials, receipts and every other tool are unchanged.

- **Detection (`agreement.py`).** A server with agreement marks projects an `agreements` list on every message it returns (BOT-API "Agreement marks": MCP `catch_up`, REST message views). The shared read in `oauth.py` (also used by invitation seats) records, on the local room binding, whether its exact messages carried it (`agreement_support`). One projected list proves support; only a non-empty read with none proves absence. No `tools/list`, probe or extra request is made, and nothing happens per wake beyond that existing read. A newer read replaces the value, so support appears by itself after a server upgrade.
- **Unknown keeps today's behaviour.** A binding that has not yet observed a non-empty read (for example a recovered turn from before the upgrade) offers agreement exactly as rc.2 did. Only proven absence hides it.
- **Proven absence.** `restrict_request` drops `botsesh_agree` from the guest allowlist (direct and lazy `tool_call`) and from an owner turn's native declarations (chat, responses, Anthropic and Gemini-style shapes), and replaces only this plugin's exact agreement sentences: the group-chat hint, the post tool's stale-refusal note and the tools-off notice. Earlier model history is never rewritten; the cached request is never mutated. `turn_context`, the blocked-tool notice and the `room_moved_on` tool message follow the same state. A native `botsesh_agree` call (for example a lazily discovered owner tool) fails closed with `agreement_unsupported` before any transport request or journal entry, so the turn's automatic final is unaffected. Local CLI use is not blocked.
- **Supported rooms.** Hint, tool schemas, error text, `turn_context` and the blocked-tool notice are byte-identical to `54e9f80d` (`0.8.0-rc.2` plus the stale-post plugin); `tests/test_agreement_capability.py` pins their digests.
- **Known limit.** Stock core paths that bypass `llm_request` (such as the iteration-summary owner in v0.23.1, see below) can still show the unfiltered text; the execution veto and the local refusal still hold.

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
- `skills/botsesh/SKILL.md`, `README.md`, after-install and release metadata: retain both own-person pending-only check-in consent and agreement/receipt guidance; versions agree on `0.8.0-rc.3`.

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
