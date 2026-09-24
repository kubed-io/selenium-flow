# The Hangar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split a session's files into Downloads, Screenshots and Files — in storage, in the MCP surface, in the admin API and on the admin page — put Files and Flows on tabs, give screenshots a stepping lightbox, and add a read-only Secrets tab.

**Architecture:** Screenshots move into a new `<session>/screenshots/` folder beside the existing `<session>/files/` (which *is* the Files section). `session://files` becomes a folder: it lists Files and names two sub-folders, `session://files/screenshots` and `session://files/downloads`, each with a `/{name}` template. `keep_file` takes a file's URI: a screenshot is **moved** into Files, a download **copied**. The admin page gets session tabs (Files | Flows), three rows under Files, a lightbox that steps, and a Secrets tab fed by one new admin endpoint.

**Tech Stack:** Python 3.10+, FastMCP 4, Starlette, pytest; vanilla JS/HTML/CSS in `static/` (no build step); Penpot for the design.

**Spec:** `saga/Chapter_4_The_Hangar.md` — §F4.1–§F4.11 are the rulings. The Penpot file *Admin UI* (pages Sessions, Session · Files, Session · Flows, Secrets, Components) is the drawing; the version *Chapter 4 design — prototype walkthroughs fixed* is what was approved.

## Global Constraints

- **Names, verbatim:** the three sections are **Downloads**, **Screenshots**, **Files**, in that order on the page (§F4.2, §F4.5). Folder keys `downloads`, `screenshots`, `files`.
- **URIs, verbatim:** `session://files`, `session://files/{name}`, `session://files/screenshots`, `session://files/screenshots/{name}`, `session://files/downloads`, `session://files/downloads/{name}` (§F4.6).
- `screenshots` and `downloads` are **reserved names inside Files**: a file arriving there under either name lands as `screenshots (1)` / `downloads (1)` (§F4.6).
- A clash inside a folder lands as `name (1)`, `name (2)` … — never an overwrite — except keeping a **download**, which replaces a same-named file in Files, as today (§F4.7).
- `keep_file(uri)`: screenshot → **move**; download → **copy**; a Files URI → answers with that file. `idempotentHint` is **false** (§F4.7).
- `upload_file(kept=name)` becomes `upload_file(file=uri)`, accepting any of the three kinds (§F4.7).
- **No agent tool clears or deletes anything.** Clearing and deleting are admin-only HTTP (§F4.7, AGENTS.md).
- **No delete-all for Files, no per-screenshot delete** (§F4.1, §F4.9).
- Every list is **newest first**; the lightbox steps in grid order, **‹ Prev** / **Next ›**, ← → keys, Esc, stops at the ends (§F4.3, §F4.11).
- **Keep inside the lightbox moves on to the next screenshot** (§F4.9).
- Clear downloads is disabled unless a browser is **live** — not merely attached (§F4.9).
- Session tabs: **Files** is the default; the tab is in the hash: `#/sessions/<key>`, `#/sessions/<key>/flows`, `#/sessions/<key>/flows/<flow>` (§F4.8, §F4.10). Secrets: `#/secrets`.
- Secrets page is read-only and never shows a value; backlinks come only from stored flows (§F4.10).
- **No backwards compatibility** (§F3.7): old shapes and paths are removed, not deprecated.
- `components.js` is shared with the MCP app, which holds no credential: it may *render* a button, never *wire* one (§F1.36, §F1.40). Secrets rendering is admin-only and lives in `admin.html`.
- Integration tests: **a flow file in `tests/integration/flows/` and nothing else**; the rule is "name what a person would see break" (AGENTS.md).
- CHANGELOG: one short line per user-visible change under `[Unreleased]`; **BREAKING:** lines may stretch (CONTRIBUTING.md).

## How to run things (every task)

```bash
cd /projects/modules/selenium-flow
export SFENV=/tmp/claude-1000/-projects-cluster/2f3f76f1-c974-4758-ae06-6f18b8dd88b9/scratchpad/sfenv
# a user-site `kubed` package shadows this namespace package, hence PYTHONNOUSERSITE
alias t='PYTHONNOUSERSITE=1 PYTHONPATH=$PWD:$SFENV python3 -m pytest -q -p no:cacheprovider --ignore=tests/integration'
alias lint='PYTHONNOUSERSITE=1 PYTHONPATH=$SFENV python3 -m ruff check kubed tests scripts'
```

Baseline before Task 1: `1246 passed, 20 skipped`. **Never run psalm, php-cs-fixer or Behat** (not this repo, but the rule stands). Never run the integration suite in the pod; CI runs it.

## File map

| File | Responsibility after this plan |
|---|---|
| `kubed/selenium_flow/flows/library.py` | the store: flows, and two file folders (`files`, `screenshots`) |
| `kubed/selenium_flow/http/files.py` | the file domain: URIs, listings, keep, read, clear; MCP resources + tools; the `/files` REST tree |
| `kubed/selenium_flow/http/links.py` | signed paths, now three: downloads, screenshots, files |
| `kubed/selenium_flow/core/actions.py` | `screenshot` keeps into `screenshots`; `upload_file(file=uri)` |
| `kubed/selenium_flow/server.py` | wiring `keep(name, data, folder)` and `read_file(uri, session)` |
| `kubed/selenium_flow/mcp/tools.py`, `mcp/completions.py` | `upload_file(file=)`; completions for the three templates |
| `kubed/selenium_flow/spec/schemas.py`, `spec/builder.py` | published shapes and the `/files` operations |
| `kubed/selenium_flow/http/admin.py` | admin files API, screenshot signed route, counts, `/admin/secrets` |
| `static/components.js` | tiles with one action, `fileSections`, a stepping `lightbox` |
| `static/admin.html`, `static/app.css` | session tabs, three rows, confirms, lightbox wiring, Secrets tab |
| `skills/…`, `AGENTS.md`, `CHANGELOG.md`, `wiki/`, `tests/integration/flows/` | docs and the one new flow |

---

### Task 1: Two file folders in the store

**Files:**
- Modify: `kubed/selenium_flow/flows/library.py` (constants near line 75; `FlowStore` protocol ~251; `LocalFlowStore` file methods ~359-368 and ~541-615)
- Test: `tests/test_file_folders.py` (create)

**Interfaces:**
- Produces:
  - `FILES_DIR = "files"`, `SCREENSHOTS_DIR = "screenshots"`, `FOLDERS = (FILES_DIR, SCREENSHOTS_DIR)`
  - `LocalFlowStore.files(session, folder=FILES_DIR) -> list[dict]` (entries `{name, size, creationTime}`, newest first)
  - `read_file(session, name, folder=FILES_DIR) -> bytes` (raises `FileNotFoundError`)
  - `write_file(session, name, data, folder=FILES_DIR) -> dict` (create-or-replace)
  - `create_file(session, name, data, folder=FILES_DIR) -> dict` (exclusive; `FileExistsError`)
  - `delete_file(session, name, folder=FILES_DIR) -> bool`
  - `clear_folder(session, folder) -> int` (files removed; refuses `FILES_DIR` with `InvalidName`)
  - an unknown folder raises `InvalidName`

- [ ] **Step 1: Write the failing tests** — `tests/test_file_folders.py`:

```python
"""A session keeps files in two folders: Files, and its screenshots (§F4.7).

Downloads are not here — they are the Grid's. What is asserted is that the two
folders are separate, that the folder is part of every address, and that the
one bulk operation the store offers refuses the folder a person curated.
"""

import pytest

from kubed.selenium_flow.flows import library as flows

pytestmark = pytest.mark.unit

S = "desktop"


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path)


def test_the_folders_are_named_once():
    assert flows.FOLDERS == ("files", "screenshots")


def test_a_screenshot_lands_in_its_own_folder(store, tmp_path):
    store.create_file(S, "shot.png", b"png", flows.SCREENSHOTS_DIR)
    assert (tmp_path / S / "screenshots" / "shot.png").read_bytes() == b"png"
    assert not (tmp_path / S / "files").exists()


def test_the_default_folder_is_files(store, tmp_path):
    store.write_file(S, "report.pdf", b"pdf")
    assert (tmp_path / S / "files" / "report.pdf").exists()


def test_each_folder_lists_only_itself(store):
    store.create_file(S, "a.png", b"1", flows.SCREENSHOTS_DIR)
    store.write_file(S, "b.pdf", b"2")
    assert [f["name"] for f in store.files(S, flows.SCREENSHOTS_DIR)] == ["a.png"]
    assert [f["name"] for f in store.files(S)] == ["b.pdf"]


def test_one_name_can_live_in_both_folders(store):
    store.create_file(S, "shot.png", b"screenshot", flows.SCREENSHOTS_DIR)
    store.write_file(S, "shot.png", b"kept")
    assert store.read_file(S, "shot.png", flows.SCREENSHOTS_DIR) == b"screenshot"
    assert store.read_file(S, "shot.png") == b"kept"


def test_a_delete_names_its_folder(store):
    store.create_file(S, "shot.png", b"1", flows.SCREENSHOTS_DIR)
    store.write_file(S, "shot.png", b"2")
    assert store.delete_file(S, "shot.png", flows.SCREENSHOTS_DIR) is True
    assert store.read_file(S, "shot.png") == b"2"


def test_clearing_screenshots_counts_what_went_and_leaves_files(store):
    for n in ("a.png", "b.png"):
        store.create_file(S, n, b"x", flows.SCREENSHOTS_DIR)
    store.write_file(S, "keep.pdf", b"y")
    assert store.clear_folder(S, flows.SCREENSHOTS_DIR) == 2
    assert store.files(S, flows.SCREENSHOTS_DIR) == []
    assert [f["name"] for f in store.files(S)] == ["keep.pdf"]


def test_clearing_a_folder_that_does_not_exist_is_zero(store):
    assert store.clear_folder(S, flows.SCREENSHOTS_DIR) == 0


def test_files_cannot_be_cleared_wholesale(store):
    """Everything in Files was put there on purpose (§F4.1)."""
    store.write_file(S, "keep.pdf", b"y")
    with pytest.raises(flows.InvalidName):
        store.clear_folder(S, flows.FILES_DIR)
    assert store.read_file(S, "keep.pdf") == b"y"


@pytest.mark.parametrize("folder", ["downloads", "flows", "../files", ""])
def test_an_unknown_folder_is_refused(store, folder):
    with pytest.raises(flows.InvalidName):
        store.files(S, folder)
```

- [ ] **Step 2: Run them to see them fail** — `t tests/test_file_folders.py` → FAIL (`FOLDERS` missing, unexpected `folder` argument).

- [ ] **Step 3: Implement** in `library.py`:

```python
# Where a session's own files land. `files` IS the Files section — a print, and
# anything kept; `screenshots` holds every screenshot until it is kept or
# cleared (§F4.1). Downloads are not a folder here: they are the Grid's.
FILES_DIR = "files"
SCREENSHOTS_DIR = "screenshots"
FOLDERS = (FILES_DIR, SCREENSHOTS_DIR)


def valid_folder(folder) -> str:
    """``folder`` if it is one of this store's file folders, else raise."""
    if folder not in FOLDERS:
        raise InvalidName(
            f"{folder!r} is not a file folder: use one of {', '.join(FOLDERS)}"
        )
    return folder
```

Replace `_files_dir`/`_file_path` and the kept-file methods (`files`, `read_file`, `write_file`, `create_file`, `delete_file`) so each takes `folder: str = FILES_DIR` as its **last** parameter, validated with `valid_folder`, and joined in place of the hard-coded `FILES_DIR`. Add:

```python
    def clear_folder(self, session: str, folder: str) -> int:
        """Delete every file in one folder, returning how many went.

        Files is refused: everything in it was put there on purpose, so it is
        emptied one file at a time or not at all (§F4.1). Anything this store
        could not address is left where it is, as `files` skips it.
        """
        if valid_folder(folder) == FILES_DIR:
            raise InvalidName("Files is never cleared wholesale; delete one file")
        removed = 0
        for entry in self.files(session, folder):
            if self.delete_file(session, entry["name"], folder):
                removed += 1
        return removed
```

Update the `FlowStore` protocol signatures to match, and its docstring's last paragraph to say a session's own files are two folders. Rename the section comment `# -- kept files --` to `# -- a session's own files --`.

- [ ] **Step 4: Run** — `t tests/test_file_folders.py tests/test_kept_files.py` → all PASS (existing callers use the default folder).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "The store keeps a session's files in two folders, Files and screenshots"`

---

### Task 2: The file domain — URIs, listings, keep, read, clear

**Files:**
- Modify: `kubed/selenium_flow/http/files.py` (module docstring and everything above `register`)
- Modify: `kubed/selenium_flow/http/links.py` (add screenshot paths)
- Test: `tests/test_file_sections.py` (create); rewrite `tests/test_kept_files.py` store-level tests that assert `merged`/`kept` (lines ~240-380, ~993-1150)

**Interfaces:**
- Consumes: Task 1's store API.
- Produces (all in `files.py`):
  - constants `FILES = "files"`, `SCREENSHOTS = "screenshots"`, `DOWNLOADS = "downloads"`, `RESERVED = frozenset({SCREENSHOTS, DOWNLOADS})`, `ROOT_URI = "session://files"`, `FILE_URI = "session://files/{name}"`, `FOLDER_URI = {SCREENSHOTS: "session://files/screenshots", DOWNLOADS: "session://files/downloads"}`, `ITEM_URI = {SCREENSHOTS: "session://files/screenshots/{name}", DOWNLOADS: "session://files/downloads/{name}"}`
  - `uri_of(folder, name) -> str`; `parse_uri(uri) -> tuple[str, str]` (raises `ValueError`)
  - `describe(folder, entry, url, base="") -> dict` → `{name, uri, size, created, content_type, image, url, absolute_url?, keep_with?}`
  - `listing_of(actions, store, folder, session, session_id, token, base="", mount="", downloads=None) -> list[dict]`
  - `root(actions, sessions, store, token, name, base="", mount="") -> dict` → `{session, count, files, folders: [{name, uri, count, browser?}]}`
  - `folder(actions, sessions, store, token, name, which, base="", mount="") -> dict` → `{session, folder, uri, count, files, browser?}`
  - `sections(actions, sessions, store, token, name, base="", mount="", session_id=None, downloads=None) -> dict` → `{component: "fileSections", session, browser, downloads, screenshots, files}`
  - `keep(actions, sessions, store, uri, name=None, session_id=None) -> dict` → `{kept: True, from, uri, name, size, created}` — `session_id`, when given, is the browser to read a download from and is never resolved (the admin passes it so it can never open a browser); when None, the caller's browser is resolved as today
  - `keep_made(sessions, store, name, data, token, base="", mount="", folder=FILES) -> dict` (a described entry)
  - `read_file(actions, sessions, store, uri, name=None) -> tuple[str, bytes]`
  - `delete_one(store, session, name) -> dict`; `clear_screenshots(store, session) -> dict`
  - `links.screenshot_path(session, name)`, `links.screenshot_url(session, name, token, mount="", ttl=...)` — path `/screenshots/{session}/{name}`
- Removed: `merged`, `listing`, `describe_kept`, `keep_one`, `read_kept`.

- [ ] **Step 1: Write the failing tests** — `tests/test_file_sections.py`:

```python
"""A session's files as three sections, addressed by path (§F4.6, §F4.7)."""

import json

import pytest

from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.http import files

pytestmark = pytest.mark.unit

S = "desktop"
TOKEN = "t"
DOWNLOADS = [
    {"name": "export.csv", "size": 3, "creationTime": 1700000002000},
    {"name": "screenshots", "size": 1, "creationTime": 1700000001000},
]


class Grid:
    def __init__(self, entries=None, data=b"from-grid"):
        self.entries = entries if entries is not None else list(DOWNLOADS)
        self.data = data
        self.read = []

    def files(self, session_id):
        return self.entries

    def read_file(self, session_id, name):
        self.read.append((session_id, name))
        return self.data


class Actions:
    def __init__(self, grid=None):
        self.grid = grid or Grid()


class Sessions:
    def __init__(self, browser="abc"):
        self._browser = browser

    def name(self):
        return S

    def browser(self, name):
        return self._browser

    def resolve(self, name):
        return self._browser


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path)


# ---- addresses ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("folder", "name", "uri"),
    [
        ("files", "report.pdf", "session://files/report.pdf"),
        ("screenshots", "shot.png", "session://files/screenshots/shot.png"),
        ("downloads", "export.csv", "session://files/downloads/export.csv"),
        ("files", "Q3 summary (1).csv", "session://files/Q3%20summary%20%281%29.csv"),
    ],
)
def test_a_uri_round_trips(folder, name, uri):
    assert files.uri_of(folder, name) == uri
    assert files.parse_uri(uri) == (folder, name)


def test_a_literal_space_parses_like_its_escape():
    assert files.parse_uri("session://files/screenshots/shot (1).png") == (
        "screenshots",
        "shot (1).png",
    )


@pytest.mark.parametrize(
    "uri",
    [
        "session://files",
        "session://files/screenshots",
        "session://files/downloads",
        "session://files/a/b",
        "session://files/flows/x",
        "flow://flows/x",
        "",
    ],
)
def test_a_uri_that_names_no_file_is_refused_with_the_shapes(uri):
    with pytest.raises(ValueError, match="session://files/screenshots/"):
        files.parse_uri(uri)


# ---- listings -----------------------------------------------------------------


def test_the_root_lists_files_and_names_two_folders(store):
    store.write_file(S, "report.pdf", b"p")
    store.create_file(S, "shot.png", b"s", flows.SCREENSHOTS_DIR)
    got = files.root(Actions(), Sessions(), store, TOKEN, S)
    assert [f["name"] for f in got["files"]] == ["report.pdf"]
    assert got["count"] == 1
    assert got["folders"] == [
        {"name": "screenshots", "uri": "session://files/screenshots", "count": 1},
        {"name": "downloads", "uri": "session://files/downloads", "count": 2, "browser": True},
    ]


def test_with_no_browser_the_downloads_folder_says_so(store):
    got = files.root(Actions(), Sessions(browser=""), store, TOKEN, S)
    assert got["folders"][1] == {
        "name": "downloads", "uri": "session://files/downloads", "count": 0, "browser": False,
    }


def test_every_entry_carries_its_own_uri_and_no_kept_flag(store):
    store.create_file(S, "shot.png", b"s", flows.SCREENSHOTS_DIR)
    shot = files.folder(Actions(), Sessions(), store, TOKEN, S, "screenshots")["files"][0]
    assert shot["uri"] == "session://files/screenshots/shot.png"
    assert "kept" not in shot
    assert shot["keep_with"] == 'keep_file("session://files/screenshots/shot.png")'


def test_a_file_in_files_has_nothing_to_keep(store):
    store.write_file(S, "report.pdf", b"p")
    entry = files.root(Actions(), Sessions(), store, TOKEN, S)["files"][0]
    assert "keep_with" not in entry


def test_the_same_name_in_two_folders_is_two_entries(store):
    store.write_file(S, "shot.png", b"kept")
    store.create_file(S, "shot.png", b"new", flows.SCREENSHOTS_DIR)
    got = files.sections(Actions(), Sessions(), store, TOKEN, S)
    assert [f["uri"] for f in got["files"]] == ["session://files/shot.png"]
    assert [f["uri"] for f in got["screenshots"]] == ["session://files/screenshots/shot.png"]


def test_sections_are_newest_first_and_name_the_app_component(store, tmp_path):
    import os

    for i, n in enumerate(["old.png", "new.png"]):
        store.create_file(S, n, b"x", flows.SCREENSHOTS_DIR)
        os.utime(tmp_path / S / "screenshots" / n, (1_700_000_000 + i, 1_700_000_000 + i))
    got = files.sections(Actions(), Sessions(), store, TOKEN, S)
    assert got["component"] == "fileSections"
    assert [f["name"] for f in got["screenshots"]] == ["new.png", "old.png"]
    assert [f["name"] for f in got["downloads"]] == ["export.csv", "screenshots"]


def test_each_folder_signs_its_own_route(store):
    store.write_file(S, "a.pdf", b"1")
    store.create_file(S, "b.png", b"2", flows.SCREENSHOTS_DIR)
    got = files.sections(Actions(), Sessions(), store, TOKEN, S)
    assert got["files"][0]["url"].startswith("/kept/desktop/a.pdf?")
    assert got["screenshots"][0]["url"].startswith("/screenshots/desktop/b.png?")
    assert got["downloads"][0]["url"].startswith("/files/abc/export.csv?")


# ---- keeping --------------------------------------------------------------------


def test_keeping_a_screenshot_moves_it_into_files(store):
    store.create_file(S, "shot.png", b"png", flows.SCREENSHOTS_DIR)
    got = files.keep(Actions(), Sessions(), store, "session://files/screenshots/shot.png")
    assert got["uri"] == "session://files/shot.png"
    assert got["from"] == "session://files/screenshots/shot.png"
    assert store.read_file(S, "shot.png") == b"png"
    assert store.files(S, flows.SCREENSHOTS_DIR) == []


def test_a_moved_screenshot_never_overwrites_a_kept_file(store):
    store.write_file(S, "shot.png", b"kept earlier")
    store.create_file(S, "shot.png", b"new", flows.SCREENSHOTS_DIR)
    got = files.keep(Actions(), Sessions(), store, "session://files/screenshots/shot.png")
    assert got["name"] == "shot (1).png"
    assert store.read_file(S, "shot.png") == b"kept earlier"
    assert store.read_file(S, "shot (1).png") == b"new"


def test_keeping_a_download_copies_it_and_replaces_a_same_named_file(store):
    store.write_file(S, "export.csv", b"old copy")
    grid = Grid(data=b"fresh")
    got = files.keep(Actions(grid), Sessions(), store, "session://files/downloads/export.csv")
    assert got["uri"] == "session://files/export.csv"
    assert store.read_file(S, "export.csv") == b"fresh"
    assert grid.read == [("abc", "export.csv")]


@pytest.mark.parametrize("name", ["screenshots", "downloads"])
def test_a_reserved_name_lands_beside_itself(store, name):
    got = files.keep(Actions(), Sessions(), store, f"session://files/downloads/{name}")
    assert got["name"] == f"{name} (1)"


def test_keeping_a_file_already_in_files_answers_with_it(store):
    store.write_file(S, "report.pdf", b"p")
    got = files.keep(Actions(), Sessions(), store, "session://files/report.pdf")
    assert got["uri"] == "session://files/report.pdf"


def test_keeping_a_screenshot_that_is_gone_says_where_to_look(store):
    with pytest.raises(ValueError, match="session://files/screenshots"):
        files.keep(Actions(), Sessions(), store, "session://files/screenshots/nope.png")


def test_keeping_a_download_with_no_browser_is_refused(store):
    with pytest.raises(ValueError, match="browser"):
        files.keep(Actions(), Sessions(browser=""), store, "session://files/downloads/export.csv")


def test_keeping_refuses_when_there_is_nowhere_to_keep():
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        files.keep(Actions(), Sessions(), None, "session://files/screenshots/a.png")


# ---- bytes this server made -------------------------------------------------


def test_a_screenshot_is_kept_in_screenshots_with_a_link(store):
    got = files.keep_made(Sessions(), store, "screenshot.png", b"p", TOKEN, folder="screenshots")
    assert got["uri"] == "session://files/screenshots/screenshot.png"
    again = files.keep_made(Sessions(), store, "screenshot.png", b"q", TOKEN, folder="screenshots")
    assert again["name"] == "screenshot (1).png"


def test_a_print_is_kept_in_files(store):
    got = files.keep_made(Sessions(), store, "page.pdf", b"p", TOKEN)
    assert got["uri"] == "session://files/page.pdf"


# ---- reading back, clearing, deleting ------------------------------------------


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("session://files/report.pdf", ("report.pdf", b"kept")),
        ("session://files/screenshots/shot.png", ("shot.png", b"png")),
        ("session://files/downloads/export.csv", ("export.csv", b"from-grid")),
    ],
)
def test_any_file_can_be_read_back_by_its_uri(store, uri, expected):
    store.write_file(S, "report.pdf", b"kept")
    store.create_file(S, "shot.png", b"png", flows.SCREENSHOTS_DIR)
    assert files.read_file(Actions(), Sessions(), store, uri) == expected


def test_reading_a_name_nobody_has_is_the_callers_mistake(store):
    with pytest.raises(ValueError, match=r"session://files"):
        files.read_file(Actions(), Sessions(), store, "session://files/nope.pdf")


def test_clearing_screenshots_reports_how_many(store):
    store.create_file(S, "a.png", b"1", flows.SCREENSHOTS_DIR)
    store.write_file(S, "keep.pdf", b"2")
    assert files.clear_screenshots(store, S) == {"cleared": 1, "session": S}
    assert store.read_file(S, "keep.pdf") == b"2"


def test_json_for_keep_with_survives_a_quote_in_a_name(store):
    store.create_file(S, 'Q4 "final".png', b"1", flows.SCREENSHOTS_DIR)
    entry = files.folder(Actions(), Sessions(), store, TOKEN, S, "screenshots")["files"][0]
    call = entry["keep_with"]
    assert json.loads(call[len("keep_file("):-1]) == entry["uri"]
```

- [ ] **Step 2: Run** — `t tests/test_file_sections.py` → FAIL (missing names).

- [ ] **Step 3: Implement.** In `links.py`, beside `kept_path`/`kept_url`:

```python
def screenshot_path(session: str, name: str) -> str:
    """The unsigned path of one screenshot. Keyed by session name, like a kept
    file, because a screenshot outlives the browser that took it."""
    return f"/screenshots/{quote(session, safe='')}/{quote(name, safe='')}"


def screenshot_url(
    session: str, name: str, token: str | None, mount: str = "", ttl: int = DEFAULT_TTL
) -> str:
    """A URL for one screenshot."""
    return _url(screenshot_path(session, name), token, mount, ttl)
```

(Match `kept_url`'s real parameter names and default TTL constant — read it first; the shape above mirrors it.)

In `files.py`, rewrite the module docstring to describe **three sections, addressed by path** (§F4.6) — one list per folder, a folder that names its sub-folders, keep = move for a screenshot and copy for a download, no delete tool, why `screenshots`/`downloads` are reserved in Files. Then replace the domain functions:

```python
FILES, SCREENSHOTS, DOWNLOADS = "files", "screenshots", "downloads"
RESERVED = frozenset({SCREENSHOTS, DOWNLOADS})
ROOT_URI = "session://files"
FILE_URI = "session://files/{name}"
FOLDER_URI = {SCREENSHOTS: f"{ROOT_URI}/{SCREENSHOTS}", DOWNLOADS: f"{ROOT_URI}/{DOWNLOADS}"}
ITEM_URI = {k: v + "/{name}" for k, v in FOLDER_URI.items()}
FILES_TOOL = "session_files"
KEEP_TOOL = "keep_file"

SHAPES = (
    "session://files/<name> for a file in Files, "
    "session://files/screenshots/<name> for a screenshot, or "
    "session://files/downloads/<name> for a download"
)


def uri_of(folder: str, name: str) -> str:
    """The address of one file. The folder is part of it, so a name only has to
    be unique inside its folder (§F4.6)."""
    leaf = quote(name, safe="")
    return f"{ROOT_URI}/{leaf}" if folder == FILES else f"{FOLDER_URI[folder]}/{leaf}"


def parse_uri(uri) -> tuple[str, str]:
    """``(folder, name)`` for a file's address, or a ValueError naming the shapes."""
    text = str(uri or "")
    prefix = ROOT_URI + "/"
    parts = text[len(prefix):].split("/") if text.startswith(prefix) else []
    if len(parts) == 1 and parts[0] and parts[0] not in RESERVED:
        return FILES, flows.valid_file_name(unquote(parts[0]))
    if len(parts) == 2 and parts[0] in RESERVED and parts[1]:
        return parts[0], flows.valid_file_name(unquote(parts[1]))
    raise ValueError(f"{text!r} is not a file: use {SHAPES}")


def _unreserved(name: str) -> str:
    """A name Files can hold: the folder names are taken there (§F4.6)."""
    return f"{name} (1)" if name in RESERVED else name


def _candidates(name: str):
    """``name``, then ``name (1)``, ``name (2)`` … the way a browser names a
    second download."""
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    yield name
    n = 1
    while True:
        yield f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})"
        n += 1


def _claim(store, session: str, name: str, data: bytes, folder: str) -> dict:
    """Write under the first free name. The exclusive create is the claim, so
    two racing saves cannot both win (Copilot, #40)."""
    for candidate in _candidates(name):
        if folder == FILES and candidate in RESERVED:
            continue
        try:
            return store.create_file(session, candidate, data, folder)
        except FileExistsError:
            continue
    raise AssertionError("unreachable")  # _candidates is infinite
```

`describe(folder, entry, url, base)` builds the shape in the interface list: `keep_with` only for `SCREENSHOTS` and `DOWNLOADS`, as `f"keep_file({json.dumps(uri)})"`; `absolute_url` only when `base`. `listing_of` picks the store folder, or the Grid (only when `session_id` is set; `downloads` passes the Grid's listing in when the caller already has it), signs with `links.file_url` / `links.screenshot_url` / `links.kept_url`, and sorts newest first on `created`. A Grid failure is **not** swallowed (keep the reasoning from today's `merged` docstring). `root`, `folder` and `sections` resolve the browser with `sessions.browser(name)` — **never** `resolve` (keep today's comment on why) — and use `owner(store, name)` for the session library. With `store is None`, Files and Screenshots are empty lists and only downloads list; with neither a browser nor a store, raise `ValueError("nothing to list: this server keeps no files and holds no browser for you")`.

`keep`:

```python
def keep(actions, sessions, store, uri, name=None, session_id=None) -> dict:
    """Put one file in Files. A screenshot moves; a download is copied, because
    the Grid cannot delete one file (§F1.10); a Files URI answers with itself."""
    if store is None:
        raise ValueError(OFF)
    folder, leaf = parse_uri(uri)
    session = owner(store, name or sessions.name())
    if folder == FILES:
        entry = next((e for e in store.files(session) if e["name"] == leaf), None)
        if entry is None:
            raise ValueError(f"no file called {leaf!r} in Files. {ROOT_URI} lists them")
        landed = entry
    elif folder == SCREENSHOTS:
        try:
            data = store.read_file(session, leaf, SCREENSHOTS)
        except FileNotFoundError:
            raise ValueError(
                f"no screenshot called {leaf!r}: it may be kept already or cleared. "
                f"{FOLDER_URI[SCREENSHOTS]} lists what there is"
            ) from None
        landed = _claim(store, session, leaf, data, FILES)
        store.delete_file(session, leaf, SCREENSHOTS)
    else:
        if session_id is None:
            session_id = sessions.resolve(name or sessions.name())
        if not session_id:
            raise ValueError("a download is read from a browser, and none is open")
        data = actions.grid.read_file(session_id, leaf)
        landed = store.write_file(session, _unreserved(leaf), data, FILES)
    log.info("kept %s as %s/%s", uri, session, landed["name"])
    return {
        "kept": True, "from": uri_of(folder, leaf), "uri": uri_of(FILES, landed["name"]),
        "name": landed["name"], "size": landed.get("size", 0), "created": landed.get("creationTime"),
    }
```

`keep_made(..., folder=FILES)` → `describe(folder, _claim(store, session, wanted, data, folder), url_for(folder, …), base)` with `wanted = flows.valid_file_name(name)`. `read_file` parses the URI: `FILES`/`SCREENSHOTS` from the store (a `FileNotFoundError` becomes `ValueError(f"no file at {uri}. {ROOT_URI} lists what there is")`), `DOWNLOADS` from `actions.grid.read_file(sessions.browser(n), leaf)` — refusing with "a download is read from a browser, and none is open" when there is no browser — and returns `(leaf, bytes)`. `clear_screenshots` → `{"cleared": store.clear_folder(session, SCREENSHOTS), "session": session}`. `delete_one` is unchanged but for its docstring (Files only).

- [ ] **Step 4: Rewrite the store-level tests in `tests/test_kept_files.py`** that call the removed functions: `test_both_halves_appear_in_one_list`, `test_a_kept_file_wins_a_name_collision`, `test_the_list_is_newest_first`, `test_the_list_survives_the_browser`, `test_a_grid_failure_is_not_hidden`, `test_the_listing_ignores_a_browser_the_grid_has_reaped`, `test_the_listing_still_uses_a_browser_that_is_live`, `test_with_no_store_only_downloads_are_listed`, the `test_keeping_*` group (~335-370), the `read_kept` group (~1000-1030) and `test_an_unkept_file_says_what_would_keep_it` / `test_a_kept_file_has_nothing_to_keep`. Delete each one whose behaviour `test_file_sections.py` now covers; port the rest (Grid failure surfaced, reaped browser ignored, no-store lists downloads only) to call `files.sections`. Leave the HTTP and admin tests for Tasks 3 and 5.

- [ ] **Step 5: Run** — `t tests/test_file_sections.py tests/test_file_folders.py tests/test_kept_files.py -x` → failures only in tests that go through `register` / HTTP / admin (Tasks 3 and 5). Note their names in the report.

- [ ] **Step 6: Commit** — `git commit -am "Files, screenshots and downloads are three sections addressed by path"` (add the new test file).

---

### Task 3: The MCP and REST surfaces for files

**Files:**
- Modify: `kubed/selenium_flow/http/files.py` (`register`, `_routes`, `FILE_ENDPOINTS`, `FILE_ROUTES`, `DESCRIPTION`)
- Modify: `kubed/selenium_flow/mcp/completions.py`; `kubed/selenium_flow/spec/schemas.py` (`FILE_SCHEMAS`, `_FILE_OPERATIONS`); `kubed/selenium_flow/spec/builder.py` (`_mcp_tools` files block, `_file_paths`)
- Test: `tests/test_file_surfaces.py` (create); update `tests/test_kept_files.py` HTTP tests (~450-540), `tests/test_resources.py`, `tests/test_surfaces.py`, `tests/test_openapi.py`

**Interfaces:**
- Consumes: Task 2.
- Produces:
  - resources `session://files`, `session://files/screenshots`, `session://files/downloads` (JSON) and templates `session://files/{name}`, `session://files/screenshots/{name}`, `session://files/downloads/{name}` (bytes)
  - tool `keep_file(uri: str) -> dict`, annotations `hints("Keep a file in Files", idempotent=False)`
  - tool `session_files() -> dict` returning `files.sections(...)` (app only)
  - `FILE_ENDPOINTS = ("list", "screenshots", "downloads", "keep")`, `FILE_ROUTES = {"list": ("get", ""), "screenshots": ("get", "/screenshots"), "downloads": ("get", "/downloads"), "keep": ("put", "/{folder}/{name}/kept")}`
  - completions for all three templates

- [ ] **Step 1: Write the failing tests** — `tests/test_file_surfaces.py`:

```python
"""What an agent and an HTTP caller can do with a session's files (§F4.6, §F4.7)."""

import asyncio
import json

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit
S = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}", "X-Session-Key": S}


@pytest.fixture
def srv(tmp_path):
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN, flow_data_dir=str(tmp_path))


@pytest.fixture
def http(srv):
    return TestClient(srv.mcp.http_app(), headers=AUTH)


def _run(coro):
    return asyncio.run(coro)


def test_the_resources_are_a_folder_and_two_sub_folders(srv):
    async def go():
        async with Client(srv.mcp) as c:
            listed = {str(r.uri) for r in await c.list_resources()}
            templated = {t.uriTemplate for t in await c.list_resource_templates()}
            return listed, templated
    listed, templated = _run(go())
    assert {"session://files", "session://files/screenshots", "session://files/downloads"} <= listed
    assert {
        "session://files/{name}",
        "session://files/screenshots/{name}",
        "session://files/downloads/{name}",
    } <= templated


def test_keep_file_takes_a_uri_and_is_not_idempotent(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return {t.name: t for t in await c.list_tools()}["keep_file"]
    tool = _run(go())
    assert list(tool.inputSchema["properties"]) == ["uri"]
    assert tool.inputSchema["required"] == ["uri"]
    assert tool.annotations.idempotentHint is False


def test_the_rest_tree_mirrors_the_folders(http, srv, tmp_path):
    srv.flows.create_file(S, "shot.png", b"p", flows.SCREENSHOTS_DIR)
    got = http.get("/files/screenshots").json()
    assert got["folder"] == "screenshots"
    assert [f["uri"] for f in got["files"]] == ["session://files/screenshots/shot.png"]
    assert http.get("/files").json()["folders"][0]["uri"] == "session://files/screenshots"


def test_keeping_over_http_moves_a_screenshot(http, srv):
    srv.flows.create_file(S, "shot.png", b"p", flows.SCREENSHOTS_DIR)
    got = http.put("/files/screenshots/shot.png/kept")
    assert got.status_code == 200, got.text
    assert got.json()["uri"] == "session://files/shot.png"
    assert srv.flows.files(S, flows.SCREENSHOTS_DIR) == []


@pytest.mark.parametrize("folder", ["files", "flows", "kept"])
def test_keeping_over_http_names_only_the_two_keepable_folders(http, folder):
    assert http.put(f"/files/{folder}/x.png/kept").status_code == 400


def test_reading_a_screenshot_resource_returns_its_bytes(srv):
    srv.flows.create_file(S, "shot.png", b"\x89PNG", flows.SCREENSHOTS_DIR)

    async def go():
        async with Client(srv.mcp) as c:  # stdio-like: the session is `stdio`
            return await c.read_resource("session://files/screenshots/shot.png")
    srv.flows.create_file("stdio", "shot.png", b"\x89PNG", flows.SCREENSHOTS_DIR)
    got = _run(go())
    assert got[0].blob or got[0].text
```

(If the in-process `Client` resolves the caller as a name other than `stdio`, read `sessions.name()` in the test and write the fixture file under that name — check how `tests/test_resources.py` does it and follow that.)

- [ ] **Step 2: Run** — `t tests/test_file_surfaces.py` → FAIL.

- [ ] **Step 3: Implement** `register`: three `@mcp.resource` listings (`root`, and `folder(..., SCREENSHOTS)` / `folder(..., DOWNLOADS)`), three byte templates (Files and Screenshots from the store; Downloads from the Grid with the caller's browser — keep today's `file_resource` reasoning), `session_files` returning `sections(...)` with `app=app_config`, and:

```python
    @mcp.tool(
        name=KEEP_TOOL,
        description=(
            "Keep one file in Files, where it stays until a person deletes it.\n\n"
            "uri is as session://files and its folders list it. A screenshot "
            "moves out of session://files/screenshots; a download is copied, "
            "since the browser keeps its own until it ends. The result is the "
            "file's new uri — a clash lands as name (1). upload_file(file=uri) "
            "puts any file back into a page."
        ),
        annotations=hints("Keep a file in Files", idempotent=False),
    )
    def keep_file(uri: str) -> dict:
        return keep(actions, sessions, store, uri)
```

`_routes`: GET `/files`, GET `/files/screenshots`, GET `/files/downloads`, PUT `/files/{folder}/{name}/kept` — the last refuses any `folder` not in `(SCREENSHOTS, DOWNLOADS)` with a `ValueError` naming them (→ 400 via `errors.py`), then calls `keep(..., uri_of(folder, name), name=<caller>)`. Register the two literal GETs **before** anything with a path parameter.

`completions.py`: add `("session://files/screenshots/{name}", "name")` and `("session://files/downloads/{name}", "name")` to `TEMPLATES`, each completed from its folder resource (`(uri, "files", "name")`).

`spec/schemas.py`: `FileEntry` drops `kept`, adds `uri` (`"This file's address. keep_file and upload_file(file=) take it."`); `keep_with`'s description says it is present on screenshots and downloads; add `FileFolder` (`name`, `uri`, `count`, `browser`), give `FileList` a `folders` array of it, add `FolderList` (`session`, `folder`, `uri`, `count`, `browser`, `files`), and reshape `FileKept` to `kept`, `from`, `uri`, `name`, `size`, `created`. `_FILE_OPERATIONS`: `list` (listFiles, root), `screenshots` (listScreenshots, FolderList), `downloads` (listDownloads, FolderList), `keep` (keepFile — "Moves a screenshot into Files, or copies a download there…", request `{}` since both are in the path). `builder._mcp_tools` files block: `list`→`ROOT_URI`, `screenshots`→`FOLDER_URI[SCREENSHOTS]`, `downloads`→`FOLDER_URI[DOWNLOADS]`, `keep`→`KEEP_TOOL`. `_file_paths` must accept the `folder` path parameter (`_named_in_path` already reads `{…}` from the template).

- [ ] **Step 4: Update the old surface tests**: in `tests/test_kept_files.py` rewrite `test_keep_then_list_over_http` (→ keep a download over `PUT /files/downloads/{name}/kept`), `test_an_unusable_name_is_a_400_over_http`, `test_a_grid_that_says_no_is_not_a_500`, and the published-shape test `test_the_published_file_shape_matches_what_describe_returns` (→ compare `FileEntry` properties with `files.describe` keys). In `test_resources.py` / `test_surfaces.py` / `test_openapi.py`, change every `session://files` expectation and `keep_file` schema expectation to the new ones — the assertion changes, the property it guards does not.

- [ ] **Step 5: Run** — `t` → only admin/actions/UI failures remain (Tasks 4–5). `lint` clean.

- [ ] **Step 6: Commit** — `git commit -am "session://files is a folder: keep_file takes a URI, REST mirrors the paths"`

---

### Task 4: Screenshots keep into screenshots; upload takes any file by URI

**Files:**
- Modify: `kubed/selenium_flow/core/actions.py` (`Actions.__init__`, `_kept`, `screenshot`, `upload_file`); `kubed/selenium_flow/server.py` (wiring ~160-171); `kubed/selenium_flow/mcp/tools.py` (`upload_file`); `kubed/selenium_flow/spec/builder.py` (multipart `kept` → `file`); `kubed/selenium_flow/flows/run.py` (comment on `LIBRARY_ARG`)
- Test: `tests/test_screenshot_saving.py`, `tests/test_kept_files.py` upload tests (~1033-1135)

**Interfaces:**
- Consumes: `files.keep_made(..., folder=)`, `files.read_file(actions, sessions, store, uri, name=None) -> (name, bytes)`.
- Produces: `Actions(grid, keep=None, pointers=None, read_file=None)`; `keep(name, data, folder)`; `read_file(uri, session=None) -> tuple[str, bytes]`; `upload_file(..., file=None, session=None)`.

- [ ] **Step 1: Failing tests.** In `tests/test_screenshot_saving.py` change `_Keeper.__call__` to `(self, name, data, folder)` recording `(name, data, folder)`, and add:

```python
def test_a_screenshot_is_kept_in_the_screenshots_folder(acting, keeper):
    acting.screenshot("abc")
    assert keeper.kept[0][2] == "screenshots"


def test_a_print_is_kept_in_files(acting, keeper):
    acting.print_("abc")
    assert keeper.kept[0][2] == "files"
```

In `tests/test_kept_files.py`, rewrite `test_upload_sends_the_bytes_of_a_kept_file` as `test_upload_sends_any_file_named_by_its_uri` (parametrise over a Files URI and a screenshot URI, stubbing `read_file` to return `("x.png", b"...")`), rename `kept=` to `file=` in `test_an_http_caller_can_name_the_library_its_file_was_kept_in`, `test_the_upload_endpoint_accepts_the_library_name`, `test_a_kept_upload_is_refused_when_there_is_nowhere_to_keep`, `test_only_one_source_may_be_given`, and add:

```python
def test_upload_says_file_is_a_uri(actions):
    with pytest.raises(ValueError, match="file"):
        actions.upload_file("abc", selector={"css": "input"})
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.** `_kept(self, name, data, folder=FILES)` passes `folder` through (import the constants from `..flows.library` as `FILES_DIR`/`SCREENSHOTS_DIR` — the domain layer must not import `http`). `screenshot` calls `self._kept(_generated_name(filename or "screenshot", ".png"), raw, SCREENSHOTS_DIR)`; `print_` calls with `FILES_DIR`. Rename the `read_kept` attribute and constructor argument to `read_file`; in `upload_file` rename `kept` → `file`: the source list becomes `("text", text), ("content", content), ("file", file), ("path", path)`, the messages say "file for any file this session has, by its session://files uri", and the branch does `name, raw = self.read_file(str(file), str(session) if session else None)` then `name = _safe_name(filename or name, mime_type)`. Update the docstring bullet (§F4.7: any of the three kinds). `server.py`:

```python
        self.actions.keep = lambda name, data, folder: files.keep_made(
            self.sessions, self.flows, name, data, auth_token, base, self.prefix, folder
        )
        self.actions.read_file = lambda uri, session=None: files.read_file(
            self.actions, self.sessions, self.flows, uri, session
        )
```

`mcp/tools.py` `upload_file`: parameter `file: str | None = None` in place of `kept`, docstring bullet "file, any file this session has, by its uri from session://files — a screenshot, a download or a file in Files — without its bytes passing through you". `spec/builder.py` multipart: rename the `kept` property to `file` with the same meaning. `flows/run.py`: the comment says `upload_file(file=...)`.

- [ ] **Step 4: Run** — `t tests/test_screenshot_saving.py tests/test_kept_files.py tests/test_surfaces.py tests/test_openapi.py tests/test_flowrun.py` → PASS. `lint`.

- [ ] **Step 5: Commit** — `git commit -am "Screenshots keep into their own folder; upload_file takes any file by its uri"`

---

### Task 5: The admin files API, the screenshot route and the counts

**Files:**
- Modify: `kubed/selenium_flow/http/admin.py` (`sessions_payload` ~334-472, `admin_files` ~625-687, `admin_keep_file` ~689-713, `admin_delete_file` ~715-735, new signed route beside `kept_file` ~966)
- Test: `tests/test_admin_files.py` (create); rewrite admin tests in `tests/test_kept_files.py` (~617-990)

**Interfaces:**
- Consumes: Task 2 (`sections`, `keep`, `delete_one`, `clear_screenshots`, `uri_of`, `links.screenshot_path`).
- Produces:
  - `GET /admin/sessions/{key}/files` → `{key, session: <header row>, browser: bool, downloads: [...], screenshots: [...], files: [...]}`
  - `DELETE /admin/sessions/{key}/files/downloads` (clears the Grid store), `DELETE /admin/sessions/{key}/files/screenshots` (clears the folder), `DELETE /admin/sessions/{key}/files/{name}` (one file in Files) — one route; the name decides
  - `POST /admin/sessions/{key}/files/{folder}/{name}/keep` (`folder` ∈ screenshots, downloads)
  - `GET /screenshots/{session}/{name}` signed, like `/kept/…`
  - session rows gain `counts: {downloads: int|None, screenshots: int|None, files: int|None}`; `files_count` is their sum (None when all unknown); `files_rev` is JSON of sorted `[folder, name]` pairs; `kept_count` is removed.

- [ ] **Step 1: Failing tests** — `tests/test_admin_files.py`, using a `SeleniumMCP` with `flow_data_dir=tmp_path`, a stored `SessionRecord(session_id="abc", url="https://x/")` under key `desktop`, and the `FakeGrid`/monkeypatch pattern `tests/test_kept_files.py` already uses for `actions.grid` (copy that fixture block verbatim — do not import it across test modules). Cases:

```python
def test_the_listing_is_three_sections(client, live, tmp_path): ...
    # a download on the grid, a screenshot and a Files file on disk
    # → body["downloads"], body["screenshots"], body["files"] each hold exactly theirs; body["browser"] is True

def test_clearing_screenshots_leaves_files(client, live): ...
    # DELETE /admin/sessions/desktop/files/screenshots → 200 {"cleared": n}; Files untouched

def test_clearing_downloads_empties_the_grid_store(client, live): ...
    # DELETE …/files/downloads → grid.clear_files("abc") called; screenshots untouched

def test_a_reaped_browser_has_no_downloads_to_clear(client, kept_server): ...
    # record with session_id but grid says not alive → DELETE …/files/downloads → 200, grid.clear_files NOT called

def test_deleting_one_file_in_files(client, live): ...

def test_there_is_no_delete_for_one_screenshot(client, live): ...
    # DELETE …/files/screenshots/shot.png → 404 or 405, and the screenshot is still there

def test_keeping_from_the_admin_moves_a_screenshot(client, live): ...
    # POST …/files/screenshots/shot.png/keep → 200, file now in Files

@pytest.mark.parametrize("folder", ["files", "flows"])
def test_the_admin_keeps_only_from_the_two_folders(client, live, folder): ...  # → 400

def test_the_screenshot_route_serves_a_signed_file(client, live): ...
    # url from the listing's screenshot entry → GET it → 200, bytes; without sig → 403

def test_the_session_row_counts_each_section(client, live): ...
    # row["counts"] == {"downloads": 1, "screenshots": 1, "files": 1}; row["files_count"] == 3

def test_the_file_stamp_changes_when_a_screenshot_is_kept(client, live): ...
    # files_rev before != after POST keep, even though files_count is unchanged
```

Every endpoint above also gets a no-token case returning 401 (one parametrised test).

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.** `admin_files` GET: keep today's `is_alive` reasoning, then `files.sections(actions, sessions, flow_store, token, session_name, mount=prefix, session_id=live_id, downloads=grid_listing)` merged with `{"key": key, "session": await header(key, attached)}`. One DELETE handler on `/files/{name}`:

```python
        name = request.path_params["name"]
        if name == files.DOWNLOADS:
            session_id = attached_id(key)
            alive = session_id and await run_in_threadpool(actions.grid.is_alive, session_id)
            if alive:
                await run_in_threadpool(actions.grid.clear_files, session_id)
            return JSONResponse({"success": True, "key": key, "cleared": files.DOWNLOADS})
        if name == files.SCREENSHOTS:
            got = await run_in_threadpool(files.clear_screenshots, flow_store, library(key))
            return JSONResponse({"success": True, "key": key, **got})
        removed = await run_in_threadpool(files.delete_one, flow_store, library(key), name)
        return JSONResponse(removed)
```

(Wrap each in the existing `errors.status_for` / `errors.message` refusal pattern.) Keep: route `…/files/{folder}/{name}/keep`, `POST`; refuse a folder outside `(SCREENSHOTS, DOWNLOADS)` with `ValueError` → 400; call `files.keep(actions, sessions, flow_store, files.uri_of(folder, name), name=key, session_id=attached_id(key))`. **Delete** the old `…/files` DELETE and the old `…/files/{name}/keep` route. Signed route `GET {prefix}/screenshots/{session}/{name}` copying `kept_file`, validating against `links.screenshot_path` and reading `flow_store.read_file(session, name, flows.SCREENSHOTS_DIR)`.

`sessions_payload`: replace `kept = named(flow_store.files, session)` with names per folder (`lambda s: flow_store.files(s, flows.SCREENSHOTS_DIR)` and the Files default), compute `counts`, `files_count = sum(v for v in counts.values() if v is not None)` or None, and `files_rev = json.dumps(sorted([f, n] for f, names in (("downloads", downloads or []), ("screenshots", shots), ("files", kept)) for n in names))`. Keep the comments that explain *why* the stamp is names-with-folders and not a count (reword "kept copy" to "a name moving between folders"). Remove `kept_count`.

- [ ] **Step 4: Port or delete** the admin tests in `tests/test_kept_files.py` (~617-990): delete those now covered by `test_admin_files.py` (keep/delete/clear/listing/stamp), port the ones about unusable session names, reaped browsers, Grid outage and the disposition header to the new routes.

- [ ] **Step 5: Run** — `t` → only UI tests may fail (Task 7+). `lint`.

- [ ] **Step 6: Commit** — `git commit -am "The admin API clears screenshots or downloads, keeps from either, and counts each"`

---

### Task 6: `GET /admin/secrets` — the catalogue and who uses each

**Files:**
- Modify: `kubed/selenium_flow/http/admin.py` (`register` gains `catalogue=None`; new route); `kubed/selenium_flow/server.py` (pass `catalogue=self.secrets`)
- Create: `kubed/selenium_flow/http/secret_uses.py`
- Test: `tests/test_admin_secrets.py` (create)

**Interfaces:**
- Produces:
  - `secret_uses.uses(store) -> dict[str, list[dict]]` — secret name → `[{session, flow, steps: [1-based ints], keys: [str], shared: bool}]`, sorted by (shared, session, flow)
  - `GET /admin/secrets` → `{enabled: bool, count, secrets: [<catalogue entry> + {uses: [...]}], undefined: [{name, uses: [...]}]}`; 401 without the token; `enabled: false` with empty lists when no catalogue

- [ ] **Step 1: Failing tests** — `tests/test_admin_secrets.py`:

```python
"""The Secrets tab's one endpoint: the catalogue, and which flows type each (§F4.10)."""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.http import secret_uses
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _flow(store, lib, name, steps):
    store.save(lib, name, {"description": name, "steps": steps})


def _types(secret, key, css="#x"):
    return {"tool": "write", "args": {"selector": {"css": css}, "secret": {"name": secret, "key": key}}}


NAV = {"tool": "navigate", "args": {"url": "https://example.test/"}}


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path / "flows")


def test_a_flow_that_types_a_secret_is_a_use_with_its_steps(store):
    _flow(store, "desktop", "login", [NAV, _types("demo", "username"), _types("demo", "password")])
    assert secret_uses.uses(store) == {
        "demo": [{"session": "desktop", "flow": "login", "steps": [2, 3], "keys": ["username", "password"], "shared": False}]
    }


def test_a_shared_flow_is_marked_shared(store):
    _flow(store, flows.GLOBAL_SESSION, "nc", [_types("nextcloud", "password")])
    assert secret_uses.uses(store)["nextcloud"][0]["shared"] is True


def test_a_flow_with_no_secret_is_not_a_use(store):
    _flow(store, "desktop", "search", [NAV])
    assert secret_uses.uses(store) == {}


def test_a_broken_flow_does_not_take_the_listing_down(store, tmp_path):
    _flow(store, "desktop", "login", [_types("demo", "password")])
    (tmp_path / "flows" / "desktop" / "flows" / "broken.yaml").write_text("steps: [", encoding="utf-8")
    assert list(secret_uses.uses(store)) == ["demo"]


@pytest.fixture
def secrets_dir(tmp_path):
    d = tmp_path / "secrets" / "demo"
    d.mkdir(parents=True)
    (d / "username").write_text("u")
    (d / "password").write_text("p")
    return tmp_path / "secrets"


def test_the_endpoint_joins_the_catalogue_to_its_uses(tmp_path, secrets_dir):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN,
                      flow_data_dir=str(tmp_path / "flows"), secrets_dirs=str(secrets_dir))
    _flow(srv.flows, "desktop", "login", [_types("demo", "password"), _types("gone", "token")])
    body = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).json()
    assert body["enabled"] is True
    demo = next(s for s in body["secrets"] if s["name"] == "demo")
    assert demo["uses"][0]["flow"] == "login"
    assert body["undefined"] == [{"name": "gone", "uses": [
        {"session": "desktop", "flow": "login", "steps": [2], "keys": ["token"], "shared": False}]}]


def test_no_value_is_ever_sent(tmp_path, secrets_dir):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN, secrets_dirs=str(secrets_dir))
    text = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).text
    assert '"u"' not in text and '"p"' not in text


def test_it_needs_the_token(tmp_path):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)
    assert TestClient(srv.mcp.http_app()).get("/admin/secrets").status_code == 401


def test_with_no_catalogue_it_says_off(tmp_path):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)
    body = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).json()
    assert body == {"enabled": False, "count": 0, "secrets": [], "undefined": []}
```

(Check how a secrets directory is laid out in `tests/test_secrets.py` and match it exactly — the layout above is the assumption to verify, and the `SeleniumMCP` keyword is `secrets_dirs`. If the catalogue needs a metadata file for `description`/`allowed_urls`, write one the way those tests do.)

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** `secret_uses.py`:

```python
"""Which stored flows type which secrets — the Secrets tab's backlinks (§F4.10).

A scan of what is on disk, because a step's `args.secret` already names the
secret and the key. Nothing new is recorded: a session typing a secret by hand
with `write` leaves no trace, and recording that would be logging secret use —
its own decision, not a side effect of a page.
"""

from __future__ import annotations

import logging

from ..flows import document as flowdoc
from ..flows import library as flows

log = logging.getLogger(__name__)


def uses(store) -> dict[str, list[dict]]:
    """Secret name -> the flows whose steps type it, with 1-based step numbers."""
    found: dict[str, dict[tuple, dict]] = {}
    if store is None:
        return {}
    for lib in store.sessions():
        try:
            names = store.names(lib)
        except Exception:  # noqa: BLE001 - one unreadable library is not an outage
            log.info("could not list flows in %s", lib)
            continue
        for name in names:
            document = store.get(lib, name)  # a broken file reads as absent
            steps = document.get("steps") if isinstance(document, dict) else None
            for index, step in enumerate(steps if isinstance(steps, list) else [], start=1):
                args = step.get("args") if isinstance(step, dict) else None
                ref = args.get(flowdoc.SECRET_ARG) if isinstance(args, dict) else None
                if not isinstance(ref, dict) or not ref.get("name"):
                    continue
                use = found.setdefault(str(ref["name"]), {}).setdefault(
                    (lib, name),
                    {"session": lib, "flow": name, "steps": [], "keys": [],
                     "shared": lib == flows.GLOBAL_SESSION},
                )
                use["steps"].append(index)
                if ref.get("key") and ref["key"] not in use["keys"]:
                    use["keys"].append(str(ref["key"]))
    return {
        secret: sorted(by.values(), key=lambda u: (u["shared"], u["session"], u["flow"]))
        for secret, by in found.items()
    }
```

Route in `admin.py`:

```python
    @mcp.custom_route(f"{prefix}/admin/secrets", methods=["GET"], name="admin_secrets")
    @guarded
    async def admin_secrets(_request: Request) -> JSONResponse:
        """The catalogue, each entry with the stored flows that type it (§F4.10).

        Never a value: the catalogue has none to give. Names a flow uses that no
        secret answers to come back as `undefined`, because such a flow fails at
        the step that types it and this is where an operator can see that.
        """
        if catalogue is None:
            return JSONResponse({"enabled": False, "count": 0, "secrets": [], "undefined": []})
        listed = await run_in_threadpool(catalogue.listing)
        used = await run_in_threadpool(secret_uses.uses, flow_store)
        known = {s["name"] for s in listed["secrets"]}
        return JSONResponse({
            "enabled": True,
            "count": listed["count"],
            "secrets": [{**s, "uses": used.get(s["name"], [])} for s in listed["secrets"]],
            "undefined": [{"name": n, "uses": u} for n, u in sorted(used.items()) if n not in known],
        })
```

`server.py`: `admin.register(..., catalogue=self.secrets)`.

- [ ] **Step 4: Run** — `t tests/test_admin_secrets.py tests/test_secrets.py` → PASS; `lint`.

- [ ] **Step 5: Commit** — `git commit -am "The admin API lists secrets with the flows that type them, and names nobody defined"`

---

### Task 7: The shared components — tiles, sections, a lightbox that steps

**Files:**
- Modify: `static/components.js`; `static/app.html` (nothing but the component name if it is referenced); `static/app.css`
- Test: `tests/test_admin_ui.py` (components block), `tests/test_resources.py` if it asserts `fileGrid`

**Interfaces:**
- Produces on `SF`:
  - `fileGrid(el, files, opts)` — `files` an array; `opts.base`, `opts.empty` (string), `opts.action` (`'keep'` | `'delete'` | undefined), `opts.onopen(index)` (else the lightbox opens itself over this list). Tiles carry **no** status mark; the action button (when `opts.action`) is `<button class="act keep" data-keep="<name>">📌</button>` or `<button class="act drop" data-delete="<name>">🗑️</button>` — the **name**, because the admin endpoints address a file by folder and name.
  - `fileSections(el, data, opts)` — renders `data.downloads`, `data.screenshots`, `data.files` as three `section.row` blocks titled Downloads / Screenshots / Files with counts; read-only unless `opts.actions`.
  - `lightbox(files, index, opts)` — `opts.base`, `opts.action` = `{label, run: async (file) => void}` (optional), `opts.onclose()`; head is `name · i / n · ‹ Prev · Next › · [action] · Download · Close`; ← → step, Esc closes, Prev/Next `disabled` at the ends; after `action.run` resolves the lightbox **re-renders over the list it is given back** via `opts.refresh()` → `{files, index}` so keep advances.
  - `sessionList`: the meta line says the non-zero counts from `s.counts` ("1 download · 6 screenshots · 2 files"), falling back to `files_count`.

- [ ] **Step 1: Failing tests** in `tests/test_admin_ui.py` — replace the file-tile tests that pin the status marks (`status / pin`, `mark bubble`, `➕`) with:

```python
def test_a_tile_carries_one_action_and_no_status_mark(components):
    """The row says what a file is, so the corner marks went (§F4.9)."""
    assert "mark pin" not in components and "mark bubble" not in components
    assert 'class="act keep" data-keep="' in components
    assert 'class="act drop" data-delete="' in components
    assert "&#128204;" in components  # the pin is the keep action now


def test_the_lightbox_steps_and_says_where_it_is(components):
    for needed in ("‹ Prev", "Next ›", "ArrowLeft", "ArrowRight", "data-prev", "data-next", " / "):
        assert needed in components, needed


def test_the_lightbox_disables_its_ends_rather_than_wrapping(components):
    assert "index === 0" in components and "index === files.length - 1" in components


def test_the_app_draws_three_read_only_rows(components):
    assert "function fileSections(" in components
    for title in ("'Downloads'", "'Screenshots'", "'Files'"):
        assert title in components, title


def test_the_session_card_names_each_count(components):
    assert "' download'" in components and "' screenshot'" in components
```

And keep `test_the_shared_library_never_wires_a_click` (or whatever the existing no-wiring guard is called) passing — `fileGrid` renders buttons, the page wires them.

- [ ] **Step 2: Run** — `t tests/test_admin_ui.py` → FAIL.

- [ ] **Step 3: Implement.** `fileGrid`: drop both status marks and their comment block (replace it with two lines: the row says what a file is; the one action is a real button, rendered here and wired by the page). Tile HTML per action as in the interface. The thumb click calls `opts.onopen ? opts.onopen(i) : lightbox(files, i, {base})`. `fileSections`:

```js
  function fileSections(el, data, opts = {}) {
    el.innerHTML = '';
    const rows = [
      ['Downloads', data.downloads || [], 'No downloads.'],
      ['Screenshots', data.screenshots || [], 'No screenshots yet.'],
      ['Files', data.files || [], 'Nothing here yet — prints land here, and anything you keep.'],
    ];
    for (const [title, files, empty] of rows) {
      const row = document.createElement('section');
      row.className = 'row';
      row.innerHTML = '<div class="head"><strong>' + esc(title) + '</strong>' +
        '<span class="pill">' + files.length + '</span></div><div class="body"></div>';
      fileGrid(row.querySelector('.body'), files, {base: opts.base, empty});
      el.appendChild(row);
    }
  }
```

`lightbox(files, index, opts)` — build once, re-render its contents on each step:

```js
  function lightbox(files, index, opts = {}) {
    const base = opts.base || '';
    const box = document.createElement('div');
    box.className = 'lightbox';
    let list = files, at = index;
    function render() {
      const f = list[at];
      const href = base + f.url;
      const kind = f.content_type || '';
      const viewable = f.image || kind === 'application/pdf';
      box.innerHTML =
        '<div class="head"><span class="name">' + esc(f.name) + '</span>' +
        '<span class="pos">' + (at + 1) + ' / ' + list.length + '</span>' +
        '<span class="grow"></span>' +
        '<button type="button" data-prev' + (at === 0 ? ' disabled' : '') + '>‹ Prev</button>' +
        '<button type="button" data-next' + (at === list.length - 1 ? ' disabled' : '') + '>Next ›</button>' +
        (opts.action ? '<button type="button" data-act>' + esc(opts.action.label) + '</button>' : '') +
        '<a href="' + esc(href) + '" download="' + esc(f.name) + '">Download</a>' +
        '<button type="button" data-close>Close</button></div>' +
        '<div class="body">' +
        (f.image ? '<img alt="' + esc(f.name) + '" src="' + esc(href) + '">'
          : viewable ? '<iframe title="' + esc(f.name) + '" src="' + esc(href) + '"></iframe>'
          : '<div class="nopreview"><span class="glyph">' + (GLYPH[(f.name.split('.').pop() || '').toLowerCase()] || '📁') +
            '</span><p>No preview for this kind of file.</p><a href="' + esc(href) + '" download="' +
            esc(f.name) + '">Download</a></div>') +
        '</div>';
    }
    const step = (d) => { const n = at + d; if (n >= 0 && n < list.length) { at = n; render(); } };
    function close() { box.remove(); document.removeEventListener('keydown', onKey); if (opts.onclose) opts.onclose(); }
    function onKey(e) {
      if (e.key === 'Escape') close();
      else if (e.key === 'ArrowLeft') step(-1);
      else if (e.key === 'ArrowRight') step(1);
    }
    box.addEventListener('click', async (e) => {
      const t = e.target;
      if (t === box || t.hasAttribute('data-close')) return close();
      if (t.hasAttribute('data-prev')) return step(-1);
      if (t.hasAttribute('data-next')) return step(1);
      if (t.hasAttribute('data-act') && opts.action) {
        t.disabled = true;
        try {
          await opts.action.run(list[at]);
          const next = opts.refresh ? await opts.refresh(at) : null;
          if (!next || !next.files.length) return close();
          list = next.files; at = Math.min(next.index, list.length - 1); render();
        } catch (err) { t.disabled = false; alert(err.message); }
      }
    });
    document.addEventListener('keydown', onKey);
    render();
    document.body.appendChild(box);
  }
```

The `index === 0` / `index === files.length - 1` strings the test pins: name the render variables `index`/`files` instead of `at`/`list` if you prefer, but the test and the code must agree — **update the test to the names you use**, the property is "disabled at the ends". Export `fileSections` on `SF`. `sessionList` meta: build `[['download', c.downloads], ['screenshot', c.screenshots], ['file', c.files]]` → non-zero parts `n + ' ' + word + (n === 1 ? '' : 's')`, joined with ` · `. CSS: `.lightbox .pos { opacity: .7 }`, `.lightbox button:disabled { opacity: .35 }`, `.lightbox .nopreview { … centred column, #f2f2f7 }`, `.pill.warn { color: var(--warn); border-color: currentColor; }`, and `section.row` styling equal to today's `.section` (reuse the rules, do not duplicate them — add `section.row` to the existing selectors).

`app.html`: nothing changes unless it names `fileGrid`; `session_files` now returns `component: "fileSections"`.

- [ ] **Step 4: Run** — `t tests/test_admin_ui.py tests/test_resources.py` and `node --check` (the parse test runs it) → PASS.

- [ ] **Step 5: Commit** — `git commit -am "Tiles carry one action, the app draws three rows, and the lightbox steps"`

---

### Task 8: The session page — tabs, three rows, keep, clear, delete

**Files:**
- Modify: `static/admin.html` (markup ~57-105; script: `showDetail`, `loadFiles`, the `$('files')` click handler, `$('clearFiles')`, `openSession`, `refreshDetail`, `route`); `static/app.css`
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: Task 5's API, Task 7's components.
- Produces (page-level, for Task 9–10): `openSession(key, tab, flow)`; `sessionTab(tab)`; element ids `sessionTabs`, `tabFiles`, `tabFlows`, `paneFiles`, `paneFlows`, `downloadsSection` / `downloads` / `clearDownloads` / `downloadsCount`, `screenshotsSection` / `screenshots` / `clearScreenshots` / `screenshotsCount`, `keptSection` / `kept` / `keptCount`; module state `filesData = {downloads, screenshots, files, browser}`.

- [ ] **Step 1: Failing tests** in `tests/test_admin_ui.py` — replace `test_the_detail_view_is_two_accordions` and the Clear-downloads tests with:

```python
def test_files_and_flows_are_tabs(page):
    """§F4.8: the page is one tab's column at a time."""
    for id_ in ("sessionTabs", "tabFiles", "tabFlows", "paneFiles", "paneFlows"):
        assert f'id="{id_}"' in page, id_


def test_the_files_tab_is_three_rows_in_order(page):
    at = [page.index(f'id="{s}Section"') for s in ("downloads", "screenshots", "kept")]
    assert at == sorted(at), "Downloads, Screenshots, Files — in that order (§F4.5)"
    assert ">Files<" in page.split('id="keptSection"')[1][:600]


def test_each_row_that_clears_has_its_own_button(page):
    assert 'id="clearDownloads"' in page and 'id="clearScreenshots"' in page
    assert 'id="clearKept"' not in page, "Files is never cleared wholesale (§F4.1)"


def test_clear_downloads_needs_a_live_browser_not_an_attached_one(page):
    assert "$('clearDownloads').disabled = !row.live" in page


def test_the_tab_is_in_the_hash(page):
    assert "'/flows'" in page and "#/sessions/" in page


def test_clearing_screenshots_confirms_with_the_names(page):
    handler = page.split("$('clearScreenshots').onclick")[1][:1800]
    assert "modal(" in handler and "'/files/screenshots'" in handler and "'DELETE'" in handler


def test_keeping_posts_to_the_folder_it_came_from(page):
    assert "'/keep'" in page and "encodeURIComponent(folder)" in page
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.** Markup, inside `#detailView` after `#detailHeader`:

```html
      <div class="tabs subtabs" id="sessionTabs">
        <button id="tabFiles" aria-selected="true">Files <span id="filesTotal" class="count"></span></button>
        <button id="tabFlows" aria-selected="false">Flows <span id="flowsTotal" class="count"></span></button>
      </div>
      <div id="paneFiles">
        <!-- Downloads, Screenshots, Files: each clears in the way that fits
             what it holds (§F4.1). Files has no clear: everything in it was
             put there on purpose. -->
        <section class="section" id="downloadsSection" data-open="true">…head: caret, "Downloads", pill #downloadsCount, grow, <button id="clearDownloads" class="danger">Clear downloads</button>… <div class="body"><div id="downloads"></div></div></section>
        <section class="section" id="screenshotsSection" data-open="true">… "Screenshots", #screenshotsCount, <button id="clearScreenshots" class="danger">Clear screenshots</button> … <div id="screenshots"></div></section>
        <section class="section" id="keptSection" data-open="true">… "Files", #keptCount … <div id="kept"></div></section>
      </div>
      <div id="paneFlows" hidden>
        <div class="section"><div class="body"><div id="flows"></div></div></div>
      </div>
```

(Write each section head in full, with the same `button.title[data-toggle]` + `aria-expanded`/`aria-controls` + caret markup today's sections use.) Remove `#filesSection`, `#flowsSection`'s accordion head, `#clearFiles`, `#filesCount`, `#flowsCount` (the Flows total moves to `#flowsTotal`; update `loadFlows` to write it).

Script:
- `showDetail(row)`: `$('endBrowser').disabled = !row.attached; $('clearDownloads').disabled = !row.live;` and `$('filesTotal').textContent = row.files_count ?? ''`.
- `loadFiles(key)`: `GET …/files`, store `filesData`, write the three counts, disable `clearScreenshots` when there are none, and draw the three rows with `SF.fileGrid($('downloads'), d.downloads, {base: ROOT, action: 'keep', empty: d.browser ? 'No downloads.' : 'No browser — downloads go with it.', onopen: (i) => openLightbox('downloads', i)})`, likewise `screenshots` (`action: 'keep'`, empty "No screenshots yet.") and `kept` (`action: 'delete'`, empty "Nothing here yet — prints land here, and anything you keep.").
- One delegated click handler on `#paneFiles` replacing today's `$('files')` one: `data-keep` → `folder` is the row the tile is in (`e.target.closest('section').id === 'screenshotsSection' ? 'screenshots' : 'downloads'`); `POST sessionPath(key, '/files/' + encodeURIComponent(folder) + '/' + encodeURIComponent(name) + '/keep')` where `name` is the tile's `data-keep`. `data-delete` → today's confirm, retitled "Delete a file" with "This removes it from Files for good. There is no undo." Keep the "read `current` once" comment and behaviour.
- `$('clearScreenshots').onclick`: `modal({title: 'Clear screenshots', body: '<p>Deletes all ' + n + ' screenshots in this session. Anything you kept is in Files and stays.</p><ul class="names">…every name…</ul>', confirm: 'Delete ' + n + ' screenshot' + (n === 1 ? '' : 's'), danger: true, onconfirm: … api(sessionPath(key, '/files/screenshots'), 'DELETE') … loadFiles(key)})`.
- `$('clearDownloads').onclick`: today's handler, pointed at `'/files/downloads'`, its per-name fate reading "— copy in Files stays" for a name also in `filesData.files` and "— gone" otherwise.
- Tabs: `sessionTab(tab)` sets `aria-selected` and shows one pane; `$('tabFiles').onclick = () => go('#/sessions/' + encodeURIComponent(current))`, `$('tabFlows').onclick = () => go('#/sessions/' + encodeURIComponent(current) + '/flows')`.
- `route()`: split the hash into `[view, key, tab, flow]`; `sessions/<key>[/flows[/<flow>]]` calls `openSession(key, tab === 'flows' ? 'flows' : 'files', flow)`; only reload the session's data when the key changed (switching tabs must not refetch). `openFlow(name)` also updates the hash to `#/sessions/<key>/flows/<name>` with `history.replaceState` (so picking a flow does not stack history); `openSession(..., flow)` opens that flow after `loadFlows`.
- `refreshDetail`: unchanged in spirit — re-fetch files when `filesStamp(row)` moved, flows when `flowsStamp(row)` moved, whichever tab is showing.

CSS: `.subtabs { margin-top: var(--gap) }`, `.tabs .count { color: var(--muted); margin-left: 4px }`.

- [ ] **Step 4: Run** — `t tests/test_admin_ui.py` (includes the `node --check` parse test) → PASS; then the whole suite `t`.

- [ ] **Step 5: Commit** — `git commit -am "The session page: Files and Flows tabs, and three rows that keep, clear and delete"`

---

### Task 9: The lightbox on the page — stepping, and keep that moves on

**Files:**
- Modify: `static/admin.html` (`openLightbox`)
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: `SF.lightbox(files, index, opts)` (Task 7), `filesData`, `loadFiles` (Task 8).
- Produces: `openLightbox(folder, index)`.

- [ ] **Step 1: Failing test:**

```python
def test_keep_in_the_lightbox_moves_on_to_the_next_screenshot(page):
    """§F4.9: 109 can be triaged without closing it."""
    body = page.split("function openLightbox(")[1][:2500]
    assert "refresh:" in body and "'📌 Keep'" in body and "'🗑 Delete'" in body
    assert "await loadFiles(" in body, "the list is re-read before stepping on"
    assert "oncancel" in body, "closing the confirm any way must release the button"
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement:**

```js
/* One lightbox for all three rows, stepping through the row it was opened
   from. Keep advances: the kept screenshot leaves the list, so the one after it
   is now at the same position — which is the next screenshot (§F4.9). Deleting
   from Files works the same way, after its confirm. */
function openLightbox(folder, index) {
  const key = current;
  const list = () => (filesData && filesData[folder === 'kept' ? 'files' : folder]) || [];
  const keepable = folder === 'screenshots' || folder === 'downloads';
  SF.lightbox(list(), index, {
    base: ROOT,
    action: keepable
      ? {label: '📌 Keep', run: (f) => api(sessionPath(key, '/files/' + encodeURIComponent(folder)
          + '/' + encodeURIComponent(f.name) + '/keep'), 'POST')}
      : {label: '🗑 Delete', run: (f) => new Promise((resolve, reject) => modal({
          title: 'Delete a file',
          body: '<p>This removes it from Files for good. There is no undo.</p>'
              + '<ul class="names"><li>' + SF.esc(f.name) + '</li></ul>',
          confirm: 'Delete', danger: true,
          onconfirm: async () => { await api(sessionPath(key, '/files/' + encodeURIComponent(f.name)), 'DELETE'); resolve(); },
          oncancel: () => reject(new Error('cancelled')),
        }))},
    // The same index either way: a kept screenshot or a deleted file leaves
    // the list, so the next one slides into its place; a kept download is a
    // copy and stays, so the lightbox stays on it.
    refresh: async (at) => {
      await loadFiles(key);
      if (gone(key)) return null;
      return {files: list(), index: at};
    },
  });
}
```

`modal()` gains an `oncancel` option, called by its `close()` whenever it closes **without** a successful confirm — Cancel, Esc or the backdrop — so the lightbox's button is never left disabled. A cancelled delete must **not** show an alert: in `SF.lightbox`'s catch, ignore an error whose message is `'cancelled'` (amend Task 7's code: `catch (err) { t.disabled = false; if (err.message !== 'cancelled') alert(err.message); }`) — and pin that with a test asserting `'cancelled'` appears in the components block.

- [ ] **Step 4: Run** — `t tests/test_admin_ui.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "The lightbox keeps and deletes, and keep moves on to the next screenshot"`

---

### Task 10: The Secrets tab

**Files:**
- Modify: `static/admin.html` (top tabs, a `#paneSecrets`, `loadSecrets`, `route`); `static/app.css`
- Test: `tests/test_admin_ui.py`

**Interfaces:**
- Consumes: `GET /admin/secrets` (Task 6); the `#/sessions/<key>/flows/<flow>` deep link (Task 8).

- [ ] **Step 1: Failing tests:**

```python
def test_secrets_sit_beside_sessions(page):
    tabs = page.split('<div class="tabs">')[1][:600]
    assert tabs.index('id="tabSessions"') < tabs.index('id="tabSecrets"') < tabs.index('id="tabConsole"')
    assert 'id="paneSecrets"' in page and "'#/secrets'" in page


def test_a_secret_card_links_to_the_flow_that_types_it(page):
    body = page.split("function renderSecrets(")[1][:3000]
    assert "'#/sessions/'" in body and "'/flows/'" in body
    assert "u.shared" in body, "a shared flow belongs to no one session and is not linked"


def test_the_secrets_page_never_renders_a_value(page):
    body = page.split("function renderSecrets(")[1][:3000]
    assert ".value" not in body


def test_a_name_no_secret_answers_to_is_shown(page):
    assert "Named by a flow, not defined" in page
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement.** Tabs: `<button id="tabSecrets" aria-selected="false">Secrets</button>` between Sessions and Grid console; `<section id="paneSecrets" hidden><div id="secrets"></div></section>`. `selectTab` becomes a three-way `selectTab(name)` (`'sessions' | 'secrets' | 'console'`) setting `aria-selected` on all three and showing one pane; update its two callers and `route()` (`secrets` → `selectTab('secrets'); loadSecrets();`). `loadSecrets()` fetches `/admin/secrets` and calls `renderSecrets(data)`:

```js
/* Read-only. A card per secret: what it is for, its keys, where it may be
   used, where it came from, and the stored flows that type it (§F4.10). Never
   a value — the server has none to send. Rendered here rather than in the
   shared library because only the admin page has secrets to show. */
function renderSecrets(data) {
  const box = $('secrets');
  if (!data.enabled) { box.innerHTML = '<div class="empty">No secrets are configured on this server.</div>'; return; }
  const uses = (list) => list.length
    ? list.map((u) => '<div class="use"><span class="pill name">' + SF.esc(u.flow) + '</span>'
        + '<span class="small muted">step' + (u.steps.length === 1 ? ' ' : 's ') + u.steps.join(', ') + '</span>'
        + '<span class="grow"></span>'
        + (u.shared ? '<span class="small muted">&#127760; shared</span>'
           : '<a href="' + SF.esc('#/sessions/' + encodeURIComponent(u.session) + '/flows/' + encodeURIComponent(u.flow)) + '">'
             + SF.esc(u.session) + ' &rarr;</a>') + '</div>').join('')
    : '<div class="small muted">No flow uses it.</div>';
  const card = (s, warn, reason) => '<div class="card secret">'
    + '<div class="row"><span>&#128273;</span><strong class="grow">' + SF.esc(s.name) + '</strong>'
    + (warn ? '<span class="pill warn">' + SF.esc(warn) + '</span>' : '') + '</div>'
    + (s.description ? '<div>' + SF.esc(s.description) + '</div>' : '')
    + (reason ? '<div class="small error">' + SF.esc(reason) + '</div>' : '')
    + (s.keys ? '<div class="fact"><span class="k">keys</span>' + s.keys.map((k) => '<span class="pill">' + SF.esc(k) + '</span>').join('') + '</div>' : '')
    + (s.keys ? '<div class="fact"><span class="k">allowed</span>' + SF.esc(s.restricted ? (s.allowed_urls || []).join(', ') || '—' : 'any site') + '</div>' : '')
    + (s.source ? '<div class="fact"><span class="k">from</span><span class="small muted">' + SF.esc(s.source + ' · ' + (s.location || '')) + '</span></div>' : '')
    + '<div class="used"><div class="k">USED BY</div>' + uses(s.uses || []) + '</div></div>';
  box.innerHTML =
    '<h2>Secrets</h2><p class="small muted">What flows can type without anyone seeing it. Read-only here: '
    + 'names, keys and where each may be used — never a value.</p>'
    + data.secrets.map((s) => card(s,
        s.allowed_urls_rejected ? 'unusable until fixed' : (s.restricted ? '' : 'any site'),
        s.allowed_urls_rejected ? 'allowed_urls: ' + [].concat(s.allowed_urls_rejected).join(', ') : '')).join('')
    + (data.undefined.length
        ? '<h3>Named by a flow, not defined</h3><p class="small muted">These flows will fail at the step that types the secret.</p>'
          + data.undefined.map((u) => card({name: u.name, uses: u.uses}, 'not defined',
              'A flow names this secret and the catalogue has no such entry, so the step will fail when it runs.')).join('')
        : '');
}
```

(Read one real catalogue entry — `secret://secrets` shows the fields: `name, keys, description, allowed_urls, restricted, source, location`, and `allowed_urls_rejected` when broken — and adjust the field names above if any differ.) CSS: `.card.secret` grid of facts (`.fact .k` 72px column), `.secret .use { display:flex; gap: var(--gap); align-items:center }`.

- [ ] **Step 4: Run** — `t tests/test_admin_ui.py` → PASS; whole suite `t`; `lint`.

- [ ] **Step 5: Commit** — `git commit -am "A Secrets tab: every secret, where it may be used, and the flows that type it"`

---

### Task 11: Docs, the skill, the changelog, the wiki, and the integration flows

**Files:**
- Modify: `skills/selenium-flow/SKILL.md` (Chrome-or-Firefox paragraph ~57-61, tool table `keep_file`/`upload_file`/`screenshot`, URI table); `skills/selenium-flow/references/SESSIONS.md` (~85) and any reference that says `kept=` (grep); `AGENTS.md` (Read first: Chapter 4; the files paragraph in "Everything to read is a resource"); `CHANGELOG.md` `[Unreleased]`; `wiki/notes/*.notes.md` that mention kept files; `README.md` if it describes files (grep)
- Modify: `tests/integration/flows/open-this-session.yaml`; Create: `tests/integration/flows/keep-a-screenshot.yaml`
- Regenerate: `python scripts/generate_wiki.py` (wiki pages), then `t tests/test_wiki.py tests/test_skill.py tests/test_readme.py`

- [ ] **Step 1: Grep for every stale sentence** — `grep -rn -e "keep_file(name" -e "kept=" -e "session://files lists both" -e "Clear downloads" -e "kept flag" -e "\"kept\"" skills AGENTS.md README.md wiki/notes CONTRIBUTING.md` — and fix each to the §F4.6/§F4.7 vocabulary (Files, screenshots, downloads; `keep_file(uri)`; `upload_file(file=uri)`). §F3.3's rule: hunt every stale description.

- [ ] **Step 2: SKILL.md.** The paragraph after "To switch…" becomes: *"**The files it had go with it** — the downloads, which the Grid deletes with the browser. Screenshots and prints are your session's already: `session://files` lists what is in Files and names two folders, `session://files/screenshots` and `session://files/downloads`. `keep_file(uri)` moves a screenshot into Files, or copies a download there before the browser goes."* Tool rows: `keep_file` → "put a screenshot or download in Files" / `uri`; `upload_file` → key arguments `text`, `content`, `file` or `path`; `screenshot` → "…kept in session://files/screenshots, with a link to share". URI rows: the six URIs from Global Constraints. Keep SKILL.md an index (its tests check linked references).

- [ ] **Step 3: CHANGELOG `[Unreleased]`**, one line each:

```markdown
### Added

- **The admin UI's session page has Files and Flows tabs**, and Files is three rows — Downloads, Screenshots and Files — each cleared the way that fits it.
- **The lightbox steps** with ‹ Prev / Next › and the arrow keys, and Keep in it moves on to the next screenshot.
- **A Secrets tab** lists every secret's keys and where it may be used — never a value — and the flows that type it, including names no secret answers to.

### Changed

- **BREAKING:** screenshots are kept in `session://files/screenshots` until kept or cleared; `session://files` lists Files and names the screenshots and downloads folders, and entries carry a `uri` instead of `kept`.
- **BREAKING:** `keep_file` takes a file's `uri` — a screenshot moves into Files, a download is copied — and `upload_file(kept=)` is `upload_file(file=uri)`, for any file.
- **BREAKING:** `PUT /files/{name}/kept` is `PUT /files/screenshots/{name}/kept` or `PUT /files/downloads/{name}/kept`, beside `GET /files/screenshots` and `GET /files/downloads`.
```

- [ ] **Step 4: Integration flows.** `open-this-session.yaml`: after clicking the session card, add `interact` click on `#tabFlows` before the `assert`. `keep-a-screenshot.yaml`:

```yaml
description: >-
  Take a screenshot of this very page, find it in this session's Screenshots
  row, keep it, and see it arrive in Files — the admin UI's whole files story,
  and the kept file Chapter 2 never exercised from the page.
parameters:
  type: object
  required: [admin, session]
  properties:
    admin: {type: string, description: The origin the browser reaches this server at}
    session: {type: string, description: The name the caller running this flow gave its session}
steps:
- tool: navigate
  args: {url: "${admin}/"}
- tool: write
  args:
    selector: {css: "#token"}
    secret: {name: admin, key: token}
- tool: interact
  args:
    action: click
    selector: {css: "#loginForm button[type=submit]"}
- tool: screenshot
  args: {filename: hangar-check}
- tool: navigate
  args: {url: "${admin}/#/sessions/${session}"}
- tool: assert
  args:
    script: >-
      return [...document.querySelectorAll('#screenshots .file .name')]
      .some((n) => n.textContent.startsWith('hangar-check'))
    message: The screenshot this flow just took is not in the Screenshots row.
- tool: interact
  args:
    action: click
    selector: {xpath: "//div[@id='screenshots']//div[contains(@class,'file')][.//div[starts-with(., 'hangar-check')]]//button[contains(@class,'keep')]"}
- tool: assert
  args:
    script: >-
      return [...document.querySelectorAll('#kept .file .name')]
      .some((n) => n.textContent.startsWith('hangar-check'))
    message: Keep was clicked and the screenshot did not arrive in Files.
    stable_for: 500
```

(Check `tests/integration/conftest.py` for how flows are loaded and which parameters it passes; keep the flow valid against `flow://schema` — `save_flow`'s validation runs in `tests/test_flowdoc.py`-style checks, so run `t -k flow` too. The keep button is visible only on hover; if the integration run shows `interact` refusing a zero-opacity button, add an `interact` `hover` on the tile first.)

- [ ] **Step 5: Regenerate and run** — `PYTHONNOUSERSITE=1 PYTHONPATH=$PWD:$SFENV python3 scripts/generate_wiki.py`; `t`; `lint`.

- [ ] **Step 6: Commit** — the repo commit, plus the `wiki` submodule commit **and push of the wiki submodule** before the PR (memory: an unpushed submodule SHA fails `Test` in 10s). `git commit -am "Docs, skill, changelog and wiki speak Files, screenshots and downloads; one new integration flow"`

---

### Task 12: Verify, tick the saga, and open the PR

**Files:**
- Modify: `saga/Chapter_4_The_Hangar.md` (status → BUILT; a Part III checklist ticked against the build)

- [ ] **Step 1: Full local gate** — `t` (expect ≥ baseline passing, 0 failed), `lint`, `PYTHONNOUSERSITE=1 PYTHONPATH=$PWD:$SFENV python3 scripts/generate_openapi.py` then `python3 -m openapi_spec_validator openapi.yaml`.
- [ ] **Step 2: See it working** — run the server locally against the real Grid (`GRID_URL` from the cluster's `apps/selenium/components/mcp/mcp.env`, `FLOW_DATA_DIR` in the scratchpad, `MCP_AUTH_TOKEN=dev`, bound on `0.0.0.0`), and drive `http://<pod-ip>:<port>/` from selenium-flow: take screenshots as that session, open the page, step the lightbox, keep one, clear the set, open Secrets. Screenshots `save=false`. `end_browser` afterwards.
- [ ] **Step 3: Saga** — status line, a Part III checklist of Tasks 1–11 ticked, and anything the build decided that the spec did not (from the SDD ledger's `Ruling:` lines).
- [ ] **Step 4: Commit, push the branch and the wiki submodule, open the PR** with `gh pr create` — title "The Hangar: Downloads, Screenshots and Files, tabs, a stepping lightbox, and Secrets"; body: what changed for a user, the breaking changes, the Penpot file and version, the saga chapter, the one-off move still to do after deploy (§F4.4), and the attribution line.
- [ ] **Step 5: After CI** — read and answer Copilot and code-scanning threads with `gh` (memory: they have found real bugs), resolve them, and report.
