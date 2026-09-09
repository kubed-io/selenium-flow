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

## Sessions: what is stateful and what is not

Three different "sessions" are in play, and conflating them is the trap.

| | Lives in | Survives a restart |
|---|---|---|
| Browser session | Selenium Grid | yes |
| MCP transport session | this process's memory | no |
| `session_key` -> browser mapping | the session store (memory, or Redis) | only with Redis |

**The browser session is the one that matters, and this server does not hold it.**
`/browser/open` returns an id and the caller carries it. That is why a pod can restart,
scale to zero, or be replaced mid-workflow without losing a browser.

### Never key on `Context.session_id`

**`ctx.session_id` is a trap and this package must not use it.** It looks like the obvious
way to identify a caller, and FastMCP's own docs suggest it for exactly that. The problem
is its failure mode: when it cannot find a real session it returns `str(uuid4())` rather
than raising, so it *never* fails — it silently hands back a brand new identity on every
single request.

That shipped once. Every tool call looked like a first-time caller, opened a browser, and
leaked a Grid slot; the next call then timed out hunting for elements on `about:blank`.
The tell was the shape of the key: the server's real ids are undashed hex
(`131c43cc…`) while the ones in the log were dashed UUIDs (`bf532044-b55e-…`) — a value
FastMCP had invented, not one the transport negotiated.

`sessions.py` therefore reads the `Mcp-Session-Id` header itself, via
`get_http_request()`. That is a different code path from the one `ctx.session_id` uses and
it keeps working where that one gives up — verified against the deployed server. A missing
session then shows up as a missing session, which is the whole point.

### How a caller is identified

In order, first match wins, and nothing is ever invented:

1. **A name the client chose** — the `X-Session-Key` header, else `?session=<name>` on the
   MCP URL. **The header winning is a permission boundary, not a preference.** The header
   lives in the credential, which an admin controls; the query parameter is written by
   whoever wires up the call. An admin who pins a name in the credential is deliberately
   tying one session to one credential, so a caller must not be able to override it from
   the URL. Leaving the header out is equally a decision: it delegates the choice to
   whoever implements the call, who names each caller in its own URL against one shared
   bearer credential — rather than needing a multi-header credential per agent.
2. **The MCP transport session** — the `Mcp-Session-Id` header, read directly.
3. **stdio**, where one process serves one client, so a constant is correct.
4. **Otherwise no key**, and the caller is told to pass `session_id`.

Opening a browser on first use is safe *because* every accepted key is stable by
construction. That is the invariant to preserve: if a new key source is ever added, it must
be one the client controls, or the leak comes straight back.
`test_a_request_with_nothing_stable_has_no_key` and
`test_repeated_calls_on_one_key_open_exactly_one_browser` are the guards.

### Refresh, not cleanup

A stored mapping can name a browser the Grid has already reaped. `resolve` checks
`Grid.is_alive` and, if it is gone, reopens and navigates back to the record's last known
`url` — which is why the store holds a record rather than a bare id. Nothing in this
package runs a cleanup loop: the Grid expires idle browsers via `SE_NODE_SESSION_TIMEOUT`,
and the store expires mappings via its own TTL. Do not add a scheduler.

An explicitly passed `session_id` is taken on trust and never validated or replaced — the
caller owns it, and may well have opened it through the HTTP surface.

### The status resource, and why it is also a tool

`session://current` is the natural shape for "what browser am I holding" — state to read,
not an action, so a client can pull it into context without spending a tool call. It must
stay side-effect free: `describe()` peeks at the store rather than going through `resolve`,
because a status read that opens a browser would be the original leak wearing a hat.

Resources are the least implemented part of MCP, so the same status is a `current_session`
tool as well. That tool is **hidden from `tools/list` by default and still callable** — the
decision is made per request in `on_list_tools` middleware, because it depends on who is
asking, and one server object serves every client. Registering it conditionally at startup
would bake one client's capabilities into a shared process.

This is the general pattern for anything that has to vary by client: filter the listing,
keep the capability. `?resources=off` / `X-MCP-Resources: off` is how a client declares it.

## The two session modes are exclusive, and the schema says so

`open_session` is the only place a browser is created. It was briefly implicit —
`resolve` opened one on first use — and that was removed because it hid the one
place a session's settings can be chosen. Do not reintroduce it. A refresh after
the Grid reaps a session is the *only* other open, and it replays the stored
settings so the browser cannot change shape underneath a task.

`resolve` enforces one rule per mode:

- **stateless** (no caller key): `session_id` required.
- **saved** (a key): `session_id` **refused**. An id from elsewhere is either a
  mistake or a browser someone else owns. Sharing is by session *name*, which is
  then the single way to do it.

`resources.ShapeSessionId` rewrites the advertised schema per request to match —
absent in saved mode, required in stateless. It **copies** each tool with
`model_copy`; the registered tools are shared by every client, so mutating one
in place would let the first client to list tools decide what every other client
sees. `test_shaping_does_not_leak_between_clients` guards that.

## Frame switches are Grid-side and sticky

`switch_to.frame` changes the session's browsing context on the **Grid**, not in
this process, so it survives every reconnect and keeps applying until something
switches back — verified, since our architecture reconnects per call. That makes
a forgotten switch a nasty failure: locators on the main page fail for a reason
that looks nothing like the cause. `session://current` reports `in_frame` for
exactly that, detected with `window.self !== window.top` because WebDriver has
no "which frame am I in" command.

## Dialogs, and why the browser must never answer one

`unhandledPromptBehavior` is set to `ignore` in `Grid._options()`. Chrome's
default is "dismiss and notify", which **silently clicks Cancel** on a `confirm`
and then reports it as an error on whatever command happened to notice — so a
destructive prompt gets answered by accident and the dialog is gone before
anyone can decide. That is the worst available outcome and it is off deliberately.

The consequence is that a dialog stays open and blocks reading the URL or title.
So `browser.page_state()` catches that and reports the dialog *as* page state
rather than raising: the click landed, it just opened a prompt, and failing the
action would be a lie. Every action returns through that helper — if you add one,
use it rather than reading `current_url` directly.

## Waits explain themselves

Selenium raises `TimeoutException` with an **empty message**, which reaches a
caller as the string `"Message:"` and says nothing. Every wait goes through
`browser._waited`, which re-raises with what was being waited for, the timeout,
and the URL the browser was actually on — because "the browser is somewhere
unexpected" is the real diagnosis most of the time. This bit twice during
development before it was fixed; do not add a bare `WebDriverWait`.

## The embedded skill

`skills/selenium-flow/` sits at the **repo root** and is mapped into the package
by `[tool.setuptools.package-dir]`, so it reads as documentation but installs
inside the wheel. `skill_path()` checks the packaged location first and falls
back to the root, which is what makes a source checkout and an installed wheel
both work; a test asserts the mapping is still declared.

It is served by FastMCP's `SkillProvider` with `supporting_files="resources"`
rather than the default `"template"` — at half a dozen files the enumeration is
cheap, and it makes each reference an individually listed, linkable resource
instead of something a client can only reach by reading the manifest first.

Two rules keep it working:

- **The directory name is the skill name.** SkillProvider takes it from the
  folder, not the frontmatter, and publishes `skill://<folder>/SKILL.md`.
  Renaming the directory renames the skill and moves its URI;
  `test_the_directory_name_is_the_skill_name` pins the two together.
- **The URI shape is not ours to choose.** `fastmcp.utilities.skills.list_skills`
  discovers skills by scanning for resources ending in `/SKILL.md` under the
  `skill://` scheme, reads `<name>/_manifest` for the file list, then fetches
  each file. A prettier URI makes the skill invisible to every one of those
  helpers. Two tests do that discovery for real rather than asserting on the
  string.

Package data is easy to lose: `[tool.setuptools.package-data]` covers
`skills/**/*`, not just `*.md`, because a supporting file of another type would
otherwise be absent from the wheel with no error at build or import time.
`test_every_skill_file_is_covered_by_package_data` fails if a file is ever added
that no pattern matches.

**SKILL.md is an index, not the manual.** It carries the two facts that shape
everything, the session-mode branch every caller has to take, and a routing table
into `references/`. Detail belongs in a reference so an agent loads only what its
task needs. Three tests hold that line: every `references/...` path the index
names must exist, every file on disk must be linked from the index, and both
session modes must have a reference — an unlinked file is never lazily loaded, so
it may as well not ship.

Write for a model deciding what to do next, not for a developer reading reference
docs; the tool descriptions already say what each tool takes.

## Scaling: replicas > 1 requires --stateless

The `/browser` surface is replica-safe as it stands. The `/mcp` surface is **not** by
default: FastMCP keeps MCP sessions in process memory, so a client whose next request is
balanced to another pod is told its session does not exist.

`--stateless` / `STATELESS_HTTP=true` drops MCP sessions entirely - no `Mcp-Session-Id` is
issued and every request stands alone. Both surfaces are then replica-safe. The browser is
unaffected either way, because its session was never here.

So: `replicas: 1` needs nothing; more than one requires the flag, and Redis if `session_key`
is in use.

## Gotchas

- **Do not bake a deployment's conventions into this package.** `REDIS_DB` defaults to
  Redis's own `0`, not to whatever index some particular cluster happens to hand out.
  Safety comes from `REDIS_PREFIX`, which namespaces every key so a shared database is
  fine. The index is still passed to the client explicitly, so a `REDIS_URL` with no
  `/<index>` path does not override an operator's `REDIS_DB`.

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

## Session lifetime: who owns what

| | Who owns it | Default here |
|---|---|---|
| **How long a browser lives** | the Grid — `SE_NODE_SESSION_TIMEOUT` on the node | `300s` idle, in the cluster repo |
| **How long we remember a caller** | `SESSION_TTL` | `3600s`, slid forward on every call |
| **Where we remember it** | `SESSION_STORE` | `memory` (or `redis` to share it) |

**Nothing runs a cleanup loop, and nothing should** — the Grid expires idle browsers, the store expires its own keys. If the Grid reaped one we remembered, the next call notices and reopens it at the page it was last on. 🪄
