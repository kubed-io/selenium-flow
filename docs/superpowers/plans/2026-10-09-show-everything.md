# show draws every resource — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Assumes E1 (#60) merged.** Every URI here is the renamed one (`workspace://…`,
`X-Workspace`); branch `issue-63-show` starts from `issue-60-workspaces`. If E1
is not on `main` yet, rebase onto it before Task 1.

**Goal:** `show(uri)` draws every URI form the server serves — one file, the
saved site data and one site, the skill's pages, `flow://schema` — and a test
fails the build when a resource or template is added without a view.

**Architecture:** `mcp/show.py`'s `VIEWS` becomes a table of `View` rows with
an `entry` flag: a single-file URI is drawn from its folder's listing entry,
read through the server, never from its bytes; a markdown resource's `data` is
its text. Four Svelte views (`file`, `sites`, `site`, `document`) join the shell,
which passes each view its `uri`, whether the host can go fullscreen, and an
`openLink` callback, and labels Back by the view it returns to. Markdown is
lexed by `marked` and drawn token by token in Svelte; nothing is ever an HTML
string.

**Tech Stack:** Python 3.10+, FastMCP 4, pytest; Svelte 5, Vite 8, vitest +
@testing-library/svelte, `@modelcontextprotocol/ext-apps` 2.0.3, `marked`
18.1.0 (new).

**Spec:** `docs/superpowers/specs/2026-10-09-show-everything-design.md` — read
*Rulings*, *Constraints* and *Design* first. Programme:
`docs/superpowers/specs/2026-10-09-workspaces-and-observability-design.md`, R17.
Penpot: file *Admin UI*, page *App · show*, boards `site-data`, `site`, `file`,
`document`, `context` (read only).

## Global Constraints

- `MAX_SHOWN` stays 100 000 characters, payload and line as sent.
- The URI-to-component table lives in `kubed/selenium_flow/mcp/show.py` and nowhere else.
- A single file is drawn from its listing entry, never its bytes; no resource is added or changed.
- `show` never calls `Workspaces.resolve`: drawing anything never opens a browser.
- The app stays within 110 KB gzipped (`npm --prefix ui run size`).
- `marked` is pinned at exactly `18.1.0` in `dependencies`, and only `Lexer` is imported from it.
- No `{@html}` and no `innerHTML` (the eslint config already errors on both).
- A link is clickable only when `safeHref` passes it or it is a concrete URI on `skill`, `flow`, `workspace` or `secret`.
- httpOnly cookie values arrive as `•••` from the server and are drawn as given.
- Python 3.10–3.14, `from __future__ import annotations`; `ruff check kubed` clean.
- UI: `npm --prefix ui run check`, `lint` and `test` clean.
- One CHANGELOG line, in `[Unreleased]` only.
- Penpot is read, never edited.

Working rules for this repo, also binding:

- Python tests run as `pytest <paths> -v` after `pip install -e ".[test]"`. In
  the agent pod: `PYTHONPATH=$PWD:<deps> python3 -m pytest <paths> -v`.
- UI: `npm --prefix ui ci` once, then `npm --prefix ui test`, `… run check`,
  `… run lint`, `… run build`, `… run size`.
- Real hosts never appear in tests or copy (`example.com`, user `drk`). UI copy
  is terse.
- One commit per task, ending with a blank line and
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

| File | Responsibility |
|---|---|
| Modify `kubed/selenium_flow/mcp/show.py` | `View` rows for all 16 forms; `row_for`; reading a whole resource (JSON or markdown text) or one file's listing entry; the description by scheme. |
| Create `tests/test_show_inventory.py` | The guard: every listed resource and template has a view; the inventory is the spec's; every component is a key of the shell's `VIEWS`. |
| Modify `tests/test_show.py` | The table's cases; single files; documents; the site pair; the description. |
| Modify `ui/package.json`, `ui/package-lock.json` | `marked` 18.1.0. |
| Modify `ui/src/lib/format.ts` (+ `format.test.ts`) | `readable`, `leaf`, `until`, `clock`, `duration`. |
| Create `ui/src/lib/sites.ts` (+ `sites.test.ts`) | `siteCounts`, shared by the admin pane and the app. |
| Modify `ui/src/admin/SiteDataPane.svelte` | Counts through `siteCounts`. |
| Modify `ui/src/lib/types.ts` | `FileEntry.uri`/`keep_with`, `SitesData`. |
| Modify `ui/src/app.css` | `.chips`/`.chip` (moved from `FilesView`), `.uri`, `.md`, `.tree`. |
| Create `ui/src/lib/links.ts` (+ test) | What a link or a code span in a document is. |
| Create `ui/src/lib/markdown.ts` (+ test) | Front matter off, lex, lift the title. |
| Create `ui/src/lib/Markdown.svelte`, `MarkdownInline.svelte` (+ `Markdown.test.ts`) | Block and inline tokens to elements. |
| Create `ui/src/lib/JsonTree.svelte` (+ test) | A JSON value as a collapsible tree. |
| Create `ui/src/lib/views/FileView.svelte`, `SitesView.svelte`, `SiteView.svelte`, `DocumentView.svelte` (+ tests) | The four new views. |
| Modify `ui/src/lib/views/FilesView.svelte`, `FolderView.svelte`, `ContextView.svelte` (+ tests) | Tiles drill into `file`; the Site data chip. |
| Modify `ui/src/App.svelte`, `ui/src/App.test.ts` | Register the views; `uri`, `expandable`, `onlink`; the Back label. |
| Docs | `skills/selenium-flow/SKILL.md`, `AGENTS.md`, `README.md`, `CHANGELOG.md`, `wiki/` prose. |

---

### Task 1: The table covers every URI form, and the guard holds it

**Files:**
- Modify: `kubed/selenium_flow/mcp/show.py`
- Create: `tests/test_show_inventory.py`
- Modify: `tests/test_show.py:47-91` (the table tests), `tests/test_show.py:173-177`

**Interfaces:**
- Produces: `show.View(form: str, pattern: re.Pattern[str], component: str, entry: bool = False)`; `show.VIEWS: tuple[View, ...]`; `show.row_for(uri: str) -> View`; `show.view_for(uri: str) -> str` (unchanged signature); `show.SHOWABLE: tuple[str, ...]`; components `file`, `sites`, `site`, `document`.

- [ ] **Step 1: Write the guard (failing)**

Create `tests/test_show_inventory.py`:

```python
"""Every URI this server serves has a view in show's table (programme R17).

A new resource or template without a row in `mcp/show.py` fails here, the way a
capability without both surfaces fails `test_surfaces.py`. If it fails, add the
row (and its view in the app) rather than editing the expectation.
"""

from __future__ import annotations

import re

import pytest
from fastmcp import Client

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import show
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit

# The app shell is the drawing, not a thing to draw (spec
# 2026-10-09-show-everything, ruling 8).
EXEMPT = ("ui://",)
SKILL = "skill://selenium-flow/"

# Research's table in the spec, with the skill's files folded into one.
FORMS = {
    "workspace://current",
    "workspace://site-data",
    "workspace://site-data/{site}",
    "workspace://files",
    "workspace://files/screenshots",
    "workspace://files/recordings",
    "workspace://files/downloads",
    "workspace://files/{name}",
    "workspace://files/screenshots/{name}",
    "workspace://files/recordings/{name}",
    "workspace://files/downloads/{name}",
    "flow://flows",
    "flow://flows/{name}",
    "flow://schema",
    "secret://secrets",
    SKILL + "*",
}


@pytest.fixture
def everything(tmp_path, built_ui):
    """Every section that registers a resource, turned on, and the UI built.

    Today a section that is off still lists its resource; a future one may list
    only when on, and the guard has to see it either way."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    secret = tmp_path / "secrets" / "demo"
    secret.mkdir(parents=True)
    (secret / "username").write_text("u")
    return SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path / "data")},
        secrets={"dirs": str(tmp_path / "secrets")},
        recording={"enabled": True, "dir": str(inbox)},
    ))


async def served(server) -> list[str]:
    """Every listed resource's URI and every template, as a client sees them."""
    async with Client(server.mcp) as c:
        resources = [str(r.uri) for r in await c.list_resources()]
        templates = [t.uriTemplate for t in await c.list_resource_templates()]
    return resources + templates


def concrete(uri: str) -> str:
    """A template made into one URI it would answer."""
    return re.sub(r"\{[^}]+\}", "sample", uri)


async def test_every_uri_the_server_serves_has_a_view(everything):
    missing = []
    for uri in await served(everything):
        if uri.startswith(EXEMPT):
            continue
        try:
            show.view_for(concrete(uri))
        except ValueError:
            missing.append(uri)
    assert missing == [], f"no row in mcp/show.py VIEWS for: {missing}"


async def test_the_shell_itself_is_served_and_exempt(everything):
    assert "ui://selenium-flow/component" in await served(everything)


async def test_the_inventory_is_the_one_the_spec_lists(everything):
    """A new resource fails here too: add it to the spec's table and to FORMS."""
    forms = {
        SKILL + "*" if uri.startswith(SKILL) else uri
        for uri in await served(everything)
        if not uri.startswith(EXEMPT)
    }
    assert forms == FORMS
```

- [ ] **Step 2: Run it to see it fail**

Run: `pytest tests/test_show_inventory.py -v`
Expected: `test_every_uri_the_server_serves_has_a_view` FAILS listing
`workspace://site-data`, `workspace://site-data/{site}`, the four single-file
templates, `flow://schema` and the ten `skill://` URIs; the other two pass.

- [ ] **Step 3: Update the table tests in `tests/test_show.py` (failing)**

Add `import re` to the imports. Replace `test_every_showable_uri_has_one_view`,
`test_every_row_matches_its_own_display_form` and
`test_anything_else_is_refused_naming_what_can_be_shown` with:

```python
@pytest.mark.parametrize(
    ("uri", "component"),
    [
        ("workspace://current", "context"),
        ("workspace://site-data", "sites"),
        ("workspace://site-data/app.example.com", "site"),
        ("workspace://files", "files"),
        ("workspace://files/screenshots", "folder"),
        ("workspace://files/recordings", "folder"),
        ("workspace://files/downloads", "folder"),
        ("workspace://files/screenshots/a.png", "file"),
        ("workspace://files/recordings/run.mp4", "file"),
        ("workspace://files/downloads/report.csv", "file"),
        ("workspace://files/report.pdf", "file"),
        ("flow://flows", "flows"),
        ("flow://flows/login", "flow"),
        ("flow://schema", "document"),
        ("secret://secrets", "secrets"),
        ("skill://selenium-flow/SKILL.md", "document"),
        ("skill://selenium-flow/_manifest", "document"),
        ("skill://selenium-flow/references/FLOWS.md", "document"),
    ],
)
def test_every_showable_uri_has_one_view(uri, component):
    assert show.view_for(uri) == component


@pytest.mark.parametrize(
    ("uri", "entry"),
    [
        ("workspace://files/screenshots/a.png", True),
        ("workspace://files/recordings/run.mp4", True),
        ("workspace://files/downloads/report.csv", True),
        ("workspace://files/report.pdf", True),
        ("workspace://files/screenshots", False),
        ("workspace://site-data/app.example.com", False),
        ("skill://selenium-flow/SKILL.md", False),
    ],
)
def test_only_a_single_file_is_drawn_from_its_entry(uri, entry):
    assert show.row_for(uri).entry is entry


def test_every_row_matches_its_own_display_form():
    """One table: each row's display form, made concrete, is drawn by that row's
    own component, not an earlier row's."""
    for view in show.VIEWS:
        concrete = re.sub(r"\{[^}]+\}", "x", view.form)
        assert show.row_for(concrete) is view


@pytest.mark.parametrize(
    "uri",
    ["flow://flows/", "workspace://current/x", "secret://secrets/demo",
     "skill://other/SKILL.md", "workspace://files/screenshots/a/b",
     "workspace://site-data/a/b"],
)
def test_anything_else_is_refused_naming_what_can_be_shown(uri):
    with pytest.raises(ValueError) as refused:
        show.view_for(uri)
    for showable in show.SHOWABLE:
        assert showable in str(refused.value)
```

And in `test_an_unshowable_uri_is_refused_through_the_client`, replace
`"flow://schema"` with `"secret://secrets/demo"` (the schema is showable now).

- [ ] **Step 4: Run them to see them fail**

Run: `pytest tests/test_show.py -v -k "view or row or refused"`
Expected: FAIL — `row_for` does not exist; the new URIs have no view.

- [ ] **Step 5: Rewrite the table in `kubed/selenium_flow/mcp/show.py`**

Add `from typing import NamedTuple` to the imports. Replace everything from
`TOOL = "show"` through the end of `view_for` with:

```python
TOOL = "show"

FILES = "workspace://files"
FOLDERS = ("screenshots", "recordings", "downloads")


class View(NamedTuple):
    """One URI form: how a URI is matched, what draws it, and whether it is
    drawn from its folder's listing entry rather than read whole (a single
    file: never its bytes, programme R17)."""

    form: str
    pattern: re.Pattern[str]
    component: str
    entry: bool = False


def _exactly(form: str, component: str) -> View:
    return View(form, re.compile(re.escape(form)), component)


# The only place a URI is tied to a view: what the tool names, what it matches,
# what draws it. First match wins, so a folder sits before the `{name}` row
# that would otherwise take it. Every URI the server serves has a row, and
# tests/test_show_inventory.py holds that.
VIEWS: tuple[View, ...] = (
    _exactly("workspace://current", "context"),
    _exactly("workspace://site-data", "sites"),
    View(
        "workspace://site-data/{site}",
        re.compile(r"workspace://site-data/[^/]+"),
        "site",
    ),
    _exactly(FILES, "files"),
    *(_exactly(f"{FILES}/{folder}", "folder") for folder in FOLDERS),
    *(
        View(
            f"{FILES}/{folder}/{{name}}",
            re.compile(rf"{FILES}/{folder}/[^/]+"),
            "file",
            entry=True,
        )
        for folder in FOLDERS
    ),
    View(f"{FILES}/{{name}}", re.compile(rf"{FILES}/[^/]+"), "file", entry=True),
    _exactly("flow://flows", "flows"),
    View("flow://flows/{name}", re.compile(r"flow://flows/[^/]+"), "flow"),
    _exactly("flow://schema", "document"),
    # One secret is a closer look inside the app, not a URI: there is no
    # single-secret resource to read (secrets.py, "one read").
    _exactly("secret://secrets", "secrets"),
    View(
        "skill://selenium-flow/{path}",
        re.compile(r"skill://selenium-flow/.+"),
        "document",
    ),
)
SHOWABLE = tuple(view.form for view in VIEWS)

# Claude drops a tool result over ~150k characters, and the app then never gets
# its data: a flow may be 1 MiB of YAML and a listing is unbounded.
MAX_SHOWN = 100_000
# What a listing's `count` counts, for the model's line.
NOUNS = {"files": "kept file", "folder": "file", "flows": "flow", "secrets": "secret"}


def row_for(uri: str) -> View:
    """The row that draws ``uri``, or a ValueError naming every form."""
    retired_uri(uri)
    for view in VIEWS:
        if view.pattern.fullmatch(uri):
            return view
    raise ValueError(f"{uri} has no view; show draws {', '.join(SHOWABLE)}")


def view_for(uri: str) -> str:
    return row_for(uri).component
```

(`summary` and `register` stay as they are in this task.)

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_show.py tests/test_show_inventory.py tests/test_names.py -v`
Expected: the table tests and the guard PASS. Tests in `test_show.py` that call
`show` on a new form are not written yet; every existing one still passes.

- [ ] **Step 7: Lint and commit**

```bash
ruff check kubed tests/test_show.py tests/test_show_inventory.py
git add kubed/selenium_flow/mcp/show.py tests/test_show.py tests/test_show_inventory.py
git commit -m "show: a row for every URI form the server serves, and the guard that holds it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: show reads one file from its entry and a page as its text

**Files:**
- Modify: `kubed/selenium_flow/mcp/show.py` (`register` and two module helpers)
- Modify: `tests/test_show.py`

**Interfaces:**
- Consumes: `show.row_for`, `View.entry` (Task 1).
- Produces: `show` results — `file`: `data` is the listing entry (`name`, `uri`, `size`, `created`, `content_type`, `image`, `url`, and `keep_with` outside Files); `document`: `data` is a `str` (markdown) or the JSON; `sites`/`site`: the resources' JSON.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_show.py` (add `from kubed.selenium_flow.workspace.store import Workspace` to the imports):

```python
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def never(*args, **kwargs):
    raise AssertionError("show read bytes, or opened a browser, to draw an entry")


@pytest.mark.parametrize(
    ("folder", "name", "uri"),
    [
        ("screenshots", "shot 1.png", "workspace://files/screenshots/shot%201.png"),
        ("recordings", "run.mp4", "workspace://files/recordings/run.mp4"),
        (None, "report.pdf", "workspace://files/report.pdf"),
    ],
)
async def test_one_file_is_drawn_from_its_listing_entry(
    flow_server, named_caller, monkeypatch, folder, name, uri
):
    if folder:
        flow_server.flows.write_file(named_caller, name, PNG, folder)
    else:
        flow_server.flows.write_file(named_caller, name, PNG)
    listing = uri.rpartition("/")[0]
    monkeypatch.setattr(flow_server.flows, "read_file", never)
    monkeypatch.setattr(flow_server.workspaces, "resolve", never)
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool("show", {"uri": uri})).structured_content
        listed = json.loads((await c.read_resource(listing))[0].text)
    entry = next(f for f in listed["files"] if f["name"] == name)

    def unsigned(e):
        return {k: v for k, v in e.items() if k != "url"}

    assert shown["component"] == "file"
    assert shown["uri"] == uri
    assert unsigned(shown["data"]) == unsigned(entry)
    assert shown["data"]["url"]


async def test_a_download_is_drawn_from_the_grids_listing(flow_server, monkeypatch):
    monkeypatch.setattr(flow_server.workspaces, "browser", lambda name: "abc")
    monkeypatch.setattr(flow_server.workspaces, "resolve", never)
    monkeypatch.setattr(
        flow_server.actions.grid,
        "files",
        lambda session_id: [{"name": "report.csv", "size": 3, "creationTime": 1}],
    )
    monkeypatch.setattr(flow_server.actions.grid, "read_file", never)
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool(
            "show", {"uri": "workspace://files/downloads/report.csv"}
        )).structured_content
    assert shown["component"] == "file"
    assert shown["data"]["name"] == "report.csv"
    assert shown["data"]["keep_with"].startswith("keep_file(")


async def test_a_file_the_listing_lacks_is_refused_naming_the_listing(flow_server):
    async with Client(flow_server.mcp) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "workspace://files/screenshots/gone.png"})
    assert (
        "no file at workspace://files/screenshots/gone.png. "
        "workspace://files/screenshots lists what there is"
    ) in str(refused.value)


async def test_a_skill_page_is_drawn_from_its_text(flow_server):
    uri = "skill://selenium-flow/references/TROUBLESHOOTING.md"
    async with Client(flow_server.mcp) as c:
        result = await c.call_tool("show", {"uri": uri})
        read = (await c.read_resource(uri))[0].text
    assert result.structured_content == {"component": "document", "uri": uri, "data": read}
    assert read.startswith("# When something goes wrong")
    assert [b.text for b in result.content] == [
        f"Showing {uri} to the person (document)."
    ]


@pytest.mark.parametrize("uri", ["flow://schema", "skill://selenium-flow/_manifest"])
async def test_a_json_document_is_drawn_from_its_json(flow_server, uri):
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool("show", {"uri": uri})).structured_content
        read = json.loads((await c.read_resource(uri))[0].text)
    assert shown == {"component": "document", "uri": uri, "data": read}


async def test_the_site_data_pair_draws_with_httponly_masked(flow_server, named_caller):
    site = "app.example.com"
    flow_server.workspaces.store.set(named_caller, Workspace(site_data={
        "cookies": [
            {"name": "sid", "value": "s3cret", "domain": site, "http_only": True},
            {"name": "theme", "value": "dark", "domain": site},
        ],
        "origins": {f"https://{site}": {"local": {"k": "v"}}},
        "session": {"origin": f"https://{site}", "items": {}},
        "saved_at": 1000.0,
    }).visited(f"https://{site}/x"))
    async with Client(flow_server.mcp) as c:
        listing = (await c.call_tool("show", {"uri": "workspace://site-data"})).structured_content
        one = (await c.call_tool(
            "show", {"uri": f"workspace://site-data/{site}"}
        )).structured_content
    assert listing["component"] == "sites"
    assert [r["uri"] for r in listing["data"]["sites"]] == [f"workspace://site-data/{site}"]
    assert one["component"] == "site"
    sent = json.dumps(one, ensure_ascii=False)
    assert "\u2022\u2022\u2022" in sent
    assert "s3cret" not in sent


async def test_the_description_names_the_schemes(built_ui, server):
    with patch.object(apps, "supported", return_value=True):
        tool = next(t for t in await server.mcp.list_tools() if t.name == "show")
    for scheme in ("workspace://", "flow://", "secret://", "skill://"):
        assert scheme in tool.description
    assert "read the resource instead" in tool.description
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_show.py -v -k "entry or download or lacks or skill_page or json_document or site_data_pair or schemes"`
Expected: FAIL — a single file's bytes are not JSON ("is not a resource show can
draw"); a skill page is refused the same way; the description lists forms.

- [ ] **Step 3: Implement the reading in `kubed/selenium_flow/mcp/show.py`**

Add `from urllib.parse import unquote` to the imports. Below `summary`, add:

```python
# The one text type `show` hands a view as text: a document is drawn from it.
MARKDOWN = "text/markdown"

DESCRIPTION = (
    "Draw any resource this server serves - a workspace://, flow://, secret:// "
    "or skill:// URI, as its resources and resource templates list them - for "
    "the person to see. For you to read one, read the resource instead. The "
    "view the person is looking at is shared with you as context."
)


async def _content(uri: str):
    """A resource as a view takes it: markdown as its text, anything else as
    its JSON."""
    try:
        result = await get_context().fastmcp.read_resource(uri)
    except NotFoundError:
        raise ValueError(f"no resource at {uri!r}") from None
    item = result.contents[0]
    content = item.content
    kind = getattr(item, "mime_type", None) or ""
    if isinstance(content, str) and kind.startswith(MARKDOWN):
        return content
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        raise ValueError(f"{uri} is not a resource show can draw") from None


async def _entry(uri: str) -> dict:
    """One file, as its folder's listing describes it: never its bytes. The
    listing never opens a browser, and a download's costs the Grid call the
    Downloads view already makes (spec 2026-10-09-show-everything, ruling 1)."""
    listing_uri, _, leaf = uri.rpartition("/")
    listing = await _content(listing_uri)
    name = unquote(leaf)
    files = listing.get("files", []) if isinstance(listing, dict) else []
    found = next(
        (f for f in files if isinstance(f, dict) and f.get("name") == name), None
    )
    if found is None:
        raise ValueError(f"no file at {uri}. {listing_uri} lists what there is")
    return found
```

In `register`, replace the `description=(…)` argument with
`description=DESCRIPTION,` and replace the body of `show` up to and including
the `json.loads` block with:

```python
    async def show(uri: str) -> ToolResult:
        view = row_for(uri)
        component = view.component
        data = await (_entry(uri) if view.entry else _content(uri))
```

The rest of the body (payload, `summary`, the `MAX_SHOWN` check, the
`ToolResult`) is unchanged.

- [ ] **Step 4: Run the whole show suite**

Run: `pytest tests/test_show.py tests/test_show_inventory.py tests/test_names.py -v`
Expected: PASS, including the existing
`test_content_that_is_not_json_is_refused_cleanly` (its fake item has no
`mime_type`, so it takes the JSON path and is refused).

- [ ] **Step 5: Lint and commit**

```bash
ruff check kubed tests/test_show.py
git add kubed/selenium_flow/mcp/show.py tests/test_show.py
git commit -m "show: one file from its listing entry, a skill page as its text

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The shared helpers — formatting, site counts, types, chip styles

**Files:**
- Modify: `ui/src/lib/format.ts`, `ui/src/lib/format.test.ts`
- Create: `ui/src/lib/sites.ts`, `ui/src/lib/sites.test.ts`
- Modify: `ui/src/admin/SiteDataPane.svelte`
- Modify: `ui/src/lib/types.ts`
- Modify: `ui/src/app.css`, `ui/src/lib/views/FilesView.svelte` (style block)

**Interfaces:**
- Produces: `readable(uri: string): string`; `leaf(uri: string): string`; `until(expiry: number, now?: number): string`; `clock(ms: number, now?: number): string`; `duration(seconds: number): string` (all in `lib/format.ts`); `siteCounts(r: SiteRow): string` (`lib/sites.ts`); types `FileEntry.uri?: string`, `FileEntry.keep_with?: string`, `SitesData`; global CSS classes `.chips`, `.chip`, `.uri`.

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/lib/format.test.ts` (merge the import line with the existing one):

```ts
import { clock, duration, leaf, readable, until } from './format'

test('readable undoes escapes and survives a bad one', () => {
  expect(readable('workspace://files/recordings/run%201.mp4')).toBe('workspace://files/recordings/run 1.mp4')
  expect(readable('workspace://files/%E0%A4%A')).toBe('workspace://files/%E0%A4%A')
})

test('leaf is the last segment, readable', () => {
  expect(leaf('skill://selenium-flow/references/FLOWS.md')).toBe('FLOWS.md')
  expect(leaf('workspace://files/a%20b.png')).toBe('a b.png')
  expect(leaf('flow://schema')).toBe('schema')
})

test('until counts down to an expiry in seconds', () => {
  const now = 1_000_000_000_000
  const s = now / 1000
  expect(until(s + 45 * 60, now)).toBe('45 m')
  expect(until(s + 10, now)).toBe('1 m')
  expect(until(s + 5 * 3600, now)).toBe('5 h')
  expect(until(s + 3 * 86400, now)).toBe('3 d')
  expect(until(s + 400 * 86400, now)).toBe('1 y')
})

test('clock is a time today and a date otherwise', () => {
  const now = new Date(2026, 9, 9, 21, 12).getTime()
  expect(clock(now - 60_000, now)).toMatch(/\d:\d\d/)
  const before = new Date(2026, 9, 1, 9, 0).getTime()
  expect(clock(before, now)).toBe(new Date(before).toLocaleDateString())
})

test('duration is minutes and seconds; nothing for an unknown length', () => {
  expect(duration(252)).toBe('4:12')
  expect(duration(9)).toBe('0:09')
  expect(duration(Infinity)).toBe('')
  expect(duration(NaN)).toBe('')
})
```

Create `ui/src/lib/sites.test.ts`:

```ts
import { expect, test } from 'vitest'
import { siteCounts } from './sites'

const row = (cookies: number, storage: [number, number][] = []) => ({
  site: 'app.example.com',
  cookies,
  storage: storage.map(([l, s], i) => ({ origin: 'https://app.example.com:' + i, local_storage: l, session_storage: s })),
})

test('a cookie-only host reads its cookies', () => {
  expect(siteCounts(row(3))).toBe('3 cookies')
  expect(siteCounts(row(1))).toBe('1 cookie')
})

test('a host with storage gives every count, summed over its origins', () => {
  expect(siteCounts(row(2, [[2, 0]]))).toBe('2 cookies · 2 local · 0 session')
  expect(siteCounts(row(0, [[1, 1], [2, 0]]))).toBe('0 cookies · 3 local · 1 session')
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `npm --prefix ui test -- src/lib/format.test.ts src/lib/sites.test.ts`
Expected: FAIL — the functions and the module do not exist.

- [ ] **Step 3: Implement**

Append to `ui/src/lib/format.ts`:

```ts
/* A URI with its escapes undone: for showing a person, never for calling. */
export function readable(uri: string): string {
  try {
    return decodeURIComponent(uri)
  } catch {
    return uri
  }
}

/* A URI's last segment, as a person reads it. */
export const leaf = (uri: string): string => readable(uri.split('/').pop() || uri)

/* Time left until an expiry in epoch seconds: "45 m", "5 h", "3 d", "1 y". */
export function until(expiry: number, now = Date.now()): string {
  const s = Math.max(0, expiry - now / 1000)
  if (s < 3600) return Math.max(1, Math.round(s / 60)) + ' m'
  if (s < 86400) return Math.round(s / 3600) + ' h'
  if (s < 365 * 86400) return Math.round(s / 86400) + ' d'
  return Math.round(s / (365 * 86400)) + ' y'
}

/* A moment as a clock time when it is today, else as a date. */
export function clock(ms: number, now = Date.now()): string {
  const d = new Date(ms)
  return d.toDateString() === new Date(now).toDateString()
    ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleDateString()
}

/* A media length in seconds as m:ss; nothing while it is unknown. */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds)) return ''
  const s = Math.round(seconds)
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0')
}
```

Create `ui/src/lib/sites.ts`:

```ts
import { plural } from './format'
import type { SiteRow } from './types'

const sum = (r: SiteRow, k: 'local_storage' | 'session_storage') => r.storage.reduce((n, e) => n + e[k], 0)

/* One rule for every surface that lists saved sites: a cookie-only host reads
   "3 cookies"; one with storage gives every count. */
export const siteCounts = (r: SiteRow): string =>
  [
    plural(r.cookies, 'cookie'),
    ...(r.storage.length ? [sum(r, 'local_storage') + ' local', sum(r, 'session_storage') + ' session'] : []),
  ].join(' · ')
```

In `ui/src/admin/SiteDataPane.svelte`: add `import { siteCounts } from '../lib/sites'`;
delete the `sum` and `counts` constants (and the comment above `counts`); in the
markup replace `{counts(r)}` with `{siteCounts(r)}`. Keep the pane's own
`plural`, which `clearBody` still uses.

In `ui/src/lib/types.ts`, add to `FileEntry`:

```ts
  /** The file's own address: what `show` draws and `keep_file` takes. */
  uri?: string
  /** Outside Files: the call that keeps it past the browser. */
  keep_with?: string
```

and after `SiteDataPayload`:

```ts
/** `workspace://site-data`, as `show` hands it to the app. */
export interface SitesData { sites: SiteRow[]; saved_at: number | null; uri?: string }
```

Move the `.chips`, `.chip`, `button.chip`, `button.chip:hover` and
`button.chip:focus-visible` rules from `FilesView.svelte`'s `<style>` (delete
that block) to the end of `ui/src/app.css`, and add beside them:

```css
/* A resource's URI beside a view's title, as the boards draw it. */
.uri { font-family: var(--mono, ui-monospace, monospace); overflow-wrap: anywhere; }
```

- [ ] **Step 4: Run the UI suite**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint`
Expected: PASS, `SiteDataPane.test.ts` and `FilesView.test.ts` unchanged and
green.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/format.ts ui/src/lib/format.test.ts ui/src/lib/sites.ts ui/src/lib/sites.test.ts \
  ui/src/admin/SiteDataPane.svelte ui/src/lib/types.ts ui/src/app.css ui/src/lib/views/FilesView.svelte
git commit -m "ui: the helpers the new show views share with the admin

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The shell hands views their URI, fullscreen and openLink, and Back says where

**Files:**
- Modify: `ui/src/App.svelte`
- Modify: `ui/src/App.test.ts`

**Interfaces:**
- Consumes: `leaf` (Task 3).
- Produces: every view receives `data`, `uri?: string`, `onshow?: (uri: string) => void`, `onlink?: (url: string) => void`, `expanded?: boolean`, `expandable?: boolean`. `onlink` is defined only when the host's capabilities include `openLinks`. `EXPANDS` contains `document`. The Back button keeps `aria-label="Back"` and reads `← <label>`.

- [ ] **Step 1: Write the failing tests**

In `ui/src/App.test.ts`, add `openLink: vi.fn(),` to the hoisted `host`
object, `openLink(p: unknown) { return host.openLink(p) }` to the mocked `App`
class, and `host.openLink` to the `beforeEach` reset list. Then append:

```ts
test('Back names the view it returns to', async () => {
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: FLOW })
  render(App)
  await shown(FLOWS)
  await fireEvent.click(await screen.findByRole('button', { name: 'login' }))
  const back = await screen.findByRole('button', { name: 'Back' })
  expect(back).toHaveTextContent('← Flows')
})

test('Back under the files root says Files', async () => {
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: {
    component: 'folder', uri: 'workspace://files/screenshots',
    data: { workspace: 's', folder: 'screenshots', uri: 'workspace://files/screenshots', count: 0, files: [] },
  } })
  render(App)
  await shown({ component: 'files', uri: 'workspace://files', data: { workspace: 's', count: 0, files: [], folders: [{ name: 'screenshots', uri: 'workspace://files/screenshots', count: 0 }] } })
  await fireEvent.click(await screen.findByRole('button', { name: /Screenshots/ }))
  expect(await screen.findByRole('button', { name: 'Back' })).toHaveTextContent('← Files')
})
```

Views are given `uri`, `expandable` and `onlink`; these are asserted through the
views that use them in Tasks 5 and 8.

- [ ] **Step 2: Run them to see them fail**

Run: `npm --prefix ui test -- src/App.test.ts`
Expected: FAIL — Back reads `← Back`, not `← Flows` / `← Files`.

- [ ] **Step 3: Implement in `ui/src/App.svelte`**

Import `leaf` beside `plural`: `import { leaf, plural } from './lib/format'`.
Replace the `Props` type and `EXPANDS`:

```ts
  type Props = {
    data: never
    uri?: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
    expanded?: boolean
    expandable?: boolean
  }
```

```ts
  const EXPANDS = new Set(['flow', 'document'])
  // What Back says: the view it returns to (spec 2026-10-09-show-everything, ruling 12).
  const LABELS: Record<string, string> = { context: 'Workspace', files: 'Files', flows: 'Flows', sites: 'Site data', secrets: 'Secrets' }
```

Add state and derived values beside `onshow`:

```ts
  let onlink = $state.raw<((url: string) => void) | undefined>(undefined)
```

```ts
  const below = $derived(stack.at(-2))
  const expandable = $derived(modes.includes('fullscreen'))
```

Add the label function after `summary`:

```ts
  function label(s: Shown | undefined): string {
    const c = String(s?.component)
    const folder = (s?.data as { folder?: unknown } | undefined)?.folder
    if (c === 'folder' && typeof folder === 'string') return folder.charAt(0).toUpperCase() + folder.slice(1)
    if (c === 'document' && s?.uri) return leaf(s.uri)
    return Object.hasOwn(LABELS, c) ? LABELS[c] : 'Back'
  }
```

In `onMount`, after `onshow = …`:

```ts
        onlink = h.getHostCapabilities()?.openLinks
          ? (url: string) => { h.openLink({ url }).catch(() => {}) }
          : undefined
```

In the markup, the Back button becomes:

```svelte
        <button type="button" aria-label="Back" onclick={() => settle(stack.slice(0, -1))}>← {label(below)}</button>
```

and the view line:

```svelte
      {#key top}<View data={top.data as never} uri={top.uri} {onshow} {onlink} {expanded} {expandable} />{/key}
```

- [ ] **Step 4: Run the UI suite**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint`
Expected: PASS. Existing tests that find Back by its role name still pass, since
its accessible name stays *Back*.

- [ ] **Step 5: Commit**

```bash
git add ui/src/App.svelte ui/src/App.test.ts
git commit -m "app shell: views get their URI, fullscreen and openLink; Back names where it goes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The file view, and tiles that drill into it

**Files:**
- Create: `ui/src/lib/views/FileView.svelte`, `ui/src/lib/views/FileView.test.ts`
- Modify: `ui/src/lib/views/FolderView.svelte`, `FolderView.test.ts`, `FilesView.svelte`, `FilesView.test.ts`
- Modify: `ui/src/App.svelte`, `ui/src/App.test.ts`

**Interfaces:**
- Consumes: `bytes`, `clock`, `duration`, `glyphFor`, `readable` (format.ts); `FileEntry` with `uri`; the shell's `onlink` (Task 4).
- Produces: component `file` registered in the shell's `VIEWS`.

- [ ] **Step 1: Write the failing tests**

Create `ui/src/lib/views/FileView.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import FileView from './FileView.svelte'

const entry = (name: string, content_type: string, folder = 'recordings', extra = {}) => ({
  name, size: 18_400_000, created: Date.now() - 60_000, content_type,
  image: content_type.startsWith('image/'),
  uri: `workspace://files/${folder}/${encodeURIComponent(name)}`,
  url: 'https://flow.example.com/f/' + encodeURIComponent(name), ...extra,
})

test('a recording: name, readable URI, REC, a player, facts and Open', () => {
  const data = entry('run 1.mp4', 'video/mp4')
  const { container } = render(FileView, { props: { data, uri: data.uri } })
  expect(screen.getByText('run 1.mp4')).toBeInTheDocument()
  expect(screen.getByText('workspace://files/recordings/run 1.mp4')).toBeInTheDocument()
  expect(screen.getByText('● REC')).toHaveClass('pill')
  expect(container.querySelector('video')).toHaveAttribute('src', data.url)
  expect(screen.getByText(/17\.5 MB/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open' })).toHaveAttribute('href', data.url)
})

test('the player reports its length once it knows it', async () => {
  const data = entry('run.mp4', 'video/mp4')
  const { container } = render(FileView, { props: { data, uri: data.uri } })
  const video = container.querySelector('video')!
  Object.defineProperty(video, 'duration', { value: 252 })
  await fireEvent(video, new Event('loadedmetadata'))
  expect(screen.getByText(/^4:12 · /)).toBeInTheDocument()
})

test('a screenshot is an image from its signed link, without REC', () => {
  const data = entry('a.png', 'image/png', 'screenshots')
  render(FileView, { props: { data, uri: data.uri } })
  expect(screen.getByRole('img', { name: 'a.png' })).toHaveAttribute('src', data.url)
  expect(screen.queryByText('● REC')).toBeNull()
})

test('a download is a card with its glyph and its type', () => {
  const data = entry('report.csv', 'text/csv', 'downloads')
  render(FileView, { props: { data, uri: data.uri } })
  expect(screen.getByText('📊')).toBeInTheDocument()
  expect(screen.getByText(/text\/csv/)).toBeInTheDocument()
})

test('a link that will not load says why, in place of the preview', async () => {
  const data = entry('a.png', 'image/png', 'screenshots')
  render(FileView, { props: { data, uri: data.uri } })
  await fireEvent.error(screen.getByRole('img', { name: 'a.png' }))
  expect(screen.getByText(/This link has expired, or this server's address is not reachable from here/)).toBeInTheDocument()
  expect(screen.queryByRole('img')).toBeNull()
})

test('Open goes through the host when it can open links', async () => {
  const onlink = vi.fn()
  const data = entry('report.csv', 'text/csv', 'downloads')
  render(FileView, { props: { data, uri: data.uri, onlink } })
  await fireEvent.click(screen.getByRole('link', { name: 'Open' }))
  expect(onlink).toHaveBeenCalledWith(data.url)
})
```

Append to `ui/src/lib/views/FolderView.test.ts`:

```ts
test('with onshow a tile drills into its file instead of the lightbox', async () => {
  const onshow = vi.fn()
  const files = [{ ...f('a.png'), uri: 'workspace://files/screenshots/a.png' }]
  const { container } = render(FolderView, { props: { data: { workspace: 's', folder: 'screenshots', uri: 'u', count: 1, files }, onshow } })
  await fireEvent.click(container.querySelector('a.thumb')!)
  expect(onshow).toHaveBeenCalledWith('workspace://files/screenshots/a.png')
  expect(container.ownerDocument.querySelector('.lightbox')).toBeNull()
})
```

(add `vi` to that file's `vitest` import). Append to `FilesView.test.ts`:

```ts
test('with onshow a kept file drills into its file view', async () => {
  const onshow = vi.fn()
  const files = [{ ...data.files[0], uri: 'workspace://files/k.pdf' }]
  const { container } = render(FilesView, { props: { data: { ...data, files }, onshow } })
  await fireEvent.click(container.querySelector('a.thumb')!)
  expect(onshow).toHaveBeenCalledWith('workspace://files/k.pdf')
})
```

Append to `App.test.ts`, inside the `test.each` table of views drawn from a show result, the row:

```ts
  ['file', { name: 'a.png', size: 10, url: 'https://flow.example.com/f/a.png', image: true, content_type: 'image/png', uri: 'workspace://files/screenshots/a.png' }, 'a.png'],
```

and, after it:

```ts
test('the file view opens its link through the host when the host offers it', async () => {
  host.caps = { serverTools: {}, openLinks: {} }
  host.openLink.mockResolvedValue({})
  render(App)
  await shown({ component: 'file', uri: 'workspace://files/x.csv', data: { name: 'x.csv', size: 1, url: 'https://flow.example.com/f/x.csv', content_type: 'text/csv' } })
  await fireEvent.click(await screen.findByRole('link', { name: 'Open' }))
  expect(host.openLink).toHaveBeenCalledWith({ url: 'https://flow.example.com/f/x.csv' })
})

test('Back under a folder names the folder', async () => {
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: {
    component: 'file', uri: 'workspace://files/screenshots/a.png',
    data: { name: 'a.png', size: 10, url: 'https://flow.example.com/f/a.png', image: true, content_type: 'image/png', uri: 'workspace://files/screenshots/a.png' },
  } })
  const { container } = render(App)
  await shown({ component: 'folder', uri: 'workspace://files/screenshots', data: {
    workspace: 's', folder: 'screenshots', uri: 'workspace://files/screenshots', count: 1,
    files: [{ name: 'a.png', size: 10, url: 'https://flow.example.com/f/a.png', image: true, uri: 'workspace://files/screenshots/a.png' }],
  } })
  await vi.waitFor(() => expect(container.querySelector('a.thumb')).not.toBeNull())
  await fireEvent.click(container.querySelector('a.thumb')!)
  expect(host.callServerTool).toHaveBeenCalledWith({ name: 'show', arguments: { uri: 'workspace://files/screenshots/a.png' } })
  expect(await screen.findByRole('button', { name: 'Back' })).toHaveTextContent('← Screenshots')
})

test('without openLinks, Open is a plain link', async () => {
  render(App)
  await shown({ component: 'file', uri: 'workspace://files/x.csv', data: { name: 'x.csv', size: 1, url: 'https://flow.example.com/f/x.csv', content_type: 'text/csv' } })
  const open = await screen.findByRole('link', { name: 'Open' })
  expect(open).toHaveAttribute('target', '_blank')
  await fireEvent.click(open)
  expect(host.openLink).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `npm --prefix ui test -- src/lib/views src/App.test.ts`
Expected: FAIL — `FileView.svelte` does not exist; tiles open the lightbox; `file` has no view.

- [ ] **Step 3: Implement**

Create `ui/src/lib/views/FileView.svelte`:

```svelte
<script lang="ts">
  import { bytes, clock, duration, glyphFor, readable } from '../format'
  import type { FileEntry } from '../types'

  let { data, uri = '', onlink }: {
    data: FileEntry
    uri?: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
  } = $props()

  const kind = $derived(data.content_type ?? '')
  const video = $derived(kind.startsWith('video/'))
  const pdf = $derived(kind === 'application/pdf')
  const address = $derived(data.uri ?? uri)
  const recording = $derived(address.startsWith('workspace://files/recordings/'))
  // A signed link that has expired, or a host that cannot reach this server.
  let broken = $state(false)
  let length = $state<number | null>(null)
  const facts = $derived(
    [
      length === null ? '' : duration(length),
      bytes(data.size),
      data.created ? clock(data.created) : '',
      data.image || video ? '' : kind,
    ].filter(Boolean).join(' · '),
  )

  function open(e: MouseEvent) {
    // Without the host's openLinks the plain link is the way out.
    if (!onlink) return
    e.preventDefault()
    onlink(data.url)
  }
</script>

<div class="card file">
  <div class="row head">
    <strong class="name">{data.name}</strong>
    <code class="uri small muted grow">{readable(address)}</code>
    {#if recording}<span class="pill rec">● REC</span>{/if}
  </div>
  <div class="preview">
    {#if broken}
      <p class="small error">This link has expired, or this server's address is not reachable from here (PUBLIC_BASE_URL).</p>
    {:else if data.image}
      <img alt={data.name} src={data.url} onerror={() => (broken = true)}>
    {:else if video}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video controls preload="metadata" src={data.url}
             onerror={() => (broken = true)}
             onloadedmetadata={(e) => (length = (e.currentTarget as HTMLVideoElement).duration)}></video>
    {:else if pdf}
      <iframe title={data.name} src={data.url}></iframe>
    {:else}
      <span class="glyph">{glyphFor(data.name)}</span>
    {/if}
  </div>
  <div class="row foot">
    <span class="small muted grow">{facts}</span>
    <a class="btn" href={data.url} target="_blank" rel="noopener" onclick={open}>Open</a>
  </div>
</div>

<style>
  .head { margin-bottom: 10px; flex-wrap: wrap; }
  .name { overflow-wrap: anywhere; }
  .preview { border-radius: var(--radius); overflow: hidden; background: color-mix(in srgb, var(--ink) 6%, transparent); }
  .preview img { display: block; width: 100%; max-height: 480px; object-fit: contain; }
  .preview video { display: block; width: 100%; aspect-ratio: 16 / 9; background: #000; }
  .preview iframe { display: block; width: 100%; height: 420px; border: 0; }
  .preview .glyph { display: grid; place-items: center; height: 120px; font-size: 48px; }
  .preview .error { padding: 16px; margin: 0; }
  .foot { margin-top: 10px; }
</style>
```

In `FolderView.svelte`, take `onshow` from the props and pass a drill to the grid:

```svelte
<script lang="ts">
  import FileGrid from '../FileGrid.svelte'
  import type { FolderData } from '../types'

  let { data, onshow }: { data: FolderData; onshow?: (uri: string) => void } = $props()
  const title = $derived(data.folder.charAt(0).toUpperCase() + data.folder.slice(1))
  // With the host's tools a tile opens its own view; without, the lightbox.
  const drill = $derived(onshow ? (i: number) => { const u = data.files[i]?.uri; if (u) onshow(u) } : undefined)
</script>

<section class="section">
  <div class="head"><strong>{title}</strong><span class="pill">{data.count}</span></div>
  <div class="body"><FileGrid files={data.files} layout="row" empty="Nothing here yet." onopen={drill} /></div>
</section>
```

In `FilesView.svelte`, add the same `drill` derived after `UNAVAILABLE` and pass
`onopen={drill}` to its `FileGrid`.

In `App.svelte`, `import FileView from './lib/views/FileView.svelte'` and add
`file: FileView as Component<Props>,` to `VIEWS`.

- [ ] **Step 4: Run the UI suite**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/views/FileView.svelte ui/src/lib/views/FileView.test.ts ui/src/lib/views/FolderView.svelte \
  ui/src/lib/views/FolderView.test.ts ui/src/lib/views/FilesView.svelte ui/src/lib/views/FilesView.test.ts \
  ui/src/App.svelte ui/src/App.test.ts
git commit -m "show app: one file as a picture, a player or a card with Open; tiles drill into it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Site data and one site, and the context chip that opens them

**Files:**
- Create: `ui/src/lib/views/SitesView.svelte`, `SitesView.test.ts`, `SiteView.svelte`, `SiteView.test.ts`
- Modify: `ui/src/lib/views/ContextView.svelte`, `ContextView.test.ts`
- Modify: `ui/src/App.svelte`, `ui/src/App.test.ts`

**Interfaces:**
- Consumes: `siteCounts` (Task 3), `clock`, `until`, `SitesData`, `SiteDetail`, `SiteCookie`.
- Produces: components `sites`, `site` registered in the shell's `VIEWS`; the context view's *Site data* chip drilling to `site_data.uri`.

- [ ] **Step 1: Write the failing tests**

Create `ui/src/lib/views/SitesView.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import SitesView from './SitesView.svelte'

const data = {
  uri: 'workspace://site-data', saved_at: Date.now() / 1000,
  sites: [
    { site: 'app.example.com', uri: 'workspace://site-data/app.example.com', cookies: 2, storage: [{ origin: 'https://app.example.com', local_storage: 2, session_storage: 0 }] },
    { site: 'docs.example.com', uri: 'workspace://site-data/docs.example.com', cookies: 5, storage: [] },
  ],
}

test('one row per host with its counts; a row drills into the site', async () => {
  const onshow = vi.fn()
  render(SitesView, { props: { data, onshow } })
  expect(screen.getByText('Site data')).toBeInTheDocument()
  expect(screen.getByText('workspace://site-data')).toBeInTheDocument()
  expect(screen.getByText(/^saved /)).toBeInTheDocument()
  expect(screen.getByText('2 cookies · 2 local · 0 session')).toBeInTheDocument()
  await fireEvent.click(screen.getByRole('button', { name: /docs\.example\.com/ }))
  expect(onshow).toHaveBeenCalledWith('workspace://site-data/docs.example.com')
})

test('without onshow the rows are not buttons', () => {
  render(SitesView, { props: { data } })
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getAllByTitle("Open isn't available in this client")).toHaveLength(2)
})

test('nothing saved says how to save', () => {
  render(SitesView, { props: { data: { uri: 'workspace://site-data', saved_at: null, sites: [] } } })
  expect(screen.getByText(/Nothing saved/)).toBeInTheDocument()
  expect(screen.queryByText(/^saved /)).toBeNull()
})
```

Create `ui/src/lib/views/SiteView.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import SiteView from './SiteView.svelte'

const ORIGIN = 'https://app.example.com'
const cookie = (name: string, value: string, extra = {}) =>
  ({ name, value, domain: 'app.example.com', path: '/', expiry: null, http_only: false, secure: false, same_site: null, shared: false, ...extra })
const detail = {
  site: 'app.example.com', uri: 'workspace://site-data/app.example.com',
  cookies: [
    cookie('sid', '•••', { http_only: true, secure: true }),
    cookie('pref', 'abc', { domain: '.example.com', shared: true, expiry: Date.now() / 1000 + 400 * 86400 }),
  ],
  storage: [{ origin: ORIGIN, local_storage: { theme: 'dark' }, session_storage: {} }],
  own_cookies: ['sid'], kept_shared: [{ name: 'pref', domain: '.example.com', path: '/' }],
}

test('cookies with their facts, httpOnly as the server masked it', () => {
  render(SiteView, { props: { data: detail } })
  expect(screen.getByText('app.example.com')).toBeInTheDocument()
  expect(screen.getByText('workspace://site-data/app.example.com')).toBeInTheDocument()
  expect(screen.getByText('•••')).toBeInTheDocument()
  expect(screen.getByText('httpOnly · secure · session')).toBeInTheDocument()
  expect(screen.getByText('.example.com · 1 y')).toBeInTheDocument()
})

test('storage per origin, only where it has entries', () => {
  render(SiteView, { props: { data: detail } })
  expect(screen.getByText(`Local storage · ${ORIGIN}`)).toBeInTheDocument()
  expect(screen.queryByText(/Session storage/)).toBeNull()
  expect(screen.getByText('theme')).toBeInTheDocument()
  expect(screen.getByText('dark')).toBeInTheDocument()
})

test('a host with nothing at all says so', () => {
  render(SiteView, { props: { data: { ...detail, cookies: [], storage: [] } } })
  expect(screen.getByText('Nothing saved for this site.')).toBeInTheDocument()
  expect(screen.queryByText('Cookies')).toBeNull()
})
```

Append to `ContextView.test.ts` (add `fireEvent` and `vi` to its imports):

```ts
test('the Site data chip drills into the saved site data', async () => {
  const onshow = vi.fn()
  render(ContextView, { props: { data: { ...base, site_data: { sites: 2, uri: 'workspace://site-data' } }, onshow } })
  const chip = screen.getByRole('button', { name: /Site data/ })
  expect(chip).toHaveTextContent('2')
  await fireEvent.click(chip)
  expect(onshow).toHaveBeenCalledWith('workspace://site-data')
})

test('without onshow the chip is not a button', () => {
  render(ContextView, { props: { data: { ...base, site_data: { sites: 2, uri: 'workspace://site-data' } } } })
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getByTitle("Open isn't available in this client")).toHaveTextContent('Site data')
})
```

Add to the `App.test.ts` `test.each` table:

```ts
  ['sites', SITES.data, 'app.example.com'],
  ['site', { site: 'app.example.com', uri: 'workspace://site-data/app.example.com', cookies: [], storage: [], own_cookies: [], kept_shared: [] }, 'Nothing saved for this site.'],
```

with, above the `test.each`:

```ts
const SITES = {
  component: 'sites', uri: 'workspace://site-data',
  data: { saved_at: null, uri: 'workspace://site-data', sites: [{ site: 'app.example.com', uri: 'workspace://site-data/app.example.com', cookies: 1, storage: [] }] },
}
```

and, after the `test.each`:

```ts
test('a site row drills in, and Back says Site data', async () => {
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: {
    component: 'site', uri: 'workspace://site-data/app.example.com',
    data: { site: 'app.example.com', uri: 'workspace://site-data/app.example.com', cookies: [], storage: [], own_cookies: [], kept_shared: [] },
  } })
  render(App)
  await shown(SITES)
  await fireEvent.click(await screen.findByRole('button', { name: /app\.example\.com/ }))
  expect(host.callServerTool).toHaveBeenCalledWith({ name: 'show', arguments: { uri: 'workspace://site-data/app.example.com' } })
  expect(await screen.findByRole('button', { name: 'Back' })).toHaveTextContent('← Site data')
})
```

- [ ] **Step 2: Run them to see them fail**

Run: `npm --prefix ui test -- src/lib/views src/App.test.ts`
Expected: FAIL — the two views do not exist; the context view has no chip.

- [ ] **Step 3: Implement**

Create `ui/src/lib/views/SitesView.svelte`:

```svelte
<script lang="ts">
  import { clock } from '../format'
  import { siteCounts } from '../sites'
  import type { SitesData } from '../types'

  let { data, onshow }: { data: SitesData; onshow?: (uri: string) => void } = $props()
  const UNAVAILABLE = "Open isn't available in this client"
</script>

<div class="card">
  <div class="row head">
    <strong>Site data</strong>
    <code class="uri small muted grow">{data.uri ?? 'workspace://site-data'}</code>
    {#if data.saved_at}<span class="small muted">saved {clock(data.saved_at * 1000)}</span>{/if}
  </div>
  {#if !data.sites.length}
    <p class="small muted">Nothing saved — an agent calls <code>save_site_data</code> after signing in.</p>
  {:else}
    <ul class="rows">
      {#each data.sites as r (r.site)}
        <li>
          {#if onshow && r.uri}
            <button type="button" class="siterow" onclick={() => onshow(r.uri!)}>
              <strong class="grow">{r.site}</strong><span class="pill">{siteCounts(r)}</span><span aria-hidden="true">›</span>
            </button>
          {:else}
            <div class="siterow" title={UNAVAILABLE}><strong class="grow">{r.site}</strong><span class="pill">{siteCounts(r)}</span></div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .head { margin-bottom: 10px; }
  .rows { list-style: none; margin: 0; padding: 0; border: 1px solid var(--line); border-radius: var(--radius); }
  .rows li + li { border-top: 1px solid var(--line); }
  .siterow {
    display: flex; align-items: center; gap: var(--gap); width: 100%; min-height: 44px; padding: 0 12px;
    border: 0; border-radius: 0; background: transparent; color: var(--ink); font: inherit; text-align: left;
  }
  button.siterow { cursor: pointer; }
  button.siterow:hover { background: color-mix(in srgb, var(--ink) 5%, transparent); }
  button.siterow:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
</style>
```

Create `ui/src/lib/views/SiteView.svelte`:

```svelte
<script lang="ts">
  import { until } from '../format'
  import type { SiteCookie, SiteDetail } from '../types'

  let { data }: { data: SiteDetail; onshow?: (uri: string) => void } = $props()

  // httpOnly · secure · the domain when a parent set it · how long it lasts.
  const facts = (c: SiteCookie) =>
    [c.http_only ? 'httpOnly' : '', c.secure ? 'secure' : '', c.shared ? (c.domain ?? '') : '', c.expiry ? until(c.expiry) : 'session']
      .filter(Boolean).join(' · ')
  // Per origin, never merged; an empty storage is not drawn.
  const groups = $derived(
    data.storage
      .flatMap((e) => [
        { label: 'Local storage', origin: e.origin, entries: Object.entries(e.local_storage) },
        { label: 'Session storage', origin: e.origin, entries: Object.entries(e.session_storage) },
      ])
      .filter((g) => g.entries.length),
  )
</script>

<div class="card">
  <div class="row head">
    <strong>{data.site}</strong>
    <code class="uri small muted grow">{data.uri ?? ''}</code>
  </div>
  {#if !data.cookies.length && !groups.length}
    <p class="small muted">Nothing saved for this site.</p>
  {/if}
  {#if data.cookies.length}
    <div class="block">
      <h3>Cookies</h3>
      {#each data.cookies as c (c.name + '|' + c.domain + '|' + c.path)}
        <div class="line">
          <code class="key">{c.name}</code>
          <span class="value clip" title={c.value}>{c.value}</span>
          <span class="small muted">{facts(c)}</span>
        </div>
      {/each}
    </div>
  {/if}
  {#each groups as g (g.label + g.origin)}
    <div class="block">
      <h3>{g.label} · {g.origin}</h3>
      {#each g.entries as [k, v] (k)}
        <div class="line"><code class="key">{k}</code><span class="value clip" title={v}>{v}</span></div>
      {/each}
    </div>
  {/each}
</div>

<style>
  .head { margin-bottom: 10px; flex-wrap: wrap; }
  .block { margin-top: 10px; padding: 8px 12px; border-radius: var(--radius); background: color-mix(in srgb, var(--ink) 4%, transparent); }
  h3 { margin: 0 0 4px; font-size: 11px; font-weight: 400; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .line { display: grid; grid-template-columns: minmax(90px, 160px) minmax(0, 1fr) auto; align-items: baseline; gap: 8px; padding: 3px 0; }
  .key { font-size: 12px; overflow-wrap: anywhere; }
  .value.clip { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--mono, ui-monospace, monospace); }
</style>
```

In `ContextView.svelte`, take `onshow` from the props
(`let { data, onshow }: …`) and add after the last `.row`:

```svelte
  {#if data.site_data}
    <div class="chips">
      {#if onshow && data.site_data.uri}
        <button type="button" class="chip" onclick={() => onshow(data.site_data!.uri!)}>
          <span>Site data</span><span class="pill">{data.site_data.sites}</span>
        </button>
      {:else}
        <span class="chip" title="Open isn't available in this client"><span>Site data</span><span class="pill">{data.site_data.sites}</span></span>
      {/if}
    </div>
  {/if}
```

In `App.svelte`, import `SitesView` and `SiteView` and add
`sites: SitesView as Component<Props>,` and `site: SiteView as Component<Props>,`
to `VIEWS`.

- [ ] **Step 4: Run the UI suite**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint`
Expected: PASS. The existing context test `no principal, no url, idle` still
finds no `/site/` text, since it has no `site_data`.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/views/SitesView.svelte ui/src/lib/views/SitesView.test.ts ui/src/lib/views/SiteView.svelte \
  ui/src/lib/views/SiteView.test.ts ui/src/lib/views/ContextView.svelte ui/src/lib/views/ContextView.test.ts \
  ui/src/App.svelte ui/src/App.test.ts
git commit -m "show app: the saved site data and one site, opened from the context view

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Markdown, lexed and drawn by Svelte

**Files:**
- Modify: `ui/package.json`, `ui/package-lock.json`
- Create: `ui/src/lib/links.ts`, `ui/src/lib/links.test.ts`
- Create: `ui/src/lib/markdown.ts`, `ui/src/lib/markdown.test.ts`
- Create: `ui/src/lib/Markdown.svelte`, `ui/src/lib/MarkdownInline.svelte`, `ui/src/lib/Markdown.test.ts`
- Modify: `ui/src/app.css`

**Interfaces:**
- Consumes: `safeHref` (format.ts).
- Produces: `showable(text: string): string | null`; `target(href: string, base: string): Target | null` with `type Target = { kind: 'show'; uri: string } | { kind: 'open'; url: string }` (`lib/links.ts`); `body(text: string): string`, `parse(text: string): { title: Tokens.Heading | null; blocks: Token[] }` (`lib/markdown.ts`); `Markdown.svelte` props `{ tokens: Token[]; base: string; onshow?; onlink? }`, `MarkdownInline.svelte` the same.

- [ ] **Step 1: Add the dependency**

Run: `npm --prefix ui install --save-exact marked@18.1.0`
Expected: `ui/package.json` `dependencies` gains `"marked": "18.1.0"`; the lock
file updates.

- [ ] **Step 2: Write the failing tests**

Create `ui/src/lib/links.test.ts`:

```ts
import { expect, test } from 'vitest'
import { showable, target } from './links'

const SKILL = 'skill://selenium-flow/SKILL.md'

test('a concrete URI on a server scheme is showable; a placeholder is not', () => {
  expect(showable('workspace://files')).toBe('workspace://files')
  expect(showable('skill://selenium-flow/references/FLOWS.md')).toBe('skill://selenium-flow/references/FLOWS.md')
  expect(showable('flow://flows/{name}')).toBeNull()
  expect(showable('flow://flows/<name>')).toBeNull()
  expect(showable('workspace://')).toBeNull()
  expect(showable('https://example.com/')).toBeNull()
  expect(showable('open_session')).toBeNull()
})

test('a relative link resolves against the document and drills', () => {
  expect(target('references/FLOWS.md', SKILL)).toEqual({ kind: 'show', uri: 'skill://selenium-flow/references/FLOWS.md' })
  expect(target('../SKILL.md', 'skill://selenium-flow/references/FLOWS.md')).toEqual({ kind: 'show', uri: SKILL })
  expect(target('FLOWS.md#parameters', 'skill://selenium-flow/references/WORKSPACES.md'))
    .toEqual({ kind: 'show', uri: 'skill://selenium-flow/references/FLOWS.md' })
})

test('the web opens; anything else is text', () => {
  expect(target('https://example.com/wiki', SKILL)).toEqual({ kind: 'open', url: 'https://example.com/wiki' })
  expect(target('javascript:alert(1)', SKILL)).toBeNull()
  expect(target('#top', SKILL)).toBeNull()
  expect(target('', SKILL)).toBeNull()
  expect(target('mailto:drk@example.com', SKILL)).toBeNull()
})
```

Create `ui/src/lib/markdown.test.ts`:

```ts
import { expect, test } from 'vitest'
import { body, parse } from './markdown'

test('front matter is not drawn', () => {
  expect(body('---\nname: x\ndescription: y\n---\n\n# T\n')).toBe('\n# T\n')
  expect(body('# T\n')).toBe('# T\n')
  expect(body('---\nunterminated')).toBe('---\nunterminated')
})

test('the first # heading is the title; spaces are not blocks', () => {
  const doc = parse('---\nname: x\n---\n\n# When something goes wrong\n\nRead the error.\n\n## A timeout\n')
  expect(doc.title?.text).toBe('When something goes wrong')
  expect(doc.blocks.map((b) => b.type)).toEqual(['paragraph', 'heading'])
})

test('no # heading, no title', () => {
  expect(parse('Just text.').title).toBeNull()
})
```

Create `ui/src/lib/Markdown.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import Markdown from './Markdown.svelte'
import { parse } from './markdown'

const BASE = 'skill://selenium-flow/references/TROUBLESHOOTING.md'
const draw = (text: string, props = {}) => render(Markdown, { props: { tokens: parse(text).blocks, base: BASE, ...props } })

test('the block types the skill uses', () => {
  const { container } = draw([
    '## A timeout', '', 'Suspect **the page** before *the expression*.', '',
    '- one', '- two', '', '1. first', '2. second', '',
    '```', 'extract(selector={"xpath": "//title"})', '```', '',
    '| Task | Read |', '|---|---|', '| a | b |', '', '> quoted', '', '---',
  ].join('\n'))
  expect(container.querySelector('h2')).toHaveTextContent('A timeout')
  expect(container.querySelector('strong')).toHaveTextContent('the page')
  expect(container.querySelector('em')).toHaveTextContent('the expression')
  expect(container.querySelectorAll('ul > li')).toHaveLength(2)
  expect(container.querySelectorAll('ol > li')).toHaveLength(2)
  expect(container.querySelector('pre code')).toHaveTextContent('extract(selector={"xpath": "//title"})')
  expect(container.querySelectorAll('th')).toHaveLength(2)
  expect(container.querySelector('td')).toHaveTextContent('a')
  expect(container.querySelector('blockquote')).toHaveTextContent('quoted')
  expect(container.querySelector('hr')).not.toBeNull()
})

test('raw HTML is text, never markup', () => {
  const { container } = draw('A <img src=x onerror=alert(1)> b\n\n<script>alert(1)</script>\n')
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('script')).toBeNull()
  expect(container).toHaveTextContent('<img src=x onerror=alert(1)>')
})

test('a URI in a code span drills through show', async () => {
  const onshow = vi.fn()
  draw('See `skill://selenium-flow/references/FLOWS.md` and `flow://flows/{name}`.', { onshow })
  await fireEvent.click(screen.getByRole('button', { name: 'skill://selenium-flow/references/FLOWS.md' }))
  expect(onshow).toHaveBeenCalledWith('skill://selenium-flow/references/FLOWS.md')
  expect(screen.queryByRole('button', { name: 'flow://flows/{name}' })).toBeNull()
  expect(screen.getByText('flow://flows/{name}').tagName).toBe('CODE')
})

test('without onshow a URI is just code', () => {
  draw('See `workspace://files`.')
  expect(screen.queryByRole('button')).toBeNull()
})

test('a relative link drills; a web link opens through the host', async () => {
  const onshow = vi.fn()
  const onlink = vi.fn()
  draw('[flows](FLOWS.md) and [the wiki](https://example.com/wiki) and [bad](javascript:alert(1))', { onshow, onlink })
  await fireEvent.click(screen.getByRole('button', { name: 'flows' }))
  expect(onshow).toHaveBeenCalledWith('skill://selenium-flow/references/FLOWS.md')
  const web = screen.getByRole('link', { name: 'the wiki' })
  expect(web).toHaveAttribute('href', 'https://example.com/wiki')
  await fireEvent.click(web)
  expect(onlink).toHaveBeenCalledWith('https://example.com/wiki')
  expect(screen.queryByRole('link', { name: 'bad' })).toBeNull()
  expect(screen.getByText('bad')).toBeInTheDocument()
})
```

- [ ] **Step 3: Run them to see them fail**

Run: `npm --prefix ui test -- src/lib/links.test.ts src/lib/markdown.test.ts src/lib/Markdown.test.ts`
Expected: FAIL — the modules do not exist.

- [ ] **Step 4: Implement**

Create `ui/src/lib/links.ts`:

```ts
import { safeHref } from './format'

/* The schemes this server serves: a URI on one of them is something `show`
   may draw. */
const SCHEMES = ['skill:', 'flow:', 'workspace:', 'secret:']
// Concrete: something after the //, and no placeholder or space in it.
const CONCRETE = /^(skill|flow|workspace|secret):\/\/[^\s{}<>]+$/

/* A code span's text, when the whole of it is a URI worth drilling into. */
export const showable = (text: string): string | null => (CONCRETE.test(text) ? text : null)

export type Target = { kind: 'show'; uri: string } | { kind: 'open'; url: string }

/* Where a markdown link goes, resolved against the document's own URI
   (spec 2026-10-09-show-everything, ruling 4). Null: draw it as text. */
export function target(href: string, base: string): Target | null {
  if (!href || href.startsWith('#')) return null
  let url: URL
  try {
    url = new URL(href, base)
  } catch {
    return null
  }
  if (SCHEMES.includes(url.protocol)) {
    url.hash = ''
    const uri = showable(url.href)
    return uri ? { kind: 'show', uri } : null
  }
  const open = safeHref(url.href)
  return open ? { kind: 'open', url: open } : null
}
```

Create `ui/src/lib/markdown.ts`:

```ts
import { Lexer, type Token, type Tokens } from 'marked'

/* YAML front matter is a skill's metadata, not its text; lexed, it becomes a
   rule and a setext heading. */
export function body(text: string): string {
  if (!text.startsWith('---\n')) return text
  const end = text.indexOf('\n---\n', 4)
  return end === -1 ? text : text.slice(end + 5)
}

export interface Doc { title: Tokens.Heading | null; blocks: Token[] }

/* A document's blocks, its first # heading lifted out as the title. Tokens
   only: the app draws them with Svelte and never renders an HTML string. */
export function parse(text: string): Doc {
  const tokens: Token[] = new Lexer({ gfm: true }).lex(body(text)).filter((t) => t.type !== 'space')
  const at = tokens.findIndex((t) => t.type === 'heading' && (t as Tokens.Heading).depth === 1)
  if (at === -1) return { title: null, blocks: tokens }
  return { title: tokens[at] as Tokens.Heading, blocks: tokens.filter((_, i) => i !== at) }
}
```

Create `ui/src/lib/MarkdownInline.svelte`:

```svelte
<script lang="ts">
  import type { Token, Tokens } from 'marked'
  import { showable, target } from './links'
  import Self from './MarkdownInline.svelte'

  let { tokens, base, onshow, onlink }: {
    tokens: Token[]
    base: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
  } = $props()

  function open(e: MouseEvent, url: string) {
    if (!onlink) return
    e.preventDefault()
    onlink(url)
  }
</script>

{#each tokens as t, i (i)}
  {#if t.type === 'text' || t.type === 'escape'}
    {@const x = t as Tokens.Text}
    {#if x.tokens?.length}<Self tokens={x.tokens} {base} {onshow} {onlink} />{:else}{x.text}{/if}
  {:else if t.type === 'strong'}
    <strong><Self tokens={(t as Tokens.Strong).tokens} {base} {onshow} {onlink} /></strong>
  {:else if t.type === 'em'}
    <em><Self tokens={(t as Tokens.Em).tokens} {base} {onshow} {onlink} /></em>
  {:else if t.type === 'del'}
    <del><Self tokens={(t as Tokens.Del).tokens} {base} {onshow} {onlink} /></del>
  {:else if t.type === 'br'}
    <br>
  {:else if t.type === 'codespan'}
    {@const text = (t as Tokens.Codespan).text}
    {@const uri = showable(text)}
    {#if uri && onshow}
      <button type="button" class="md-uri" onclick={() => onshow(uri)}><code>{text}</code></button>
    {:else}
      <code>{text}</code>
    {/if}
  {:else if t.type === 'link'}
    {@const l = t as Tokens.Link}
    {@const to = target(l.href, base)}
    {#if to && to.kind === 'show' && onshow}
      <button type="button" class="md-link" onclick={() => onshow(to.uri)}><Self tokens={l.tokens} {base} {onshow} {onlink} /></button>
    {:else if to && to.kind === 'open'}
      <a href={to.url} target="_blank" rel="noopener noreferrer" onclick={(e) => open(e, to.url)}><Self tokens={l.tokens} {base} {onshow} {onlink} /></a>
    {:else}
      <Self tokens={l.tokens} {base} {onshow} {onlink} />
    {/if}
  {:else}
    {t.raw}
  {/if}
{/each}
```

Create `ui/src/lib/Markdown.svelte`:

```svelte
<script lang="ts">
  import type { Token, Tokens } from 'marked'
  import Inline from './MarkdownInline.svelte'
  import Self from './Markdown.svelte'

  let { tokens, base, onshow, onlink }: {
    tokens: Token[]
    base: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
  } = $props()

  // The document's # is its title; what is left starts at h2.
  const tag = (depth: number) => 'h' + Math.min(6, Math.max(2, depth))
</script>

{#each tokens as t, i (i)}
  {#if t.type === 'heading'}
    {@const h = t as Tokens.Heading}
    <svelte:element this={tag(h.depth)}><Inline tokens={h.tokens} {base} {onshow} {onlink} /></svelte:element>
  {:else if t.type === 'paragraph'}
    <p><Inline tokens={(t as Tokens.Paragraph).tokens} {base} {onshow} {onlink} /></p>
  {:else if t.type === 'text'}
    {@const x = t as Tokens.Text}
    {#if x.tokens?.length}<Inline tokens={x.tokens} {base} {onshow} {onlink} />{:else}{x.text}{/if}
  {:else if t.type === 'list'}
    {@const l = t as Tokens.List}
    {#if l.ordered}
      <ol start={typeof l.start === 'number' ? l.start : undefined}>
        {#each l.items as item, j (j)}<li><Self tokens={item.tokens} {base} {onshow} {onlink} /></li>{/each}
      </ol>
    {:else}
      <ul>
        {#each l.items as item, j (j)}<li><Self tokens={item.tokens} {base} {onshow} {onlink} /></li>{/each}
      </ul>
    {/if}
  {:else if t.type === 'code'}
    <pre><code>{(t as Tokens.Code).text}</code></pre>
  {:else if t.type === 'table'}
    {@const tb = t as Tokens.Table}
    <div class="md-table">
      <table>
        <thead><tr>{#each tb.header as c, j (j)}<th><Inline tokens={c.tokens} {base} {onshow} {onlink} /></th>{/each}</tr></thead>
        <tbody>
          {#each tb.rows as row, j (j)}
            <tr>{#each row as c, k (k)}<td><Inline tokens={c.tokens} {base} {onshow} {onlink} /></td>{/each}</tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else if t.type === 'blockquote'}
    <blockquote><Self tokens={(t as Tokens.Blockquote).tokens} {base} {onshow} {onlink} /></blockquote>
  {:else if t.type === 'hr'}
    <hr>
  {:else if t.type === 'space'}
    <!-- nothing to draw -->
  {:else}
    <p>{t.raw}</p>
  {/if}
{/each}
```

Append to `ui/src/app.css`:

```css
/* A document drawn from markdown tokens (lib/Markdown.svelte). */
.md { line-height: 1.5; }
.md h2, .md h3, .md h4, .md h5, .md h6 { margin: 16px 0 6px; font-size: 15px; }
.md p, .md ul, .md ol, .md blockquote { margin: 0 0 10px; }
.md pre { margin: 0 0 10px; padding: 10px 12px; border-radius: var(--radius); background: color-mix(in srgb, var(--ink) 6%, transparent); overflow-x: auto; }
.md code { font-family: var(--mono, ui-monospace, monospace); font-size: 12px; }
.md .md-table { overflow-x: auto; margin: 0 0 10px; }
.md table { border-collapse: collapse; font-size: 13px; }
.md th, .md td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
.md blockquote { padding-left: 10px; border-left: 3px solid var(--line); color: var(--muted); }
.md .md-uri, .md .md-link { padding: 0; min-height: 0; border: 0; background: none; color: var(--accent); font: inherit; cursor: pointer; text-decoration: underline; }
```

- [ ] **Step 5: Run the UI suite and the budget**

Run: `npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run build && npm --prefix ui run size`
Expected: PASS. `size` prints `app:` under 110 KB — the components are not
imported by the shell yet, so this line still reads about 84 KB; Task 8 is where
the budget is really spent.

- [ ] **Step 6: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/src/lib/links.ts ui/src/lib/links.test.ts ui/src/lib/markdown.ts \
  ui/src/lib/markdown.test.ts ui/src/lib/Markdown.svelte ui/src/lib/MarkdownInline.svelte ui/src/lib/Markdown.test.ts ui/src/app.css
git commit -m "ui: markdown lexed by marked and drawn token by token, links that drill

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The document view and the JSON tree; the shell draws every component

**Files:**
- Create: `ui/src/lib/JsonTree.svelte`, `ui/src/lib/JsonTree.test.ts`
- Create: `ui/src/lib/views/DocumentView.svelte`, `ui/src/lib/views/DocumentView.test.ts`
- Modify: `ui/src/App.svelte`, `ui/src/App.test.ts`, `ui/src/app.css`
- Modify: `tests/test_show_inventory.py`

**Interfaces:**
- Consumes: `parse` (Task 7), `Markdown`, `MarkdownInline`, `leaf`, `readable`, `isRecord` (format.ts), the shell's `uri`/`expanded`/`expandable`/`onshow`/`onlink` (Task 4).
- Produces: component `document` registered; every component in `show.VIEWS` is a key of `App.svelte`'s `VIEWS`.

- [ ] **Step 1: Write the failing tests**

Create `ui/src/lib/JsonTree.test.ts`:

```ts
import { render, screen } from '@testing-library/svelte'
import { expect, test } from 'vitest'
import JsonTree from './JsonTree.svelte'

test('objects and arrays fold with a count; the top is open, the rest shut', () => {
  const { container } = render(JsonTree, { props: { value: { title: 'Flow', steps: [1, 2], timeout: 30, shared: false, x: null } } })
  const folds = container.querySelectorAll('details')
  expect(folds).toHaveLength(2)
  expect(folds[0]).toHaveAttribute('open')
  expect(folds[1]).not.toHaveAttribute('open')
  expect(folds[0].querySelector('summary')).toHaveTextContent('{5}')
  expect(folds[1].querySelector('summary')).toHaveTextContent('steps [2]')
  expect(screen.getByText('"Flow"')).toBeInTheDocument()
  expect(screen.getByText('30')).toBeInTheDocument()
  expect(screen.getByText('false')).toBeInTheDocument()
  expect(screen.getByText('null')).toBeInTheDocument()
})

test('a scalar alone is a leaf', () => {
  render(JsonTree, { props: { value: 'just text' } })
  expect(screen.getByText('"just text"')).toBeInTheDocument()
})
```

Create `ui/src/lib/views/DocumentView.test.ts`:

```ts
import { fireEvent, render, screen } from '@testing-library/svelte'
import { expect, test, vi } from 'vitest'
import DocumentView from './DocumentView.svelte'

const URI = 'skill://selenium-flow/references/TROUBLESHOOTING.md'
const page = (n: number) =>
  '# When something goes wrong\n\n' + Array.from({ length: n }, (_, i) => `Paragraph ${i + 1}.`).join('\n\n') + '\n'

test('markdown: the # heading is the title beside the URI', () => {
  render(DocumentView, { props: { data: page(2), uri: URI } })
  expect(screen.getByText('When something goes wrong').closest('strong')).not.toBeNull()
  expect(screen.getByText(URI)).toBeInTheDocument()
  expect(screen.getByText('Paragraph 2.')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { level: 1 })).toBeNull()
})

test('inline with fullscreen on offer: six blocks and +N more', () => {
  render(DocumentView, { props: { data: page(9), uri: URI, expandable: true } })
  expect(screen.getByText('Paragraph 6.')).toBeInTheDocument()
  expect(screen.queryByText('Paragraph 7.')).toBeNull()
  expect(screen.getByText('+3 more')).toBeInTheDocument()
})

test('fullscreen, or no fullscreen to offer: everything', () => {
  render(DocumentView, { props: { data: page(9), uri: URI, expandable: true, expanded: true } })
  expect(screen.getByText('Paragraph 9.')).toBeInTheDocument()
  expect(screen.queryByText(/more$/)).toBeNull()
})

test('without fullscreen the whole document draws', () => {
  render(DocumentView, { props: { data: page(9), uri: URI, expandable: false } })
  expect(screen.getByText('Paragraph 9.')).toBeInTheDocument()
})

test('no # heading: the file name is the title', () => {
  render(DocumentView, { props: { data: 'Just text.', uri: 'skill://selenium-flow/references/NOTES.md' } })
  expect(screen.getByText('NOTES.md')).toBeInTheDocument()
})

test('a URI in the text drills through show', async () => {
  const onshow = vi.fn()
  render(DocumentView, { props: { data: '# T\n\nRead `skill://selenium-flow/references/FLOWS.md`.\n', uri: URI, onshow } })
  await fireEvent.click(screen.getByRole('button', { name: 'skill://selenium-flow/references/FLOWS.md' }))
  expect(onshow).toHaveBeenCalledWith('skill://selenium-flow/references/FLOWS.md')
})

test('JSON is a tree, titled by its title or its URI', () => {
  const { container, unmount } = render(DocumentView, { props: { data: { title: 'Flow', type: 'object' }, uri: 'flow://schema' } })
  expect(screen.getByText('Flow', { selector: 'strong' })).toBeInTheDocument()
  expect(container.querySelector('details')).not.toBeNull()
  unmount()
  render(DocumentView, { props: { data: { skill: 'selenium-flow', files: [] }, uri: 'skill://selenium-flow/_manifest' } })
  expect(screen.getByText('_manifest', { selector: 'strong' })).toBeInTheDocument()
})
```

Add to the `App.test.ts` `test.each` table:

```ts
  ['document', '# When something goes wrong\n\nRead the error first.\n', 'Read the error first.'],
```

and:

```ts
test('a document can go fullscreen when the host offers it', async () => {
  host.ctx = { availableDisplayModes: ['inline', 'fullscreen'] }
  render(App)
  await shown({ component: 'document', uri: 'skill://selenium-flow/SKILL.md', data: '# T\n\ntext\n' })
  expect(await screen.findByRole('button', { name: 'Fullscreen' })).toBeInTheDocument()
})

test('Back under a document names its file', async () => {
  host.callServerTool.mockResolvedValue({ content: [], structuredContent: {
    component: 'document', uri: 'skill://selenium-flow/references/FLOWS.md', data: '# Flows\n\nSave once.\n',
  } })
  render(App)
  await shown({ component: 'document', uri: 'skill://selenium-flow/SKILL.md', data: '# Skill\n\nSee `skill://selenium-flow/references/FLOWS.md`.\n' })
  await fireEvent.click(await screen.findByRole('button', { name: 'skill://selenium-flow/references/FLOWS.md' }))
  expect(await screen.findByRole('button', { name: 'Back' })).toHaveTextContent('← SKILL.md')
})
```

Append to `tests/test_show_inventory.py` (add `from pathlib import Path` to the imports):

```python
APP = Path(__file__).parents[1] / "ui" / "src" / "App.svelte"


def test_every_component_show_names_the_shell_draws():
    """The other end of the table: a component with no Svelte view would draw
    'Nothing to show'."""
    keys = set(re.findall(
        r"^\s+'?([\w-]+)'?: \w+ as Component<Props>,", APP.read_text(), re.M
    ))
    assert {view.component for view in show.VIEWS} == keys
```

- [ ] **Step 2: Run them to see them fail**

Run: `npm --prefix ui test -- src/lib src/App.test.ts && pytest tests/test_show_inventory.py -v`
Expected: FAIL — `JsonTree` and `DocumentView` do not exist; the pytest names
`document` as missing from the shell.

- [ ] **Step 3: Implement**

Create `ui/src/lib/JsonTree.svelte`:

```svelte
<script lang="ts">
  import Self from './JsonTree.svelte'

  let { value, name = null, depth = 0 }: { value: unknown; name?: string | null; depth?: number } = $props()

  const branch = $derived(value !== null && typeof value === 'object')
  const entries = $derived<[string, unknown][]>(
    !branch ? [] : Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value as Record<string, unknown>),
  )
  const count = $derived(Array.isArray(value) ? '[' + entries.length + ']' : '{' + entries.length + '}')
  const scalar = (v: unknown) => (typeof v === 'string' ? JSON.stringify(v) : String(v))
</script>

{#if branch}
  <details class="tree" open={depth === 0}>
    <summary>{#if name !== null}<code class="key">{name}</code>{' '}{/if}<span class="small muted">{count}</span></summary>
    <div class="kids">
      {#each entries as [k, v] (k)}<Self value={v} name={k} depth={depth + 1} />{/each}
    </div>
  </details>
{:else}
  <div class="tree leaf">{#if name !== null}<code class="key">{name}</code>{': '}{/if}<code class="v">{scalar(value)}</code></div>
{/if}
```

Create `ui/src/lib/views/DocumentView.svelte`:

```svelte
<script lang="ts">
  import { isRecord, leaf, readable } from '../format'
  import JsonTree from '../JsonTree.svelte'
  import Markdown from '../Markdown.svelte'
  import MarkdownInline from '../MarkdownInline.svelte'
  import { parse } from '../markdown'

  let { data, uri = '', onshow, onlink, expanded = false, expandable = false }: {
    data: unknown
    uri?: string
    onshow?: (uri: string) => void
    onlink?: (url: string) => void
    expanded?: boolean
    expandable?: boolean
  } = $props()

  // Inline, a document stops here when fullscreen can show the rest (ruling 6).
  const LIMIT = 6
  const doc = $derived(typeof data === 'string' ? parse(data) : null)
  const blocks = $derived(doc ? (expanded || !expandable ? doc.blocks : doc.blocks.slice(0, LIMIT)) : [])
  const more = $derived(doc ? doc.blocks.length - blocks.length : 0)
  const jsonTitle = $derived(isRecord(data) && typeof data.title === 'string' ? data.title : leaf(uri))
</script>

<div class="card doc">
  <div class="row head">
    <strong>
      {#if doc?.title}<MarkdownInline tokens={doc.title.tokens} base={uri} />{:else if doc}{leaf(uri)}{:else}{jsonTitle}{/if}
    </strong>
    <code class="uri small muted grow">{readable(uri)}</code>
  </div>
  {#if doc}
    <div class="md"><Markdown tokens={blocks} base={uri} {onshow} {onlink} /></div>
    {#if more > 0}<div class="small muted">+{more} more</div>{/if}
  {:else}
    <JsonTree value={data} />
  {/if}
</div>

<style>
  .head { margin-bottom: 10px; flex-wrap: wrap; }
</style>
```

Append to `ui/src/app.css`:

```css
/* A JSON document as a tree (lib/JsonTree.svelte). */
.tree { font-size: 13px; }
.tree summary { cursor: pointer; padding: 2px 0; }
.tree .kids { padding-left: 16px; border-left: 1px solid var(--line); margin-left: 4px; }
.tree.leaf { padding: 2px 0; overflow-wrap: anywhere; }
.tree .key { color: var(--accent); }
```

In `App.svelte`, import `DocumentView` and add
`document: DocumentView as Component<Props>,` to `VIEWS`.

- [ ] **Step 4: Run everything and the budget**

Run:
```bash
npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint
npm --prefix ui run build && npm --prefix ui run size
pytest tests/test_show.py tests/test_show_inventory.py -v
```
Expected: PASS; `size` prints `app:` at about 100 KB and under the 110 KB
budget. If it is over, stop: the task is not done (spec Design 7).

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/JsonTree.svelte ui/src/lib/JsonTree.test.ts ui/src/lib/views/DocumentView.svelte \
  ui/src/lib/views/DocumentView.test.ts ui/src/App.svelte ui/src/App.test.ts ui/src/app.css tests/test_show_inventory.py
git commit -m "show app: the skill's pages and the flow schema as documents; every view registered

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Documentation

**Files:**
- Modify: `skills/selenium-flow/SKILL.md:173`
- Modify: `AGENTS.md` (section "`show` is the one app tool")
- Modify: `README.md` (section "🧩 MCP Apps")
- Modify: `CHANGELOG.md` (`[Unreleased]`)
- Modify: `wiki/` prose (submodule)

- [ ] **Step 1: The skill's tool table**

In `skills/selenium-flow/SKILL.md`, replace the `show` row with:

```markdown
| `show` | draw any resource for the person (hosts that render apps): the workspace, files and one file, site data, flows, the secrets, these pages, the flow schema | `uri` |
```

- [ ] **Step 2: AGENTS.md**

In "`show` is the one app tool", after the paragraph that opens the section,
add:

```markdown
- **Every URI the server serves has a view** (programme R17).
  `tests/test_show_inventory.py` enumerates the resources and templates through
  a client and fails on one without a row in `VIEWS`, and on a component the
  shell does not draw. A new resource gets its row and its view in the same
  change. `ui://` is exempt: the shell is the drawing.
- **A single file is drawn from its folder's listing entry**, read through the
  server: never its bytes, and the item resources keep serving bytes to clients
  that read them.
- **A document is lexed, never rendered to HTML.** `marked`'s `Lexer` makes
  tokens and `lib/Markdown.svelte` draws them; `{@html}` stays forbidden. A URI
  in a code span drills through `show`.
```

- [ ] **Step 3: README**

In "🧩 MCP Apps", replace the sentence beginning `` `show(uri)` draws `` with:

```markdown
`show(uri)` draws any resource the server serves: your workspace, its files and any one of them — a screenshot, a recording you can play — the saved site data, the flows as cards, one flow, the secrets, the skill's pages and the flow schema.
```

- [ ] **Step 4: CHANGELOG**

Under `## [Unreleased]`, after the existing `show(uri)` line, add:

```markdown
- `show` draws every resource: one file, the saved site data, the skill's pages and the flow schema.
```

- [ ] **Step 5: The wiki**

```bash
git submodule update --init wiki
grep -rln 'show(' wiki/
```

In each hand-written page or `wiki/notes/*.notes.md` that lists what `show`
draws (`Installing.md` at least), replace the list with the README's sentence
from Step 3. Generated pages are not edited (`show` has no endpoint, so it has
no generated page). Then:

```bash
python scripts/generate_wiki.py --check
pytest tests/test_wiki.py tests/test_skill.py -v
```
Expected: PASS (`--check` reports the generated pages current).

Commit inside the submodule first, then the pointer:

```bash
git -C wiki add -A && git -C wiki commit -m "show draws every resource

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Full run and commit**

```bash
ruff check kubed
pytest
npm --prefix ui test && npm --prefix ui run check && npm --prefix ui run lint && npm --prefix ui run build && npm --prefix ui run size
git add skills/selenium-flow/SKILL.md AGENTS.md README.md CHANGELOG.md wiki
git commit -m "show draws every resource: the skill, AGENTS.md, README, changelog and wiki

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: everything green; `app:` under 110 KB.

---

## After the tasks

Dr K, live on Claude web (spec *Testing*, *Verify first* 5–6): "show me my last
screenshot", "show me the recording" (does it play?), "show me the saved site
data" → a site → Back, "show me the skill" → a reference, "show me the flow
schema"; light and dark; Open on a download (does Claude offer `openLinks`?).
