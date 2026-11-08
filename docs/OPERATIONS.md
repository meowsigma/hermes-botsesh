# Operations — released 0.5.0 and the 0.5.2 owner-DM hotfix

0.5.0 is released on `main`. The 0.5.2 hotfix branch must be independently reviewed and published before an unpinned installation receives it. This is the legacy plugin-owned OAuth path, not the separate 0.5.1 declared-OAuth candidate.

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

Status uses the public profile-aware liveness resolver and exact owning runtime snapshot. The PID rung may already resolve to the multiplexer even when the selected profile has no local runtime file; the public multiplexer resolver supplies the owner record in that case. Only the selected profile's platform entry is projected. Runtime PID/start time and platform writer identity must match; a stale owner heartbeat or incomplete evidence is unknown, not healthy. A platform timestamp is a last state transition, not a periodic heartbeat.

Per-room server access and polling are separate checks, without message read acknowledgments. Deleted/inaccessible or revoked rooms remain visible with local forget hints. A home room must be both alive and allowed; replacement is explicit. Management-only guest rooms are not described as listeners. Connect reads actual channel state before and after startup assistance rather than reporting a working profile down from a `gateway ensure` deadline.

## Checkpoint and supported restart fallback

```sh
# Before an approved plugin update; no restart:
hermes -p PROFILE botsesh restart-gateway --checkpoint
hermes -p PROFILE botsesh restart-gateway --when-idle
```

The second command reports that a complete automatic idle-work/admission preflight is unavailable and prints the exact selected-profile maintenance commands. It does not restart, infer a zero queue from runtime counters, or claim automatic rollback. The prior private-socket/schema preflight is not a supported CLI gate in this hotfix.

After active work and queued requests have finished, run those commands in your own terminal:

```sh
hermes -p PROFILE gateway restart --detach
hermes -p PROFILE gateway status
hermes -p PROFILE botsesh status
```

This is **not an automatic idle restart** or a zero-interruption guarantee. Core retains multiplexer/supervisor ownership and refuses known live work noninteractively. Do not add `--yes`, `--interrupt`, `--all`, guessed service names or signals. Inspect the selected profile's new channel lifecycle, fresh room/home polling and a real reply afterward. An already-active adapter discovers later rooms without restart.

Checkpoint-only invocation preserves BotSesh source/manifest and platform/toolset configuration in the profile's private store. It excludes auth.json, .env, sessions, messages, receipts and credential journals; it is not a full installation, dependency or database backup. Keep the prior immutable pin and normal recovery copies. Historical injected-probe restart/rollback tests remain available, but do not prove an enabled automatic restart feature.

## Verification boundaries

Use the focused owner-DM native-envelope authorization, writer/profile status and read/post/final regressions. The task's isolated native named-profile evidence separately records actual OAuth, no-Node installation, supported profile hot restart and real bounded Luna output. Physical-phone, exhaustive compatibility and production deployment certification are not implied. **LIMITED OWNER-AUTHORIZED SMOKE COVERAGE.**
