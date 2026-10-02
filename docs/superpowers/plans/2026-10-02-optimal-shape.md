# The Optimal Shape Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor selenium-flow so that every capability is one call through five owned stages (Caller, Capability, Recipe, Settle, Surface), delete the repetition that grew round by round, take the measured performance wins, and close the security gaps the review found, with no regression in any behaviour.

**Architecture:** Lock first (characterisation tests, goldens, parity, a JS harness, one fakes module), then move pure rules into framework-free modules behind a boundary test, then state the per-action recipe, the post-action settle and the secret bind once each, then generate both surfaces, the spec and the wiki from one capability table. Performance and security work ride on the seams this creates.

**Tech Stack:** Python 3.10+, FastMCP 4, Selenium 4 (WebDriver + BiDi), Starlette, Redis, pytest; Svelte 5, Vite 8, vitest 5.

**Spec:** `docs/superpowers/specs/2026-10-02-optimal-shape-design.md` — read its "The core abstraction", "Behaviour contract", "Plan of record" and "Rulings" before any task.

## Global Constraints

- No published shape changes: tool names, parameters, descriptions, annotations, HTTP paths, bodies, result keys, URIs, error sentences an agent reads, the OpenAPI document. The goldens of Task 3 are the proof; a golden is never updated in a commit that moves code.
- Every rule in `AGENTS.md` holds; a rule may move where it is enforced, never whether.
- `tests/` changes only by import path in a refactor commit; the pass count never drops below 1572 (unit) and 205 (vitest). Each task ends green: `ruff check .`, the unit suite, and for UI tasks `npm --prefix ui run check && npm --prefix ui run lint && npx vitest run`.
- Unit suite command in the pod: `cd /projects/modules/selenium-flow && S=/tmp/claude-1000/-projects-cluster/2222ebcd-ef22-431d-92ea-d22a6044b628/scratchpad; PYTHONPATH=$S/deps:. python3 -S -m pytest -q -m "not integration" tests --deselect tests/test_wiki.py::test_the_generated_pages_are_current -p no:cacheprovider`. Ruff: `$S/tools/bin/ruff check .`. Never run `tests/integration` in the pod.
- No back-compat shims for the one user (AGENTS.md); a deleted name is deleted, and its callers are updated in the same commit.
- Every blind `except` carries `# noqa: BLE001` with its reason. Line length 88. `pathlib` over `os.path`.
- Commits: one per task, message in the repo's voice (what changed and why, no "feat:"), trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Research reports with file:line evidence for every task are under `.superpowers/round3/understand/` (gitignored); a task names the report sections it draws on.

---

## Part A — Lock

### Task 1: One fakes module

**Files:** Create `tests/fakes.py`. Modify `tests/conftest.py`, `tests/test_kept_files.py`, `tests/test_flowrun.py`, `tests/test_run_progress.py`, `tests/test_sessions.py`, `tests/site_data_fakes.py`, `tests/test_spare_tab.py`, `tests/test_skill.py`, `tests/test_file_surfaces.py`, `tests/bench/test_session_store.py`. Evidence: `tests-ci.md` §2.

**Produces:** `tests.fakes.FakeRedis` (with `FakePipeline`, `scan_iter`, `mget`, `set(ex=)`, `get`, `delete`, `watch/multi/execute`, `ttl`), `FakeGrid`, `FakeActions`, `FakeClock`, `FakeBidi`, `FakeTabs`; `conftest` re-exports `RecordingActions`, `FakeGrid`, `manager`, `http`.

- [ ] Diff every duplicate pair (`FakeRedis` at `test_sessions.py:418`, `bench/test_session_store.py:17`; `FakeGrid` at `conftest.py:54`, `test_kept_files.py:81`; `FakeActions` at `test_flowrun.py:16`, `test_kept_files.py:97`; `FakeClock` at `test_run_progress.py:148`, `test_flowrun.py:249`; `FakeBidi`/`FakeTabs` at `site_data_fakes.py:69,77` vs `test_spare_tab.py:25,85`) and write the union of their behaviours into `tests/fakes.py`, one class each, docstring naming what production method it answers.
- [ ] Replace each copy with an import; delete the `http()` redefinitions in `test_skill.py:50` and `test_file_surfaces.py:30` in favour of `conftest.http`.
- [ ] Run the suite and the bench (`pytest tests/bench -q`): same pass counts (1572; bench collects the same cases).
- [ ] Mutation check: break `FakeRedis.mget` to return `[]`; the same tests that failed before the merge fail now (record which in the report); revert.
- [ ] Commit: `Tests: one fakes module, no second copy of any double`.

### Task 2: Characterise the critic's fifteen — core and browser

**Files:** Test: `tests/test_actions_contract.py` (new), `tests/test_pointer.py`, `tests/test_assert.py`. Evidence: `critique.md` §3 items 1–6; `core-browser.md` §1 ids X1, K6, X3, N1–N3, I2, I8, I9, S3, S4, S10, F2, G1, U5, U6, C2.

Use `RecordingActions`/`FakeGrid` and a scripted fake driver (add `ScriptedDriver` to `tests/fakes.py`: records every attribute access and call in order; `current_url`/`title` may raise `UnexpectedAlertPresentException`; `switch_to.alert.text` returns a canned string; `execute_script` returns canned values by script substring).

- [ ] `upload_file`: a missing `path` is a 400 with the sentence it emits today; the temp directory is removed after success and after failure; an unwritable `TMPDIR` is a `RuntimeError` naming it (U5, U6).
- [ ] An open dialog is reported as page state, never raised, by every action's `page_state` (X1); `settled()` expires as a normal outcome after `NAVIGATION_SETTLE` (K6).
- [ ] `navigate`, `extract`, `execute_script` result shapes, key by key (N1–N3); `ensure_url` skips the navigation when the browser is already there (X3).
- [ ] `interact`: a refused argument never calls `_at` (I2) — parametrise over every action with the pattern of `test_an_impossible_hold_is_refused_before_the_browser_is_touched`, marking with `xfail(strict=True)` the four that validate after reconnect today (`frame`, `screenshot`, `extract`, `outline`; Task 13 turns them green); presence vs clickable wait (I8); `nudged`/`unglideable` notes (I9).
- [ ] `open_session` window size and timeouts replay (S3, S4); `end_browser` forgets the pointer (S10); `frame` index default and `in_frame` (F2); `dialog(read)` leaves the prompt open and returns its text (G1); `screenshot`'s three capture modes restore the window in `finally` (C2).
- [ ] Each test, broken on purpose once (flip the assertion's subject in the code), fails; record the line you broke in the report.
- [ ] Commit: `Tests: pin what the browser layer promises before anything moves`.

### Task 3: Characterise the critic's fifteen — flows, session, HTTP, boot

**Files:** Test: `tests/test_flowdoc.py`, `tests/test_flowrun.py`, `tests/test_flows.py`, `tests/test_sessions.py`, `tests/test_routes.py`, `tests/test_errors.py`, `tests/test_main.py` (new). Evidence: `critique.md` §3 items 7–13, 15; `flows.md` §1 D19, D20, R9, D8, D13, R27, X1, S21; `session-store.md` N8–N12, M36, M31, S12, S5, S25.

- [ ] Hand-edited flow shapes that 500 today (`required: "email"`, a list `parameters`, an unhashable step `id`, a string `steps`): pin the current status with `xfail(strict=True)` and the expected 400 sentence in the test body (Task 16 flips them).
- [ ] `assert` is never continued past, at save and at run (D13, R27); `args.secret` is popped and the page read only when bound (X1); a flow save is not atomic today — pin that a crash mid-write leaves the old document unreadable? No: pin only the observable, the saved document reads back byte-equal (S21), so the atomic write of Task 10 has a test to keep.
- [ ] Request naming: surrounding whitespace, case, a repeated `?session=a&session=b` (today: last wins — pin it, the spec changes it in Task 7 so mark `xfail(strict=True)` with the new sentence), `describe()`'s `named_by` on each surface (M36), `browser()` (M31).
- [ ] HTTP status table end to end through the ASGI app for every `errors.py` class, including `StoreConflict` → 500 (S12) and a non-numeric `opened_at` (S5).
- [ ] `tests/test_main.py`: `main()` with an unknown config key exits non-zero printing the key; Selenium's wire loggers are at INFO after boot; `auth=off` is logged when no token (runs `main` in-process with `uvicorn.run` monkeypatched to record its args).
- [ ] Record today's interleaving of two concurrent calls on one session (two threads, `RecordingActions` with a barrier): both run; assert only that both complete and the record is consistent (Task 23 changes the ordering and will edit this one test — the only test a later task may edit).
- [ ] SSE: with three connected `/admin/events` clients, each receives the same payload sequence and `last` semantics (count `grid.status` calls: today 3 per tick — pin the count with a comment; Task 18 edits that number).
- [ ] Commit: `Tests: pin the flow, session, HTTP and boot edges`.

### Task 4: Golden shapes and surface parity

**Files:** Create `tests/golden/` (JSON), `tests/test_golden.py`, `tests/golden_tools.py` (helpers: normalise, load, compare with a readable diff). Evidence: `research-testing-ui.md` §A; `mcp-spec.md` §1.

- [ ] Snapshot, via the real server object (`SeleniumMCP`) and the FastMCP client in-process: every tool's `name`, `description`, `inputSchema`, `annotations`, for both client modes (`?resources=on` and `off`), to `tests/golden/tools-<mode>.json`.
- [ ] Snapshot `build_spec()` to `tests/golden/openapi.json` with `info.version` normalised.
- [ ] Snapshot the run reports of the `test_flowrun` fixture matrix (SIMPLE, guarded, failing, continue, out-of-time, cancelled, redacted URL, file-producing) with `FakeClock` so times are stable, to `tests/golden/flow-reports.json`.
- [ ] Snapshot `/admin/sessions` for a canned `MemoryStore` of three records (one detached, one with site data, one with a reopen report) with `rev` fields kept and signed URLs replaced by `<signed>`, to `tests/golden/admin-sessions.json`.
- [ ] Snapshot every error envelope: raise each `errors.py` class through a `/browser/navigate` call and record status + body, to `tests/golden/errors.json`.
- [ ] Parity: for `navigate`, `extract`, `write`, `interact`, `screenshot(save=False)`, `save_site_data`, call the tool and the endpoint with the same args against `RecordingActions`; the normalised results are equal (the screenshot's image block vs base64 is the one sanctioned difference: compare after decoding).
- [ ] Mutation check: change one tool description character; `test_golden` fails naming the tool; revert.
- [ ] Commit: `Tests: golden shapes for every published surface, and parity between the two`.

### Task 5: A harness for `js/*.js`

**Files:** Create `tests/js/run.mjs`, `tests/test_js_behaviour.py`. Modify nothing in `js/`. Evidence: `core-browser.md` §1.8 O4, §2.7.

- [ ] `tests/js/run.mjs`: loads `ui/node_modules/jsdom`, builds a document from an HTML string argument, evaluates a named script from `js/` with `arguments` bound the way WebDriver binds them, prints the return as JSON. Skips (pytest `skip`) when `node` or `ui/node_modules/jsdom` is absent, the way `test_every_script_parses` does.
- [ ] Pin: `helpers.js` finds the interactive ancestor of a text node; `center.js` returns the element's centre and viewport; the probe's `explain` payload for a covered element; the outline's heading list for a canned page; the site-data fill script writes the given keys and nothing else (O4 list in `core-browser.md` §1.8).
- [ ] Commit: `Tests: the page scripts run under jsdom, so a change to them is provable`.

## Part B — The caller edge

### Task 6: `Caller.from_request`, multi-valued, passed in

**Files:** Modify `kubed/selenium_flow/session/sessions.py` (`Caller`, `http_request`, `name_in`, `requested`, `name_from`, `library_from`), `session/settings.py` (`resolve` takes `client=`), `mcp/clients.py` (`declared` reads through the same reader), `mcp/tools.py`, `routes.py`, `http/files.py`, `flows/api.py`, `secrets.py`, `server.py`, `tests/conftest.py` (`named_caller`/`unnamed_caller` pass a `Caller`). Evidence: `session-store.md` §5.4; `mcp-kb-compare.md` "Multi-value request reads"; `research-python-fastmcp.md` (CurrentRequest/CurrentHeaders).

**Produces:** `Caller.from_request(params: dict[str, list[str]], headers: dict[str, list[str]]) -> Caller`, `Caller.stdio()`, `Caller.name`, `.library`, `.named_by`, `.client` (the `clientInfo` name or `""`), `.flags` (declared `resources`/`apps`); `request_values() -> tuple[dict[str, list[str]], dict[str, list[str]]] | None` (one FastMCP dependency walk, no dead ImportError guard); `SessionManager.describe(caller)`, `.act(caller, call)`, `.open_browser(caller, ...)`, `.end_browser(caller)` etc. take a `Caller` and never call `requested()`.

- [ ] Write the failing tests: a repeated `?session=a&session=b` is refused with `the request names two sessions: a, b` (replace the Task 3 xfail); header beats nothing — header plus param is still the existing 400; off HTTP `Caller.stdio()`; `settings.resolve(client=None)` gives no client defaults.
- [ ] Implement `Caller.from_request` and `request_values`; make every entry point resolve the caller once (tools: in the `run()` closure; routes: from the Starlette request; flows/api, files, secrets: parameter) and pass it down. Delete `requested`, `name_in`, `name_from`, `library_from`, `clients.declared`'s own parsing (it takes the `Caller`), and the lazy imports that existed to break the cycle.
- [ ] `conftest.named_caller` monkeypatches `request_values` only (one seam), returning list-valued dicts.
- [ ] Suite green; goldens unchanged; commit: `Sessions: the caller is read once at the edge and handed in`.

## Part C — Pure modules out

### Task 7: `names.py` and `urls.py`

**Files:** Create `kubed/selenium_flow/names.py`, `kubed/selenium_flow/urls.py`. Modify `flows/library.py`, `session/store.py`, `secrets.py`, `core/site_data.py`, `core/actions.py`, `errors.py`, `core/browser.py`, `flows/run.py`, `config.py`. Evidence: `flows.md` §5.4; `session-store.md` §5.3; `mcp-kb-compare.md` "Scrub in one place".

**Produces:** `names.NAME`, `FILE_NAME`, `InvalidName`, `valid_name`, `valid_session_name`, `valid_file_name`, `GLOBAL_SESSION`, `STDIO_SESSION`, `RESERVED_SESSIONS`, `library_of`; `urls.origin_of`, `host_of`, `page_of`, `scrub` (the one userinfo scrubber), `normalize_url`, `public_url`.

- [ ] Move, do not rewrite. `secrets.origin` becomes `urls.origin_of` after `test_origin_keeps_scheme_host_and_port_and_nothing_else` and `secrets._origin`'s default-port handling are shown equal (write the one test that proves it first).
- [ ] The three scrubbers (`errors.py:129`, `core/browser.py:153`, `flows/run.py:228`) call `urls.scrub`; the `USERINFO` regex exists once (`config.py:75` imports it).
- [ ] `core/` no longer imports from `flows`; `grep -rn "from ..flows.library import valid_" kubed` returns nothing outside `flows/`.
- [ ] Commit: `Names and URLs: the identity and origin rules in two modules of their own`.

### Task 8: The core package of pure modules

**Files:** Create `core/keys.py`, `core/naming.py`, `core/coerce.py`, `core/assertion.py`. Modify `core/actions.py`, `core/browser.py`, `mcp/tools.py`, `routes.py`, `flows/run.py`, tests' import paths only. Evidence: `core-browser.md` §5 R1, §2.1, §2.5.

- [ ] `keys.py`: the key vocabulary (K1–K4) and `SUBMIT_KEYS`; `naming.py`: `_safe_name` → `safe_name`, `EXTENSIONS`, the capture/print file naming; `coerce.py`: `as_bool`, `as_int`, `_seconds` → `seconds`; `assertion.py`: the `assert_` poll/hold engine as a class taking `evaluate()` and a clock.
- [ ] `spare_tab`, `SPARE_*`, `ServiceWorkerAnswered` move beside site data (Task 9 takes them; here they move to `core/spare.py`).
- [ ] `browser.py` keeps `Grid`, `ReattachDriver`, the waits, `locator`, `page_state`. Delete `Actions.files`, `Actions.clear_files`, `Grid.timeout` (or wire it to config: ruling — delete), the list fallback in `probe.outline` after fixing the `_Page` double in tests.
- [ ] Commit: `Core: the pure rules in their own modules, and the vestiges gone`.

### Task 9: Site data: model apart from transfer

**Files:** Create `kubed/selenium_flow/site_data/__init__.py`, `site_data/snapshot.py`, `site_data/transfer.py`, `site_data/spare.py`. Delete `core/site_data.py`, `core/spare.py`. Modify importers (`session/sessions.py`, `mcp/resources.py`, `spec/builder.py`, `http/admin.py`, `core/actions.py`, `tests/site_data_fakes.py`, tests' import paths). Add `kubed.selenium_flow.site_data` to `pyproject.toml` packages. Evidence: `session-store.md` §5.3, §5.7, §5.10.

- [ ] `snapshot.py`: `snapshot`, `forget`, `restorable`, `live_cookies`, `_Jar`, `view`, `views`, `site_view`, `summary`, the constants (`LIST_URI`, `CAPTURED`, `MASK`, `SW_REASON`, `LEFT_OUT`, `MAX_BYTES`) with their values unchanged; `view()`/`summary()` count from `_Jar` without building details (`test_site_data_views.py` unchanged, plus one new count test on `_cookie_view`).
- [ ] `transfer.py`: `capture`, `restore`, the fill/read scripts, `BidiUnavailable`; `spare.py`: the spare tab. `matching_secrets` moves to `secrets.py`.
- [ ] `SessionRecord.at()` splits into `visited(*urls, now)` and `pruned(now, ttl)`; `remember` uses `dataclasses.replace` so a new field survives a bind (`test_an_open_drops_a_report_nobody_collected` still passes: `reopened` reset explicitly).
- [ ] `test_packaging.py` passes with the new package; commit: `Site data: the model in one module, the BiDi transfer in another`.

### Task 10: Flows: template, redact, engine, report; two stores

**Files:** Create `flows/template.py`, `flows/redact.py`, `flows/engine.py`, `flows/report.py`, `flows/shape.py` (Task 16 fills it; create the skeleton here only if needed), `flows/store.py`. Modify `flows/run.py` (a façade: `run()` keeps its signature), `flows/library.py`, `flows/api.py`, `secrets.py` (imports `flows.redact`, not `flows.run`), `http/admin.py`. Evidence: `flows.md` §5.1, §5.5, §5.6.

**Produces:** `template.PARAM_REFERENCE, references, substitute, with_defaults, check_params`; `redact.HIDDEN, hidden_forms, scrub, scrub_values, taints, redacted_fields`; `engine.Run` (deadline, stop, seen, pages, was_at, reports), `engine.Hooks` protocol (`before_step`, `after_step`, `before_save`), `engine.StepOutcome`; `report.summarise, hint_for, with_hint`; `store.SessionLayout`, `FlowStore`, `FileStore` (the fourteen `LocalFlowStore` methods split by what they hold, fd-pinning listing moved unchanged); flow saves atomic via tmp + `os.replace`, `CSafeDumper`.

- [ ] Golden `flow-reports.json` (Task 4) is the gate: byte-equal after the split. `run.py` imports neither `routes` nor `mcp.guidance`: `RUNNABLE`/`method_for` and the pointer function are passed in by `flows/api.py`.
- [ ] Keep `flowrun.listed`, `flowrun.HIDDEN`, `flowrun.time` re-exported from `run.py` (the tests monkeypatch them) — the one re-export the round allows, because the alternative edits 40 tests.
- [ ] Cheap wins from `flows.md` §5.6: `_document_schema` cached; `DOCUMENT_KEYS` shared by the MCP tool, the HTTP PUT and the schema; `FLOW_ENDPOINTS` derived from `FLOW_ROUTES`; `save_flow` disk work in `anyio.to_thread.run_sync`; `summaries` without a deep copy; `test_every_field_a_save_or_read_returns_is_published` extended to the run report.
- [ ] Bench `tests/bench/test_library.py` numbers not worse (run before and after, paste both tables in the report).
- [ ] Commit: `Flows: an engine, a template, a redactor and a reporter, and a store per kind`.

### Task 11: The admin package

**Files:** Create `http/admin/__init__.py` (`register`, same name), `http/admin/sessions.py` (`sessions_payload`, the row builders, the events stream), `http/admin/flows.py`, `http/admin/files.py`, `http/admin/site_data.py`, `http/admin/page.py` (`page()`, `static_path`), `http/admin/signed.py` (one signed-route factory). Delete `http/admin.py`. Modify `http/answer.py` (gains `refused` and the one `read_body`; delete `admin.body_of`), `server.py`, tests' import paths (`_admin.static_path` → `http.admin.page.static_path`). Add the package to `pyproject.toml`. Evidence: `http-server.md` §2, §5.1.

- [ ] Before the move, write `tests/test_admin_routes.py::test_the_route_table_is_unchanged`: snapshot (method, path, name) of every admin route to `tests/golden/admin-routes.json`.
- [ ] Move; the three inline `status_for` blocks (`admin.py:994/1030/1080`) go through `answer.refused`, which scrubs the `OSError` path like the others; the three copy-paste signed routes become one factory call each.
- [ ] Admin flow PUT/GET/DELETE call `flowapi`'s save/read/delete rather than re-implementing them.
- [ ] Commit: `Admin: a package, with one error shaper, one body reader and one signed route`.

### Task 12: The boundary test and the dead code

**Files:** Create `tests/test_boundaries.py`. Modify `mcp/skill.py` (delete `read`, `manifest_json`, `MANIFEST*`, `RESOURCE_URI`), `mcp/tools.py` (delete `SELECTOR`), `mcp/guidance.py` (`PAGES` gains `SITE_DATA.md`), `mcp/apps.py:21`, `flows/api.py:258`, `flows/document.py:834` (stale names), `server.py:121` (one instructions source). Evidence: `mcp-spec.md` §2; `mcp-kb-compare.md` "Import-boundary proof".

- [ ] `test_boundaries.py`: in a subprocess with `fastmcp`, `starlette`, `mcp`, `uvicorn` blocked via a `sys.meta_path` finder, import every module under `core/` (except `browser`, `pointer`, `probe`, `spare`, which may import selenium), `session/`, `flows/` (except `api`), `site_data/snapshot`, `names`, `urls`, `errors`, `secrets`; with `selenium` also blocked, import `core/keys`, `naming`, `coerce`, `assertion`, `site_data/snapshot`, `session/store`, `flows/template`, `redact`, `engine`, `report`, `names`, `urls`. Fails naming the module and the forbidden import.
- [ ] Delete the dead code; `grep -rn` for each removed name returns nothing; `test_guidance` covers the `PAGES` addition.
- [ ] Commit: `A boundary the kernel cannot cross, and the dead code gone`.

## Part D — One recipe, one settle, one bind

### Task 13: `Recipe` under every action

**Files:** Create `core/recipe.py`. Modify `core/actions.py` (each of the fourteen actions becomes validation + a body). Evidence: `core-browser.md` §2.2, §5 R2.

**Produces:** `Recipe(reconnect, navigate=None, wait=None, retry_stale=False)` with `.run(session_id, body, url=..., selector=...) -> dict` doing coerce/validate → reconnect → optional navigate (`ensure_url`) → optional wait (`presence`|`clickable`) → body (one stale retry when asked, wrapping move+gesture for `interact`) → `page_state`; `WAIT_TIMEOUT` the one default.

- [ ] Flip the Task 2 `xfail`s: `frame`, `screenshot`, `extract`, `outline` validate before reconnect (observable only as "no navigation on a 400").
- [ ] The `CountingDriver` (add to `tests/fakes.py`) asserts the driver call order per action is unchanged for the other ten.
- [ ] Commit: `Actions: the one recipe every action follows, stated once`.

### Task 14: `Settle` owns every post-action write

**Files:** Modify `session/store.py` (`update(key, fn) -> tuple[SessionRecord | None, Any]`: `fn` returns `(record, note)`; the note is from the last run), `session/sessions.py` (`settle(caller, result, *, url=..., touch=True)` used by `act`, `_hold_again` as a plain second write; delete the five `.clear()`-ing closures), `flows/api.py` (`run_for` calls `settle`), `secrets.py` (`perform_write` calls `settle`), `http/admin/site_data.py` (forget/clear read the note). Evidence: `session-store.md` §5.1, §5.2.

- [ ] Tests named in `session-store.md` §5.1–5.2 pass unchanged; `test_history.py`'s write-count tests still count one write per flush.
- [ ] Commit: `Sessions: one settle after every action, and a write that answers`.

### Task 15: One bind-and-scrub policy

**Files:** Create `secrets_binding.py`? No — create `kubed/selenium_flow/binding.py`. Modify `flows/document.py` (`_check_step`), `flows/engine.py` (`resolve_step`), `secrets.py` (`prepare_write`, `perform_write`). Evidence: `flows.md` §5.2; `config-secrets.md` §2.

**Produces:** `binding.check_pair(args) -> list[str]`, `bind_into(kwargs, catalogue, page, tool) -> tuple[dict, Guarded]`, `after(result, hidden) -> dict`; the refusal sentences (`one value, one place`, `may not also navigate`, `pass text or secret, not both`) as module constants.

- [ ] One parametrised test feeds the same bad `args` to the three entry points and asserts all three refuse with the same sentence.
- [ ] Commit: `Secrets: the bind-and-scrub rule written once for the three places that need it`.

### Task 16: A tolerant `Shape` over a flow document

**Files:** Create `flows/shape.py`. Modify `flows/document.py` (`validate`, `_properties`, `required_params`, `with_defaults`), `flows/engine.py` (preflight), `http/admin/flows.py` (`_uses`), `flows/library.py` (`_step_count`). Evidence: `flows.md` §5.3.

- [ ] Flip the Task 3 `xfail`s: the four hand-edited shapes are 400s with the sentences pinned there; `_check_params` sorts by `str(key)`; an unhashable `id` is a problem, not a `seen` member.
- [ ] Commit: `Flows: one tolerant view of a document, and no 500 for a hand-edited one`.

## Part E — The registry

### Task 17: The `Capability` table generates both surfaces, the spec and the wiki

**Files:** Create `core/capabilities.py`. Modify `routes.py` (`ENDPOINTS`, `METHOD_ALIASES`, `ACTION_IN_PATH` become the table's columns), `mcp/tools.py` (wrappers generated: signature kept literal per tool for FastMCP's schema, body generated), `spec/builder.py`, `spec/schemas.py` (`RESPONSES` keyed by the table), `scripts/generate_wiki.py`, `tests/test_surfaces.py` (`EXPECTED` stays a hand list, by design). Evidence: `mcp-spec.md` §5.1; `core-browser.md` §2.9.

**Produces:** `CAPABILITIES: tuple[Capability, ...]` with `Capability(name, method, route, http_method, annotations, response, in_path=False)`; `capability(name)`.

- [ ] Golden `tools-*.json` and `openapi.json` byte-equal; `test_surfaces.py` unchanged; the wiki regenerates with no diff (`python scripts/generate_wiki.py --check` or the test).
- [ ] Commit: `One table of capabilities, and both surfaces, the spec and the wiki read it`.

## Part F — Performance, each with its number

### Task 18: One admin session-list broadcaster

**Files:** Modify `http/admin/sessions.py`. Test: `tests/test_admin_events.py` (new), `tests/bench/test_admin.py` (new: `sessions_payload` at 10 sessions × 1 MB). Evidence: `profile-bench.md` opp. 2; `http-server.md` §5.2.

- [ ] One task computes `sessions_payload` per interval while any stream is connected, fans the same payload out; `GET /admin/sessions` serves the cached payload when under ~1 s old. Per-stream `last` semantics and error handling unchanged; the Task 3 test's `grid.status` count becomes 1 per tick for 3 clients (edit that number only); `test_shutdown` passes.
- [ ] Commit with the bench table before/after in the message body.

### Task 19: Fewer Grid round trips

**Files:** Modify `core/browser.py` (a pooled `requests.Session` for the Grid calls, a reused connection for `ReattachDriver`; `page_state` as one `execute_script` returning `[href, title]` keeping the alert fallback; `wait_for_*` polling 0.2 s), `js/center.js` (`viewport` folded in). Evidence: `core-browser.md` §5 R4, §3.1–3.3; `profile-bench.md` opp. 3.

- [ ] `CountingDriver` asserts round trips per action before/after (table in the report); a fake Grid counts TCP connections: two reconnects share a pool; after a Grid restart the first call retries once.
- [ ] Commit: `Browser: one connection pool, one script for page state, fewer trips per action`.

### Task 20: Snapshot in O(n), cheap views, cached pages

**Files:** Modify `site_data/snapshot.py` (`snapshot`'s size cap sizes once and subtracts; the sum is an upper bound of the serialisation, separators counted), `http/admin/page.py` (`page()` cached per (name, mount, console), `Cache-Control: no-cache` with an `ETag`), `routes.py` (`/ready` cached 2 s). Test: `tests/bench/test_site_data.py` (new: 400 × 20 KB), a Hypothesis-free property test comparing the new cap to the old loop over random inputs (keep the old loop in the test file). Evidence: `profile-bench.md` opp. 4; `session-store.md` §5.8; `ui.md` §3.

- [ ] `test_a_cookie_jar_over_the_cap_raises_and_saves_nothing` and the two eviction tests unchanged.
- [ ] Commit with the 400-origin time before/after.

### Task 21: The suites themselves

**Files:** Modify `ui/vite.config.ts` (`test.pool: 'vmThreads'`, `unstubGlobals: true`, `restoreMocks: true`), `tests/test_routes.py` (a module-scoped immutable app for `test_every_endpoint_is_mounted`), `pyproject.toml` (`test` extra gains `pytest-randomly`, `pytest-xdist`; `addopts` unchanged), `.github/workflows/test.yml` (`-n 2 --dist loadfile` once three random seeds are green locally — record the seeds in the report). Evidence: `tests-ci.md` §3; `ui.md` §3; `research-testing-ui.md`.

- [ ] vitest: 205 pass twice with shuffle; duration before/after in the report. Unit suite: pass count unchanged; `--durations=10` before/after.
- [ ] Commit: `Tests run faster and in any order`.

## Part G — Security

### Task 22: No path upload, web schemes only, a frame-aware leash

**Files:** Modify `core/actions.py` (`upload_file` loses `path`; `navigate`, `open_browser` and every `url=` pass through `urls.allowed_navigation(url)` which admits `http`, `https`, `about:blank`, `data:` and refuses the rest with `only http(s), about:blank and data: URLs can be opened here; <scheme>: cannot`), `mcp/tools.py` (the `path` parameter removed from the tool), `spec/schemas.py`, `secrets.py`/`binding.py` (the leash takes the effective origin: `Actions.page` reports the frame's origin when `in_frame`, read with `document.location.origin` inside the frame), `skills/selenium-flow/references/*.md`, `wiki/notes/upload_file.notes.md`, `CHANGELOG.md`. Tests: `tests/test_actions_contract.py`, `tests/test_secrets.py`. Evidence: `security.md` F1, F2, F5; Rulings (3), (4).

- [ ] Goldens `tools-*.json`, `openapi.json` are updated in this commit and this commit only (a shape change Dr K ruled).
- [ ] Red first: a secret write inside a cross-origin frame is refused naming the frame's origin; `navigate(url="file:///etc/passwd")` is a 400 with the sentence; `upload_file(path=...)` is an unknown argument on both surfaces.
- [ ] Commit: `Security: no server-path upload, web schemes only, and the leash sees the frame`.

### Task 23: Serialise one session's browser-driving calls

**Files:** Create `session/locks.py` (a per-name lock registry, weak so an unused name costs nothing; in Redis mode still process-local — one replica, AGENTS.md "Scaling"). Modify `core/recipe.py` (acquires the session's lock around reconnect→page_state), `session/sessions.py` (`end_browser` and `open_browser` do not take it; `end_browser` sets the session's cancel from `core/cancel.py` so a running `assert` ends with its existing cancellation sentence before the browser is ended), `flows/engine.py` (the cancel flag is checked between steps, as today, and the lock is per step so a flow does not starve `end_browser`), `CHANGELOG.md`, `AGENTS.md` (a new paragraph under "A session is the thing"), saga Ch. 4 open question 2 marked answered by pointer to the spec. Tests: the Task 3 concurrency test edited to assert ordering; `end_browser` returns within one second while a 10 s `assert` holds the lock; two `navigate`s on different sessions overlap. Evidence: `profile-bench.md` "Saga Ch.4"; Ruling (2).

- [ ] Commit: `Sessions: one browser-driving call at a time per session, and end_browser still interrupts`.

### Task 24: The `security` section, headers, log and caps

**Files:** Modify `config.py` (`SecuritySettings(Section)`: `frame_ancestors: list[str] = []` described "Origins allowed to frame the admin page, e.g. a Nextcloud or Grafana host; empty forbids framing"), `http/admin/page.py` (`Content-Security-Policy: frame-ancestors 'none'` or the list, `X-Content-Type-Options: nosniff`; `Referrer-Policy: same-origin`), `server.py` (uvicorn access log through a filter that drops the query string, or `access_log=False` plus our own one-line log without the query — ruling: the filter), `main.py` (a tokenless server bound to a non-loopback address logs a WARNING with the sentence `AUTH_TOKEN is not set: anyone who can reach <host>:<port> can drive every browser`), `http/answer.py` (`read_body` caps JSON at 1 MiB and multipart at 64 MiB with a 413), `flows/library.py` (flow YAML capped at 1 MiB, 413/400 as the surface says), the Settings tab's generated wiki page, `CHANGELOG.md`. Tests: `tests/test_config_schema.py`, `tests/test_ui_serving.py` (headers), `tests/test_main.py`, `tests/test_http_answer.py`. Evidence: `security.md` F4, F7, F8; `ui.md` §4; Ruling (6).

- [ ] Commit: `Security: a frame-ancestors setting, quieter logs, a loud warning, and body caps`.

## Part H — Docs and the PR

### Task 25: Documentation follows the code

**Files:** `AGENTS.md` (TTL 86400; the five stages as a section; "where behaviour lives" now names `core/recipe.py` and `core/capabilities.py`; the lock), `CONTRIBUTING.md` ("Where things live" rows for `site_data/`, `http/admin/`, `flows/{template,redact,engine,report,shape,store}.py`, `names.py`, `urls.py`, `binding.py`, `tests/fakes.py`, `tests/golden/`), `README.md` if it names `upload_file(path=)`, `wiki/` regenerated and the submodule pushed, `CHANGELOG.md` `[Unreleased]`: `**BREAKING:** upload_file no longer takes path; send content with a filename, or a kept file's URI.` · `navigate and every url refuse non-web schemes (file:, chrome:, …).` · `One browser-driving call runs at a time per session; end_browser still interrupts a long assert.` · `security.frame_ancestors lets Nextcloud or Grafana frame the admin page.` · `The admin page and server logs no longer expose session names or signed links.`
- [ ] `python scripts/generate_wiki.py`; `test_wiki` passes; the wiki submodule is pushed before the PR.
- [ ] Commit: `Docs: the shape the code has now`.

### Task 26: The PR loop

- [ ] Push `optimal-shape`; open the PR with a body listing every behaviour change (the four changelog lines), the perf tables, and the rulings; watch `test`, `ui`, `package`, `quality`, `integration`, `bench`; answer every Copilot thread via `gh` (fix or decline with evidence) until Copilot asks for a human review.
- [ ] Build the branch tag with `image.yml`, deploy it to the cluster, drive it with selenium-flow itself (open a session on the admin page, History and Site data tabs, a flow run, `end_browser` during an `assert`), then report to Dr K.
