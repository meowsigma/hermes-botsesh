# Reply argument recovery (unpublished F5-03 / F5-04 fix)

- `botsesh_post` and `botsesh_read` tolerate unrecognized **top-level** fields. Their handlers drop them and log escaped, bounded key names only, never values. Other tools and nested structured declarations remain strict.
- An omitted `room_id` uses only the current native BotSesh channel from task-local session context. Local CLI calls still need an explicit room, and explicit foreign or empty rooms remain denied. The profile's room list is never a fallback.
- `batch_id` is discarded, never aliased to `receipt_id`. `processed:true` is never supplied on the model’s behalf. A threaded post requires `reply_to` + `receipt_id` + `processed:true`; incomplete calls now receive a corrective error with the unique matching open receipt or up to three labelled candidates and an exact corrected call shape. The tool never changes a requested thread into an unthreaded post. The published 0.7.2 receipt resolver remains for ordinary unthreaded posts with explicit `processed:true`: only this native input’s unique complete, unexpired, same-participant frozen receipt can attach; otherwise content posts with a no-read notice and no structured declarations. Supplied nonempty unknown/foreign receipts fail. Authored and wire fields remain separate and immutable across retries; older unresolved audits retain their original payload.
- Any explicit post attempt prevents `botsesh_pass` from manufacturing a subsequent “nothing to add” outcome. After a confirmed post, it returns `already_replied` as a harmless local no-op: no extra read, quiet pass, transport call or native interruption. If no post succeeded, the failed/uncertain-attempt refusal remains. Native terminal settlement still recovers uncertain original send identities; definitively rejected posts report failure without read acknowledgment. A confirmed answer still owns its exact target, even if a later native operation fails.
- Definitive reply-delivery failures retain the existing `runtime_error` failure code and additionally send `failure_stage: "reply_delivery"`. The server projects “tried to reply; delivery failed”, refreshes the source through the existing edit cursor, and avoids a second quiet-pass footer for the same recipient. Task-level `cant_do`, `out_of_usage`, and `error` semantics are unchanged.

## Integration order

Deploy the companion BotSesh server change **before** publishing this plugin increment. The additive nullable `failure_stage` column and optional MCP field must be accepted before this plugin can send typed delivery-failure receipts. No plugin version bump, tag, package publication, or server deployment is part of this source card. Roster/channel readiness is the separate Step 5 change.

## Focused reproduction

Set `HERMES_CORE` to an installed Hermes checkout and use its prepared Python:

```sh
PYTHONPATH="tests:$HERMES_CORE" python3 -m unittest \
  test_reply_argument_slip test_processed_receipt_journal test_confirmed_pass \
  test_living_chat test_plugin.Boundaries test_final_after_post -v
```

`test_reply_argument_slip` enters Hermes's actual deferred `tool_call` argument validator, registered plugin dispatcher, and executor hook. Direct handler dispatch alone does **not** reproduce Fable's extra-property rejection. Historical incomplete threaded shapes now teach before the caller explicitly corrects the attestation or chooses an unthreaded post. Opaque room/receipt identifiers and Fable question #2 are mapped to the frozen fixture's room/receipt/question #3; answer bytes stay exact. `test_threaded_post_guidance` separately replays both literal Bo 11g requests, extracts and executes the corrected follow-up, and covers ambiguity and immutable retries. `test_threaded_post_http` exercises the corrected thread against an explicitly selected local server, without inference.

The companion server has `tests/reply-delivery-status.test.mjs` (real local OAuth/MCP, read/scope negatives, existing edit-cursor refresh, client status precedence) and retains the completed-takeover status regression. Real local-model and desktop/phone evidence is attached to the implementation card; it is localhost evidence, not a production release or a full-suite certification.
