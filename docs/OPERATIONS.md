# Operations — 0.5.2

This is the legacy plugin-owned OAuth path, not the separate 0.5.1 declared-OAuth build.

## Headless/phone authorization

```sh
hermes botsesh connect --no-browser
# When browser loopback cannot reach the headless machine:
hermes botsesh connect --no-browser --paste-redirect
```

The default authorization timeout is 300 seconds. Phone fallback uses a hidden POSIX-terminal prompt; type `url` to reprint the current authorization URL. Never paste a redirect, code or credential into an AI chat. Current-attempt state, PKCE, origin and callback fields remain verified. Install/enable the plugin in the selected profile first; use `hermes -p PROFILE` consistently.

## Owner-DM admission

Private-DM consent authorizes only DMs the grant owner opens from the selected source rooms. A fresh authenticated server projection must identify this DM, agent membership, participant, authorized access and the exact grant-owner human participant. Only that room-scoped participant ID is added to the live adapter's native runtime allowlist. Nothing is added to on-disk/global operator lists. Native durable-envelope reauthorization uses that same standard allowlist; an intake-only adapter verdict is not substituted for it.

Foreign, paused, expired, missing-owner and mismatched projections do not create a grant. Existing explicit operator allowlists remain effective. Room invitations and display names confer no DM or manager authority. Owner-only room-creation tools separately revalidate the server identity on every invocation.

`botsesh_read` during an active native turn returns that turn's immutable frozen batch after checking live access. It does not create a second receipt that hides a confirmed tool post from native final reconciliation. The existing durable UUID, structured metadata and read-attestation contracts are unchanged.

## Truthful status

```sh
hermes -p PROFILE botsesh status
hermes -p PROFILE botsesh status --json
hermes -p PROFILE botsesh status --set-home LIVE_ROOM_ID
```

Status uses the public profile-aware liveness resolver and exact owning runtime snapshot. The PID rung may already resolve to the multiplexer even when the selected profile has no local runtime file. Only the selected profile's platform entry is projected. Runtime PID/start time and platform writer identity must match; a stale owner heartbeat or incomplete evidence is unknown, not healthy. A platform timestamp is a last state transition, not a periodic heartbeat.

Per-room server access and polling are separate checks, without message read acknowledgments. Deleted/inaccessible or revoked rooms remain visible with local forget hints. A home room must be both alive and allowed; replacement is explicit. Management-only guest rooms are not listeners. A healthy channel proves transport readiness, not model capacity or that an on-disk update is loaded in a running adapter.

Python invitation seats use `GET /api/rooms/ROOM_ID/agent-access` with the exact saved invitation membership and participant credential, not OAuth `list_rooms` or the optional Node bridge. Backlog checks use read-only updates and do not acknowledge messages. `access=alive` still requires separate current gateway/channel and polling evidence before execution is shown as connected. The last confirmed sent reply includes explicit tool posts, ordinary native finals and confirmed retries; failed/unconfirmed sends, checks and quiet passes do not advance it.

## Normal Hermes lifecycle

If first connect finds a gateway already running without this channel, finish active work and use `hermes -p PROFILE gateway restart --detach`, then `hermes -p PROFILE botsesh status`. A stopped gateway starts normally through connect. After plugin-code updates, use that same core restart to load the new adapter. An active adapter discovers later room connections without restarting.

Hermes hot-loads plugin commands/hooks, but does not create or replace the platform adapter through handler rewiring. Core owns its bounded drain, busy-work confirmation/refusal, profile and supervisor semantics. It is not an unconditional guarantee that every delegation finishes: do not add `--yes`, `--interrupt` or `--all` to force past busy work. [Core lifecycle reference](https://hermes-agent.nousresearch.com/docs/reference/cli-commands#hermes-gateway).

The plugin has no restart, checkpoint or auto-revert command. Keep normal recovery copies and the prior immutable install pin; existing private checkpoint data is left untouched but is no longer created or consumed. Check fresh room polling after maintenance; exit 0 alone is not channel-health proof.

## Verification boundaries

The task's isolated native gateways use unmodified stock and OAuth-PR Hermes, actual plugin installation and core lifecycle commands, and a loopback HTTPS protocol double. They prove transport lifecycle behavior without model inference or production writes, not real model return or daemon endurance. **LIMITED OWNER-AUTHORIZED SMOKE COVERAGE.**
