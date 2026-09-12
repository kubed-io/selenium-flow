# 🌊 Selenium Flow

**One browser, many calls.** Drive a real Chrome or Firefox on [Selenium Grid](https://www.selenium.dev/documentation/grid/) from an agent over MCP — or from anything else over plain HTTP. Same actions, same server, one browser that stays exactly where you left it. 🧭

[![🧪 Test](https://github.com/kubed-io/selenium-flow/actions/workflows/test.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/test.yml)
[![🛡️ Quality](https://github.com/kubed-io/selenium-flow/actions/workflows/quality.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/quality.yml)
[![📸 Image Builder](https://github.com/kubed-io/selenium-flow/actions/workflows/image.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/image.yml)
[![📖 Wiki](https://github.com/kubed-io/selenium-flow/actions/workflows/wiki.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/wiki)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-kubed%2Fselenium--flow-2496ed?logo=docker&logoColor=white)](https://hub.docker.com/r/kubed/selenium-flow)
[![FastMCP](https://img.shields.io/badge/FastMCP-4-8a2be2)](https://gofastmcp.com/)

---

## The whole idea, in one breath

Open a browser once. It stays alive — same page, same cookies, same scroll position — while an agent works a task one call at a time, or an n8n workflow steps node to node. Nothing relaunches, nothing logs in twice.

```
   agent  ──── MCP  /mcp ─────▶  ┌───────────────┐        ┌───────────────┐
                                 │ selenium-flow │ ─────▶ │ Selenium Grid │ ──▶ 🌐
workflow  ──── HTTP /browser ──▶ └───────────────┘        └───────────────┘
                                    stateless               the browser
                                                            lives here
```

**This server holds no browser.** The session lives on the Grid, so the server can restart, scale to zero, or run several replicas without anyone losing a tab. 🪄

---

## 🔀 Two surfaces, one implementation

| Surface | For | Path |
|---|---|---|
| **MCP** over Streamable HTTP | agents and MCP clients | `/mcp` |
| **JSON** over HTTP | n8n HTTP nodes, curl, scripts, anything | `/browser/*` |

Every action is a tool **and** an endpoint, one to one, and a test fails the build if that stops being true. They differ in one place: `screenshot` hands MCP an image block a vision model can *see*, and HTTP a base64 payload.

`GET /health` and `GET /openapi.yaml` need no credentials.

---

## 🧰 Every action, both ways

Fourteen actions, each a tool **and** an endpoint with identical parameters. All endpoints are `POST` with a JSON body.

> **The one difference:** over HTTP `session_id` is always **required**; over MCP it depends on the mode, and the advertised schema says which. See [Sessions](#-sessions).

| Action | Endpoint | |
|---|---|---|
| [`open_session`](https://github.com/kubed-io/selenium-flow/wiki/open_session) | `POST /browser/open` | Start a browser — `chrome` or `firefox` 🚀 |
| [`navigate`](https://github.com/kubed-io/selenium-flow/wiki/navigate) | `POST /browser/navigate` | Go to a URL 🧭 |
| [`interact`](https://github.com/kubed-io/selenium-flow/wiki/interact) | `POST /browser/interact` | Click, double-click, right-click, hover, scroll to 🖱️ |
| [`write`](https://github.com/kubed-io/selenium-flow/wiki/write) | `POST /browser/write` | Type into a field ⌨️ |
| [`press_key`](https://github.com/kubed-io/selenium-flow/wiki/press_key) | `POST /browser/press-key` | Press a named key — `tab`, `enter`, arrows 🎹 |
| [`extract`](https://github.com/kubed-io/selenium-flow/wiki/extract) | `POST /browser/extract` | Read text and HTML off the page 📖 |
| [`screenshot`](https://github.com/kubed-io/selenium-flow/wiki/screenshot) | `POST /browser/screenshot` | Capture a PNG, viewport or full page 📸 |
| [`save_pdf`](https://github.com/kubed-io/selenium-flow/wiki/save_pdf) | `POST /browser/pdf` | Print the page with the browser's print engine 📄 |
| [`execute_script`](https://github.com/kubed-io/selenium-flow/wiki/execute_script) | `POST /browser/script` | Run JavaScript — the escape hatch 🧪 |
| [`frame`](https://github.com/kubed-io/selenium-flow/wiki/frame) | `POST /browser/frame` | Enter and leave an iframe 🖼️ |
| [`dialog`](https://github.com/kubed-io/selenium-flow/wiki/dialog) | `POST /browser/dialog` | Answer a native alert, confirm or prompt 💬 |
| [`resize`](https://github.com/kubed-io/selenium-flow/wiki/resize) | `POST /browser/resize` | Change the window at any time 📐 |
| [`upload_file`](https://github.com/kubed-io/selenium-flow/wiki/upload_file) | `POST /browser/upload` | Attach a file to a file input 📎 |
| [`end_browser`](https://github.com/kubed-io/selenium-flow/wiki/end_browser) | `POST /browser/end` | Give the slot back, keep the session 🧹 |

Every parameter, every return field and the traps worth knowing are one page per action in the **[wiki](https://github.com/kubed-io/selenium-flow/wiki/Actions)** — generated from `openapi.yaml`, which is itself generated from the live tool schemas, so it cannot drift from the server. The same schemas are served at `GET /openapi.yaml`.

### 🦊 Chrome or Firefox

`open_session(browser="firefox")` and you are on Firefox; leave it out and you are on Chrome. Every other action behaves identically on both — both are plain W3C WebDriver, so only session creation differs.

### 🧠 A session is not a browser

The session outlives the browsers it holds. When the Grid reaps an idle one, or an operator ends one, the session keeps the browser choice, the window and the page it was on — so recovery is one call with no arguments:

```
open_session()      # same browser, same window, back where you were
```

Sessions expire on `SESSION_TTL`, slid forward on every use. Nothing else removes one. [More in the wiki](https://github.com/kubed-io/selenium-flow/wiki/Sessions).

### 🧭 About that `url` parameter

`click`, `write`, `press_key`, `extract`, `screenshot` and `execute_script` all take an optional `url`, and it is **not an assertion**. If the browser is elsewhere it goes there first, so you can jump straight to a page instead of clicking a path to it.

---

## 🍪 Sessions

Over HTTP it is session in, session out, always — so an n8n workflow owns its session and can pass it between nodes.

**`open_session` always comes first.** Nothing opens a browser implicitly, because that is the only place its browser, window size and timeouts can be chosen.

After that there are two modes, and they are **exclusive**. `session://current` reports which one applies and links the reference that explains it:

| Mode | When | The rule |
|---|---|---|
| **saved** | the server can identify you | **never** pass `session_id` — it is not even advertised |
| **stateless** | it cannot, or you are on `/browser/*` | `session_id` is **required** on every call |

It works out who is calling from the first of these it finds and **never invents one** — a caller it cannot identify is stateless, not quietly handed a browser:

| Key | How it's set | How stable |
|---|---|---|
| **A name you choose** | `X-Session-Key` header, else `?session=<name>` on the MCP URL | Survives a client restart or reconnect |
| **The MCP transport session** | the negotiated `Mcp-Session-Id` | Lasts the connection; a reconnect is a new key |
| **stdio** | one process, one client | Lasts the process |

Prefer the **query parameter**: one credential shared across callers, each naming itself in its URL. The **header** wins over it, so an admin can pin one browser per credential.

> ⚠️ n8n opens a **new MCP transport per tool call**, so the negotiated id is never the same twice. Name the session in the URL or saved mode cannot work there.

Browser lifetime is the Grid's (`SE_NODE_SESSION_TIMEOUT`, 300s here); how long we remember a caller is `SESSION_TTL`, slid forward on every call. Nothing runs a cleanup loop.

### 📍 Where am I?

`session://current` reports the mode, the session this client holds and which browser it is running, whether the Grid still has it, whether you are inside a frame, and a link to the reference that applies. Reading it never opens a browser.

The same status is also a `current_session` **tool**, hidden unless a client declares `?resources=off` or `X-MCP-Resources: off` — resources being the least implemented corner of MCP.

### 📖 It teaches you how to use it

The server ships an **Agent Skill**: the strategic half tool descriptions cannot
hold — which session mode you are in, why `extract` beats `screenshot` by orders
of magnitude, how to reach a page in one call, and what a timeout usually means.

`SKILL.md` is a thin index; each reference is its own resource, so an agent loads
only what its task needs:

| Resource | Holds |
|---|---|
| `skill://selenium-flow/SKILL.md` | the index: session mode, the three rules, where next |
| `.../references/STATELESS.md` | you own the session id |
| `.../references/SAVED_SESSIONS.md` | the server holds your browser |
| `.../references/READING_PAGES.md` | extract vs script vs screenshot, and durable XPath |
| `.../references/INTERACTION.md` | forms, clicks, keys, scrolling, waiting |
| `.../references/TROUBLESHOOTING.md` | timeouts, dead sessions, blank captures |
| `.../references/CONFIGURATION.md` | setting the server up, which env var to change |
| `skill://selenium-flow/_manifest` | the file listing, with sizes and hashes |

The two session references are mutually exclusive: `session://current` tells you
which one applies.

Those URIs are FastMCP's convention, served by its own `SkillProvider`, so
`list_skills` and `download_skill` work here with no special casing.

Same fallback as the session status: clients that cannot read resources get a
`selenium_flow_skill` tool instead, hidden otherwise. `SKILL_ENABLED=false` turns
both shapes off.

---

## 🔁 Flows — do it once, run it forever

Drive a form once, save the steps under a name, and every run after that is one call:

```
run_flow(name="sign-up", params={"email": "a@example.com"})
```

A step is just a tool call, validated against the live tool schemas when it is saved — so a flow that could not run is refused before it starts. It runs in whatever browser you already hold, which means the same flow checks Chrome and then Firefox without an edit. Each named session keeps its own library, beside a shared one called `global` that every session can run and none can change. Set `FLOW_DATA_DIR` to turn them on.

## 🔐 Secrets — typed, never shown

Mount credentials as a directory per secret and a file per key — exactly how Kubernetes already mounts a `Secret` — and point `SECRETS_DIRS` at it. An agent sees the names and keys, never a value, and binds one where the value would go:

```
write(css="#password", secret={"name": "nextcloud", "key": "password"})
```

The server types it; it never passes through the model, the transcript or a log. A secret can be pinned to the sites it may be used on, and is refused anywhere else.

## 🗂 What a session leaves behind

Everything a session downloads is kept **by the Grid**, in a per-session store beside the browser — created with the session, deleted with it. Two kinds of file land there, undistinguished: whatever the **site** served to a download, and whatever **you** kept with `screenshot(save=true)` or `save_pdf`.

| Read it as | URI / path |
|---|---|
| a resource | `session://files` — the listing |
| a resource | `session://files/{name}` — one file, as bytes |
| a tool | `session_files` — same listing, where there are no resources |
| a link | `GET /files/{session}/{name}?exp=…&sig=…` |

That last one travels: signed over path and expiry, because an `<img>` tag cannot send an `Authorization` header.

---

## 🖥 Admin UI

`GET /admin` — **your** sessions and what each downloaded, marked with the browser each is running. Click a file to view it in place; click a session for a header of its context. **End** quits a stale browser and gives its Grid slot back, rather than waiting out the Grid's idle timeout — the session itself is kept.

Flow sessions, not Grid sessions: browsers somebody else put on the Grid are not listed. Nothing on the MCP surface lists sessions at all — a client sees its own and nothing else. [More in the wiki](https://github.com/kubed-io/selenium-flow/wiki/Administration).

The list **pushes its own updates** over Server-Sent Events — no refresh button, and no polling per tab: one loop serves every page. Rows carry the name their caller claimed; a browser this server has no record of is labelled as somebody else's rather than passed off as ours.

There are no accounts: the sign-in box asks for the server's token, since anyone holding it can already drive every browser through the API. It is kept in `sessionStorage`, so it does not outlive the tab.

The Grid's own console is a second tab, framed same-origin. Put both behind one host — the Grid at `/`, this server under a path — and its live view works with no cross-origin exception.

---

## 🧩 MCP Apps

Hosts implementing the [MCP Apps extension](https://modelcontextprotocol.io/extensions/apps/overview) — Claude, ChatGPT, VS Code, Goose — render a tool result as UI rather than JSON. `session_files` and `browser_sessions` each declare one, so a listing arrives as thumbnails.

The components are shared with the admin UI, not copied, so the two cannot drift. Degradation is the point: one server, the client's capabilities pick the rendering.

| The client can | It gets |
|---|---|
| render apps | the component, inline |
| read resources | `session://files`, and the file as bytes |
| neither | the tool's JSON, with links anything can open |

Apps get a deny-by-default CSP with no network, so `PUBLIC_BASE_URL` is also what admits this server's images to the frame. `APPS_ENABLED=false` turns it off.

---

## ⚙️ Configuration

Every flag has an environment fallback: containers are configured with env vars, developers reach for flags.

| Env | Flag | Default | Notes |
|---|---|---|---|
| `GRID_URL` | `--grid-url` | the in-cluster Grid Service | Selenium Grid hub |
| `MCP_AUTH_TOKEN` | `--auth-token` | unset | Bearer token for both surfaces. Unset disables auth |
| `ROUTE_PREFIX` | `--route-prefix` | `/browser` | Path prefix for the HTTP endpoints |
| `SAVED_SESSIONS` | `--no-saved-sessions` | `true` | Let MCP callers omit `session_id`. Never affects HTTP |
| `SESSION_STORE` | — | `memory` | `memory` or `redis`. Any `REDIS_*` setting implies `redis` |
| `SESSION_TTL` | — | `3600` | Seconds a caller's mapping is kept |
| `REDIS_URL`, or `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_USERNAME` / `REDIS_PASSWORD` / `REDIS_SSL` | — | unset | Connection for `SESSION_STORE=redis`. `REDIS_DB` applies even with no `/<index>` in the URL |
| `REDIS_PREFIX` | — | `selenium-flow:session:` | Key namespace, so sharing a database is safe |
| `FLOW_DATA_DIR` | `--flow-data-dir` | unset | Where saved flows live, one folder per session name. Unset turns flows off |
| `SECRETS_DIRS` | `--secrets-dirs` | unset | Colon-separated directories of secrets, first match wins. Unset turns secrets off |
| `SKILL_ENABLED` | `--no-skill` | `true` | Serve the embedded skill as a resource, and as a tool where there are none |
| `APPS_ENABLED` | `--no-apps` | `true` | Offer the MCP Apps components to hosts that render them |
| `PUBLIC_BASE_URL` | — | unset | Externally reachable root, e.g. `https://selenium.example.com/flow`. Needed for file links and the app CSP |
| `GRID_CONSOLE_URL` | — | `/` | Where the admin UI frames the Grid console from |
| `DEFAULT_BROWSER` | — | `chrome` | `chrome` or `firefox` for new sessions. Not `BROWSER`, which many environments already set |
| `WINDOW_WIDTH` / `WINDOW_HEIGHT` | — | node default | Default window size for new sessions |
| `PAGE_LOAD_TIMEOUT` | — | unbounded | Seconds a navigation may take. **Worth setting** — a hung page holds a Grid slot |
| `SCRIPT_TIMEOUT` | — | driver default | Seconds `execute_script` may take |
| `STATELESS_HTTP` | `--stateless` | `false` | Drop MCP transport sessions. Required for >1 replica |
| `TRANSPORT` | `--transport` | `http` | `http` or `stdio` |
| `HOST` / `PORT` | `--host` / `--port` | `0.0.0.0` / `8000` | |
| `LOG_LEVEL` | `--log-level` | `INFO` | `DEBUG` logs which key each call resolved to, and how |

### Session defaults cascade

The browser, the window size and the two timeouts resolve in order of increasing specificity:

```
server default (env)  <  client default (?width= / X-Window-Width)  <  open_session argument
```

A bad default is ignored and logged; an explicit `browser` fails loudly. The browser is stored with the session, so a reaped one reopens as the same browser.

### 🔐 Auth

Setting `MCP_AUTH_TOKEN` turns on auth for both surfaces at once. Clients send it the usual way:

```
Authorization: Bearer <token>
```

The HTTP endpoints also accept the bare token as the `Authorization` value, for clients that can't express a scheme. `/health` is always open.

It is also the signing key for file links and the event stream, so rotating it revokes those too. In the cluster it is generated by External Secrets — no value is authored anywhere.

---

## 🚀 Running it

```bash
docker compose up --build
```

That starts the server **and** a standalone Grid for it to drive, with auth off:

```bash
curl -X POST localhost:8000/browser/open \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","width":1280,"height":800}'
```

Point an MCP client at `http://localhost:8000/mcp`, open `localhost:8000/admin` — and watch the browser work live at **`localhost:7900`**, the Grid's noVNC view. 👀

---

## 🛠 Contributing

Setup, tests, how the OpenAPI spec is generated and how the embedded skill is
packaged: [CONTRIBUTING.md](CONTRIBUTING.md). Design rules worth reading before
changing behaviour: [AGENTS.md](AGENTS.md).

---

## 🔗 References

- [Model Context Protocol](https://modelcontextprotocol.io/) · [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) · [FastMCP](https://gofastmcp.com/)
- [Selenium Grid](https://www.selenium.dev/documentation/grid/) · [Selenium Python API](https://selenium-python.readthedocs.io/)
- [Docker Hub](https://hub.docker.com/r/kubed/selenium-flow)

---

## 📜 Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or sponsored by the Selenium project. "Selenium" is a trademark of the Software Freedom Conservancy, used here only to identify the software this server drives.
