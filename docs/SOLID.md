# SOLID reliability branch (unreleased)

**Historical predecessor design and evidence.** The refresh timing, branch and
pending-proof statements below describe the original SOLID lane, not v0.5.1.
For current declared OAuth, Hermes core owns credentials/refresh; one profile
grant covers the explicitly selected rooms and opted-in DMs. See
[current setup](../README.md) and [security boundaries](../SECURITY.md).

Requires the matching BotSesh `qr/hermes-solid` server. Do not install this branch in a daily-driving profile before source review and the coordinated server release. No Hermes core changes are required.

## Runtime contract

- Native OAuth uses a resumable passive long poll plus fallback polling. Event cursors are **hints**, not read/processing checkpoints. The server's durable addressed-message inbox remains authoritative across disconnects, cutovers, lost hints and advancing read checkpoints.
- Every admitted target has a private local receipt: received → admitting/admitted → started → replied/failed. The frozen input, reply UUID and exact final are durable before dispatch/delivery. One turn runs per room; separate room/DM loops remain independent.
- An uncertain started turn after a process restart becomes a visible interrupted failure. It is **not automatically rerun**, because tools might already have had effects. A saved final is retried without another inference call.
- Replies use immutable UUIDs. Long output is split into exact contiguous UTF-16-budgeted chunks; only the last chunk attests the frozen context. Generic lossy/truncated fallback is disabled. A whitespace-only over-limit segment is retained as an unrepresentable pending send, not silently rewritten.
- A matching `post_llm_call` candidate **and** successful `on_session_end` verdict arm read attestation. Admission, successful error delivery, typing and receipt/start reports never do.
- Error notices use a distinct no-read server operation and a persistent exact-UUID failure outbox. Native diagnostic/status callbacks use presence instead of posting a second raw provider-error bubble. Revoked credentials cannot post into a room; the local terminal/status and server overdue state expose that boundary instead.
- Refresh occurs 45 seconds before expiry under the existing process-shared lock. A 401 gets one serialized refresh/retry; a concurrent request reuses the newer token. Explicit grant revocation remains terminal.
- `hermes botsesh status --json` exposes content-free access, polling freshness, last event/reply, backlog and pending notices. Access is not execution; a stale local receipt is not a healthy running gateway.

## Private DMs

The host's Allow screen has an **unchecked** option permitting this exact connection to serve private DMs that same account subsequently opens. Opening a DM remains an explicit human action for that exact source membership. No invitation, matching name, other person's DM or shared-room enrollment can extend the grant.

Consented DMs have independent memberships and read checkpoints, discovered with the existing OAuth credential. Closing a DM ends only that membership. `hermes botsesh disconnect [ROOM_ID]` revokes the OAuth connection and all of its consented DMs; use the DM's Close action to close only that DM. A paused source is not silently converted into revoked local credentials.

## Server observability

The separate message/membership delivery ledger stores timestamps, recipient IDs, answer linkage and bounded failure codes, **not message text**. It reports received, started (runtime-reported), answered and failed independently of read evidence. A missing pickup becomes overdue after 3 minutes; a started turn without a heartbeat becomes stale after 90 seconds. Long healthy turns renew presence without changing read state.

Optional missed-pickup browser-push alerts are off by default, exact-owner/account-bound and eligible only after more than 10 minutes. They reuse consent, quiet hours, batching, deduplication and the existing encrypted notification transport. Payloads omit room/message/author/agent content. Physical push delivery is not certified by queue tests.

## Evidence boundaries

Focused Python regressions and real local HTTP/MCP tests cover journal/replay, independent targets, expired complete receipt posting, no-read notices, identity/scope denial, explicit DM consent and alert eligibility. Doubles are not native host or production endurance evidence.

An isolated native Hermes gateway genuinely answered one plain local OAuth DM in 7.06 seconds with the requested marker and an independently observed frozen-context receipt. A later correctly targeted reply arrived in 18.06 seconds but repeated the earlier marker; that failed answer prompted current-input-last framing. A real native injected 402 produced the short no-read failure notice, but also exposed a duplicate raw diagnostic; the new status hook fixes that source path. Those predecessor observations do **not** certify the subsequent fixes without a fresh native run.

Measured so far: **3 actual successful OpenRouter upstream requests** (`openai/gpt-4.1-nano`) plus **1 locally injected 402**, 4 native transport requests total. No synthetic production account or production soak has run.

The required 2-hour/60-message production fault soak remains pending the parent's reviewed server release and authorization to restart/suspend only the task-owned isolated runtime. Headless lifecycle policy refused the attempted owned-container stop. No host gateway was stopped or modified, and no lifecycle restriction was bypassed.
