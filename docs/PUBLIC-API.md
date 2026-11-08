# Public API migration — 0.5.1 catalog candidate

The catalog build imports only documented Hermes APIs and ordinary third-party
or plugin-owned modules. It does not patch validators, expand allowlists, hide
imports dynamically, or claim unsupported operations succeeded.

Reference checked: upstream `origin/main` **8d7061f1a74980a7549356a4e5e57b603475ddf0**.
Final native verification targets core PR [151209](https://github.com/NousResearch/hermes-agent/pull/151209),
**1d1280a0071c795aaea313271e124366becd3e19**. That PR fixes platform-plugin
OAuth admission and profile-copy ownership, and adds the declared public
client-ID default. It does not provide a complete public idle-work API. Until the fix is in the user's Hermes
version, ordinary 0.5.1 sign-in is not a supported release claim.

Sources:
- [Public plugin API](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/public-api)
- [Platform-adapter helpers](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters)
- [Profiles and HERMES_HOME](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- [Native slash commands](https://hermes-agent.nousresearch.com/docs/reference/slash-commands)
- [Programmatic gateway integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration)

## All 17 original private-import sites

Line numbers refer to predecessor plugin `eb160dcd7feaa693f57cb1457f882c90505e4294`.

| # | Original site / symbol | Replacement and behavior |
|---|---|---|
| 1 | `command_catalog.py:20 hermes_cli.commands.SUBCOMMANDS` | Plugin-owned documented shortcuts; no assertion that this is the complete live registry. Native `/commands` remains authoritative. |
| 2 | `command_catalog.py:21 hermes_cli.commands_platforms.telegram_bot_commands` | Same shortcuts, without Telegram-specific discovery. |
| 3 | `command_catalog.py:23 hermes_cli.plugins.get_plugin_commands` | Live plugin discovery off; installed plugin commands still work through native text dispatch and `/commands`. |
| 4 | `dm_controls.py:37 agent.i18n.t` | Plugin-owned English presentation strings. Authored text is not translated or guessed. Dynamic core/plugin language-pack inheritance needs a public translation API. |
| 5 | `dm_controls.py:469 gateway.platforms.base_exec_approval.approval_timeout_seconds` | Public re-export `gateway.platforms.base.approval_timeout_seconds`; local card expiry is not claimed to be the request's exact remaining time. |
| 6 | `dm_controls.py:554 hermes_cli.model_selection_guards.combined_selection_warning` | Custom model-picker override off. Native `/model` listing and typed selection retain core's full guards, including live-session context. |
| 7 | `identity.py:14 hermes_cli.profiles.current_profile_name` | Public `get_hermes_home()` + `hermes_constants.profile_name_for_home`/`named_profile_is_live`, limited to the active immediate home so nested custom homes cannot adopt an ancestor's profile. Active named homes survive core preparse and override saved seat choices; argv is never scanned. |
| 8 | `ops_restart.py:49 gateway.control_socket.query_gateway_control` | Automated idle restart off: no complete public live-work/admissions preflight exists. Refuses without restart; checkpoint still works. |
| 9 | `ops_status.py:23 hermes_cli.gateway_runtime.control_home_for` | Public `gateway.status.resolve_gateway_liveness` and runtime readers; no private socket/home discovery. |
| 10 | `command_catalog.py:20 hermes_cli.commands.resolve_command` | Documented shortcuts + actual native dispatch, not a copied dispatcher. |
| 11 | `identity.py:20 hermes_cli.profiles.list_profiles` | Public `PROFILE_ID_RE` and `named_profile_is_live` identity/tombstone contract; never copy marker lists or open secret/config contents. |
| 12 | `identity.py:51 hermes_cli.profiles.get_profile_dir` | Public `get_default_hermes_root(home=...)` and home override context manager; explicit nested/custom homes stay confined; reject missing, deleted and symlink targets. |
| 13 | `identity.py:107 hermes_cli.profiles.read_profile_meta` | Read documented `profile.yaml` display-name field with the stock third-party safe YAML parser. Never interpret it as identity/authority. |
| 14 | `oauth.py:33 hermes_cli.profiles.current_profile_name` | Reuse plugin-owned home-derived profile label. |
| 15 | `ops_status.py:13 hermes_cli.profiles.current_profile_name` | Reuse plugin-owned home-derived profile label. |
| 16 | `ops_status.py:23 hermes_cli.gateway_runtime.discover_gateway_endpoint` | Public liveness result and its multiplexed runtime snapshot. |
| 17 | `ops_status.py:24 gateway.control_socket.query_gateway_control` | Public runtime snapshot, PID/start-time/writer/profile matching and freshness checks. Stale/incomplete evidence is **unknown**, not healthy. |

## Attribute-level seams addressed too

A module-level lint finding is not permission to keep its private attributes.
Clarify imports only public `get_pending`, `mark_awaiting_text` and
`resolve_gateway_clarify`; typed interception tracks request IDs actually
received by this adapter instead of querying a private session registry.
Approval uses the public `ExecApprovalPrompt.request_id` and normalized actions,
then `resolve_gateway_approval_card(..., request_id=...)`; it never scans core
pending queues or guesses from equal command text. Missing request identity
falls back to native text approval. Settled/stale IDs cannot approve later work.

Slash-confirm buttons have no documented public resolver. The adapter inherits
core's unsupported-card result, which selects the real native
`/approve` / `/always` / `/cancel` fallback. It is not a fake confirmation.
The custom model-picker method is absent so core feature-detection uses its
native guarded text route, rather than calling an unguarded selection callback.
`public_capabilities.py` reports unsupported contracts as false; it detects the
public approval request-ID field without probing private modules. New APIs must
be documented/reviewed before an unsupported flag can be enabled.

The documented external `commands.catalog` protocol is not a drop-in platform
menu API: it describes a TUI/Desktop surface, omits messaging commands and may
include quick-command bodies in fallback descriptions. The plugin does not
open an extra gateway connection to launder that mismatch.
`hermes gateway status --json` is **not** a supported command on this core;
`gateway status` is human-readable, while `gateway attach --json` mints a client
ticket. Neither is a complete read-only idle-work contract.

## Preserved boundaries

Declared OAuth remains Hermes-owned through `credentialed_provider_request`.
Anonymous guest creation and exact-room manager operations remain a separate
cookie-free transport: creation must not attach the account's OAuth bearer.
No Node bridge is needed for normal OAuth/DM/status/guest operations. No new
provider, service, deployment, catalog post or publication is performed here.
