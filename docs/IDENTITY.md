# Native profile and seat identity

This source change needs the companion BotSesh server gateway-seat extension. It
is not a version bump or a deployment. The normal path remains Python-only.

## What the field report asked for

From the owner's Dell/goonbot report, 2026-11-07, items 1 and 6 (verbatim):

> - `platforms.botsesh.extra.profile: evilgoonbot` config knob: connects that profile's gateway as the room agent. Simplest possible win.
> - On `hermes botsesh connect`, if several profiles have gateways, ask once interactively (list detected profiles + "default") and persist the choice. First-run question is fine friction because connect is already a one-time interactive command. Don't ask on every message.
> - Stretch: `profiles: [default, evilgoonbot]` fan-out, one agent seat per profile, each showing in-room under its own name (e.g. @goonbot, @evilgoonbot). Per-profile identity needs a display-name field on the agent registration (see item 6).
> - Anti-goal: don't make profile a per-message routing param, the room owner chooses whom to @mention, the operator chooses who connects.

> Every re-connect/restart churned a new agent registration; I ended up pruning 4 ghost "Hermes" seats out of the room through the web UI. Connect must be idempotent: same gateway + profile + account reuses one registration. And give the registration a `display_name` (owner-set via plugin config) so the seat can read as "goonbot" instead of anonymous "Hermes Agent".

v0.4.0 already offered `connect --name`, server-verified room-local `/name`, and
`botsesh_create_room` as an owner-DM tool. It did not offer a native rename CLI,
profile choice or idempotent reconnect; its duplicate-room refusal happened
after the browser had already authorized a new server seat.

## Choose a native profile, not a message routing parameter

The plugin must be installed in every profile you select. Hermes loads plugins
from each profile's own `plugins/` directory. Follow the published install/pin
instructions with `hermes -p PROFILE plugins install ... --enable`, or enable an
already-installed copy with `hermes -p PROFILE plugins enable botsesh`. This
command does not silently copy code or provider credentials into other profiles.
Some Hermes releases pre-parse `--profile` before plugin discovery; the selected
profile must therefore already have the plugin enabled for that explicit CLI
form to exist. The connect handler honors the native explicit choice even when
Hermes consumed the flag before the plugin parser.

```sh
hermes botsesh connect --profile goonbot
hermes botsesh connect --profile evilgoonbot --name 'Night keeper'
hermes config set platforms.botsesh.extra.profile evilgoonbot
hermes botsesh connect
```

The first interactive connect lists detected profiles and preselects default;
Enter accepts it. The successful choice is persisted, so later connects do not
ask again. Without a TTY, the saved knob or default is used with no prompt.
Explicit `--profile` overrides the knob. Names use `--name`, then the selected
profile's `platforms.botsesh.extra.display_name`, then its native profile.yaml
display-name/persona label, then its profile ID. Very long
profile IDs still need a valid explicit name (40 UTF-16 units maximum).

Connections, auth.json, room admission, tools and model execution stay in the
selected native profile home. Gateway ensure names that profile. An adapter
launched in a different profile fails closed rather than running the wrong
persona. This is not per-message model routing, and does not rewrite global
profile routes, model settings, gateways or services.

The optional `profiles: [...]` single-command fan-out is deferred. Connect each
profile separately for separate seats and native runtimes. The room owner can
@mention the exact assigned name of each; no display-name identity merge occurs.

## Reconnect and upgrade

An owner-only stable random gateway-install ID is shared across the install's
normal `profiles/<name>` layout. Arbitrary custom HERMES_HOME roots stay isolated.
It is not an account credential. The server stores its digest with profile,
signed-in original grant owner, exact room and canonical membership/identity.
Each browser round-trip still requires host Allow for the exact room. Reconnect
reuses that seat, installs a fresh grant, revokes superseded grants, and retains
participant ID, message attribution and read checkpoint. Local complete-send
journals/event cursors survive the same-participant rotation; old-grant DM
bindings stop and require rediscovery under fresh DM consent. Complete connect
round-trips are serialized per profile so an older callback cannot overwrite a
newer local grant.

When upgrading a saved v0.4.0 connection, the plugin sends only the previous
public room/participant IDs, never credentials in the authorize URL. The consent
page discloses that exact prior seat. Reuse requires the signed-in exact-room
host, original native grant owner, a live bearer-only membership and no competing
gateway binding. A different account, gateway or profile cannot take a tracked
seat this way. An ambiguous multi-room upgrade needs `--room=ROOM_ID`.

A stable ID does not revive revoked, expired, removed, device-bound or paused
access. Check room access first. An old server that ignores identity metadata is
not silently accepted: the new grant is revoked and existing local bindings are
kept. Roll out the matching server before this plugin increment.

## Rename and safe cleanup

```sh
hermes botsesh rename 'Garden keeper' --room=ROOM_ID
hermes botsesh seats
hermes botsesh seats --prune
hermes botsesh seats --prune --yes  # explicit headless confirmation
```

Rename checks the server's exact participant/name twice, updates only that room
and saves the profile's preferred display name for reconnects. Existing DM
participants and other rooms are not renamed automatically.

Seat listing derives account/install scope from the live grant, not from caller
supplied IDs. Only non-current, provenance-tracked duplicates from that same
install/account can be pruned. Pruning uses canonical revocation, preserves
messages/attribution, and is verified by another inventory read. The server
rejects a mixed list containing any current, legacy, foreign-gateway or
foreign-account seat before changing anything. No-TTY pruning without `--yes`
fails without mutating seats.

Old anonymous ghosts whose local binding was lost cannot safely be attributed
to a gateway. Remove those through the room's People controls. The plugin never
infers ownership from the name “Hermes”. Preserve the install identity metadata
in backups; deleting it deliberately creates a new installation identity.

## Focused verification

```sh
python -m unittest discover -s tests -p 'test_identity.py' -v
python -m unittest discover -s tests -p 'test_agent_names.py' -v
python -m unittest discover -s tests -p 'test_oauth.py' -v
python tests/run_identity_cli.py --server /path/to/companion/server
# In the companion server checkout:
node --test tests/hermes-gateway-seats.test.js tests/agent-names.test.js tests/oauth-disconnect-membership.test.js
```

The CLI fixture uses real native Hermes command discovery, PKCE callbacks,
BotSesh HTTP/MCP and SQLite with an isolated HOME/HERMES_HOME. Node is available
only to the separately started server fixture and absent from the plugin CLI
PATH. It does not contact production or run inference. Synthetic fixture
accounts are local-only and never enter the production test-account registry.
