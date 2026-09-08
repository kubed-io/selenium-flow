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

**This server holds no browser.** That is the whole trick: the session lives on the Grid and the caller carries its id, so the server can restart, scale to zero, or sit behind several replicas without anyone losing a tab. 🪄

---

## 🔀 Two surfaces, one implementation

| Surface | For | Path |
|---|---|---|
| **MCP** over Streamable HTTP | agents and MCP clients | `/mcp` |
| **JSON** over HTTP | n8n HTTP nodes, curl, scripts, anything | `/browser/*` |

Every action is a tool **and** an endpoint, one to one — and a test fails the build if that ever stops being true. Hand a whole task to an agent, or drive the same actions yourself when you want exact control, and move between the two without giving up a single capability.

They differ in exactly one place, and only where it earns its keep: `screenshot` hands MCP an image block a vision model can *see*, and HTTP a base64 payload a script can save.

`GET /health` reports Grid readiness and the live session count. `GET /openapi.yaml` (or `.json`) describes the HTTP surface. Neither asks for credentials — a kubelet hasn't got any, and a contract you must authenticate to read is needlessly awkward.

---

## 🧰 Every action, both ways

Nine actions. Each one is a tool and an endpoint; the parameters are identical, with a single exception noted below. All endpoints are `POST` with a JSON body.

> **The one difference:** over HTTP, `session_id` is always **required**. Over MCP it is optional, because the server can work out which browser you mean — see [Sessions](#-sessions).

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

`click` and the double/right variants wait for the element to be *clickable*;
`hover` and `scroll_to` only wait for it to *exist*, since requiring clickability
would refuse exactly the off-screen element `scroll_to` is for.

`hover` is the one with no alternative — menus that appear only on mouse-over
cannot be reached any other way.

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
it. Send the file and it is shipped there for you.

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `session_id` | string | **yes** *(HTTP)* | — | |
| `xpath` | string | **yes** | — | The `<input type="file">` |
| `content` | string | **yes** *(or `path`)* | — | The file. Base64 over JSON and MCP; a raw file part over multipart |
| `filename` | string | no | the part's name | What the page sees |
| `path` | string | no | — | A file already on the **server's** filesystem. Use instead of `content` |
| `url` | string | no | — | |
| `wait_timeout` | integer | no | `30` | |

**Over HTTP, post a normal file** — no base64 needed:

```bash
curl -X POST localhost:8000/browser/upload \
  -F session_id=… -F 'xpath=//input[@type="file"]' -F content=@report.csv
```

Base64 exists because MCP tool arguments must be JSON; there is no binary input
channel in the protocol.

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

**This server never answers a dialog for you.** Chrome's default is to silently
dismiss one — quietly clicking *Cancel* on a confirmation and destroying the
evidence — so `unhandledPromptBehavior` is set to `ignore` deliberately. An
action that opens a dialog still succeeds and reports it, and `read` inspects
the message without answering.

**Returns** `action`, `message` (read before answering), plus page state.

</details>

<details>
<summary><b><code>resize</code></b> &nbsp;·&nbsp; <code>POST /browser/resize</code> &nbsp;—&nbsp; change the window 📐</summary>

<br>

Window size is one of the few things WebDriver lets you change after the browser
is open, which is why it is its own action and not only an `open_session`
argument — a caller whose browser was opened for it can still set the size.

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

`open_session` returns a `session_id` and every other call takes it. Over HTTP that is the whole story — session in, session out, always — so an n8n workflow owns its session outright and can pass it between nodes, store it, or hand it to another workflow.

**Over MCP, `session_id` is optional.** An agent works one conversation at a time and gains nothing from threading an id through every call, so the server remembers a browser per caller. It works out who is calling from the first of these it finds, and **never invents one** — a caller it cannot identify is told to pass `session_id` rather than being quietly handed a fresh browser:

| Key | How it's set | How stable it is |
|---|---|---|
| **A name you choose** | `X-Session-Key` header, else `?session=<name>` on the MCP URL | Stable by construction — the same name comes back after a client restart or reconnect |
| **The MCP transport session** | the `Mcp-Session-Id` the server negotiates, automatically | Lasts as long as the client's connection; a reconnect is a new key |
| **stdio** | one process serves one client | Lasts as long as the process |

That column is about the *key*, not the browser: how long the mapping behind it survives is `SESSION_TTL` below.

The **query parameter** is usually the one you want: one bearer credential shared across callers, each naming itself in its own URL — so n8n needs a single credential rather than a multi-header one per agent. Setting the **header** instead pins a session to a credential, so an admin can enforce one browser per credential and a caller cannot override it from the URL. Leaving the header out is equally a decision: it delegates the choice to whoever implements the call.

An explicit `session_id` always wins over all of it, and is taken on trust — you may well have opened it through the HTTP surface.

### What we actually set

| | Who owns it | Default here |
|---|---|---|
| **How long a browser lives** | the Grid — `SE_NODE_SESSION_TIMEOUT` on the node | `300s` idle, in the cluster repo |
| **How long we remember a caller** | `SESSION_TTL` | `3600s`, slid forward on every call |
| **Where we remember it** | `SESSION_STORE` | `memory` (or `redis` to share it) |

**Nothing here runs a cleanup loop, and nothing should.** The Grid expires idle browsers on its own and Redis expires its own keys, so both halves are already somebody's job. If the Grid has reaped a browser we remembered, the next call notices, reopens one, and navigates back to the page it was last on — the refresh is invisible. 🪄

### 📍 Where am I?

`session://current` is an MCP **resource** reporting the browser this client is holding: `session_id`, the page it's on, and whether the Grid still has it. Reading it never opens a browser, so a null `session_id` genuinely means nothing is held.

Resources are the least widely implemented corner of MCP — n8n has no notion of them — so the same status is also a `current_session` **tool**, hidden from `tools/list` by default. A client that can't read resources says so with `?resources=off` or an `X-MCP-Resources: off` header, and the tool appears. It stays callable either way.

### 📖 It teaches you how to use it

The server ships an **Agent Skill** describing how to drive it well — the
strategic half tool descriptions cannot hold: whether you need to pass
`session_id` at all, why `extract` beats `screenshot` by orders of magnitude, how
to reach a page in one call instead of clicking a path to it, that scrolling is
`execute_script` and not `press_key`, and what a timeout on a good XPath usually
means.

`SKILL.md` is a thin index — the two facts that matter, the one branch every
caller has to take, and pointers to the rest. Each reference is its own resource,
so an agent loads only what its task needs:

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

The skill lives at [`skills/`](skills/) in the repo, where it reads as
documentation, and is mapped into the package at build time so it ships **inside
the wheel**. The guidance and the tools it describes therefore cannot be
versioned apart — upgrade the server and the advice upgrades with it. Nothing to
mount, nothing to sync.

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
| `STATELESS_HTTP` | `--stateless` | `false` | Drop MCP transport sessions. Required for more than one replica |
| `TRANSPORT` | `--transport` | `http` | `http` or `stdio` |
| `HOST` / `PORT` | `--host` / `--port` | `0.0.0.0` / `8000` | |
| `LOG_LEVEL` | `--log-level` | `INFO` | `DEBUG` logs which key each call resolved to, and how |

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

## 📐 The OpenAPI spec writes itself

Request schemas come from the MCP tools themselves — the very objects FastMCP publishes to agents — so the two contracts aren't two descriptions that happen to agree, they're the same schema. Add a parameter to a tool and the spec follows with no further work.

It's committed at [`openapi.yaml`](openapi.yaml) so it can be reviewed in a pull request and linted, and a test fails the build if it drifts from what the code produces:

```bash
python scripts/generate_openapi.py   # the fix when that test fails
```

Response shapes are the one hand-maintained half, in `openapi.py` — the actions return plain dicts, so there's nothing to introspect. A test asserts every endpoint has one.

---

## 🧪 Development

```bash
pip install -e ".[test]"
ruff check kubed
pytest
```

The tests wire a server against an unroutable Grid address and drive both surfaces through the real ASGI app, so they need no browser and no network. Agent-facing notes on the internals live in [AGENTS.md](AGENTS.md).

---

## 🔗 References

- [Model Context Protocol](https://modelcontextprotocol.io/) · [FastMCP](https://gofastmcp.com/)
- [Selenium Grid](https://www.selenium.dev/documentation/grid/) · [Selenium Python API](https://selenium-python.readthedocs.io/) · [RemoteWebDriver](https://www.selenium.dev/documentation/webdriver/drivers/remote_webdriver/)
- [Docker Hub — kubed/selenium-flow](https://hub.docker.com/r/kubed/selenium-flow)

---

## 📜 Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or sponsored by the Selenium project. "Selenium" is a trademark of the Software Freedom Conservancy, used here only to identify the software this server drives.
