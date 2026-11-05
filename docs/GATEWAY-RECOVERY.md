# Exact gateway recovery (HACK5)

## Root causes

The HACK2 draft-ready 403 was the existing **posting actor's authority boundary**, not a recipient-name or SEC2 actor-binding repair. The invitation enrolled an ordinary participant, but did not give that participant live manager authority or an active action claim plus answer rights. The retained reply had already posted before its subsequent declaration failed. The approved partial-completion source in `docs/PARTIAL-RECOVERY.md` keeps those effects separate and blocks substitute finals. The plugin tool/skill now make the same-actor prerequisite and honest partial result explicit. They do not acquire a claim or weaken server rules.

The remaining duplicate-recovery bug was a separate path. Hermes's `gateway/delivery_ledger.py` supplies reconnect/restart/flood prefixes. Its `GatewayStartupMixin._redeliver_claimed_obligations` calls `adapter.send` without the original current-turn state, processed receipt, client UUID, reply target or notify flag. The old adapter treated the changed text as a new gateway notice and generated a new UUID. That created a second message instead of replaying the original delivery.

## Plugin-owned fix

Before a notify-marked gateway send, persist the exact room-bound payload (original client_id, text, reply target, frozen receipt and processed flag as applicable). Keep it after handling succeeds because the generic ledger may still retry after a lost local confirmation or restart. A still-pending pre-upgrade final is also recoverable from its existing exact journal.

On a generic retry, recognize only the installed core's exact transport marker as a lookup hint, not as message content or authority. Require exactly one matching original payload in that room. Submit **that payload unchanged** through the existing client/bridge/SDK. A completed bridge operation verifies current membership and returns the original result; an incomplete operation uses its original SDK journal. No recovery preamble is posted and no fresh UUID is generated. Unmarked, exact, unique replays with no active turn use the same journal, too.

Unknown/modified or ambiguous recovery text fails closed. If two distinct turns legitimately produced identical text, the current core passes no obligation ID to resolve them; the plugin refuses rather than guessing. A replay never clears a different/newer pending final. Pending structured completions and auth, mismatch, frozen-read and room boundaries remain enforced by the existing bridge.

No production import of private delivery-ledger constants is needed. A small compatibility tuple mirrors the three current wire prefixes; the regression passes the **actual installed core dispatcher and its own constants** through the adapter. An unfamiliar English recovered-reply marker is refused. A public stable replay-operation ID/metadata seam in Hermes would remove this compatibility constraint; core is unchanged here.

The private registry retains the latest 1,000 delivery identities per room in insertion order, evicting the oldest entry so long-lived rooms can keep sending. A marked replay whose entry was evicted is refused without posting; it never receives a fresh UUID. A still-pending final remains separately recoverable. Delivery and pending-final journals are validated and saved in one atomic registry update, so a pre-post journal refusal cannot strand a newly written pending final. Existing uncertain operations are not discarded on a transport failure. Completed operations, receipt-expiry behavior and membership checks remain canonical SDK-owned. This journal is private plugin data, not a room or model-visible text cache.

## Focused execution

`tests/test_gateway_recovery.py` uses a real Python client, Node bridge, vendored SDK and the installed Hermes ledger dispatcher, with the existing explicitly **offline synthetic HTTP fixture**. It is not live-server, model or automatic-return evidence.

The identical regression on approved predecessor `17a91d73` makes four retained messages from one original plus the three recovery markers. The fixed path keeps **one exact reply and one original acknowledgment**, including after a fresh adapter instance. The test verifies changed content, ambiguous identical answers and revoked membership are denied; a completed SDK replay does not issue another message POST.

The journal-cap regression seeds 1,000 insertion-ordered records, sends the 1,001st final through the real bridge/SDK offline fixture, and checks that the oldest entry is evicted, one reply/ack exists, and a marked replay of the evicted entry is refused with no extra post. A separate conflicting-envelope refusal leaves no newly created pending final and preserves the original record.

Run with a workspace-only `TMPDIR` and scratch `HERMES_HOME`:

```sh
PYTHONPATH=/path/to/hermes-agent /path/to/hermes-venv/bin/python -m unittest discover -s tests -p 'test*.py' -v
node --test tests/protocol.test.mjs tests/partial-recovery.test.mjs
hermes plugins validate .
```

The final-SHA live plugin declaration and the fresh Fern Bakery two-agent demo are separate remaining acceptance work, to run **after independent source review**. Do not relabel the old manual SDK declaration as plugin success, clear its forensic pending journals, reuse old latency as fresh evidence, or change HACK2's frozen installs.
