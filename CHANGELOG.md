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
- Saved sessions: an MCP caller may omit `session_id` and the browser from earlier in the conversation is found again, backed by pod memory or optionally Redis (`REDIS_*`, db 6). The HTTP endpoints stay explicit — session in, session out — so a workflow owns its session.
- `--stateless` / `STATELESS_HTTP` to drop MCP transport sessions, which is what more than one replica requires.
- `GET /openapi.yaml` and `/openapi.json` describing the HTTP surface, generated from the MCP tool schemas so the two contracts cannot drift; the spec is committed and CI fails if it goes stale.
