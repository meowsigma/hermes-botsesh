# Native declared-OAuth CLI smoke

Run from this workspace with the prepared Hermes interpreter:

```sh
/home/sigma/.hermes/hermes-agent/venv/bin/python plugin/tests/native_declared_oauth_smoke.py
```

The safe machine-readable result is `evidence/native-declared-oauth-smoke.json`.
Exit 0 means every implemented check ran; exit 1 preserves an actual blocker.
Do not interpret passing setup checks as passing the complete OAuth workflow.

## Isolation and custody

- Fresh private HERMES_HOME, HOME, XDG paths and empty CLI PATH on every run.
- No inherited session flags, model credentials, auth store or account cookies.
- The unchanged working-tree plugin is copied into native user-plugin storage;
  native `plugins enable` and `plugins show` verify admission and readback. This
  is **not** a network/catalog installation of the published plugin version.
- The actual sibling `server/app.js` is served by a tiny HTTPS-only test wrapper.
  An ephemeral trusted CA signs a leaf for the unchanged `botsesh.com` manifest
  host. A CONNECT relay forwards only `botsesh.com:443` to that local listener
  and explicitly refuses every other CONNECT target. There are no DNS,
  `/etc/hosts`, system trust, TLS-verification or production-server changes.
- Dependency bootstrap uses a private copy of prepared PM/cache state. Original
  public PyPI setuptools/wheel/packaging archives are downloaded using system
  TLS trust, verified against their published SHA-256, and exposed through a
  private file package index. Package download traffic is separate from the
  BotSesh HTTPS relay. No package lock/hash checks are relaxed. The prepared
  native runtime is used with lazy installs disabled; explicit native admission
  still runs normally.
- The human-context auth subprocess has PTY stdin and a clean environment. The
  harness consumes the printed authorize URL privately and submits actual local
  first-party review/CSRF consent. It never replaces OAuth code/token responses,
  reads/replays raw native tokens, patches Hermes or calls private Hermes APIs.
- Foreign/revoked negative probes use the installed plugin's public
  `OAuthClient.rpc`, which calls the documented native credentialed-request API.
  They are not represented as native CLI commands.
- No gateway/start/service or model invocation is issued. Status uses unchanged
  real core probes. All accounts, databases, credentials, logs, certs, keys,
  temporary dependency files and isolated homes are deleted in `finally`.
  Only source files and a safe JSON result remain.

## Actual blocker found

The current real native CLI refuses the stock platform plugin before it can
issue an authorization request:

```text
Plugin 'botsesh' has an invalid oauth: block in plugin.yaml:
oauth[0].client_id_env: BOTSESH_OAUTH_CLIENT_ID is a variable Hermes uses itself; declare the plugin's own
```

The source-level cause is an upstream interaction:

1. `hermes_cli/config.py::_inject_platform_plugin_env_vars` imports a platform
   manifest's `requires_env` and `optional_env` entries into `OPTIONAL_ENV_VARS`.
2. `hermes_cli/plugin_oauth.py::_hermes_owned_env` calls
   `config_env_routing.is_registered_env_name`, which treats those same injected
   entries as Hermes-owned.
3. Declared OAuth consequently rejects the platform's own required client-ID
   declaration. Both native `botsesh connect --no-start` and a real clean
   human-context `auth add botsesh:rooms` reproduce it.

The harness preserves this as `native_platform_declared_oauth_env_collision` and
fails closed; it does not change the manifest kind/variable, clear core registries,
force admission, or call private auth functions to get around it. The relevant
native OAuth source files are checked against upstream `21ae0e12` without editing
the live checkout. A separate minimal reproduction using the untouched upstream
codeload tree also confirms the same parse failure: the variable is not in
CORE_DECLARED_ENV_NAMES but is in the dynamically expanded registry. The
task-workspace `reproduce-upstream-env.py` and `upstream-repro.log` retain that proof.

The remainder of the full workflow is implemented **but not yet exercised**:
PKCE loopback exchange, exact two-room approval/connect/config/status/rooms/seats,
identity-preserving rename/reconnect, native public refresh, actual foreign and
owner-revoked room denial, sibling preservation, shared disconnect, invalid-grant
refresh denial, and native logout readback. Re-run after the runtime owner resolves
the stock platform/declared-OAuth compatibility issue.

## Fixture lessons

- Keep stock declared HTTPS hosts and route them through a trusted local TLS
  fixture; an HTTP origin override or `verify=False` tests another contract.
- Include `keyUsage=critical,keyCertSign,cRLSign` on the ephemeral CA. Current
  Python strict verification rejects a CA missing key usage.
- Native plugin enable can build a fresh dependency generation even for a
  dependency-free plugin. Preserve bootstrap failures separately; never fake
  an install by editing PM facts or bypassing admission.
- PM drops ambient `UV_OFFLINE`; use supported local package-index inputs and
  original wheel archives. Repacked cache contents correctly fail locked hashes.
- Verify platform-client declarations through a real fresh CLI, not only a
  unit helper: startup env injection can make the native manifest invalid even
  when direct parser tests pass.
