# Provenance of the vendored BotSesh SDK

Source: <https://botsesh.com/botsesh-agent.mjs>, retrieved 2026-11-04.
The matching live `botsesh-agent.mjs.sha256` sidecar was retrieved separately.

Unmodified upstream SHA-256:

```text
960c6e4e2746fb06ed93915ad3f611010a2a64117de4c04bf8194dc61cf93da8
```

The original sidecar is retained as `UPSTREAM.sha256`. The bundled file is source, not a binary, and is reviewed/pinned with this plugin. It is never updated at runtime. SDK build metadata alone is not the source identity; this byte digest is.

**One additive change:** `BotSeshAgent.pluginCatchUp()` calls the existing private authenticated request machinery for the documented `/catch_up` endpoint, validates room/schema/read_effect and continuation consistency, and accumulates at most 100 pages. It never acknowledges. The method uses only the real server protocol. All other upstream SDK bytes are unchanged. `docs/SDK-SEAM.diff` contains the exact original→bundled patch.

The wrapper activates only the fixed operations in `bridge.mjs`. The canonical SDK also contains standalone CLI/runtime/service/keyring helpers; those code paths are not invoked by this plugin. In particular it does not run another Hermes, install a service, import another application's login or use the SDK's OS-keyring backend. Own generated access is stored under the active profile's plugin data directory, as explicitly disclosed in README.

BotSesh source is maintained by meowsigma; the standalone plugin and vendored client are distributed under the repository's MIT license.
