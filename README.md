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
                                  holds no browser          the browser
                                                            lives here
```

**This server holds no browser.** The browser lives on the Grid, and with `SESSION_STORE=redis` the record naming it does too — so the server can restart or scale to zero without anyone losing a tab. With the default in-memory store, a restart forgets which browser was whose. 🪄

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

Seventeen actions, each a tool **and** an endpoint with identical parameters — a request body *is* the tool's schema, with nothing added and nothing taken away.

> **Name your session** on either surface: `?session=<name>` or an `X-Session-Key` header. There is no session id anywhere. See [Sessions](#-sessions).

| Action | Endpoint | |
|---|---|---|
| [`open_session`](https://github.com/kubed-io/selenium-flow/wiki/open_session) | `POST /browser` | Start a browser — `chrome` or `firefox` 🚀 |
| [`navigate`](https://github.com/kubed-io/selenium-flow/wiki/navigate) | `POST /browser/navigate` | Go to a URL 🧭 |
| [`interact`](https://github.com/kubed-io/selenium-flow/wiki/interact) | `POST /browser/interact/{action}` | Click, double-click, right-click, hover, scroll to 🖱️ |
| [`drag`](https://github.com/kubed-io/selenium-flow/wiki/drag) | `POST /browser/drag` | Drag an element onto another, or by an offset 🤏 |
| [`write`](https://github.com/kubed-io/selenium-flow/wiki/write) | `POST /browser/write` | Type into a field ⌨️ |
| [`press_key`](https://github.com/kubed-io/selenium-flow/wiki/press_key) | `POST /browser/press-key` | Press a named key — `tab`, `enter`, arrows 🎹 |
| [`extract`](https://github.com/kubed-io/selenium-flow/wiki/extract) | `POST /browser/extract` | Read text and HTML off the page 📖 |
| [`outline`](https://github.com/kubed-io/selenium-flow/wiki/outline) | `POST /browser/outline` | What is on the page: a checked selector each, and what works 🗺️ |
| [`assert`](https://github.com/kubed-io/selenium-flow/wiki/assert) | `POST /browser/assert` | JavaScript that must come back true, or the call fails ✅ |
| [`screenshot`](https://github.com/kubed-io/selenium-flow/wiki/screenshot) | `POST /browser/screenshot` | Capture a PNG, viewport or full page 📸 |
| [`print`](https://github.com/kubed-io/selenium-flow/wiki/print) | `POST /browser/print` | Keep the page as a PDF or as HTML 📄 |
| [`execute_script`](https://github.com/kubed-io/selenium-flow/wiki/execute_script) | `POST /browser/script` | Run JavaScript — the escape hatch 🧪 |
| [`frame`](https://github.com/kubed-io/selenium-flow/wiki/frame) | `POST /browser/frame` | Enter and leave an iframe 🖼️ |
| [`dialog`](https://github.com/kubed-io/selenium-flow/wiki/dialog) | `POST /browser/dialog` | Answer a native alert, confirm or prompt 💬 |
| [`resize`](https://github.com/kubed-io/selenium-flow/wiki/resize) | `POST /browser/resize` | Change the window at any time 📐 |
| [`upload_file`](https://github.com/kubed-io/selenium-flow/wiki/upload_file) | `POST /browser/upload` | Attach a file to a file input 📎 |
| [`end_browser`](https://github.com/kubed-io/selenium-flow/wiki/end_browser) | `DELETE /browser` | Give the slot back, keep the session 🧹 |

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

**Every session is named by whoever calls, on both surfaces.** Say who you are and you get the browser that belongs to that name — there is no session id in the contract at all, so there is nothing to keep and nothing to pass.

| How to name it | Looks like | Use it when |
|---|---|---|
| **A URL parameter** | `…/mcp?session=research-bot` | the usual case: one credential, each caller named in its own URL |
| **A header** | `X-Session-Key: research-bot` | an operator pins one session to one credential |
| **The same header, renamed** | `X-Workspace: research-bot` | a client that only sends approved headers, such as Claude.ai custom connectors |
| **stdio** | nothing to do | one process serves one client, and it is named `stdio` |

**Sending both is a 400**, not a contest one wins: two names is two ideas about who is calling, and quietly picking one hides that from whoever wired it up. Naming nothing is a 400 too, with a message saying how — except on the flow library, which falls back to the shared `global` one that everyone reads and nobody writes.

**`open_session` always comes first.** Nothing opens a browser implicitly, because that is the only place its browser, window size and timeouts can be chosen.

Call again with the same name — after a reconnect, a client restart, a week later — and you are back on the same browser at the same page.

> ⚠️ n8n opens a **new MCP transport per tool call**, so nothing the transport negotiates is ever the same twice. Name the session in the URL and it simply works.

> 🔑 A session name is a credential. The bearer token is the only thing guarding it, so anyone who can call this server can name your session and drive your browser.

Browser lifetime is the Grid's (`SE_NODE_SESSION_TIMEOUT`, 300s here); how long a session is remembered is `SESSION_TTL`, slid forward on every call. Nothing runs a cleanup loop.

### 📍 Where am I?

`session://current` reports the session name, which browser it is running, the page it is on, whether one is open at all, whether you are inside a frame, and the window size. Reading it never opens a browser.

Everything there is to read is a resource like this one, and a client whose model cannot read resources — VS Code Copilot, or anything declaring `?resources=off` — gets two tools instead: `list_resources` and `read_resource(uri)`.

### 📖 It teaches you how to use it

The server ships an **Agent Skill**: the strategic half tool descriptions cannot
hold — how to name a session, why `extract` beats `screenshot` by orders
of magnitude, how to reach a page in one call, and what a timeout usually means.

`SKILL.md` is a thin index; each reference is its own resource, so an agent loads
only what its task needs:

| Resource | Holds |
|---|---|
| `skill://selenium-flow/SKILL.md` | the index: naming your session, the three rules, where next |
| `.../references/SESSIONS.md` | naming a session, sharing one, recovering a dead browser |
| `.../references/READING_PAGES.md` | extract vs script vs screenshot, and durable XPath |
| `.../references/INTERACTION.md` | forms, clicks, keys, scrolling, waiting |
| `.../references/TROUBLESHOOTING.md` | timeouts, dead sessions, blank captures |
| `.../references/CONFIGURATION.md` | setting the server up, which env var to change |
| `skill://selenium-flow/_manifest` | the file listing, with sizes and hashes |

The two session references are mutually exclusive: `session://current` tells you
which one applies.

Those URIs are FastMCP's convention, served by its own `SkillProvider`, so
`list_skills` and `download_skill` work here with no special casing.

A client that implements the MCP Skills extension also finds it through
`skills/list` and `skills/get`, and can verify every file it reads.

A client that cannot read resources reads the same URIs with `read_resource`.
`MCP_SKILL=false` turns the skill off.

---

## 🔁 Flows — do it once, run it forever

Drive a form once, save the steps under a name, and every run after that is one call:

```
run_flow(name="sign-up", params={"email": "a@example.com"})
```

A step is just a tool call, validated against the live tool schemas when it is saved — so a flow that could not run is refused before it starts. It runs in whatever browser you already hold, which means the same flow checks Chrome and then Firefox without an edit. Each named session keeps its own library, beside a shared one called `global` that every session can run and none can change. Set `DATA_DIR` to turn them on; sessions live under `DATA_DIR/sessions/<name>/`.

## 🔐 Secrets — typed, never shown

Mount credentials as a directory per secret and a file per key — exactly how Kubernetes already mounts a `Secret` — and point `SECRETS_DIRS` at it. An agent sees the names and keys, never a value, and binds one where the value would go:

```
write(selector={"css": "#password"}, secret={"name": "nextcloud", "key": "password"})
```

The server types it; it never passes through the model, the transcript or a log. A secret can be pinned to the sites it may be used on, and is refused anywhere else.

## 🗂 Files, Screenshots and Downloads

Four sections, addressed by path, that never merge into one list:

| Section | Holds | Cleared |
|---|---|---|
| **Downloads** | whatever the **site** downloaded — the Grid's own store | dies with the browser |
| **Screenshots** | every `screenshot`, from the moment it is taken | kept, or cleared in bulk |
| **Recordings** | the video of a browser opened with `record=true` | kept, or cleared in bulk |
| **Files** | anything `keep_file`'d, and every `print` | one at a time, by an operator |

`keep_file(uri)` **moves** a screenshot or recording into Files, or **copies** a download
there before the browser ends it. `upload_file(file=uri)` attaches any of the
others to a file input. No agent tool clears or deletes anything — that is an
operator action in the [Admin UI](#-admin-ui) below.

| Read it as | URI |
|---|---|
| a resource | `session://files` — Files, plus the two folders below |
| a resource | `session://files/{name}` — one kept file, as bytes |
| a resource | `session://files/screenshots`, `.../screenshots/{name}` |
| a resource | `session://files/downloads`, `.../downloads/{name}` |
| JSON over HTTP | `GET /files`, `GET /files/screenshots`, `GET /files/downloads` |
| keep one, over HTTP | `PUT /files/screenshots/{name}/kept`, `PUT /files/downloads/{name}/kept` |

Every entry carries a link to hand someone — signed and time-limited when the
server has a token, a plain path when authentication is off — because an
`<img>` tag cannot send an `Authorization` header. `screenshot(save=false)`
opts out when a capture is not worth keeping.

---

## Recording

`open_session(record=true)` films the browser's whole life — from that call until
the browser ends or the Grid reaps it — and the video appears under
`session://files/recordings` (and in the admin page's Recordings row) shortly
after. Like screenshots, recordings stay until they are kept into Files or
cleared.

**Selenium records; you deliver; selenium-flow files.** Every docker-selenium
node image from `4.45.0-20260606` contains the recorder and starts it for a
session that asks with `se:recordVideo`. The Grid has no endpoint to download a
recording, so how the file reaches this server is yours to choose. The one rule:

> Make the Grid's recordings arrive in `RECORDING_DIR` (default
> `$DATA_DIR/recordings`), and set `RECORDING_ENABLED=true`.

Both sides need write access to that directory: the Grid's side writes, this
server moves the finished file out. Keep `SE_NODE_MAX_SESSIONS=1` on recording
nodes, or recordings share one screen.

- **A shared volume.** Mount the same directory at the nodes' `/videos` and at
  `RECORDING_DIR` here. Nothing else to configure.
- **rclone, to anything.** The recorder uploads each finished file with rclone:
  set `SE_UPLOAD_DESTINATION_PREFIX` and an `RCLONE_CONFIG_<REMOTE>_*` remote on
  the nodes — a WebDAV such as Nextcloud whose folder is the same storage as
  `RECORDING_DIR`, S3, SFTP. Leaving `--inplace` out of `SE_UPLOAD_OPTS` makes
  rclone write `*.partial` and rename, which this server waits for.
- **A local folder** for stdio or compose: bind-mount it into the Grid container
  at `/videos` (see `docker-compose.yaml`). A standalone container may also need
  `SE_VIDEO_RECORD_STANDALONE=true` to start its recorder (set in
  `docker-compose.yaml`; not yet verified).

A recording is matched to its session by the Grid's session id in its file name,
so any path and prefix the transport adds is fine. One that never arrives is
given up on after `RECORDING_WAIT` seconds with a warning in the log.

---

## 🖥 Admin UI

`GET /` — **your** sessions, marked with the browser each is running. Open one for its Files tab — Downloads, Screenshots, Recordings and Files, each cleared the way that fits it — and its Flows tab. Click a file to view it in place; **End** quits a stale browser and gives its Grid slot back, rather than waiting out the Grid's idle timeout — the session itself is kept.

Flow sessions, not Grid sessions: browsers somebody else put on the Grid are not listed. Nothing on the MCP surface lists sessions at all — a client sees its own and nothing else. [More in the wiki](https://github.com/kubed-io/selenium-flow/wiki/Administration).

The list **pushes its own updates** over Server-Sent Events — no refresh button, and no polling per tab: one loop serves every page. Rows carry the name their caller claimed; a browser this server has no record of is labelled as somebody else's rather than passed off as ours.

There are no accounts: the sign-in box asks for the server's token, since anyone holding it can already drive every browser through the API. It is kept in `sessionStorage`, so it does not outlive the tab.

The Grid's own console is a second tab, framed same-origin. Put both behind one host — the Grid at `/`, this server under a path — and its live view works with no cross-origin exception.

---

## 🧩 MCP Apps

Hosts implementing the [MCP Apps extension](https://modelcontextprotocol.io/extensions/apps/overview) — Claude, ChatGPT, VS Code, Goose — render a tool result as UI rather than JSON. `show(uri)` draws your session, its files, a folder of screenshots, the saved flows as cards, or one flow.

The views are the app's own; the files grid, lightbox and browser marks are shared with the admin UI, not copied. Degradation is the point: one server, the client's capabilities pick the rendering.

| The client can | It gets |
|---|---|
| render apps | `show`, and the view inline |
| read resources | `session://files`, `flow://flows`, and the file as bytes |
| neither | `read_resource` / `list_resources`, the same JSON, and links anything can open |

Apps get a deny-by-default CSP with no network, so `PUBLIC_BASE_URL` is also what admits this server's images to the frame. `MCP_APPS=false` turns it off.

---

## ⚙️ Configuration

Every setting can be set in a YAML config file, in the environment, or on the command line, and a later one wins: **default < config < env < args**. A setting's file path is its name: `redis.host` is `REDIS_HOST` and `--redis-host`.

```yaml
# /etc/selenium-flow/config.yaml
grid:
  url: http://selenium-hub:4444
redis:
  host: redis.data
secrets:
  entries:
    nextcloud:
      allowed_urls: [https://nextcloud.example.com]
      keys:
        password: {env: NEXTCLOUD_PASSWORD}
```

Point at the file with `--config-file` or `CONFIG_FILE`. Prefer `file:` or `env:` for a secret's keys — `value:` writes the value into the config file itself.

Every setting, in all three spellings: the wiki's [Configuration](https://github.com/kubed-io/selenium-flow/wiki/Configuration) page.

### Session defaults cascade

The browser, the window size and the two timeouts resolve in order of increasing specificity:

```
server default (config: session.*)  <  client default (?width= / X-Window-Width)  <  open_session argument
```

A bad *client* default is ignored and logged; a bad `session.*` in the config, env or args stops the boot, and an explicit `browser` argument fails loudly. The browser is stored with the session, so a reaped one reopens as the same browser.

### 🔐 Auth

Setting `AUTH_TOKEN` turns on auth for both surfaces at once. Clients send it the usual way:

```
Authorization: Bearer <token>
```

The HTTP endpoints also accept the bare token as the `Authorization` value, for clients that can't express a scheme. `/health` is always open.

It is also the signing key for file links and the event stream, so rotating it revokes those too. In the cluster it is generated by External Secrets — no value is authored anywhere.

Behind an OIDC gateway, `/mcp` also takes your identity provider's JWT. Set these
beside `AUTH_TOKEN`, and the server checks the signature, audience, expiry and role itself:

    OIDC_ISSUER=https://auth.example.com/realms/example
    OIDC_AUDIENCE=https://mcp.example.com
    OIDC_JWKS_URI=https://auth.example.com/realms/example/protocol/openid-connect/certs
    OIDC_ROLES=mcp

The token keeps working beside it, and stays the admin's.

---

## 🚀 Running it

```bash
docker compose up --build
```

That starts the server **and** a standalone Grid for it to drive, with auth off:

```bash
curl -X POST localhost:8000/browser \
  -H 'X-Session-Key: demo' -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","width":1280,"height":800}'
```

Point an MCP client at `http://localhost:8000/mcp`, open `localhost:8000` — and watch the browser work live at **`localhost:7900`**, the Grid's noVNC view. 👀

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
