# Operations: headless OAuth, truthful status, idle-aware restart

This increment addresses the owner's **2026-11-07 goonbot field report**, items 3–5. It is not a claim of production or physical-phone verification.

## Exact field asks

> Callback listener answered `501` to the redirect when a client replayed the 302 as POST, then sat silently until the 180s timeout with no hint about what happened. Accept POST on /callback (or 301-redirect POST to a GET path), and on timeout print what was received.

> 180s is tight when the owner is on a phone and the URL is 400 chars. 300s, and a "re-print URL" affordance.

> Success output is great ("Connected to goon sesh", config writes echoed). Keep that.

> `hermes botsesh status` said `Hermes room · axAsOLki... · invalid_grant` forever: a room that no longer exists still occupies the slot, and it never tells you the channel is dead *in the running gateway* vs *not connected at all*. Its own footer even admits "Connected is access status, not proof of a running gateway", so print the gateway-side truth: connected channel yes/no, which profile, which rooms, last inbound/outbound event timestamps. Also validate `home_channel` on connect, mine pointed at the deleted room and nothing warned.

> The "restart the gateway once to load the channel" note is in after-install.md, right behavior, but on a 24/7 inference box a bare "restart it normally" is how owners get an outage. Ship `hermes botsesh restart-gateway --when-idle` (drain + idle gate + auto-revert) or at minimum document the pattern. I had to write my own drain waiter (a Hermes skill template existed but wasn't discoverable from the plugin docs).

## Already present in v0.4.0

`connect --name` already selects an addressable profile-derived room name. The `botsesh_create_room` tool already exists, including Python guest creation and explicit ownership offers. These did **not** supply a `create-room` CLI, profile fan-out, idempotent reconnect identity, or idle-aware restart. Other HB050 lanes own those CLI/identity gaps; this lane does not change them or bump the release version.

## Headless/phone authorization

```sh
hermes botsesh connect --no-browser
# If browser loopback cannot reach the headless machine:
hermes botsesh connect --no-browser --paste-redirect
```

Default timeout: 300 seconds; `--timeout SECONDS` remains available. `/callback` accepts GET, POST replaying the query, and form-encoded POST bodies. Duplicate OAuth fields fail closed. Callback logging is disabled; diagnostics retain the latest 20 method/path + known-field presence observations, not query/body values or error descriptions.

Phone mode has a hidden POSIX-terminal prompt. Type `url` to re-print the authorization URL. Paste the **current attempt's exact redirect URL**, including the random port. It verifies origin, port, path, state and duplicates before token exchange; PKCE still binds the code. Never paste into AI chat. The input reader is synchronous and restores terminal echo on success, timeout or cancellation; it cannot linger and steal a later shell command. Non-TTY and non-POSIX paste mode refuse safely. Use loopback SSH forwarding there. Cores whose private callback-helper signatures changed retain native fallback with an explicit warning; upgrade those cores for this increment's POST diagnostics/paste behavior.

## Status and home room

```sh
hermes botsesh status
hermes botsesh status --json
hermes botsesh status --set-home LIVE_ROOM_ID
hermes botsesh connect --set-home
```

Process liveness comes from Hermes's profile-aware ownership resolver. Channel status comes from the owning live gateway control socket and its exact profile/platform writer identity—not a saved room token or successful HTTP request. A live process whose channel is absent/down is distinct from an absent process; unanswerable/unsupported diagnostics say **unknown**, not healthy. `since` comes from this plugin's adapter lifecycle stamps with a process-incarnation check; old or crashed runtimes may have no proven start/stop time. Native `updated_at` is only a last observation, never relabelled a connection-start timestamp.

Each OAuth room is checked against its own live server membership, without reading/acknowledging messages. `alive`, explicit `deleted`, `invalid_grant` and other safe server codes are exposed. An omitted room is **deleted_or_inaccessible**, because absence alone cannot prove which happened. Room polling health is down whenever access or the live channel is down. Python management-only bindings keep their separate membership-verified / no-gateway status. Advanced Node invitation bindings are not spawned just to answer status.

A home room must be a live, allowed OAuth room. Connect/status warn and show exact live candidates for an explicit `--set-home` change; they never silently reassign an existing home. Dead room hints name `hermes botsesh forget ROOM_ID`, supplied by the sibling CLI-management lane. Forgetting is local cleanup, not revocation.

## Update/restart pattern

On an installation that already has this command:

```sh
# BEFORE applying the approved plugin update (never after the bad change):
hermes botsesh restart-gateway --checkpoint
# Apply the approved plugin update through Hermes's plugin lifecycle.
hermes botsesh restart-gateway --when-idle --max-wait 300 --health-timeout 60
```

Connect refreshes a config-only pre-change checkpoint on **each connect**, so a failed later connect/update does not restore the stale config from before earlier rooms were connected. Explicit `--checkpoint` additionally captures plugin runtime source/manifest, for a version rollback; automatic connect checkpoints never replace that operator-prepared source checkpoint. Checkpoints are private in this profile's existing 0700/0600 plugin store. They never include auth.json, .env, sessions, room messages, receipts or token journals. First upgrading from v0.4.0 cannot retroactively capture an old code checkpoint with a command v0.4.0 did not have: retain the prior immutable install pin/backup separately. A missing checkpoint refuses a restart instead of promising a fictional rollback.

The bounded gate waits for live unscoped `active-work` (including delegations), each served authority's queued/started/unknown durable admissions, unresolved local sends/receipts/outboxes, and **alive** active OAuth-room server inbox backlog to reach zero. Non-live server inboxes (including deleted/inaccessible rooms and `invalid_grant`) are reported as skipped, not treated as drainable queues. Their unresolved **local** sends/receipts/outboxes still gate restart. Unknown/invalid counters for a live inbox refuse without restart. It does not discard backlog to manufacture idle. `--max-wait` bounds polling; individual live/network probes have their own bounded timeouts. `--restart-timeout` bounds the lifecycle subprocess.

The only restart operation is **`hermes gateway restart`**, with `HERMES_HOME` explicitly bound to the selected profile (including a saved BotSesh profile choice), stdin closed and **without** `--yes`, `--interrupt`, `--all`, guessed services, or custom signals. Hermes rechecks active work and owns its drain/relaunch semantics. On multiplexed installs, Hermes can hot-restart only the named profile instead of stopping other profiles. Immediately before restart, the command snapshots room/home liveness. Health success requires an up channel for a new process incarnation or fresh adapter lifecycle, plus new connected polling for **every room that was alive before restart**. A previously live home must remain that same live home. Already-dead rooms or home produce explicit warnings, not a false rollback; they remain dead in status. If no room was live before restart, no room is required to become live, but the new channel must still be up. Exit code zero alone never proves health.

If the candidate fails health, recovery first waits again for active work/durable queues/local pending work, or proves the gateway stopped. Recovery deliberately does not require the broken candidate to drain the remote inbox. It compares the attempted source/config before any restore; concurrent edits abort recovery rather than being overwritten. Only checkpointed BotSesh platform/toolset config and plugin runtime files are restored. The durable room/token/session stores stay intact. A second supported restart is health-checked against the **original pre-restart baseline**, not the failed candidate's degraded room set. A recovered rollback returns a nonzero `candidate_failed_auto_revert_healthy` result so scripts cannot mistake a failed update for success. Unhealthy recovery or unsafe/unavailable rollback is reported explicitly; the checkpoint is retained.

### Important safety limits

Hermes currently exposes a live-work probe and lifecycle refusal, but **not an atomic global idle-and-restart admission fence** or a complete count of every legacy in-memory messaging queue. Another admission can race the preflight; Hermes's second work check narrows but does not eliminate that race. This command is **idle-aware, not a certified zero-interruption/zero-outage guarantee**. For strict maintenance exclusion, quiesce external traffic through the operator's existing maintenance controls before using it. The plugin does not invent a private signal protocol, silently pause all profiles or bypass an unknown queue. No host-Hermes changes were made here.

Checkpoints contain Python runtime/manifest and Advanced bridge/vendor files, not a full git/installation/dependency transaction. Same-source plugin code updates are the supported rollback scope; moving install paths, changing dependencies or upgrading Hermes itself needs its own existing lifecycle/backup procedure. Source rollback leaves newly introduced, unreferenced files in place, and refuses unsafe paths. Shared source used by several profiles must be updated by its one operator/integration owner, not competing per-profile updaters.

## Verification boundary

The focused OPS suite uses real loopback GET/POST callbacks, hidden pseudo-terminal input, status/work/socket doubles, real isolated config/source restoration, read-only SQLite queue probes and simulated successful-restart/failed-candidate/recovery sequences. Regression fixtures retain the deleted/inaccessible and invalid-grant rooms and dead home: pending local work still delays restart, pre-existing dead state does not trigger rollback, a newly lost live room does, and recovery must restore the original live set. Successive connect checkpoints are restored to verify earlier rooms survive; explicit source checkpoints remain unchanged. A separate subprocess uses the installed native `python -m hermes_cli.main` CLI with isolated HOME/HERMES_HOME, **an empty PATH (no Node)** and a real local OAuth/MCP fixture: connect via POST, persist configuration, and read status back. No production account/room writes, inference, real gateway/service restarts, physical-phone tests or overnight claims.

Lifecycle source/docs research: [CLI commands](https://hermes-agent.nousresearch.com/docs/reference/cli-commands), [gateway monitoring](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-monitoring), installed `gateway/control_socket.py`, `gateway/run_active_work_verb.py`, `gateway/status.py`, `hermes_cli/gateway_runtime.py`, `hermes_cli/gateway_profile_lifecycle.py` and `hermes_cli/gateway_lifecycle_guards.py`.
