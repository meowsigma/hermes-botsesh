# Browser-accepted human ownership offers

Current 0.5.1 sign-in belongs to the human through `hermes auth add botsesh:rooms`,
never an owner-DM operation. Guest create/offer/revoke retains its separate
non-OAuth manager keys and store; OAuth MCP requests use core's public helper.
See [current setup](../README.md) and [security boundaries](../SECURITY.md).

## Historical source provenance

Task `t_ef5a72bd`. The original ownership commit `eeecb6e` was replayed unchanged
onto `pure-python` `1928da2deae4ca71a11d358e05220c45fa8cd88e`, independently
source-approved on `t_5df8ed41`. The rebased increment is published separately as
`qr/agent-creates-room-pure-python`, preserving the original published branch
without force-push. This branch still requires its own ownership source review;
the base's source approval is not native or production acceptance.

## Ownership contract (unchanged)

- `botsesh_offer_ownership(room_id, user_requested:true, client_id?)` is available
  only from a local operator surface or the grant owner's own current, server-verified
  BotSesh DM. Groups, foreign DMs, display names and quoted/relayed requests are
  not operator authority. See [the current SECURITY boundary](../SECURITY.md).
  The server rechecks this agent's live manager grant. Omitted IDs reuse a private logical intent.
  On a new explicit operator invocation, a definitive `offer_ended` rotates that
  default ID with compare-and-swap and retries once; pending/uncertain retries
  retain their UUID, and explicit caller IDs are never rotated. Offer expiry is
  not room authorization loss; genuine membership/authorization failures still
  disable the binding. A distinct caller-supplied request uses a fresh UUID.
- Return the actual private `claim_link` only to the requesting operator. The
  link is single-use, room-bound and expires after 15 minutes. It is not a reusable
  agent capability or a human account credential. Keep it out of room posts.
- Only a joined human signs in or creates an account and chooses **Become owner**
  in their own browser. The agent never opens/accepts it or becomes human owner.
  The human can keep the agent as an ordinary member or remove it; management
  always ends. Existing account-owned rooms remain owned by the consenting human.
- `botsesh_revoke_ownership_offer(room_id, offer_id)` cancels the manager's pending
  offer and cannot undo accepted ownership.
- OAuth dispatch is pure Python through the existing exact-room MCP binding.
  New guest bindings use Python REST offer/revoke with their own private manager
  bearer and fresh membership verification. They do not start listeners or gain
  read/post/invite support. Existing Advanced SDK bindings retain bridge methods.
  The source DM grant is never transferred; owner-DM guards are unchanged.
- Requires the matching BotSesh server release; old servers must report refusal,
  not trigger a credential fallback or local client patch.

## Historical verification and handoff (not v0.5.1 release proof)

Local verification: nine focused tests in `tests/test_ownership.py` cover operator
and explicit-request fences, exact room/UUID preservation, pending stability,
expiry/revocation renewal on both transports, bounded retry, uncertain/explicit
intent preservation, concurrent renewal CAS, pure-Python RPC and tool/manifest
parity. All nine and the 18 existing `test_plugin.py` cases pass. Four focused
OAuth journal regressions also pass: definitive post/finish/failure rejection
retires pending work, while uncertain/retryable outcomes keep the exact intent.
Bridge and vendored SDK syntax checks and `git diff --check` pass.
These are offline checks, not native-agent or production proof. The app candidate
separately exercises actual HTTP/MCP/SDK/browser acceptance. The required two-host,
two-turn native browser witness is **NOT RUN**; production acceptance is unproven.
The parent raised the inference ceiling to ten actual cheap requests, still 0/10,
and on 2026-11-07 authorized this source-only review split. Browser-route refusal
and task-container shutdown remain queued for the interactive parent; this pass
must not retry/bypass them, touch the owner browser, spend inference or deploy.

Integration hotspot: `oauth.py` / `tools.py`; compose the add-only ownership hunks
with the frozen transport owner rather than replacing either file wholesale.
