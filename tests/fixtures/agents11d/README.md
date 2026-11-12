# Offline replay provenance

Source: `AGENTS11d-evidence.tar.xz`, attachment on BotSesh card `t_b320dcf2`.
Plugin pin: `e2c83389e8a23a94a66272da2982b0fa5abd492a`.
Server pin: `6462c40b5e7ba374f4789e7b02d168994ec699dc`.
Stock Hermes core: `bf0203a794a07aac3ff764972ab9087baa4e4f4e`.

The `.gz` files decompress to the original request/event files **byte for byte**.
`call-15-ledger.json.gz` is the unmodified parsed ledger row with `n=15`, including
its original completion envelope and `BrokenPipeError` observation. No response,
tool argument, receipt, UUID or model text was invented or repaired.

- Call 03 contains call 01's exact threaded post plus its actual
  `unattested_reply_target_not_supported` tool result.
- Call 15 contains the later native request and authored pass. Its actual relay
  completion envelope is replayed through a socket pair. A deterministic barrier
  reproduces the old cleanup ordering: the gateway closes first, then the pass
  response arrives. With draining first, the exact response reaches the native
  peer before stop. A genuine external disconnect remains explicitly uncertain,
  never a successful pass or an automatic resend.
- The failed 12-tool summary request was **not captured** by 11d: its assertion
  ran before request retention. `live-run.log` records its literal 12-name
  inventory. The summary regression combines that inventory and the pinned
  native schemas with the captured message history, then calls the actual core
  summary builder and BotSesh request filter. This is source-grounded offline
  reconstruction of the missing envelope, not a claim to recovered raw bytes.

All participants/rooms are the original synthetic localhost fixtures. Tests
never use their real grants or connect to production. These tests establish
code/transport behavior, not a successful model conversation or 3/3 settlement.
