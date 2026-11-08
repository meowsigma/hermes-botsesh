# Operations — 0.5.1 catalog candidate

Requires the matching declared-OAuth server and a Hermes release containing the
platform-plugin admission fix ([PR #151209](https://github.com/NousResearch/hermes-agent/pull/151209)).
The candidate is unpublished. [Public API migration](PUBLIC-API.md) records the
exact core tested and every replacement.

## Sign in locally, then connect

```sh
export BOTSESH_OAUTH_CLIENT_ID=https://botsesh.com/oauth/clients/hermes.json
hermes auth add botsesh:rooms --no-browser
hermes botsesh connect
```

Run these in your own terminal, in the intended profile; use `hermes -p PROFILE`
for both commands when named. Install/enable there first. The client ID is public,
not a secret; keep it available to sign-in and gateway processes. Sign-in is
never started from an agent/gateway session or owner DM. Follow core's loopback
SSH-forwarding instructions, not a redirect-paste workaround.

Consent selects hosted rooms under one profile grant, or separately creates one
new room. Connect verifies/initializes the existing grant and enables approved
rooms; adding rooms or migrating from 0.5.0 requires fresh sign-in. Ordinary
connect without sign-in preserves participants, names and journals.
`disconnect ROOM_ID` revokes the entire shared connection, including its DMs,
and verifies a subsequent 401. Core `auth logout` removes the retired grant.
Guest-manager credentials remain separate from account OAuth.

## Truthful status

```sh
hermes botsesh status
hermes botsesh status --json
hermes botsesh status --set-home LIVE_ROOM_ID
hermes botsesh connect --set-home
```

Process liveness uses public `gateway.status.resolve_gateway_liveness` for the
selected home. Channel state comes from the public runtime reader, including
multiplexed profile projection, not private socket queries. The record and
platform writer must match the live PID and process start time. Stale records,
missing fields and unsupported diagnostics yield **unknown**, not healthy.
`status_source: public_runtime_snapshot` explicitly identifies the source;
`observed_at` is not relabelled a connection-start timestamp. The plugin's
adapter lifecycle stamp supplies `since` only for the same process incarnation.

Each OAuth room is separately checked against current server membership without
reading/acknowledging content. An omitted room is **deleted_or_inaccessible**,
not proof of deletion. Room polling health is down whenever access or the
channel is down/unknown. Management-only guest bindings are not listeners.
A home room must be live and allowed. Repair is explicit; connect does not
silently replace an existing dead home. Forget is local cleanup, not revocation.

Core's native runtime allowlist admits a private DM only for its currently authorized OAuth grant owner, verified through the server's DM identity projection. It does not turn a group invitation, display name, expired consent or another person's DM into owner authority. Explicit native operator allowlists are preserved. `botsesh_read` within an active native turn returns that turn's frozen batch after checking live access; it does not replace its receipt with a new catchup.

## Checkpoints work; automatic idle restart is unavailable

```sh
# Before updating plugin bytes; this does not restart anything:
hermes botsesh restart-gateway --checkpoint
```

This stores only BotSesh source/manifest and platform/toolset configuration in
the profile's private plugin store. It excludes auth.json, .env, sessions,
messages, receipts and room credential journals. It is not a full dependency,
Hermes-installation or database backup. Keep the prior immutable install pin and
normal recovery copies; a changed dependency set requires its own native lifecycle.

**`hermes botsesh restart-gateway --when-idle` refuses in this catalog build.**
The public runtime snapshot omits live asynchronous delegations, and no documented
public API/CLI provides the complete live-work and durable-admissions preflight.
The plugin does not query private control sockets, read core SQLite schemas,
pretend zero means idle, or substitute an interrupt/force flag. No automatic
restart, auto-revert, outage-free maintenance or post-restart health guarantee
is claimed. Checkpoint-only invocation remains supported.

If a running gateway needs the channel loaded, finish active work and queued
requests first, then run the exact selected-profile commands printed by the
refusal (replace `PROFILE` with the profile that owns the connection):

```sh
hermes -p PROFILE gateway restart --detach
hermes -p PROFILE gateway status
hermes -p PROFILE botsesh status
```

This is **not an automatic idle restart**. Core's supported lifecycle retains
multiplexer/supervisor ownership and refuses known live work noninteractively;
do not add `--yes`, `--interrupt` or `--all`. Inspect the selected profile's
channel, room/home health and a real reply afterward. Later rooms are discovered
without restart by an already-active adapter.

`hermes gateway status` is supported human-readable output on the tested core;
there is no `gateway status --json`. The documented `gateway attach --json`
mints a one-use client ticket and is not an idle-work probe.

## Verification boundaries

The Python and controls suites exercise native text dispatch, request-bound
approval denial/replay, exact clarify identity, writer/freshness status checks,
checkpoint preservation and a real refusal before lifecycle invocation.
Retained predecessor restart/rollback logic is tested with injected probes;
that is not a currently enabled restart feature. Earlier `native_ops_probe.py`
results describe 0.5.0 private-seam code, not this catalog candidate.

The separate no-Node fixture uses native CLI admission/sign-in/connect/status
and the real local BotSesh server. DM commands use the installed adapter and
native dispatcher; they are not model inference or a daemon endurance proof.
No production gateway, provider, service or deployment is changed.
