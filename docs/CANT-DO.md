# Explicit failure and native replacement — source candidate

This is the **unpublished `cantdo` candidate**, based on plugin v0.3.0 `a977430b27af5cdedb5e0db4c47cf01821620bc0`. It is not a v0.3.1 release or production certification. Native replacement requires the matching BotSesh server `qr/hermes-cantdo` counterpart based on B63 `85e41a4b57e708030195b6b03e1f2a4bf4302d68`.

## Explicit inability

`botsesh_cant_do(reason, partial_notes?)` is model-visible. Its handler requires an active BotSesh gateway turn, exact task-local room **and message anchor**, one matching native adapter, and that turn's exact frozen source. With `addressing.taskId` it retains the canonical task path. The unpublished `cantdo-plain-ask` successor also supports missing-task OAuth requests through `hermes_cant_do`, bound to the source number/version, complete receipt and this membership's started delivery. It requires the matching server `qr/cantdo-plain-ask` based on B66 `686f6884`. The model cannot supply another room, task, source, receipt, participant, grant or failure status. It must report its own genuine missing tool/capability/access, never clarification or another agent's reason.

The operation is journaled before network I/O. Uncertain retries keep the exact task, receipt, UUID, reason and partial notes. OAuth uses existing exclusive `finish_room_turn.output.failure`; invitation access uses the existing typed failure endpoint through the narrowly adapted SDK. Server checks still require the current live canonical winner and unchanged source. Failure never acknowledges the frozen context. The gateway suppresses a later prose final instead of sending a duplicate or pretending success. Ordinary inability prose is not parsed as failure.

For a missing task, only one structurally addressed human request qualifies:
an explicit unique mention, original single-agent selection, reply to this exact
agent, or accepted open DM. Ambient, ambiguous and other-directed messages do
not become implicit tasks. The server atomically materializes the task and uses
the same takeover enablement, winner, exact-version, backup eligibility/order
and hop guards. Exact retries return the same task/offer; changed bindings are
conflicts. The plugin checks the returned source and canonical UUID before
settling its journal. No read acknowledgment is added. Invitation transport does
not advertise the new native-only missing-task fallback.

## Canonical replacement in the native adapter

1. `hermes_inbox` additionally returns eligible **own canonical takeover offers**. Offered is not claimed. No new route, listener, connector, grant or owner role is created.
2. The native OAuth adapter accepts one offer through `hermes_takeover`. Server rechecks the native client, selected membership/grant, separate background/spend consent, current listener heartbeat, eligibility, lease, source version, route binding and room pause. Shared execution/model and wake budgets apply, including the ten-second start floor. A duplicate accept returns `inference_authorized:false`; an uncertain accepted response never authorizes another model run.
3. The adapter fetches the canonical task snapshot plus the complete normal frozen briefing/source context, including untrusted unfinished notes. Fetching creates **no read acknowledgment**. This is delivered as an ordinary native Hermes turn with the original human identity and stable source message anchor; normal gateway admission still applies.
4. After actually consuming that context, the model explicitly invokes `botsesh_takeover_claim(reason, processed:true)`. It can name no other offer/task. The server binds the complete batch to the same membership/grant and exact source version, then calls the existing `/wake/tasks/:id/ack` and `/wake/claim` guards. The tool reports success only for the exact canonical winner. A claim is source-read attestation, not external tool permission or a whole-room read checkpoint.
5. Work continues **in that same admitted native turn**. A normal final follows the existing attributed/receipt-bound send, or the model reports its own explicit `cant_do`. The canonical claim's generated follow-up wake is absorbed; no second runtime or synthetic answer is started. The native slot stays fenced through passive checks and canonical triage quieting until processing completion settles a verified own reply/failure.

Only native OAuth has automatic replacement-offer reception in this candidate. The advanced invitation route retains explicit failure reporting; it is not advertised as a second automatic replacement receiver.

## Verification

From this plugin checkout, with the matching server source and its dependencies available:

```sh
python3 -m unittest discover -s tests -p test_cant_do.py -v
node --test tests/cant-do.test.mjs
BOTSESH_SERVER_SOURCE=/absolute/path/to/matching/server node --test tests/native-replacement.test.mjs
hermes plugins validate .
```

The replacement test uses real local OAuth consent/server routes and the **actual plugin adapter, tool registration/handlers, receipt journals and OAuth operation mapping**. Only the native model actions and HTTP transport between the subprocess and local test server are scripted. It proves delivery → explicit exact-source claim → ordinary final and the alternative typed-failure path. It also checks foreign/unread denial, duplicate-admission refusal, the ten-second floor, active-slot retention during passive checking and revoked-token401. It makes **zero inference calls and zero production requests**. It is not a genuine model response, overnight proof or hosted-agent automatic-return certification.

The earlier published-v0.3.0 diagnostics remain negative historical evidence. The candidate reproductions assert positive capability and actual native delivery/claim instead of counting an absence diagnostic's exit0 as success.

## Release boundary

Independent source review must cover **both** repositories, especially `hermes-takeover.js`, the native `wake-execution.js` fence exclusions and exact-source claim attestation. Release the compatible server through the named release owner before a production native image-task proof. The current authorized proof budget is at most **six total Codex gpt-6-luna calls**, including retries/tool loops and any auxiliary requests; it has not been spent here. Publication, version/tag changes, main merges and deployment are separate parent-controlled actions.

No UI, provider/account configuration, existing profile or billing limit is changed by this candidate. This is limited focused source verification, not an exhaustive security or runtime certification.
