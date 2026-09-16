# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!--
  These ARE the release notes. One SHORT line per entry, written for a user —
  never a paragraph. Say what someone can now do, not how it was built and not
  why. Only **BREAKING:** may stretch. Internal work — CI, refactors, tests,
  types, docs — usually earns no line at all, and never more than a terse one.
  Deeper detail lives in AGENTS.md or the PR, not here.

  A version may open with a short preamble above its first heading, framing the
  release as a whole. That is prose, and the "never a paragraph" rule does not
  reach it — it is about the entries.

  ONLY EVER EDIT THE [Unreleased] SECTION. Every section below it carries a
  version number and is IMMUTABLE — those notes shipped with a release and must
  never be reworded, reordered, or removed. Add new work under [Unreleased].
  publish.yml (duplocloud/version-bump) rolls [Unreleased] into a dated version
  section at release time. See CONTRIBUTING.md / AGENTS.md.
-->

## [Unreleased]

### Added

- **A refused tool call says what shape it wanted** — `css="button.go"` is told `selector={"css": "button.go"}`, in the same words `save_flow` uses for a step.

- **`outline` lists a page's content before its navigation, header, footer and sidebars**, says which `region` each entry is in, and reports a `total` so a cut-short map says so.

### Changed

- **A flow run's default budget is 120 seconds**, down from 300. A flow meant to wait longer sets `timeout`.

- **A failed MCP call reads like a failed HTTP call** — no Selenium stack dump for the caller, and a caller's mistake is one warning line in the log rather than a traceback.

### Fixed

- **Error messages keep their XPath.** `//input[@name='q']` was cut to `//name='q']` by the credential scrub.

- **The flow document schema resolves its selectors**; every step's `selector` referenced a definition the schema did not include.

### Added

- **A flow can declare its own `timeout`**, in seconds, for a run that exists to wait longer than the default 300.

- **`run_flow` reports progress** — the step it is on, and a heartbeat while one waits — so a long run stays alive in clients that abort a silent call, and shows as a progress bar where the client draws one.

### Fixed

- **Cancelling `run_flow` stops the run** instead of leaving it to drive the browser to the end.

## [0.2.0] - 2026-09-16

### Added

- **One endpoint per probe**: `/health` (liveness, and deliberately independent of the Grid), `/started` (startup), `/ready` (readiness, which is the one that asks the Grid) and `/info` (version, mount, what it is wired to). All four answer at the root as well as under the prefix.

- **`outline` maps the page** — every element worth acting on, with a selector checked to match exactly one, and whether it can be used.

- **A failed click says why, and what to do**: a hidden ancestor, something on top of it, no size, off-screen, disabled.

- **A flow can say what must be true.** `assert` runs JavaScript that has to come back `true`, and fails with the message you wrote.

- **Two prompts a person can pick.** `repair_flow` fixes a flow against the page it now fails on; `build_flow` walks a task by hand and saves what worked.

- **A failed run says where to read and which prompt repairs it.**

- **`press_key` takes the browser's key names, single characters and combinations** — `ArrowLeft`, `/`, `Control+a`, `Shift+Tab`.

- **`drag` drags an element onto another, or by an offset** — `drag(selector={"css": ".card"}, to={"css": ".done"})` or `drag(selector={"css": "input[type=range]"}, by_x=120)`. Real pointer input, so range sliders respond, and on Chrome it drives native HTML5 drag-and-drop as well.

- **`interact` takes `glide`** — the pointer travels in steps instead of jumping, for interfaces that watch movement rather than arrival.

- **`assert` takes `stable_for`** — the answer must still be true that many seconds later, which is what a guard needs and what asking until true cannot give it.

- **`upload_file` takes `kept`**, the name of a file `keep_file` kept — download an export from one site and upload it to another without the bytes passing through you.

- **`open_session(fresh=true)`** opens on a blank page instead of returning to the one your session was last on.

### Changed

- **The server's instructions name the embedded skill**, so a client is told where the how-to lives when it connects rather than after its first failure. Omitted when the skill is not being served.

- **BREAKING: `ROUTE_PREFIX` mounts the whole server**, and defaults to `/`. Every tree is fixed beneath it — `/browser`, `/flows`, `/files`, `/admin`, `/mcp`, `/openapi.yaml` — where it used to rename `/browser` while everything else stayed put.

- **The admin UI is at the server root**, with its views after the hash (`#/sessions/<name>`), so a view survives a reload and can be linked to. `/admin` redirects there and remains the API the page calls.

- **BREAKING: every session is named by its caller.** Add `?session=<name>` to the URL or send an `X-Session-Key` header; sending both, or neither, is refused. There is no `session_id` on any tool, in any request body, or in any result — call again with the same name to get the same browser back.

- **BREAKING: the HTTP surface is REST.** `GET /flows/{name}`, `PUT` to save it, `DELETE` to remove it, `POST /flows/{name}/runs` to run it; `POST /browser` opens your browser, `DELETE /browser` ends it, `GET /browser` says what you are holding; a mouse action is a path — `POST /browser/interact/click`. Files are `GET /files` and `PUT /files/{name}/kept`.

- **BREAKING: `xpath` and `css` are one `selector`.** `interact(selector={"css": "button.go"})`, and `drag` takes `selector` and `to`. Exactly one of the two, never both — and never a fallback from one to the other.

- **`SAVED_SESSIONS` is gone**, along with the two session modes it switched between. There is one contract now.

- **A browser opened over HTTP is a session like any other** — it appears in the admin list, slides its TTL, and is reopened where it left off after the Grid reaps it.

- **Screenshots are kept with the session's files by default**, and come back with a link anyone can open — signed and time-limited when the server has a token, a plain path when authentication is off. `screenshot(save=false)` opts out, and a page that refuses the download returns `file_error` with the image.

- **BREAKING:** a stored file is now described the way `session_files` describes one — `created` instead of `creationTime`, plus `content_type`, `image`, `kept`, `url` and `absolute_url`. Affects the `file` in `screenshot` and `save_pdf` results.

- **Tools list their fixed choices in their schemas** — every `interact`, `dialog` and `frame` action, and `open_session`'s browsers — so a client can show them, and a flow naming one that does not exist is refused when it is saved.

- **Every mouse gesture moves the pointer onto the element first**, so after a click the pointer is on what you clicked and a `:hover` menu stays open across it. The click itself is unchanged, so a covered target is still refused.

- **`outline` says what *reveals* a hidden element, not just what blocks it** — `revealed_by` carries the trigger's selector and `open_with` says whether to click it or hover it.

- **A run report says where each step went** when the page changed, without `verbose`.

- **A saved flow that starts on whatever page you happen to be on is warned about** — not refused.

### Fixed

- **An unreachable Grid no longer writes its credentials into the log.** The URL is stripped from every failure message and from the traceback, not only from the one failure known to quote it.

- **A flow step that captures a file says where it went.** A screenshot's link is in the run report without needing `return: true` — a capture nobody can find cannot show anyone what it saw.

- **The admin flow panel shows a selector as one expression** — `css button.go` — instead of a raw JSON object.

- **The Grid's URL no longer appears with its credentials** in `/ready` or `/info`, which answer to anyone.

- **A click on a page that repaints itself is no longer racy** — the element is found again and the action retried once, rather than failing with a stale reference. Found by the admin page's own session list, which refreshes every two seconds.

- **The admin page lists sessions again on Redis** — it went blank once any browser had been pointed at something.

- **A screenshot of a page the browser will not download from no longer costs fifteen seconds.** It says so straight away and still returns the image.

- **A saved file is named for what it actually is** — `screenshot(filename="chart.pdf")` is stored as a PNG.

- **A hover onto something the pointer was already on did nothing and reported success.** The pointer now steps aside first, and the result says `nudged`.

- **The advice for a hidden menu said "hover" even when the page said it opens on click.** It reads `aria-expanded` now and says which.

- **`outline` no longer offers a selector built from a container's concatenated descendant text**, and no longer skips an `<a>` for having no `href`.

- **An unkept file says what would keep it** — `keep_with` on every entry whose link dies with the browser.

- **A glide to something below the fold works.** It used to send coordinates outside the window, which WebDriver refuses, and the whole move was lost.

- **A drag step with no element or no destination is refused when it is saved**, rather than at run time.

- **`upload_file` naming a kept file that is not there answers 400**, like naming a `path` that is not there — not 500.

- **`POST /browser/upload` takes `session`**, naming the library a kept file belongs to — the same way `/files/keep` already does, so a file kept into a named session can be uploaded back out of it.

- **A drag that failed because the browser had gone says so** — 404 with the fix, instead of 400 blaming the element's shape.

- **A drag whose source is taller than the window works.** The pointer's position is now where WebDriver actually puts it — the element's in-view center — rather than the center of a rectangle running off the screen.

- **A flow that uploads a kept file reads it from the library the run belongs to**, not from the shared `global` one.

- **`assert` refuses a `stable_for` it cannot read** instead of silently dropping the stability check, and no longer accepts an answer a slow script returned after `wait_timeout` had passed.

## [0.1.0] - 2026-09-12

0.0.2 could drive a browser. This one lets an agent keep what it worked out.
Save a sequence of steps as a named flow, give it parameters, and run the whole
thing in one call — including steps that type a credential the model is never
shown. The admin UI grew a panel for reading and editing them.

### Added

- **Saved flows.** Save a sequence of steps under a name and run the whole thing in one call with `run_flow` — a twelve-step form becomes one call instead of twelve. Set `FLOW_DATA_DIR` to turn them on.

- **Flows take parameters.** Declare them and write `${name}` in any argument of any step — `url: ${site}/orders/${id}` — so one flow serves every account and every environment.

- **Type a secret you never see.** Give `write` a `secret` naming one instead of `text`, and the server reads it and types it, in a flow step or a single call. It never becomes an argument string, so it cannot be assembled into one by mistake.

- **A secrets catalogue.** Point `SECRETS_DIRS` at a directory per secret and a file per key — the shape Kubernetes already mounts — and `list_secrets` shows an agent what it may use. A secret can be leashed to the sites it is allowed on. Values are never returned by anything.

- **A shared `global` flow library** every session can list and run, which only an operator can change.

- **Kept files.** `keep_file` copies a download out of the browser so it survives the browser being reaped, switched or ended, and `session_files` lists both kinds together.

- **The admin UI reads and edits flows.** Every session's flows beside its files: what a flow takes, what it does, and what any step or parameter holds. Edit the YAML, move a flow to or from `global`, or delete it.

- **CSS selectors.** Every tool that acts on an element now takes `css` as well as `xpath` — pass one or the other.

- **A flow run answers with the steps you marked `return: true`** — any number of them, and nothing else.

- `screenshot(save=true)` now tells you what the file was called, which is the name `keep_file` takes.

- The built-in skill teaches flows and secrets, and the [wiki](https://github.com/kubed-io/selenium-flow/wiki) documents them with a page per endpoint.

### Changed

- **HTTP errors now say whose fault they are**: `400` for a request you can fix, `404` for a browser that has ended, `503` for a Grid that is unreachable or full. They were all `500`.

- **`Clear downloads` lists exactly what it will remove** before it removes it.

- Error messages no longer carry the driver's stack trace.

### Fixed

- **A browser stays usable after logging in.** Chrome's "Save password?" prompt took the keyboard and mouse for itself, so every click and keystroke after a login silently did nothing — while every call still reported success.

## [0.0.2] - 2026-09-10

The first release. Everything is new, which is why there is no *Changed* or
*Fixed* below — both are relative to a version somebody is running, and there
isn't one.

### Added

- **Drive a real Chrome or Firefox browser from an agent.** Fourteen actions: open and end a browser, navigate, click, hover, type, press keys, read an element, screenshot, print to PDF, upload a file, switch frames, answer dialogs, resize, and run JavaScript.

- **Every action is an MCP tool *and* a plain HTTP endpoint**, one to one. Hand the whole job to an agent over MCP, or drive the same actions yourself from an n8n HTTP node when you want exact control.

- **The browser stays open between calls**, keeping its page, cookies and scroll position — it lives on the Grid, so a restart of this server does not lose it.

- **The server can hold your browser for you.** Name your session with `?session=<name>` on the MCP URL or an `X-Session-Key` header, and `session_id` becomes optional on every call.

- **A session outlives its browser.** When the Grid reaps one, or an operator ends it, the session keeps the browser choice, the window size and the page it was on — so `open_session()` with no arguments puts you back where you were.

- **Chrome or Firefox, chosen per session** with `open_session(browser="firefox")`. Every other action behaves identically on either.

- **`session://current` says what you are holding**: the browser, the page, the window size, whether you are inside a frame, and whether the Grid still has it. Reading it never opens a browser.

- **Screenshots come back as images an agent can see** — the viewport, one element, or the whole scrollable page.

- **`save_pdf` prints with the browser's own print engine**, so the text stays selectable and the whole document is included rather than just the viewport.

- **Everything the browser downloads is kept with the session**, alongside anything saved with `screenshot(save=true)` or `save_pdf`, and listed as `session://files`.

- **Signed file URLs**, so a screenshot can be shown in a chat transcript or an `<img>` tag rather than described. Rotating the server token revokes every link.

- **An admin UI at `/admin`**: live sessions, what each has downloaded, clickable thumbnails, the Grid's own console, and buttons to clear a session's files or end its browser. It updates itself as things change.

- **Bearer token auth on both surfaces.** `/health` stays open so a kubelet can probe a pod that has no credentials, and reports Grid readiness rather than just process liveness.

- **`GET /openapi.yaml`** describing the HTTP surface, generated from the MCP tool schemas so the two contracts cannot drift.

- **An Agent Skill ships inside the wheel**, written as an index so an agent loads only the reference its task needs.

- **MCP Apps components**: a host that implements the extension renders a file grid instead of JSON.

- **Sessions in memory or Redis**, and `--stateless` for running more than one replica.

- **Ships as the `kubed/selenium-flow` image**, and as a wheel attached to each release. The manual is the [wiki](https://github.com/kubed-io/selenium-flow/wiki).
