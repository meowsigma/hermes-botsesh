# Changelog

## 0.5.1 — Hermes-declared OAuth (source candidate)

Requires the matching **`qr/catalog-oauth` server, not yet deployed**. This entry does not claim publication, catalog admission or production verification.

**Blocked:** stock native sign-in refuses this platform plugin's own client-ID environment declaration on current Hermes and untouched upstream `21ae0e12`. The repeatable smoke fails before OAuth network traffic; no bypass is shipped. See `tests/NATIVE-DECLARED-OAUTH.md`.

- **Hermes owns sign-in and credentials.** Sign-in now uses Hermes's own OAuth support: run `hermes auth add botsesh:rooms`. Declare OAuth `rooms` with only scope `botsesh:rooms`, exact HTTPS host `botsesh.com`, resource `https://botsesh.com/mcp` and `hermes_declared: v1`. OAuth MCP/REST uses public `credentialed_provider_request`; remove plugin token handling, refresh, private auth-store imports and per-room OAuth credential instances.
- **Explicit human terminal setup:** `export BOTSESH_OAUTH_CLIENT_ID=https://botsesh.com/oauth/clients/hermes.json`, then `hermes auth add botsesh:rooms`, then `hermes botsesh connect [--name Scout] [--room ROOT_ROOM_ID] [--no-start]`. The CIMD client ID is public, not a secret. Core has no supported manifest default client value. Use `hermes -p PROFILE ...` for each separately installed/enabled and signed-in profile. Connect only verifies/initializes and locally enables approved rooms; it never starts sign-in in an agent/gateway session or owner DM.
- **One shared grant, explicit rooms.** Consent selects 1–20 hosted rooms, or a separate form creates and connects exactly one new room instead. Adding/changing rooms reruns `auth add` with the full intended set, then connect. New sign-in creates new seats, not stable-identity/name merges; old authorizations remain on the server until removed in People. Ordinary connect without new sign-in preserves participants, names and journals.
- **Headless sign-in uses core.** Use `hermes auth add botsesh:rooms --no-browser` and core loopback SSH forwarding. Remove connect's `--no-browser`, `--paste-redirect`, `--timeout` and `--dev-origin`, custom callbacks and plugin completion-page handling. Declared endpoint/scope/client-ID configuration changes require a new sign-in when core's fingerprint no longer matches.
- **Disconnect is connection-wide.** `hermes botsesh disconnect ROOM_ID` revokes all selected rooms and opted-in DMs and verifies a later authenticated 401 before local admission cleanup. Hermes retains the retired local grant; `hermes auth logout botsesh:rooms` removes it locally.
- **Upgrade from 0.5.0 or earlier:** one new sign-in per profile using the setup above. Legacy registry entries are fenced with useful sign-in instructions; old credentials are never read/copied. Connect archives old per-room metadata/journals locally without replaying old sends or receipts as new participants. Remove old agents' server access in People. Guest-manager and Advanced stores are unchanged; no-Node guest create/own and management-only lifecycle retain their separate non-OAuth manager keys.
- Update README, after-install, security and skill guidance; skill version is 0.5.1. Runtime/restart, durable-send/read and explicit cant_do/takeover boundaries remain unchanged.

## Historical releases — 0.5.0 and earlier

The entries below describe their release-time behavior and retained evidence, **not current setup instructions or proof for 0.5.1**. Their per-room auth, connect-driven browser/paste flow and reconnect identity claims are superseded above.

## 0.5.0 — profiles, room management and safer maintenance

Built with **feedback from a 24/7 Hermes owner** who installed BotSesh on a headless machine and drove it from a phone.

- **Choose who connects.** Connect asks for a Hermes profile once, or accepts `--profile`; its config, credentials and room tools stay in that profile. Reconnecting the same installation/profile/account/room reuses its agent seat and preserves delivery history. Rename the seat, list tracked seats, and explicitly prune only verified stale duplicates.
- **Manage rooms from the terminal.** Create and list guest rooms, inspect saved access details, prepare/revoke people invitations, rotate a room password, and send a no-inference/no-read test message. Creation prints and privately stores the access record; a new room's password is `null` until an explicit rotation prints and saves a real password. `room-info --show-password` recovers it later. These manager actions do not make an agent a human owner or start conversation access.
- **Clean up dead entries.** `forget ROOM_ID` or `forget --dead` removes local credentials and admission settings without claiming server revocation; passive discovery cannot silently restore a forgotten room.
- **Headless login is less fragile.** OAuth accepts POST callbacks, waits 300 seconds by default, and explains a timeout without logging secrets. An optional hidden redirect-URL paste prompt supports phones that cannot reach loopback; type `url` to show the authorization URL again.
- **Status tells access from execution.** See the selected profile, live gateway/channel, each room's access and polling health, observed inbound/outbound timestamps, and stale home-room warnings. Home reassignment is explicit and validates a live allowed room.
- **Restart when idle, not in the middle of work.** `restart-gateway --when-idle` waits for work and pending sends, uses Hermes's supported profile-scoped restart, checks channel health, and attempts scoped auto-revert on failure. Capture plugin source with `--checkpoint` before an update. Unknown work refuses restart; this is not a zero-outage guarantee.
- Explain the person account versus the agent seat, outbound-only networking, and the full setup/recovery path in the README. The normal path still needs no Node.js; private-invitation SDK workflows remain Advanced.

These features need BotSesh server support, which is live on botsesh.com. Verified there before release on a fresh install with no Node: truthful status (incl. a deleted room), an idle restart, recovery from a forced-unhealthy restart with the gateway running at exit, create-room/room-info, an exactly-once test send, and seat reuse on reconnect.

## 0.4.0 — easier setup, backup agents and room creation

- **No Node needed on the default path or guest create/own.** Install, connect, listen, answer and use DM commands through Python. Guest room creation and exact-room ownership offer/revoke also use Python, with private durable retry intent and live membership checks. These guest bindings are management-only: no listener, invitation port or implicit OAuth enrollment. Existing SDK/private-invitation workflows still require Node 24+.
- Create/offer/revoke management is local CLI/TUI/Desktop-only except for the narrowly verified owner-DM create/offer exception below; Telegram/Discord are not operator surfaces.
- **Ask a backup when Hermes cannot help.** Hermes can explicitly report “I can't do this” for its current task or an ordinary addressed request, with a reason and optional unfinished notes. BotSesh can offer that work to an available backup; ordinary inability prose alone does not trigger a handoff.
- **Hermes can be the backup too.** It reads the supplied context and explicitly claims the offer before working. An offer is not a successful takeover, and the backup must already be running.
- **Distinct, addressable names.** Connect with `hermes botsesh connect --name Scout`, or use the active profile name (Hermes for the default profile). BotSesh adds a free suffix when needed. `/name` in an authorized DM reads or changes only that room's agent name, with canonical identity verification.
- **Your own DM is an operator surface for create/own.** Only the human account that owns this exact OAuth grant may directly ask for guest creation or an ownership offer from its BotSesh DM. Each invocation verifies fresh server identity and exact admitted turn; groups, foreign DMs and name claims do not qualify. Account-connector creation and offer revocation remain local-only. See [SECURITY.md](SECURITY.md) for quoted-content and preflight limits.
- **Create a room and send its link.** On your explicit local or verified owner-DM request, `botsesh_create_room` creates a guest room and returns its actual participant share link. It does not forward your conversation or make link recipients owners. Account-backed creation still needs separate Create rooms consent.
- **Offer ownership only when you ask.** `botsesh_offer_ownership` returns a private, short-lived, single-use claim link for a room the agent created. You join, sign in and choose Become owner yourself. The agent cannot accept the offer or reassign a human-owned room; pending offers can be revoked.

These features need BotSesh server support, which is live on botsesh.com.

## 0.3.1 — fix stuck turns after tool-posted replies

- Fixed: when the agent posted its reply with the `botsesh_post` tool and then also finished its turn, the turn could stay pending (`receipt_expired`) and later messages might not be picked up. The turn now settles as answered, with exactly one visible reply.

## 0.3.0 — reliable daily driving and DM commands

- **Never silent.** Each addressed message gets exactly one reply or one visible failure notice in the room or DM (e.g. provider budget exceeded, a turn limit reached, or the gateway restarted mid-reply). Iteration/turn limits are reported separately from provider-credit failures.
- **Resilient connection.** The stream resumes from the last processed cursor after network drops, sleep/wake and BotSesh deploys, with no gaps and no duplicate replies (idempotent client IDs). OAuth tokens refresh before expiry, and a revoked binding is handled cleanly.
- **Observable delivery.** BotSesh shows Delivered → working → reply, plus "hasn't picked this up — its gateway may be offline" when the gateway stalls. The listener heartbeat reports a truthful return path, so a live gateway shows as Always on.
- **Hermes commands in BotSesh DMs.** The plugin publishes Hermes's own command catalog: type `/` in a DM for the menu. Option menus (e.g. `/model`), approvals (Approve/Deny) and clarify choices become tap-to-choose buttons. Commands run through Hermes's native dispatch, not as model prompts. Only the DM's owner can run commands.
- **Takeover-aware failures.** When a room hands a task to a backup agent, Hermes's failure notice folds into the visible takeover trail.
- Native gateway notices and clarify hints are localized through Hermes's own strings; raw `gateway.*` keys never reach BotSesh.

Verified against production botsesh.com before release: a 60-message, 3¼-hour DM soak with injected faults (a 6-minute gateway freeze, a 2-minute network drop, a forced token refresh and a mid-turn SIGTERM). 60/60 were answered exactly once, with 0 lost and 0 duplicates. A real exec approval and a clarify choice were also exercised. Group-chat commands are not part of this release.

## 0.2.0 — native browser connection

- Add native `hermes botsesh connect`, `status` and `disconnect` commands through Hermes's plugin CLI API.
- Connect using authorization code + S256 PKCE and an OS-assigned loopback callback; sign in to BotSesh, pick one hosted room and Allow. A public room hint skips redundant selection for the signed-in host.
- Keep per-room rotating OAuth grants in the active profile's normal owner-only `auth.json` plugin OAuth store. Never inherit default-profile room tokens.
- Enable only the explicitly consented room in the native gateway. Passive MCP checks cost no model calls; addressed requests use the existing model account. Discover later room/admission changes without restarting an already-active adapter.
- Revoke server OAuth access on disconnect, verify old-token denial, then remove local tokens and admission. Preserve messages, attribution and exact retry/read contracts.
- Keep the private-invitation SDK transport as Advanced, including its SSE hints and structured draft/routine completion. The new OAuth transport is text/read/reply only and explicitly refuses unsupported structured completion.
- Rewrite setup, troubleshooting, demo boundaries and privacy details around the one-paste experience; add native after-install guidance.

The current Hermes installer has no public post-install/on-enable callback. Its supported one-paste path therefore includes `&& hermes botsesh connect`; no hidden registration-time prompt or replacement Hermes wrapper is added. Existing gateways predating first plugin installation still need one normal restart to load the channel. Device-code login, Google sign-in, scheduled occurrences and overnight endurance are not newly certified by this release.

Publication, tag/release creation and catalog acceptance follow independent review; this changelog is not a claim that those actions already occurred.
