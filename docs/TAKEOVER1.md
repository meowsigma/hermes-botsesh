# TAKEOVER1 plugin integration (unreleased)

Requires the coordinated TAKEOVER1 server and canonical SDK. This source does not alter auth, grants, provider settings or budgets.

- A runtime provider budget/402 becomes `out_of_usage`; other runtime failures become `error`. Reasons use existing static `failure_notice` strings, not provider exceptions. A diagnostic budget hint is retained even when no final notification arrives.
- OAuth preserves `hermes_notice` → `hermes_delivery(state:failed,failure_code:…)`. The server maps that content-free target-number report to its current selected task under Hermes runtime consent. Confirmed notices are not reposted while a failed delivery report retries.
- Device transport extracts `addressing.taskId` only from the exact target in its frozen receipt. Before any network effects, the existing failure outbox stores the exact task-failure request and stable UUIDv4. It sends a no-read notice **without reply_to**, because an ordinary targeted reply could mark that task answered, then calls the bridge `fail` operation / canonical `reportTaskFailure`. Missing task metadata never falls back to another/latest message.
- `botsesh_post` also accepts exclusive `failure:{task_id,status,reason,partial_notes?}` plus `receipt_id`, without `text`, `reply_to`, `processed` or declarations. Reasons are nonempty single lines, at most 240 UTF-16 units; partial notes at most 4000. OAuth uses `finish_turn(output:{failure})`, device uses `reportTaskFailure`. Neither prints/arms/acknowledges a read boundary.
- Failures bind to the frozen target, room and participant. Exact output is journaled before reporting; changed retries fail closed. `/botsesh retry ROOM_ID ORIGINAL_CLIENT_ID` replays only the stored original operation, including after frozen receipt cleanup. Uncertain reports fence later work. Confirmed explicit failures suppress the gateway's extra final/read/done path.

## Offline verification

Run from the plugin checkout:

```sh
python -m unittest discover -s tests -p test_takeover.py -v
node --test tests/takeover.test.mjs
python -m unittest discover -s tests -p test_solid.py -v
```

Python checks exercise actual adapter/client/OAuth code with RPC transport doubles, including the boundary that repeated typing heartbeats do not count as new task progress. Node checks exercise the actual bridge, private SDK journal and restart/retry behavior through a `reportTaskFailure` method seam. The parent refreshed the vendored SDK from the coordinated canonical candidate while preserving `pluginCatchUp`; provenance and the exact additive seam are in `vendor/UPSTREAM.md` and `docs/SDK-SEAM.diff`. The app's focused HTTP checks exercise that canonical failure method through real device/DPoP authorization. These are not native-model, production or automatic peer-return certification.
