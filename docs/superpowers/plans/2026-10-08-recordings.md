# Recordings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `open_session(record=true)` records the browser's whole life on the Grid; a bounded collector files each finished video into `sessions/<name>/recordings/`, where it follows the screenshot lifecycle — ephemeral, cleared in bulk, kept into Files — on every surface and in the admin UI.

**Architecture:** The Grid's own recorder makes the MP4 (`se:recordVideo`); the operator's transport drops it in an inbox directory (`recording.dir`, default `$DATA_DIR/recordings`). On open, the server writes a note naming the Grid id; one asyncio `Collector` per process watches the inbox with `watchfiles` (events, or polling on a network filesystem), files a recording the moment it ends in a valid `mfro` box, and nudges the admin broadcast. The data directory becomes a root with `sessions/` and `recordings/` in it (`DATA_DIR` replaces `FLOW_DATA_DIR`).

**Tech Stack:** Python 3.10+ (CI 3.10–3.14), FastMCP 4.0.x (`lifespan`), Starlette 1.7 (`FileResponse` with Range), `watchfiles` ≥ 1.3 (`awatch`), pydantic-settings, pytest (`asyncio_mode = "auto"`); Svelte 5 + vitest for `ui/`.

**Spec:** `docs/superpowers/specs/2026-10-08-recordings-design.md` — read *Rulings* and *Design* §1–§10 before any task. The Penpot file *Admin UI*, page *Session · Files*, flow **Recordings**, is the drawing Task 7 builds.

## Global Constraints

- **The recording lifecycle is the screenshot lifecycle** (Dr K, 2026-10-08): a filed recording is *ephemeral* in Recordings; **Clear recordings** deletes them in bulk; **keep** moves one into Files, out of the ephemeral state, exactly as keeping a screenshot does. No per-recording delete. No agent tool clears or deletes anything.
- Only filed recordings are ever listed or counted. Notes (`.pending/`) and the inbox are never shown on any surface (ruling 5).
- The Grid's session id appears on our disk (notes) and in the inbox only — never in a result, resource, admin payload or log line a caller reads (AGENTS.md).
- Names, verbatim: settings `data.dir` (`DATA_DIR`), `recording.enabled` (`RECORDING_ENABLED`, default `false`), `recording.dir` (`RECORDING_DIR`, default `$DATA_DIR/recordings`), `recording.wait` (`RECORDING_WAIT`, default `600`, ≥ 30), `recording.watch` (`RECORDING_WATCH`, `auto|events|poll`, default `auto`), `recording.poll` (`RECORDING_POLL`, ms, default `1000`, ≥ 200). Folders `sessions/`, `recordings/`, `.pending/`. File names `rec-<YYYYMMDD-HHMM>.mp4` (UTC, from the note's `opened`). URIs `session://files/recordings`, `session://files/recordings/{name}`. Capabilities `se:recordVideo: true`, `se:videoName: <session name>`.
- The 400 for an unconfigured server is, verbatim: `Recording is not set up on this server (recording.enabled is off). See the README's Recording section.`
- The retired-name refusal is, verbatim: `FLOW_DATA_DIR is now DATA_DIR, and session folders live under DATA_DIR/sessions/ — move them there once, then set DATA_DIR`.
- Plain W3C WebDriver only. No CDP. No new scheduler: the collector is the one bounded wait (AGENTS.md amendment, Task 8).
- Python tests: `PYL=/tmp/claude-1000/-projects-kubed-io-selenium-flow/9c97ca0c-58d7-41ec-beb2-6f8801af747d/scratchpad/pylibs` then `PYTHONPATH=$PWD:$PYL python3 -S -m pytest -q -p no:randomly <paths>` from the repo (or worktree) root. `-S` is required in this pod. `$PYL` was built 2026-10-08 with `python3 -m pip install --target $PYL ".[test]" watchfiles ruff`, then `rm -rf $PYL/kubed $PYL/kubed_selenium_flow*` so the source tree wins; if it is gone, rebuild it the same way. Known env-only failure: `tests/test_wiki.py::test_the_generated_pages_are_current` (it spawns a bare interpreter) — check the wiki with `PYTHONPATH=$PWD:$PYL python3 -S scripts/generate_wiki.py --check` instead. Baseline at `8f3d6dc`: 2146 passed, 20 skipped, that 1 failed.
- Lint: `PYTHONPATH=$PYL python3 -S -m ruff check .` (CI runs only `ruff check`; never `ruff format` a file you did not create).
- Goldens: regenerate with `GOLDEN_UPDATE=1` on the same pytest command for `tests/test_golden.py`, then read the diff — it must show only what the task changed.
- Wiki: `PYTHONPATH=$PWD:$PYL python3 -S scripts/generate_wiki.py` rewrites `wiki/` (a submodule — commit inside it first, then the pointer).
- UI: `npm --prefix ui ci` once per worktree, then `npm --prefix ui test`, `npm --prefix ui run check`, `npm --prefix ui run lint`, `npm --prefix ui run build`, `npm --prefix ui run size`.
- Copy is terse: dots, not "set"; no helper sentences in the UI. Real hosts never appear in tests or examples (`example.com`, user `drk`).
- CHANGELOG: one short line per user-visible change, under `[Unreleased]` only (AGENTS.md).
- Commits: one per task on branch `recordings`, house style, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push or open a PR unless Dr K asks.

## File structure

| File | Responsibility |
|---|---|
| Modify `kubed/selenium_flow/names.py` | `SESSIONS_DIR`, `RECORDINGS_DIR`, `INBOX_DIR`, `FOLDERS` gains recordings; `RESERVED_IN_FILES`, `candidates(name)` and `valid_grid_id` move/arrive here as shared pure rules. |
| Modify `kubed/selenium_flow/config.py` | `DataSettings` replaces `FlowSettings`; `RecordingSettings`; retired-name refusal; `recording_problem`; `recording_dir(settings)`. |
| Modify `kubed/selenium_flow/flows/store.py` | Root = `data.dir/sessions`; the old-layout refusal; `file_path`, `move_in`, notes (`write_note`, `notes`, `delete_note`). |
| Create `kubed/selenium_flow/recordings/__init__.py` | Package docstring. |
| Create `kubed/selenium_flow/recordings/mp4.py` | `is_complete(path)`: the `mfro` trailer check. |
| Create `kubed/selenium_flow/recordings/mounts.py` | `network_filesystem(path)`, `polling(watch, path)`. |
| Create `kubed/selenium_flow/recordings/collector.py` | `Collector`: notes as the queue, one bounded task, `expect`/`ended`, `start`/`stop`, filing. |
| Modify `kubed/selenium_flow/session/settings.py` | `record` joins `SETTINGS` (explicit only). |
| Modify `kubed/selenium_flow/session/sessions.py` | The gate, dropping `record` from `previous`, the hooks into the collector, `recording` in `describe`. |
| Modify `kubed/selenium_flow/core/browser.py`, `core/actions.py` | `se:recordVideo`/`se:videoName`; `open_session(record, video_name)`. |
| Modify `kubed/selenium_flow/mcp/tools.py`, `core/capabilities.py`, `spec/schemas.py` | The `record` argument, its docstring, the `recording` result and status fields. |
| Modify `kubed/selenium_flow/http/files.py`, `http/links.py`, `http/admin/signed.py`, `http/admin/files.py`, `http/admin/sessions.py`, `http/admin/__init__.py`, `mcp/show.py`, `spec/schemas.py`, `spec/builder.py` | The Recordings folder on every surface; disk-backed routes stream with Range. |
| Modify `kubed/selenium_flow/server.py`, `main.py` | Build the collector, the lifespan, the broadcast poke; `ConfigError` from construction stops the boot. |
| Modify `ui/src/lib/types.ts`, `ui/src/admin/session.svelte.ts`, `ui/src/admin/FilesPane.svelte`, `ui/src/admin/SessionDetail.svelte`, `ui/src/lib/Lightbox.svelte`, `ui/src/lib/FileTile.svelte`, `ui/src/lib/SessionSummary.svelte`, `ui/src/lib/format.ts`, `ui/src/lib/views/FilesView.svelte` (+ their `*.test.ts`) | The Recordings row, video tiles and lightbox, the ● REC pill, the counts. |
| Docs | `README.md`, `AGENTS.md`, `CHANGELOG.md`, `docker-compose.yaml`, `pyproject.toml`, `skills/selenium-flow/references/SESSIONS.md`, `skills/selenium-flow/references/CONFIGURATION.md`, `wiki/` regenerated, the spec's §10. |

---

### Task 1: Verify first (spike, no production code)

The spec's §10 lists what was researched and not run. Two items are settled already and only need recording; the rest are measured here. Nothing in this task is kept but the notes.

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-recordings-design.md` (§10 only)

- [ ] **Step 1: Record the two settled items.** Under §10 write, as results:

```markdown
**Results (2026-10-08):**

2. Settled: Starlette 1.7.0 `FileResponse` answers `Range: bytes=10-19` with
   `206` and `Content-Range: bytes 10-19/2560` (measured with `TestClient`).
6. Settled for wheels: watchfiles 1.3.0 ships `cp310-abi3` wheels (manylinux
   x86_64/aarch64, macOS) — one wheel covers 3.10–3.14.
```

- [ ] **Step 2: Measure a real recording (Dr K's live environment).** Recordings already reach `/data/flows/recordings` (Dr K, 2026-10-08). Ask Dr K to run these and paste the output — the agent's service account cannot exec into `flow` pods:

```bash
kubectl -n flow exec deploy/selenium-flow -- ls -laR /data/flows/recordings
kubectl -n flow exec deploy/selenium-flow -- python3 -c "
import os, sys
d = '/data/flows/recordings'
paths = [os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs]
p = max(paths, key=os.path.getmtime)
size = os.path.getsize(p)
with open(p, 'rb') as f:
    f.seek(size - 16); tail = f.read(16)
print(p, size, tail.hex(' '))
"
```

Expected: a path whose file name contains a Grid session id (32 hex or a dashed UUID), and a tail of `00 00 00 10 6d 66 72 6f 00 00 00 00 xx xx xx xx` (`mfro`). Write under §10: the exact path shape (top level or a subfolder; `*.partial` or not while copying), the tail bytes, and whether item 5 holds.

- [ ] **Step 3: Measure the timing.** Ask Dr K to open a recorded browser on the Grid directly, end it, and time the file's arrival (the Grid is in-cluster; a laptop with `kubectl port-forward -n flow svc/selenium-grid-selenium-hub 4444` works):

```python
# run where the Grid is reachable; prints seconds from quit to an mfro-ended file
import time
from selenium import webdriver
o = webdriver.ChromeOptions()
o.set_capability("se:recordVideo", True)
o.set_capability("se:videoName", "spike")
d = webdriver.Remote("http://localhost:4444", options=o)
gid = d.session_id
d.get("https://example.com"); time.sleep(20)
t0 = time.time(); d.quit()
print("grid id", gid, "quit at", t0)
```

then, every few seconds, the `exec` from Step 2 with `p` chosen as the file containing that id. Write under §10 item 4: seconds from quit to a file ending in `mfro`.

- [ ] **Step 4: Item 1 (compose).** Note under §10 item 1: "measured in Task 8, Step 6, once `docker-compose.yaml` mounts `./data/recordings`." (It needs the code.)

- [ ] **Step 5: Item 3 (video CSP).** Note under §10 item 3: "measured in Task 9 against the deployed server." Task 6 ships `default-src 'none'; media-src 'self'; style-src 'unsafe-inline'` for `video/*`, which is what a top-level media document needs to load itself.

- [ ] **Step 6: If a measurement contradicts the spec** (no Grid id in the name, no `mfro`), stop and tell Dr K before Task 5: the collector's matching or completion rule depends on it.

- [ ] **Step 7: Commit.**

```bash
git add docs/superpowers/specs/2026-10-08-recordings-design.md
git commit -m "Recordings: what the spike measured

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `DATA_DIR`, with sessions under `sessions/` (breaking)

**Files:**
- Modify: `kubed/selenium_flow/names.py`, `kubed/selenium_flow/config.py` (`FlowSettings` at ~194–202, `Settings.flow` at ~322, `load` at ~700–760), `kubed/selenium_flow/flows/store.py` (`from_settings` at ~520, module docstring), `kubed/selenium_flow/server.py:118`, `kubed/selenium_flow/main.py:46-56`
- Modify (message text `FLOW_DATA_DIR` → `DATA_DIR`): `core/actions.py:121`, `core/actions.py:1121` (comment), `core/naming.py:99` (comment), `flows/api.py:108`, `http/files.py:20,140`, `http/answer.py:121` (comment), `http/admin/signed.py`, `spec/builder.py` (any description text), `ui/src/admin/FlowsPane.svelte` (+ its test)
- Modify tests: every file listed by `grep -rlE "data_dir|FLOW_DATA_DIR" tests` (26 files), `tests/integration/conftest.py:129`
- Test: `tests/test_config_load.py`, `tests/test_flows.py`, `tests/test_main.py`

**Interfaces:**
- Produces: `names.SESSIONS_DIR = "sessions"`, `names.INBOX_DIR = "recordings"`; `config.DataSettings(dir: str | None)`; `Settings.data`; `config.RETIRED_FLOW = <the verbatim refusal>`; `flowstore.from_settings(data: DataSettings) -> LocalFlowStore | None` whose root is `Path(data.dir) / SESSIONS_DIR`; `flowstore.old_layout(root: Path) -> list[str]`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_config_load.py`:

```python
def test_data_dir_is_the_setting_and_reads_from_env():
    loaded = config.load([], {"DATA_DIR": "/srv/data"})
    assert loaded.settings.data.dir == "/srv/data"
    assert loaded.sources["data.dir"] == "env"


def test_the_retired_env_name_stops_the_boot_with_the_move():
    with pytest.raises(config.ConfigError) as exc:
        config.load([], {"FLOW_DATA_DIR": "/data/flows"})
    assert str(exc.value) == config.RETIRED_FLOW


def test_a_blank_retired_env_name_is_not_a_setting():
    config.load([], {"FLOW_DATA_DIR": ""})


def test_the_retired_file_section_stops_the_boot_with_the_move(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("flow:\n  data_dir: /data/flows\n")
    with pytest.raises(config.ConfigError) as exc:
        config.load(["--config-file", str(path)], {})
    assert str(exc.value) == config.RETIRED_FLOW
```

Append to `tests/test_flows.py`:

```python
from kubed.selenium_flow.config import ConfigError, DataSettings


def test_sessions_live_under_sessions(tmp_path):
    store = flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    store.write_file("bot", "a.txt", b"x")
    assert (tmp_path / "sessions" / "bot" / "files" / "a.txt").read_bytes() == b"x"


def test_an_old_layout_stops_the_boot_and_names_what_to_move(tmp_path):
    (tmp_path / "claudecode" / "screenshots").mkdir(parents=True)
    (tmp_path / "global" / "flows").mkdir(parents=True)
    (tmp_path / "recordings").mkdir()  # the inbox is not a session
    with pytest.raises(ConfigError) as exc:
        flowstore.from_settings(DataSettings(dir=str(tmp_path)))
    assert "claudecode, global" in str(exc.value)
    assert "sessions/" in str(exc.value)


def test_unset_data_dir_is_off():
    assert flowstore.from_settings(DataSettings()) is None
    assert flowstore.from_settings(DataSettings(dir="   ")) is None
```

Replace the two `FlowSettings` assertions at `tests/test_flows.py:341-346` with the `DataSettings` ones above (delete the old lines and the `FlowSettings` import).

Append to `tests/test_main.py`:

```python
def test_a_config_error_while_building_the_server_stops_the_boot(tmp_path, monkeypatch):
    (tmp_path / "old" / "flows").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc:
        main.main(["--data-dir", str(tmp_path)])
    assert "move" in str(exc.value) and "sessions/" in str(exc.value)
```

(Read the top of `tests/test_main.py` for how it imports `main` and stubs `run`; follow it.)

- [ ] **Step 2: Run them to verify they fail.**

Run: `PYTHONPATH=$PWD:$PYL python3 -S -m pytest -q -p no:randomly tests/test_config_load.py tests/test_flows.py tests/test_main.py`
Expected: FAIL — `AttributeError: ... has no attribute 'data'`, `ImportError: cannot import name 'DataSettings'`.

- [ ] **Step 3: `names.py`.** After `STDIO_SESSION`, add:

```python
# The data directory is a root with two homes in it (recordings spec, ruling 6):
# every session's own folder under `sessions/`, and the inbox the Grid's
# recordings arrive in. Neither can collide with a session's name, because a
# session is one level further down.
SESSIONS_DIR = "sessions"
INBOX_DIR = "recordings"
```

- [ ] **Step 4: `config.py`.** Replace `FlowSettings` with:

```python
class DataSettings(Section):
    dir: str | None = Field(
        None,
        description=(
            "Where sessions and recordings live. Unset turns flows, kept files "
            "and recordings off."
        ),
    )

    @field_validator("dir")
    @classmethod
    def _blank_is_off(cls, value):
        return (value or "").strip() or None
```

In `Settings`, replace the `flow:` field with:

```python
    data: DataSettings = Field(
        default_factory=DataSettings,
        description="Where sessions, their flows and files, and recordings live.",
    )
```

Above `def load(`, add:

```python
RETIRED_FLOW = (
    "FLOW_DATA_DIR is now DATA_DIR, and session folders live under "
    "DATA_DIR/sessions/ — move them there once, then set DATA_DIR"
)


def _retired(environ: Mapping[str, str], file: dict) -> None:
    """A name this server used to read, refused rather than ignored.

    The env layer drops unknown names on purpose (Kubernetes injects plenty),
    so a deployment still setting FLOW_DATA_DIR would boot with flows quietly
    off. One retired name is worth naming; the rule stays lenient for the rest.
    """
    if any(k.lower() == "flow_data_dir" and not _is_blank(v) for k, v in environ.items()):
        raise ConfigError(RETIRED_FLOW)
    if "flow" in file:
        raise ConfigError(RETIRED_FLOW)
```

In `load`, right after `file = _drop_blank_leaves(_read_file(path)) if path else {}`, add `_retired(environ, file)`.

- [ ] **Step 5: `flows/store.py`.** Replace the `TYPE_CHECKING` import of `FlowSettings` with `DataSettings`, import `SESSIONS_DIR` from `..names`, and replace `from_settings` with:

```python
# Folders that mark a directory as a session's, for the old-layout check.
_SESSION_MARKS = (FLOWS_DIR, FILES_DIR, "screenshots")


def old_layout(root: Path) -> list[str]:
    """Session folders still at the top of the data directory, sorted.

    Before the recordings release a session lived at ``DATA_DIR/<name>``; it
    lives at ``DATA_DIR/sessions/<name>`` now. One left behind would make every
    flow and file it holds silently vanish, so the boot refuses and names them.
    """
    if not root.is_dir():
        return []
    found = []
    for entry in root.iterdir():
        if entry.name == SESSIONS_DIR or not entry.is_dir() or entry.is_symlink():
            continue
        try:
            valid_name(entry.name)
        except InvalidName:
            continue
        if any((entry / mark).is_dir() for mark in _SESSION_MARKS):
            found.append(entry.name)
    return sorted(found)


def from_settings(data: DataSettings) -> LocalFlowStore | None:
    """The store the config asks for, or None when the data directory is unset."""
    if not data.dir:
        log.info("flows: off (set data.dir to enable them)")
        return None
    root = Path(data.dir)
    stranded = old_layout(root)
    if stranded:
        from ..config import ConfigError  # local: config imports names, not us

        raise ConfigError(
            f"{', '.join(stranded)} in {root} are session folders from before "
            f"the sessions/ layout: move them into {root / SESSIONS_DIR}/"
        )
    log.info("flows: local, under %s", root / SESSIONS_DIR)
    return LocalFlowStore(root / SESSIONS_DIR)
```

Update the module docstring's two `FLOW_DATA_DIR` mentions to `DATA_DIR` and say sessions live under `DATA_DIR/sessions`.

- [ ] **Step 6: Wiring.** `server.py:118`: `self.flows = flowstore.from_settings(settings.data)`. `main.py`: move `server = SeleniumMCP(settings, sources=loaded.sources)` inside a `try` that catches `config.ConfigError` the same way `load` does:

```python
    try:
        server = SeleniumMCP(settings, sources=loaded.sources)
    except config.ConfigError as exc:
        raise SystemExit(f"selenium-flow: {exc}") from None
```

(`logging.basicConfig` and `quiet_the_wire()` stay before it.)

- [ ] **Step 7: Message text.** Replace `FLOW_DATA_DIR` with `DATA_DIR` in the files listed under **Files** (both user-facing strings and comments). `rg -n "FLOW_DATA_DIR|flow\.data_dir|flow-data-dir" kubed ui/src skills README.md` must print nothing when done.

- [ ] **Step 8: Tests, mechanically.**

```bash
grep -rlE "data_dir|FLOW_DATA_DIR" tests | xargs sed -i -E \
  -e 's/flow=\{"data_dir": /data={"dir": /g' \
  -e 's/FLOW_DATA_DIR/DATA_DIR/g' \
  -e 's/flow\\\.data_dir/data\\.dir/g'
```

Then fix by hand what the sed cannot:
- `tests/test_config_schema.py:84` → `assert Settings(data={"dir": "   "}).data.dir is None`.
- `tests/test_config_load.py:150` → `("data:\n  dir: 1\n", r"data\.dir")`, and its docstring's `data_dir` → `dir`.
- `tests/test_flows.py:424-433` → `data={"dir": ...}`.
- `tests/test_config_wiring.py:44` sets `DATA_DIR` now; read the test and keep its intent.
- `tests/integration/conftest.py:129` → `"DATA_DIR": str(root / "data")`.
- Any test that builds a server through `Settings(data=...)` **and** then asserts a path on disk needs `/ "sessions"` inserted after the data dir. Run the suite (Step 10) and fix each such failure by inserting `"sessions"` — never by changing what the test asserts about the file.
- The golden `tests/golden/openapi.json`: regenerate (Step 10).

- [ ] **Step 9: CHANGELOG.** Under `[Unreleased]`, add: `- **Breaking:** \`FLOW_DATA_DIR\` is now \`DATA_DIR\`, and session folders live under \`DATA_DIR/sessions/\` — move them there once.`

- [ ] **Step 10: Run everything.**

Run: `PYTHONPATH=$PWD:$PYL python3 -S -m pytest -q -p no:randomly --ignore=tests/integration`
Expected: everything passes but the known env-only wiki test. Then `GOLDEN_UPDATE=1` on `tests/test_golden.py`, read the diff (only `FLOW_DATA_DIR` → `DATA_DIR` text), `scripts/generate_wiki.py` (Configuration.md shows the `data` section), `npm --prefix ui test`, ruff.

- [ ] **Step 11: Commit.**

```bash
git add -A kubed tests ui/src CHANGELOG.md wiki
git commit -m "DATA_DIR replaces FLOW_DATA_DIR; sessions live under sessions/

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The `recording` settings and `open_session(record=true)`

The capability and the gate, with the collector behind a two-method hook it does not exist yet to fill. The hook is what `SessionManager` calls; Task 5 supplies the real one.

**Files:**
- Modify: `kubed/selenium_flow/config.py`, `kubed/selenium_flow/session/settings.py:82-90`, `kubed/selenium_flow/session/sessions.py` (`__init__` ~265, `describe` ~287, `resolve` ~364, `open_browser` ~539, `end_browser` ~774), `kubed/selenium_flow/core/browser.py:187-274`, `kubed/selenium_flow/core/actions.py:145-245`, `kubed/selenium_flow/mcp/tools.py:343-383`, `kubed/selenium_flow/core/capabilities.py:131-164`, `kubed/selenium_flow/spec/schemas.py` (`current_session`)
- Test: create `tests/test_recording_open.py`; extend `tests/test_browser_choice.py`, `tests/test_config_load.py`

**Interfaces:**
- Consumes: `Settings.data` (Task 2).
- Produces: `config.RecordingSettings(enabled: bool, dir: str | None, wait: int, watch: Literal["auto","events","poll"], poll: int)`; `Settings.recording`; `config.recording_problem(settings) -> str | None`; `config.recording_dir(settings) -> str | None`; `session.settings.SETTINGS["record"]`; `sessions.RECORDING_OFF` (the verbatim 400); `SessionManager(..., recordings=None)` where `recordings` has `expect(session: str, grid_id: str, browser: str) -> None` and `ended(grid_id: str) -> None`; `Grid._options(browser, insecure=False, record=False, video_name=None)`; `Grid.open(browser, insecure=False, record=False, video_name=None)`; `Actions.open_session(..., record=None, video_name=None)`; `describe()` and `open_session` results carry `recording: bool`.

- [ ] **Step 1: Write the failing tests.** `tests/test_recording_open.py`:

```python
"""open_session(record=true): the capability, the gate, and the hook."""

import pytest

from kubed.selenium_flow import config
from kubed.selenium_flow.session import sessions as sessions_module
from kubed.selenium_flow.session.sessions import Caller, SessionManager

pytestmark = pytest.mark.unit


class Recorder:
    """The hook SessionManager calls; records what it was told."""

    def __init__(self):
        self.expected, self.finished = [], []

    def expect(self, session, grid_id, browser):
        self.expected.append((session, grid_id, browser))

    def ended(self, grid_id):
        self.finished.append(grid_id)


class Grid:
    def __init__(self):
        self.alive, self.opened, self.quit = set(), [], []

    def is_alive(self, sid):
        return sid in self.alive


class Actions:
    """Answers the two calls SessionManager makes when opening and ending."""

    def __init__(self):
        self.grid = Grid()
        self.calls = []
        self.n = 0

    def open_session(self, **kwargs):
        self.n += 1
        sid = f"grid{self.n:04d}"
        self.grid.alive.add(sid)
        self.calls.append(kwargs)
        settings = {"browser": "chrome", "width": 1280, "height": 900}
        if kwargs.get("record"):
            settings["record"] = True
        return {"session_id": sid, "browser": "chrome", "url": "about:blank",
                "title": "", "width": 1280, "height": 900, "settings": settings}

    def end_browser(self, sid):
        self.grid.alive.discard(sid)
        return {"success": True}


def manager(recorder=None):
    return SessionManager(Actions(), recordings=recorder)


def caller(name="bot"):
    return Caller(name, "query")


def test_record_without_recording_set_up_is_a_400_and_opens_nothing():
    m = manager(None)
    with pytest.raises(ValueError) as exc:
        m.open_browser(caller(), record=True)
    assert str(exc.value) == sessions_module.RECORDING_OFF
    assert m.actions.calls == []


def test_record_sends_the_capability_and_tells_the_collector():
    rec = Recorder()
    m = manager(rec)
    result = m.open_browser(caller(), record=True)
    assert m.actions.calls[-1]["record"] is True
    assert m.actions.calls[-1]["video_name"] == "bot"
    assert rec.expected == [("bot", "grid0001", "chrome")]
    assert result["recording"] is True
    assert "grid0001" not in str(result)


def test_an_explicit_open_after_end_does_not_inherit_record():
    rec = Recorder()
    m = manager(rec)
    m.open_browser(caller(), record=True)
    m.end_browser(caller())
    result = m.open_browser(caller())
    assert not m.actions.calls[-1].get("record")
    assert result["recording"] is False
    assert rec.finished == ["grid0001"]


def test_a_reap_replays_record_and_expects_the_new_browser():
    rec = Recorder()
    m = manager(rec)
    m.open_browser(caller(), record=True)
    m.actions.grid.alive.clear()  # the Grid reaped it
    m.resolve("bot")
    assert m.actions.calls[-1]["record"] is True
    assert m.actions.calls[-1]["video_name"] == "bot"
    assert rec.expected[-1] == ("bot", "grid0002", "chrome")


def test_a_note_that_cannot_be_written_keeps_the_browser_and_says_why():
    class Broken(Recorder):
        def expect(self, session, grid_id, browser):
            raise PermissionError(13, "denied", "/data/sessions/bot/recordings")

    m = manager(Broken())
    result = m.open_browser(caller(), record=True)
    assert result["recording"] is True
    assert result["recording_error"] == "this recording cannot be filed: PermissionError"
    assert "/data" not in result["recording_error"]
    assert m.actions.grid.alive == {"grid0001"}


def test_describe_says_whether_the_held_browser_is_recorded():
    rec = Recorder()
    m = manager(rec)
    assert m.describe(caller())["recording"] is False
    m.open_browser(caller(), record=True)
    assert m.describe(caller())["recording"] is True
    m.end_browser(caller())
    assert m.describe(caller())["recording"] is False


def test_recording_enabled_without_a_data_dir_does_not_boot():
    with pytest.raises(config.ConfigError, match="recording.enabled needs data.dir"):
        config.load([], {"RECORDING_ENABLED": "true"})


def test_the_inbox_defaults_inside_the_data_dir_and_can_be_set_apart():
    s = config.Settings(data={"dir": "/d"})
    assert config.recording_dir(s) == "/d/recordings"
    s = config.Settings(data={"dir": "/d"}, recording={"dir": "/elsewhere"})
    assert config.recording_dir(s) == "/elsewhere"
    assert config.recording_dir(config.Settings()) is None
```

In `tests/test_browser_choice.py`, beside the `se:downloadsEnabled` assertions (~53–95), add:

```python
@pytest.mark.parametrize("name", ["chrome", "firefox"])
def test_recording_asks_the_grid_for_video_only_when_asked(name):
    plain = Grid("http://grid.invalid")._options(name).to_capabilities()
    assert "se:recordVideo" not in plain and "se:videoName" not in plain
    caps = Grid("http://grid.invalid")._options(
        name, record=True, video_name="bot"
    ).to_capabilities()
    assert caps["se:recordVideo"] is True
    assert caps["se:videoName"] == "bot"
```

(Use the same `Grid` import and construction the surrounding tests use.)

- [ ] **Step 2: Run to verify they fail.** Run: `... pytest -q -p no:randomly tests/test_recording_open.py tests/test_browser_choice.py`. Expected: FAIL (`unexpected keyword argument 'recordings'`, `'record'`).

- [ ] **Step 3: `config.py`.** After `DataSettings`, add:

```python
class RecordingSettings(Section):
    enabled: bool = Field(
        False, description="The Grid's recordings reach recording.dir."
    )
    dir: str | None = Field(
        None, description="Where the Grid's recordings arrive. Unset is data.dir/recordings."
    )
    wait: int = Field(
        600, ge=30, description="Seconds after a browser ends to wait for its recording."
    )
    watch: Literal["auto", "events", "poll"] = Field(
        "auto", description="auto, events or poll: how recording.dir is watched."
    )
    poll: int = Field(
        1000, ge=200, description="Milliseconds between looks, when polling."
    )

    @field_validator("dir")
    @classmethod
    def _blank_is_unset(cls, value):
        return (value or "").strip() or None
```

In `Settings`, after `data`, add:

```python
    recording: RecordingSettings = Field(
        default_factory=RecordingSettings,
        description="Video of a browser's whole life, from the Grid's recorder.",
    )
```

After `oidc_problem`, add:

```python
def recording_problem(settings: Settings) -> str | None:
    """Why recording cannot run, or None. Checked after every layer merges."""
    if settings.recording.enabled and not settings.data.dir:
        return "recording.enabled needs data.dir: a recording is filed into its session"
    return None


def recording_dir(settings: Settings) -> str | None:
    """The inbox: recording.dir, else data.dir/recordings, else None."""
    if settings.recording.dir:
        return settings.recording.dir
    if settings.data.dir:
        return str(Path(settings.data.dir) / INBOX_DIR)
    return None
```

Import `INBOX_DIR` from `.names`. In `load`, after the `oidc_problem` check, add the same three lines for `recording_problem`.

- [ ] **Step 4: `session/settings.py`.** In `SETTINGS`, after `insecure`:

```python
    # Explicit only, like insecure: no parameter, no header, no default. Stored
    # so a reap replays it — but `open_browser` never inherits it (recordings
    # spec, ruling 3): video is costly, and ending the browser ends it.
    "record": (None, None, _as_flag),
```

- [ ] **Step 5: `core/browser.py`.** `_options(self, browser=None, insecure=False, record=False, video_name=None)`; after the `insecure` block add:

```python
        # `open_session(record=true)`: the Grid's own recorder films this
        # browser's whole life (recordings spec). The name only makes the
        # operator's inbox readable; nothing matches on it, because the
        # recorder strips `.` from names and two sessions could collide.
        if record:
            options.set_capability("se:recordVideo", True)
            if video_name:
                options.set_capability("se:videoName", video_name)
```

`open(self, browser=None, insecure=False, record=False, video_name=None)` passes both through to `_options`.

- [ ] **Step 6: `core/actions.py`.** `open_session(self, url=None, browser=None, width=None, height=None, page_load_timeout=None, script_timeout=None, insecure=None, record=None, video_name=None, site_data=None)`; `record = as_bool(record, False)`; `driver = self.grid.open(name, insecure=insecure, record=record, video_name=video_name)`; after `if insecure: applied["insecure"] = True` add `if record: applied["record"] = True`. Docstring: one sentence — "``record`` asks the Grid to film this browser's whole life."

- [ ] **Step 7: `session/sessions.py`.** At module level:

```python
RECORDING_OFF = (
    "Recording is not set up on this server (recording.enabled is off). "
    "See the README's Recording section."
)
```

`SessionManager.__init__(..., defaults=None, recordings=None)`: `self.recordings = recordings` with a comment: "Who files a recorded browser's video (`recordings.Collector`), or None when recording is off. Told on open and on end; it owns no browser."

In `open_browser`, change the `previous=` argument so `record` is never inherited, and gate before anything is ended:

```python
        inherited_settings = {
            k: v for k, v in (previous.get("settings") or {}).items() if k != "record"
        }
        resolved = settings_module.resolve(
            wanted,
            defaults=self.defaults,
            previous=inherited_settings,
            client=caller.defaults,
        )
        if resolved.get("record") and self.recordings is None:
            raise ValueError(RECORDING_OFF)
```

Pass `video_name=name` into `self.actions.open_session(...)`. After `kept = self.remember(...)` and the race check, before building `told`:

```python
        noted = None
        if resolved.get("record"):
            try:
                self.recordings.expect(
                    name, opened["session_id"], resolved.get("browser") or DEFAULT_BROWSER
                )
            except OSError as exc:
                # The browser is open and recording on the Grid; only the filing
                # failed. Said in the result, as `file_error` is for a screenshot,
                # and never with the path (names a layout nobody asked about).
                log.warning("recording for %s cannot be filed: %s", name, faults.message(exc))
                noted = f"this recording cannot be filed: {type(exc).__name__}"
```

then build `told` as today and add `told["recording"] = bool(resolved.get("record"))` and, when `noted`, `told["recording_error"] = noted`. Add `"recording_error"` (string, "Why this recording cannot be filed, when it cannot.") to the `open_session` response shape in Step 9. In `_held`, add `"recording": bool(settings.get("record"))`.

In `resolve`'s reopen, pass `video_name=name` to `open_session`, and after `self.remember(...)` returns `kept`:

```python
        if (record.settings or {}).get("record") and self.recordings is not None:
            self.recordings.expect(name, opened["session_id"], (record.settings or {}).get("browser") or DEFAULT_BROWSER)
        return kept
```

(Restructure the `return self.remember(...)` into `kept = self.remember(...)` first.) A reap ends the old browser's recording on the Grid's side; the collector learns that from the file, or from its own liveness check — no `ended` call is needed here.

In `end_browser`, after `self.actions.end_browser(target)` (inside the `try`, after it succeeds or fails — put it after the `except`):

```python
        if self.recordings is not None:
            self.recordings.ended(target)
```

In `describe`, add `"recording": False` to the initial `status`, and after `status["live"] = ...`:

```python
        status["recording"] = bool(status["live"] and status["settings"].get("record"))
```

- [ ] **Step 8: `mcp/tools.py`.** Add `record: bool | None = None,` after `insecure` in `open_session`'s signature, pass `record=record` to `open_browser`, and add to the docstring after the `insecure` sentence:

```
        record=true films this browser's whole life as a video, from now until it
        ends; it appears under session://files/recordings shortly after. A person
        watches it, so only when one will — it costs the Grid. Not inherited: ask
        again for the next browser.
```

- [ ] **Step 9: Response shapes.** `core/capabilities.py`, `open_session`'s `response.properties`, after `"settings"`:

```python
                "recording": {
                    "type": "boolean",
                    "description": "Whether this browser is being recorded.",
                },
```

`spec/schemas.py`, `current_session.properties`, after `"live"`:

```python
            "recording": {
                "type": "boolean",
                "description": "Whether the browser this session holds is being recorded.",
            },
```

- [ ] **Step 10: Run, regenerate, lint.** The new tests and `tests/test_sessions.py tests/test_surfaces.py tests/test_openapi.py` pass; `GOLDEN_UPDATE=1` on `tests/test_golden.py` (the diff: `record` in the `open_session` schema, `recording` in two responses, the `recording` config section); `scripts/generate_wiki.py`; ruff; the full suite.

- [ ] **Step 11: Commit** — `open_session(record=true) asks the Grid to record` with the trailer.

---

### Task 4: The Recordings folder in the store, and the completion check

**Files:**
- Modify: `kubed/selenium_flow/names.py`, `kubed/selenium_flow/flows/store.py` (`FileStore`, ~398–505), `kubed/selenium_flow/http/files.py` (`RESERVED`, `_candidates`, `_unreserved` → imported from `names`)
- Create: `kubed/selenium_flow/recordings/__init__.py`, `kubed/selenium_flow/recordings/mp4.py`
- Test: create `tests/test_recording_store.py`, `tests/test_recording_mp4.py`

**Interfaces:**
- Produces: `names.RECORDINGS_DIR = "recordings"`; `names.FOLDERS = (FILES_DIR, SCREENSHOTS_DIR, RECORDINGS_DIR)`; `names.RESERVED_IN_FILES = frozenset({"screenshots", "downloads", "recordings"})`; `names.candidates(name: str) -> Iterator[str]`; `names.valid_grid_id(value: str) -> str` (raises `InvalidName`); `FileStore.file_path(session, name, folder=FILES_DIR) -> Path`; `FileStore.move_in(session, source: Path, name: str, folder: str) -> dict` (entry of the landed file); `FileStore.write_note(session, grid_id, note: dict) -> None`; `FileStore.notes() -> list[tuple[str, str, dict]]` (`(session, grid_id, note)`); `FileStore.delete_note(session, grid_id) -> bool`; `recordings.mp4.is_complete(path) -> bool`; `recordings.mp4.trailer(mfra_payload: bytes = b"") -> bytes` (for tests and docs: a valid `mfra` box).

- [ ] **Step 1: Write the failing tests.** `tests/test_recording_mp4.py`:

```python
"""A finished recording ends in an mfro box (ffmpeg movenc.c, mov_write_mfra_tag)."""

import pytest

from kubed.selenium_flow.recordings import mp4

pytestmark = pytest.mark.unit

BODY = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 16 + b"\x00\x00\x00\x10moof" + b"\x00" * 200


def write(tmp_path, data):
    path = tmp_path / "v.mp4"
    path.write_bytes(data)
    return path


def test_a_file_that_ends_in_a_valid_mfra_is_complete(tmp_path):
    assert mp4.is_complete(write(tmp_path, BODY + mp4.trailer()))


def test_a_trailer_with_tfra_entries_is_complete(tmp_path):
    assert mp4.is_complete(write(tmp_path, BODY + mp4.trailer(b"\x00" * 40)))


def test_a_file_still_growing_is_not(tmp_path):
    assert not mp4.is_complete(write(tmp_path, BODY))


def test_a_copy_cut_inside_the_trailer_is_not(tmp_path):
    assert not mp4.is_complete(write(tmp_path, (BODY + mp4.trailer())[:-5]))


def test_an_mfro_naming_the_wrong_size_is_not(tmp_path):
    bad = bytearray(BODY + mp4.trailer())
    bad[-1] ^= 0x01
    assert not mp4.is_complete(write(tmp_path, bytes(bad)))


def test_a_tiny_or_missing_file_is_not(tmp_path):
    assert not mp4.is_complete(write(tmp_path, b"\x00" * 10))
    assert not mp4.is_complete(tmp_path / "absent.mp4")
```

`tests/test_recording_store.py`:

```python
"""Recordings and notes in the session store."""

import json
import os

import pytest

from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.names import (
    FILES_DIR,
    RECORDINGS_DIR,
    InvalidName,
    candidates,
    valid_grid_id,
)

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path / "sessions")


def test_recordings_is_a_folder(store):
    store.write_file("bot", "a.mp4", b"x", RECORDINGS_DIR)
    assert [f["name"] for f in store.files("bot", RECORDINGS_DIR)] == ["a.mp4"]


def test_move_in_takes_a_file_from_outside_and_never_overwrites(store, tmp_path):
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    (inbox / f"bot_{GID}.mp4").write_bytes(b"one")
    first = store.move_in("bot", inbox / f"bot_{GID}.mp4", "rec-20261008-1403.mp4", RECORDINGS_DIR)
    (inbox / "x.mp4").write_bytes(b"two")
    second = store.move_in("bot", inbox / "x.mp4", "rec-20261008-1403.mp4", RECORDINGS_DIR)
    assert first["name"] == "rec-20261008-1403.mp4"
    assert second["name"] == "rec-20261008-1403 (1).mp4"
    assert not (inbox / f"bot_{GID}.mp4").exists() and not (inbox / "x.mp4").exists()
    assert store.read_file("bot", second["name"], RECORDINGS_DIR) == b"two"


def test_move_in_copies_when_a_link_is_impossible(store, tmp_path, monkeypatch):
    src = tmp_path / "v.mp4"
    src.write_bytes(b"data")
    monkeypatch.setattr(os, "link", lambda *_a, **_k: (_ for _ in ()).throw(OSError(18, "EXDEV")))
    landed = store.move_in("bot", src, "rec.mp4", RECORDINGS_DIR)
    assert store.read_file("bot", landed["name"], RECORDINGS_DIR) == b"data"
    assert not src.exists()


def test_moving_a_recording_into_files_skips_the_reserved_names(store, tmp_path):
    src = tmp_path / "recordings"
    src.write_bytes(b"v")
    landed = store.move_in("bot", src, "recordings", FILES_DIR)
    assert landed["name"] == "recordings (1)"


def test_notes_are_written_listed_and_deleted_and_never_listed_as_files(store):
    store.write_note("bot", GID, {"opened": 1, "ended": None, "browser": "chrome"})
    assert store.notes() == [("bot", GID, {"opened": 1, "ended": None, "browser": "chrome"})]
    assert store.files("bot", RECORDINGS_DIR) == []
    assert store.delete_note("bot", GID) is True
    assert store.notes() == []
    assert store.delete_note("bot", GID) is False


def test_a_broken_note_is_skipped_not_fatal(store):
    store.write_note("bot", GID, {"opened": 1})
    path = store.root / "bot" / RECORDINGS_DIR / ".pending" / f"{GID}.json"
    path.write_text("{not json")
    assert store.notes() == []


def test_grid_ids_are_validated_before_they_become_paths():
    assert valid_grid_id(GID) == GID
    assert valid_grid_id("bf532044-b55e-4c1a-9f0e-1234567890ab")
    for bad in ("../x", "a/b", "", "x" * 80, ".hidden"):
        with pytest.raises(InvalidName):
            valid_grid_id(bad)


def test_candidates_are_the_browser_naming_rule():
    it = candidates("a.mp4")
    assert [next(it) for _ in range(3)] == ["a.mp4", "a (1).mp4", "a (2).mp4"]
```

- [ ] **Step 2: Run to verify they fail** (`ModuleNotFoundError: kubed.selenium_flow.recordings`, `ImportError: RECORDINGS_DIR`).

- [ ] **Step 3: `names.py`.** Replace the folder constants with:

```python
# Where a session's own files land. `files` IS the Files section — a print, and
# anything kept; `screenshots` and `recordings` hold what the server made until
# it is kept or cleared (§F4.1; recordings spec, ruling 7). Downloads are not a
# folder here: they are the Grid's.
FILES_DIR = "files"
SCREENSHOTS_DIR = "screenshots"
RECORDINGS_DIR = "recordings"
FOLDERS = (FILES_DIR, SCREENSHOTS_DIR, RECORDINGS_DIR)

# The folder names a file in Files can never take, since session://files/<name>
# would then mean the folder (§F4.6). One arriving under one lands as `name (1)`.
RESERVED_IN_FILES = frozenset({SCREENSHOTS_DIR, "downloads", RECORDINGS_DIR})

# A Grid session id becomes a note's file name. The Grid hands us 32 hex or a
# dashed UUID; anything else is refused before it touches a path.
GRID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")


def valid_grid_id(value) -> str:
    """``value`` if it can name a note, else raise."""
    if not isinstance(value, str) or not GRID_ID.match(value):
        raise InvalidName(f"{value!r} is not a Grid session id")
    return value


def candidates(name: str):
    """``name``, then ``name (1)``, ``name (2)`` … the way a browser names a
    second download. Shared by every folder that never overwrites."""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    yield name
    n = 1
    while True:
        yield f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})"
        n += 1
```

Place `valid_grid_id` after `class InvalidName`. In `http/files.py`, delete `_candidates`, import `candidates` and `RESERVED_IN_FILES` from `..names`, and use `candidates(` in `_claim`; keep `RESERVED = frozenset({SCREENSHOTS, DOWNLOADS})` for now (Task 6 widens it) but make `_claim` and `_unreserved` skip `RESERVED_IN_FILES`.

- [ ] **Step 4: `recordings/__init__.py` and `recordings/mp4.py`.**

```python
"""Recordings: the Grid films a browser; this package files the video.

The Grid's recorder writes the MP4 and the operator's transport drops it in the
inbox (``recording.dir``). Nothing here talks to the Grid's recorder or moves a
file across machines (recordings spec, ruling 1). No protocol imports and no
``selenium``: the boundary test holds this package to it.
"""
```

```python
"""Whether a recording the Grid made is finished.

The recorder's ffmpeg writes a fragmented MP4 (``-movflags
frag_keyframe+empty_moov+default_base_moof``) and closes it with an ``mfra``
box whose last 16 bytes are an ``mfro`` box: size 16, ``mfro``, version and
flags 0, then the size of the whole ``mfra`` (FFmpeg ``movenc.c``,
``mov_write_trailer`` → ``mov_write_mfra_tag``). A file still being recorded,
or still being copied by whatever delivers it, cannot end that way. Only a
recording whose ffmpeg was killed has none — the collector handles that case.
"""

from __future__ import annotations

import os

TRAILER = 16


def trailer(tfra: bytes = b"") -> bytes:
    """A valid ``mfra`` box around ``tfra``: what a finished recording ends with."""
    size = 8 + len(tfra) + TRAILER
    mfro = (16).to_bytes(4, "big") + b"mfro" + b"\0\0\0\0" + size.to_bytes(4, "big")
    return size.to_bytes(4, "big") + b"mfra" + tfra + mfro


def is_complete(path) -> bool:
    """True when ``path`` ends in an ``mfro`` that names a real ``mfra``."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < 8 + TRAILER:
                return False
            f.seek(size - TRAILER)
            tail = f.read(TRAILER)
            if tail[:12] != (16).to_bytes(4, "big") + b"mfro" + b"\0\0\0\0":
                return False
            mfra = int.from_bytes(tail[12:], "big")
            if mfra < 8 + TRAILER or mfra > size:
                return False
            f.seek(size - mfra)
            head = f.read(8)
            return head[4:] == b"mfra" and int.from_bytes(head[:4], "big") == mfra
    except OSError:
        return False
```

- [ ] **Step 5: `FileStore` additions** (in `flows/store.py`, import `errno`, `json`, `shutil`, and `RECORDINGS_DIR, RESERVED_IN_FILES, candidates, valid_grid_id` from `..names`):

```python
PENDING_DIR = ".pending"
```

Inside `FileStore`:

```python
    def file_path(self, session: str, name: str, folder: str = FILES_DIR) -> Path:
        """Where one file lives, checked: for a route that streams it from disk."""
        return self._file_path(session, name, folder)

    def move_in(self, session: str, source: Path, name: str, folder: str) -> dict:
        """Take ``source`` into a folder under the first free name, and remove it.

        Never an overwrite: the claim is a hard link (or, where links cannot
        cross, an exclusive create and a copy), so a racing move cannot win the
        same name. Files skips its reserved names, as `_claim` does.
        """
        source = Path(source)
        for candidate in candidates(valid_file_name(name)):
            if folder == FILES_DIR and candidate in RESERVED_IN_FILES:
                continue
            target = self._file_path(session, candidate, folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except FileExistsError:
                continue
            except OSError as exc:
                # No hard link here (another filesystem, or one that has none):
                # claim the name with an exclusive create and copy instead.
                if not (isinstance(exc, PermissionError) or exc.errno in _NO_LINK):
                    raise
                try:
                    with target.open("xb") as out, source.open("rb") as src:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                except FileExistsError:
                    continue
            source.unlink()
            return self._entry(target)
        raise AssertionError("unreachable")  # candidates is infinite

    def _note_path(self, session: str, grid_id: str) -> Path:
        return self._resolved(
            valid_name(session, "session name"),
            RECORDINGS_DIR,
            PENDING_DIR,
            f"{valid_grid_id(grid_id)}.json",
        )

    def write_note(self, session: str, grid_id: str, note: dict) -> None:
        """Write a recording's note whole: a temp file, then a rename."""
        path = self._note_path(session, grid_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(note), encoding="utf-8")
        os.replace(tmp, path)

    def delete_note(self, session: str, grid_id: str) -> bool:
        try:
            self._note_path(session, grid_id).unlink()
        except FileNotFoundError:
            return False
        return True

    def notes(self) -> list[tuple[str, str, dict]]:
        """Every owed recording, as ``(session, grid_id, note)``. A note that is
        not JSON, or names no usable id, is skipped with a warning."""
        found = []
        for session in self.sessions():
            try:
                pending = self._resolved(valid_name(session), RECORDINGS_DIR, PENDING_DIR)
            except InvalidName:
                continue
            if not pending.is_dir():
                continue
            for entry in sorted(pending.glob("*.json")):
                grid_id = entry.stem
                try:
                    valid_grid_id(grid_id)
                    note = json.loads(entry.read_text(encoding="utf-8"))
                except (InvalidName, ValueError, OSError):
                    log.warning("ignoring recording note %s/%s", session, entry.name)
                    continue
                if isinstance(note, dict):
                    found.append((session, grid_id, note))
        return found
```

Beside `PENDING_DIR`, define `_NO_LINK = frozenset({errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK})`. (The test fakes `OSError(18, "EXDEV")`; 18 is `errno.EXDEV` on Linux.)

Update `FileStore`'s docstring: three folders now, `screenshots` and `recordings` disposable until kept. `clear_folder` already refuses Files only — Recordings clears like Screenshots. `files()` lists regular files only, so `.pending/` never appears; the test proves it.

- [ ] **Step 6: Run, lint, full suite, commit** — `Recordings: the folder, notes, and the mfro check`.

---

### Task 5: The collector

**Files:**
- Create: `kubed/selenium_flow/recordings/mounts.py`, `kubed/selenium_flow/recordings/collector.py`
- Modify: `kubed/selenium_flow/server.py` (build it, the lifespan, the poke), `kubed/selenium_flow/http/admin/__init__.py` (`register` returns the `Broadcast`), `kubed/selenium_flow/http/admin/sessions.py` (`Broadcast.poke`), `pyproject.toml` (`watchfiles>=1.3`), `tests/test_boundaries.py` (if it lists packages explicitly, add `recordings`)
- Test: create `tests/test_recording_mounts.py`, `tests/test_recording_collector.py`

**Interfaces:**
- Consumes: `FileStore.move_in/write_note/notes/delete_note` (Task 4), `mp4.is_complete` (Task 4), `RECORDINGS_DIR`, `valid_grid_id` (Task 4); the hook contract `expect(session, grid_id, browser)` / `ended(grid_id)` (Task 3); `config.recording_dir` (Task 3).
- Produces: `mounts.network_filesystem(path, mountinfo="/proc/self/mountinfo") -> bool`; `mounts.polling(watch: str, path) -> bool`; `collector.Collector(store, inbox, *, alive, wait, polling, poll_ms, on_filed=None, clock=time.time, tick=30.0, idle_after=60.0)` with `expect`, `ended`, `async start()`, `async stop()`, `async sweep()`, `owed: dict[str, Owed]`; `collector.name_for(opened_ms: int) -> str`; `Broadcast.poke()`.

- [ ] **Step 1: Write the failing tests.** `tests/test_recording_mounts.py`:

```python
import pytest

from kubed.selenium_flow.recordings import mounts

pytestmark = pytest.mark.unit

INFO = """\
22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw
40 22 0:50 / /data/flows rw,relatime - nfs4 nas:/volume1/flows rw
41 22 0:51 / /mnt/my\\040share rw - cifs //nas/share rw
42 22 0:52 / /data/local rw - xfs /dev/sdb1 rw
"""


@pytest.fixture
def info(tmp_path):
    path = tmp_path / "mountinfo"
    path.write_text(INFO)
    return str(path)


def test_the_longest_mount_decides(info):
    assert mounts.network_filesystem("/data/flows/recordings", info)
    assert not mounts.network_filesystem("/data/local/recordings", info)
    assert not mounts.network_filesystem("/tmp/x", info)
    assert mounts.network_filesystem("/mnt/my share/v", info)


def test_no_proc_means_events(tmp_path):
    assert not mounts.network_filesystem("/x", str(tmp_path / "absent"))


def test_watch_overrides_auto(info, monkeypatch):
    monkeypatch.setattr(mounts, "network_filesystem", lambda p, *_: True)
    assert mounts.polling("auto", "/x") is True
    assert mounts.polling("events", "/x") is False
    assert mounts.polling("poll", "/x") is True
```

`tests/test_recording_collector.py`:

```python
"""The collector files a recording as soon as it is finished, and only then."""

import asyncio
import logging

import pytest

from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.names import RECORDINGS_DIR
from kubed.selenium_flow.recordings import collector as collector_module
from kubed.selenium_flow.recordings import mp4

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"
BODY = b"\x00\x00\x00\x10moof" + b"\x00" * 100


class Clock:
    def __init__(self, now=1_791_500_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def parts(tmp_path):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    alive = {GID}
    filed = []
    clock = Clock()
    c = collector_module.Collector(
        store, inbox, alive=lambda gid: gid in alive, wait=600, polling=True,
        poll_ms=50, on_filed=lambda: filed.append(1), clock=clock, tick=0.1,
        idle_after=60.0,
    )
    return c, store, inbox, alive, filed, clock


async def test_nothing_is_filed_while_the_file_grows(parts):
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    await c.sweep()
    assert store.files("bot", RECORDINGS_DIR) == [] and filed == []


async def test_a_file_that_ends_in_mfro_is_filed_and_its_note_goes(parts):
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    names = [f["name"] for f in store.files("bot", RECORDINGS_DIR)]
    assert names == [collector_module.name_for(int(clock.now * 1000))]
    assert store.notes() == [] and GID not in c.owed and filed == [1]
    assert not (inbox / f"bot_{GID}.mp4").exists()


async def test_a_match_deep_in_the_inbox_is_found_and_partials_are_not(parts):
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    deep = inbox / "a" / GID
    deep.mkdir(parents=True)
    (deep / f"x_{GID}.mp4.partial").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == []
    (deep / f"x_{GID}.mp4.partial").rename(deep / f"x_{GID}.mp4")
    await c.sweep()
    assert filed == [1]


async def test_a_cut_off_file_waits_for_the_browser_and_a_minute_of_quiet(parts):
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    await c.sweep()
    clock.now += 61
    await c.sweep()  # quiet, but the browser lives: never early
    assert filed == []
    alive.clear()
    clock.now += 61
    await c.sweep()
    assert filed == [1]


async def test_a_file_that_never_comes_is_dropped_after_wait_with_a_warning(parts, caplog):
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    alive.clear()
    clock.now += 599
    await c.sweep()
    assert store.notes() != []
    clock.now += 2
    with caplog.at_level(logging.WARNING):
        await c.sweep()
    assert store.notes() == [] and GID not in c.owed
    assert "never reached" in caplog.text and GID not in caplog.text


async def test_a_reap_is_noticed_by_the_tick(parts):
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    alive.clear()
    await c.sweep()
    assert c.owed[GID].ended == int(clock.now * 1000)
    assert store.notes()[0][2]["ended"] == int(clock.now * 1000)


async def test_the_task_runs_only_while_something_is_owed_and_survives_a_restart(parts):
    c, store, inbox, alive, filed, clock = parts
    await c.start()
    assert c.running is False
    c.expect("bot", GID, "chrome")
    await asyncio.sleep(0.05)
    assert c.running is True
    await c.stop()
    # A new process: the note is the queue.
    c2 = collector_module.Collector(
        store, inbox, alive=lambda gid: False, wait=600, polling=True, poll_ms=50,
        clock=clock, tick=0.1,
    )
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c2.start()
    for _ in range(50):
        if not c2.owed:
            break
        await asyncio.sleep(0.05)
    assert c2.owed == {} and c2.running is False
    assert len(store.files("bot", RECORDINGS_DIR)) == 1
    await c2.stop()


async def test_two_sessions_never_claim_each_others_files(parts):
    c, store, inbox, alive, filed, clock = parts
    other = "0123456789abcdef0123456789abcdef"
    alive.add(other)
    c.expect("a.b", GID, "chrome")
    c.expect("ab", other, "chrome")
    # The recorder strips "." from names: both files start "ab_".
    (inbox / f"ab_{other}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert store.files("a.b", RECORDINGS_DIR) == []
    assert len(store.files("ab", RECORDINGS_DIR)) == 1
    assert GID in c.owed and other not in c.owed


async def test_an_unknown_end_is_ignored(parts):
    c, *_ = parts
    c.ended("ffffffffffffffffffffffffffffffff")
    await asyncio.sleep(0)
    assert c.owed == {}
```

- [ ] **Step 2: Run to verify they fail** (`ModuleNotFoundError: ...recordings.mounts`).

- [ ] **Step 3: `recordings/mounts.py`.**

```python
"""Whether the inbox is on a network filesystem, where events see nothing.

inotify reports only changes made through this machine's own mount: a file an
NFS, SMB or FUSE peer writes raises no event (recordings spec, research). So
``auto`` polls there and uses events everywhere else, including where there is
no ``/proc`` to ask.
"""

from __future__ import annotations

import os
from pathlib import Path

NETWORK = frozenset({"nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "ceph", "glusterfs"})


def network_filesystem(path, mountinfo: str = "/proc/self/mountinfo") -> bool:
    """True when the mount holding ``path`` is a network or FUSE filesystem."""
    try:
        lines = Path(mountinfo).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    target = os.path.realpath(path)
    best_point, best_type = "", ""
    for line in lines:
        left, sep, right = line.partition(" - ")
        fields = left.split()
        if not sep or len(fields) < 5 or not right.split():
            continue
        point = fields[4].replace("\\040", " ")
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and len(point) >= len(best_point):
            best_point, best_type = point, right.split()[0]
    return best_type in NETWORK or best_type.startswith("fuse")


def polling(watch: str, path) -> bool:
    """Whether to poll the inbox, from ``recording.watch``."""
    if watch == "poll":
        return True
    if watch == "events":
        return False
    return network_filesystem(path)
```

- [ ] **Step 4: `recordings/collector.py`.**

```python
"""The collector: files each owed recording into its session as it finishes.

**The queue is the notes on disk** (``sessions/<name>/recordings/.pending/
<gridId>.json``), written when a recorded browser opens; **the engine is one
task per process**, which runs only while a recording is owed and ends when
none is — the shape the admin broadcast has (AGENTS.md: a bounded wait for
something this server was told to expect is allowed; a loop that tidies is
not). The notes survive a restart, so ``start`` picks up where the last
process left off.

It wakes on a change in the inbox (``watchfiles``: events, or polling on a
network filesystem) and on a timer, and each time sweeps: a file whose name
holds an owed Grid id and ends in ``mfro`` is moved into the session's
recordings and its note deleted; one with no ``mfro`` is moved as it is once
it has been quiet for a minute **and** the browser is gone (a live recording
writes a keyframe at least every ~17 s, so this never files one early); an
owed browser not yet known to have ended is asked about, so a file that never
comes has a deadline to miss.

It owns no browser and takes no session lock. ``expect`` and ``ended`` are
called from worker threads (FastMCP's sync tools, Starlette's routes), so they
write to disk there and hand the rest to the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import anyio
from watchfiles import awatch

from ..names import RECORDINGS_DIR, InvalidName, valid_grid_id
from . import mp4

log = logging.getLogger(__name__)

# A transport that writes a temporary name and renames it is still copying.
PARTIAL_SUFFIXES = (".partial", ".part", ".tmp")


def name_for(opened_ms: int) -> str:
    """``rec-YYYYMMDD-HHMM.mp4``, UTC, from when the browser opened."""
    return time.strftime("rec-%Y%m%d-%H%M.mp4", time.gmtime(opened_ms / 1000))


@dataclass
class Owed:
    session: str
    grid_id: str
    opened: int
    browser: str
    ended: int | None = None
    checked: float = 0.0


class Collector:
    def __init__(
        self,
        store,
        inbox,
        *,
        alive,
        wait: int,
        polling: bool,
        poll_ms: int,
        on_filed=None,
        clock=time.time,
        tick: float = 30.0,
        idle_after: float = 60.0,
    ):
        self.store = store
        self.inbox = Path(inbox)
        self.alive = alive
        self.wait = wait
        self.polling = polling
        self.poll_ms = poll_ms
        self.on_filed = on_filed
        self.clock = clock
        self.tick = tick
        self.idle_after = idle_after
        self.owed: dict[str, Owed] = {}
        # path -> (size, mtime, first seen at that size and mtime)
        self._quiet: dict[str, tuple[int, float, float]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._stop: anyio.Event | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---- from worker threads ----------------------------------------------

    def expect(self, session: str, grid_id: str, browser: str) -> None:
        """A recorded browser opened: note it on disk, then tell the loop."""
        valid_grid_id(grid_id)
        owed = Owed(session, grid_id, int(self.clock() * 1000), browser)
        self.store.write_note(session, grid_id, self._note(owed))
        self._post(lambda: self._add(owed))

    def ended(self, grid_id: str) -> None:
        """A browser was ended. Ignored unless it is owed."""
        self._post(lambda: self._mark_ended(grid_id))

    def _post(self, fn) -> None:
        loop = self._loop
        if loop is None:
            # Not started: nothing runs yet, so there is no task to wake and no
            # thread to cross. Apply it here; `start` re-reads the notes anyway.
            fn()
            return
        if loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn()
        else:
            loop.call_soon_threadsafe(fn)

    # ---- on the loop ------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for session, grid_id, note in await anyio.to_thread.run_sync(self.store.notes):
            self.owed[grid_id] = Owed(
                session, grid_id, int(note.get("opened") or 0),
                str(note.get("browser") or ""), note.get("ended"),
            )
        if self.owed:
            log.info("recordings: %d owed from before the restart", len(self.owed))
            self._ensure()

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _add(self, owed: Owed) -> None:
        self.owed[owed.grid_id] = owed
        self._ensure()

    def _mark_ended(self, grid_id: str) -> None:
        owed = self.owed.get(grid_id)
        if owed is None or owed.ended is not None:
            return
        owed.ended = int(self.clock() * 1000)
        self._write(owed)

    def _ensure(self) -> None:
        if not self.running and self._loop is not None:
            self._task = self._loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self.owed:
                self._stop = anyio.Event()
                await self.sweep()
                if not self.owed:
                    break
                async for _changes in awatch(
                    self.inbox,
                    stop_event=self._stop,
                    force_polling=self.polling,
                    poll_delay_ms=self.poll_ms,
                    yield_on_timeout=True,
                    rust_timeout=max(1, int(self.tick * 1000)),
                    watch_filter=None,
                    recursive=True,
                ):
                    await self.sweep()
                    if not self.owed:
                        self._stop.set()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - logged; the notes keep the queue
            log.exception("recordings: the collector stopped; it resumes on the next recording")
        finally:
            if self._task is asyncio.current_task():
                self._task = None

    async def sweep(self) -> None:
        """One look: file what is finished, ask about the rest, drop the late."""
        files = await anyio.to_thread.run_sync(self._inbox_files)
        now = self.clock()
        for grid_id, owed in list(self.owed.items()):
            match = next((p for p in files if grid_id in p.name), None)
            if match is not None:
                if await anyio.to_thread.run_sync(mp4.is_complete, match):
                    await self._file(owed, match)
                elif self._quiet_for(match, now) >= self.idle_after and not await self._is_alive(owed):
                    log.info("recordings: %s/%s ends without a trailer; filed as it is", owed.session, name_for(owed.opened))
                    await self._file(owed, match)
                continue
            if owed.ended is None and now - owed.checked >= self.tick:
                owed.checked = now
                if not await self._is_alive(owed):
                    self._mark_ended(grid_id)
            if owed.ended is not None and now * 1000 - owed.ended >= self.wait * 1000:
                await anyio.to_thread.run_sync(self.store.delete_note, owed.session, grid_id)
                self.owed.pop(grid_id, None)
                log.warning(
                    "recording for session %s (opened %s UTC) never reached "
                    "RECORDING_DIR; see the README's Recording section",
                    owed.session, time.strftime("%H:%M", time.gmtime(owed.opened / 1000)),
                )

    async def _is_alive(self, owed: Owed) -> bool:
        try:
            return bool(await anyio.to_thread.run_sync(self.alive, owed.grid_id))
        except Exception:  # noqa: BLE001 - the Grid can blip; assume it lives
            return True

    async def _file(self, owed: Owed, path: Path) -> None:
        try:
            entry = await anyio.to_thread.run_sync(
                self.store.move_in, owed.session, path, name_for(owed.opened), RECORDINGS_DIR
            )
        except (OSError, InvalidName) as exc:
            log.warning("recordings: could not file %s for %s: %s", path.name, owed.session, type(exc).__name__)
            return
        await anyio.to_thread.run_sync(self.store.delete_note, owed.session, owed.grid_id)
        self.owed.pop(owed.grid_id, None)
        self._quiet.pop(str(path), None)
        log.info("recordings: filed %s/%s", owed.session, entry["name"])
        if self.on_filed is not None:
            self.on_filed()

    def _quiet_for(self, path: Path, now: float) -> float:
        try:
            info = path.stat()
        except OSError:
            return 0.0
        key = str(path)
        seen = self._quiet.get(key)
        if seen is None or seen[0] != info.st_size or seen[1] != info.st_mtime:
            self._quiet[key] = (info.st_size, info.st_mtime, now)
            return 0.0
        return now - seen[2]

    def _inbox_files(self) -> list[Path]:
        found = []
        for root, dirs, names in os.walk(self.inbox):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                if name.startswith(".") or name.endswith(PARTIAL_SUFFIXES):
                    continue
                found.append(Path(root) / name)
        return found

    def _note(self, owed: Owed) -> dict:
        return {"opened": owed.opened, "ended": owed.ended, "browser": owed.browser}

    def _write(self, owed: Owed) -> None:
        try:
            self.store.write_note(owed.session, owed.grid_id, self._note(owed))
        except OSError as exc:
            log.warning("recordings: could not update a note for %s: %s", owed.session, type(exc).__name__)
```

Notes for the implementer: `_mark_ended` writes the note on the loop thread — a tiny JSON write; acceptable. The `watch_filter=None` keeps every change (the default filter drops dotfiles and `.tmp`, which is fine either way since `sweep` re-reads the directory). `owed.checked` starts at `0.0`, so the first sweep asks the Grid once.

- [ ] **Step 5: `Broadcast.poke`.** In `http/admin/sessions.py`, `class Broadcast`, after `invalidate`:

```python
    def poke(self) -> None:
        """Something an open page should see changed (a recording was filed):
        forget the last broadcast and tick now. Called on the event loop."""
        self.invalidate()
        self._nudge.set()
```

`http/admin/__init__.py`'s `register` ends with `return broadcast` (update its docstring: "Returns the session list's `Broadcast`, so the server can tell open pages a recording was filed").

- [ ] **Step 6: `server.py`.** Imports: `from contextlib import asynccontextmanager`, `from pathlib import Path`, `from .recordings import collector as recording_collector, mounts`. Before `self.sessions = SessionManager(...)`, build the collector when recording is on:

```python
        # Recordings (recordings spec): the Grid films, the operator delivers to
        # the inbox, the collector files. Built before the sessions, which tell
        # it about every recorded browser; None when recording is off.
        self.collector = None
        problem = config.recording_problem(settings)
        if problem:
            # `load` checks this too; Settings built in code skips `load`.
            raise config.ConfigError(problem)
        if settings.recording.enabled:
            inbox = Path(config.recording_dir(settings))
            if not inbox.is_dir() or not os.access(inbox, os.R_OK | os.W_OK | os.X_OK):
                raise config.ConfigError(
                    f"recording.dir {inbox} must exist and be readable and writable "
                    "by this server: filing a recording moves it out"
                )
```

Then, because the store is built after the sessions today, **move** `self.flows = flowstore.from_settings(settings.data)` up to just after `self.skill = ...`, and finish the collector there:

```python
            self.collector = recording_collector.Collector(
                self.flows,
                inbox,
                alive=self.grid.is_alive,
                wait=settings.recording.wait,
                polling=mounts.polling(settings.recording.watch, inbox),
                poll_ms=settings.recording.poll,
            )
            log.info(
                "recordings: on, inbox %s, %s",
                inbox, "polling" if self.collector.polling else "events",
            )
```

Pass `recordings=self.collector` to `SessionManager(...)`. Delete the later `self.flows = ...` line. Build FastMCP with a lifespan:

```python
        collector = self.collector

        @asynccontextmanager
        async def lifespan(_server):
            if collector is not None:
                await collector.start()
            try:
                yield {}
            finally:
                if collector is not None:
                    await collector.stop()

        self.mcp = FastMCP(
            "Selenium",
            instructions=tools.first_instructions(self.skill is not None),
            auth=auth,
            lifespan=lifespan,
        )
```

Capture the admin broadcast and wire the poke: `broadcast = admin.register(...)`, then `if self.collector is not None: self.collector.on_filed = broadcast.poke`. Import `os`. Add `recordings=%s` to `main.py`'s config log line: `"on" if server.collector else "off"`.

- [ ] **Step 7: `pyproject.toml`.** In `dependencies`, after `pydantic-settings`:

```toml
  # The recordings collector watches the inbox with it: native events on a local
  # disk, polling on a network filesystem where events cannot see the other
  # side's writes. abi3 wheels from cp310 cover every Python CI runs.
  "watchfiles>=1.3",
```

- [ ] **Step 8: A server-level test.** Append to `tests/test_recording_collector.py`:

```python
def test_a_server_with_recording_on_needs_a_usable_inbox(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.server import SeleniumMCP

    settings = config.Settings(
        grid={"url": "http://grid.invalid:4444"},
        data={"dir": str(tmp_path)},
        recording={"enabled": True},
    )
    with pytest.raises(config.ConfigError, match="recording.dir"):
        SeleniumMCP(settings)
    (tmp_path / "recordings").mkdir()
    server = SeleniumMCP(settings)
    assert server.collector is not None and server.sessions.recordings is server.collector
```

- [ ] **Step 9: Run, boundaries, lint, full suite.** `tests/test_boundaries.py` must pass with the new package (it may need `recordings` added to its list of non-protocol packages — read it). Commit: `Recordings: the collector files each video as it finishes`.

---

### Task 6: Recordings on every surface

The Screenshots folder's twin (§F4.6–§F4.9), in one commit, plus streaming every disk-backed file with Range.

**Files:**
- Modify: `kubed/selenium_flow/http/files.py`, `kubed/selenium_flow/http/links.py`, `kubed/selenium_flow/http/admin/signed.py`, `kubed/selenium_flow/http/admin/files.py`, `kubed/selenium_flow/http/admin/sessions.py`, `kubed/selenium_flow/mcp/show.py`, `kubed/selenium_flow/spec/schemas.py`, `kubed/selenium_flow/spec/builder.py` (only if it enumerates folders by hand)
- Test: create `tests/test_recording_surfaces.py`; update `tests/test_file_surfaces.py`, `tests/test_kept_files.py` (`FILE_ENDPOINTS`), `tests/test_screenshot_saving.py` (CSP table), `tests/test_admin_files.py`, `tests/test_show.py`, `tests/golden/*`

**Interfaces:**
- Consumes: `RECORDINGS_DIR`, `RESERVED_IN_FILES`, `FileStore.file_path`, `FileStore.move_in` (Task 4).
- Produces: `files.RECORDINGS = "recordings"`; `files.RESERVED = RESERVED_IN_FILES`; `FOLDER_URI[RECORDINGS]`, `ITEM_URI[RECORDINGS]`; `FILE_ENDPOINTS = ("list", "screenshots", "downloads", "recordings", "keep")`; `FILE_ROUTES["recordings"] = ("get", "/recordings")`; `files.clear_recordings(store, session) -> dict`; `links.recording_path(session, name)`, `links.recording_url(session, name, token, mount="", ttl=...)`; `signed.headers_for(name, max_age) -> dict`; admin entry rows gain `counts.recordings`, `recording: bool`, and `files_rev` covers recordings.

- [ ] **Step 1: Write the failing tests.** `tests/test_recording_surfaces.py` (use the fixtures style of `tests/test_file_sections.py` — `Grid`, `Actions`, `Sessions` doubles, `store` on `tmp_path`):

```python
"""The Recordings folder on every surface: the Screenshots folder's twin."""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.http import files, links
from kubed.selenium_flow.http.admin import signed
from kubed.selenium_flow.names import FILES_DIR, RECORDINGS_DIR

pytestmark = pytest.mark.unit

S = "desktop"


class Grid:
    def files(self, _sid):
        return []

    def read_file(self, _sid, _name):
        return b""


class Actions:
    grid = Grid()


class Sessions:
    def browser(self, _name):
        return ""


@pytest.fixture
def store(tmp_path):
    s = flows.LocalFlowStore(tmp_path)
    s.write_file(S, "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
    return s


def test_addresses():
    assert files.uri_of(RECORDINGS_DIR, "a.mp4") == "session://files/recordings/a.mp4"
    assert files.parse_uri("session://files/recordings/a.mp4") == (RECORDINGS_DIR, "a.mp4")
    assert "recordings" in files.RESERVED


def test_the_root_names_a_third_folder(store):
    root = files.root(Actions(), Sessions(), store, None, S)
    named = {f["name"]: f for f in root["folders"]}
    assert named["recordings"]["count"] == 1
    assert named["recordings"]["uri"] == "session://files/recordings"


def test_a_recording_entry_is_a_file_entry_with_keep_with(store):
    listing = files.folder(Actions(), Sessions(), store, None, S, RECORDINGS_DIR)
    entry = listing["files"][0]
    assert entry["content_type"] == "video/mp4" and entry["image"] is False
    assert entry["keep_with"] == 'keep_file("session://files/recordings/rec-20261008-1403.mp4")'
    assert entry["url"].startswith("/recordings/desktop/")


def test_keeping_a_recording_moves_it_into_files(store):
    kept = files.keep(Actions(), Sessions(), store, "session://files/recordings/rec-20261008-1403.mp4", S)
    assert kept["uri"] == "session://files/rec-20261008-1403.mp4"
    assert store.files(S, RECORDINGS_DIR) == []
    assert [f["name"] for f in store.files(S, FILES_DIR)] == ["rec-20261008-1403.mp4"]


def test_clearing_recordings_leaves_files_and_notes(store):
    store.write_note(S, "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d", {"opened": 1})
    store.write_file(S, "keep.txt", b"k")
    assert files.clear_recordings(store, S)["cleared"] == 1
    assert store.files(S, RECORDINGS_DIR) == []
    assert len(store.notes()) == 1 and len(store.files(S)) == 1


def test_a_video_is_served_from_disk_with_range_and_no_sandbox(store):
    from fastmcp import FastMCP

    mcp = FastMCP("t")
    signed.mount(mcp, Actions(), store, None, "")
    client = TestClient(mcp.http_app())
    path = links.recording_path(S, "rec-20261008-1403.mp4")
    resp = client.get(path, headers={"Range": "bytes=0-9"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == "bytes 0-9/64"
    assert resp.headers["content-security-policy"] == signed.MEDIA_ONLY
    assert "sandbox" not in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_a_kept_video_in_files_is_served_the_same_way(store):
    from fastmcp import FastMCP

    files.keep(Actions(), Sessions(), store, "session://files/recordings/rec-20261008-1403.mp4", S)
    mcp = FastMCP("t")
    signed.mount(mcp, Actions(), store, None, "")
    resp = TestClient(mcp.http_app()).get(
        links.kept_path(S, "rec-20261008-1403.mp4"), headers={"Range": "bytes=0-0"}
    )
    assert resp.status_code == 206
```

And the MCP surface, as `tests/test_file_surfaces.py` drives it (an in-memory `Client` reads as the `stdio` session):

```python
import asyncio
import json

from fastmcp import Client

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.sessions import STDIO_NAME


@pytest.fixture
def srv(tmp_path):
    server = SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)}))
    server.flows.write_file(STDIO_NAME, "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
    return server


def test_the_folder_and_its_template_are_resources(srv):
    async def go():
        async with Client(srv.mcp) as c:
            listed = {str(r.uri) for r in await c.list_resources()}
            templated = {t.uri_template for t in await c.list_resource_templates()}
            return listed, templated
    listed, templated = asyncio.run(go())
    assert "session://files/recordings" in listed
    assert "session://files/recordings/{name}" in templated


def test_reading_one_recording_answers_its_entry_not_the_video(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return await c.read_resource("session://files/recordings/rec-20261008-1403.mp4")
    got = asyncio.run(go())
    entry = json.loads(got[0].text)
    assert entry["name"] == "rec-20261008-1403.mp4" and entry["url"]
    assert entry["content_type"] == "video/mp4"


def test_keep_file_moves_a_recording(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return await c.call_tool("keep_file", {"uri": "session://files/recordings/rec-20261008-1403.mp4"})
    result = asyncio.run(go())
    assert result.structured_content["uri"] == "session://files/rec-20261008-1403.mp4"
    assert srv.flows.files(STDIO_NAME, RECORDINGS_DIR) == []
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: `http/files.py`.**
  - Constants: `FILES, SCREENSHOTS, DOWNLOADS, RECORDINGS = "files", "screenshots", "downloads", "recordings"`; `RESERVED = RESERVED_IN_FILES`; add `RECORDINGS` to `FOLDER_URI` (and so `ITEM_URI`); `SHAPES` gains `, session://files/recordings/<name> for a recording`; `FILE_ENDPOINTS` and `FILE_ROUTES` gain `"recordings"` / `("get", "/recordings")`; `DESCRIPTION` names three folders.
  - `parse_uri`: unchanged logic — `RESERVED` now includes recordings.
  - `url_for`: `if folder == RECORDINGS: return links.recording_url(session, name, token, mount, ttl)`.
  - `root`: a third folder `{"name": RECORDINGS, "uri": FOLDER_URI[RECORDINGS], "count": len(store.files(owned, RECORDINGS)) if store is not None and owned else 0}` between screenshots and downloads.
  - `folder`: accept `RECORDINGS`.
  - `sections`: add `"recordings": listing_of(actions, store, RECORDINGS, owned, target, token, base, mount, ttl=ttl)`.
  - `keep`: a new branch before the downloads `else`:

```python
    elif folder_of == RECORDINGS:
        # The screenshot rule, without reading a video into memory: a move,
        # landing beside a same-named file as name (1) (recordings spec, ruling 7).
        try:
            source = store.file_path(session, leaf, RECORDINGS)
            if not source.is_file():
                raise FileNotFoundError(leaf)
            landed = store.move_in(session, source, leaf, FILES)
        except FileNotFoundError:
            raise ValueError(
                f"no recording called {leaf!r}: it may be kept already or cleared. "
                f"{FOLDER_URI[RECORDINGS]} lists what there is"
            ) from None
        log.info("kept %s as %s/%s", uri, session, landed["name"])
```

  - `clear_recordings(store, session)`: like `clear_screenshots`, folder `RECORDINGS`.
  - `register`: the folder resource loop covers `(SCREENSHOTS, RECORDINGS, DOWNLOADS)`; `FOLDER_NAME[RECORDINGS] = "Recordings"`; `FOLDER_DESCRIPTION[RECORDINGS]`:

```python
    RECORDINGS: (
        "This session's recordings: one video per browser opened with "
        "open_session(record=true), filed shortly after that browser ends. "
        f"{KEEP_TOOL}(uri) moves one into {ROOT_URI}. Each entry's url plays it; "
        "reading one by its uri answers that entry, not the video."
    ),
```

    The item resource for recordings returns the entry JSON rather than bytes — register it separately after the loop:

```python
    @mcp.resource(
        ITEM_URI[RECORDINGS],
        name="Recording",
        description=(
            f"One recording from {FOLDER_URI[RECORDINGS]}, described: its url plays "
            "it. Not the video's bytes — a model cannot watch one, and a resource "
            "would carry it as base64."
        ),
        mime_type="application/json",
    )
    def recording_resource(name: str) -> dict:
        if store is None:
            raise ValueError(OFF)
        listing = folder(actions, sessions, store, token, clients.caller().name,
                         RECORDINGS, base=base, mount=prefix, ttl=ttl)
        entry = next((f for f in listing["files"] if f["name"] == unquote(name)), None)
        if entry is None:
            raise ValueError(f"no recording called {name!r}. {FOLDER_URI[RECORDINGS]} lists them")
        return entry
```

    and keep the bytes loop over `(FILES, SCREENSHOTS, DOWNLOADS)` only.
  - `keep_file`'s description: "A screenshot or a recording moves out of its folder …".
  - `_routes`: `GET {files_root}/recordings` (`name="files_recordings"`) before the keep route; `keep_route` accepts `RECORDINGS`.
  - `read_file` (for `upload_file`) works unchanged for recordings.

- [ ] **Step 4: `http/links.py`.** Beside the screenshot pair:

```python
def recording_path(session: str, name: str) -> str:
    """The unsigned path of one recording, keyed by session like a screenshot."""
    return f"/recordings/{quote(session, safe='')}/{quote(name, safe='')}"


def recording_url(
    session: str, name: str, token: str | None, mount: str = "", ttl: int = DEFAULT_TTL
) -> str:
    """A URL for one recording."""
    return _url(recording_path(session, name), token, mount, ttl)
```

- [ ] **Step 5: `http/admin/signed.py` — stream disk files with Range.** Add:

```python
# A video or audio file opened in a tab is a media document that loads itself:
# it needs `media-src 'self'` and nothing else, and like an image it is not
# sandboxed, for the reason RASTER_TYPES gives.
MEDIA_ONLY = "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'"


def headers_for(name: str, max_age: int) -> dict:
    """The headers every stored file is served with; see `served`."""
    kind = files.content_type(name)
    headers = {
        "Content-Disposition": disposition(name),
        "Cache-Control": f"private, max-age={max_age}",
        "X-Content-Type-Options": "nosniff",
    }
    if kind in files.RASTER_TYPES:
        headers["Content-Security-Policy"] = NO_SCRIPT
    elif kind.startswith(("video/", "audio/")):
        headers["Content-Security-Policy"] = MEDIA_ONLY
    elif kind != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox"
    return headers
```

Rewrite `served` to `return Response(data, media_type=files.content_type(name), headers=headers_for(name, max_age))`, moving its comments into `headers_for`. Give `route()` an optional `path: Callable[[str, str], Path] | None = None`; when set, the handler resolves the path in a worker thread (`await run_in_threadpool(path, who, leaf)`, catching `absent` → 404; a missing file → 404) and returns:

```python
            return FileResponse(
                resolved,
                media_type=files.content_type(leaf),
                headers=headers_for(leaf, fresh_for(request)),
            )
```

(`FileResponse` sets `Accept-Ranges` and answers `Range` itself; our `Content-Disposition` header wins because we pass it explicitly — check the response in the test.) Mount kept and screenshot routes with `path=lambda s, leaf: flow_store.file_path(s, leaf)` / `(..., SCREENSHOTS_DIR)` instead of `read=`, and a fourth route:

```python
    route(
        mcp,
        token,
        url=f"{prefix}/recordings/{{session}}/{{name}}",
        name="recording_file",
        owner="session",
        path_of=links.recording_path,
        path=lambda session, leaf: flow_store.file_path(session, leaf, RECORDINGS_DIR),
        closed=lambda _leaf: flow_store is None,
        absent=gone,
        what="reading recording {name} for {owner}",
        doc="""One recording, authorised by the signature in its own URL, streamed
        from disk so a player can seek (Range).""",
    )
```

Keep `read=` for the Grid's `file` route. Update `tests/test_screenshot_saving.py`'s CSP table: `video/mp4` → `MEDIA_ONLY`.

- [ ] **Step 6: Admin.** `http/admin/files.py`: the empty listing gains `"recordings": []`; the keep route accepts `stored.RECORDINGS`; `admin_delete_file` gains:

```python
            if name == stored.RECORDINGS:
                got = await run_in_threadpool(
                    stored.clear_recordings, flow_store, library(key)
                )
                return JSONResponse({"success": True, "key": key, **got})
```

`http/admin/sessions.py` (~395–470): count recordings like screenshots (`recs = named(lambda s: flow_store.files(s, RECORDINGS_DIR), session) if stores else []`), add `"recordings": len(recs) if stores else None` to `counts`, add `("recordings", recs)` to `files_rev`, and add to the row `"recording": bool(live and (record.settings or {}).get("record"))`. Update `tests/golden/admin-sessions.json` with `GOLDEN_UPDATE=1` and read the diff.

- [ ] **Step 7: `show` and the spec.** `mcp/show.py` `VIEWS`, after the screenshots line: `("session://files/recordings", re.compile(r"session://files/recordings"), "folder"),`. `spec/schemas.py`: both folder `enum`s gain `"recordings"`; `FileList`/`FolderList` descriptions name it; `_FILE_OPERATIONS` gains:

```python
    "recordings": (
        "listRecordings",
        "This session's recordings, not yet kept.",
        "Newest first. One video per browser opened with record=true, filed "
        "shortly after it ended. Works after the browser is gone.",
        {"type": "object", "properties": {}},
        "FolderList",
    ),
```

and `keep`'s summary: "Moves a screenshot or a recording into Files, or copies a download there."

- [ ] **Step 8: Existing tests that pin the old shape.** Update by intent, not by deletion: `tests/test_file_surfaces.py:38` (resources are one folder plus **three**), `tests/test_kept_files.py` (`FILE_ENDPOINTS` on both surfaces), `tests/test_show.py` (the parametrised table gains the recordings row), `tests/test_admin_files.py` (sections carry `recordings`; clear recordings), `tests/test_file_sections.py` (root folders). Regenerate goldens (tools-on/off, openapi, admin-sessions), the wiki (a new `list_recordings` page is generated if the generator maps `FILE_ROUTES`; add `wiki/notes/list_recordings.notes.md` only if a sibling `list_screenshots.notes.md` exists).

- [ ] **Step 9: Run everything, lint, commit** — `Recordings on every surface; stored files stream with Range`.

---

### Task 7: The admin UI and the MCP app (the Penpot drawing)

Build what the Penpot flow **Recordings** shows: the row between Screenshots and Files, video tiles (🎬 with a ▶ badge), the video lightbox that steps and whose Keep moves on, the clear confirm, and ● REC on the session card.

**Files:**
- Modify: `ui/src/lib/types.ts`, `ui/src/admin/session.svelte.ts`, `ui/src/admin/FilesPane.svelte`, `ui/src/admin/SessionDetail.svelte`, `ui/src/lib/FileTile.svelte`, `ui/src/lib/Lightbox.svelte`, `ui/src/lib/SessionSummary.svelte`, `ui/src/lib/format.ts`, `ui/src/lib/views/FilesView.svelte`, `ui/src/app.css` (only if a class is new)
- Test: `ui/src/admin/SessionDetail.test.ts`, `ui/src/lib/FileGrid.test.ts`, `ui/src/lib/Lightbox.test.ts`, `ui/src/lib/format.test.ts`, `ui/src/lib/SessionSummary.test.ts`, `ui/src/lib/views/FilesView.test.ts` (if present)

**Interfaces:**
- Consumes: the admin payloads from Task 6 — `GET …/files` → `recordings: FileEntry[]`; row `counts.recordings`, `recording`; `DELETE …/files/recordings`; `POST …/files/recordings/{name}/keep`.

- [ ] **Step 1: Write the failing tests** (vitest + @testing-library/svelte, following each file's existing helpers):
  - `format.test.ts`: `countsText({key:'k', counts:{downloads:0, screenshots:6, recordings:2, files:2}})` → `'6 screenshots · 2 recordings · 2 files'`; `recordings: 1` → `'1 recording'`.
  - `SessionDetail.test.ts`: with a files response holding two recordings, the `#recordingsSection` shows count `2` and two tiles; `#clearRecordings` is disabled when the list is empty; clicking it asks a confirm titled `Clear recordings` whose button reads `Delete 2 recordings` and, confirmed, sends `DELETE …/files/recordings`; the 📌 on a recording tile sends `POST …/files/recordings/<name>/keep`.
  - `Lightbox.test.ts`: an entry with `content_type: 'video/mp4'` renders `<video controls>` with `src` = base + url, not "No preview".
  - `FileGrid.test.ts` (or `FileTile`): a `video/mp4` entry shows the 🎬 glyph and a `.play` badge, and no `<img>`.
  - `SessionSummary.test.ts`: a row with `live: true, recording: true` shows a `pill rec` reading `● REC`; `recording: false` shows none.

- [ ] **Step 2: Run to verify they fail.** `npm --prefix ui test`.

- [ ] **Step 3: Types and model.** `types.ts`: `Folder = 'downloads' | 'screenshots' | 'recordings' | 'files'`; `FilesData`, `FilesResponse` and `Counts` gain `recordings`; `SessionRow` gains `recording?: boolean`. `session.svelte.ts`: `NO_FILES` gains `recordings: []`; `FilesView` and `loadFiles` carry `recordings` and `counts.recordings` the way they carry screenshots.

- [ ] **Step 4: `FilesPane.svelte`.** The snippet gains `'recordings'`:

```svelte
  {:else if folder === 'recordings'}
    <FileGrid files={m.view.recordings} base={root} action="keep" empty="No recordings yet."
              onopen={(i) => onopen('recordings', i)} onkeep={(f) => onkeep('recordings', f)} />
```

and a section after Screenshots:

```svelte
  <Section id="recordingsSection" title="Recordings" count={count(m.view?.counts.recordings)}>
    {#snippet actions()}
      <button id="clearRecordings" class="danger" disabled={clearRecordingsOff}
              title="Delete every recording. Anything you kept is in Files and stays."
              onclick={() => onclear('recordings')}>Clear recordings</button>
    {/snippet}
    <div id="recordings">{@render rows('recordings')}</div>
  </Section>
```

with `const clearRecordingsOff = $derived(!m.files.recordings.length || m.loadingFiles)` and `onclear: (what: 'downloads' | 'screenshots' | 'recordings') => void`.

- [ ] **Step 5: `SessionDetail.svelte`.** `clear(what)` gains a recordings branch shaped exactly like the screenshots one (title `Clear recordings`, body `clearRecordingsBody` — a snippet like `clearScreenshotsBody` saying how many, that Files is untouched, and the names — confirm `'Delete ' + n + ' recording' + (n === 1 ? '' : 's')`, `DELETE …/files/recordings`). `openLightbox` needs no change: a recording's action is `📌 Keep` because its folder is not `files`, and its refresh keeps the index, so Keep moves on to the next.

- [ ] **Step 6: Tiles and lightbox.** `FileTile.svelte`: inside `.thumb`, after the glyph branch:

```svelte
  {#if f.content_type?.startsWith('video/')}<span class="play" aria-hidden="true">▶</span>{/if}
```

and `format.ts`'s `glyphFor` already maps `mp4` → 🎬. `Lightbox.svelte`, before the PDF branch:

```svelte
        {:else if f.content_type?.startsWith('video/')}
          <!-- svelte-ignore a11y_media_has_caption -->
          <video controls autoplay preload="metadata" src={href}></video>
```

CSS (`app.css` or the components' `<style>`): `.thumb .play` — a 28px circle, `rgba(22,22,29,.72)`, white ▶, bottom-right 8px (the Penpot `file / video` tile); the lightbox `video` — `max-width: 100%; max-height: 100%; background: #000; border-radius: 6px` (the Penpot `lightbox / recording`).

- [ ] **Step 7: Session card and counts.** `SessionSummary.svelte` after the live/idle pill: `{#if s.live && s.recording}<span class="pill rec">● REC</span>{/if}`; `.pill.rec { background: var(--warn); border-color: var(--warn); color: #fff }` (use the variable the `warn` pill already uses). `format.ts` `countsText`: insert `[c.recordings, ' recording']` after screenshots (treat `undefined` as 0 for older rows).

- [ ] **Step 8: MCP app.** `ui/src/lib/views/FilesView.svelte` draws one chip per folder from `data.folders`; make sure the recordings folder renders like screenshots (a third chip) and that `show("session://files/recordings")` renders through the same folder view as screenshots (the `folder` component already handles any folder listing — add a test row if `FilesView.test.ts` enumerates folders).

- [ ] **Step 9: Run the UI checks** — `test`, `check`, `lint`, `build`, `size` — and compare the built page against the Penpot boards `recordings`, `lightbox-recording-1`, `overlay-clear-recordings`, `recordings-cleared` (export them with the Penpot MCP if it is connected, or ask Dr K for the link). Commit: `Admin: the Recordings row, video lightbox and REC pill`.

---

### Task 8: Docs, compose, and the whole-branch check

**Files:**
- Modify: `README.md`, `AGENTS.md`, `CHANGELOG.md`, `docker-compose.yaml`, `skills/selenium-flow/references/SESSIONS.md`, `skills/selenium-flow/references/CONFIGURATION.md`, `docs/superpowers/specs/2026-10-08-recordings-design.md` (status line, §10 item 1)

- [ ] **Step 1: README — a `## Recording` section** (after the files section; keep the README's voice):

```markdown
## Recording

`open_session(record=true)` films the browser's whole life — from that call until
the browser ends or the Grid reaps it — and the video appears under
`session://files/recordings` (and in the admin page's Recordings row) shortly
after. Like screenshots, recordings stay until they are kept into Files or
cleared.

**Selenium records; you deliver; selenium-flow files.** Every docker-selenium
node image from `4.45.0-20260606` contains the recorder and starts it for a
session that asks with `se:recordVideo`. The Grid has no endpoint to download a
recording, so how the file reaches this server is yours to choose. The one rule:

> Make the Grid's recordings arrive in `RECORDING_DIR` (default
> `$DATA_DIR/recordings`), and set `RECORDING_ENABLED=true`.

Both sides need write access to that directory: the Grid's side writes, this
server moves the finished file out. Keep `SE_NODE_MAX_SESSIONS=1` on recording
nodes, or recordings share one screen.

- **A shared volume.** Mount the same directory at the nodes' `/videos` and at
  `RECORDING_DIR` here. Nothing else to configure.
- **rclone, to anything.** The recorder uploads each finished file with rclone:
  set `SE_UPLOAD_DESTINATION_PREFIX` and an `RCLONE_CONFIG_<REMOTE>_*` remote on
  the nodes — a WebDAV such as Nextcloud whose folder is the same storage as
  `RECORDING_DIR`, S3, SFTP. Leaving `--inplace` out of `SE_UPLOAD_OPTS` makes
  rclone write `*.partial` and rename, which this server waits for.
- **A local folder** for stdio or compose: bind-mount it into the Grid container
  at `/videos` (see `docker-compose.yaml`).

A recording is matched to its session by the Grid's session id in its file name,
so any path and prefix the transport adds is fine. One that never arrives is
given up on after `RECORDING_WAIT` seconds with a warning in the log.
```

Also update the README's configuration table/prose for `DATA_DIR` and the `recording.*` settings (the wiki's Configuration page is generated; the README links it).

- [ ] **Step 2: `docker-compose.yaml`.** On the Grid service: `volumes: ["./data/recordings:/videos"]` and `environment: SE_NODE_SESSION_TIMEOUT=300` kept; on the server: `DATA_DIR=/data`, `RECORDING_ENABLED=true`, `volumes: ["./data:/data"]`. Add `data/` to `.gitignore` if it is not there.

- [ ] **Step 3: AGENTS.md.**
  - In *Refresh, not cleanup*, after "Do not add a scheduler.", add: "**One bounded wait is allowed:** a task that waits for something this server was told to expect, and ends when nothing is owed — the admin broadcast (while a page listens) and the recordings collector (while a recording is owed). A loop that tidies is still not."
  - A new section *Recordings: Selenium records, the operator delivers, we file* (6–10 lines): the inbox is the operator's and is never cleaned by us; notes under `.pending/` are the queue; matching is by Grid id, never by name (the recorder strips `.`); a file is complete when it ends in `mfro`; the Grid id stays on disk; recordings follow the screenshot lifecycle; `record` is never inherited by an explicit open but is replayed on a reap.
  - *Configuration*: `data` and `recording` are sections; `FLOW_DATA_DIR` is the one retired name refused at boot.
  - The data layout (`DATA_DIR/sessions/<name>/{flows,files,screenshots,recordings}`, `DATA_DIR/recordings/`).

- [ ] **Step 4: Skill references.** `SESSIONS.md`: one paragraph — `record=true` on `open_session`, not inherited, the video under `session://files/recordings` after the browser ends, `keep_file` to keep it, and that it costs the Grid so only when a person will watch. `CONFIGURATION.md`: `DATA_DIR`, the `recording.*` settings.

- [ ] **Step 5: CHANGELOG `[Unreleased]`** (the breaking line is already there from Task 2): `- \`open_session(record=true)\` records the browser; recordings appear under \`session://files/recordings\` and in the admin Files tab.`

- [ ] **Step 6: Compose check (spec §10 item 1).** If Docker is available where you are, run `docker compose up --build`, open a recorded session through the server's HTTP surface (`POST /browser?session=compose {"record": true}`), `DELETE /browser?session=compose`, and watch `./data/sessions/compose/recordings/` — record under §10 item 1 whether the standalone image recorded without `SE_VIDEO_RECORD_STANDALONE=true` (if not, add it to the compose file and the README's local-folder example). If Docker is not available, write that under §10 item 1 and ask Dr K to run it.

- [ ] **Step 7: The whole-branch check.** Full Python suite, ruff, `generate_wiki.py --check`, goldens untouched by a fresh `GOLDEN_UPDATE` run, `npm --prefix ui` test/check/lint/build/size. Set the spec's status line to `BUILT on branch recordings`. Commit: `Recordings: README, AGENTS, skill, compose`.

---

### Task 9: Live check after Dr K deploys (Dr K)

Not code: the acceptance test, on the real Grid and inbox. Dr K moves the session folders once (`mkdir /data/flows/sessions && mv` every session folder into it — list them first; `recordings/` stays), sets `DATA_DIR=/data/flows` and `RECORDING_ENABLED=true` in the cluster repo, and deploys the branch's image.

- [ ] **Step 1:** The pod boots; its log says `recordings: on, inbox /data/flows/recordings, polling`.
- [ ] **Step 2:** From Claude Code: `open_session(record=true)`, navigate, `end_browser`. Within ~15 s the admin page's Recordings row shows `rec-…mp4` without a reload; `session://current` said `recording: true` while it ran, and the card showed ● REC.
- [ ] **Step 3:** The lightbox plays it and seeking works (Range). Opening the tile's link in a new tab plays it too — that is spec §10 item 3; if the tab refuses to play, capture the console's CSP error and adjust `MEDIA_ONLY`.
- [ ] **Step 4:** Keep it → it leaves Recordings and lands in Files, still playable. Clear recordings on another → gone; Files untouched.
- [ ] **Step 5:** A reap: open recorded, wait past `SE_NODE_SESSION_TIMEOUT` without calling anything — the recording still files.
- [ ] **Step 6:** Write the results under the spec's §10 and set its status to `LIVE`.
