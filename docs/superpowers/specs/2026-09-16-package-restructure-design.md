# The refit: one package with rooms, one way to answer a request

Design record for the cleanup round that follows #35. Written 2026-09-16,
before any code moved.

## Why now

`main` carries a breaking contract change (#34, #35) and the next thing that
happens is a version bump. That makes this the one moment where restructuring
costs nothing extra: callers are already migrating, so a package reorganisation
rides along invisibly instead of being a second disruption.

The brief, in Dr K's words: *"look at the code and think how you would have done
this from the beginning if you only knew all the requirements and details we
have now"* — plus a deep pass on every document and description.

## What is actually wrong

Measured, not assumed:

- **29 modules, 12,815 lines, flat.** No subpackages at all. The three largest
  are `openapi.py` (1,443), `actions.py` (1,377) and `admin.py` (979).
- **Almost no duplicated text.** A clone scan over the package found exactly one
  repeated six-line block, and it is an import preamble. So "share more code" is
  not about extracting copy-paste.
- **Four request wrappers doing one job**: `routes._answer`, `files.answer`,
  `flowapi.answer`, `admin.guarded`. Three ways to read a JSON body
  (`auth.json_request`, `routes._body`, raw `request.json()`). Five places that
  shape `errors.status_for` + `errors.message` into a `JSONResponse`.
- **Data living in code.** `openapi.py` is fifteen literal schema blobs beside
  fourteen helpers; `admin.py` is a 700-line `register()` closure holding
  thirteen routes.
- **A misnamed module.** `hints.py` builds MCP *tool annotations* — read-only,
  destructive, idempotent, open-world. It says nothing about hints in the sense
  of guidance, and that cost a direct question from Dr K, which is the proof.

## What is not wrong, and stays

- The tools are the source of the request schemas. The spec is derived from them
  so the two surfaces cannot drift, and `test_openapi.py` asserts it.
- The declared route tables (`ENDPOINTS`, `FLOW_ROUTES`, `FILE_ROUTES`) already
  exist and already work. They get a shared mounter, not a redesign.
- The behaviour. This refit changes no endpoint, no tool signature, no result
  shape. 1,150 tests pass unchanged, and not one assertion may be edited to make
  the restructure fit.

## The target layout

```
kubed/selenium_flow/
  server.py main.py routes.py errors.py          the app itself
  core/      browser actions probe pointer js/   WebDriver and the page
  session/   sessions store settings             who is calling, what they hold
  flows/     document library run api            saved documents
  http/      answer auth links files admin       shared request machinery
  mcp/       tools resources prompts skill apps annotations guidance
  spec/      builder + schema data
```

**The app stays on top** (Dr K's call, and the right one): `server.py` composes
it, `main.py` starts it, `routes.py` is the app's own route table and the mount
every other tree hangs beneath, and `errors.py` is cross-cutting. Everything
below them is an independent component in its own room — which is also the test
of whether a room is real: if it cannot be described without naming the app, it
does not deserve a directory.

Each package holds three to eight files. The tree then says what the README
says: one core, two surfaces over it.

**Rejected — by surface** (`core/` + `surfaces/http/` + `surfaces/mcp/`): one
more level on every import, buying a distinction the package names already make.

**Rejected — minimal** (split only the three big modules): leaves the flat
surface that prompted the brief.

## One way to answer a request

`http/answer.py` owns the whole shape: authorise, read the body, name the
session, run the call, turn a failure into JSON. It replaces all four wrappers
and both stray body readers.

```python
async def answer(request, token, what, call, *, body=True) -> JSONResponse
```

`admin.guarded` becomes a thin decorator over the same primitives rather than a
parallel implementation — admin handlers return HTML and streams as well as
JSON, which is why it exists at all, and that difference stays explicit.

`errors.as_response(exc, what)` becomes the single place a failure becomes a
status and a message. Today five sites do it, and they already disagree about
logging.

`http/mount.py` takes a declared route table and mounts it under the prefix.
Top-level `routes.py` declares the app's own table and calls it, as
`flows/api.py`, `http/files.py` and `http/admin.py` do with theirs. Five modules
currently re-implement `f"{prefix}/…"` wiring around tables that already exist.

## Prose: neutral in both surfaces

The published spec's operation descriptions **are** the MCP tool descriptions
(`"description": tool.description`). That is why an HTTP caller reading
`POST /browser/screenshot` — 1,636 characters — is told *"do not paste the image
back into your reply"*, and why nine operations carry `keep_file`,
`session_files`, `list_secrets` and `execute_script` in a JSON API document.

**Decision: rewrite the tool descriptions to be surface-neutral**, and move
agent coaching into the skill, where agents already read it. One source
survives, both surfaces get shorter and truer, and no transform layer is
invented.

**Considered and rejected: `x-mcp` extensions in the spec** (Dr K's idea, and a
good one). FastMCP 4.0.3's `from_openapi()` takes an explicit `mcp_names` dict
and reads no `x-mcp*` extension, so such annotations would be a private
convention that nothing consumes. Producing text no reader reads is work with
no payoff. If an ecosystem consumer appears, this decision is cheap to revisit —
the prose will by then live in one place, which is what makes it revisitable.

Neutral does not mean vague. Every description still says what the call does,
what it returns, and what the common mistake is. What leaves is the second
person addressed at a model, and the names of tools that do not exist over HTTP.

## Guidance, and what `hints` really was

Two separate mechanisms, currently tangled by one name.

**Annotations** (`hints.py` → `mcp/annotations.py`): unchanged in behaviour,
renamed for what it is. MCP defaults an unannotated tool to destructive, so
these are load-bearing, and the rename is the whole change.

**Guidance** (`mcp/guidance.py`, new): one shape for pointing a caller at the
reference that helps.

```python
{"read": "skill://selenium-flow/references/FLOWS.md", "section": "...", "prompt": {...}}
```

Today three emitters disagree: a failed flow run returns `hint` as an object, the
session status returns `guidance` as a bare string, and a blocked dialog returns
`hint` as prose. One shape, one key, three call sites.

**And the gap worth closing:** the skill is published correctly — a client
listing resources sees `skill://selenium-flow/SKILL.md` and all six references —
but nothing tells a client to read it. Only *failures* point at the skill, so an
agent finds the manual after it has already gone wrong. `INSTRUCTIONS`, the one
blurb every MCP client receives at connect time, never mentions it.

So: name the skill in `INSTRUCTIONS`, and emit guidance at the two non-failure
moments where it is actually useful — the first `open_session` of a session, and
the "no browser yet" refusal. Not on every result; the current failure-only
instinct is right, and spam would train clients to ignore it.

## The documentation pass

Findings already in hand, each verified rather than suspected:

1. `scripts/generate_openapi.py` says `openapi.yaml` is *"checked in so it can
   be reviewed in a pull request"*. It is gitignored and untracked;
   `test_openapi.py` and `quality.yml` both correctly call it a build artifact.
2. Nine HTTP operations carry MCP-only vocabulary (above).
3. `saga/Chapter_1_The_Flight_Plan.md`'s E5 table reads `$ROUTE_PREFIX/admin`,
   `/files`, `/flows`, which can be read as only `/admin` being mounted.
4. Every skill reference, the README, AGENTS.md, CONTRIBUTING.md and the
   hand-written wiki pages get read end to end against the shipped contract —
   not skimmed for keywords. The session/selector sweeps of #35 found stale
   wording three separate times, each after the previous sweep claimed to be
   done.

The standard: not overdone, not vague, not missing, and never describing a
contract the code does not have.

## Loose ends folded in

- **Orphan session rows.** Redis still holds pre-#34 records keyed
  `named:<name>`, which the admin list renders as if they were sessions. A
  session name can never contain `:`, so the listing skips keys that are not
  valid session names. The live keys expire on their own; no migration.
- **The packaging tripwire.** `[tool.setuptools] packages` is a hand-written
  list. A subpackage missing from it ships a wheel with a module silently
  absent, and **nothing tests this today**. A test walks the source tree and
  asserts every package is declared. This is the single highest-risk item in the
  refit and it gets its guard first.

## Testing

- The 1,150 existing tests are the behaviour guard and their assertions are
  frozen. Import lines change; expectations do not.
- Six string-form patch targets (`monkeypatch.setattr("kubed.selenium_flow.…")`
  and one `patch(…)`) break silently at runtime rather than at import, so they
  are enumerated and fixed deliberately: `actions.ActionChains`,
  `browser.settled`, `browser.wait_for_clickable`, `browser.wait_for_element`,
  `sessions.http_request`, `browser.requests.delete`. The other 100 monkeypatch
  calls are object-form and move with their imports.
- Every new test is proved by breaking the thing it guards, confirming it fails,
  and restoring — the discipline that caught two vacuous guards in #35.
- The wheel is built and its contents asserted before the PR is opened, because
  a missing subpackage is invisible until an installed copy imports it.

## Sequence inside the one PR

1. The packages-list test, first, so every later move is guarded.
2. Mechanical moves and import updates. Tests green, no behaviour edits.
3. `http/answer.py`, `errors.as_response`, the route mounter. Tests green.
4. `spec/` data split; descriptions rewritten neutral; redocly lint passes.
5. `annotations.py` rename, `guidance.py`, the `INSTRUCTIONS` pointer.
6. The documentation pass, then the saga.

## Acceptance

- No endpoint, tool signature or result shape differs from `main`.
- No test assertion edited to accommodate a move.
- `pip wheel` contains every module the source tree has.
- The published spec contains no MCP-only vocabulary.
- `INSTRUCTIONS` names the skill.
- Every document read end to end, and every statement in it true of the shipped
  contract.
