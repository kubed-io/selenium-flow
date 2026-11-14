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
| `DATA_DIR` | unset | give workspaces their flows and files, under `DATA_DIR/workspaces/<name>/`; unset means none |
| `RECORDING_ENABLED` | `false` | allow `open_session(record=true)`, once the Grid's recordings reach `RECORDING_DIR` |
| `RECORDING_DIR` | `$DATA_DIR/recordings` | where the Grid's recordings arrive. The recorder must keep `SE_VIDEO_FILE_NAME=auto` and `SE_VIDEO_FILE_NAME_SUFFIX=true` (or `SE_VIDEO_SESSION_SUBFOLDER=true`) so the Grid session id is in the path |
| `RECORDING_WAIT` | `600` | seconds to wait for a recording after its browser ends |
| `RECORDING_WATCH` / `RECORDING_POLL` | `auto` / `1000` | `events` or `poll` to watch that folder, and the poll interval in ms |
| `SECRETS_DIRS` | unset | colon-separated directories of secrets; unset means none |
| `MCP_SKILL` | `true` | `false` to serve the tools without this skill |
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
WORKSPACES.md has the naming rules. The other per-client switch says whether the
client's model can read MCP resources: `X-MCP-Resources: off` or `on`, or
`?resources=off` or `on`, and the header wins. A client that is off is given the
tools `list_resources` and `read_resource` instead. With neither sent, Visual
Studio Code counts as off and every other client as on.

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
