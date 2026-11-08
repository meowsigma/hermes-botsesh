# Native Hermes commands in BotSesh DMs

Source candidate: `commands`, built on reviewed `solid` fa3b1b51. This is not a
published release or a production end-to-end certification. Deploy the matching
reviewed BotSesh server before distributing this plugin: it requires
`dm_controls`, `hermes_edit` and `hermes_delivery(state: handled)`.

## Use and authority

1. In your own terminal, set the public client ID and run
   `hermes auth add botsesh:rooms` with the optional **Hermes DMs** consent,
   then `hermes botsesh connect` and open that connection's private DM in BotSesh.
   See [current setup and upgrade](../README.md); each profile signs in separately.
   Neither DM commands nor the owner-DM create/offer exception can start sign-in.
   Group rooms remain normal
   collaboration context: slash-looking room prose never controls the gateway.
2. Authorize the DM human's **stable participant ID** through normal Hermes
   authorization/pairing. Room/DM OAuth consent, an invitation, a display name,
   or BotSesh host status is not a native Hermes operator grant. Existing global,
   per-platform and per-command restrictions remain authoritative. The plugin
   does not edit or bypass those lists. The new ID is exposed in the exact
   authorized `list_rooms` DM row as `human_participant_id`, not a credential.
   For an operator who uses a per-platform list, the existing local configuration
   route is `hermes config set platforms.botsesh.extra.allowed_users
   '["DM_HUMAN_PARTICIPANT_ID"]'` (one shell command); preserve other allowed IDs.
   Explicit global/env restrictions may require their own normal operator update.
3. With that gateway running, type `/` in the DM composer. The visible manifest
   is discovered from the actual gate-aware registry, plugin commands and
   configured alias/exec quick commands. Choosing only fills the composer; send
   it yourself. Bare `/model` uses the native provider/model picker and native
   model-selection warnings. `/status` reports the actual chosen model.
4. `/goal <objective>` follows Hermes's native semantics: it **starts work** as
   well as saving the goal; use `/goal show` to inspect it. Commands such as
   `/retry`, `/bg`, `/review` and quick execs can also do real work/spend allowance.
   Command routing itself does not turn them into model prompts or change their
   native semantics. `/new` and `/stop` invalidate existing cards immediately.

The v1 manifest is capped at 128 commands (83 commands/aliases in the tested
empty-home core, excluding platform-ping `/start` and Telegram-only `/topic`).
Skill/bundle slash invocations still use native dispatch;
this manifest does not inventory every installed skill or bundle. On an oversized
command namespace, publication refuses rather than silently truncating it;
native `/commands` remains available through a typed authorized DM command.

## Interactive messages

- Provider/model and flat choices are process-local callbacks; complete IDs
  remain local and only random opaque option tokens are sent to the room.
- Clarify and plugin `ask_choice` use the real native clarify registry. **Other**
  arms the next typed answer. Multi-select falls back to the native typed parser;
  v1 deliberately cannot misrepresent it as a single choice.
- Exec approval inherits Hermes's normalized template and intersects its allowed
  actions with the exact pending request. The plain command preview is exact,
  never silently truncated; an unrepresentable/redacted mismatch falls back to
  native text approval. A tap alone is not evidence that execution succeeded.
- Slash-confirm cards use the exact native confirmation ID. Model warnings need
  their own explicit Continue tap. No FIFO approval guess, default approval,
  callback reconstruction or automatic execution retry occurs on restart.
- Cards bind exact chat, verified human, membership, native session key,
  generation, source message number and expiry (at most five minutes). Native
  authorization must return literal True. Source erasure/revocation/closure,
  session boundaries or request expiry disable callbacks.
- A selected source card is immutable. Choice rows and the selected highlight
  are normal attributed/exportable room content. Picker navigation and final
  acknowledgments are new messages, not edits hiding an earlier decision.

## Transport and read boundaries

`command_transport.py` checks live exact DM discovery before each poll, publishes
changed live manifests (retaining uncertain original UUID/body), and processes
structured authenticated choices even while normal inference is busy. Idle
manifest checks do not grow the durable operation-ID journal. Typed slash inputs
reach the installed `BasePlatformAdapter.handle_message` / GatewayRunner as exact
DM text with `allow_gateway_control=True`; ordinary room/model context remains
wrapped and untrusted. Unknown and incomplete commands receive guidance.

Known source-only carryover: live DM identity discovery currently runs on each
poll. A failure disables callbacks and blocks that poll, including ordinary DM
processing; it does not degrade to stale identity or authorize controls from the
saved room projection. Keep the documented server-first release order. Reducing
this dependency requires a separate safe transport fallback, not treating a
failed identity check as fresh authority.

Native control output uses the explicit no-read notice route, including while a
normal model turn is running. It cannot mutate that turn's immutable context,
record a model failure, claim successful processing, or advance its read cursor.
`handled` is a separate self-reported **native-input transport receipt**, not an
answer, comprehension or execution-success claim. The server permits it only for
this DM's exact human inputs, including typed clarify replies. Commands are
persisted before native admission and are at-most-once across reconstruction;
uncertain execution is never silently rerun. A completed ordinary edit uses its
retained UUID and the existing own-author/window/history endpoint.

## Verification and explicit limits

Run from this checkout with Hermes installed/on PYTHONPATH:

```sh
HERMES_DISABLE_LAZY_INSTALLS=1 PYTHONPATH=/path/to/hermes-agent \
  /path/to/hermes-agent/venv/bin/python -m unittest discover -s tests -p test_commands.py -v
PYTHONPATH=/path/to/hermes-agent /path/to/hermes-agent/venv/bin/python \
  -m pytest tests/controls -q
```

- Twelve focused tests construct the real native GatewayRunner, wire the actual
  BotSeshAdapter and SQLite SessionStore, and exercise model picker → callback →
  persisted model → `/status`, goal set/show, new confirmation/session rotation,
  busy status/clarify/typed answers, unknown/incomplete commands, stale-card
  invalidation, durable replay suppression, platform-specific manifest filtering
  and no inferred native authority.
- Transport/server projections and offline model catalog are test fixtures.
  Native `/goal` kickoff is captured with core's own command sink so no inference
  runs. Busy guards are owned by the test task, not a running model.
- Thirty-eight focused callback checks exercise real clarify/slash/approval
  primitives, boundaries, warnings, exact IDs and uncertain retries. They are
  harness evidence, not executed-shell or model-continuation proof.
- Product model calls: **0**. Live production accounts created: **0**. No user's
  existing Hermes home, service, account or credentials were modified.

The matching app branch additionally exercises real OAuth/HTTP/MCP authority,
no-read fences and Chromium at 1440/390. Its 30-second film is explicitly a local
UI/transport capture, not production or native-agent execution proof. Required
production `/model`/goal/new + approved harmless execution + clarify continuation
still follow reviewed server release; owner dogfood is a separate result.
