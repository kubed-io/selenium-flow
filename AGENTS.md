# AGENTS.md

Agent context for `selenium-flow`. Read this before working in this repo.

## Read first

**The design record lives in [`saga/`](saga/).** This file is the operating
manual — the rules and invariants you must not break. The saga is *why* they are
what they are, plus the plan for what is being built next. Decisions there are
cited as `§F1.n` and `§F2.n` and are the reference for anything in this file that
says "see the saga".

Start with [Chapter 1 — The Flight Plan](saga/Chapter_1_The_Flight_Plan.md) if
you are picking up the **flows** feature: saved sequences of tool calls, run
server-side on one clearance. It closed with `v0.1.0`.

[Chapter 2 — Pilot Reports](saga/Chapter_2_Pilot_Reports.md) is still open for E17:
what the first agent flying a real app reported — discoverable capabilities, a
pointer that can glide, `drag`, `assert` steps that stop a run with instructions,
and `outline`.

[Chapter 3 — Other Aircraft](saga/Chapter_3_Other_Aircraft.md): what clients
other than Claude Code can reach — VS Code Copilot above all, whose model
cannot read resources — and why everything to read is named by URI, with two
tools that read one for the clients that cannot.

[Chapter 4 — The Hangar](saga/Chapter_4_The_Hangar.md) is the latest: Files,
Screenshots and Downloads as three sections instead of one merged pile,
`keep_file(uri)` and `upload_file(file=uri)`, and the admin UI's Files and
Flows tabs.


This repo ships **an image and nothing else**. It does not deploy itself — unlike the
sibling `skills-mcp`, there is no `kustomization.yaml` and no `deploy/` here. The manifests
that run this live in the cluster repo at `apps/selenium/components/mcp`, and the image tag
that is actually deployed is pinned there.

That split is deliberate: the server is useless without a Grid, so it is deployed as a
component *of* the Selenium app rather than as a free-standing app.

## The loop (short version)

```
branch + PR ─► test.yml    ruff + pytest on 3.14          ← the gate
             + quality.yml CodeQL, pip-audit, zizmor,
                           hadolint, OpenAPI lint
             + pr.yml      fails if CHANGELOG [Unreleased]
                           has no new entry
             + Copilot reviews against .github/copilot-instructions.md
     │       (the image is NOT built here — see image.yml for why)
     │
   merge ───► test.yml sweeps 3.10 → 3.14
             image.yml pushes kubed/selenium-flow:main + :latest
     │
 publish  ──► Actions → 🧬 Publish Version → pick patch/minor/major
  (manual)   test.yml (called) sweeps the full matrix first
             version-bump: roll changelog → tag vX.Y.Z
             image.yml   (called) pushes kubed/selenium-flow:vX.Y.Z
             package.yml (called) builds the sdist + wheel → artifact
             wiki.yml    (called) republishes the wiki
             release: downloads that artifact → GitHub Release
     │
  deploy ───► bump newTag in the CLUSTER repo's components/mcp, then `kubectl up`
```

`release` runs last and checks out nothing: it attaches the exact files
`package.yml` built and verified. So the image is on Docker Hub and the
distribution exists before the Release that points at them does.

Run Publish once with `push=false` first. It computes the version and builds the image
without pushing anything, which is the cheap way to find out the build is broken before a
tag exists. A failed build after a successful tag strands a tag on a nonexistent image.

## Rules

- **Every browser action is both a tool and an endpoint. One to one, no exceptions.**
  This is what lets a caller choose its style: an n8n workflow can hand the whole job to
  an agent over MCP, or drive the same actions itself with HTTP Request nodes when it wants
  exact control. A tool with no endpoint would take that choice away, because the workflow
  could not reproduce what the agent did. Both surfaces are mounted from one table,
  `CAPABILITIES` in `core/capabilities.py`: every row is a route, and `mcp/tools.py` refuses
  to start with a row it has no declaration for. `tests/test_surfaces.py` asserts the two
  sets are equal against its own hand list — if it fails, add the missing half rather than
  editing the assertion.

  Two clarifications on the rule. Operational routes — `/health`, `/openapi.yaml` — are
  HTTP-only on purpose; they describe or monitor the server rather than doing anything to a
  browser, so they are not capabilities and get no tool. And a future high-level action
  (say a `login` that opens, types, types and clicks) is still an action: it gets both.

- **The surfaces may differ in return shape, never in capability.** `screenshot` returns an
  MCP image block to a tool caller and base64 JSON to an HTTP caller, because that is what
  each can actually use. That is the only sanctioned kind of divergence.

- **`core/actions.py` is the only place behaviour lives** — with `core/recipe.py` (the road
  every action takes) and `core/capabilities.py` (the table both surfaces are mounted from)
  beside it. `mcp/tools.py` and `routes.py` are thin wrappers over them. Adding a capability to one surface and not the other is the failure
  this design exists to prevent, and `tests/test_surfaces.py` asserts they match — if that
  test fails, add the missing half rather than editing the assertion.
- **Every tool declares its MCP annotations, and they must be honest.** `core/annotations.py` builds
  them, and has no intra-package imports so every surface that registers a tool can use it
  without closing a cycle. A client reads `readOnlyHint` / `destructiveHint` to decide
  whether to ask the user before running a tool — ChatGPT skips the confirmation prompt
  for a read-only tool — so these are a promise, not decoration. An *unannotated* tool is
  not neutral: MCP's default is `destructiveHint: true`, so leaving them off makes a
  harmless tool look dangerous.

  `destructiveHint` is the one worth arguing about, and the rule is: **true wherever the
  tool hands the page an instruction the page is free to interpret** — `interact`,
  `press_key`, `dialog`, `execute_script`. A click is not destructive in itself and can
  place an order, and this server cannot tell which. Of the browser actions `extract` is
  the only read-only one; `screenshot` is not, because it writes a file, and a
  tool cannot be read-only only sometimes. `list_resources` and `read_resource` are reads
  too, and are the ones easiest to forget — they only appear for a client that cannot read
  resources, so a listing taken in the default mode proves nothing about them.
  `tests/test_surfaces.py` pins all of this, parametrised over both modes.

- **One place decides what a failure means: `errors.py`.** An HTTP status is a contract
  with a *machine* — `4xx` says "your request is wrong, sending it again will not help",
  `5xx` says "we are broken, retrying might". Everything below the auth check used to be a
  500, so an n8n node with Retry-On-Fail replayed mistyped XPaths and every alert on the
  5xx rate counted somebody's typo as an outage.

  A timeout is the case worth knowing: it is **400**, not 5xx, because every wait here is
  for an element, frame or dialog *the caller named*, and a page that never contained
  `//nope` will not contain it on the retry. A dead browser is **404** — the fix is
  specific and automatable, call `/browser/open`. An unreachable or full Grid is **503**.
  Anything unrecognised stays **500**, deliberately: "the caller's fault" is the dangerous
  guess about a failure we do not understand, because it tells a client to stop retrying
  something that may be ours. Add a new status only alongside the `responses` block in
  `spec/`, or the published contract starts lying.

- **One place decides whether a request is authorised: `http/auth.py`.** It is used by the
  action endpoints, the admin API and the event stream. The comparison is
  `hmac.compare_digest`, because these routes are reachable by anyone who can reach the
  port and `==` leaks the length of a correct prefix. There used to be two hand-rolled
  copies of this check, both using `==`. If a third door appears, it calls `http/auth.py`.

- **`http/auth.py` also builds `/mcp`'s verifier (`provider()`)**: the token alone, or
  `MultiAuth[token, OidcVerifier]`. A third credential goes there too.
- **The verifiers return a `PrincipalToken`; `Caller.principal` carries it. It decides nothing
  yet** — ownership, per-tool roles and the admin UI's OIDC sign-in are the next round's, and
  the spec lists them.
- **The REST routes, admin API and signed links are token-only on purpose**: the gateway only
  routes `/mcp`.
- **`oidc` without `auth.token` is a `ConfigError`**, checked after the layers merge
  (`config.oidc_problem`).
- **Behind agentgateway, `X-Session-Key` / `X-Workspace` cross and `?session=` does not.**
- **A JWT with an unknown `kid` makes `JWTVerifier` fetch the JWKS**, and FastMCP's bearer
  middleware runs on every path, so any request on a door that bypasses the gateway (the
  in-cluster Service, the ingress) can cause one. `OidcVerifier` floors that at one attempt
  per `JWKS_REFETCH_FLOOR` (60 s), failed attempts included. Misses during a fetch wait for
  it, and misses after it, inside the floor, get its body back rather than a refusal, even
  when the caller that started it was cancelled before caching anything: the fetch is
  shielded and kept, never cleared. FastMCP 4.1's own floor (`jwks_refresh_interval`) is
  passed as 0, so ours is the only one on every version: FastMCP's fetches unshielded
  under a lock, so a first caller cancelled mid-fetch takes the fetch with it, and
  everyone waiting is refused for the whole interval.

- **Type hints in `mcp/tools.py` are the tool schema.** FastMCP builds the JSON schema from the
  signature, so a missing or loose annotation is a worse tool, not a style nit. This is the
  whole reason the server exists: the n8n MCP trigger advertised every tool as a single
  opaque `input` string and silently dropped every argument.
- **Docstrings in `mcp/tools.py` are prompt.** They are read by a model choosing a tool, not by
  a developer reading source. Write them for that reader.
- **Coerce, don't trust.** Callers send JSON by hand, through form encoders and through LLM
  tool calls. `as_bool` exists because `bool("false")` is `True`; `as_int` exists because
  `int("")` raises and an omitted optional parameter often arrives as `""`. Both have
  already caught real failures.
- **Plain W3C WebDriver only.** No CDP. It is Chrome-only, which forfeits running against
  any other browser the Grid offers, and the CDP DevTools API is deprecated for removal in
  Selenium 5. WebDriver BiDi is W3C and allowed, used **only** for site data, and reattached
  per call through `Grid.bidi(session_id)`, which closes its socket on exit. CDP `Page.captureScreenshot` with `captureBeyondViewport` does produce a
  better full-page image, and `Emulation.setDeviceMetricsOverride` adds JPEG and a 2x
  retina render — both tested working against this Grid. If that capability is wanted, add
  it as a **separate** tool so the portable path keeps working when CDP goes away.

## The five stages every call takes

Every capability is one call that takes the same road, and each stage has one
owner. A host — the MCP surface, the HTTP surface, a flow run — drives the
stages; none of them writes its own copy.

| Stage | What it is | Where it lives |
|---|---|---|
| **Caller** | who is asking: name, library, client, declared flags, read once at the edge and handed in | `Caller` in `session/sessions.py`; the readers in `mcp/clients.py` and `http/` |
| **Capability** | what is being asked: name, arguments, route, annotations, the action | the `CAPABILITIES` table in `core/capabilities.py`, the actions in `core/actions.py`, annotations in `core/annotations.py` |
| **Recipe** | how the browser does it: check, reattach, navigate, wait, act, report the page, under the session lock | `Recipe.run` in `core/recipe.py` |
| **Settle** | what it leaves behind: one record write (history, capture, the reopen report) | `SessionManager.settle` in `session/sessions.py` |
| **Surface** | how it is answered: an MCP result, an HTTP response, a flow step's entry | `mcp/tools.py`, `routes.py` with `http/answer.py`, `flows/engine.py` |

The layering is enforced, not remembered: `tests/test_boundaries.py` fails when
`core/`, `session/`, `flows/` or `site_data/` imports the protocol layers
(`mcp/`, `http/`, `routes`, `server`, `spec`), and when the modules that are
meant to be plain import `selenium`. Shared pure rules live in their own small
modules — `names.py`, `urls.py`, `binding.py`, `faults.py`, `core/coerce.py` —
so a layer that needs one does not import a layer above it.

## Integration tests: less is more

`tests/integration/` runs the real server and a real browser against the
server's own admin page. **A test there is a flow file in `flows/` and nothing
else** — no new test functions, no helpers, no fixtures for one case.

This is the rule most likely to be broken by an agent, so it is stated bluntly:
**do not add integration tests to be thorough.** Add a flow only when you can
name what a person would see break that no existing flow catches. Everything
else is a unit test. Only exercise tools the admin UI actually needs. Two flows
is the right size; ten is a mistake. See `CONTRIBUTING.md`.

## The changelog is the release notes, not a work log

`publish.yml` hands the `[Unreleased]` section to `duplocloud/version-bump`, which stamps a
version heading on it and puts it straight into the GitHub Release. There is no editing
pass between what you write and what a stranger reads.

So: **one short line per entry, saying what someone can now do.** Not a paragraph, not the
reasoning, not what it replaced — the reasoning belongs here in `AGENTS.md`. Agents get
this wrong in one direction every time, by explaining. **Internal work earns no line at
all**: a CI change, a refactor, a dependency bump or a test pass takes the `no changelog`
label unless a user would actually notice something, in which case it gets one terse line.

Only ever edit `[Unreleased]`; the version sections below it shipped and are immutable.
Never write a version heading or bump a version — `setuptools_scm` reads them from git
tags and the release flow owns them. Full rules in
[CONTRIBUTING.md](CONTRIBUTING.md#the-changelog).

## The OpenAPI spec is a build artifact

**`openapi.yaml` is generated and not committed.** It is in `.gitignore`. Write it with
`python scripts/generate_openapi.py` when you want a copy to lint or publish; nothing in
the repo needs it to exist.

**Half of the document is derived and half is written**, and knowing which is which is the
part that matters:

| Part of the document | Where it comes from |
|---|---|
| **request** schemas | the MCP tool schemas verbatim, which FastMCP derives from the signatures in `mcp/tools.py` |
| **response** shapes | each capability's `response` column in `core/capabilities.py`, by hand — the actions return plain dicts, so there is nothing to introspect |
| info, servers, tags, `/health`, error shape | `spec/schemas.py`, by hand |

So adding a *parameter* to a tool updates the spec on its own; adding a *return field* does
not, and `test_every_action_declares_a_response_shape` is what stops that being forgotten.
There is **no** transform any more. Three used to be applied, all consequences of
the HTTP surface having no session of its own; §F2.12 and §F2.13 removed the
cause, so a request body here is exactly the tool's schema. What this document
adds that no tool schema carries is the session itself, as the header and query
parameter every operation takes.

**`spec/` is where a change goes**, always. Both renderings come out of `build_spec`:

| | Where | Built |
|---|---|---|
| **served** | `GET /openapi.yaml`, `GET /openapi.json` | per request, from the live tool schemas |
| **artifact** | `openapi.yaml`, gitignored | on demand by `scripts/generate_openapi.py`, and by `test.yml` before it lints |

Nothing reads the artifact as an input. `scripts/generate_wiki.py` builds the spec
in-process for the same reason the tests do — a generator that consumed a checked-in file
could render from a stale one, and could not run until something else had written it.

**Code generates the spec, not the other way round.** Spec-first was considered and
rejected: FastMCP derives tool schemas from Python signatures, so a YAML source would mean
generating Python and then deriving schemas from the generated Python. Nothing here is a
contract another team designs against, which is when spec-first pays.

**`info.version` is a placeholder in the artifact.** OpenAPI requires the field, and
stamping the real one would make the file differ per checkout since setuptools_scm derives
it from git. The served document carries the true version.

## The wiki is generated from the spec

`python scripts/generate_wiki.py` renders one page per action from the spec it builds
in-process, so the wiki is downstream of the code and cannot describe a server that no
longer exists.
`tests/test_wiki.py` fails when the committed pages do not match the generator.

Hand-written prose lives in `wiki/notes/<tool>.notes.md` — inside the wiki submodule — and
is folded into the bottom of that tool's page, below the generated tables. Regeneration
leaves it alone, so it is where guidance a schema cannot express belongs.

**The `.notes.md` suffix is load-bearing.** A GitHub wiki addresses a page by *basename*
whatever directory it sits in, so a note named `<tool>.md` answers to the same URL as its
own page — GitHub then served the fragment, and the page looked like it had lost
everything but its prose while the file on disk was perfect. That is why `notes/` was
deleted from the wiki once already. `test_no_page_is_shadowed_by_a_file_in_a_subdirectory`
fails if a bare `<tool>.md` comes back.

## Configuration

`kubed/selenium_flow/config.py` is the schema: one setting, three spellings. A
setting's path **is** its name — `redis.host` is `REDIS_HOST` in the
environment and `--redis-host` on the command line — so the other two are
derived mechanically rather than declared by hand. Three constraints follow:
a section name has no underscore (the first `_` in an env name splits section
from key), a section name is never itself an env var something else sets
(`browser` is out — the shell's `BROWSER` — and so is `selenium`, because
`SELENIUM_FLOW_PORT` and friends sit too close; `mcp` is allowed, since the
env layer keeps only known leaves, so an unrelated `MCP_KB_PORT` reads as
`mcp.kb_port` and is dropped rather than mistaken for a setting), and env
names match case-insensitively.

**`os.environ` is read in exactly two modules**: `config.py`, at load, and
`secrets.py`, for an `{env: …}` key at bind time. `test_the_environment_is_read_in_two_modules_only`
holds that — everything else takes a `Settings` or a `Catalogue`, never the
process environment directly.

**Env is lenient, the file is strict.** Kubernetes injects a `<SERVICE>_PORT`
variable for every Service in the namespace, so an unknown env name is
dropped rather than refused. An unknown key in the config file stops the
boot, because the file is not shared with anything else and a typo there is
just a typo.

`_Environment` overrides a pydantic-settings *private* hook, `_load_env_vars`,
to keep this filtering — so a pydantic-settings upgrade changing that hook's
shape is a real risk. `tests/test_config_load.py` is what catches it.

**The Settings tab shows; it does not explain.** Names, defaults and allowed
values are the wiki's job — the tab is a read-only view of what the server is
actually running with (Dr K, 2026-09-26).

## A client owns one session, and only its own

This is a rule, not a preference.

- **A client gets `open_session` and `end_browser`. That is its session.**
- **It can only ever control its own.** Nothing on the MCP surface enumerates
  sessions, because a listing hands any client somebody else's session — and a
  session name is now the entire credential for driving that browser.
- **An orphan is invisible to it.** A client whose browser went simply has none,
  and calls `open_session`. There is no "reclaim", no "take over".
- **The admin surface is HTTP endpoints and the UI, never tools or resources.**
  It is the only thing that sees across sessions, and it is gated on the server
  token rather than on being an MCP client at all.
- **Everything is ephemeral.** Stale entries are ignored and silently cleaned;
  nothing needs an operator to tidy up.

`grid://sessions` and the `browser_sessions` tool existed and were removed for
exactly this reason. `test_the_mcp_surface_never_lists_other_sessions` is the
guard — if you find yourself adding a tool that returns more than one session,
that test is the design telling you no.

`session://current` is the sanctioned shape: this caller's session, and nothing
else in the process.

## A session is the thing; a browser is something it holds

The two used to be one, and the split is what most of the session code is about.

A **session** is a record in the store, keyed by **the name its caller chose**:
the browser choice and window it was opened with, the page it was last on, and
— when it has one — the id of a browser on the Grid.

Detached is an **ordinary state**, not a broken one. It happens when the Grid
reaps an idle browser, or an admin ends one. What survives is the context, and
that is the point:

- `resolve` reports a detached session exactly like an absent one, so the agent
  takes the branch it already knows: call `open_session`.
- `open_session` with no arguments inherits that context — same browser, same
  window, back to the page it was on. The settings cascade is where this lives:
  env < client default < **this session's last values** < explicit argument.
- Ending a browser from the admin UI therefore costs the caller nothing but the
  browser's live state. It never removes the session.

**A session is only ever removed by expiring.** `SESSION_TTL` slides on every
use, so one in daily use never goes and one abandoned yesterday does. There is
deliberately no delete button: nothing should be permanently lost by a misclick,
and the store is a cache of intent, not a system of record. A name reappears the
moment its caller calls again, because the name comes from the caller's own URL
or header rather than from anything stored.

**Both surfaces are the same session.** `routes.py` resolves a caller through
the same `SessionManager` the tools use, so a browser opened over HTTP is in the
admin list, slides its TTL, and is reopened after a reap exactly like one opened
over MCP. That was not true before §F2.13 and the difference was invisible until
a workflow's session expired underneath it.

**One browser-driving call at a time per session** (`session/locks.py`). The
record was always safe — every write is a compare-and-set — but two action
sequences interleaved on one browser are not, so `Recipe.run` holds the
session's lock from the reconnect to the page state and a second call waits its
turn: no 409, no timeout of its own. A bound write holds it from the page read
through the keystrokes, so nothing navigates or switches frames between the
leash check and the typing; a flow holds it per step, never for the run, and
the lock is first come, first served, so a call that asks during a step runs
before the next one. `end_browser` and `open_session` take no lock, and nothing
that only reads does: `end_browser` sets the cancel the holder watches
(`core/cancel.py`), so a long `assert` lets go instead of making the call that
exists to stop it wait — a direct call with `cancel.Ended` ("the browser was
ended while this call was waiting", a 404 like any dead browser), a flow with
its own cancellation. Keyed by the browser's Grid id, reentrant, fair, weak,
and process-local — one replica (see "Scaling").

A waiting call holds one of the 40 worker threads that FastMCP's sync tools and
Starlette's routes share (anyio's default limiter), and it does not notice its
client giving up: about 40 calls queued on one session would stall every other
session, `end_browser` and `/ready`. A limiter of its own for `end_browser` is
the fix when that becomes real.

## Sessions: what is stateful and what is not

Three different "sessions" are in play, and conflating them is the trap.

| | Lives in | Survives a restart |
|---|---|---|
| Browser session | Selenium Grid | yes |
| MCP transport session | this process's memory | no |
| name -> browser mapping | the session store (memory, or Redis) | only with Redis |

**The browser session is the one that matters, and this server does not hold it.**
It lives on the Grid, and the record naming it lives in the store, so a pod can
restart, scale to zero, or be replaced mid-workflow without losing a browser.

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

`mcp/clients.py` therefore reads what the request carries itself, via
`get_http_request()`, into a `Caller` (`session/sessions.py`) that the edge
hands in. A missing name then shows up as a missing name, which is
the whole point — and under §F2.12 it is an error with a message rather than a
silent new identity.

### How a caller is identified

**The caller names itself, and nothing is ever invented** (§F2.12):

1. **`X-Session-Key`**, a header — what an admin pins inside a credential when
   one credential should mean one session. **`X-Workspace`** is the same header
   by another name, for clients that may only send approved headers (Claude.ai
   custom connectors): both with one value are one name, two values are refused
   like a repeated `X-Session-Key`, and either reports `named_by: header`.
2. **`?session=<name>`** on the URL — the ergonomic path: one shared bearer
   credential, each caller naming itself in its own URL.
3. **stdio**, where one process serves one client, so the constant `stdio` is
   correct and needs no configuration.

**A header and `?session=` at once is a 400.** It used to be a precedence — the header won, on the
reasoning that an admin's credential outranks a caller's URL — and Dr K replaced
that with a refusal: a request carrying two names has two ideas about who is
calling, and quietly picking one hides that from whoever wired it up.

**No name at all is a 400** on anything touching a browser, with a message
saying how to set one. The single exception is the flow library, which falls
back to the shared `global` one — readable by everyone, writable by nobody, so
an unnamed caller can list and run shared flows and can write nowhere.

**The name is validated where it arrives**, by the same rule that validates a
flow library's directory, because a session name *is* that directory. There is
no longer a lenient answer for the browser and a strict one for storage: that
split is what let `?session=my bot` drive a private browser while saving its
flows into the shared library.

If a new way to supply a name is ever added, it must be one the client controls,
or the leak `caller_key` existed to prevent comes straight back.
`test_a_request_that_names_nothing_names_nothing` and
`test_repeated_calls_on_one_key_open_exactly_one_browser` are the guards.

### Site data

- Saved **explicitly** by `save_site_data`, never captured per call. A save is a **snapshot**
  that replaces the last whole (`site_data.snapshot`): the jar, the localStorage of every origin
  in the record's `history`, and the page's sessionStorage. An origin that cannot be read keeps
  its last storage; over 1 MB the oldest-visited origins go first.
- `SessionRecord.history` is where the session has been: one entry per origin, newest first,
  written by `touch` (a flow run: once at the end, every step's page in order — and, before a
  `save_site_data` step, the pages reached so far, so the save reads them). `record.url` is
  `history[0].url`. A withheld URL (§F1.24) or a page with no origin records nothing. Only pages
  a call ended on are recorded; entries older than the session TTL go (the top one stays), at
  most `HISTORY_CAP` (100), so a save never reads an origin that has aged out.
  Only the top entry keeps its whole URL; below it a URL keeps its origin and path, because a
  query string or fragment carries OAuth codes and reset tokens.
- Other origins are reached through `spare.spare_tab`: a background tab whose requests a BiDi
  intercept answers with a marked blank page, so the site never loads. A save reads there; a
  restore writes there, then sets sessionStorage in the main tab the same way, all before the
  first page. A page without the marker is a service worker's and is never read. No CDP.
- Kept on `SessionRecord` in the store, never on this server's disk (with the Redis store it is
  as durable as Redis), never logged — `main.py` holds Selenium's wire loggers at INFO for that;
  it expires with the record. httpOnly values are shown as `•••` on every surface; a secret a
  site keeps in its localStorage is saved and shown as it is.
- An action returns the capture under `CAPTURED`; `SessionManager.settle` stores it and strips
  it, so it is never returned. Every record write after an action goes through `settle`:
  `act`, every flow step and the run's pages, and a bound write (its URL withheld when tainted).
- A silent reopen's report waits on `SessionRecord.reopened` until `touch` hands it to the first
  result from that browser — for a flow, to the run.
- The admin tabs keep the two apart: History (the history joined by host with secrets and the
  snapshot's counts) and Site data (the snapshot). Forget and both Clears change the store
  only — never the live browser, never the other tab's data.
- Every record write goes through `store.update(key, fn)`, which applies `fn` to the record as
  it is at write time (memory: a per-key lock; Redis: `WATCH`/`MULTI`/`EXEC`, retried, then
  `StoreConflict`). A whole record read before slow work and `set` after reverts a browser
  opened or a save made meanwhile. Do slow work outside `fn`; `fn` only computes, and may run
  twice. What a write found comes back through `store.change`: `fn` returns the record and
  a note, and only the last run's note is returned — never a dict `fn` fills on the side.

### Refresh, not cleanup

A stored mapping can name a browser the Grid has already reaped. `resolve` checks
`Grid.is_alive` and, if it is gone, reopens and navigates back to the record's last known
`url` — which is why the store holds a record rather than a bare id. Nothing in this
package runs a cleanup loop: the Grid expires idle browsers via `SE_NODE_SESSION_TIMEOUT`,
and the store expires mappings via its own TTL. Do not add a scheduler.

**One bounded wait is allowed:** a task that waits for something this server was
told to expect, and ends when nothing is owed — the admin broadcast (while a page
listens) and the recordings collector (while a recording is owed). A loop that
tidies is still not.

### Recordings: Selenium records, the operator delivers, we file

- A file is matched by the owed Grid id anywhere in its path below the inbox; one
  path naming two owed ids is skipped. The recorder must keep `SE_VIDEO_FILE_NAME=auto`
  and `SE_VIDEO_FILE_NAME_SUFFIX=true` or `SE_VIDEO_SESSION_SUBFOLDER=true`, or no id is there.
- The inbox (`recording.dir`) is the operator's; we never clean it. A file no
  note claims stays where it is; one a `discard` note claims (a recorded browser
  quit after losing a race to bind) is deleted, never filed.
- The queue is the notes under `sessions/<name>/recordings/.pending/<gridId>.json`.
  They are on disk, so they survive a restart whatever the session store is.
- **A note leaves the queue only once it is gone from disk.** Filing (or the
  deadline) first marks the note `filed` / `dropped`, then deletes it; a delete
  that fails keeps it owed but done, and every sweep (and the next process)
  retries the delete alone, never matching or filing it again. A filing's move,
  mark and delete are one shielded task `stop` waits for, so a rolling deploy
  mid-copy finishes it; only a hard crash between the move and the mark leaves
  an unmarked note the next process can file again.
- A storage error reading the notes is a fault, not a broken note, and it is a
  session's: `notes(on_error=)` reads every other session on (only the data
  directory itself unreadable raises), walking the root with `lstat` rather
  than `sessions()`, whose `is_dir` reads an unreadable folder as none. The
  collector owes what it read, logs the rest once a streak and reads again each
  tick, holding the notes lock across the read and the merge; the boot carries
  on. Only bad JSON, a bad id or a non-file is skipped.
- Matching is never by session name (the recorder strips `.`). The Grid id stays on disk, in the note only.
- A file is complete when it ends in `mfro`; one cut off (no `mfro`, unchanged
  60 s, browser gone) is filed as it is.
- **The collector never sends the Grid a session command.** It learns whether a
  recorded browser still runs from one `GET /status` listing per tick
  (`Grid.sessions()`): a WebDriver command such as `GET /session/{id}/url` counts
  as activity and would stop the Grid ever reaping the browser. A listing taken
  before a browser opened cannot mark it gone (a cached listing older than the
  note's `opened` is ignored). A failed sweep is retried a tick later, logged once
  per failure streak.
- Recordings follow the screenshot lifecycle: kept into Files or cleared.
- `record` is never inherited by an explicit open, but a reap replays it, and
  only while `recording.enabled`; with recording off it is dropped from the reopen.
- Layout: `DATA_DIR/sessions/<name>/{flows,files,screenshots,recordings}`, and
  the inbox `DATA_DIR/recordings/` by default, never in or above `sessions/`
  (refused at boot). `data` and `recording` are config sections;
  `FLOW_DATA_DIR` is the one retired name refused at boot.

### Everything to read is a resource, named by its URI

`session://current` is the natural shape for "what browser am I holding" — state to read,
not an action, so a client can pull it into context without spending a tool call. It must
stay side-effect free: `describe()` peeks at the store rather than going through `resolve`,
because a status read that opens a browser would be the original leak wearing a hat. It
reports the session **name** and never the Grid's id.

The same goes for the skill, the flow library, Files, Screenshots, Downloads and
the secrets catalogue: each is a resource, and **every piece of text an agent
reads names it by URI** — a hint, an
error, a prompt, SKILL.md. A client whose model cannot read resources gets two tools that
take those URIs, `list_resources` and `read_resource` (`mcp/mirror.py`), never a tool per
resource (§F3.6). Do not add one: a name the text has to use instead of the URI is the
second vocabulary that was removed.

Who counts as unable is decided in `mcp/clients.py`: `?resources=` / `X-MCP-Resources`
when given, else the client's own `clientInfo.name` against a table of measured clients,
else yes (§F3.1). The listing is filtered per request, in middleware, and the tools stay
callable — one server object serves every client, so a decision about who is asking can
only be made when someone asks. The handshake's instructions vary the same way (§F3.2).

## One contract, and no id in it

`open_session` is the only place a browser is created. It was briefly implicit —
`resolve` opened one on first use — and that was removed because it hid the one
place a session's settings can be chosen. Do not reintroduce it. A refresh after
the Grid reaps a session is the *only* other open, and it replays the stored
settings so the browser cannot change shape underneath a task.

There used to be two modes — **saved**, where the server held your browser, and
**stateless**, where you passed an id — with a middleware rewriting every tool
schema per request so a caller could see which rules applied. All of it is gone
(§F2.12). There is no `session_id` on any tool, in any body, or in any result;
`SAVED_SESSIONS` is gone. **Sharing is by
session name**, which is then the single way to do it — and worth writing down,
because a name is guarded by nothing but the bearer token.

## The HTTP surface is REST, and the session is not in the path

Paths and methods are **declared** per capability — the `route` and
`http_method` columns of its row in `core/capabilities.py` — never derived from
tool names (§F2.13). What the two surfaces share is bodies and
results — a request body *is* the tool's schema, asserted by
`test_request_schemas_are_the_tool_schemas` — not shape.

The session is who is calling, so it is a header or a query parameter and never
a path segment. `POST /browser` opens *yours*; there is no `/browser/{id}`,
because addressing a browser by id is exactly what E18 removed.

**The one place a session is in a path is `/admin`**, and that is the same rule
from the other side: the token holder looking across sessions is the only role
that addresses them as resources.

## `ROUTE_PREFIX` mounts the whole server

`routes.mount()` turns it into a path segment every tree hangs off: `""` for the
root, and `/` means the same thing because that is what an operator types when
they mean "no prefix". It used to rename `/browser` while `/mcp`, `/admin` and
`/files` stayed fixed, which was backwards — nobody wants the browser endpoints
called something else, and everybody eventually wants the server under a path
(§F1.11).

So `prefix` means **the mount** in every registrar that takes one, and each tree
is fixed beneath it. If you find yourself passing `f"{prefix}/flows"` into
`flowapi.register`, that is the old meaning coming back.

**The UI is the mount root** and `/admin/*` is the API it calls — two different
things that shared a name. Its views live after the hash, because every other
path under the mount is a real endpoint and a history route would need a
catch-all that answered mistyped API calls with HTML.

**Four ops endpoints, one question each**: `/health` is liveness and says
nothing about the Grid, `/started` is startup, `/ready` is readiness and is the
one that dials the Grid, `/info` is what an operator asks when a call went
somewhere unexpected. All four answer at the root **as well as** under the
mount, because a kubelet did not choose the mount. `/openapi.json` was being
used as a liveness probe in this cluster before they existed; it is not one.

A URL this server hands out is a **server path with the mount in it**, signed
over the **unprefixed** path — the route knows where it is mounted, and the
signature has to mean the same thing on both sides of the wire. With
`PUBLIC_BASE_URL` set, where the server's own root is reachable, an MCP or HTTP
result's `url` has it in front; there is no second field. The admin API passes
no base, so the page gets paths and resolves them against `ROOT`, what an
ingress stripped, never its own path.

**A file opened from a link is sandboxed unless it is a raster image.**
Everything else a site made — HTML, SVG, anything unknown — gets
`Content-Security-Policy: sandbox`, so its scripts cannot reach the admin
token on this origin. Images get `default-src 'none'` and no sandbox, and
this is not an oversight to tidy away: the sandbox's opaque origin is felt by
every extension in the tab, and one reading `localStorage` threw on each
render until screenshot links opened black and hung. A PDF gets neither,
since Chrome's viewer does not load under a sandbox. Video and audio are the other unsandboxed case: they get
`default-src 'none'; media-src 'self'; style-src 'unsafe-inline'`, because a
sandboxed top-level media document does not play. Files on our disk (kept files,
screenshots, recordings) stream with HTTP Range so a video can seek. A link that does not open
is a page saying why for a browser and JSON for anything else, and
`link_ttl` sets how long one lasts.

## A selector is one object

`xpath` and `css` are fields of a `selector`, not two flat arguments (§F2.14):
one choice, one description, one rule. `drag` takes `selector` and `to`, which
is the same type rather than a `to_` prefix doing a type's work.

**Exactly one of the two, and never a fallback.** A typo in the first would
become a click on whatever the second found, and the run would report `ok` while
it drifted. `browser.locator` refuses both-or-neither at the boundary and the
model refuses it in the schema; `flowdoc` refuses it at save time, because a
flow is YAML somebody may have written by hand.

## Frame switches are Grid-side and sticky

`switch_to.frame` changes the session's browsing context on the **Grid**, not in
this process, so it survives every reconnect and keeps applying until something
switches back — verified, since our architecture reconnects per call. That makes
a forgotten switch a nasty failure: locators on the main page fail for a reason
that looks nothing like the cause. `session://current` reports `in_frame` for
exactly that, detected with `window !== window.top` because WebDriver has no
"which frame am I in" command. Never `window.self`: a page can run `self = top`
and forge it. A secret's leash does not ask whether it is in a frame at all: it
always reads `document.location.origin` of the selected context, because a
keystroke reaches the frame and WebDriver's url is always the top page's, and
**both** the top page and that origin must be allowed. Out of a frame they are
the same origin; in one, the frame only tightens the leash. A bound write also
refuses any element that can host a nested document (`iframe`, `frame`, `object`,
`embed`, `fencedframe`, `portal`) as its target: keys sent to one can land in that
document whatever context is selected, an origin the leash never read.

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

## Clicks wait for navigation. Keystrokes do not.

`element.click()` and `driver.get()` are *specified* to wait for a navigation they
cause. `send_keys` carries no such promise: the command returns while the browser is
still on the old page. So `write(submit=True)` and `press_key("enter")` used to report
the page they had just submitted **from**, and could tear — the old URL beside the new
document's not-yet-set title.

Measured against this Grid before it was fixed: `write(submit=True)` reported the
pre-submit page on **6 of 6** Firefox runs and 2 of 6 Chrome runs, while `interact` and
`navigate` were correct 12 of 12 and 3 of 3. It is a race, not a browser quirk — Chrome
merely wins it more often, which is the worst way for a bug like this to behave.

`browser.settled()` is the fix: after a submitting keystroke, wait for the anchor
element to go stale, then for the new document to stop parsing. Two rules keep it
honest:

- **Expiring is a normal outcome, not an error.** A form handled in JavaScript never
  navigates and never goes stale, so the timeout is short (`NAVIGATION_SETTLE`) and a
  timeout means "nothing navigated", which is a true answer.
- **Only keys that can submit pay for it** — `SUBMIT_KEYS`, which is Enter and Return.
  Tab, Escape and the arrows navigate nowhere, and making every one of them wait to
  discover that would tax the common case for nothing.

If you add an action that navigates by any means other than a click or `driver.get`, it
needs this too.

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

The **Skills extension** (SEP-2640, `io.modelcontextprotocol/skills`) is a second
discovery surface over the same files, in `mcp/skill_extension.py`: `skills/list`
and `skills/get` return one entry naming every file with the sha256 and size of
the bytes `resources/read` serves. Those are not `_manifest`'s hashes, which are of
the disk, and `SkillProvider` reads text with newline normalisation. FastMCP
declares the extension in `server/discover` only, so `AdvertiseSkills` puts it back
into a legacy `initialize`. A SKILL.md that fails the spec (name ≠ folder,
description outside 1–1024 characters, frontmatter that is not representable as
JSON) is still served as resources but not offered. agentgateway 1.6 refuses both methods
(`unsupported method`, agentgateway#3579), so through the gateway a client sees the
declaration and gets an error; in-cluster they work. Delete the module when
FastMCP ships its own (#5016).

**SKILL.md is an index, not the manual.** It carries the two facts that shape
everything, the session-mode branch every caller has to take, and a routing table
into `references/`. Detail belongs in a reference so an agent loads only what its
task needs. Three tests hold that line: every `references/...` path the index
names must exist, every file on disk must be linked from the index, and both
session modes must have a reference — an unlinked file is never lazily loaded, so
it may as well not ship.

Write for a model deciding what to do next, not for a developer reading reference
docs; the tool descriptions already say what each tool takes.

## Scaling: one replica

The `/mcp` surface keeps MCP transport sessions in process memory, and relies on
them: a client's `initialize` — its name and capabilities, which decide what it
is shown (§F3.1) — is remembered there. A second replica would answer a client
it never met. There used to be a `--stateless` flag trading that away; it was
removed rather than kept for a scaling nobody runs.

The browser is unaffected: its session lives on the Grid, and the record naming
it lives in the session store. Use Redis for that record if the pod should come
back from a restart holding its callers' browsers.

The per-session lock (`session/locks.py`) is process memory too, with Redis or
without: a second replica would let two calls drive one browser at once.

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
GRID_URL=http://<hub>:4444 AUTH_TOKEN=dev python -m kubed.selenium_flow
```

The admin UI and MCP App live in `ui/` — Svelte, built by npm. `npm --prefix ui
ci && npm --prefix ui run build` writes it into
`kubed/selenium_flow/http/static/`; `npm --prefix ui run dev` rebuilds on every
change instead of once. It is optional for a source install: skip it, and the
server still runs every tool, with the admin URL serving a plain "Selenium
Flow" page and no MCP App offered. The build output is gitignored — it is
never committed, whether it exists or not.

### `show` is the one app tool

`show(uri)` draws a resource in the app shell; the URI-to-component table lives
in `mcp/show.py` and nowhere else; `App.svelte` maps the component names to
Svelte views. The data is the resource's own JSON, so a view cannot drift from
what the resource serves.

- **Not an app on `read_resource`.** That is the model's reading tool; an app
  there would draw a UI on every read Claude makes to think.
- **Drill-down is the shell calling `show` through `callServerTool`** — the tool
  carries `visibility: ["model", "app"]`. Never `sendMessage`: Claude only drafts
  that into the input box, it does not send it.
- **`updateModelContext` shares the shown URI, best effort.** A host may ignore
  it; nothing depends on it.
- **claude.ai ignores `size-changed`** and reads the document's own height, so
  `App.svelte` sets `documentElement.style.height` as well as letting autoResize
  report it. Drop it only once claude.ai honours the notification.
- `show` is listed only for a client that renders apps.
- **The payload goes once, as `structuredContent`**; the text block is one line
  for the model. A plain dict would send the JSON twice.
- **A result over `MAX_SHOWN` (100k characters, both parts as sent) is
  refused**: Claude drops one over ~150k and the app would never get its data.
- **One secret opens inside the app**, from the list `show` already sent: there
  is no single-secret resource (secrets.py, "one read"), so a drill-down has
  nothing to call. `SecretCard` and `lib/secrets.ts` are shared with the admin
  pane, so both read a catalogue entry the same way, and neither ever has a value.

## Session lifetime: who owns what

| | Who owns it | Default here |
|---|---|---|
| **How long a browser lives** | the Grid — `SE_NODE_SESSION_TIMEOUT` on the node | `300s` idle, in the cluster repo |
| **How long we remember a caller** | `SESSION_TTL` | `86400s` (a day), slid forward on every call |
| **Where we remember it** | `SESSION_STORE` | `memory` (or `redis` to share it) |

**Nothing runs a cleanup loop, and nothing should** — the Grid expires idle browsers, the store expires its own keys. If the Grid reaped one we remembered, the next call notices and reopens it at the page it was last on. 🪄
