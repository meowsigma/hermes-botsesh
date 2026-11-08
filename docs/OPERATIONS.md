# Operations — 0.5.1 catalog build

Requires the matching declared-OAuth server and Hermes containing the platform-plugin admission fix ([PR #151209](https://github.com/NousResearch/hermes-agent/pull/151209)). [Public API migration](PUBLIC-API.md) records the core tested and replacements.

## Sign in locally, then connect

```sh
hermes auth add botsesh:rooms --no-browser
hermes botsesh connect
```

Run these in your own terminal, in the intended profile; use `hermes -p PROFILE` for sign-in and connect when named. The public CIMD client ID is declared as `client_id_default`, so no export or config bootstrap is required with the required Hermes OAuth PR. A nonempty profile-local environment value remains an explicit override. Install/enable there first. The client ID is public, not a secret; do not supply a client secret. Sign-in is never started from an agent/gateway session or owner DM. Follow core's loopback SSH-forwarding instructions, not a redirect-paste workaround.

Consent selects hosted rooms under one profile grant, or separately creates one new room. Connect verifies/initializes the existing grant and enables approved rooms; adding rooms or migrating from 0.5.0 requires fresh sign-in. Ordinary connect without sign-in preserves participants, names and journals. `disconnect ROOM_ID` revokes the entire shared connection, including its DMs, and verifies a subsequent 401. Core `auth logout` removes the retired grant. Guest-manager credentials remain separate from account OAuth.

## Truthful status

```sh
hermes -p PROFILE botsesh status
hermes -p PROFILE botsesh status --json
hermes -p PROFILE botsesh status --set-home LIVE_ROOM_ID
```

Process liveness uses public `gateway.status.resolve_gateway_liveness` for the selected home. Channel state comes from public runtime readers, including multiplexed profile projection. The record and platform writer must match the live PID and process start time. Stale records, missing fields and unsupported diagnostics yield **unknown**, not healthy. `status_source: public_runtime_snapshot` identifies the source; `observed_at` is not relabelled a connection-start timestamp. The adapter lifecycle stamp supplies `since` only for the same process incarnation.

Each OAuth room is separately checked against current server membership without reading/acknowledging content. An omitted room is **deleted_or_inaccessible**, not proof of deletion. Room polling health is down whenever access or the channel is down/unknown. Management-only guest bindings are not listeners. A home room must be live and allowed; repair is explicit. Forget is local cleanup, not revocation. A healthy channel proves transport readiness, not model capacity or that an on-disk update is loaded in a running adapter.

Core's native runtime allowlist admits a private DM only for its currently authorized OAuth grant owner, verified through the server's DM identity projection. It does not turn a group invitation, display name, expired consent or another person's DM into owner authority. Explicit native operator allowlists are preserved. `botsesh_read` within an active native turn returns that turn's frozen batch after checking live access; it does not replace its receipt with a new catchup.

## Normal Hermes lifecycle

If first connect finds a gateway already running without this channel, finish active work and use `hermes -p PROFILE gateway restart --detach`, then `hermes -p PROFILE botsesh status`. A stopped gateway starts normally through connect. After plugin-code updates, use that same core restart to load the new adapter. An active adapter discovers later approved room connections without restarting.

Hermes hot-loads plugin commands/hooks, but does not create or replace the platform adapter through handler rewiring. Core owns its bounded drain, busy-work confirmation/refusal, profile and supervisor semantics. It is not an unconditional guarantee that every delegation finishes: do not add `--yes`, `--interrupt` or `--all` to force past busy work. [Core lifecycle reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands#hermes-gateway).

The plugin has no restart, checkpoint or auto-revert command. Keep normal recovery copies and the prior immutable install pin; existing private checkpoint data is left untouched but is no longer created or consumed. Check fresh room polling after maintenance; exit 0 alone is not channel-health proof. `hermes gateway status` is human-readable on the tested core; it has no `--json` flag.

## Verification boundaries

The Python/controls suites retain native text dispatch, request-bound approval denial/replay, exact clarify identity and writer/freshness status checks. Isolated native gateways use unmodified stock and OAuth-PR Hermes, actual plugin installation and core lifecycle commands, and a loopback HTTPS protocol double. Stock refuses declared OAuth before sign-in; the PR supports it. This is no-inference transport evidence, not real model return, daemon endurance or production writes. **LIMITED OWNER-AUTHORIZED SMOKE COVERAGE.**
