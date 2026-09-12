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

- **Saved flows.** Save a sequence of steps under a name, then run the whole thing in one call with `run_flow` — a twelve-step form becomes one call instead of twelve. Set `FLOW_DATA_DIR` to turn them on.
- **Kept files.** `keep_file` copies a download out of the browser so it survives being reaped, switched or ended; `session_files` lists both kinds together and keeps working after the browser has gone. Deleting a kept file is an operator action in the admin UI.

- **Flows take parameters.** Declare them and write `${name}` in any argument of any step — `url: ${site}/orders/${id}` — so one saved flow serves every account and every environment.

- **Type a secret you never see.** Give `write` a `secret` naming one instead of text, and the server reads it and types it — in a flow step or a single call. It is never passed as an argument string, so it cannot be assembled into one by mistake.

- **A secrets catalogue.** Point `SECRETS_DIRS` at a directory per secret and a file per key — the shape Kubernetes already mounts — and `list_secrets` shows an agent what it can use. Values are never returned by anything.

- **CSS selectors.** Every tool that acts on an element now takes `css` as well as `xpath` — pass one or the other.

- The built-in skill now teaches flows and secrets, so an agent finds and uses them without being told how.

- **The admin UI shows flows.** Every session's flows beside its files: read the steps, edit the YAML, move one to or from the shared `global` library, or delete it.
- **Files are one grid with a mark each.** A bubble is a download and a pin is a file kept beyond the browser — click the bubble to keep it, hover the pin to delete it. `Clear downloads` now lists exactly what it will remove and leaves kept files alone.

- `screenshot(save=true)` now tells you what the file was called, which is the name `keep_file` takes.

- **A flow run answers with the steps you marked `return: true`** — any number of them, and nothing else.

- **The wiki documents flows, secrets and kept files.** A reference page for each of the eight endpoints they added, plus guides for all three — and the two environment variables that switch them on.

### Fixed

- **A browser stays usable after logging in.** Chrome's "Save password?" prompt took the keyboard and mouse for itself, so every click and keystroke after a login silently did nothing — while every call still reported success.

### Changed

- **BREAKING:** the shared `global` flow library is read-only. Every session can list and run its flows; none can change them — including a caller with no session name, which used to save straight into it. Name your session with `?session=<name>` to get a library of your own; a stdio client gets one automatically.

- **HTTP errors now say whose fault they are**: `400` for a request you can fix, `404` for a browser that has ended, `503` for a Grid that is unreachable or full. They were all `500`.

- Error messages no longer carry the driver's stack trace.

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
