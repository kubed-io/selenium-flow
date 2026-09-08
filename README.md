# Selenium MCP

An MCP server that drives a real browser on [Selenium Grid](https://www.selenium.dev/documentation/grid/),
and serves the same actions as plain HTTP endpoints.

The browser is **persistent**. A session stays alive between calls and keeps its page,
cookies and scroll position, so an agent can work through a multi-step task instead of
starting a fresh browser for every action.

## Two surfaces, one implementation

| Surface | For | Path |
|---|---|---|
| MCP over Streamable HTTP | agents and MCP clients | `/mcp` |
| JSON over HTTP | anything else — n8n HTTP nodes, curl, scripts | `/browser/*` |

Both call the same functions in `actions.py`, so they cannot drift — every action is a
tool *and* an endpoint, one to one, and a test enforces it. That is deliberate: a caller
picks the style that suits the job. Hand a whole task to an agent over MCP, or drive the
same actions directly over HTTP when you want exact control, and switch between them
without losing any capability.

The surfaces differ only in return shape where it matters: `screenshot` gives MCP an image
block a vision model can see, and HTTP a base64 payload a script can save.

## Actions

| Tool | Endpoint | Does |
|---|---|---|
| `open_session` | `POST /browser/open` | Start a session; returns the `session_id` everything else needs |
| `navigate` | `POST /browser/navigate` | Go to a URL |
| `click` | `POST /browser/click` | Click the element at an XPath |
| `write` | `POST /browser/write` | Type into a field, optionally pressing Enter |
| `press_key` | `POST /browser/press-key` | Press a named key — Tab, Escape, arrows |
| `extract` | `POST /browser/extract` | Read an element's text and HTML |
| `execute_script` | `POST /browser/script` | Run JavaScript and return its result |
| `screenshot` | `POST /browser/screenshot` | Capture the viewport, one element, or the full page |
| `close_session` | `POST /browser/close` | Quit the session and free its Grid slot |

`GET /health` reports Grid readiness and the live session count, and `GET /openapi.yaml`
(or `.json`) describes the HTTP surface. Neither needs credentials — a kubelet has none,
and a contract you must authenticate to read is needlessly awkward.

### OpenAPI

The spec is **generated, not written**. Request schemas come from the MCP tools themselves
— the same objects FastMCP publishes to agents — so the two contracts are the same schema
rather than two descriptions that happen to agree. It is also committed at
[`openapi.yaml`](openapi.yaml) so it can be reviewed in a pull request and linted; a test
fails if that file drifts from what the code produces.

```bash
python scripts/generate_openapi.py   # the fix when that test fails
```

Response shapes are the one hand-maintained half, in `openapi.py` — the actions return
plain dicts, so there is nothing to introspect. A test asserts every endpoint has one.

### Sessions

`open_session` returns a `session_id`; every other call takes it. The browser lives on the
Grid, not in this process, which is why the server can restart, scale to zero, or run
behind several replicas without losing one.

Over MCP, `session_id` is optional: the server remembers a browser per caller. It works out
who is calling from the first of these it finds, and never invents one — a caller it cannot
identify is told to pass `session_id` rather than being handed a fresh browser.

| Key | How | Good for |
|---|---|---|
| A name you choose | `?session=<name>` on the MCP URL, or an `X-Session-Key` header (which wins if both are set) | Any client at all — the only option that does not depend on the client holding an MCP session |
| The MCP transport session | the `Mcp-Session-Id` the server negotiates | Clients that hold a session, which is most of them |
| stdio | one process serves one client | Local use |

The query parameter is usually the one you want: a single bearer credential is reused
across callers and each names itself in its URL, so n8n needs one credential rather than a
multi-header one per agent. Setting the header instead pins a session to a credential, so
an admin can enforce one browser per credential and a caller cannot override it from the
URL; leaving it out delegates the choice to whoever implements the call.

If the Grid has reaped a remembered browser, the next call reopens one and navigates back
to the page it was last on, so the refresh is invisible.

**The HTTP endpoints never do any of this.** They take a `session_id` in and give one back,
always, so an n8n workflow owns its session outright and can pass it between nodes.

Always `close_session`, including on failure paths. Sessions are limited and an abandoned
one holds a slot until the Grid times it out — which the Grid does on its own, so nothing
here runs a cleanup loop.

### Navigation

`click`, `write`, `press_key`, `extract`, `screenshot` and `execute_script` all take an
optional `url`. It is **not an assertion** — if the browser is somewhere else it navigates
there first, so a caller can jump straight to a page instead of clicking a path to it.
URLs compare with the fragment and any trailing slash ignored; query strings count.

### Screenshots

Three modes: pass `xpath` for one element, `full_page` for the whole scrollable page, or
neither for the viewport. Over MCP the result is an image content block a vision model can
actually see; over HTTP it is base64 plus real pixel dimensions and byte size.

Everything uses plain W3C WebDriver, so it works on any browser the Grid runs. Chrome has
no W3C full-page command, so `full_page` grows the window to the document height.

## Configuration

Every flag has an environment fallback.

| Env | Flag | Default | Notes |
|---|---|---|---|
| `GRID_URL` | `--grid-url` | the in-cluster Grid Service | Selenium Grid hub |
| `MCP_AUTH_TOKEN` | `--auth-token` | unset | Bearer token required on `/mcp` and `/browser/*`. Unset disables auth |
| `ROUTE_PREFIX` | `--route-prefix` | `/browser` | Path prefix for the HTTP endpoints |
| `SAVED_SESSIONS` | `--no-saved-sessions` | `true` | Let MCP callers omit `session_id`. Never affects the HTTP endpoints |
| `SESSION_STORE` | — | `memory` | `memory` or `redis`. Unset, any `REDIS_*` setting implies `redis` |
| `SESSION_TTL` | — | `3600` | How long a caller's mapping is kept, in seconds. Honoured by both stores |
| `REDIS_URL` | — | unset | Connection for `SESSION_STORE=redis` |
| `REDIS_DB` | — | `0` | Database index. Applied even when `REDIS_URL` carries no `/<index>` |
| `REDIS_HOST` / `REDIS_PORT` | — | `localhost` / `6379` | Alternative to `REDIS_URL` |
| `REDIS_USERNAME` / `REDIS_PASSWORD` / `REDIS_SSL` | — | unset | Credentials for the above |
| `REDIS_PREFIX` | — | `selenium-flow:session:` | Key namespace, so sharing a database is safe |
| `STATELESS_HTTP` | `--stateless` | `false` | Drop MCP transport sessions. Required to run more than one replica |
| `TRANSPORT` | `--transport` | `http` | `http` or `stdio` |
| `HOST` / `PORT` | `--host` / `--port` | `0.0.0.0` / `8000` | |
| `LOG_LEVEL` | `--log-level` | `INFO` | |

### Auth

Setting `MCP_AUTH_TOKEN` turns on auth for both surfaces at once. Clients send it the
normal way:

```
Authorization: Bearer <token>
```

The HTTP endpoints also accept the bare token as the `Authorization` value, for clients
that cannot express a scheme. `/health` is always open.

In the cluster the token is generated by External Secrets — no value is authored anywhere.
See `apps/selenium/components/mcp` in the cluster repo.

## Running it

```bash
docker compose up --build
```

That starts the server *and* a standalone Grid for it to drive, with auth off:

```bash
curl localhost:8000/health
curl -X POST localhost:8000/browser/open -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","width":1280,"height":800}'
```

Point an MCP client at `http://localhost:8000/mcp`. The Grid's noVNC view is on
`localhost:7900` if you want to watch the browser work.

## Development

```bash
pip install -e ".[test]"
ruff check kubed
pytest
```

The tests wire a server against an unroutable Grid address and drive both surfaces through
the real ASGI app, so they need no browser and no network.

## References

- [Model Context Protocol](https://modelcontextprotocol.io/)
- [FastMCP](https://gofastmcp.com/)
- [Selenium Grid](https://www.selenium.dev/documentation/grid/)
- [Selenium Python API](https://selenium-python.readthedocs.io/)
- [RemoteWebDriver](https://www.selenium.dev/documentation/webdriver/drivers/remote_webdriver/)
- [Docker Hub — kubed/selenium-flow](https://hub.docker.com/r/kubed/selenium-flow)
