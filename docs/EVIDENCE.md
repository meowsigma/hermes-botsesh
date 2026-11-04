# Native Hermes integration evidence

Recorded 2026-11-04 against **LIVE botsesh.com**, in an owned synthetic guest room. This is **LIMITED OWNER-AUTHORIZED SMOKE COVERAGE**, not exhaustive certification or a production release.

## Native execution

Hermes 0.22.5 / `openai-codex/gpt-6.1-sol` ran inside an isolated Docker runtime with a scratch `HERMES_HOME`, narrowly authorized task-owned participant IDs and the installed BotSesh plugin. The owner's default profile and host gateway were not modified. Existing authorized sign-in was used; no paid API, new account or provider installation was added.

| Check | Actual result |
| --- | --- |
| Native `/botsesh join @<private 0600 file>` | 1.341 s; zero model tokens |
| Addressed question #5 → native Hermes reply #6 | 5.678 s; exact same enrolled participant; reply target #5 |
| Distinct later SDK-peer handoff #7 → native Hermes reply #9 | 5.508 s; same Hermes identity; no manually launched model turn |
| Final Python/bridge source, after isolated gateway restart | Queued request #11 → correlated reply #12; no pending final remains |
| Final reply timing | 5.239 s after adapter-connected; **169.284 s from the original request**, including a failed harness startup/configuration attempt |
| Native `/botsesh leave` + `/botsesh status` readback | `active:false`, `state:detached`; zero inference; not server revocation |

The final harness initially omitted `platform:"botsesh"` from Hermes's `HomeChannel` configuration. That run timed out; it is not counted as a fast successful trial. The scratch configuration was corrected, the gateway restarted normally, and the queued question was independently read back as #12. The README contains the working full configuration shape.

## Read and send boundaries

Independent authenticated SDK readback verifies the exact sender, reply target and server-observed processed-context receipts. Reply #6 covers messages #0–#5 and Brief revision 1; reply #9 covers #0–#7; the final #12 covers #0–#11. None reports file-byte processing. A server receipt is **not proof of comprehension or model input**; it is paired here with native runtime/session evidence rather than treated as that proof by itself.

Native Hermes actually invoked `botsesh_rooms` and `botsesh_read`. Their complete successful tool results were checked in the scratch session database; the streaming CLI truncates large tool-output previews and is not a substitute for that record.

Live bridge checks, using the same SDK path as the tools:

- Guest creation succeeded in 1.227 s. Two participant-only invitations were prepared privately, then genuinely enrolled by the two runtimes.
- Passive read left the Hermes checkpoint **2237 → 2237**.
- An ordinary non-manager's invitation attempt was denied **403**.
- A foreign processed receipt was denied.
- An unassigned `routine_report` was rejected before posting its reply.
- Draft-ready completion produced #10; the exact retry produced no second message, while changed intent under the same UUID was denied.
- A separate fresh read confirms `draft_ready.ready:true` bound to message **#10, version 0**, digest `09bd989dcc8647533cfb8114f387ad8dd859263819c8068a1345ceb466bcb7ef`.

Credential-free retained values are in [live-evidence.json](live-evidence.json). Raw invitations, credentials, signing material and provider snapshots are deliberately excluded.

## Focused offline checks

- **16 Python unit tests** passed: profile-private storage, room boundaries, local-only command controls, consented account dispatch, UUID retry binding, successful-model attestation gate, failed-turn behavior, durable final replay and cron execution identity.
- **4 Node protocol tests** passed: complete non-acknowledging paging; malformed/foreign/inconsistent paging rejection; draft/routine output schema with no approval action; and canonical 401/403-revoked/410 error classification. These are offline doubles/schema checks, not live revocation proof.
- `hermes plugins validate <repo> --install-deps --json`: all checks pass, security scan `safe`, no core overrides.
- `hermes plugins doctor <repo> --ci`: passes; **5 tools / 1 hook** registered. The isolated CLI also emitted an optional `pm-runtime` preparation diagnostic because lazy installation was disabled; PM functionality is not used by the plugin, and no unrelated installation/guard was changed.

**Remaining validator warning:** `tools.py` imports `gateway.session_context.get_session_env`. Hermes's slash-command documentation explicitly documents this helper, but the current validator's public-import allowlist does not include it. It is not monkey-patched. Independent review must resolve/accept that documented seam before catalog submission; no warning-free or catalog-admitted claim is made.

## Actual UI evidence and cleanup

[Desktop](live-room-desktop.png) and [phone](live-room-phone.png) screenshots show real retained room history, not a staged UI. Phone: Chromium emulation at **390×844**, coarse pointer/touch enabled, no horizontal overflow; not a physical phone test. An ordinary viewer reload restored its same visible identity and connected history. A transient reconnect/partial-directory state recovered through native retry/reload; final desktop readback was connected with no partial-directory notice. Screenshots retain ordinary Hermes diagnostic chatter and the live site's update banner.

The plugin was detached locally, the isolated gateway shut down normally, and status readback confirmed it is not running. The task's copied provider access snapshot was deleted. No native model process or SDK stream remains in the container; its idle `sleep infinity` container is retained for the operator's normal cleanup. The viewer's isolated browser context is disposed during handoff. The guest room and synthetic memberships expire naturally; local detach is not membership revocation or erasure.

## Not proven or not implemented

No live signed-in account creation, actual membership revocation, successful due routine occurrence/scheduled autonomous return, full endurance, physical-device input, media/file delivery, E2EE, or named ChatGPT/Claude/Grok autonomous return is claimed. A scripted peer proves transport handoff, not another reasoning model. Scheduled transport/negative assignment boundaries are implemented and focused-tested, but need a separately authorized real scheduling trial.

The repo remains **private**, source is committed locally for independent `bscheck` review, and no Git push/publication, catalog PR, X post, demo video or submission form is performed by this implementation phase. The separate catalog draft pins that local standalone commit and must not be submitted until the owner approves publication and the reviewed commit is available remotely.
