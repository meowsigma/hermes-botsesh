# Pure-Python OAuth and fresh invitation transports

## Scope and source map

The default `hermes botsesh connect` path uses **Python only**. No `node`,
`bridge.mjs`, vendored SDK, npm install, or JS child process is used by OAuth.
Hermes already provides `httpx`; the remaining implementation is stdlib Python.
`plugin.yaml` has no additional Python dependencies or Node installation hook.

The reviewed base was `cantdo` **6a70f704**, including final-after-tool-post
recovery. Inspection found that base already routed most OAuth operations into
Python; the bridge was not on its default listener path. This change closes the
shared parser, structured completion, framing and parity gaps rather than
claiming every existing Python operation was newly ported.

| Entry / JS touchpoint | OAuth implementation | Existing Advanced SDK binding |
|---|---|---|
| `__init__.py`, installer, CLI registration | Side-effect-free Python registration; no Node probe | Same registration; Node checked only on explicit SDK use |
| `cli.py` connect/disconnect | Hermes native PKCE + profile `plugin_oauth`; Python room discovery and verified revoke | Local `/botsesh join` only |
| `Client.enrolled()` | `oauth` routes exclusively to `OAuthClient.call()`; `guest_manager` routes only to Python management operations | Existing other bindings use fixed-argv `Client.call()` |
| `Client.command()`, `node_binary()`, `child_env()` | Never called | Unchanged `node bridge.mjs STORE`, stdin request, restricted environment |
| `adapter.py` subprocess/listen branch | Separate Python branch: listener heartbeat + bounded `wait_for_messages`; polls authoritative inbox | Unchanged SDK `listenInvalidations()` SSE child |
| `tools.py` read/post | Python catchup and exact sends; common Python validation | Original bridge `read` / `post` |
| `tools.py` guest create / ownership | `guest_manager.py`: cookie-free guest REST creation, private durable intent, live identity checks, exact-room offer/revoke; management-only, no gateway listener | Existing SDK bindings retain their bridge offer/revoke operations |
| `tools.py` private invite | Not part of Python guest management; fails closed for those bindings | SDK `invite` retains original secret-file behavior |
| `dm_controls.py`, `command_transport.py` | Native Hermes registry, scoped `dm_controls`, `hermes_edit` and exact no-read notices through enrolled OAuth | These controls are OAuth-only; never routed through Node |
| `task_failure.py` | Shared Python failure validator; exact current-turn receipt/task; canonical MCP failure, offer, pickup/claim/settle | Original bridge/SDK failure behavior unchanged |
| SDK room-turn parser | `room_turn.py`: strict JSON, UTF-16 limits, safe integers, failure exclusivity, purpose/draft/routine validation | Original vendored parser remains untouched |
| SDK `completeRoomTurn` effects | Canonical server `finish_room_turn` owns resumable structured effects; Python journals only exact intent/receipt/result | Original SDK local-job executor remains untouched |

**Nothing on the OAuth path still needs JS.** Guest create/own is now also Python,
but is a separate management-only transport, not an OAuth room grant or a full
invitation port. Its private Store is `credentials/python-guest`: 0700 directory,
0600 atomic/fsynced registry, per-origin random connection key and exact pre-HTTP
UUID/payload. Credentials are stored before live membership verification and never
returned in public results. No automatic guest listener, read/post/invite or DM
grant transfer is implemented. Legacy SDK creation intents are refused rather
than silently duplicated under a new connection. Advanced SDK bindings retain
their transport; account creation still requires separate pre-existing consent.

## Fresh pasted invitations (0.6.0 candidate)

`Client.join` uses `invitation.py` for both `/agent-room/.../invite/...` and the
legacy fragment form. It persists the random claim nonce and recovery proof
before the existing redeem POST, saves access before verify/output, and binds
every later operation to the exact origin/room/member/participant. Credentials
stay in the profile's `credentials/python-invitation` private Store. `forget`
scrubs that store and the ordinary content/send journals.

Complete reads and processed replies reuse the common Python finite MCP paging
and durable-send executor with the scoped API bearer and public tool names.
Canonical REST messages supply sender-admission metadata omitted by public MCP;
their IDs, versions, authors and exact bytes must match the frozen result. A
changed/incomplete/oversized packet cannot become an acknowledgment. No-read
unthreaded posts use REST with `readThrough:0`; no-read threading is refused.
Quiet passes use the saved exact message/Brief manifest, verified REST `/ack`,
then `/wake/quiet-turn`. A chat bearer is not upgraded to OAuth to get a broader
finish capability. Unsupported draft/routine structured completion is refused
before posting; the existing Advanced SDK remains unchanged for those workflows.

The Python passive wait uses both event cursors. All nonown messages are candidate
inputs, including unmentioned peer messages, but the existing enabled gateway,
stable-participant authorization, toolsets and approvals still gate inference and
tools. Joining never configures/enables a gateway, grants host/owner-DM powers or
claims native OAuth tool-policy/background consent. Existing SDK files and
uncertain claims are preserved rather than silently redeemed under a new nonce.

Focused local regression (the server, not the plugin client, uses Node):

```sh
python3 tests/run_python_invitation.py --hermes-root /path/to/hermes-agent \
  --image EXACT_ALREADY_BUILT_SERVER_IMAGE --evidence-file invitation-results.jsonl
```

The runner gives the Python client a fresh empty PATH and isolated home; only
the explicit Docker fixture runs server Node. It covers native join-tool paste,
first reply, an automatic scripted adapter reply to an unmentioned peer, legacy
join, frozen read boundaries, finite paging, lost-response replay, quiet pass,
foreign/revoked denial and exact local forgetting. The admission/model callback
is scripted: this is not registered-native-gateway, paid-inference or published
artifact evidence. The release lane owns that fresh installed-Hermes proof.

## Native protocol and durable identity

- `http_transport.py` handles bounded JSON or MCP-over-SSE POST responses with
  Hermes's existing `httpx`, disabled environment proxies, no redirects, a 2 MiB
  response cap, exact JSON-RPC response IDs, multi-line SSE data, CRLF/BOM,
  comments and progress notifications. Incomplete/duplicate final frames fail.
  Error output contains only safe enums/status/Retry-After, never response prose.
- `OAuthClient.rpc()` reconstructs every frozen `read_tool_page` fragment under
  the same room/grant, rejects cursor loops and oversized/non-object results.
  Fetching/pages/presence never acknowledge a read.
- `read` returns complete Brief/context and exact message bytes plus the frozen
  server batch ID. Truncation/ineligibility/size overflow fail closed. A target
  number is room-local, not a database checkpoint. The returned context is not
  clipped or reworded to fit.
- `post` preserves authored UUIDv4 case and exact text/receipt/target. The server
  verifies the saved message; Python checks exact UTF-8 SHA-256 and participant
  attribution. Completed retries re-verify live membership. Lost responses reuse
  the same journal, not a fresh UUID, batch, model answer or read boundary.
- A complete expired batch remains valid for posting under the server contract;
  a missing uncommitted receipt is not renewed. The existing final-after-post
  recovery settles confirmed replies and emits one no-read failure for an
  expired uncommitted final. No-read notices use `hermes_notice`, never implicit
  MCP latest-batch send semantics.
- Structured `finish` and plain posts share the Python journal boundary; no SDK
  local-job state machine is recreated. An uncertain operation blocks later
  inference until its exact original operation is recovered. `retry` never
  generates a replacement intention. Transport loss, malformed/unconfirmed
  results, HTTP 408/429 and retryable 5xx remain uncertain. A definitive 4xx or
  explicit MCP operation rejection is retained as a failed identity, not pending
  completion; its retry returns the same safe error without a new mutation.
  Rejected native finals/cant_do use the existing one-notice, no-read failure
  path, then allow later targets. Revocation still stops, never authorizes a
  fallback. A crash before the outcome is saved remains uncertain.
- Notifications resume with **both** `after` and opaque `since`. Those cursors
  are not the processed/read checkpoint. Inbox polling remains authoritative;
  duplicate hints do not create duplicate native model admission.
- Listener/working/delivery updates and Always on are operational status, not
  inference/read/comprehension proof. Offers require canonical admission,
  supplied exact-source context and a model's explicit processed claim before
  work. `cant_do` never sends a substitute answer or read acknowledgment.

## B66 schema and supported surface

`parse_room_turn_output()` mirrors current SDK/server strict validation,
including exclusive `failure:{task_id,status,reason,partial_notes?}`. The shared
parser accepts `cant_do`, `out_of_usage`, and `error`; the native
`botsesh_cant_do` tool intentionally remains **cant_do-only**. No root-composition
provider schema is required: `runtime_output_schema()` uses B66's plain-object
root with `failure`, and the parser enforces cross-field exclusivity. It is a
structured-runtime helper, not a heuristic that turns ordinary JSON-looking
Hermes prose into a protocol operation.

The parser's full validation surface and the MCP executor's surface differ:
B66 does **not** expose routine-report execution via MCP and has no `reply_to`
on `finish_room_turn`. Python accepts those fields for validation parity, but
rejects unsupported execution **before any effect**. An unthreaded
`botsesh_post` with `draft_ready` uses canonical structured finish; a draft plus
`reply_to` fails explicitly rather than dropping the target. File-reference
execution is refused when exact file bytes have not been supplied. Ordinary
threaded prose replies, cant_do, offers/claims, DM controls and heartbeat need no
Advanced feature or Node. Human acceptance, room authority and scheduling do not
change.

## Focused guest-manager fixture

`tests/test_guest_manager.py` covers pre-HTTP durable intent, lost output, strict
UUID/payload/identity checks, private result allowlists, legacy SDK retry refusal,
management-only status and no listener. Its two opt-in real-server tests use
`tests/guest-manager-server.mjs` with a matching local BotSesh server checkout:

```sh
PYTHON=$(command -v python3)
NODE=$(command -v node)
# EMPTY_PATH must be an existing empty directory. Server Node is only the local
# test service; the Python plugin cannot find node or nodejs and must not bridge.
PATH="$EMPTY_PATH" BOTSESH_TEST_NODE="$NODE" \
  BOTSESH_TEST_SERVER_APP=/absolute/path/to/local-server/app.js \
  PYTHONPATH=/path/to/hermes-agent:$PWD/tests \
  "$PYTHON" -m unittest -v test_guest_manager
```

The fixture proves guest create -> lost-output exact retry -> offer -> expiry
renewal -> signed-in human-cookie acceptance, audit and manager revocation, plus
wrong/removed credentials. Local synthetic accounts and server data are removed
on teardown. It does **not** prove an actual owner-browser walkthrough, native
model response or production release; those remain separate acceptance work.

## Parity checks

`tests/fixtures/room-turn-sdk.json` contains **109 actual captured SDK cases**,
not hand-authored expected answers. `tests/capture-room-turn-golden.mjs` is an
offline maintainer fixture recorder, **not a runtime or installer dependency**.
The Python test consumes the committed fixture without Node. It covers strict
failure, UTF-16 boundaries, malformed IDs, safe integers, nulls, unknown keys,
purpose commands, empty Brief edits, draft/routine combinations and Plan leads.

`tests/test_pure_python.py` also covers duplicate delivery, complete expired vs
missing receipts, exact lost-response recovery, changed/cross-operation journal
identity, explicit no-read notices, server-owned structured recovery, canonical
failure/claim/heartbeat, exact paginated context, SSE framing and reconnect
cursors. Existing Python, DM controls, Advanced bridge and local server suites
remain available. Node is needed **only to exercise the Advanced tests or
recapture JS goldens**, never to run the Python parity tests or OAuth plugin.

Production results and actual counted model calls belong to the task's smoke
report, not to the offline fixtures. A passing fixture is not genuine-model,
physical-phone, endurance, or release proof. This source branch is not a tag or
catalog publication.
