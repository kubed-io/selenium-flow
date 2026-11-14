# Setting up and configuring selenium-flow

This is the operator's half: what the server needs, how a client connects, and
which setting to change. For a failed browser call see
`skill://selenium-flow/references/TROUBLESHOOTING.md`.

## What it needs

A reachable Selenium Grid. The server holds no browser itself: it speaks
WebDriver to the Grid, which runs Chrome and Firefox.

```
GRID_URL=http://selenium-grid-selenium-hub.<namespace>.svc.cluster.local:4444
```

`GET /ready` reports Grid reachability and the live browser count, and a server
that cannot see a Grid is not ready. `/health` says the process is up,
`/started` that it finished starting, and `/info` what it is wired to. None needs
credentials, and all four answer at the root as well as under `ROUTE_PREFIX`.

## The config file

Every setting can be set in a YAML config file, in the environment, or on the
command line, and a later one wins: `default < config < env < args`. A setting's
file path is its name: `redis.host` is `REDIS_HOST` in the environment and
`--redis-host` on the command line. Point at the file with `--config-file` or
`CONFIG_FILE`; there is no default location, and an unknown key in it stops the
server. Every setting, in all three spellings, is on the wiki's
[Configuration](https://github.com/kubed-io/selenium-flow/wiki/Configuration)
page.

## Running it

```bash
docker compose up --build      # server + a standalone Grid, auth off
```

The image is `kubed/selenium-flow`. To watch a browser work, a person opens the
admin UI's Live tab.

## Connecting a client

Point an MCP client at `/mcp` and everything else at `/browser/*`, under the
mount when `ROUTE_PREFIX` is set: `/flow/mcp` and `/flow/browser/*` for
`ROUTE_PREFIX=/flow`. Only the four ops endpoints also answer at the root.

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

The HTTP endpoints accept the same `Authorization: Bearer <token>`, and also a
bare token as the header value for clients that cannot express a scheme. `/mcp`
takes only the bearer form.

## The three per-client switches

These go on the MCP URL as query parameters, or as headers.

| Switch | Header | Parameter | Does |
|---|---|---|---|
| Workspace name | `Sf-Workspace` | `?workspace=<name>` | Names your workspace, so it holds your browser and survives a reconnect. A header and the parameter together are refused. `X-Workspace` is an alias of the header, for a client that can only send allowlisted header names. |
| Resources | `X-MCP-Resources: off` or `on` | `?resources=off` or `on` | Whether this client is given `list_resources` and `read_resource`. The header wins over the parameter. With neither sent, Visual Studio Code is not given them and every other client is |

A workspace name gives you a browser and a library of your own
(`skill://selenium-flow/references/WORKSPACES.md`). Without one a caller is
refused, with a message saying how to set one.

## Server settings worth knowing

Every setting, in all three spellings, is on the wiki's
[Configuration](https://github.com/kubed-io/selenium-flow/wiki/Configuration)
page; these are the ones that change behaviour you will notice.

| Env | Default | Why you would change it |
|---|---|---|
| `AUTH_TOKEN` | unset | Sets the bearer token for both surfaces. Unset means no auth |
| `WORKSPACE_STORE` | `memory` | `redis` to keep workspaces across a restart |
| `WORKSPACE_TTL` | `86400` | How long a workspace is kept after its last use. Not the browser's lifetime |
| `DATA_DIR` | unset | The data root: workspaces (flows, kept files, screenshots, recordings) live under `DATA_DIR/workspaces/<name>/`. Unset means none of them |
| `RECORDING_ENABLED` | `false` | The operator says the Grid's recordings reach `RECORDING_DIR`; without it `open_session(record=true)` is refused |
| `RECORDING_DIR` | `$DATA_DIR/recordings` | Where the Grid's recordings arrive; the server moves each finished one into its workspace. The recorder must keep `SE_VIDEO_FILE_NAME=auto` and `SE_VIDEO_FILE_NAME_SUFFIX=true` (or `SE_VIDEO_SESSION_SUBFOLDER=true`) so the Grid session id is in the path |
| `RECORDING_WAIT` | `600` | Seconds after a browser ends to wait for its recording before giving up |
| `RECORDING_WATCH` / `RECORDING_POLL` | `auto` / `1000` | `auto`, `events` or `poll` for watching that folder, and the poll interval in ms |
| `SECRETS_DIRS` | unset | Colon-separated directories of secrets. Unset means none to bind |
| `MCP_SKILL` | `true` | `false` serves only the tools, no skill |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |

## Session settings, and where they come from

The browser, the window size and the two timeouts resolve in order of
increasing specificity:

```
server default (config: session.*)   <   client default (URL param / header)   <   open_session argument
```

| Setting | Env | Parameter | Header |
|---|---|---|---|
| Browser | `SESSION_BROWSER` | `?browser=` | `Sf-Browser` |
| Browser version | `SESSION_VERSION` | `?version=` | `Sf-Version` |
| Window width | `SESSION_WIDTH` | `?width=` | `Sf-Window-Width` |
| Window height | `SESSION_HEIGHT` | `?height=` | `Sf-Window-Height` |
| Page load timeout (s) | `SESSION_PAGE_LOAD_TIMEOUT` | `?page_load_timeout=` | `Sf-Page-Load-Timeout` |
| Script timeout (s) | `SESSION_SCRIPT_TIMEOUT` | `?script_timeout=` | `Sf-Script-Timeout` |

The header beats the parameter. The old `X-Browser`, `X-Version`,
`X-Window-Width`, `X-Window-Height`, `X-Page-Load-Timeout` and
`X-Script-Timeout` names are refused with the `Sf-` name to use.

A version belongs to its browser: a header or an argument naming a browser other
than the configured one discards `SESSION_VERSION`, so that browser opens at
latest. `insecure` and `headless` have no default anywhere; only `open_session`
sets them (`skill://selenium-flow/references/WORKSPACES.md`).

No bound is checked on the window or the timeouts, and one left unset is the
browser's own default. `SESSION_PAGE_LOAD_TIMEOUT` is worth setting so a hung
navigation does not hold one of the Grid's few slots. An unusable client value
(a `?page_load_timeout=` parameter or `Sf-Page-Load-Timeout` header) is ignored
with a warning, and `open_session` reports the settings it resolved to. A bad
`session.*` value in the config file, the environment or the command line is the
operator's and stops the boot. The browser is `chrome` unless something says
otherwise, and an explicit `open_session(browser="chrom")` fails rather than
handing back Chrome. The env var is `SESSION_BROWSER`, not `BROWSER`.

`SESSION_CAPABILITIES` is a JSON object of W3C capabilities every new session
sends, such as `{"kubed:pool": "default"}` for a Grid whose node classes each
carry one. It is sent under whatever a caller's own `capabilities` say, and a
key the caller sends replaces it. A key one of the named options controls
(`browserName`, `browserVersion`, ...) stops the boot, naming the option. There
is no per-client switch for it.

## Uploads need one writable directory

`/browser/upload` stages the file on this server before Selenium ships it to the
Grid node. The container image runs read-only as an unprivileged user, so mount
an `emptyDir` at `/tmp` or point `TMPDIR` at a writable path. Without one, an
upload fails with "cannot stage the upload: no writable temporary directory".

## Two lifetimes

- The session (the live browser) is kept alive by this server for
  `session.keep_alive` after its last call, then expired by the Grid on its own
  idle timeout (`SE_NODE_SESSION_TIMEOUT`, set on the Grid node).
- The workspace (what the server remembers about a caller) is expired by
  `WORKSPACE_TTL`, sliding forward on every write.

The server runs no cleanup loop. If the Grid has reaped a browser, the next call
reopens it at the last known URL and its result says so in `reopened`.
