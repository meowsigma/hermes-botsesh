# Ongoing replies candidate — not published

This source-only candidate keeps version0.5.4 and does not change a BotSesh pin.
It needs both the Hermes0.22.5 canonical producer/admission/result seams and the
separately reviewed BotSesh native `hermes_tool_policy` capability for OAuth
turns. A missing, unavailable or malformed policy retires that exact target with
one typed no-read runtime failure before inference; later requests are not
stranded. The diagnostic policy code/status stays in the local receipt, not
room prose. Revoked/expired credentials stop the room without a fallback send.
Advanced invitation/Node-bridge bindings retain their original transport and
native toolsets/approvals, without calling the OAuth-only policy capability.

- Active, exact OAuth room guests are eligible without being labelled verified
  accounts. Native participant/room allowlists and owner-only DM/management
  checks remain independent requirements.
- When no `platform_toolsets.botsesh` key exists, connection inherits the native
  effective CLI toolsets and adds the BotSesh transport tools. A saved list,
  including `[]` or `['botsesh']`, stays unchanged. Historical transport-only
  lists may be deliberate: an operator must explicitly choose any added native
  capabilities with `hermes tools`; connecting never silently repairs them.
- Everyone remains a server-consented default. An opt-in restrictive setting
  uses the native `pre_tool_call` block directive, not a model promise. Each
  attempt revalidates live exact-request policy; an initially restricted turn
  cannot acquire broader capabilities midway through execution. The native
  resolver, explicit toolset selection and ordinary approval rules still apply.
- No local wake event or one-time approval is synthesized for a native request.
  Native Allow once is not claimed by this implementation.
- A processing-success callback may mean admission only. The opaque canonical
  receipt is persisted; queued/started work is not treated as a completed empty
  answer. Terminal flags and outcome govern no-output/cancel/failure handling.
  Unknown execution stays uncertain and is never silently replayed. Core output
  delivery remains separate from execution completion.
- Pending final/tool send operations retain their exact UUID, target, receipt
  and bytes. Restart recovery reconciles that same admission, not new inference.
  An old final callback cannot bind to a newer room request.
- A profile default does not rename an existing returned seat. An explicit
  `--name` retains the separately consented optional rename flow. Connect has no
  retry branch that creates a replacement after an OAuth failure.
- New runtime failures use `<canonical saved seat name> couldn't do it.` and
  retain the failure code in the typed receipt. Old outbox payloads, authored
  inability answers and saved message bytes are not rewritten.

The adapter remains text-only. A native file-path attachment warning is a
separate egress limitation, not evidence that text, tools or delivery succeeded.

## Evidence boundary

Focused changed-hunk checks cover admission/lifecycle/policy, saved send recovery
and name custody. The installed candidate was then exercised in native
Hermes0.22.5 (`bf0203a794a07aac3ff764972ab9087baa4e4f4e`) against approved server
`f4b19061a6ee192442a09e424fe508451c91fd9f` in task `t_358b11c1`:

- Same original seat, room and OAuth grant: plain guest question -> GUEST-OK;
  11.395 seconds after that reply, a distinct signed-in owner's request actually
  invoked read_file and terminal/pwd, then returned the exact file text and cwd.
- Real native terminal attempts from a guest under Only me and from the owner
  under No one both returned the execution veto. Both filesystem sentinels stayed
  absent. This is tool-result/side-effect evidence, not model-promised refusal.
- One cumulative <=10-forward relay recorded9 requests:7 successful inference
  calls (matching native session counts), a props404 probe and a models200 probe. Actual
  forwarded output was capped at768 tokens per inference. No allowance reset.
- Exact foreign-room, removed-requester, removed-seat, group-as-owner-DM and
  revoked-bearer negatives ran through the installed client and live server.
  A cleanup-only assertion occurred after those checks; its receipt and the
  independent revoked-grant/read-ledger readback are retained, not called a
  fully successful negative harness exit.
- Real1440/390 captures show one Hermes and Online. The existing grey dot beside
  Online is server/UI source outside this plugin change. No attachment upload
  was requested or certified.
- The first attempt used a3.13 entrypoint with install-selected3.14 dependencies
  and failed before inference. Its one typed no-read failure remains retained.
  The corrected runtime resumed the same grant/seat; no useful return is claimed
  for that failed attempt.
- Supported native gateway stop, grant revocation/local token removal and owned
  room deletion were verified. No publication, version bump or server pin change.

The installed proof above used implementation03958f92 (Python3.14), not the
subsequent review corrections. Those corrections limit the policy seam to OAuth,
retire unavailable/malformed pre-inference policy as one typed no-read failure,
and migrate existing fixture admission/copy contracts. They were exercised
offline against the same Hermes0.22.5 source with Python3.13:13 focused checks,
the real Node-bridge pending-send witness and the reviewer-listed existing suites
pass. The two new defect regressions fail on the prior4bd0ac90 candidate. No new
model requests or installed final-source runtime certification are claimed.
Cancel, interruption, terminal no-output and uncertain-send branches have focused
fixture coverage, not genuine mid-turn fault-injection trials.
Independent source review and an explicitly authorized publication remain required.
LIMITED OWNER-AUTHORIZED SMOKE COVERAGE, not exhaustive certification.
