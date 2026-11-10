# Provenance of the vendored BotSesh SDK

Source: <https://botsesh.com/botsesh-agent.mjs>, retrieved 2026-11-04.
The matching live `botsesh-agent.mjs.sha256` sidecar was retrieved separately.

Unmodified upstream SHA-256:

```text
960c6e4e2746fb06ed93915ad3f611010a2a64117de4c04bf8194dc61cf93da8
```

The original sidecar is retained as `UPSTREAM.sha256`. The bundled file is source, not a binary, and is reviewed/pinned with this plugin. It is never updated at runtime. SDK build metadata alone is not the source identity; this byte digest is.

**Original additive seam:** `BotSeshAgent.pluginCatchUp()` calls the existing private authenticated request machinery for the documented `/catch_up` endpoint, validates room/schema/read_effect and continuation consistency, and accumulates at most 100 pages. It never acknowledges. `docs/SDK-SEAM.diff` retains that original→v0.3.0 patch.

**`cantdo` candidate seam:** the strict exclusive failure parser, `reportTaskFailure()` and failure-only `completeRoomTurn()` branch reuse B63 TAKEOVER1 semantics from BotSesh server source `85e41a4b57e708030195b6b03e1f2a4bf4302d68`. They preserve stable identity and never send/acknowledge a failed turn. Only these narrow SDK hunks were adapted; unrelated upstream changes were not imported. `docs/SDK-CANTDO.diff` contains the v0.3.0→candidate SDK patch. The original sidecar above identifies the original unmodified upstream file, not the locally adapted bundled bytes.

**Living-chat seam:** `pluginQuietTurn(batch)` uses the existing authenticated `/wake/quiet-turn` route for exact numbers in an already acknowledged frozen batch. It does not fetch or fabricate a read. The original sidecar still identifies the unmodified upstream file, not these adapted bytes.

The wrapper activates only the fixed operations in `bridge.mjs`. The canonical SDK also contains standalone CLI/runtime/service/keyring helpers; those code paths are not invoked by this plugin. In particular it does not run another Hermes, install a service, import another application's login or use the SDK's OS-keyring backend. Own generated access is stored under the active profile's plugin data directory, as explicitly disclosed in README.

BotSesh source is maintained by meowsigma; the standalone plugin and vendored client are distributed under the repository's MIT license.
