# Recent room history in native Hermes wakes

This change builds on Step 3's exact plugin `d37b2b92cce4d57146821140c6df9152184df708`. It does not change Hermes's per-sender session routing.

## Contract

A targeted native OAuth catchup now includes at least the latest **20 full retained room messages**, or all retained messages when fewer exist, plus every unprocessed row needed to preserve the existing complete boundary. All authors are included. The current Brief remains in that same frozen packet. Native compact messages retain the original `createdAt`, canonical author and room number.

The plugin places this history and Brief before the existing final `CURRENT BOTSESH INPUTS` section. `isOwn` is computed from the canonical participant ID, never display names. `your_earlier_message_numbers` and a plain legend explicitly identify the agent's own earlier messages. Replayed history is context, not a new instruction or request.

History is not a passive add-on that is silently attested by a different batch: its exact message versions are included in the **same** frozen server receipt. Fetching still has no read effect. New arrivals after the freeze remain outside it. The existing server output cap, complete-page requirements and plugin 160,000-byte result / 160,000-character final-input fences remain. If complete context cannot fit, the turn fails closed with `context_limit`; it does not clip a message and then acknowledge it.

The receipt's internal `nativeWorkFrom` retains the original pending-work boundary independently of the expanded context interval. Replaying an old answered message cannot create a new quiet pass, complete an old task again, target an old task failure, or borrow a historical sender's tool permission. The field is stripped before public `contextAck` validation. Legacy receipts without it retain their previous behavior.

## Integration

This fix also requires the companion BotSesh server branch `fix/native-room-history-t_17ecacae`, built on Step 3 server `2ba2330a21ff1dce610d7cba7c05cdcf23f4c7c5`. Deploy the reviewed combined server before publishing the combined Steps 3–5 plugin release. The plugin does not independently fetch or cache a second copy of room history. Old servers cannot supply this window. No tag, version bump, publication or deployment is part of this task.

The three-room live setup also exposed a pre-existing grant-wide pagination bug: a paged `list_rooms` call had no `args`, and the continuation dereferenced it and supplied an invalid null room. The paging helper now omits absent room scope while preserving the exact grant cursor. A focused base-RED regression covers this; room-scoped pagination is unchanged.

This native wake fix is not evidence about scheduled Muse or another client host, nor a claim that every model will recall arbitrary content correctly.

## Focused reproduction

Server:

```sh
node --test tests/native-room-history.test.js tests/living-chat-batch-settlement.test.js tests/hermes-tool-policy.test.mjs
```

The new regression has first human #0, agent answer #1, acknowledgment #2 and a distinct second human's recall question #3. It requires the exact old answer and Brief in the new receipt, verifies timestamps and full 20-message retention, and keeps late arrivals, budget refusal, read ledgers, quiet-state truth, foreign access and revocation boundaries intact. It fails on the Step 3 server base because message #1 is absent and the 20-message window collapses to the new message.

Plugin (with the installed Hermes core on `PYTHONPATH`):

```sh
python3 -m unittest discover -s tests -p test_recent_room_history.py -v
```

The admission regression verifies the actual event text, own-message legend, canonical identity even with duplicate display names, final current-input ordering, unchanged frozen result and exact receipt numbers. This is an offline double, not live model proof. The task's separate retained local-only native harness proves the actual recall path.
