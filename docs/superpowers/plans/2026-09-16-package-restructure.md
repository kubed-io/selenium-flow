# Package Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganise 29 flat modules into subpackages with the app on top, collapse four request wrappers into one, move the OpenAPI schema data out of Python, and make every description and document true and surface-neutral — with no change to behaviour.

**Architecture:** `server.py`, `main.py`, `routes.py` and `errors.py` stay at the package root as the app itself. Independent components move into `core/`, `session/`, `flows/`, `http/`, `mcp/` and `spec/`. One `http/answer.py` owns authorise → read body → name session → run → shape failure; `errors.as_response` becomes the single failure-to-JSON site; `http/mount.py` mounts the declared route tables that already exist.

**Tech Stack:** Python 3.10–3.14, FastMCP 4.0.3, Starlette, pytest, setuptools + setuptools_scm, redocly (spec lint), ruff (E501 at 88 chars).

**Spec:** `docs/superpowers/specs/2026-09-16-package-restructure-design.md`

## Global Constraints

- **Refactor only.** No endpoint, tool signature, result shape or behaviour changes. Nothing gained, nothing lost.
- **Existing test assertions are frozen.** Import lines change; expectations do not. If an assertion seems to need editing, the move is wrong.
- The suite is **1,150 passed, 18 skipped** and must stay so, except where a task explicitly adds a test.
- Run tests with: `PYTHONPATH=$DEPS:. python3 -m pytest -q -p no:cacheprovider tests --ignore=tests/integration` where `$DEPS` is the scratchpad deps directory (no venv in this pod).
- Ruff line limit is 88. `git diff -U0 -- '*.py' | grep '^+' | awk 'length > 89'` must print nothing before any commit.
- Every new test is proved by breaking the thing it guards, watching it fail, then restoring.
- `js.py` and `prompts.py` each load files from a sibling directory of the same name, mapped by `[tool.setuptools.package-dir]`. Moving either module means moving its mapping key in the same commit.
- Baselines to beat, measured 2026-09-16 on `3a57900`: **28 tools / 19,257 description bytes**, spec **96,071 bytes** with **16,238 bytes** of operation descriptions, `INSTRUCTIONS` **1,379 bytes**.

---

### Task 1: The packages guard

Nothing today asserts that pyproject's hand-written `packages` list covers the source tree. A subpackage missing from it ships a wheel with a module silently absent and CI green. This guard comes first because every later task adds a package.

**Files:**
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Produces: `declared_packages()` and `source_packages()` helpers used by later tasks' verification.

- [ ] **Step 1: Write the failing test**

```python
def declared_packages() -> set[str]:
    """Every package name pyproject says ships."""
    data = tomllib.loads(PYPROJECT.read_text())
    return set(data["tool"]["setuptools"]["packages"])


def source_packages() -> set[str]:
    """Every importable package in the source tree, as a dotted name."""
    found = set()
    for init in (REPO / "kubed").rglob("__init__.py"):
        parts = init.relative_to(REPO).parent.parts
        found.add(".".join(parts))
    return found


def test_every_source_package_is_declared():
    """setuptools takes an explicit list here, so a package missing from it is
    simply absent from the wheel — no error at build, no error at import until
    something reaches for it in an installed copy."""
    missing = source_packages() - declared_packages()
    assert not missing, (
        f"add to [tool.setuptools] packages in pyproject.toml: {sorted(missing)}"
    )
```

- [ ] **Step 2: Prove it is not vacuous**

```bash
mkdir -p kubed/selenium_flow/_guardcheck && touch kubed/selenium_flow/_guardcheck/__init__.py
PYTHONPATH=$DEPS:. python3 -m pytest -q tests/test_packaging.py -k every_source_package
# Expected: FAIL naming kubed.selenium_flow._guardcheck
rm -rf kubed/selenium_flow/_guardcheck
```

- [ ] **Step 3: Run it clean**

Run: `PYTHONPATH=$DEPS:. python3 -m pytest -q tests/test_packaging.py`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_packaging.py
git commit -m "A package missing from pyproject's list ships an absent module"
```

---

### Task 2: `core/` — the browser and the page

**Files:**
- Create: `kubed/selenium_flow/core/__init__.py`
- Move: `browser.py`, `actions.py`, `probe.py`, `pointer.py`, `js.py` → `core/`
- Modify: `pyproject.toml` (packages list, package-dir key for `js`)
- Modify: every importer of those modules

**Interfaces:**
- Produces: `from .core import browser, actions, probe, pointer, js` for all later tasks.
- `js.py` keeps `Path(__file__).parent / "js"`, which is why its package-dir key moves with it.

- [ ] **Step 1: Move the modules**

```bash
mkdir -p kubed/selenium_flow/core && touch kubed/selenium_flow/core/__init__.py
git mv kubed/selenium_flow/{browser,actions,probe,pointer,js}.py kubed/selenium_flow/core/
```

- [ ] **Step 2: Update pyproject**

In `[tool.setuptools] packages` add `"kubed.selenium_flow.core"` and change `"kubed.selenium_flow.js"` to `"kubed.selenium_flow.core.js"`. In `[tool.setuptools.package-dir]` and `[tool.setuptools.package-data]` rename the `kubed.selenium_flow.js` key to `kubed.selenium_flow.core.js`, value unchanged (`"js"`).

- [ ] **Step 3: Fix imports in the package**

```bash
grep -rln "from \.\(browser\|actions\|probe\|pointer\|js\) import\|from \. import.*\(browser\|actions\|probe\|pointer\|js\)" kubed/selenium_flow/
```
Inside `core/`, siblings import each other as `from . import browser`. Outside it, `from .core import browser`. The four string-form patch targets in tests that name `kubed.selenium_flow.browser` / `.actions` become `kubed.selenium_flow.core.browser` / `.core.actions`.

- [ ] **Step 4: Fix imports in tests**

```bash
grep -rl "kubed.selenium_flow.\(browser\|actions\|probe\|pointer\|js\)\|from kubed.selenium_flow import.*\(browser\|actions\|probe\|pointer\|js\)" tests/
```
Change the import path only. **No assertion may change.**

- [ ] **Step 5: Run the suite and the packaging guard**

Run: `PYTHONPATH=$DEPS:. python3 -m pytest -q -p no:cacheprovider tests --ignore=tests/integration`
Expected: 1,150 passed, 18 skipped.

- [ ] **Step 6: Prove the wheel still carries the JavaScript**

```bash
python3 -m build --wheel --outdir /tmp/w . 2>/dev/null || pip wheel --no-deps -w /tmp/w .
unzip -l /tmp/w/*.whl | grep -E "core/js/.*\.js|core/browser.py" | head
```
Expected: `kubed/selenium_flow/core/js/outline.js` and friends are present.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "core/: the browser, the page, and the JavaScript it runs"
```

---

### Task 3: `session/` — who is calling

**Files:**
- Create: `kubed/selenium_flow/session/__init__.py`
- Move: `sessions.py`, `store.py`, `settings.py` → `session/`
- Modify: `pyproject.toml` packages list; importers; `tests/` import lines

**Interfaces:**
- Produces: `from .session import sessions, store, settings`.
- The string-form patch target `kubed.selenium_flow.sessions.http_request` becomes `kubed.selenium_flow.session.sessions.http_request`.

- [ ] **Step 1: Move**

```bash
mkdir -p kubed/selenium_flow/session && touch kubed/selenium_flow/session/__init__.py
git mv kubed/selenium_flow/{sessions,store,settings}.py kubed/selenium_flow/session/
```

- [ ] **Step 2: Declare the package** — add `"kubed.selenium_flow.session"` to the packages list.

- [ ] **Step 3: Update importers and test import lines** (assertions frozen).

- [ ] **Step 4: Run the suite** — Expected: 1,150 passed, 18 skipped.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "session/: the caller's name, the record, the settings"
```

---

### Task 4: `flows/` — saved documents

Names lose the `flow` prefix they only carried to stay unique while flat.

**Files:**
- Create: `kubed/selenium_flow/flows/__init__.py`
- Move: `flowdoc.py` → `flows/document.py`, `flows.py` → `flows/library.py`, `flowrun.py` → `flows/run.py`, `flowapi.py` → `flows/api.py`
- Modify: `pyproject.toml`; importers; test import lines

**Interfaces:**
- Produces: `from .flows import document, library, run, api`.
- `flows.library` keeps every public name it has today (`valid_name`, `valid_session_name`, `RESERVED_SESSIONS`, `GLOBAL_SESSION`, `InvalidName`).

- [ ] **Step 1: Move with renames**

```bash
mkdir -p kubed/selenium_flow/flows && touch kubed/selenium_flow/flows/__init__.py
git mv kubed/selenium_flow/flowdoc.py kubed/selenium_flow/flows/document.py
git mv kubed/selenium_flow/flows.py  kubed/selenium_flow/flows/library.py
git mv kubed/selenium_flow/flowrun.py kubed/selenium_flow/flows/run.py
git mv kubed/selenium_flow/flowapi.py kubed/selenium_flow/flows/api.py
```

- [ ] **Step 2: Declare the package** — add `"kubed.selenium_flow.flows"`.

- [ ] **Step 3: Update importers and tests** — `tests/test_flows.py`, `tests/test_flowdoc.py`, `tests/test_flowapi.py`, `tests/test_flowrun.py` change import lines only.

- [ ] **Step 4: Run the suite** — Expected: 1,150 passed, 18 skipped.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "flows/: document, library, run, api"
```

---

### Task 5: `http/` — the request machinery

**Files:**
- Create: `kubed/selenium_flow/http/__init__.py`
- Move: `auth.py`, `links.py`, `files.py`, `admin.py` → `http/`
- Modify: `pyproject.toml`; importers; test import lines

**Interfaces:**
- Produces: `from .http import auth, links, files, admin`.
- `routes.py` stays at the package root and imports `from .http import auth`.

- [ ] **Step 1: Move**

```bash
mkdir -p kubed/selenium_flow/http && touch kubed/selenium_flow/http/__init__.py
git mv kubed/selenium_flow/{auth,links,files,admin}.py kubed/selenium_flow/http/
```

- [ ] **Step 2: Declare the package** — add `"kubed.selenium_flow.http"`.

- [ ] **Step 3: `admin.py` finds `static/` via `Path(__file__)`** — check `static_path()` still resolves after the move; the packaged copy is `kubed/selenium_flow/static`, now one level up from `http/`. Fix the parent count and keep the source-checkout fallback.

- [ ] **Step 4: Run the suite** — Expected: 1,150 passed, 18 skipped. `tests/test_files_and_admin.py` exercises the served page, which is what catches a wrong parent count.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "http/: auth, links, files, admin"
```

---

### Task 6: `mcp/` — what an agent sees, and the rename

**Files:**
- Create: `kubed/selenium_flow/mcp/__init__.py`
- Move: `tools.py`, `resources.py`, `skill.py`, `apps.py`, `prompts.py` → `mcp/`
- Move + rename: `hints.py` → `mcp/annotations.py`
- Modify: `pyproject.toml` (packages; `kubed.selenium_flow.prompts` → `kubed.selenium_flow.mcp.prompts` in package-dir and package-data)

**Interfaces:**
- Produces: `from .mcp import tools, resources, skill, apps, prompts, annotations`.
- `annotations.hints()` and `annotations.reads()` keep their names and signatures — only the module name changes.
- `prompts.py` keeps `Path(__file__).parent / "prompts"`, so its package-dir key moves with it.

- [ ] **Step 1: Move and rename**

```bash
mkdir -p kubed/selenium_flow/mcp && touch kubed/selenium_flow/mcp/__init__.py
git mv kubed/selenium_flow/{tools,resources,skill,apps,prompts}.py kubed/selenium_flow/mcp/
git mv kubed/selenium_flow/hints.py kubed/selenium_flow/mcp/annotations.py
```

- [ ] **Step 2: Update pyproject** — add `"kubed.selenium_flow.mcp"`, rename the `kubed.selenium_flow.prompts` and `kubed.selenium_flow.skills` keys to `kubed.selenium_flow.mcp.prompts` and `kubed.selenium_flow.mcp.skills`, values unchanged.

- [ ] **Step 3: Update importers** — five modules import `hints`; they become `from .mcp import annotations` and call `annotations.hints(...)` / `annotations.reads(...)`.

- [ ] **Step 4: Run the suite** — Expected: 1,150 passed, 18 skipped.

- [ ] **Step 5: Prove the wheel carries the skill and prompts**

```bash
pip wheel --no-deps -w /tmp/w . && unzip -l /tmp/w/*.whl | grep -E "mcp/skills/selenium-flow/SKILL.md|mcp/prompts/.*\.md"
```
Expected: both present. This is the failure mode that is invisible until an installed copy serves no skill.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "mcp/: tools, resources, skill, prompts, apps — and hints is annotations"
```

---

### Task 7: `spec/` — schema data out of Python

**Files:**
- Create: `kubed/selenium_flow/spec/__init__.py`, `spec/builder.py`, `spec/schemas.py`
- Move: `openapi.py` → split between `spec/builder.py` (the fourteen helpers and `build_spec`) and `spec/schemas.py` (the fifteen literal blobs: `PAGE_STATE`, `RESPONSES`, `ERROR`, `HEALTH`, `STARTED`, `READY`, `INFO`, `SESSION_PARAMETERS`, `FLOW_STEP`, `FLOW_SCHEMAS`, `_SESSION`, `_WRITE_SESSION`, `_FLOW_OPERATIONS`, `FILE_SCHEMAS`, `_FILE_OPERATIONS`)
- Modify: `scripts/generate_openapi.py`, `routes.py`, `tests/test_openapi.py` import lines

**Interfaces:**
- Produces: `from .spec.builder import build_spec, PLACEHOLDER_VERSION`.
- `DESCRIPTION` stays with the builder: it is prose about the API, not a schema.

- [ ] **Step 1: Split** — move the fifteen constants verbatim into `spec/schemas.py`; `builder.py` imports them. No text changes in this task.

- [ ] **Step 2: Run the suite** — Expected: 1,150 passed, 18 skipped. `test_openapi.py` compares the built document against the live tools, so an accidental edit shows up here.

- [ ] **Step 3: Regenerate and lint the spec**

```bash
PYTHONPATH=$DEPS:. python3 scripts/generate_openapi.py
npx --yes @redocly/cli@2 lint openapi.yaml
```
Expected: same path and schema counts as the baseline (26 paths, 52 schemas), lint clean.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "spec/: the builder, and the schema data it assembles"
```

---

### Task 8: One way to answer a request

**Files:**
- Create: `kubed/selenium_flow/http/answer.py`, `kubed/selenium_flow/http/mount.py`
- Modify: `errors.py` (add `as_response`), `routes.py`, `flows/api.py`, `http/files.py`, `http/admin.py`
- Test: `tests/test_http_answer.py` (new)

**Interfaces:**
- Produces:
  - `async def answer(request, token, what, call, *, body: bool = True) -> JSONResponse`
  - `def as_response(exc: Exception, what: str) -> JSONResponse` in `errors`
  - `def mount(mcp, prefix: str, table: dict, handler) -> None` in `http/mount.py`
- Consumes: `auth.authorized`, `sessions.name_from`, `errors.status_for`, `errors.message`.

- [ ] **Step 1: Write the failing test**

```python
async def test_one_answer_shapes_every_surface_the_same(server):
    """Four wrappers used to decide this separately: unauthorised is 401, a
    non-object body is 400, and a failure carries errors.message — whichever
    tree answered."""
    from kubed.selenium_flow.http import answer as answer_module

    assert answer_module.answer.__module__ == "kubed.selenium_flow.http.answer"
    client = TestClient(server.mcp.http_app())
    for path in ("/browser", "/files", "/flows/x"):
        assert client.post(path, json={}).status_code == 401
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=$DEPS:. python3 -m pytest -q tests/test_http_answer.py -v`
Expected: FAIL — `kubed.selenium_flow.http.answer` does not exist.

- [ ] **Step 3: Implement `answer.py`** — lift `routes._answer` verbatim, then add the `body=False` branch that `files.answer` needs for GETs. Keep the comment explaining why a 503's traceback is suppressed.

- [ ] **Step 4: Move the three other wrappers onto it** — `flows/api.py` and `http/files.py` delegate; `http/admin.py` keeps `guarded` as a thin decorator over `auth.authorized` because its handlers return HTML and event streams, and says so in one line.

- [ ] **Step 5: Add `errors.as_response`** and use it in all five shaping sites.

- [ ] **Step 6: Run the suite** — Expected: 1,150 passed + the new test, 18 skipped.

- [ ] **Step 7: Prove the new test is not vacuous** — delete the `auth.authorized` check in `answer.py`, watch the 401 assertions fail, restore.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "One answer(): authorise, read, name, run, shape the failure"
```

---

### Task 9: Guidance, and the skill nobody was told about

**Files:**
- Create: `kubed/selenium_flow/mcp/guidance.py`
- Modify: `flows/run.py`, `session/sessions.py`, `core/browser.py`, `mcp/tools.py` (`INSTRUCTIONS`)
- Test: `tests/test_guidance.py` (new)

**Interfaces:**
- Produces: `def pointer(page: str, section: str = "", prompt: dict | None = None) -> dict` returning `{"read": "skill://selenium-flow/references/<page>", "section": ..., "prompt": ...}` with empty keys omitted.

- [ ] **Step 1: Write the failing test**

```python
def test_every_pointer_names_a_reference_that_exists():
    """A skill:// URI that 404s is worse than no URI: it teaches the agent the
    manual is broken."""
    from kubed.selenium_flow.mcp import guidance, skill

    root = skill.skill_path()
    for page in guidance.PAGES:
        assert (root / "references" / page).is_file(), page


def test_the_instructions_name_the_skill():
    """INSTRUCTIONS is the one blurb every client reads at connect. Before this,
    only failures pointed at the skill — the manual arrived after the mistake."""
    from kubed.selenium_flow.mcp.tools import INSTRUCTIONS

    assert "skill://selenium-flow/SKILL.md" in INSTRUCTIONS
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=$DEPS:. python3 -m pytest -q tests/test_guidance.py -v`
Expected: FAIL — no `guidance` module, and `INSTRUCTIONS` does not mention the skill.

- [ ] **Step 3: Implement `guidance.pointer` with a `PAGES` tuple** of the reference filenames it may name.

- [ ] **Step 4: Move the three emitters onto it** — `flows/run.py`'s `hint_for`, `session/sessions.py`'s bare `guidance` string, `core/browser.py`'s dialog hint. The result keys stay exactly as they are today for `hint_for`; `sessions` gains the same object shape in place of a bare string. **This is the one place a result shape changes**, so `tests/test_sessions.py` and the `openapi` schema for `current_session` are updated together, and the CHANGELOG records it.

- [ ] **Step 5: Add one sentence to `INSTRUCTIONS`** naming `skill://selenium-flow/SKILL.md` as where the how-to lives.

- [ ] **Step 6: Run the suite, prove the new tests break** — point `PAGES` at a file that does not exist, watch it fail, restore.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "One guidance shape, and the instructions finally name the skill"
```

---

### Task 10: Surface-neutral descriptions

**Files:**
- Modify: `kubed/selenium_flow/mcp/tools.py` (every tool docstring), `skills/selenium-flow/SKILL.md` and `references/*.md` (receive the agent coaching)
- Test: `tests/test_surfaces.py` (add)

**Interfaces:**
- Consumes: the baseline numbers in Global Constraints.

- [ ] **Step 1: Write the failing test**

```python
MCP_ONLY = ("keep_file", "session_files", "list_secrets", "execute_script",
            "open_session", "end_browser", "run_flow", "save_flow")


async def test_the_http_spec_speaks_http(server):
    """The spec's prose is the tool's prose, so a tool description written at an
    agent — 'do not paste the image back into your reply' — is published to
    every HTTP caller as if it were API documentation."""
    spec = await build_spec(server.mcp, ENDPOINTS, "", authenticated=True)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "delete"):
                continue
            text = op.get("description") or ""
            found = [w for w in MCP_ONLY if w in text]
            assert not found, f"{method.upper()} {path} names MCP-only {found}"
```

- [ ] **Step 2: Run it to verify it fails**

Expected: FAIL naming nine operations (`/browser/write`, `/browser/press-key`, `/browser/script`, `/browser/assert`, `/browser/screenshot`, `/browser/upload`, `POST /browser`, `DELETE /browser`, `/schemas/flow`).

- [ ] **Step 3: Rewrite the offending descriptions** — say what the call does, what it returns, and the common mistake. Move every sentence addressed at a model ("do not paste the image back into your reply", "give them the absolute_url") into `skills/selenium-flow/references/READING_PAGES.md` and `SKILL.md`, where agents read it.

- [ ] **Step 4: Re-measure**

```bash
PYTHONPATH=$DEPS:. python3 scripts/measure_descriptions.py   # written in this step
```
Record tools/description bytes, spec bytes and operation-description bytes against the baseline. Expect a material drop; record the real number even if it is small.

- [ ] **Step 5: Run the suite and regenerate the spec** — `test_skill.py`'s selector guard and `test_wiki.py` both read these files, so they fail loudly on a broken example.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "Descriptions say what the call does; coaching moves to the skill"
```

---

### Task 11: The documentation pass

**Files:**
- Modify: `scripts/generate_openapi.py` (docstring), `saga/Chapter_1_The_Flight_Plan.md` (E5 table row), `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `.github/copilot-instructions.md`, `skills/selenium-flow/**`, `wiki/*.md` (hand-written pages), `CHANGELOG.md`
- Modify: `http/admin.py` (orphan session rows)
- Test: `tests/test_files_and_admin.py` (add)

- [ ] **Step 1: Fix the lie** — `scripts/generate_openapi.py` says `openapi.yaml` is "checked in so it can be reviewed in a pull request". It is gitignored and untracked; it is a build artifact regenerated and linted in CI.

- [ ] **Step 2: Fix the saga table row** — Chapter 1's E5 row reads `$ROUTE_PREFIX/admin`, `/files`, `/flows`, which reads as only `/admin` being mounted. All three are mounted.

- [ ] **Step 3: Orphan session rows — write the failing test**

```python
def test_the_listing_skips_keys_that_cannot_be_session_names(server, client):
    """Pre-#34 records are keyed `named:<name>`. A session name can never
    contain a colon, so such a key is not a session and must not render as one."""
    server.sessions.store.set("named:ghost", SessionRecord(session_id=""))
    names = [row["key"] for row in client.get("/admin/sessions", headers=AUTH).json()["sessions"]]
    assert "named:ghost" not in names
```

- [ ] **Step 4: Implement** — the listing filters keys that `flows.library.valid_session_name` rejects. No migration: the live keys carry TTLs and expire.

- [ ] **Step 5: Read every document end to end** against the shipped contract — README, AGENTS, CONTRIBUTING, copilot-instructions, all seven skill files, and the hand-written wiki pages (`Home`, `Installing`, `Deployment`, `Administration`, `Sessions`, `Flows`, `Files`, `Secrets`). Not a keyword skim: the #35 sweeps found stale wording three times, each after the previous sweep reported done.

- [ ] **Step 6: Regenerate the wiki, push the submodule first**

```bash
PYTHONPATH=$DEPS:. python3 scripts/generate_wiki.py
cd wiki && git add -A && git commit -m "..." && git push origin master && cd ..
```

- [ ] **Step 7: CHANGELOG** — one short line per user-visible entry under `[Unreleased]`, in the existing Added/Changed/Fixed blocks. The restructure itself earns no line; the `current_session` guidance shape does.

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "Every document read end to end against what ships"
```

---

### Task 12: The saga, and proof

**Files:**
- Modify: `saga/Chapter_2_Pilot_Reports.md`
- Verify: everything

- [ ] **Step 1: Record the refit in the saga** — what moved and why, the four-wrappers-into-one collapse, the measured before/after description bytes, the `x-mcp` idea and why it was declined (FastMCP 4.0.3 reads no such extension), and the blocking-handlers item that remains open.

- [ ] **Step 2: Full verification**

```bash
PYTHONPATH=$DEPS:. python3 -m pytest -q -p no:cacheprovider tests --ignore=tests/integration
PYTHONPATH=$DEPS:. python3 scripts/generate_openapi.py && npx --yes @redocly/cli@2 lint openapi.yaml
PYTHONPATH=$DEPS:. python3 scripts/generate_wiki.py --check
pip wheel --no-deps -w /tmp/w . && unzip -l /tmp/w/*.whl | grep -cE "\.py$"
git diff -U0 origin/main -- '*.py' | grep '^+' | awk 'length > 89'
```
Expected: suite green, lint clean, wiki current, wheel carries every module, no over-length line.

- [ ] **Step 3: Compare against `main`** — `git diff origin/main --stat` should show moves and prose, and **no change to any test assertion**. Spot-check three test files to confirm only import lines moved.

- [ ] **Step 4: Commit and open the PR**

```bash
git add -A && git commit -m "The saga records the refit"
git push -u origin chore/refit-and-docs-pass
gh pr create --title "The refit: rooms in the package, one way to answer a request" --body "..."
```

---

## Self-review

**Spec coverage:** layout → Tasks 2–7; one answer → Task 8; annotations rename → Task 6; guidance + INSTRUCTIONS → Task 9; neutral prose → Task 10; docs findings → Task 11; orphan rows → Task 11; packages guard → Task 1; saga → Task 12. No spec section is unimplemented.

**Placeholders:** none. Every code step carries the code; every verification step carries the command and the expected result.

**Type consistency:** `answer(request, token, what, call, *, body=True)`, `as_response(exc, what)`, `mount(mcp, prefix, table, handler)` and `guidance.pointer(page, section, prompt)` are used under those exact names in Tasks 8, 9 and 11.

**One deviation from the spec, recorded deliberately:** the spec's tree put `js/` inside `core/`. It goes there, but the move is only safe because Task 2 moves the `package-dir` key with it — the module loads its files from the sibling directory of the same name, and a wheel that keeps the old mapping serves no JavaScript with no error anywhere. Task 2 Step 6 asserts the wheel contents for exactly this reason.
