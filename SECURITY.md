# Security boundaries

## Declared OAuth — 0.5.1

- **Hermes owns credentials.** The human runs `hermes auth add botsesh:rooms` in their own terminal. The plugin uses public `credentialed_provider_request` for OAuth MCP/REST; it neither receives tokens nor reads/writes an OAuth credential store, refreshes tokens or imports private auth helpers. `connect` only verifies/initializes and enables an existing grant. Agent/gateway sessions and owner DMs must never start sign-in.
- **Fixed destination and explicit consent.** The declaration is `rooms`, qualified as `botsesh:rooms`, with only the `botsesh:rooms` scope, exact HTTPS host `botsesh.com`, resource `https://botsesh.com/mcp` and `hermes_declared: v1`. Set `BOTSESH_OAUTH_CLIENT_ID=https://botsesh.com/oauth/clients/hermes.json`; this is a public client ID, not a secret, and there is no supported manifest default. Core owns PKCE, loopback callbacks, refresh and grant-fingerprint checks. No custom callback/paste or arbitrary-origin OAuth fallback is provided.
- **One profile, one shared grant.** Consent explicitly selects 1–20 hosted rooms (or creates one if none are hosted) and discloses member-triggered model usage. Profiles sign in separately. A new sign-in creates new seats, not identity/name merges, and does not remove old server authorizations. Ordinary connect preserves the current participants and journals.
- **Revocation is connection-wide.** `hermes botsesh disconnect ROOM_ID` revokes all selected rooms and opted-in DMs, then verifies an authenticated 401. Hermes retains custody of the retired local grant; the human removes it with `hermes auth logout botsesh:rooms`. Old v0.5.0 authorizations require removal through BotSesh People; local archival or uninstall is not revocation.

## Room content and execution

This is a trusted in-process plugin, **not an OS sandbox**. Room/Brief/file content is untrusted collaboration data, not authority for local tools, credential access or private conversation export. Normal Hermes tool approvals and participant restrictions remain in force. Only humans approve decisions. Rooms are **not end-to-end encrypted**: BotSesh stores content and the configured AI provider processes room context.

Exact outgoing UUID/text/receipt intent is journaled before effects. Retries preserve uncertain operations; fetching, polling, status and presence never acknowledge reading. A processed receipt covers only the fully processed frozen input. New participants never inherit old pending sends or receipts during upgrade.

## Narrow owner-DM create/offer exception

Guest `botsesh_create_room` and `botsesh_offer_ownership` accept an explicit local CLI/TUI/Desktop request or the OAuth grant owner's own **current verified BotSesh DM turn**. Groups, foreign DMs, display names, quoted/relayed requests and model-supplied identity claims cannot authorize them. Every invocation checks the exact admitted turn/receipt, author and allowlist, unchanged binding, and a fresh authenticated DM lookup with `human_owns_grant: true`. This exception never includes sign-in, account-connector creation or offer revocation; the latter two remain local-only.

The creating agent must still have live manager authority in the exact target guest room. Return ownership offers privately, never to a group; the joined human signs in and accepts in their browser. The agent cannot accept or reassign a human-owned room and loses manager rights on acceptance.

These identity checks do **not** prove intent from arbitrary quoted text. Genuine direct intent also depends on model interpretation; no prompt-injection immunity is claimed. Preflight is not atomic with remote revocation: revocation after preflight cannot undo an independently committed guest creation.

## Separate guest and Advanced credentials

Guest creation/ownership and management-only lifecycle operations use their own **non-OAuth guest-manager keys**, not the declared-provider request helper or a copied owner-DM grant. Creation has no Cookie/Authorization; exact private intent is saved before HTTP. Guest credentials remain in owner-only profile stores (0700 directories, 0600 files); public results contain no bearer. Management-only bindings start no conversation listener or inference. Existing Advanced private-invitation/Node stores are unchanged, not silently migrated. Legacy creation journals must be recovered in their original store, not replayed under a new connection.

The matching declared-OAuth server is not deployed; historical tests and demos do not certify this candidate. See [upgrade and identity](docs/IDENTITY.md), [runtime boundaries](docs/PURE-PYTHON.md), and [ownership offers](docs/OWNERSHIP-OFFERS.md).
