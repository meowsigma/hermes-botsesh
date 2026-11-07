# Browser-accepted human ownership offers

Task `t_ef5a72bd`. This is a small additive change on the transport owner's frozen
`pure-python` candidate `63badae9b78220b32ccaa1ff776b4a8316e34bc7`, coordinated on
`t_5df8ed41`. That base still needs its own independent approval; this branch does
not approve or publish the refactor.

- `botsesh_offer_ownership(room_id, user_requested:true, client_id?)` is available
  only from an operator surface, not a BotSesh room message. The server rechecks
  this agent's live manager grant. Omitted IDs reuse a private logical intent;
  retry an uncertain operation unchanged, and use a new explicit UUID for a new
  human request after expiry/revocation.
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
  The optional Advanced guest/invitation bridge gains only two narrow operations
  and matching SDK methods; no parser, receipt, HTTP/SSE or model path changes.
- Requires the matching BotSesh server release; old servers must report refusal,
  not trigger a credential fallback or local client patch.

Local verification: five focused tests in `tests/test_ownership.py` cover operator
and explicit-request fences, exact room/UUID preservation, stable retries, pure
Python OAuth RPC and registered handler/manifest parity. Existing `test_plugin.py`
passes 18 tests after adding the two tool names to its exhaustive inventory.
These are offline checks, not native-agent or production proof. The app candidate
separately exercises actual HTTP/MCP/SDK/browser acceptance. Named-host inference
has not run: the task's six-upstream-call ceiling cannot reserve the normal eight
requests for two complete tool+final turns in both hosts. No provider call has
been spent and no native completion is claimed pending owner disposition.

Integration hotspot: `oauth.py` / `tools.py`; compose the add-only ownership hunks
with the frozen transport owner rather than replacing either file wholesale.
