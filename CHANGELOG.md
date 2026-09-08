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
- `upload_file` attaches a file to a file input, shipping the bytes to the Grid node the browser actually runs on; the HTTP endpoint takes a normal `multipart/form-data` file, and base64 is there because MCP tool arguments must be JSON.
- `dialog` answers a native alert, confirm or prompt. `unhandledPromptBehavior` is now `ignore`, because Chrome's default silently clicks Cancel on a confirmation and destroys the evidence.
- `resize` changes the window on an already-open session, which `open_session` alone could not do for a caller whose browser was opened for it.
- An action that opens a dialog now succeeds and reports it rather than failing: reading the resulting url and title is refused while a dialog is open, and the click had in fact landed.
- Every wait now says what it was waiting for, for how long, and what URL the browser was on — Selenium raises timeouts with an empty message, which reached callers as the useless string "Message:".
- `--stateless` / `STATELESS_HTTP` to drop MCP transport sessions, which is what more than one replica requires.
- `GET /openapi.yaml` and `/openapi.json` describing the HTTP surface, generated from the MCP tool schemas so the two contracts cannot drift; the spec is committed and CI fails if it goes stale.
