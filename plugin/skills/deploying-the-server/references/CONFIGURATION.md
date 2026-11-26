# Setting up selenium-flow

The operator's side: what the server needs, how a client connects, which setting
to change. Every setting, in all three spellings, is on the wiki's
[Configuration](https://github.com/kubed-io/selenium-flow/wiki/Configuration)
page.

## What it needs

A reachable Selenium Grid. The server holds no browser: it speaks WebDriver to
the Grid, which runs Chrome and Firefox.

```
GRID_URL=http://selenium-grid-selenium-hub.<namespace>.svc.cluster.local:4444
```

`GET /ready` reports whether the Grid is reachable and how many browsers are
live; a server that cannot see a Grid is not ready. `/health` says the process is
up, `/started` that it finished starting, `/info` what it is wired to. None needs
credentials, and all four answer at the root as well as under `ROUTE_PREFIX`.

```bash
docker compose up --build      # server + a standalone Grid, auth off
```

The image is `kubed/selenium-flow`. A person watches a browser work in the admin
UI's Live tab.

## Settings and where they come from

A setting can be in a YAML config file, the environment or the command line, and
a later one wins: `default < config < env < args`. The file path is the name:
`redis.host` is `REDIS_HOST` and `--redis-host`. Point at the file with
`--config-file` or `CONFIG_FILE`; there is no default location, and an unknown
key in it stops the server.

| Env | Default | Change it to |
|---|---|---|
| `AUTH_TOKEN` | unset | set the bearer token for both surfaces; unset means no auth |
| `WORKSPACE_STORE` | `memory` | `redis`, to keep workspaces across a restart |
| `WORKSPACE_TTL` | `86400` | keep a workspace longer after its last use; it does not bound the browser |
| `SESSION_KEEP_ALIVE` | `900` | keep a browser alive longer between calls |
| `SESSION_SUB_TTL` | `3600` | seconds a sub-session lasts after its last use |
| `DATA_DIR` | unset | give workspaces their flows and files, under `DATA_DIR/workspaces/<name>/`; unset means none |
| `RECORDING_ENABLED` | `false` | allow `open_session(record=true)`, once the Grid's recordings reach `RECORDING_DIR` |
| `RECORDING_DIR` | `$DATA_DIR/recordings` | where the Grid's recordings arrive. The recorder must keep `SE_VIDEO_FILE_NAME=auto` and `SE_VIDEO_FILE_NAME_SUFFIX=true` (or `SE_VIDEO_SESSION_SUBFOLDER=true`) so the Grid session id is in the path |
| `RECORDING_WAIT` | `600` | seconds to wait for a recording after its browser ends |
| `RECORDING_WATCH` / `RECORDING_POLL` | `auto` / `1000` | `events` or `poll` to watch that folder, and the poll interval in ms |
| `SECRETS_DIRS` | unset | colon-separated directories of secrets; unset means none (*Secrets on the server*) |
| `PUBLIC_BASE_URL` | unset | where browsers reach this server: file links become absolute, and `workspace://current` gains `config_url` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `WARNING`, `ERROR` or `CRITICAL` |

A browser lives until the Grid's own idle timeout (`SE_NODE_SESSION_TIMEOUT`, on
the node) runs out after the keep-alive. The server runs no cleanup loop: the
next call after a reap reopens the browser.

## Connecting a client

Point an MCP client at `/mcp` and anything else at `/browser/*`, under the mount
when `ROUTE_PREFIX` is set: `/flow/mcp` for `ROUTE_PREFIX=/flow`.

```json
{
  "selenium-flow": {
    "type": "http",
    "url": "http://selenium-flow.<namespace>.svc.cluster.local:8000/mcp",
    "headers": {
      "Authorization": "Bearer <token>",
      "Sf-Workspace": "my-client"
    }
  }
}
```

`/mcp` takes only `Authorization: Bearer <token>`. The HTTP endpoints also take
the bare token as the header value, for clients that cannot send a scheme.

A caller with no workspace name is refused with a message saying how to set one.
`skill://selenium-flow/driving-a-browser/references/WORKSPACES.md` has the naming rules. Two per-client switches follow. The first says whether the
client's model can read MCP resources: `Sf-Resources: off` or `on` (or its alias
`X-MCP-Resources`), or `?resources=off` or `on`, and the `Sf-` name wins. A client that is off is given the
tools `list_resources` and `read_resource` instead. With neither sent, Visual
Studio Code counts as off and every other client as on. The second says how the client
already has the plugin's skills, prompts and agent: `Sf-Via: local` (or `?via=local`) keeps
all of them out of every listing; `extension` keeps the prompts and the skill rows out of
the listings and leaves the skills and the agent to the client's extension. Either way the
instructions name the skill the client has, and everything still reads. Unset, it is `mcp`,
and everything comes from this server.

## A client's session defaults

The browser, its version, the window and the two timeouts resolve as
`server default (session.*) < client default (parameter or header) < open_session argument`.

| Setting | Env | Parameter | Header |
|---|---|---|---|
| Browser | `SESSION_BROWSER` | `?browser=` | `Sf-Browser` |
| Browser version | `SESSION_VERSION` | `?version=` | `Sf-Version` |
| Window width | `SESSION_WIDTH` | `?width=` | `Sf-Window-Width` |
| Window height | `SESSION_HEIGHT` | `?height=` | `Sf-Window-Height` |
| Page load timeout (s) | `SESSION_PAGE_LOAD_TIMEOUT` | `?page_load_timeout=` | `Sf-Page-Load-Timeout` |
| Script timeout (s) | `SESSION_SCRIPT_TIMEOUT` | `?script_timeout=` | `Sf-Script-Timeout` |

- The header beats the parameter. The old `X-Browser`, `X-Version`,
  `X-Window-Width`, `X-Window-Height`, `X-Page-Load-Timeout` and
  `X-Script-Timeout` headers are refused, naming the `Sf-` header to use.
- A version belongs to its browser: a client or call naming another browser
  discards `SESSION_VERSION`, so that browser opens at its latest.
- `insecure` and `headless` have no default anywhere; only `open_session` sets
  them.
- No bound is checked on the window or the timeouts, and one left unset is the
  browser's own default. Set `SESSION_PAGE_LOAD_TIMEOUT` so a hung navigation
  does not hold a Grid slot.
- An unusable client value is ignored with a warning, and `open_session`
  reports the settings it resolved to. A bad `session.*` value from the
  operator stops the boot, and `open_session(browser="chrom")` fails rather than
  opening Chrome. The env var is `SESSION_BROWSER`, not `BROWSER`.

`SESSION_CAPABILITIES` is a JSON object of W3C capabilities every new session
sends, such as `{"kubed:pool": "default"}` for a Grid whose node classes each
carry one. A key a caller's own `capabilities` sends replaces it. A key an
`open_session` option controls (`browserName`, `browserVersion`, ...) stops the
boot, naming the option. No per-client switch sets it.

## Uploads need a writable directory

`/browser/upload` stages the file on this server before Selenium ships it to the
Grid node. The image runs read-only as an unprivileged user, so mount an
`emptyDir` at `/tmp` or point `TMPDIR` at a writable path. Without one, an upload
fails with "cannot stage the upload: no writable temporary directory".

## Secrets on the server

An operator puts every secret on the server; no tool and no admin page creates,
changes or reads one. A secret is a directory under one of `SECRETS_DIRS`,
named for the secret, with one file per key:

```
/secrets/nextcloud/username
/secrets/nextcloud/password
/secrets/nextcloud/_description      Admin login for Nextcloud
/secrets/nextcloud/_allowed_urls     https://nextcloud.example.com
```

- A file's content, stripped of surrounding whitespace, is the key's value, read
  when it is typed, so a rotated file is used at once. A file whose name starts
  with `_` or `.` is not a key.
- `_allowed_urls` is the leash: one bare origin per line, scheme, host and port,
  such as `https://nextcloud.example.com`. A secret without one, with no origin
  in it, or with any line that is not a bare origin (a path, a query,
  credentials) is refused, and agents see it listed with `refused` and the
  sentence that says what to fix.
- A port counts as written: `https://host:443` and `https://host` are two
  origins. List each origin the browser signs in on.
- A name in two directories comes from the first only. Every workspace on the
  server sees every secret.

On Kubernetes, mount each Secret as a directory, with `_allowed_urls` as one of
its keys:

```yaml
volumes:
- name: nextcloud
  secret:
    secretName: nextcloud-admin
containers:
- name: selenium-flow
  env:
  - name: SECRETS_DIRS
    value: /secrets
  volumeMounts:
  - name: nextcloud
    mountPath: /secrets/nextcloud
    readOnly: true
```

The config file can define a secret too, or overlay a directory's, under
`secrets.entries`; that key is read from the file only. Its `allowed_urls`
replaces the directory's leash whole. Each key is exactly one of `file`, `env`
or `value`, and `masked: false` leaves a key that is not secret out of the
scrub:

```yaml
secrets:
  entries:
    nextcloud:
      description: Admin login for Nextcloud
      allowed_urls:
      - https://nextcloud.example.com
      keys:
        username:
          env: NEXTCLOUD_USER
          masked: false
        password:
          file: /run/secrets/nextcloud-password
```

A bad line in a config entry's `allowed_urls` stops the boot instead.

## The admin token as a secret

An agent can do admin work in its own browser when the server's token is a
secret it can type (`skill://selenium-flow/using-the-admin-ui/references/AGENT_AS_ADMIN.md`).
Allow it on the exact origin the Grid's browsers load the admin page from, which
may be the Service's address rather than the public one. With the token in
`AUTH_TOKEN`, the key can read it from there:

```yaml
secrets:
  entries:
    admin:
      description: The admin page's token
      allowed_urls:
      - https://flows.example.com
      keys:
        token:
          env: AUTH_TOKEN
```

Every workspace sees every secret, so every caller of the server can then sign
in to the admin page. Leave it out where that is too much.

## OIDC

The server can accept a JWT from one OIDC issuer beside the token: on `/mcp`,
and in the admin page's **Sign in with OIDC**. The token stays the admin login,
so OIDC needs `AUTH_TOKEN`.

| Setting | Env | Is |
|---|---|---|
| `oidc.issuer` | `OIDC_ISSUER` | the issuer a JWT must name; with the next two, turns OIDC on |
| `oidc.audience` | `OIDC_AUDIENCE` | the audience a JWT must include |
| `oidc.jwks_uri` | `OIDC_JWKS_URI` | where the issuer publishes its signing keys |
| `oidc.roles` | `OIDC_ROLES` | on `/mcp`, a JWT must hold one of these; empty checks none, and the boot warns |
| `oidc.roles_claim` | `OIDC_ROLES_CLAIM` | the claim holding the roles, `roles` by default; dots walk into objects, as in `realm_access.roles` |
| `oidc.client_id` | `OIDC_CLIENT_ID` | the admin page's public client; setting it shows **Sign in with OIDC** |
| `oidc.admin_roles` | `OIDC_ADMIN_ROLES` | a sign-in on the admin page holding one of these is the admin |

- `oidc.issuer`, `oidc.audience` and `oidc.jwks_uri` are set together or not at
  all, and so are `oidc.client_id` and `oidc.admin_roles`; `oidc.client_id`
  needs the first three. Each broken rule stops the boot with a sentence naming
  it. In the environment, the two role lists are comma-separated.
- A JWT is RS256, signed by a key at `oidc.jwks_uri`, names `oidc.issuer` and
  includes `oidc.audience`, and has an `exp`.
- The admin page's client is public: Authorization Code with PKCE (S256), no
  client secret. Its access tokens must carry `oidc.audience` too, and `azp`
  must be `oidc.client_id`; a token minted for another client is refused.
- The redirect URI is the admin page's own address with one trailing slash:
  `https://flows.example.com/`, or `https://flows.example.com/flow/` under
  `ROUTE_PREFIX=/flow`. Register it on the client. The page talks to the issuer
  from the browser, so the issuer must also accept requests from the page's
  origin (Keycloak's *Web origins*).
- A role in `oidc.admin_roles` lets a person in; a sign-in without one is told
  it holds no admin role.

```yaml
oidc:
  issuer: https://sso.example.com/realms/kubed
  audience: selenium-flow
  jwks_uri: https://sso.example.com/realms/kubed/protocol/openid-connect/certs
  roles_claim: realm_access.roles
  roles:
  - selenium-flow
  client_id: selenium-flow-admin
  admin_roles:
  - selenium-flow-admin
```

The file can hold `oidc` while `AUTH_TOKEN` comes from the environment: the
rules run once every layer has merged.
