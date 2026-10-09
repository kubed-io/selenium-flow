# Workspaces: the rename — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every surface calls the thing a caller names a *workspace* and the live browser open in it its *session* (spec ruling 8); nothing a running deployment holds is lost.

**Architecture:** A rename in nine layers, each green on its own: identifiers first with every golden unchanged (behaviour identical), then config, disk, caller wire, resource URIs, admin HTTP, UI, skill, and docs/wiki, each with its goldens regenerated and read. Five small new behaviours refuse the old names (ruling 1, 2, 5) or move the data directory (ruling 3).

**Tech Stack:** Python 3.10–3.14, FastMCP 4, Starlette, pydantic-settings, pytest (+xdist); Svelte 5, vitest, svelte-check, eslint.

**Spec:** `docs/superpowers/specs/2026-10-09-workspaces-rename-design.md` (and its programme, `2026-10-09-workspaces-and-observability-design.md`, R1).

## Global Constraints

- **The vocabulary table in spec §1 is binding.** Old → new, exactly as written there.
- **Session, in our sense, is the live browser open in a workspace** (spec ruling 8): text about whether a browser is open says session ("no session is open in this workspace"); `open_session`, the `session` config section's browser settings, and `admin_end_session` keep it.
- **Kept, never renamed** (spec §1): `session_id` and every Grid use (`/session/<id>`, `Grid.sessions()`, `session_count`, `is_alive`, `SE_NODE_SESSION_TIMEOUT`, "the Grid's session list"); the MCP transport (`Context.session_id`, `Mcp-Session-Id`, `mcp.session.id`, "transport session"); the browser's sessionStorage (`sessionStorage`, `session_storage`, site data's `"session"` key in `site_data/snapshot.py` and `site_data/transfer.py`, "Session storage", the "session" cookie expiry, `SavedCounts.session` in `ui/src/lib/types.ts`); the tool names `open_session` and `end_browser`, `Actions.open_session`, and the title "Open browser session"; pytest `scope="session"`, `start_new_session`, `requests.Session`, `client.session`.
- **Persisted shapes do not change:** the record's `session_id` field and site data's `"session"` key stay as they are serialised.
- **Refusal texts, verbatim:**
  - `` `?session=` is now `?workspace=`: rename it in the URL ``
  - `` `X-Session-Key` is now `X-Workspace`: rename the header ``
  - `<OLD> is now <NEW>: workspace settings moved out of session` (one name), `<OLD1>, <OLD2> are now <NEW1>, <NEW2>: workspace settings moved out of session` (several, sorted) — only `SESSION_STORE`, `SESSION_TTL`
  - ``the config file's `session.store` and `session.ttl` are now `workspace.store` and `workspace.ttl` ``
  - `` `session://<rest>` is now `workspace://<rest>` ``
- **Comments and docstrings move with the code they describe**; a docstring that explains *our* session says workspace, one about the Grid's keeps its word.
- **CHANGELOG:** one BREAKING line under `[Unreleased]` only (Task 9).

## Commands

Local runs use the source tree ahead of a system `kubed` package (a shim in the deps dir makes it win):

```bash
export R=/projects/kubed-io/selenium-flow
export D=/tmp/claude-1000/-projects-kubed-io-selenium-flow/55f277e2-9886-4a71-8158-035ac51251f5/scratchpad/deps
export PYTHONPATH=$R:$D
# whole unit suite (~45 s)
python3 -m pytest -q -p no:randomly -n 4 --ignore=tests/integration --ignore=tests/bench
# one file
python3 -m pytest -q -p no:randomly tests/test_x.py
# goldens: rewrite, then READ the diff before committing
GOLDEN_UPDATE=1 python3 -m pytest -q -p no:randomly tests/test_golden.py
python3 -m ruff check kubed tests scripts
# UI
npm --prefix ui test && npm --prefix ui run -s check && npm --prefix ui run -s lint
```

CI runs plain `pytest`, `ruff check .`, `ruff format --check .`, and the UI build/test.

Baseline at `6e925d5`: 2344 passed, 20 skipped; UI 272 passed; svelte-check, ruff clean.

---

## File Structure

| Path (after) | Was | Responsibility |
|---|---|---|
| `kubed/selenium_flow/workspace/` | `session/` | the package: `workspaces.py` (was `sessions.py`), `store.py`, `settings.py`, `locks.py` |
| `kubed/selenium_flow/http/admin/workspaces.py` | `http/admin/sessions.py` | admin list, end, event stream |
| `kubed/selenium_flow/names.py` | same | `WORKSPACES_DIR`, `valid_workspace_name`, `retired_uri` (new) |
| `kubed/selenium_flow/config.py` | same | `WorkspaceSettings`, section `workspace`, retired `SESSION_*` |
| `kubed/selenium_flow/flows/store.py` | same | `WorkspaceLayout`, the one-time move |
| `tests/test_workspaces.py` | `tests/test_sessions.py` | the caller and manager tests |
| `tests/test_site_data_workspaces.py` | `tests/test_site_data_sessions.py` | |
| `tests/bench/test_workspace_store.py` | `tests/bench/test_session_store.py` | |
| `tests/golden/admin-workspaces.json` | `admin-sessions.json` | |
| `tests/integration/flows/open-this-workspace.yaml`, `sign-in-and-find-this-workspace.yaml` | `…-session.yaml` | |
| `ui/src/lib/WorkspaceList.svelte`, `WorkspaceSummary.svelte`, `ui/src/admin/WorkspaceDetail.svelte`, `WorkspacesView.svelte`, `workspace.svelte.ts` (+ tests) | `Session*`, `session.svelte.ts` | |
| `skills/selenium-flow/references/WORKSPACES.md` | `SESSIONS.md` | |
| `wiki/Workspaces.md`, `wiki/current_workspace.md` | `Sessions.md`, `current_session.md` | |

---

### Task 1: The package and identifiers, no wire change

**Files:**
- Move: `kubed/selenium_flow/session/` → `kubed/selenium_flow/workspace/`; `workspace/sessions.py` → `workspace/workspaces.py`; `kubed/selenium_flow/http/admin/sessions.py` → `kubed/selenium_flow/http/admin/workspaces.py`
- Modify: every importer (`routes.py`, `server.py`, `secrets.py`, `core/recipe.py`, `core/pointer.py`, `flows/engine.py`, `flows/store.py`, `flows/api.py`, `http/answer.py`, `http/files.py`, `http/links.py`, `http/admin/*.py`, `mcp/resources.py`, `mcp/tools.py`, `mcp/clients.py`, `recordings/collector.py`, `names.py`, `config.py` (class name only), `main.py`), `pyproject.toml` (packages list)
- Move tests: `tests/test_sessions.py` → `tests/test_workspaces.py`, `tests/test_site_data_sessions.py` → `tests/test_site_data_workspaces.py`, `tests/bench/test_session_store.py` → `tests/bench/test_workspace_store.py`
- Modify tests: every file importing the package or the renamed names (`conftest.py`, `test_boundaries.py`, `test_history.py`, `test_locks.py`, `test_pointer.py`, `test_routes.py`, `test_golden.py`, `test_guidance.py`, `test_recording_*`, `test_site_data_*`, `test_admin_*`, `test_kept_files.py`, `test_files_and_admin.py`, `test_secrets.py`, `test_browser_choice.py`, `test_screenshot_saving.py`, `bench/test_admin.py`, …)

**Interfaces:**
- Produces (later tasks rely on these exact names):

| Old | New |
|---|---|
| package `kubed.selenium_flow.session` | `kubed.selenium_flow.workspace` |
| module `session.sessions` | `workspace.workspaces` |
| module `http.admin.sessions` (imported as `session_list`) | `http.admin.workspaces` (imported as `workspace_list`) |
| `SessionManager` | `Workspaces` |
| `SessionRecord` | `Workspace` |
| `SessionStore` (Protocol) | `WorkspaceStore` |
| `SessionSettings` (config class; the field `session` on `Settings` stays until Task 2) | `WorkspaceSettings` |
| `SessionLayout` (flows/store.py) | `WorkspaceLayout` |
| `_SESSION_MARKS` | `_WORKSPACE_MARKS` |
| `GLOBAL_SESSION`, `STDIO_SESSION`, `RESERVED_SESSIONS` | `GLOBAL_WORKSPACE`, `STDIO_WORKSPACE`, `RESERVED_WORKSPACES` |
| `valid_session_name` | `valid_workspace_name` |
| `SESSIONS_DIR` (value stays `"sessions"` until Task 3) | `WORKSPACES_DIR` |
| `current_session_resource` | `current_workspace_resource` |
| `sessions_payload`, `session_list`, `session_settings` (module alias) | `workspaces_payload`, `workspace_list`, `workspace_settings` |
| attribute/param `sessions` holding the manager (`self.sessions`, `register(..., sessions)`, `routes(..., sessions)`) | `workspaces` |
| `Owed.session` (collector), locals/params named `session` holding a workspace **name** (flows/store.py, http/files.py, flows/api.py, http/links.py, http/admin/*, secrets.py, recordings/*) | `workspace` |
| helpers `_session_dir`, `_session_notes` | `_workspace_dir`, `_workspace_notes` |

- **Not in this task** (later tasks own them): the `session` field on `Settings` and env names (Task 2); the folder's value (Task 3); `NAME_PARAM`, `NAME_HEADER`, refusal texts, result keys, `SESSION_PARAMETERS`, `_SESSION`, `_WRITE_SESSION`, `library_arg="session"` (Task 4); URIs (Task 5); admin route paths and route names `admin_sessions`, `admin_end_session` (Task 6).

- [ ] **Step 1: Move the test files and point every test import at the new names**

```bash
cd $R
git mv tests/test_sessions.py tests/test_workspaces.py
git mv tests/test_site_data_sessions.py tests/test_site_data_workspaces.py
git mv tests/bench/test_session_store.py tests/bench/test_workspace_store.py
```

In every test file, replace the imports and uses per the Interfaces table (e.g. `from kubed.selenium_flow.session.sessions import Caller, values_of` → `from kubed.selenium_flow.workspace.workspaces import Caller, values_of`; `SessionRecord(` → `Workspace(`; `SessionSettings` → `WorkspaceSettings`; `RESERVED_SESSIONS` → `RESERVED_WORKSPACES`). In `tests/test_boundaries.py` the package walked and named is `workspace` (`walk("workspace")`, `"workspace.store"`, the `KERNEL` entries). Rename test **functions** whose name means our session (`test_..._session...` → `..._workspace...`), leaving ones about the Grid's or the browser's.

- [ ] **Step 2: Run to see it fail**

Run: `python3 -m pytest -q -p no:randomly -x --ignore=tests/integration --ignore=tests/bench`
Expected: collection errors, `ModuleNotFoundError: No module named 'kubed.selenium_flow.workspace'`.

- [ ] **Step 3: Move the package and rename the identifiers**

```bash
git mv kubed/selenium_flow/session kubed/selenium_flow/workspace
git mv kubed/selenium_flow/workspace/sessions.py kubed/selenium_flow/workspace/workspaces.py
git mv kubed/selenium_flow/http/admin/sessions.py kubed/selenium_flow/http/admin/workspaces.py
```

Then apply the Interfaces table across `kubed/`. Use word-boundary replacements for the class and constant names (they are unique), and rename `sessions`/`session` **parameters and attributes by reading each hunk** — never a blind `s/session/workspace/`: `session_id` is the Grid's and appears beside them everywhere. In `pyproject.toml` the packages list entry `kubed.selenium_flow.session` → `kubed.selenium_flow.workspace`. Docstrings and comments that describe our record say *workspace*.

- [ ] **Step 4: Run to see it pass, goldens untouched**

Run: `python3 -m pytest -q -p no:randomly -n 4 --ignore=tests/integration --ignore=tests/bench`
Expected: 2344 passed, 20 skipped (the same as baseline). **No `GOLDEN_UPDATE`** in this task: any golden diff means wire behaviour changed, which this task must not do.
Run: `python3 -m ruff check kubed tests scripts` → clean.
Run: `git grep -n -E "SessionManager|SessionRecord|SessionStore|SessionSettings|SessionLayout|valid_session_name|GLOBAL_SESSION|STDIO_SESSION|RESERVED_SESSIONS|SESSIONS_DIR|selenium_flow\.session\b|selenium_flow/session/" -- kubed tests pyproject.toml` → no output.

- [ ] **Step 5: Commit**

```bash
git add -A kubed tests pyproject.toml
git commit -m "Workspaces: the package and its names, no wire change

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Config, Redis prefix and the ops endpoints

> **Amended 2026-10-09 (spec rulings 8–9), sent to the implementer mid-task:** the `session` section is **split**, not renamed. A new `workspace` section holds only `store` and `ttl` (class `WorkspaceSettings`, "How workspaces are kept."); `session` keeps `browser`, `width`, `height`, `page_load_timeout`, `script_timeout` (a new class `SessionSettings`, "How a session opens a browser."; `SESSION_*` env names for those unchanged). The refusal covers only `SESSION_STORE`/`SESSION_TTL` and `session.store`/`session.ttl` in the file (`config.RETIRED_SESSION_KEYS`), with the texts in Global Constraints. Where the code and tests below say otherwise, this note wins.

**Files:**
- Modify: `kubed/selenium_flow/config.py` (the `session` field on `Settings` → `workspace`; section description; `RedisSettings.prefix` default; `sources["session.store"]` → `"workspace.store"`; `_retired`; `RETIRED_SESSION_SECTION`), `kubed/selenium_flow/workspace/settings.py` (`from_settings(workspace: WorkspaceSettings)`), `kubed/selenium_flow/workspace/store.py` (`DEFAULT_PREFIX = "selenium-flow:workspace:"`, `from_settings(workspace: …)`, the log lines "workspace store: …"), `kubed/selenium_flow/server.py` (`settings.workspace`), `kubed/selenium_flow/main.py` (log field `workspaces=%s`), `kubed/selenium_flow/routes.py` (`/ready`, `/info`: `"workspaces": workspaces.kind`), `kubed/selenium_flow/spec/schemas.py` (the `/ready` and `/info` schemas' `sessions` property → `workspaces`)
- Test: `tests/test_config_load.py`, `tests/test_config_schema.py`, `tests/test_config_wiring.py`, `tests/test_pointer.py`, `tests/test_workspaces.py` (prefix test), `tests/test_routes.py` (ready/info), golden `tests/golden/openapi.json`

**Interfaces:**
- Consumes: `WorkspaceSettings` (Task 1).
- Produces: `Settings.workspace: WorkspaceSettings`; env `WORKSPACE_STORE|TTL|BROWSER|WIDTH|HEIGHT|PAGE_LOAD_TIMEOUT|SCRIPT_TIMEOUT`; flags `--workspace-*`; `config.RETIRED_SESSION_SECTION`; `/ready` and `/info` carry `"workspaces"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_load.py`:

```python
def test_the_workspace_section_reads_from_env():
    settings = config.load([], {"WORKSPACE_TTL": "60"}).settings
    assert settings.workspace.ttl == 60


def test_one_session_env_name_stops_the_boot_naming_its_workspace_name():
    with pytest.raises(config.ConfigError) as exc:
        config.load([], {"SESSION_TTL": "60"})
    assert str(exc.value) == (
        "SESSION_TTL is now WORKSPACE_TTL: the session settings are workspace settings"
    )


def test_several_session_env_names_are_named_together():
    with pytest.raises(config.ConfigError) as exc:
        config.load([], {"SESSION_TTL": "60", "session_store": "redis"})
    assert str(exc.value) == (
        "SESSION_STORE, SESSION_TTL are now WORKSPACE_STORE, WORKSPACE_TTL: "
        "the session settings are workspace settings"
    )


def test_a_blank_session_env_name_is_not_a_setting():
    config.load([], {"SESSION_TTL": ""})


def test_an_env_name_that_merely_starts_with_session_is_not_retired():
    # Kubernetes injects <SERVICE>_PORT for every Service, and a Service may be
    # called session: only a workspace setting's own name is retired.
    config.load([], {"SESSION_PORT": "tcp://10.0.0.1:80"})


def test_the_session_file_section_stops_the_boot(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("session:\n  ttl: 60\n")
    with pytest.raises(config.ConfigError) as exc:
        config.load(["--config-file", str(path)], {})
    assert str(exc.value) == config.RETIRED_SESSION_SECTION
    assert config.RETIRED_SESSION_SECTION == (
        "the config file's `session` section is now `workspace`"
    )
```

Change every existing test that sets `SESSION_*`, `--session-*`, `session:` or reads `settings.session` / `sources["session.store"]` to the workspace spelling, and the prefix test to `"selenium-flow:workspace:"`. In `tests/test_routes.py`, `/ready` and `/info` assertions read `"workspaces"`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_config_load.py tests/test_config_schema.py tests/test_config_wiring.py tests/test_routes.py`
Expected: FAIL (`Settings` has no field `workspace`; no retired refusal).

- [ ] **Step 3: Implement**

In `config.py`, rename the `Settings` field `session` → `workspace` (with its section description "How workspaces are kept, and how new browsers open."; field descriptions that say session say workspace), the Redis description "The workspace store's connection.", `RedisSettings.prefix` default `"selenium-flow:workspace:"`, and the `sources` key. Then extend `_retired`:

```python
RETIRED_SESSION_SECTION = "the config file's `session` section is now `workspace`"


def _retired_session(environ: Mapping[str, str], file: dict) -> None:
    """The `session` section's names, refused rather than dropped (spec
    2026-10-09-workspaces-rename, ruling 2). Only a workspace setting's own
    name counts: SESSION_PORT from a Service called session is not one."""
    prefix = "session_"
    old = sorted(
        key.upper()
        for key, value in environ.items()
        if key.lower().startswith(prefix)
        and key.lower()[len(prefix):] in WorkspaceSettings.model_fields
        and not _is_blank(value)
    )
    if old:
        new = ["WORKSPACE_" + key[len(prefix):] for key in old]
        verb = "is" if len(old) == 1 else "are"
        raise ConfigError(
            f"{', '.join(old)} {verb} now {', '.join(new)}: "
            "the session settings are workspace settings"
        )
    if "session" in file:
        raise ConfigError(RETIRED_SESSION_SECTION)
```

and call it at the end of `_retired` (`_retired_session(environ, file)`). Apply the renames in `workspace/settings.py`, `workspace/store.py`, `server.py`, `main.py`, `routes.py`, `spec/schemas.py` per Files.

- [ ] **Step 4: Run, regenerate the OpenAPI golden, read it**

Run: `GOLDEN_UPDATE=1 python3 -m pytest -q -p no:randomly tests/test_golden.py` then `git diff tests/golden/` — expected: only the `/ready` and `/info` `sessions` → `workspaces` properties change.
Run the whole unit suite → all pass. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A kubed tests
git commit -m "Workspaces: the config section, its env names and the Redis prefix; SESSION_* refuses to boot

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The data directory moves itself, once

**Files:**
- Modify: `kubed/selenium_flow/names.py` (`WORKSPACES_DIR = "workspaces"`, its comment), `kubed/selenium_flow/flows/store.py` (`from_settings`: the move; texts in the old-layout refusals say `workspaces/`), `kubed/selenium_flow/config.py` (`RETIRED_FLOW` text; `recording_problem` text and its variable), `kubed/selenium_flow/recordings/collector.py` (docstring path)
- Test: `tests/test_flows.py`, `tests/test_config_load.py` (the `RETIRED_FLOW` pin), `tests/test_recording_collector.py`, `tests/test_recording_store.py`, `tests/test_recording_open.py`, `tests/integration/conftest.py`

**Interfaces:**
- Consumes: `WORKSPACES_DIR` (Task 1), `ConfigError`.
- Produces: `flows.store._OLD_DIR = "sessions"`; the store root is `DATA_DIR/workspaces`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_flows.py`:

```python
def test_the_sessions_folder_becomes_the_workspaces_folder_once(tmp_path):
    flows = tmp_path / "sessions" / "desk" / "flows"
    flows.mkdir(parents=True)
    (flows / "login.yaml").write_text("steps: []\n")
    store = flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    assert store is not None
    assert not (tmp_path / "sessions").exists()
    assert (tmp_path / "workspaces" / "desk" / "flows" / "login.yaml").is_file()
    # A second boot finds nothing to move and stays up.
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None


def test_both_folders_stop_the_boot_naming_both(tmp_path):
    (tmp_path / "sessions" / "a").mkdir(parents=True)
    (tmp_path / "workspaces" / "b").mkdir(parents=True)
    with pytest.raises(ConfigError) as exc:
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    message = str(exc.value)
    assert str(tmp_path / "sessions") in message
    assert str(tmp_path / "workspaces") in message
    assert (tmp_path / "sessions" / "a").is_dir()  # nothing was moved


def test_a_fresh_data_directory_has_nothing_to_move(tmp_path):
    assert flowstore.from_settings(DataSettings(dir=str(tmp_path))) is not None
    assert not (tmp_path / "sessions").exists()
```

Change existing tests that build `tmp_path / "sessions" / …` as the **current** layout to `"workspaces"`, and the assertions on the old-layout refusal text (`"sessions/"` → `"workspaces/"`). The `RETIRED_FLOW` pin in `tests/test_config_load.py` becomes `"FLOW_DATA_DIR is now DATA_DIR, and workspace folders live under DATA_DIR/workspaces/ — move them there once, then set DATA_DIR"`. `tests/integration/conftest.py` paths use `workspaces`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_flows.py tests/test_config_load.py tests/test_recording_collector.py tests/test_recording_store.py tests/test_recording_open.py`
Expected: FAIL (`workspaces/` not created; the move does not happen).

- [ ] **Step 3: Implement**

`names.py`: `WORKSPACES_DIR = "workspaces"`. `config.py`: `RETIRED_FLOW` as pinned above; `recording_problem` says "a recording is filed into its workspace", names its variable `workspaces`, and its overlap text says "a workspace's files" / "every workspace". `flows/store.py`:

```python
# The folder workspaces lived in before they were called workspaces (spec
# 2026-10-09-workspaces-rename, ruling 3). Moved once, at boot.
_OLD_DIR = "sessions"


def _move_old_folder(root: Path) -> None:
    """Rename DATA_DIR/sessions to DATA_DIR/workspaces when only the old exists.

    One rename on one filesystem, so a crash leaves one name or the other,
    never half of each. Both present is a merge only a person can do.
    """
    from ..config import ConfigError  # local: config imports names, not us

    old, new = root / _OLD_DIR, root / WORKSPACES_DIR
    if not _is_dir(old):
        return
    if os.path.lexists(new):
        raise ConfigError(
            f"{old} and {new} both exist: merge {_OLD_DIR}/ into "
            f"{WORKSPACES_DIR}/ by hand, then remove {_OLD_DIR}/"
        )
    os.rename(old, new)
    log.info("data: moved %s to %s", old, new)
```

and in `from_settings`, call it first inside the existing `try` (so an `OSError` from the rename becomes the same "cannot be read" `ConfigError`):

```python
    try:
        _move_old_folder(root)
        stranded = old_layout(root, inbox)
    except OSError as exc:
```

The old-layout refusals' text says `workspaces/` (they interpolate `WORKSPACES_DIR` already after Task 1); the collector's docstring path says `workspaces/<name>/recordings/.pending/`.

- [ ] **Step 4: Run to see them pass**

Run the five files above, then the whole unit suite → all pass. Ruff clean. No golden changes expected (`git status tests/golden` clean).

- [ ] **Step 5: Commit**

```bash
git add -A kubed tests
git commit -m "Workspaces: DATA_DIR/sessions moves to DATA_DIR/workspaces once, at boot

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: How a caller names itself, and every result field

**Files:**
- Modify: `kubed/selenium_flow/workspace/workspaces.py` (`NAME_PARAM = "workspace"`, `NAME_HEADER` removed so `X-Workspace` is the one header, `OLD_QUERY`, `OLD_HEADER`, `UNNAMED`, `BOTH`, `_two_names`, `REPLACED_BEFORE_SAVE`, the resolve and `_held` texts, the module docstring, the status/open/end result keys), `kubed/selenium_flow/names.py` (`valid_workspace_name` text "choose another workspace name", the "workspace name" label), `kubed/selenium_flow/flows/store.py`, `flows/api.py` (texts, `writable`, result keys, `document.pop("workspace")`), `flows/library.py`, `http/admin/flows.py` (texts), `http/files.py` (listing keys and texts), `secrets.py` (listing key), `routes.py` (`end_browser` result, `_add` body-drop key, docstring), `mcp/tools.py` (`INSTRUCTIONS`, `READING_POINTER`, tool docstrings, `end_browser` result), `core/capabilities.py` (`open_session`/`end_browser` response schemas; `library_arg="workspace"`), `core/actions.py` (`upload_file(workspace=…)`), `spec/schemas.py` (`WORKSPACE_PARAMETERS`, `_WORKSPACE`, `_WRITE_WORKSPACE` and their texts; every `session` property), `spec/__init__.py` (re-exports), `spec/builder.py` (their uses, the intro and error texts that mean ours, the `workspace` strip from request bodies), `workspace/store.py` (the in-memory refusal text), `http/admin/workspaces.py` (the "cannot keep files" text)
- Test: `tests/test_workspaces.py`, `tests/test_routes.py`, `tests/test_flowapi.py`, `tests/test_file_caller_order.py`, `tests/conftest.py` (the `http({"session": …})` fixtures → `{"workspace": …}`), and every test using `?session=` / `X-Session-Key` / result key `session`; goldens `openapi.json`, `tools-on.json`, `tools-off.json`

**Interfaces:**
- Produces: `Caller.from_request` reads `?workspace=` and `X-Workspace` only; `workspaces.OLD_QUERY`, `workspaces.OLD_HEADER`; result key `workspace` on `open_session`, `end_browser`, the status; `spec.WORKSPACE_PARAMETERS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workspaces.py`:

```python
from kubed.selenium_flow.workspace.workspaces import OLD_HEADER, OLD_QUERY


def test_the_old_query_name_is_refused_naming_the_new_one():
    caller = caller_of({"session": "desk"})
    with pytest.raises(ValueError) as exc:
        caller.name  # noqa: B018 - the refusal is the point
    assert str(exc.value) == OLD_QUERY == (
        "`?session=` is now `?workspace=`: rename it in the URL"
    )


def test_the_old_header_is_refused_naming_the_new_one():
    caller = caller_of(headers={"x-session-key": "desk"})
    with pytest.raises(ValueError) as exc:
        caller.library  # noqa: B018
    assert str(exc.value) == OLD_HEADER == (
        "`X-Session-Key` is now `X-Workspace`: rename the header"
    )


def test_the_old_name_is_refused_even_beside_the_new_one():
    caller = caller_of({"workspace": "desk", "session": "desk"})
    with pytest.raises(ValueError, match="is now `\\?workspace=`"):
        caller.name  # noqa: B018


def test_a_blank_old_name_is_no_name_at_all():
    assert caller_of({"session": "", "workspace": "desk"}).name == "desk"


def test_the_new_names_name_the_workspace():
    assert caller_of({"workspace": "desk"}).name == "desk"
    assert caller_of(headers={"x-workspace": "desk"}).name == "desk"
```

Rewrite the existing caller tests to the new spelling (`{"session": …}` → `{"workspace": …}`, `x-session-key` → `x-workspace`, `"name your session"` → `"name your workspace"`, `"name your session once"` → `"name your workspace once"`). The tests about *two headers* (`x-session-key` + `x-workspace`) become tests that the old header is refused.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_workspaces.py tests/test_routes.py tests/test_flowapi.py tests/test_file_caller_order.py`
Expected: FAIL (`ImportError: cannot import name 'OLD_HEADER'`, then old names still accepted).

- [ ] **Step 3: Implement**

In `workspace/workspaces.py`:

```python
# A client names its workspace with either of these. The query parameter is the
# one most clients can set, since an MCP server is usually configured by URL.
NAME_PARAM = "workspace"
WORKSPACE_HEADER = "x-workspace"

# The names from before the rename (spec 2026-10-09-workspaces-rename, ruling
# 1): refused, not ignored, so a client wired the old way hears it on its first
# call rather than as "name your workspace".
OLD_PARAM = "session"
OLD_HEADER_NAME = "x-session-key"
OLD_QUERY = "`?session=` is now `?workspace=`: rename it in the URL"
OLD_HEADER = "`X-Session-Key` is now `X-Workspace`: rename the header"

UNNAMED = (
    "name your workspace: add ?workspace=<name> to the URL, or send an "
    "X-Workspace header. Every workspace here is named by whoever calls, and "
    "the server does not invent one. The name is yours to choose and to reuse "
    "— calling again with the same name is how you get the same browser back."
)

BOTH = (
    "name your workspace once: this request carries both an X-Workspace header "
    "and ?workspace=, and they are two answers to one question. Send whichever "
    "one you control and drop the other."
)
```

In `Caller.from_request`, read the header from `headers.get(WORKSPACE_HEADER)` only, and after the existing name logic:

```python
        if _names(params.get(OLD_PARAM)):
            named, named_by, refusal = "", "", ValueError(OLD_QUERY)
        elif _names(headers.get(OLD_HEADER_NAME)):
            named, named_by, refusal = "", "", ValueError(OLD_HEADER)
```

`_two_names` says "the request names {count} workspaces". Every other text and key listed under Files says workspace; `library_arg="workspace"` and `Actions.upload_file(workspace=…)`; `spec/builder.py` strips `workspace` from request bodies where it stripped `session`. The open_session response schema in `core/capabilities.py` names its property `workspace`.

- [ ] **Step 4: Run, regenerate the goldens, read them**

Run: `GOLDEN_UPDATE=1 python3 -m pytest -q -p no:randomly tests/test_golden.py` and read `git diff tests/golden/`: `X-Session-Key` / `session` query parameters → `X-Workspace` / `workspace`; result properties `session` → `workspace`; texts. Nothing else.
Run the whole unit suite → all pass. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A kubed tests
git commit -m "Workspaces: ?workspace= and X-Workspace name the caller, the old names are refused, and results say workspace

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Resource URIs

**Files:**
- Modify: `kubed/selenium_flow/names.py` (`RETIRED_SCHEME`, `retired_uri`), `mcp/resources.py` (`RESOURCE_URI = "workspace://current"`, names "Current Workspace", descriptions), `http/files.py` (`ROOT_URI`, `FILE_URI`, `FOLDER_URI`, `ITEM_URI`, `LIST_URI`, `SHAPES`, texts, resource names "Workspace Files"/"Workspace File"; `parse_uri` calls `retired_uri` first), `site_data/snapshot.py` (`LIST_URI = "workspace://site-data"` — the URI only; its `"session"` storage key stays), `mcp/completions.py`, `mcp/show.py` (`VIEWS` table; `view_for` calls `retired_uri` first), `mcp/mirror.py` (texts; `read_resource` calls `retired_uri` first), `mcp/tools.py`, `core/capabilities.py`, `core/actions.py`, `core/guidance.py` (pointer `WORKSPACES.md`), `spec/schemas.py`, `spec/builder.py` (operationId `currentWorkspace`, schema `WorkspaceStatus`), comments in `names.py`, `principal.py`, `workspace/locks.py`, `http/files.py`
- Test: the ~22 test files carrying `session://` (`test_file_sections.py`, `test_show.py`, `test_recording_surfaces.py`, `test_site_data_surface.py`, `test_file_surfaces.py`, `test_resources.py` ("Current Workspace"), …); `tests/test_names.py` (new tests); goldens

**Interfaces:**
- Produces: `names.retired_uri(uri: str) -> None` (raises `ValueError`); `workspace://current`, `workspace://files…`, `workspace://site-data…`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_names.py` (create it with the imports below if it does not exist):

```python
import pytest

from kubed.selenium_flow import names
from kubed.selenium_flow.http import files
from kubed.selenium_flow.mcp import show


def test_an_old_uri_names_its_new_spelling():
    with pytest.raises(ValueError) as exc:
        names.retired_uri("session://files/screenshots/a.png")
    assert str(exc.value) == (
        "`session://files/screenshots/a.png` is now "
        "`workspace://files/screenshots/a.png`"
    )


def test_a_current_uri_passes():
    names.retired_uri("workspace://current")
    names.retired_uri("flow://flows")


def test_a_saved_flows_old_upload_uri_says_where_it_went():
    with pytest.raises(ValueError, match="is now `workspace://files/a.png`"):
        files.parse_uri("session://files/a.png")


def test_show_names_the_new_spelling_of_an_old_uri():
    with pytest.raises(ValueError, match="is now `workspace://current`"):
        show.view_for("session://current")
```

Replace `session://` with `workspace://` in every existing test, and `"Current Session"` with `"Current Workspace"`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_names.py tests/test_file_sections.py tests/test_show.py tests/test_resources.py`
Expected: FAIL (`AttributeError: ... 'retired_uri'`, then URIs not found).

- [ ] **Step 3: Implement**

In `names.py`:

```python
# The scheme every workspace resource had before the rename (spec
# 2026-10-09-workspaces-rename, ruling 5). A saved flow may still name one.
RETIRED_SCHEME = "session://"


def retired_uri(uri) -> None:
    """Refuse a URI from before the rename, naming its new spelling."""
    text = str(uri or "")
    if text.startswith(RETIRED_SCHEME):
        raise ValueError(
            f"`{text}` is now `workspace://{text[len(RETIRED_SCHEME):]}`"
        )
```

Call `names.retired_uri(uri)` as the first statement of `http/files.parse_uri`, `mcp/show.view_for`, and `mcp/mirror.read_resource`. Rename every URI constant and copy listed under Files.

- [ ] **Step 4: Run, regenerate the goldens, read them**

Run: `GOLDEN_UPDATE=1 python3 -m pytest -q -p no:randomly tests/test_golden.py`; `git diff tests/golden/` shows `session://` → `workspace://`, `currentSession` → `currentWorkspace`, `SessionStatus` → `WorkspaceStatus`, names and texts — nothing else.
Run the whole unit suite → all pass. `git grep -n "session://" -- kubed tests` shows only `names.py`'s `RETIRED_SCHEME` and the tests of the refusal. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A kubed tests
git commit -m "Workspaces: workspace:// for every resource, and an old session:// URI says where it went

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The admin API

**Files:**
- Modify: `kubed/selenium_flow/http/admin/workspaces.py` (`GET /admin/workspaces` named `admin_workspaces`, `DELETE /admin/workspaces/{key}/session` named `admin_end_session`, the `{"workspaces": [...]}` payload, texts), `http/admin/files.py`, `http/admin/flows.py`, `http/admin/site_data.py` (every `/admin/sessions/{key}/…` → `/admin/workspaces/{key}/…`, keys in their payloads), `http/admin/__init__.py`, `http/admin/signed.py` and `http/links.py` (the path parameter named `session` that holds a workspace name → `workspace`; `owner="workspace"`; the `/files/{session_id}/…` Grid-id route stays), `http/access_log.py` (docstring)
- Move: `tests/golden/admin-sessions.json` → `tests/golden/admin-workspaces.json`
- Test: `tests/test_admin_*.py`, `tests/test_files_and_admin.py` (including `test_the_mcp_surface_never_lists_other_sessions` → `..._other_workspaces`), `tests/test_golden.py` (the admin golden's name), `tests/bench/test_admin.py`, `tests/test_main.py`, `tests/test_shutdown.py`; goldens `admin-routes.json`, `admin-workspaces.json`

**Interfaces:**
- Produces: `/admin/workspaces`, `/admin/workspaces/{key}/files…|flows…|history…|site-data…`; End is `DELETE /admin/workspaces/{key}/session`, route name `admin_end_session` (spec ruling 10, unchanged name); event and list payload key `workspaces`.

- [ ] **Step 1: Point the tests at the new paths**

```bash
git mv tests/golden/admin-sessions.json tests/golden/admin-workspaces.json
```

In every test listed, `/admin/sessions` → `/admin/workspaces`, payload key `"sessions"` → `"workspaces"` (rows keep their Grid `session_id`), route name `admin_sessions` → `admin_workspaces` (`admin_end_session` keeps its name; its path is `DELETE /admin/workspaces/{key}/session`); in `tests/test_golden.py` the admin golden is `admin-workspaces.json`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_admin_files.py tests/test_files_and_admin.py tests/test_golden.py`
Expected: FAIL (404 on `/admin/workspaces…`).

- [ ] **Step 3: Implement**

Apply the Files list. Signed link URL shapes (`/kept/{…}/{name}`, `/screenshots/{…}/…`, `/recordings/{…}/…`) do not change; only their parameter's name does.

- [ ] **Step 4: Run, regenerate the goldens, read them**

Run: `GOLDEN_UPDATE=1 python3 -m pytest -q -p no:randomly tests/test_golden.py`; `git diff tests/golden/` shows only the admin paths, names and payload key. Whole unit suite → all pass. Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add -A kubed tests
git commit -m "Workspaces: the admin API under /admin/workspaces

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The admin UI and the MCP App

**Files:**
- Move: `ui/src/lib/SessionList.svelte` (+`.test.ts`) → `WorkspaceList.*`; `ui/src/lib/SessionSummary.svelte` (+test) → `WorkspaceSummary.*`; `ui/src/admin/SessionDetail.svelte` (+test) → `WorkspaceDetail.*`; `ui/src/admin/SessionsView.svelte` (+test) → `WorkspacesView.*`; `ui/src/admin/session.svelte.ts` → `workspace.svelte.ts`, `ui/src/admin/session.test.ts` → `workspace.test.ts`
- Modify: every importer; `ui/src/admin/api.ts` (`workspacePath` → `'/admin/workspaces/'`), `Admin.svelte` (tab "Workspaces", ids `tabWorkspaces`, `paneWorkspaces`), `live.svelte.ts`, `router.svelte.ts` (`#/workspaces/…`, view `'workspace'`), `lib/types.ts` (`WorkspaceRow`, `WorkspacesPayload { workspaces }`, every `session` field meaning ours → `workspace`; **keep** `session_storage` and `SavedCounts.session`), `lib/format.ts` (`workspaceLabel`), `FileGrid.svelte` ("No files in this workspace yet."), `FlowsPane.svelte`, `lib/views/ContextView.svelte` (`data.workspace`), `app.css` (selectors and comments), every label and DOM id listed in the inventory (`sessionsView` → `workspacesView`, "Live sessions" → "Live workspaces", `#sessions` → `#workspaces`, "No sessions yet." → "No workspaces yet.", "← Sessions" → "← Workspaces", `sessionTabs` → `workspaceTabs`, group `'session'` → `'workspace'`, fallback `'Session'` → `'Workspace'`); **keep** `HistoryPane.svelte`'s "N session" and `SiteDataPane.svelte`'s sessionStorage texts
- Modify: `tests/integration/flows/*.yaml` (move `open-this-session.yaml` → `open-this-workspace.yaml`, `sign-in-and-find-this-session.yaml` → `sign-in-and-find-this-workspace.yaml`; the parameter `workspace`, selector `#workspaces .pill.name`, hashes `#/workspaces/${workspace}…`), `tests/integration/conftest.py` (`WORKSPACE = "integration"`), `tests/test_responsive.py`
- Design: Penpot pages *Workspace · Files|Flows|Site data|History*, *Admin* (read, don't edit)

**Interfaces:**
- Consumes: the admin API (Task 6), the status key `workspace` (Task 4).

- [ ] **Step 1: Point the UI tests at the new names**

Move the test files with their components (as above) and update every UI test (~22 files): imports, API paths, payload keys, labels, hashes, ids.

- [ ] **Step 2: Run to see them fail**

Run: `npm --prefix ui test`
Expected: FAIL (modules not found, old labels rendered).

- [ ] **Step 3: Implement**

Move the components and apply the Files list.

- [ ] **Step 4: Run to see them pass, and build**

Run: `npm --prefix ui test` (all pass), `npm --prefix ui run -s check` (0 errors, 0 warnings), `npm --prefix ui run -s lint` (clean), `npm --prefix ui run build` (the static output is gitignored; the build proves it compiles), then the Python unit suite (it serves the built UI in a few tests) → all pass.
Run: `git grep -n -i -w -E "sessions?" -- ui/src | grep -v -E "sessionStorage|session_storage|SavedCounts|HistoryPane|SiteDataPane"` → only lines you can name as the kept meanings.

- [ ] **Step 5: Commit**

```bash
git add -A ui tests
git commit -m "Workspaces: the admin UI and the MCP App

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The embedded skill and the prompts

**Files:**
- Move: `skills/selenium-flow/references/SESSIONS.md` → `skills/selenium-flow/references/WORKSPACES.md`
- Modify: `skills/selenium-flow/SKILL.md`, every `references/*.md` (CONFIGURATION, TROUBLESHOOTING, SITE_DATA — its sessionStorage stays, FLOWS, INTERACTION, SECRETS, READING_PAGES), `prompts/build_flow.md`, `prompts/repair_flow.md`, and the pointers to `SESSIONS.md` in `kubed/selenium_flow/core/guidance.py` and `workspace/workspaces.py` if Task 5 left any
- Test: `tests/test_skill.py`, `tests/test_guidance.py` (`WORKSPACES.md`); `current_session` in `tests/test_resources.py:101` and `tests/test_guidance.py:191` is a **removed tool's** name and stays

**Interfaces:**
- Produces: `skill://selenium-flow/references/WORKSPACES.md`.

- [ ] **Step 1: Point the tests at the new file**

In `tests/test_skill.py` and `tests/test_guidance.py`, `SESSIONS.md` → `WORKSPACES.md`.

- [ ] **Step 2: Run to see them fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_skill.py tests/test_guidance.py`
Expected: FAIL (`WORKSPACES.md` missing).

- [ ] **Step 3: Implement**

```bash
git mv skills/selenium-flow/references/SESSIONS.md skills/selenium-flow/references/WORKSPACES.md
```

Rewrite the skill and prompts in the new vocabulary: `?workspace=`, `X-Workspace`, `workspace://…`, `WORKSPACE_*`, "workspace". The skill is prompt (AGENTS.md "The embedded skill"): write for a model deciding what to do next, and keep SKILL.md an index.

- [ ] **Step 4: Run to see them pass**

Run the two files, then the whole unit suite (the skill tests check every linked reference exists and every file is linked) → all pass.

- [ ] **Step 5: Commit**

```bash
git add -A skills prompts kubed tests
git commit -m "Workspaces: the skill and the prompts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Docs, the wiki, the changelog, and the audit

**Files:**
- Modify: `README.md` (stay under 25 000 bytes, `tests/test_readme.py`; remove the line calling a name the credential; the anchor and wiki link to `Workspaces`), `AGENTS.md` (the design-record pointer: `docs/superpowers/` is the record and `docs/saga/` is deprecated; remove "a session name is now the entire credential for driving that browser"; every section about our sessions; the agentgateway line: `X-Workspace` crosses and `?workspace=` does not; test names it cites), `CONTRIBUTING.md`, `CHANGELOG.md` (one line under `[Unreleased]`, see Step 3), `scripts/generate_wiki.py` (`current_workspace` in GROUPS, `[Workspaces](Workspaces)`, the curl example `X-Workspace: $WORKSPACE`)
- Wiki submodule (`wiki/`): regenerate; move `Sessions.md` → `Workspaces.md`; rewrite the hand-written pages (`Files.md`, `Administration.md`, `Deployment.md`, `Installing.md`, `Flows.md`, `Home.md`, `_Sidebar.md`, `Secrets.md`) and notes (`notes/open_session.notes.md`, `save_site_data.notes.md`, `Configuration.notes.md`, `keep_file.notes.md`)
- Test: `tests/test_wiki.py` (`current_session` page → `current_workspace` at its line ~145)

- [ ] **Step 1: Point the wiki test at the new page**

In `tests/test_wiki.py`, the expected page `current_session` → `current_workspace`.

- [ ] **Step 2: Run to see it fail**

Run: `python3 -m pytest -q -p no:randomly tests/test_wiki.py`
Expected: FAIL (the committed pages do not match the generator).

- [ ] **Step 3: Implement**

Rewrite README, AGENTS.md, CONTRIBUTING.md as listed. Add under `## [Unreleased]` in `CHANGELOG.md`, in the section's existing style:

```markdown
- **BREAKING:** a session is a *workspace* now: name it with `?workspace=` or `X-Workspace`, read `workspace://…`, set `WORKSPACE_*`; the old names are refused with the new one, and `DATA_DIR/sessions/` moves to `DATA_DIR/workspaces/` on first boot.
```

Update `scripts/generate_wiki.py`, then in the submodule:

```bash
cd $R/wiki
git mv Sessions.md Workspaces.md
cd $R && python3 scripts/generate_wiki.py
```

Rewrite the hand-written wiki pages and notes listed under Files; delete a generated page the generator no longer writes (`current_session.md`) if it is still there. Commit inside the submodule:

```bash
cd $R/wiki && git add -A && git commit -m "Workspaces: the rename

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(The wiki commit is pushed to the wiki remote by the controller when the PR is opened, never by this task.)

- [ ] **Step 4: Run everything, and the audit**

Run: the whole Python unit suite (`test_wiki.py`, `test_readme.py` included) → all pass; `python3 -m ruff check kubed tests scripts`; `python3 -m ruff format --check kubed tests scripts`; the UI commands → all clean.

The audit, which must show only kept meanings (the Global Constraints' Kept list), each one you can name:

```bash
cd $R && git grep -n -i -w -E "sessions?" -- . ':!docs/saga' ':!docs/superpowers' ':!CHANGELOG.md' ':!tests/golden' \
  | grep -v -E "session_id|sessionStorage|session_storage|Mcp-Session-Id|mcp\.session|SE_NODE_SESSION_TIMEOUT|open_session|scope=\"session\"|start_new_session|requests\.Session|RETIRED_SCHEME|OLD_PARAM|OLD_HEADER|SESSION_\*|retired"
```

Every remaining line goes in the report with the meaning it keeps — our *session* (the live browser in a workspace, spec ruling 8) is one of them. A line meaning the *workspace* (the named box) is a bug: fix it in this task.

- [ ] **Step 5: Commit**

```bash
cd $R && git add -A README.md AGENTS.md CONTRIBUTING.md CHANGELOG.md scripts tests wiki
git commit -m "Workspaces: README, AGENTS.md, the wiki and the changelog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
