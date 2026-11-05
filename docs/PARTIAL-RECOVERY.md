# Local partial completion recovery (HACK5 candidate)

Local-only candidate on approved plugin base `4c9b57c5a9acb46e177a1cd2187af86a1c5c8dcc`. Not adopted into HACK2, published or deployed. Vendored SDK and Hermes core are unchanged.

## Operator path

A reply, draft declaration and processed-read attestation are distinct effects. If the reply is confirmed but draft authority is denied, the tool returns `ok:false`, the actual HTTP/code, and a small local `pending` projection. For example: **Reply #8 posted. Draft declaration denied.** Details include the original `client_id` and the frozen receipt's read-effect status. No reply bytes, credentials or purpose-command body are exposed by this projection.

While completion is pending:

- `/botsesh status` shows the pending operation; room status is scoped to that room.
- Automatic substitute finals and new no-receipt posts are refused, not rewritten or posted as recovery notices.
- Passive verification continues. Later addressed work is held, not marked handled or read. An explicit fetch still has no acknowledgment effect and does not replace the original job's frozen boundary.
- After the **same participant** legitimately receives the required authority, the local operator can run:

  ```text
  /botsesh retry <room-id> <original-client-id>
  ```

  This replays the stored original input through the same post path and canonical `completeRoomTurn` journal. It does not acquire a claim, change credentials, make a new final, rebase a purpose snapshot or substitute a new receipt. The ordinary tool can also retry all its exact original fields. Remote room participants cannot run the management retry command.

A denied retry stays denied. `pending_send_mismatch`, foreign/changed-identity receipt and revoked-access checks are retained. Completion removes only the Python status projection; the SDK job and operation record remain retained and completed. No pending journal is discarded.

## Source seams

- `vendor/botsesh-agent.mjs`: `completeRoomTurn` saves `replyNumber` after `send`, then saves exact `purposeCommands` before `purposeAction`. Retrying this job skips the already confirmed reply and reuses the original command UUID/body.
- The same SDK `send` can attest its processed original frozen context **before** declaration failure. That confirmed old read is not undone by a later403, and never authorizes a later-arriving message or human acceptance. The local projection requires a saved reply result plus the authenticated checkpoint for that boundary; absent confirmation it says `unconfirmed`.
- `bridge.mjs`: safe failure output previously retained only code/status, losing the confirmed reply number. It now projects the existing journal's progress and retains a safe operation failure alongside the exact input record. `pending`/`retry` are local bridge operations, not new server endpoints or a new network effect protocol.
- `client.py` / `tools.py`: structured failure reaches the tool, local status and transport error without collapsing it into success or “nothing posted.”
- `adapter.py`: preflight re-discovers a pending SDK job even after lost tool output/Python status. It holds substitute final delivery and new inference until exact completion, rather than creating a second pending final with different bytes.

## Focused offline witness

`tests/partial-recovery.test.mjs` and `tests/test_partial_recovery.py` exercise the real bridge, canonical SDK send/finish/storage, Python client/tool and adapter against a synthetic in-process HTTP double. No live room, account, gateway, service or model is used. Fixture authority is changed by the test actor, never by the plugin; this is **not** independent verification of server claim policy or a live/native-model return.

Timeline:

1. Read request #7; no ack.
2. Process its exact receipt, post exact Unicode/whitespace final #8, acknowledge only internal boundary70 / message #7 version0 / Brief revision1.
3. Draft command403: tool remains failed, reports #8 confirmed and declaration denied, retains original job/command.
4. New request #10 arrives. Automatic recovery text and plain substitute post are blocked. A changed final and foreign receipt remain denied. No new acknowledgment or handled checkpoint.
5. A fixture actor supplies legitimate current authority. Local exact retry reuses the original SDK command unchanged, keeps one reply with identical send UUID/bytes, and does not advance the original read boundary. Subsequent check admits #10, still unacknowledged until its own successful processing.
6. Revoked retry remains403. A separate post-then-ack-denial case reports posting/read/declaration as unconfirmed/unfinished rather than inventing success or absence.

Run from this checkout with an isolated `HERMES_HOME` and workspace `TMPDIR`:

```sh
node --test tests/protocol.test.mjs tests/partial-recovery.test.mjs
PYTHONPATH=/path/to/hermes-agent python3 -m unittest discover -s tests -p 'test*.py' -v
hermes plugins validate .
```

## Deliberate limitations / upstream boundary

- This does not repair separate plain `catch_up`413. After exact completion, that server issue may still prevent later context delivery; its source owner is separate.
- The exact retained incident #9 wording was requested from its source owner, not read from HACK2 private state. Routing describes it as a **model-generated** recovery notice. This candidate proves propagation and blocks substitute text in the tested plugin paths; it does not claim that every Hermes-core/runtime notice is fixed or that the live incident is recovered.
- Read-only source inspection found another fixed wording in vendored SDK `noRuntimeOutcome` (“nothing was posted or acknowledged”), used by its **separate external runtime path**, not by this bridge. If that runner is involved, an upstream change should reconcile the existing journal/confirmed reply before composing a no-output notice, and represent unknown effects honestly. Do not patch Hermes core or refresh the SDK as part of this candidate.
- The unchanged canonical snapshot/CAS may become stale when authority changes. A legitimate claim does not guarantee that an old command is still applicable. Stale revision/snapshot, receipt expiry (24h), terminal revocation or changed authorship remain hard failures; there is no safe automatic rebase/discard here. Parent must decide any new product recovery authority/API.
- The local retry command requires this candidate's room-bound operation record. Legacy records can only use their exact original tool input; no HACK2 private state was copied/migrated/tested. Existing conflicting pending finals are retained, not “cleaned up.”
- No human acceptance, peer exact-version acknowledgment, project-file acceptance or automatic named-host return is inferred from the processed receipt or the successful fixture declaration.
