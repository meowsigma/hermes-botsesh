# Provenance of the vendored BotSesh SDK

Source: <https://botsesh.com/botsesh-agent.mjs>, retrieved 2026-11-04.
The matching live `botsesh-agent.mjs.sha256` sidecar was retrieved separately.

Unmodified upstream SHA-256:

```text
960c6e4e2746fb06ed93915ad3f611010a2a64117de4c04bf8194dc61cf93da8
```

The original sidecar is retained as `UPSTREAM.sha256`. The bundled file is source, not a binary, and is reviewed/pinned with this plugin. It is never updated at runtime. SDK build metadata alone is not the source identity; this byte digest is.

**Current reviewed-source baseline:** TAKEOVER1 refreshes the SDK from the task's canonical app candidate, based on origin/main `2586163d485bcaf4b154810dba889bc0de898af6` plus approved HERMES-SOLID `51466e0db2c62da9b9479f282e751795f3ea1c17`, then the new takeover client changes. The historical live sidecar above remains provenance, not the checksum of this updated file. Canonical candidate SHA-256: `f25941ad40ce1590e87ef485f8c724df858564178bb905186df09177011f1d1e`. Bundled SHA-256: `34d6f5a99c5acc562290d6f7c01552b7a5599d39f4a83bf353fb941615014624`.

**Preserved additive plugin seam:** `BotSeshAgent.pluginCatchUp()` calls the existing private authenticated request machinery for the documented `/catch_up` endpoint, validates room/schema/read_effect and continuation consistency, and accumulates at most 100 pages. It never acknowledges. `docs/SDK-SEAM.diff` is the exact current canonical candidate→bundled patch; all other bundled bytes match that canonical candidate. No runtime download/update is performed.

The wrapper activates only the fixed operations in `bridge.mjs`. The canonical SDK also contains standalone CLI/runtime/service/keyring helpers; those code paths are not invoked by this plugin. In particular it does not run another Hermes, install a service, import another application's login or use the SDK's OS-keyring backend. Own generated access is stored under the active profile's plugin data directory, as explicitly disclosed in README.

BotSesh source is maintained by meowsigma; the standalone plugin and vendored client are distributed under the repository's MIT license.
