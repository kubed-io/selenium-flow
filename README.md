# 🌊 Selenium Flow

**One browser, many calls.** Drive a real Chrome or Firefox on [Selenium Grid](https://www.selenium.dev/documentation/grid/) from an agent over MCP, or from anything else over plain HTTP. Same actions, same server, one browser that stays exactly where you left it.

[![🧪 Test](https://github.com/kubed-io/selenium-flow/actions/workflows/test.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/test.yml)
[![🛡️ Quality](https://github.com/kubed-io/selenium-flow/actions/workflows/quality.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/quality.yml)
[![📸 Image Builder](https://github.com/kubed-io/selenium-flow/actions/workflows/image.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/actions/workflows/image.yml)
[![📖 Wiki](https://github.com/kubed-io/selenium-flow/actions/workflows/wiki.yml/badge.svg)](https://github.com/kubed-io/selenium-flow/wiki)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-kubed%2Fselenium--flow-2496ed?logo=docker&logoColor=white)](https://hub.docker.com/r/kubed/selenium-flow)
[![FastMCP](https://img.shields.io/badge/FastMCP-4-8a2be2)](https://gofastmcp.com/)

---

## The whole idea, in one breath

Open a browser once. It stays alive (same page, same cookies, same scroll position) while an agent works a task one call at a time, or an n8n workflow steps from node to node. Nothing relaunches, nothing logs in twice.

```
   agent  ──── MCP  /mcp ─────▶  ┌───────────────┐        ┌───────────────┐
                                 │ selenium-flow │ ─────▶ │ Selenium Grid │ ──▶ 🌐
workflow  ──── HTTP /browser ──▶ └───────────────┘        └───────────────┘
                                  holds no browser          the browser
                                                            lives here
```

The browser lives on the Grid, so the server can restart without anyone losing a tab. Keep its records in Redis and it remembers whose browser is whose across the restart too.

---

## 🧰 Every action, both ways

Twenty actions, each an MCP tool **and** an HTTP endpoint, with one schema between them. Hand a whole job to an agent, or drive the same steps yourself from an n8n HTTP node when you want exact control.

| Action | |
|---|---|
| [`open_session`](https://github.com/kubed-io/selenium-flow/wiki/open_session) | Start a browser: Chrome or Firefox, any window size |
| [`navigate`](https://github.com/kubed-io/selenium-flow/wiki/navigate) | Go to a URL |
| [`interact`](https://github.com/kubed-io/selenium-flow/wiki/interact) | Click, double-click, right-click, hover, scroll to |
| [`drag`](https://github.com/kubed-io/selenium-flow/wiki/drag) | Drag onto another element, or by an offset |
| [`write`](https://github.com/kubed-io/selenium-flow/wiki/write) | Type into a field, or type a secret nobody sees |
| [`press_key`](https://github.com/kubed-io/selenium-flow/wiki/press_key) | Press Tab, Enter, the arrows |
| [`extract`](https://github.com/kubed-io/selenium-flow/wiki/extract) | Read text and HTML off the page |
| [`outline`](https://github.com/kubed-io/selenium-flow/wiki/outline) | What is on the page, with a checked selector for each thing |
| [`assert`](https://github.com/kubed-io/selenium-flow/wiki/assert) | Wait until JavaScript comes back true, or fail |
| [`screenshot`](https://github.com/kubed-io/selenium-flow/wiki/screenshot) | A PNG of the viewport, the whole page or one element |
| [`print`](https://github.com/kubed-io/selenium-flow/wiki/print) | Keep the page as a PDF or as HTML |
| [`execute_script`](https://github.com/kubed-io/selenium-flow/wiki/execute_script) | Run JavaScript: the escape hatch |
| [`frame`](https://github.com/kubed-io/selenium-flow/wiki/frame) | Step into an iframe and back out |
| [`dialog`](https://github.com/kubed-io/selenium-flow/wiki/dialog) | Answer an alert, confirm or prompt |
| [`resize`](https://github.com/kubed-io/selenium-flow/wiki/resize) | Change the window at any time |
| [`upload_file`](https://github.com/kubed-io/selenium-flow/wiki/upload_file) | Put a file into a file input |
| [`save_site_data`](https://github.com/kubed-io/selenium-flow/wiki/save_site_data) | Keep cookies and storage for the next browser |
| [`network_body`](https://github.com/kubed-io/selenium-flow/wiki/network_body) | One captured response body |
| [`await_control`](https://github.com/kubed-io/selenium-flow/wiki/await_control) | Wait for a person to hand the browser back |
| [`end_browser`](https://github.com/kubed-io/selenium-flow/wiki/end_browser) | Give the Grid slot back, keep the workspace |

Most of them take an optional `url`: if the browser is somewhere else it goes there first, so you can jump straight to a page in one call. Every parameter and return field is on the [wiki](https://github.com/kubed-io/selenium-flow/wiki/Actions), generated from the live tool schemas.

---

## 🧠 Workspaces outlive browsers

Name your workspace, with `?workspace=research-bot` on the URL or an `Sf-Workspace` header, and you get the browser that belongs to that name. There is no session id anywhere. Call again with the same name after a reconnect, a restart or a week away, and you are back on the same browser at the same page.

When the Grid reaps an idle browser, or an operator ends one, the workspace keeps the page it was on and the settings it was opened with, so recovery is one call:

```
open_session()      # back where you were, with the settings you chose
```

Between calls the server keeps your browser alive for 15 minutes, so an agent that stops to think doesn't come back to an empty page. `workspace://current` says where you are without opening anything.

---

## 👀 Watch it live, and take the wheel

The admin UI shows each workspace's browser live in its Live tab. When an agent hits a captcha or a sign-in it can't do, take **Control**. While you hold it, an agent's calls that would change the page are refused and its reads still answer. You solve it and hand back; the agent, waiting in `await_control`, learns which pages you went through.

---

## 🔁 Flows: do it once, run it forever

Drive a form once, save the steps under a name, and every run after that is one call:

```
run_flow(name="sign-up", params={"email": "a@example.com"})
```

A step is a tool call, checked against the live schemas when it is saved. A flow runs in whatever browser you hold, so the same flow checks Chrome and then Firefox without an edit. Give it `sites` and `paths`, and a result on a matching page names it in `flows_here`, so an agent finds the flow for the page it is on. Every workspace has its own library beside a shared `global` one.

---

## 🔐 Secrets: typed, never shown

Mount credentials the way Kubernetes mounts a `Secret` (a directory per secret, a file per key) and say which sites each may be typed on. An agent sees names and keys, never a value, and binds one where the value goes:

```
write(selector={"css": "#password"}, secret={"name": "nextcloud", "key": "password"})
```

The server types it. If the value shows up later in a page, a result or a resource, it comes back as `[secret:nextcloud/password]`. A secret is refused on any site it wasn't allowed on.

---

## 🍪 Sign in once

`save_site_data` keeps the cookies, localStorage and sessionStorage of the sites a workspace visited. The next browser, even one reopened after a reap, has them restored before its first page loads, so the agent is still signed in.

---

## 🧾 See what the page saw

`open_session(capture=true)` keeps the browser's console and network traffic, masked, at `workspace://console` and `workspace://network`. A call that set off a page error says so in its result, and `network_body` fetches the response behind a failing request.

---

## 🗂 Screenshots, prints, downloads and recordings

Give the server a data directory and every screenshot and print is kept with the workspace, outliving the browser. Downloads are listed while the browser lives, and `keep_file` saves one before it goes. Turn on recording and a browser opened with `record=true` has its whole life filmed by the Grid, filed into the workspace's Recordings. Every file comes with a time-limited signed link to hand to someone, or drop into an `<img>`.

---

## 📖 It teaches you how to use it

The server ships an **Agent Skill** at `skill://selenium-flow/SKILL.md`: how to name a workspace, why `extract` beats `screenshot`, how to reach a page in one call, and what a timeout usually means. Each reference is its own resource, so an agent loads only what its task needs.

A client that speaks the agents extension also finds the **explorer**, a subagent that explores a site in your workspace, runs the flows that fit each page, saves new ones and reports back.

---

## 🖥 The admin UI and MCP Apps

Open the server's root for every workspace: its browser, live screen, files, flows, saved site data, history and captured traffic. Sign in with the server's token or, for an admin role, through your OIDC provider.

Hosts that render [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) get `show(uri)`, which draws any resource the server serves inline: your workspace, its files, flows, secrets and the skill. A client that can't read resources gets `list_resources` and `read_resource` instead.

---

## 🚀 Quick start

```bash
git clone https://github.com/kubed-io/selenium-flow.git && cd selenium-flow
docker compose up --build
```

That starts the server on port 8000 with a Chromium Grid beside it, and auth off. Open a browser over HTTP:

```bash
curl -X POST localhost:8000/browser \
  -H 'Sf-Workspace: demo' -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com", "width": 1280, "height": 800}'
```

Point an MCP client at `http://localhost:8000/mcp?workspace=demo`, and open `http://localhost:8000` to watch the `demo` workspace in its Live tab. 👀

In production, set `AUTH_TOKEN` and send it as `Authorization: Bearer <token>`. Every setting is a config file key, an environment variable and a flag.

---

## 📚 Where next

- **[The wiki](https://github.com/kubed-io/selenium-flow/wiki)**: [Installing](https://github.com/kubed-io/selenium-flow/wiki/Installing) in Claude, VS Code, n8n and others, [Deployment](https://github.com/kubed-io/selenium-flow/wiki/Deployment), [Configuration](https://github.com/kubed-io/selenium-flow/wiki/Configuration), and a page per action.
- **[The spec](docs/superpowers/specs/README.md)**: every requirement, one area per file, in the wiki's `superpowers/specs/`.
- **[CONTRIBUTING.md](CONTRIBUTING.md)**: setting up the repo, the tests, CI and releases.
- **[Docker Hub](https://hub.docker.com/r/kubed/selenium-flow)**: `kubed/selenium-flow`.

---

## 📜 Licence

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or sponsored by the Selenium project. "Selenium" is a trademark of the Software Freedom Conservancy, used here only to identify the software this server drives.
