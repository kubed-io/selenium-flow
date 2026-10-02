# The optimal shape: a refactor and optimisation round

**Status: APPROVED in chat 2026-10-02; the six questions are ruled (see Rulings). Plan: `docs/superpowers/plans/2026-10-02-optimal-shape.md`.** Dr K reads this
as it grows and drops rulings in chat; every ruling is recorded under *Rulings*.

## Brief

Dr K, 2026-10-02: feature freeze. This round is a pure refactor and optimisation
round. *"In the beginning we did not know all the requirements and nuances yet.
Look at the project from the perspective of its requirements and use cases now,
and think about how this app could have been if you knew it all up front."*

- Research best practices: Python, FastMCP, test writing, UI design.
- Keep an eye out for security issues.
- Find the most high-value refactor that enables the trajectory.
- Is there a conceptual core that can be abstracted so the logical part of the
  app is cleaner, *"like how could I make a library out of this that other apps
  could use too"* — a way of thinking about foundations, not a package to ship.
- Look at the sibling mcp-kb for anything of value.
- The profiler and the bench job now measure the code; analyse them.
- **No regression on any core functionality.** Rip things apart to put them back
  together, but every behaviour survives.
- Safe and secure throughout.

## Non-goals

- No new capability: no new tool, endpoint, tab, flag or setting. The CHANGELOG
  takes the `no changelog` label unless a user would notice something.
- No change to any published shape: tool schemas, HTTP bodies and results, the
  OpenAPI document, URIs, the skill, error messages an agent reads. A client
  caches schemas (CONTRIBUTING, "Changing an argument's shape").
- Not a packaging exercise: the "library" is a way to draw the boundaries inside
  this repo, not a second distribution.

## Constraints

- Every rule in `AGENTS.md` stays a rule. The round may move where a rule is
  enforced, never whether.
- Behaviour is pinned before it is moved: the inventory below is the regression
  contract, and an UNPINNED behaviour gets a characterisation test first.
- The unit suite stays green at every commit; ruff, svelte-check, vitest and
  the UI bundle guards too. Integration runs on the branch before merge.
- Measured, not asserted: every optimisation names the bench or profile number
  it moves.

## Process

1. **Understand** (done): thirteen read-only analysts, one per subsystem
   plus research (Python/FastMCP, testing and Svelte, mcp-kb comparison, the
   profile and bench, a whole-package security review), then a completeness
   critic. Reports under `.superpowers/round3/understand/` (gitignored).
2. **Design**: three independent proposals for the core abstraction, judged on
   behaviour safety, what they enable, simplicity, performance, security and
   library shape; this spec is the synthesis.
3. **Plan**: `docs/superpowers/plans/2026-10-02-optimal-shape.md`, tasks sized
   for subagent-driven development, each ending green.
4. **Build**: fresh implementer per task, review after each, whole-branch
   review at the end; the PR loop until CI is green and Copilot asks for a
   human review.

## What the research found

Thirteen reports under `.superpowers/round3/understand/` (read-only analysts,
one per subsystem, plus research, a profile and bench analyst, a security
review, an mcp-kb comparison and a completeness critic). The map in one page:

**The same recipe is written many times.** Every one of the fourteen browser
actions repeats coerce → reconnect → navigate → wait → body → `page_state`
with small drift (`core-browser.md` §2.2); `tools.py` restates every `Actions`
signature as keyword pass-through (~400 lines); adding an action edits
`ENDPOINTS`, `tools.py`, `METHOD_ALIASES`, `spec/schemas.py` and the wiki
(`mcp-spec.md`); the bind-and-scrub rule for a secret is written three times
(`document._check_step`, `run.resolve_step`, `secrets.perform_write`); the
post-action record write is written three ways (`settle`, `run_for`'s touch,
`perform_write`'s `told`); the "what did this request say" reader is written
in `sessions.py`, `clients.py` and `flows/api.py`; the error shaper three
times in `admin.py`/`answer.py`; the five test doubles each exist twice.

**The layers lean on ambient state.** `SessionManager` reads the HTTP request
through FastMCP's context variable; `core/actions.py` imports from `flows`,
`flows/run.py` imports `routes` and `mcp.guidance`, `secrets.py` imports
`flows.run`; nothing proves any layer imports no framework (mcp-kb has
`tests/test_boundaries.py` for exactly this). 221 `monkeypatch.setattr`
sites, the biggest on `sessions.http_request` and `admin.static_path`, tie
the tests to the module layout: a module split breaks them en masse.

**The hot paths are known and small.** The server is idle while Chrome works
(`profile-bench.md`): tools/list shaping is 1.1 ms, startup is 80 % FastMCP
imports. What costs: every action does `store.get` of the whole record (up
to 1 MB of site data), one `is_alive` Grid call, a fresh `ReattachDriver`
with a new connection pool, then `touch` rewrites the whole record (4 Redis
round trips, 5 ms CPU at 1 MB); each open admin page runs its own
`sessions_payload` loop every 2 s (N tabs = N× Grid status, N+1 `files()`
scans, every record decoded); `page()` re-reads and re-templates three files
per `GET /`; the snapshot size cap is quadratic (1.2 s at 400 origins); the
unit suite rebuilds the server per test and vitest builds jsdom 25 times.

**Security: defended where it was designed, open where it was not** (`security.md`).
Defended and pinned: `compare_digest`, signed links with `exp` inside the HMAC,
CSP sandbox on site bytes, origin-exact leash, no value in any result, YAML
`safe_load`, no server-side fetch but the Grid, the non-root image. Open:
`upload_file(path=)` reads any file the server user can (F1); the leash
checks only the top URL, so a secret can be typed into a cross-origin frame
(F2); `/ready` and `/info` are unauthenticated and name the Grid host (F3);
the access log records `?session=` and signed links (F4); no navigation
scheme policy (F5); with no token the browser routes have no Origin check
(F8); the admin page has no CSP or frame-ancestors (`ui.md`).

**Behaviour is mostly pinned, with named gaps.** The inventories run to a few
hundred lines; the critic ranks fifteen UNPINNED behaviours to characterise
before anything moves (`critique.md` §3), led by `upload_file`, the dialog as
page state, the per-action validate-before-reconnect order, the hand-edited
flow shapes that 500 today, the request-naming edge cases, the HTTP status
table end to end, `main.py` boot, and `js/*.js`, which no test executes.

## The core abstraction: one call, stated once

Every capability of this server is **one call** that takes the same road:

```
who is calling            Caller        name, library, client, declared flags
what they asked for       Capability    name, args schema, route, annotations, action
how the browser does it   Recipe        reconnect · navigate · wait · body · page_state
what it leaves behind     Settle        one record write: history, capture, report
how it is answered        Surface       MCP result · HTTP response · flow step entry
```

Today each stage exists, but as a pattern repeated per action, per surface
and per entry point rather than as a thing. The refactor is to make each
stage **one object with one owner**, and to make the two surfaces and the
flow runner *hosts* that drive the same five stages. That is the library
shape Dr K asked for: a kernel another app could host, not a package.

Concretely:

- **`Caller` is resolved once, at the edge, and passed in.** `Caller.from_request(params, headers)`
  (multi-valued, header before param, a repeated name refused like a conflict,
  as mcp-kb reads them) replaces `requested()`, `name_in`, `name_from`,
  `library_from`, `clients.declared` and the per-call `http_request()` walk.
  `SessionManager`, `settings.resolve`, `flows/api`, `files` and `secrets`
  take a `Caller`; none reads ambient state. Off HTTP the caller is `stdio`.
- **A `Capability` registry is the single source of truth.** One table of
  (name, action, route, method, annotations, alias, response shape) in `core`,
  from which `mcp/tools.py` registers tools, `routes.py` mounts endpoints,
  `spec/builder.py` builds the document and `generate_wiki.py` the pages. The
  tool signatures stay literal Python (FastMCP derives the schema from them)
  but the wrappers are generated from the table, so a tool with no endpoint is
  impossible rather than merely tested for.
- **One `Recipe` wraps every browser action.** Coerce and validate first (so a
  bad selector never costs a navigation), then reconnect, optional navigate,
  optional wait, the body, `page_state`, with the one-shot stale retry where it
  applies. This is the one place a per-session lock, a per-call deadline, or
  the spare-tab time budget can later attach.
- **One `Settle` owns every record write after an action**, for a single call,
  a flow step and a bound write alike: the history rule, the capture, the
  reopen report hand-off, the withheld URL. `store.update` returns the note
  its last run produced instead of five `.clear()`-ing closures.
- **One bind-and-scrub policy for secrets**, used by the flow validator, the
  flow runner and the direct write, with the refusal sentences as data.
- **Pure modules carry the rules.** `core/keys.py`, `core/naming.py`,
  `core/coerce.py`, `urls.py`, `core/assertion.py`; `site_data/snapshot.py`
  (model, views, forget) apart from `site_data/transfer.py` (BiDi I/O);
  `flows/template.py`, `flows/redact.py`, `flows/engine.py`, `flows/report.py`;
  `names.py` out of `flows/library.py`; `http/admin/` as a package. A
  boundary test imports `core`, `session`, `flows` and `site_data` with
  `fastmcp`, `starlette` and `selenium` blocked (the `transfer` and `browser`
  modules excepted for selenium) and fails on the first framework import.

What this enables, item by item from the roadmap: the per-session lock and a
time budget attach to `Recipe` once; "failed-but-continued steps' pages" is a
`Run.pages` variable in the engine instead of the report's page-changed rule;
"a call landing mid-save" is a note returned from the save's write; a
catalogue revision in the History row has one owner; site data under its own
key becomes a second store instance if Dr K wants it (Q1).

## Behaviour contract

The regression contract is the union of the inventories in
`.superpowers/round3/understand/*.md` §1 (the three long reports carry ~300
lines; the others ~50 each). Before any module moves, the round adds:

1. **Characterisation tests for the critic's fifteen** (`critique.md` §3), in
   the files that own them, written so that breaking the behaviour on purpose
   fails them.
2. **A golden-shape lock**: JSON snapshots, committed under `tests/golden/`,
   of every tool's `parameters`, description and annotations in both client
   modes; the OpenAPI document; the run reports of the `test_flowrun` fixture
   matrix; the `/admin/sessions` payload for a canned store; every error
   envelope by status. Volatile fields normalised. No snapshot is updated in
   a commit that also moves code.
3. **Surface payload parity**: the same action called as a tool and over HTTP
   returns the same normalised result.
4. **A `node` harness for `js/*.js`** run from pytest the way
   `test_every_script_parses` already shells out, so `helpers.js` is provable.
5. **One `tests/fakes.py`**: a single `FakeRedis`, `FakeGrid`, `FakeActions`,
   `FakeClock`, `FakeBidi`; the bench imports the same ones.

Every refactor commit leaves `tests/` untouched except for import paths, and
the suite count never drops.

## Plan of record

Ordered; each step is green on its own. Full task breakdown in the plan.

**A. Lock** — items 1–5 of the contract. Also: pin `main.py` boot, the ops
endpoints under a prefix, `StoreConflict` → 500, the status table end to end.

**B. The caller edge** — `Caller.from_request`; multi-valued reads; every
manager method takes the caller; the `http_request` monkeypatch becomes a
fixture passing a `Caller`; `conftest.named_caller` adapts; delete the four
cycle-driven local imports.

**C. Pure modules out** — the moves in "The core abstraction"; `names.py`;
the `site_data` split; the `flows` split with `run()` kept as a façade over
the engine; `admin.py` to a package with one `refused`, one `read_body`, one
signed-route factory; the boundary test; delete the dead code (`skill.read`,
`manifest_json`, `tools.SELECTOR`, `Actions.files/clear_files`, the list
fallback in `probe.outline`, stale names in docstrings).

**D. One recipe, one settle, one bind** — `Recipe` under every action with
validate-before-reconnect; `Settle` with `url=`/`touch=` used by `act`, the
flow runner and `perform_write`; `store.update` returning the note; the
`BoundWrite` policy for the three sites; the tolerant `Shape` view over a
flow document (fixes the four 500s on hand-edited YAML).

**E. The registry** — the `Capability` table; tool wrappers, routes, spec and
wiki generated from it; `FLOW_ENDPOINTS` derived from `FLOW_ROUTES`;
byte-equal against the golden tool schemas.

**F. Performance, each with its number** —
- one admin session-list broadcaster: `sessions_payload` once per interval,
  fanned out to every SSE client, cached ~1 s for `GET /admin/sessions`; a
  test counts `grid.status` calls under three streams (bench: new
  `sessions_payload` case);
- a pooled `requests.Session` in `Grid` and a reusable connection for
  `ReattachDriver` (count connections in a fake Grid);
- `page_state` as one script returning `[href, title]`, `viewport` folded
  into `center.js`, `wait_for_*` polling at 0.2 s (a `CountingDriver` asserts
  round trips per action);
- snapshot size cap in O(n) (bench the 400 × 20 KB case; property test against
  the old loop);
- views that count without building details for the admin poll;
- `page()` cached per (name, mount, console) with `Cache-Control`;
- `/ready` cached ~2 s;
- `_document_schema` cached; MCP `save_flow` off the event loop;
- vitest `pool: vmThreads` (measure; keep isolation); jsdom once per worker;
- the unit suite: a session-scoped immutable app for `test_every_endpoint_is_mounted`
  (5.2 s today), `pytest-randomly` once to expose order dependence, then
  `-n 2 --dist loadfile` if clean.

**G. Security fixes that change no published shape** — the leash takes the
effective origin (frame-aware, F2); the access log drops the query string
(F4); a tokenless server on a non-loopback bind warns loudly (F8); the admin
page gets a CSP and `frame-ancestors` (pending Q6); the signed events link
documented as token-lifetime; a body cap on JSON and flow YAML. F1, F3 and
F5 wait on Dr K (Q3–Q5).

**H. Docs** — AGENTS.md says `SESSION_TTL` is 3600 s where the code says
86400; `guidance.PAGES` omits `SITE_DATA.md`; the stale module names in
docstrings; CONTRIBUTING's "where things live" for the new modules;
the `no changelog` label unless Dr K wants the security fixes noted.

Not this round, recorded for the next: `list_changed` when flows or kept
files change and the MCP Skills extension (both new capability, learned
from mcp-kb); `ResourcesAsTools` from FastMCP in place of `mirror.py`
(saves ~150 lines, but the media shaping and client table stay ours; low
value); typed action returns feeding `output_schema` and the spec (a
shape-drift risk the golden lock makes safe later); FastMCP `lifespan` and
`Depends` (after B makes the seam explicit).

## Questions for Dr K

Only the ones that change the work. My recommendation first.

1. **Site data under its own store key?** It is the largest performance win
   per action and per admin poll for a 1 MB jar, and the highest-risk change
   in the map: "expires with the record", the clean-open erase, the
   replaced-browser save and Forget's atomicity all become two-key problems.
   *Recommend: not this round. Keep one record, take the cheap wins (F), and
   design the split in its own round with the Lock in place.*
2. **Serialising one session's calls (saga Ch. 4 Q2).** Two calls on one
   session overlap on both surfaces today; the record is safe (CAS), the
   browser is not. *Recommend: a per-session lock on browser-driving actions
   only, with `end_browser`, `open_session` and the read-only resources
   exempt, and `end_browser` raising the existing cancel so a 900 s `assert`
   ends rather than being queued behind. A second call waits; it does not
   get a 409.* This is the one behaviour change in the round; say no and it
   is deferred with the seam (`Recipe`) left ready.
3. **`upload_file(path=)`** lets any token holder upload any file the server
   can read, including its config and mounted secrets, to any site. *Recommend:
   delete the `path` source (one user, no back-compat); `file=uri` stays.*
4. **Navigation policy.** Is the Grid's egress restricted by NetworkPolicy?
   If not, `navigate` reaches every cluster host and `file:`/`chrome:`
   schemes. *Recommend: refuse non-web schemes now; hosts stay a cluster
   concern.*
5. **`/ready` and `/info` without a token** name the Grid host and version.
   *Recommend: when a token is configured, answer them with bare status
   unless the token is presented; the kubelet needs only the status.*
6. **Is the admin page ever framed** (Homepage, Grafana)? Decides
   `frame-ancestors 'none'` versus an allowlist.

Decided without asking, recorded under Rulings: delete the pre-`v0.1.0`
flow-format refusals (`valueFrom`, step `params`, run-time `writeOnly`) under
the no-back-compat rule; plain JSON goldens rather than a snapshot library;
`pytest-randomly`/`xdist` only in the test extra; test layout stays flat this
round; the TTL doc fixed to the code.

## Rulings

- Dr K, 2026-10-02: feature freeze; refactor and optimisation only; no
  regression; rulings in chat, recorded here; questions only when high level.
- Claude, 2026-10-02: the pre-`v0.1.0` flow-format and run-time `writeOnly`
  refusals go (no back-compat until a second user). Cost if wrong: a hand-kept
  old document fails with a generic message instead of a named one.
- Claude, 2026-10-02: goldens are committed JSON, no snapshot library; new
  test dependencies only in the `test` extra; the flat `tests/` layout stays;
  `AGENTS.md`'s TTL follows the code (86400 s).
- Dr K, 2026-10-02, on the six questions: (1) site data stays on the record this
  round; (2) serialise one session's browser-driving calls, `end_browser` exempt
  and cancelling; (3) `upload_file(path=)` goes — a file comes from the session's
  file store or as content with a filename; (4) refuse every scheme but the web's
  — a later round may let `navigate` show a kept file, served by the server, since
  a `file:` URL in the Grid's browser would name the Grid node's disk, not ours;
  (5) `/ready` and `/info` stay tokenless and unchanged, so Kubernetes can probe
  them; (6) the admin page should be frameable by Nextcloud or Grafana when an
  operator lists them: a `security` config section with `frame_ancestors`.
- Claude, 2026-10-02: the user-visible consequences of the security fixes take
  changelog lines (a removed argument, a refused scheme, a new setting, serialised
  calls); the refactor itself takes none.
