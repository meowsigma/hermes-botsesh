# Plugin 0.8.0 release-candidate seam

## Exact scope

`rel/plugin-0.8.0-rc` is an unpublished stock-track candidate, version `0.8.0-rc.1`.
Base: published `v0.7.2`, `9b97495278bcad562a67d9761ef5b65d6e0279bf`.
Included check-in source: `f44737e576a4aed57b9a0a2de91f64132787de7d` then `c1e27e0b984695dcd3dabcaffb0cc8e48dc334a6` (Opus review chain `t_64a46e44` → `t_b1b76921`). These are direct descendants of the release base; no other feature branch is merged.

Check-in support **requires botsesh.com server with check-ins** (planned B81.15). This plugin branch does not deploy or certify the server, and does not claim check-ins are live. A proposal is pending-only until its own person clicks Allow in the original room browser. Connection consent is not schedule consent. Scheduled inputs retain system authorship and read/post/pass-only tool ceilings.

The RC adds release metadata/documentation, updates the exhaustive tool inventory only for `botsesh_propose_checkin`, and makes the approval-control test harness inherit the real stock base template (stock 0.23.1 renders through `_EA_*` helpers). All existing approval action/deny assertions remain intact; no product approval behavior changes. Its check-in runtime bytes stay those of the reviewed source. The retained 0.7.2 post/read/replay behavior is not replaced with pending agreement work.

## Excluded work and expected conflicts

Agreement (✓) source `9fd1266124f74beb329fa97716c7b57f5aa4bd43`, the subsequent 11j receipt-handle work and review `t_c8667f7b` are **not included**. Neither `botsesh_agree` nor short receipt handles are promised by this candidate.

Expected overlap with that work (not a completed merge):

- `adapter.py`: service-policy admission and sender identity must survive agreement/receipt-context changes.
- `native_turn.py`: system-authored service sender selection must compose with exact current-input receipt handles; do not treat the approving person as the room author.
- `tool_policy.py`: keep both live service-input identity validation and the independent room-only execution veto. Agreement tool additions must not widen a scheduled turn.
- `oauth.py`: retain the `propose_check_in` route alongside agreement/post/receipt changes, without changing credential custody or settlement semantics.
- `tools.py`: union schema/handler additions in their exact paired order. Keep scheduled read/post/pass-only restrictions; a newly advertised tool is not automatically service-turn-authorized.
- `skills/botsesh/SKILL.md`, `README.md`, `CHANGELOG.md`: preserve pending-only/own-person consent, server prerequisite and cost disclosure while adding separately reviewed agreement guidance. Reconcile versions once at the final combined release.
- `tests/test_plugin.py`: preserve the exhaustive tool inventory and add only tools actually integrated. Re-run both check-in tests and the final agreement/handle regressions after any later merge.

Do not resolve these paths by taking either whole file. The later integration owner must consume the final reviewed agreement SHA, inspect both deltas and exercise their combined boundaries. `t_b15d695c` reviews this check-in-only RC; it is not a substitute for that later integration review.

## Verification and publication boundary

Run both Python suites from the repository root:

```sh
PYTHONPATH=/path/to/stock-hermes python3 -m unittest discover -s tests -v
PYTHONPATH=/path/to/stock-hermes python3 -P -m pytest --import-mode=append tests/controls -q
```

Use a fresh private HOME/HERMES_HOME and the unmodified stock CLI for an exact-commit native plugin install, then read back installed version, registration and every tracked source byte. No connection, account, model call, gateway start or browser gate is needed for that install smoke. Record exact native compatibility warnings, if any; do not suppress them.

A full Python suite plus an install smoke is not natural due-time native-model proof. This card authorizes only an additive push of the candidate branch: no tag, release, main advance, catalog change, live server deployment or installed-user-profile change. Publication requires separate authorization and final review.
