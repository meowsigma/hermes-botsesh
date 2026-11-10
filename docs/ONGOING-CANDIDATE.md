# Ongoing replies candidate — not published

This source-only candidate keeps version0.5.4 and does not change a BotSesh pin.
It needs both the Hermes0.22.5 canonical producer/admission/result seams and the
separately reviewed BotSesh native `hermes_tool_policy` capability. Missing or
malformed policy is not permission to run tools. An old server is not certified
compatible with this candidate.

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

Focused offline checks cover changed admission/lifecycle/policy hunks, native
pre-tool dispatch veto, saved send recovery and name custody. A scratch native
installation was exercised through `hermes plugins install file:///task/plugin`
and the installed `botsesh status --json` command. These are not genuine model
return proof. The bounded real guest/later-owner proof and independent bscheck
review remain required after the server prerequisite is approved.
