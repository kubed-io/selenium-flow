# AGENTS.md

Agent context for `selenium-flow`. Read this before working in this repo.

## Read first

This repo ships **an image and nothing else**. It does not deploy itself — unlike the
sibling `skills-mcp`, there is no `kustomization.yaml` and no `deploy/` here. The manifests
that run this live in the cluster repo at `apps/selenium/components/mcp`, and the image tag
that is actually deployed is pinned there.

That split is deliberate: the server is useless without a Grid, so it is deployed as a
component *of* the Selenium app rather than as a free-standing app.

## The loop (short version)

```
branch + PR ─► test.yml runs ruff + pytest        ← the gate
             + pr.yml fails if CHANGELOG [Unreleased] has no new entry
     │
   merge ───► image.yml pushes kubed/selenium-flow:main + :latest
     │
 publish  ──► Actions → 🧬 Publish Version → pick patch/minor/major
  (manual)   version-bump: roll changelog → tag vX.Y.Z → GitHub Release
     │       image.yml (called) pushes kubed/selenium-flow:vX.Y.Z
     │
  deploy ───► bump newTag in the CLUSTER repo's components/mcp, then `kubectl up`
```

Run Publish once with `push=false` first. It computes the version and builds the image
without pushing anything, which is the cheap way to find out the build is broken before a
tag exists. A failed build after a successful tag strands a tag on a nonexistent image.

## Rules

- **Every browser action is both a tool and an endpoint. One to one, no exceptions.**
  This is what lets a caller choose its style: an n8n workflow can hand the whole job to
  an agent over MCP, or drive the same actions itself with HTTP Request nodes when it wants
  exact control. A tool with no endpoint would take that choice away, because the workflow
  could not reproduce what the agent did. `tests/test_surfaces.py` asserts the two sets are
  equal — if it fails, add the missing half rather than editing the assertion.

  Two clarifications on the rule. Operational routes — `/health`, `/openapi.yaml` — are
  HTTP-only on purpose; they describe or monitor the server rather than doing anything to a
  browser, so they are not capabilities and get no tool. And a future high-level action
  (say a `login` that opens, types, types and clicks) is still an action: it gets both.

- **The surfaces may differ in return shape, never in capability.** `screenshot` returns an
  MCP image block to a tool caller and base64 JSON to an HTTP caller, because that is what
  each can actually use. That is the only sanctioned kind of divergence.

- **`actions.py` is the only place behaviour lives.** `tools.py` and `routes.py` are thin
  wrappers over it. Adding a capability to one surface and not the other is the failure
  this design exists to prevent, and `tests/test_surfaces.py` asserts they match — if that
  test fails, add the missing half rather than editing the assertion.
- **Type hints in `tools.py` are the tool schema.** FastMCP builds the JSON schema from the
  signature, so a missing or loose annotation is a worse tool, not a style nit. This is the
  whole reason the server exists: the n8n MCP trigger advertised every tool as a single
  opaque `input` string and silently dropped every argument.
- **Docstrings in `tools.py` are prompt.** They are read by a model choosing a tool, not by
  a developer reading source. Write them for that reader.
- **Coerce, don't trust.** Callers send JSON by hand, through form encoders and through LLM
  tool calls. `as_bool` exists because `bool("false")` is `True`; `as_int` exists because
  `int("")` raises and an omitted optional parameter often arrives as `""`. Both have
  already caught real failures.
- **Plain W3C WebDriver only.** No CDP. It is Chrome-only, which forfeits running against
  any other browser the Grid offers, and the CDP DevTools API is deprecated for removal in
  Selenium 5. CDP `Page.captureScreenshot` with `captureBeyondViewport` does produce a
  better full-page image, and `Emulation.setDeviceMetricsOverride` adds JPEG and a 2x
  retina render — both tested working against this Grid. If that capability is wanted, add
  it as a **separate** tool so the portable path keeps working when CDP goes away.

- **`openapi.yaml` is generated — never edit it by hand.** Run
  `python scripts/generate_openapi.py`. Request schemas are taken verbatim from the MCP
  tool schemas, which is what makes the REST contract and the tool contract provably the
  same thing rather than two things that agree today. The committed copy exists so the
  diff shows up in review and `redocly lint` can read it in CI.
- **Code generates the spec, not the other way round.** Spec-first was considered and
  rejected: FastMCP derives tool schemas from Python signatures, so a YAML source would
  mean generating Python and then deriving schemas from the generated Python. Nothing
  here is a contract another team designs against, which is when spec-first pays.

## Gotchas

- **`ReattachDriver` skips `start_session`.** That is the trick that lets this process bind
  to a browser it did not open. The cost is that `driver.caps` is empty, so anything
  reading capabilities — `execute_cdp_cmd` among them — raises `KeyError: 'browserName'`.
- **`write` reads the value back before submitting.** Submitting navigates, which makes the
  element reference stale.
- **The Grid's session timeout is not this repo's setting.** It comes from
  `SE_NODE_SESSION_TIMEOUT` on the Grid node, set in the cluster repo. A long-thinking
  agent will lose its browser mid-task if that is left at the 300s default.
- **`info.version` cannot be dropped from the committed spec.** It is required by
  OpenAPI, so the artifact carries a placeholder while the served document stamps the real
  package version. The validator test catches this if it is ever removed.
- **`@mcp.tool` returns the plain function**, not a Tool object, so a description cannot be
  patched after decoration. Pass `description=` to the decorator when it needs to be
  computed — `press_key` does this to interpolate the real key list.

## Commands

```bash
pip install -e ".[test]"
ruff check kubed
pytest

# a working system, server + Grid, auth off
docker compose up --build

# drive it against a real Grid without containers
GRID_URL=http://<hub>:4444 MCP_AUTH_TOKEN=dev python -m kubed.selenium_flow
```
