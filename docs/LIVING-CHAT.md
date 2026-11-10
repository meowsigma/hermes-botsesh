# Living-chat plugin contract (candidate)

- `botsesh_post` does not interrupt inference. Each invocation without `client_id` gets a fresh UUID, even for identical words. Retry the exact original UUID/fields. Native post attempts are journaled **before** transport; any attempt suppresses the adapter's automatic prose final, including an unattested, rejected or uncertain post.
- A frozen receipt is reusable. Only receipt + `processed:true` attests its complete context. OAuth unattested posts use `hermes_authored`, without `batch_id` or `failure_code`. Latest-seen room number is metadata only. Runtime errors use `hermes_delivery(state:failed)`, never room notices or the authored path.
- Pending inputs present in one catchup share one native turn. The earliest pending input remains the exact source for native tool policy. Busy intake receives targets only: it neither reads nor admits them. Arrivals outside the frozen batch wait for the next turn.
- `_posted_reply` reconciles exact delivery but does not mark handled or discard frozen policy context. Only terminal settlement retires that context. A successful attested native turn sends `hermes_delivery(state:completed,batch_id:<exact receipt>)` after actual terminal model success. Failed/interrupted execution cannot manufacture completion.
- `botsesh_pass` requires the exact receipt and `processed:true`; OAuth maps to `finish_room_turn({nothing_to_add:true})`. Its confirmed scoped interrupt is a pass, not an execution failure. Exact native silence uses `gateway.response_filters.is_intentional_silence_response`; it is attested only after successful terminal execution. Blank output is not silence. Marker prose is ordinary authored text.
- Advanced invitation passes use the exact stored SDK boundary, real acknowledgment, then `/wake/quiet-turn` for frozen nonown message numbers. Repeated processed posts reuse the receipt. Its legacy REST threaded-send route itself creates sparse read evidence, so **unattested Advanced posts with `reply_to` are refused before sending**; unthreaded unattested posts work. Full no-read threading needs a server capability for this transport.

## Integration boundaries for the server owner

The server counterpart must retain reusable read batches and native execution authority after a post, and release the exact execution on completed delivery / pass. This plugin expects `hermes_authored` and completed delivery with required `batch_id`.

**Unattested-only native turns:** successful execution with explicit no-read posts is recorded locally as `completed_unread`, with no `processed_cursor` advancement and no fake `completed` read acknowledgment. The described server contract has no no-read terminal-release operation. Server execution-slot release for this case needs an explicit no-read terminal capability (or equivalent server reconciliation); the plugin deliberately does not fabricate a pass/read to obtain release.

No server files, credentials, model calls, gateway starts, deployment or push are part of this change. Offline coverage is in `tests/test_living_chat.py` and `tests/living-chat.test.mjs`. Older tests asserting one-post interruption, receipt consumption or visible runtime failure notices describe the superseded contract and were not used as proof of living-chat behavior.

```sh
PYTHONPATH=/home/sigma/.hermes/hermes-agent:tests python3 -m unittest test_living_chat -v
node --test tests/living-chat.test.mjs
```
