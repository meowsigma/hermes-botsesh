# Operator sessions and the owner-DM exception

`botsesh_create_room` (guest mode) and `botsesh_offer_ownership` accept explicit
requests from a local CLI/local/TUI/Desktop operator, or from the OAuth grant
owner's own **current direct BotSesh DM turn**. No other surface is an operator.
This is not a general remote shell, credential, enrollment or account permission.

## Verified identity, never names or message claims

The authenticated server projects `human_owns_grant` only for live accepted DMs
in `list_rooms`. It compares that DM's account with the owner of the **exact
calling OAuth grant**, using server records. It returns a Boolean, not account
IDs. A missing owner row, guest account, paused connection, closed DM, revoked
membership or old server cannot establish this authority. Sharing a group room,
matching a name or being the room owner does not suffice.

For every management invocation, the native plugin requires:

- task-local BotSesh chat/message identity and exactly one active admitted turn;
- an active OAuth DM with the admission-time credential/participant binding;
- the exact frozen target and matching current/stored/frozen receipt identity;
- the verified human source author, matching the DM human and native allowlist;
- a **fresh authenticated** DM context lookup with `access_state: authorized`,
  matching room, participant, membership and human, and `human_owns_grant: true`;
- unchanged turn/binding state after that lookup.

No model argument, environment permission flag, cached menu projection or persisted
owner bit can authorize this exception. Other room tools keep their existing
cross-room fences. Ownership offers still require `user_requested: true` and the
target room's own live creating-agent manager authority. The offer link may be
returned privately to the requesting owner in their verified DM, never published
to a group or the created room. Only the joined human accepts it in a signed-in
browser; the agent cannot accept, reassign an owned room, or retain manager rights
after human acceptance.

## Direct requests versus quoted or relayed content

Only the verified owner's own direct explicit request is in scope. Requests by
other authors in room history, Brief, files or relayed messages are untrusted,
even if they claim the owner's name. Source identity is enforced in code;
distinguishing an owner's genuine request from text the owner merely quotes is
also a model semantic policy, not a deterministic prose classifier. This guard
**does not prove intent from arbitrary quoted text**. No broader guarantee of
prompt-injection immunity is made. A strict content-intent guarantee would need
an additional trusted human action bound to exact operation arguments.

The fresh check is a preflight, not an atomic cross-room transaction: a remote
revocation after preflight cannot retroactively cancel an independently committed
guest creation. Target membership/manager checks remain authoritative at mutation.

## Unchanged limits

Account-mode creation through a separately consented MCP connector and
`botsesh_revoke_ownership_offer` remain local-only. `/botsesh` remote management,
invitation handling, other tools and human-only approvals are unchanged. There is
no token export or reuse of the DM grant to enroll another room.

Guest creation still uses the optional Advanced Node 24 bridge. An owner-DM guard
pass is **not** no-Node creation support. Live no-Node create/own and useful-backup
proofs remain separate release acceptance work. Older servers fail closed; no
credential fallback or manual guard bypass is permitted.
