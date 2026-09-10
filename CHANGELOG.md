# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!--
  These ARE the release notes. One line per entry, written for someone reading
  "what's new" — never a paragraph. Length tracks impact: functional changes get
  the most words (still one line); refactors/tests stay short; CI/devops are
  shortest. Only **BREAKING:** may stretch.

  ONLY EVER EDIT THE [Unreleased] SECTION. Every section below it carries a
  version number and is IMMUTABLE — those notes shipped with a release and must
  never be reworded, reordered, or removed. Add new work under [Unreleased].
  publish.yml (duplocloud/version-bump) rolls [Unreleased] into a dated version
  section at release time. See AGENTS.md.
-->

## [Unreleased]

### Added

- A flow session now outlives the browsers it holds. `session_id` is empty when it holds none — after the Grid reaps one, or an operator ends one — and the record keeps the browser choice, window and last page. `open_session()` with no arguments inherits all of it, so recovery is one call: same browser, same window, back where you were. The settings cascade gains a tier for it, between the client default and an explicit argument.
- Stateless callers get a record too, keyed under their own browser id, so their sessions appear in the admin history and expire like everything else. It is not a caller key — nothing resolves a caller from it, so the leak `caller_key` prevents stays prevented.
- End a browser from the admin UI: an **End browser** button in a session's detail toolbar, beside Clear files, backed by `DELETE /admin/sessions/{key}`. It detaches the browser and keeps the session, so the caller loses the browser's live state but not its place. An abandoned browser already expires on its own — the Grid reaps it on `SE_NODE_SESSION_TIMEOUT` and an autoscaled node then scales to zero — so this is for not waiting out those minutes while one of a handful of Grid slots sits held.
- `open_session(browser="firefox")` opens Firefox instead of Chrome — one argument, and every other action behaves identically on both, because both are plain W3C WebDriver and only session creation differs. The choice is stored with the session, so one the Grid reaped reopens as the same browser rather than the default; `session://current` reports it, and the admin UI marks each session with the browser it is running. Set a server-wide default with `DEFAULT_BROWSER`, or a per-client one with `?browser=` / `X-Browser`.

- Session files: everything a browser downloads is kept in Selenium Grid's own per-session store, created with the session and deleted with it, so there is no second store to clean up. Read it as the `session://files` resource, one file at a time as `session://files/{name}`, or the `session_files` tool.
- `save_pdf` prints the current page with the browser's own print engine — selectable text, whole document — and keeps it with the session's files; `screenshot(save=true)` keeps a capture the same way.
- Signed file URLs (`/files/{session}/{name}?exp=&sig=`), so a screenshot can be shown in an `<img>` tag or a chat transcript, neither of which can send an `Authorization` header. The MCP token is the signing key, so rotating it revokes every link.
- An admin UI at `/admin`: the sessions the Grid is running, what each downloaded, clickable thumbnails, and the Grid's own console framed same-origin as a tab. The server's token is the whole credential.
- MCP Apps: `session_files` and `browser_sessions` declare UI components, so hosts implementing the extension (Claude, ChatGPT, VS Code, Goose) render a file grid instead of JSON. The components are shared with the admin UI rather than copied, and the tools stay visible to an app-capable client that would otherwise have them hidden as resource mirrors.
- The admin UI updates itself: the session list is pushed over Server-Sent Events when it changes, so there is no refresh button and no per-tab polling — one loop on the server serves every open page, with a slow poll kept only as a fallback if the stream is swallowed in transit.
- Sessions are shown with the name their caller claimed (`?session=<name>` or `X-Session-Key`), joined from the session store, and a session the Grid is running that this server has no record of is labelled as not its own rather than listed as though it were.
- Clicking a stored file opens it in place — images and PDFs in a lightbox rather than a new tab — and a session's detail view leads with a header of its context: name, owner, browser, node, start time.
- `PUBLIC_BASE_URL`, `GRID_CONSOLE_URL` and `APPS_ENABLED` configure the above.
- `wiki.yml` publishes the wiki: a pull request generates without pushing, a merge to main pushes, and `workflow_call` leaves the decision to the caller — the same shape as `image.yml`. `publish.yml` runs it alongside the image build rather than after, since the two share nothing.
- The GitHub wiki is a submodule at `wiki/`, holding the manual the README has no room for: installing per MCP client, deployment across stdio/HTTP/Docker/Kubernetes, sessions, administration, and one page per action generated from `openapi.yaml` by `scripts/generate_wiki.py` so they cannot drift.

### Fixed

- Switching browser no longer abandons the old one. `open_session` ends the browser this session is holding before opening its replacement, so `open_session(browser="firefox")` while on Chrome is now one call rather than a leak: previously the Chrome browser stayed on the Grid referenced by nothing, holding one of a handful of slots until the idle timeout. The tool description says so too, since an agent had no way to know it needed to close first.
- `image.yml` rebuilds when `static/` or `skills/` change. Both are mapped into the package by `package-dir`, so they ship in the wheel and therefore in the image — but the workflow only watched `kubed/**`, so an admin UI or skill change committed cleanly, passed CI, built nothing, and left the running container serving the previous version with no signal anywhere. `tests/test_packaging.py` now derives the pairing from `pyproject.toml` so it cannot drift again.

### Changed

- **BREAKING:** the `browser_sessions` tool and the `grid://sessions` resource are removed. An MCP client owns one session and may only ever see that one; a listing handed any client somebody else's browser id, which is the whole credential for driving that browser. The session list is now an admin view over HTTP, and `session://current` remains the sanctioned "what am I holding" shape.
- The admin session list shows flow sessions rather than every browser on the Grid, including ones with no browser attached. Browsers put on the Grid by something else are no longer listed at all — the Grid console tab is there for that.
- `SESSION_TTL` defaults to 24h rather than 1h: a flow session is the history the admin view shows and the context the next open inherits, not a short-lived cache.
- `openapi.yaml` is no longer committed — it is a generated artifact and is now gitignored. The server already built the same document per request at `GET /openapi.yaml`; the tests and the wiki generator now build it in-process too, so nothing reads a file that could be stale. `scripts/generate_openapi.py` still writes a copy when one is wanted, and CI writes one before linting it.
- The README hands the fourteen per-action reference tables to the wiki and links to them, so it advertises and shows the main features rather than duplicating a reference that is generated anyway — it had reached Docker Hub's 25,000-byte description limit, where the next feature would have shipped it truncated.
- The hand-written wiki prose moved from `wiki-notes/` in this repo to `wiki/notes/` inside the wiki submodule, named `<tool>.notes.md`. The suffix is load-bearing: a GitHub wiki addresses a page by basename whatever directory it sits in, so a bare `<tool>.md` would answer to the same URL as its own page.

- MCP server driving a Selenium Grid browser, with nine tools: open_session, navigate, click, write, press_key, extract, execute_script, screenshot, close_session.
- The same nine actions served as plain JSON endpoints under `/browser`, so non-MCP callers can drive the browser without speaking JSON-RPC.
- Screenshots return a real MCP image content block, so a vision model can see the page; viewport, single-element and full-page modes are all supported.
- Bearer token auth covering both surfaces, enabled by setting `MCP_AUTH_TOKEN`; `/health` stays open so a kubelet can probe it.
- `/health` reports Grid readiness and live session count, not just process liveness.
- Saved sessions: an MCP caller may omit `session_id` and the browser it used earlier is found again, keyed on a name the client chooses (`X-Session-Key` header, else `?session=<name>` on the MCP URL) or on the negotiated `Mcp-Session-Id`. The HTTP endpoints stay explicit — session in, session out — so a workflow owns its session.
- A remembered browser the Grid has already reaped is reopened on next use and returned to the page it was last on, so the refresh is invisible to the caller.
- `SESSION_STORE` (`memory` or `redis`) and `SESSION_TTL` configure where mappings are kept and for how long; both stores honour the TTL identically, and browser lifetime stays the Grid's job via `SE_NODE_SESSION_TIMEOUT`.
- Sessions are never keyed on FastMCP's `Context.session_id`, which returns a fresh `uuid4()` instead of failing when no session exists — that made every tool call look like a new client and leaked a Grid slot each time. The `Mcp-Session-Id` header is read directly instead.
- `session://current` resource reporting the browser this client holds, the page it is on, and whether the Grid still has it; reading it never opens one.
- The same status as a `current_session` tool for clients that cannot read MCP resources (n8n, for one), hidden from tools/list unless the client declares `?resources=off` or an `X-MCP-Resources: off` header.
- An Agent Skill shipped inside the wheel: `SKILL.md` is a thin index over six lazily-loaded references — the two session modes, reading pages, interaction, troubleshooting, and configuring the server — each served as its own resource so an agent reads only what its task needs.
- The skill uses FastMCP's own `SkillProvider` and URI convention, so `list_skills`, `get_skill_manifest` and `download_skill` work against this server unmodified; `SKILL_ENABLED=false` turns it off.
- `interact` replaces `click`, covering click, double_click, right_click, hover and scroll_to — hover reaches menus that appear only on mouse-over, which nothing else could.
- `upload_file` attaches a file to a file input, shipping the bytes to the Grid node the browser actually runs on. Content you already have as text goes straight in `text` with a `filename` — no encoding step; `content` takes base64 for binary, and the HTTP endpoint takes a normal `multipart/form-data` file part instead. The page reads a file's type from the filename extension, so `mime_type` supplies one when the name lacks it.
- `dialog` answers a native alert, confirm or prompt. `unhandledPromptBehavior` is now `ignore`, because Chrome's default silently clicks Cancel on a confirmation and destroys the evidence.
- `resize` changes the window on an already-open session, which `open_session` alone could not do for a caller whose browser was opened for it.
- An action that opens a dialog now succeeds and reports it rather than failing: reading the resulting url and title is refused while a dialog is open, and the click had in fact landed.
- Every wait now says what it was waiting for, for how long, and what URL the browser was on — Selenium raises timeouts with an empty message, which reached callers as the useless string "Message:".
- `open_session` is always required and never implicit: it is the only place a browser is created and the only place its window size and timeouts can be chosen, so opening one on first use hid the settings.
- The two session modes are exclusive and the advertised schemas say which you are in — `session_id` is absent from every tool in saved mode and required in stateless mode, so a model reads the rule instead of discovering it by failing a call. Using the wrong one errors and names the reference that explains it.
- An open dialog no longer looks like a dead session. The liveness probe asks for the session's URL, which a dialog blocks — so a `confirm()` made the server decide the browser was reaped, reopen, and abandon the real one with its dialog still up, leaking a Grid slot each time. Only the Grid saying `invalid session id` counts as gone now.
- `frame` moves a session into an iframe and back; frame contents are invisible to every locator otherwise. The switch is Grid-side session state and sticks until something switches back, so `session://current` reports `in_frame`.
- Window size and both timeouts cascade: env var, then URL parameter or header, then the `open_session` argument. A session's settings are stored and replayed when the Grid reaps it, so a refresh cannot silently change the browser's shape.
- `session://current` reports the `mode`, whether to pass `session_id`, the settings in force, whether the session is inside a frame, and a link to the reference that applies.
- `--stateless` / `STATELESS_HTTP` to drop MCP transport sessions, which is what more than one replica requires.
- `GET /openapi.yaml` and `/openapi.json` describing the HTTP surface, generated from the MCP tool schemas so the two contracts cannot drift; the spec is committed and CI fails if it goes stale.

### Fixed

- The published OpenAPI document omitted `session_id` from every request schema — the one field the HTTP surface always requires. It was built from a *tool listing*, which is shaped for whoever is asking, and outside a request the server identifies the caller as stdio and strips the field. It now builds from the registered tools, and the test that should have caught it no longer passes vacuously when the field is absent.

- Chrome silently refused every download after the first, because a page's second automatic download needs a permission nobody was there to grant. One file per session arrived and the rest vanished with no error.
