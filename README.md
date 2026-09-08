# 🌊 Selenium Flow

**One browser, many calls.** Drive a real Chrome on [Selenium Grid](https://www.selenium.dev/documentation/grid/) from an agent over MCP — or from anything else over plain HTTP. Same actions, same server, one browser that stays exactly where you left it. 🧭

[![🧪 Test](https://github.com/kubed-io/selenium-flow/actions/workflows/test.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/test.yml)
[![📸 Image Builder](https://github.com/kubed-io/selenium-flow/actions/workflows/image.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/image.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-kubed%2Fselenium--flow-2496ed?logo=docker&logoColor=white)](https://hub.docker.com/r/kubed/selenium-flow)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-3776ab?logo=python&logoColor=white)](pyproject.toml)
[![FastMCP](https://img.shields.io/badge/FastMCP-4-8a2be2)](https://gofastmcp.com/)

---

## The whole idea, in one breath

Open a browser once. It stays alive — same page, same cookies, same scroll position — while an agent works through a task one tool call at a time, or while an n8n workflow steps from node to node. Nothing relaunches, nothing logs in twice.

```
   agent  ──── MCP  /mcp ─────▶  ┌───────────────┐        ┌───────────────┐
                                 │ selenium-flow │ ─────▶ │ Selenium Grid │ ──▶ 🌐
workflow  ──── HTTP /browser ──▶ └───────────────┘        └───────────────┘
                                    stateless               the browser
                                                            lives here
```

**This server holds no browser.** The session lives on the Grid, so the server can restart, scale to zero, or sit behind several replicas without anyone losing a tab. 🪄

---

## 🔀 Two surfaces, one implementation

| Surface | For | Path |
|---|---|---|
| **MCP** over Streamable HTTP | agents and MCP clients | `/mcp` |
| **JSON** over HTTP | n8n HTTP nodes, curl, scripts, anything | `/browser/*` |

Every action is a tool **and** an endpoint, one to one, and a test fails the build if that stops being true. They differ in one place: `screenshot` hands MCP an image block a vision model can *see*, and HTTP a base64 payload a script can save.

`GET /health` reports Grid readiness and the live session count; `GET /openapi.yaml` describes the HTTP surface. Neither needs credentials.

---

## 🧰 Every action, both ways

Nine actions. Each one is a tool and an endpoint; the parameters are identical, with a single exception noted below. All endpoints are `POST` with a JSON body.

> **The one difference:** over HTTP, `session_id` is always **required**. Over MCP it depends on the mode — required when the server cannot identify you, and refused when it can. The advertised schema says which; see [Sessions](#-sessions).

<details>
<summary><b><code>open_session</code></b> &nbsp;·&nbsp; <code>POST /browser/open</code> &nbsp;—&nbsp; start a browser 🚀</summary>

<br>

Starts a session on the Grid and hands back the `session_id` everything else needs.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `url` | string | no | — | Navigate here once open |
| `width` | integer | no | node default | Window width |
| `height` | integer | no | node default | Window height |

Headless window defaults are small and differ between Grid nodes, so set `width`/`height` whenever layout matters.

**Returns** `session_id`, `url`, `title`, `width`, `height`.

```json
{ "url": "https://example.com", "width": 1280, "height": 800 }
```

</details>

<details>
<summary><b><code>navigate</code></b> &nbsp;·&nbsp; <code>POST /browser/navigate</code> &nbsp;—&nbsp; go to a URL 🧭</summary>

<br>

Moves the browser somewhere, unconditionally.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `url` | string | **yes** | — | Where to go |

**Returns** `url`, `title`.

```json
{ "session_id": "…", "url": "https://example.com/login" }
```

</details>

<details>
<summary><b><code>interact</code></b> &nbsp;·&nbsp; <code>POST /browser/interact</code> &nbsp;—&nbsp; click, hover, right-click 🖱️</summary>

<br>

Every mouse gesture, as one action — they take identical arguments and differ
only in what is sent.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `action` | string | **yes** | — | `click`, `double_click`, `right_click`, `hover`, `scroll_to` |
| `xpath` | string | **yes** | — | Element to act on |
| `url` | string | no | — | Page the action happens on |
| `wait_timeout` | integer | no | `30` | |

**Returns** `action`, `url`, `title` — the last two read *after* the gesture, so
a navigation it caused shows up. If it opened a dialog, `url` and `title` are
`null` and `dialog` carries the message.

```json
{ "session_id": "…", "action": "hover", "xpath": "//nav//li[contains(., 'Account')]" }
```

</details>

<details>
<summary><b><code>upload_file</code></b> &nbsp;·&nbsp; <code>POST /browser/upload</code> &nbsp;—&nbsp; attach a file 📎</summary>

<br>

The browser runs on a Grid node in another container, so a path means nothing to
it. Send the file and it is written and shipped there for you.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `xpath` | string | **yes** | — | The `<input type="file">` |
| `text` | string | one of three | — | The file's content as plain text — JSON, CSV, YAML, markdown |
| `content` | string / file | one of three | — | Base64 over JSON and MCP; a **real file part** over multipart |
| `path` | string | one of three | — | A file already on the **server's** filesystem |
| `filename` | string | no | `upload` | What the page sees. **Its extension sets the MIME type** |
| `mime_type` | string | no | — | Picks an extension when `filename` has none |
| `url` / `wait_timeout` | | no | / `30` | |

**Uploading something you generated** — no encoding step:

```json
{ "session_id": "…", "xpath": "//input[@type='file']",
  "text": "{\"generated\": true}", "filename": "data.json" }
```

**Uploading a real file over HTTP** — a normal multipart part, binary included:

```bash
curl -X POST localhost:8000/browser/upload \
  -F session_id=… -F 'xpath=//input[@type="file"]' -F content=@screenshot.png
```

**Returns** `filename` and `bytes` actually attached, plus page state.

</details>

<details>
<summary><b><code>dialog</code></b> &nbsp;·&nbsp; <code>POST /browser/dialog</code> &nbsp;—&nbsp; answer an alert 💬</summary>

<br>

A native `alert`, `confirm` or `prompt` freezes the page — nothing else can even
read the URL until it is answered.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `action` | string | no | `accept` | `accept`, `dismiss`, `read`, `send_text` |
| `text` | string | only for `send_text` | — | Fills a prompt, then accepts |
| `wait_timeout` | integer | no | `10` | |

**Returns** `action`, `message` (read before answering), plus page state.

</details>

<details>
<summary><b><code>frame</code></b> &nbsp;·&nbsp; <code>POST /browser/frame</code> &nbsp;—&nbsp; enter an iframe 🖼️</summary>

<br>

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(stateless)* | — | |
| `action` | string | no | `switch` | `switch`, `parent`, `default` |
| `xpath` | string | for `switch` | — | The `<iframe>` to enter |
| `index` | integer | alternative to `xpath` | — | Zero-based frame index |
| `wait_timeout` | integer | no | `30` | |

```json
{ "action": "switch", "xpath": "//iframe[@id='checkout']" }
{ "action": "default" }
```

**The switch sticks.** It is session state on the Grid, not per-call, so
everything afterwards stays inside that frame until something switches back —
which is why `session://current` reports `in_frame`.

**Returns** `action`, `in_frame`, plus page state.

</details>

<details>
<summary><b><code>resize</code></b> &nbsp;·&nbsp; <code>POST /browser/resize</code> &nbsp;—&nbsp; change the window 📐</summary>

<br>

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `width` | integer | no | unchanged | |
| `height` | integer | no | unchanged | |

The headless default is narrow and varies between Grid nodes, so set it before
judging anything visual.

**Returns** the `width` and `height` now in effect, plus page state.

</details>

<details>
<summary><b><code>write</code></b> &nbsp;·&nbsp; <code>POST /browser/write</code> &nbsp;—&nbsp; type into a field ⌨️</summary>

<br>

Types into an input, textarea or contenteditable.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `xpath` | string | **yes** | — | The field to type into |
| `text` | string | **yes** | — | Empty string with `clear: true` empties the field |
| `url` | string | no | — | Page the field is on |
| `clear` | boolean | no | `true` | Empty the field first |
| `submit` | boolean | no | `false` | Press Enter afterwards — a one-call search box |
| `wait_timeout` | integer | no | `30` | |

**Returns** `value`, `url`, `title`. The value is read back off the element so you can confirm the text landed — and read *before* any submit, because submitting navigates and the element reference goes stale.

```json
{ "session_id": "…", "xpath": "//input[@name='q']", "text": "selenium grid", "submit": true }
```

</details>

<details>
<summary><b><code>press_key</code></b> &nbsp;·&nbsp; <code>POST /browser/press-key</code> &nbsp;—&nbsp; press a named key 🎹</summary>

<br>

Sends a key to an element, or to wherever focus happens to be.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `key` | string | **yes** | — | A name from the list below |
| `xpath` | string | no | — | Send it here rather than to the focused element |
| `url` | string | no | — | Page to press it on |
| `wait_timeout` | integer | no | `30` | |

**Returns** `key`, `url`, `title`.

Names are lowercase: `tab`, `enter`, `escape`, `backspace`, `delete`, `space`, `home`, `end`, `page_up`, `page_down`, `arrow_up`, `arrow_down`, `arrow_left`, `arrow_right`, `f1`–`f12`, `shift`, `control`, `alt`, `meta`, `numpad0`–`numpad9`, and the rest of Selenium's set. The live list is interpolated into the tool description, so it cannot drift.

⚠️ **Not a scrolling tool.** `page_down` only moves the page when focus happens to be on the scrollable container. Use `execute_script` to scroll.

```json
{ "session_id": "…", "key": "tab" }
```

</details>

<details>
<summary><b><code>extract</code></b> &nbsp;·&nbsp; <code>POST /browser/extract</code> &nbsp;—&nbsp; read the page 📖</summary>

<br>

Waits for an element to exist, then reads it. **The cheap way to read a page** — reach for this long before a screenshot.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `xpath` | string | **yes** | — | Element to read |
| `url` | string | no | — | Page to read from |
| `wait_timeout` | integer | no | `30` | |

**Returns** `html` (the element's `innerHTML`), `text` (visible text), `url`, `title`.

`//body` reads everything; a narrower XPath keeps the result small.

```json
{ "session_id": "…", "xpath": "//h1" }
```

</details>

<details>
<summary><b><code>execute_script</code></b> &nbsp;·&nbsp; <code>POST /browser/script</code> &nbsp;—&nbsp; run JavaScript 🧪</summary>

<br>

The escape hatch for anything the other eight don't cover: scrolling, drag and drop, computed styles, direct DOM access, or batch-reading a dozen values in one call.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `script` | string | **yes** | — | Use `return` to send a value back |
| `url` | string | no | — | Page to run it on |

**Returns** `result` (any JSON type), `url`, `title`.

```json
{ "session_id": "…", "script": "window.scrollTo(0, 2000); return document.title" }
```

</details>

<details>
<summary><b><code>screenshot</code></b> &nbsp;·&nbsp; <code>POST /browser/screenshot</code> &nbsp;—&nbsp; capture a PNG 📸</summary>

<br>

Three modes, in precedence order: `xpath` wins, then `full_page`, otherwise the visible viewport.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `url` | string | no | — | Page to capture |
| `xpath` | string | no | — | Capture only this element |
| `full_page` | boolean | no | `false` | The whole scrollable page |
| `width` | integer | no | — | Resize before capturing, and **leave** it resized |
| `height` | integer | no | — | |
| `wait_timeout` | integer | no | `30` | |

**Over MCP** you get an image content block a vision model can actually see. **Over HTTP** you get `image` (base64 PNG), `width`, `height` (real pixels, read from the PNG header), `bytes` (decoded size — the quickest way to spot a blank capture), plus `url` and `title`.

`width`/`height` resize the window and leave it that way; `full_page` resizes only for the capture and restores the previous size afterwards.

It's all plain W3C WebDriver, so it works on any browser the Grid runs. Chrome has no W3C full-page command, so `full_page` grows the window to the document height and captures that.

```json
{ "session_id": "…", "full_page": true, "width": 1280 }
```

</details>

<details>
<summary><b><code>close_session</code></b> &nbsp;·&nbsp; <code>POST /browser/close</code> &nbsp;—&nbsp; give the slot back 🧹</summary>

<br>

Quits the session and frees its Grid slot.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |

**Returns** `success`, `session_id`.

**Always call this, including on failure paths.** Slots are finite, and an abandoned session holds one until the Grid times it out.

```json
{ "session_id": "…" }
```

</details>

### 🧭 About that `url` parameter

`click`, `write`, `press_key`, `extract`, `screenshot` and `execute_script` all take an optional `url`, and it is **not an assertion**. If the browser is somewhere else, it goes there first — so you can jump straight to a page instead of clicking a path to it.

URLs compare with the fragment and any trailing slash ignored, so `/settings`, `/settings/` and `/settings#top` are one page. Query strings count as different.

---

## 🍪 Sessions

Over HTTP it is session in, session out, always — so an n8n workflow owns its session outright and can pass it between nodes.

**`open_session` always comes first.** Nothing opens a browser implicitly, because that is the only place its window size and timeouts can be chosen — hiding it hid the settings too.

After that there are two modes, and they are **exclusive**. `session://current` reports which one applies and links the reference that explains it:

| Mode | When | The rule |
|---|---|---|
| **saved** | the server can identify you | **never** pass `session_id` — it is not even advertised |
| **stateless** | it cannot, or you are on `/browser/*` | `session_id` is **required** on every call |

The server works out who is calling from the first of these it finds, and **never invents one** — a caller it cannot identify is stateless, not quietly handed a browser:

| Key | How it's set | How stable it is |
|---|---|---|
| **A name you choose** | `X-Session-Key` header, else `?session=<name>` on the MCP URL | Stable by construction — the same name comes back after a client restart or reconnect |
| **The MCP transport session** | the `Mcp-Session-Id` the server negotiates, automatically | Lasts as long as the client's connection; a reconnect is a new key |
| **stdio** | one process serves one client | Lasts as long as the process |

That column is about the *key*; how long the mapping behind it survives is `SESSION_TTL`.

Prefer the **query parameter**: one bearer credential shared across callers, each naming itself in its URL. The **header** wins over it, so an admin can pin one browser per credential and a caller cannot override it.

### What we actually set

| | Who owns it | Default here |
|---|---|---|
| **How long a browser lives** | the Grid — `SE_NODE_SESSION_TIMEOUT` on the node | `300s` idle, in the cluster repo |
| **How long we remember a caller** | `SESSION_TTL` | `3600s`, slid forward on every call |
| **Where we remember it** | `SESSION_STORE` | `memory` (or `redis` to share it) |

**Nothing here runs a cleanup loop, and nothing should** — the Grid expires idle browsers and the store expires its own keys. If the Grid reaped a browser we remembered, the next call notices and reopens it at the page it was last on, with the same settings. 🪄

### 📍 Where am I?

`session://current` reports the mode, the browser this client holds, whether the Grid still has it, whether you are inside a frame, and a link to the reference that applies. Reading it never opens a browser.

Resources are the least widely implemented corner of MCP — n8n has none — so the same status is also a `current_session` **tool**, hidden unless a client declares `?resources=off` or `X-MCP-Resources: off`. It stays callable either way.

### 📖 It teaches you how to use it

The server ships an **Agent Skill**: the strategic half tool descriptions cannot
hold — which session mode you are in, why `extract` beats `screenshot` by orders
of magnitude, how to reach a page in one call, and what a timeout usually means.

`SKILL.md` is a thin index; each reference is its own resource, so an agent loads
only what its task needs:

| Resource | Holds |
|---|---|
| `skill://selenium-flow/SKILL.md` | the index: session mode, the three rules, where to go next |
| `.../references/STATELESS.md` | you own the session id — the HTTP surface, and any client the server cannot identify |
| `.../references/SAVED_SESSIONS.md` | the server holds your browser — naming, sharing, transparent refresh |
| `.../references/READING_PAGES.md` | extract vs script vs screenshot, and XPath that keeps working |
| `.../references/INTERACTION.md` | forms, clicks, keys, scrolling, waiting |
| `.../references/TROUBLESHOOTING.md` | timeouts, dead sessions, blank captures, clicks that do nothing |
| `.../references/CONFIGURATION.md` | setting the server up, connecting a client, which env var to change |
| `skill://selenium-flow/_manifest` | the file listing, with sizes and hashes |

The two session references are mutually exclusive: `session://current` tells you
which one applies, and you read that one.

Those URIs are FastMCP's convention, not ours, and served by its own
`SkillProvider` — so `list_skills`, `get_skill_manifest` and `download_skill`
from `fastmcp.utilities.skills` work against this server with no special casing,
and an agent can pull the skill down into `~/.claude/skills` if it wants it
locally.

Same fallback as the session status: clients that cannot read resources get a
`selenium_flow_skill` tool instead, hidden otherwise. `SKILL_ENABLED=false` turns
both shapes off.

---

## ⚙️ Configuration

Every flag has an environment fallback, because containers are configured with env vars and developers reach for flags.

| Env | Flag | Default | Notes |
|---|---|---|---|
| `GRID_URL` | `--grid-url` | the in-cluster Grid Service | Selenium Grid hub |
| `MCP_AUTH_TOKEN` | `--auth-token` | unset | Bearer token for both surfaces. Unset disables auth |
| `ROUTE_PREFIX` | `--route-prefix` | `/browser` | Path prefix for the HTTP endpoints |
| `SAVED_SESSIONS` | `--no-saved-sessions` | `true` | Let MCP callers omit `session_id`. Never affects the HTTP endpoints |
| `SESSION_STORE` | — | `memory` | `memory` or `redis`. Unset, any `REDIS_*` setting implies `redis` |
| `SESSION_TTL` | — | `3600` | Seconds a caller's mapping is kept. Honoured by both stores |
| `REDIS_URL` | — | unset | Connection for `SESSION_STORE=redis` |
| `REDIS_HOST` / `REDIS_PORT` | — | `localhost` / `6379` | Alternative to `REDIS_URL` |
| `REDIS_DB` | — | `0` | Database index. Applied even when `REDIS_URL` carries no `/<index>` |
| `REDIS_USERNAME` / `REDIS_PASSWORD` / `REDIS_SSL` | — | unset | Credentials for the above |
| `REDIS_PREFIX` | — | `selenium-flow:session:` | Key namespace, so sharing a database is safe |
| `SKILL_ENABLED` | `--no-skill` | `true` | Serve the embedded skill as a resource, and as a tool for clients without resources |
| `WINDOW_WIDTH` / `WINDOW_HEIGHT` | — | node default | Default window size for new sessions |
| `PAGE_LOAD_TIMEOUT` | — | unbounded | Seconds a navigation may take. **Worth setting** — a hanging page otherwise holds a Grid slot |
| `SCRIPT_TIMEOUT` | — | driver default | Seconds `execute_script` may take |
| `STATELESS_HTTP` | `--stateless` | `false` | Drop MCP transport sessions. Required for more than one replica |
| `TRANSPORT` | `--transport` | `http` | `http` or `stdio` |
| `HOST` / `PORT` | `--host` / `--port` | `0.0.0.0` / `8000` | |
| `LOG_LEVEL` | `--log-level` | `INFO` | `DEBUG` logs which key each call resolved to, and how |

### Session defaults cascade

Window size and the two timeouts resolve in order of increasing specificity:

```
server default (env)  <  client default (?width= / X-Window-Width)  <  open_session argument
```

### 🔐 Auth

Setting `MCP_AUTH_TOKEN` turns on auth for both surfaces at once. Clients send it the usual way:

```
Authorization: Bearer <token>
```

The HTTP endpoints also accept the bare token as the `Authorization` value, for clients that can't express a scheme. `/health` is always open.

In the cluster the token is generated by External Secrets, so no value is authored anywhere — see `apps/selenium/components/mcp` in the cluster repo.

---

## 🚀 Running it

```bash
docker compose up --build
```

That starts the server **and** a standalone Grid for it to drive, with auth off:

```bash
curl localhost:8000/health

curl -X POST localhost:8000/browser/open \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","width":1280,"height":800}'
```

Point an MCP client at `http://localhost:8000/mcp` — and watch the browser work live at **`localhost:7900`**, the Grid's noVNC view. 👀

---

## 🛠 Contributing

Setup, tests, how the OpenAPI spec is generated and how the embedded skill is
packaged: [CONTRIBUTING.md](CONTRIBUTING.md). Design rules worth reading before
changing behaviour: [AGENTS.md](AGENTS.md).

---

## 🔗 References

- [Model Context Protocol](https://modelcontextprotocol.io/) · [FastMCP](https://gofastmcp.com/)
- [Selenium Grid](https://www.selenium.dev/documentation/grid/) · [Selenium Python API](https://selenium-python.readthedocs.io/) · [RemoteWebDriver](https://www.selenium.dev/documentation/webdriver/drivers/remote_webdriver/)
- [Docker Hub — kubed/selenium-flow](https://hub.docker.com/r/kubed/selenium-flow)

---

## 📜 Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or sponsored by the Selenium project. "Selenium" is a trademark of the Software Freedom Conservancy, used here only to identify the software this server drives.
