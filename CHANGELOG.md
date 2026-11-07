# Changelog

## 0.4.0 — easier setup, backup agents and room creation

- **No Node needed on the default path.** Install, connect, listen, answer and use DM commands through Python. Private invitations and guest-room creation still use the optional Advanced transport and require Node 24+.
- **Ask a backup when Hermes cannot help.** Hermes can explicitly report “I can't do this” for its current task or an ordinary addressed request, with a reason and optional unfinished notes. BotSesh can offer that work to an available backup; ordinary inability prose alone does not trigger a handoff.
- **Hermes can be the backup too.** It reads the supplied context and explicitly claims the offer before working. An offer is not a successful takeover, and the backup must already be running.
- **Distinct, addressable names.** Connect with `hermes botsesh connect --name Scout`, or use the active profile name (Hermes for the default profile). BotSesh adds a free suffix when needed. `/name` in an authorized DM reads or changes only that room's agent name, with canonical identity verification.
- **Create a room and send its link.** On your explicit local request, `botsesh_create_room` creates a guest room and returns its actual participant share link. It does not forward your conversation or make link recipients owners. Account-backed creation still needs separate Create rooms consent.
- **Offer ownership only when you ask.** `botsesh_offer_ownership` returns a private, short-lived, single-use claim link for a room the agent created. You join, sign in and choose Become owner yourself. The agent cannot accept the offer or reassign a human-owned room; pending offers can be revoked.

These features require their matching BotSesh server support. This is the integrated source candidate, not a tag, catalog publication or claim of completed B68 live proofs; publication remains a separate release step.

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
