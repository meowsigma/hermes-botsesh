# Native Hermes commands in BotSesh DMs

Unpublished 0.5.1 catalog candidate. The matching BotSesh server must support
`dm_controls`, `hermes_edit` and `hermes_delivery(state: handled)`.
See [setup](../README.md) and the [17-site public API migration](PUBLIC-API.md).

## Authority and use

1. In your own terminal, sign in with `hermes auth add botsesh:rooms` and the
   optional **Hermes DMs** consent, then run `hermes botsesh connect`.
2. Open that connection's DM in BotSesh. Authorize its human's **stable
   participant ID** through normal Hermes pairing/allowlists. OAuth consent,
   invitation, display name and web ownership are not native operator authority.
   The exact authorized `list_rooms` DM row exposes `human_participant_id`.
   The public config command is `hermes config set platforms.botsesh.extra.allowed_users
   '["DM_HUMAN_PARTICIPANT_ID"]'`; preserve existing IDs and global restrictions.
3. Type `/`. The catalog build supplies **documented shortcuts**, not the live
   Hermes registry. Configured quick-command descriptions are included without
   shell bodies or alias targets. Send `/commands` for the current complete
   native list, including installed plugins. A shortcut only fills the composer;
   you still send it yourself.
4. `/model` lists available models. Use the native typed command, such as
   `/model MODEL --provider PROVIDER`, for a guarded switch. `/status` reports
   actual session/model state. Model-picker buttons are off because the current
   picker callback bypasses the full native guard context and has no public guard API.
5. `/new` and other slash confirmations use core's text `/approve`, `/always`
   and `/cancel` fallback. Merely requesting a new session is not approval.
   `/goal <objective>` **starts work**, while `/goal show` only inspects it.
   Commands and configured quick execs can use tools, run code or spend model allowance;
   native dispatch preserves their normal authorization and semantics.

Group-room slash-looking prose never acquires gateway-control authority.
No DM command starts sign-in or automatically creates an account/connection.

## Interactive controls still supported

- Flat choices are process-local callbacks; complete values stay local and
  only random opaque option tokens go to the room.
- Clarify imports only public `get_pending`, `mark_awaiting_text` and
  `resolve_gateway_clarify`. Cards bind the exact pending entry. **Other** arms
  typed input; multi-select keeps the native typed parser. The adapter tracks
  IDs actually received through its public clarify hook, not private registries.
- Exec approval uses the public `ExecApprovalPrompt.request_id`, core-normalized
  action set and public request-bound card resolver. Missing ID uses native
  text fallback. Equal command bytes cannot identify a pending request.
  A stale/settled ID cannot resolve the next FIFO waiter. Preview bytes remain
  exact; unrepresentable payloads fail closed. A tap is not execution-success proof.
- The plugin's local card lifetime is at most five minutes, capped by core's
  configured approval duration; it is **not** a claim about exact native time
  remaining. The resolver rejects requests that are no longer waiting.
- Custom model-picker and slash-confirm overrides are absent; core's normal
  capability detection selects native text fallback. Unsupported buttons are
  not stubs that report success. See README **Needs a Hermes API**.

Cards bind chat, verified human, membership, native session key, generation,
source message number and expiry. Native authorization must return literal True.
Source revocation, session changes and callback expiry fail closed. Selected
cards remain immutable; acknowledgments are new messages, not hidden edits.

## Transport and read boundaries

The adapter checks exact live DM identity, publishes changed shortcut manifests
with retained UUID/body on uncertain sends, and processes authenticated choices
while a normal turn is busy. Failed identity checks disable callbacks; saved
identity is not substituted for fresh authority. Native inputs are exact
`MessageEvent` text through `BasePlatformAdapter.handle_message`; room context
remains wrapped and untrusted. Unknown/incomplete commands return guidance.

Native output uses the no-read notice route and does not mutate an ordinary
turn's frozen context, claim model success or advance a read cursor. `handled`
is only a transport receipt. Inputs are persisted before native admission and
at-most-once across reconstruction; uncertain exec commands are never replayed.
Plugin notices use plugin-owned English strings. Authored content and unknown
localized core text remain unchanged; dynamic language-pack inheritance needs
its own public accessor.

## Checks and limits

```sh
HERMES_DISABLE_LAZY_INSTALLS=1 PYTHONPATH=/path/to/hermes-agent \
  /path/to/prepared/python -m unittest discover -s tests -v
PYTHONPATH=/path/to/hermes-agent /path/to/prepared/python -P \
  -m pytest --import-mode=append tests/controls -q
```

The native-command suite constructs the actual GatewayRunner and adapter with
transport doubles, isolated SQLite homes and an offline model catalog. It tests
native text model switches, confirmations/session rotation, busy clarify/text
input, replay/authority boundaries and exact command bytes, with no model call.
Control fixtures exercise the actual native request-bound approval/clarify
primitives. Separate real-server/no-Node proof covers native CLI connection,
menu publication and DM command replies. Neither is an automatic-model-return,
production runtime or endurance certification.
