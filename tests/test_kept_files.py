"""Files kept in a session's own store, and the admin/HTTP surfaces over them.

The Grid's file API is **list, read-one, delete-all** — there is no write and no
per-file delete, which is why keeping a download is a **copy**: the original
cannot be removed until the browser ends or the downloads are cleared.

The three-sections split and its listing rules (`root`, `folder`, `sections`,
`keep`) live in `test_file_sections.py` now. What is left here: the store's own
file rules (naming, escaping, one name per folder), that a Grid failure is
never hidden, and the admin/HTTP surfaces — keeping, deleting, clearing, the
signed link, and the published contract — built on those domain functions.

The real Grid is never dialled; a fake stands in throughout.
"""

from unittest.mock import patch
from urllib.parse import quote

import pytest
import requests
from starlette.testclient import TestClient

from kubed.selenium_flow import errors
from kubed.selenium_flow.core import browser
from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.http import admin, files, links
from kubed.selenium_flow.routes import ENDPOINTS
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.store import SessionRecord
from kubed.selenium_flow.spec import build_spec

from .conftest import NAMED, TOKEN

pytestmark = pytest.mark.unit

KEY = "desktop"
SESSION = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

# Newest last, deliberately: the Grid sorts its own listing, and a merged list
# has to impose an order rather than inherit one. report.pdf is the later file.
DOWNLOADS = [
    {"name": "report.pdf", "size": 2048, "creationTime": 1700000001000},
    {"name": "shot.png", "size": 1024, "creationTime": 1700000000000},
]


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path)


@pytest.fixture
def kept_server(tmp_path):
    """A server with somewhere to keep files."""
    return SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        flow_data_dir=str(tmp_path),
    )


@pytest.fixture
def client(kept_server):
    """Every request names its session, the way this surface says to (§F2.13)."""
    return TestClient(
        kept_server.mcp.http_app(), headers={"X-Session-Key": SESSION}
    )


@pytest.fixture
def live(kept_server):
    """A flow session holding a browser, which is what the admin API addresses."""
    kept_server.sessions.store.set(
        KEY, SessionRecord(session_id="abc", url="https://x/")
    )
    return kept_server


class FakeGrid:
    """Just enough Grid to hand out a listing and some bytes."""

    def __init__(self, entries=(), data=b"bytes"):
        self.entries = list(entries)
        self.data = data
        self.reads = []

    def files(self, session_id):
        return list(self.entries)

    def read_file(self, session_id, name):
        self.reads.append((session_id, name))
        return self.data


class FakeActions:
    def __init__(self, grid):
        self.grid = grid


class Sessions:
    """A session manager double: only what `listing` actually reads.

    `browser` is what a listing asks for — the Grid id of a LIVE browser, or ""
    — because a listing must keep answering after the browser is gone, which is
    the whole point of keeping a file.
    """

    def __init__(self, **status):
        self.status = status

    def describe(self, name=None):
        return dict(self.status)

    def browser(self, name):
        return self.status.get("session_id", "") if self.status.get("live") else ""

    def name(self):
        return NAMED


# ---- the store ---------------------------------------------------------------


def test_a_kept_file_round_trips(store):
    store.write_file(SESSION, "report.pdf", b"PDF bytes")
    assert store.read_file(SESSION, "report.pdf") == b"PDF bytes"
    assert [f["name"] for f in store.files(SESSION)] == ["report.pdf"]


def test_a_kept_entry_is_shaped_like_the_grids(store):
    """The two listings are merged into one array, so they must agree on the key
    names AND the units. Milliseconds, because that is what the Grid reports —
    a seconds-based timestamp beside it sorts every kept file to 1970 while
    nothing looks wrong."""
    store.write_file(SESSION, "report.pdf", b"x")
    entry = store.files(SESSION)[0]
    assert set(entry) == {"name", "size", "creationTime"}
    assert entry["size"] == 1
    # 2001-09-09 in ms. A seconds value would be ~1.7e9, which is 1970 in ms.
    assert entry["creationTime"] > 1_000_000_000_000


def test_keeping_the_same_name_replaces_it(store):
    """The create-or-update rule `save_flow` uses. The alternative is a second
    copy under a name nobody chose."""
    store.write_file(SESSION, "report.pdf", b"first")
    store.write_file(SESSION, "report.pdf", b"second")
    assert store.read_file(SESSION, "report.pdf") == b"second"
    assert len(store.files(SESSION)) == 1


def test_deleting_reports_whether_there_was_anything_there(store):
    store.write_file(SESSION, "report.pdf", b"x")
    assert store.delete_file(SESSION, "report.pdf") is True
    assert store.delete_file(SESSION, "report.pdf") is False


def test_one_session_cannot_see_anothers_kept_files(store):
    store.write_file(SESSION, "report.pdf", b"x")
    assert store.files("other") == []


def test_nothing_is_created_until_something_is_kept(tmp_path):
    store = flows.LocalFlowStore(tmp_path / "data")
    assert store.files(SESSION) == []
    assert not (tmp_path / "data").exists()


# ---- what a file may be called ----------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        # Chrome deduplicates downloads exactly like this, and a site may serve
        # anything. A flow name's rule would refuse every one of them, and none
        # of them is a mistake the caller can fix.
        "report (1).pdf",
        "Q3 summary.csv",
        "rapport-été.pdf",
        "a,b;c.txt",
        "UPPER.PNG",
    ],
)
def test_a_download_named_the_way_browsers_name_them_is_keepable(store, name):
    store.write_file(SESSION, name, b"x")
    assert [f["name"] for f in store.files(SESSION)] == [name]


@pytest.mark.parametrize(
    "name", ["../escape.pdf", "a/b.pdf", "a\\b.pdf", "..", ".", "", "   ", ".hidden"]
)
def test_a_file_name_that_is_not_one_segment_is_refused(store, name):
    with pytest.raises(flows.InvalidName):
        store.write_file(SESSION, name, b"x")


def test_a_file_name_is_not_trimmed(store):
    """Nobody typed this name — the site's Content-Disposition or Chrome chose
    it — so a surrounding space is part of the name rather than a typo. Trimming
    it (as a *session* name is trimmed, correctly) made `keep_one` ask the Grid
    for a name it does not have, and the stored copy could then round-trip
    through neither read nor delete."""
    name = " report.pdf "
    store.write_file(SESSION, name, b"x")
    assert [f["name"] for f in store.files(SESSION)] == [name]
    assert store.read_file(SESSION, name) == b"x"
    assert store.delete_file(SESSION, name) is True


def test_a_kept_file_cannot_escape_the_data_directory(store, tmp_path):
    """The name arrives from a URL path parameter as well as from the Grid, so
    traversal is refused by the name rule and again by `_resolved`."""
    with pytest.raises(flows.InvalidName):
        store.write_file(SESSION, "../../etc/passwd", b"x")
    assert not (tmp_path.parent / "etc").exists()


def test_a_name_on_disk_that_could_not_be_addressed_is_skipped(store, tmp_path):
    """Every caller of the listing turns a name back into a path. An entry that
    cannot round-trip would be handed to `read_file`, raise, and take the whole
    listing down with it — hiding every other file in the session."""
    directory = tmp_path / SESSION / flows.FILES_DIR
    directory.mkdir(parents=True)
    (directory / ".hidden").write_bytes(b"x")
    (directory / "real.pdf").write_bytes(b"x")
    assert [f["name"] for f in store.files(SESSION)] == ["real.pdf"]


# ---- three sections, not one list --------------------------------------------

# The behaviour these used to hold — a name in both places, the newest-first
# order, keeping being a copy — is `test_file_sections.py` territory now: two
# folders never merge, so "which one wins a collision" is not a question
# `sections` is ever asked. What is still this module's to prove is the part
# that only makes sense with a session record in front of it: a Grid failure
# is surfaced, a reaped browser is never dialled, and a live one still is.


def test_a_grid_failure_is_not_hidden(store):
    """The browser being gone costs no Grid call at all, so an error from one we
    were told is live is a real fault. Swallowing it would make a broken Grid
    look like an empty session."""

    class Broken(FakeGrid):
        def files(self, session_id):
            raise RuntimeError("grid is down")

    with pytest.raises(RuntimeError):
        files.sections(
            FakeActions(Broken()), Sessions(session_id="abc", live=True), store, TOKEN, SESSION
        )


def test_the_listing_ignores_a_browser_the_grid_has_reaped(store):
    """A reaped browser stays in the session record until something refreshes
    it, and `describe` reports that id beside `live: false`. Trusting it dials
    the Grid for a browser that is gone and fails the whole listing — in exactly
    the state a kept file exists to survive."""
    store.write_file(SESSION, "kept.pdf", b"x")

    class Reaped(FakeGrid):
        def files(self, session_id):
            raise AssertionError(f"dialled the Grid for reaped {session_id!r}")

    got = files.sections(
        FakeActions(Reaped()), Sessions(session_id="dead", live=False), store, TOKEN, SESSION
    )
    assert [f["name"] for f in got["files"]] == ["kept.pdf"]
    assert got["downloads"] == []


def test_the_listing_still_uses_a_browser_that_is_live(store):
    got = files.sections(
        FakeActions(FakeGrid(DOWNLOADS)),
        Sessions(session_id="abc", live=True),
        store,
        TOKEN,
        SESSION,
    )
    assert [f["name"] for f in got["downloads"]] == ["report.pdf", "shot.png"]


def test_with_no_store_only_downloads_are_listed():
    got = files.sections(
        FakeActions(FakeGrid(DOWNLOADS)),
        Sessions(session_id="abc", live=True),
        None,
        TOKEN,
        SESSION,
    )
    assert got["files"] == []
    assert [f["name"] for f in got["downloads"]] == ["report.pdf", "shot.png"]


# ---- deleting -----------------------------------------------------------------


def test_deleting_a_kept_file_is_idempotent(store):
    store.write_file(SESSION, "report.pdf", b"x")
    assert files.delete_one(store, SESSION, "report.pdf")["deleted"] is True
    assert files.delete_one(store, SESSION, "report.pdf")["deleted"] is False


def test_deleting_refuses_when_keeping_is_off():
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        files.delete_one(None, "", "report.pdf")


# ---- the two surfaces --------------------------------------------------------

# What answers each endpoint over MCP: a resource for a read, a tool for an
# action (§F3.6). Written out rather than derived, so a typo in either table
# cannot make the parity test agree with itself.
MCP_FOR = {
    "list": ("resource", files.ROOT_URI),
    "screenshots": ("resource", files.FOLDER_URI[files.SCREENSHOTS]),
    "downloads": ("resource", files.FOLDER_URI[files.DOWNLOADS]),
    "keep": ("tool", files.KEEP_TOOL),
}


async def test_every_file_action_is_reachable_from_both_surfaces(
    kept_server, client, monkeypatch
):
    """The promise `test_surfaces.py` makes for browser actions, kept for these.

    They are subtracted from that module's assertions because they are not
    browser actions — they act on a session's files, live under /files, and have
    their own route table. So this is where the one-to-one is actually held, and
    subtracting them there is a narrowing rather than an excuse.

    A read is a resource over MCP and an action is a tool; either way both
    doors exist.
    """
    assert set(MCP_FOR) == set(files.FILE_ENDPOINTS), (
        "an endpoint has nothing named for it over MCP, or the other way round"
    )
    names = {t.name for t in await kept_server.mcp.list_tools()}
    uris = {str(r.uri) for r in await kept_server.mcp.list_resources()}
    for path, (kind, target) in MCP_FOR.items():
        method, template = files.FILE_ROUTES[path]
        route = (
            f"/files{template}"
            .replace("{folder}", "downloads")
            .replace("{name}", "report.pdf")
        )
        assert target in (names if kind == "tool" else uris), f"{route} has no {kind}"
        # 401 rather than 404: the route exists and refused the credential,
        # which is what proves it is bound.
        assert getattr(client, method)(route).status_code == 401, f"{target} has no route"


async def test_the_file_actions_are_not_counted_as_browser_actions(kept_server):
    """They are a layer above /browser, like flows: about a session's files
    rather than about driving a page. The browser route table must not grow."""
    assert files.KEEP_TOOL not in ENDPOINTS.values()


async def test_the_keep_tool_declares_honest_annotations(kept_server):
    """An unannotated tool is advertised as destructive, and this one is not.
    Keeping copies a file and destroys nothing."""
    tools = {t.name: t for t in await kept_server.mcp.list_tools()}
    keep = tools[files.KEEP_TOOL].annotations
    assert keep.title and keep.read_only_hint is False
    assert keep.destructive_hint is False, "keeping a file destroys nothing"
    # False, not True: a screenshot moves, so keeping it twice is refused the
    # second time (there is no longer a screenshot to move), and a download
    # kept twice lands beside itself as `name (1)` rather than replacing
    # anything — neither is "repeat me and nothing changes" (§F4.7).
    assert keep.idempotent_hint is False


async def test_deleting_a_kept_file_is_not_offered_to_an_agent(
    kept_server, monkeypatch
):
    """Keeping is one-way on purpose. An agent is not the thing that runs out of
    disk, and a one-way verb is a simpler promise than a reversible one whose
    meaning depends on whether a browser still exists. Removing a kept file is
    an operator action on the admin surface — which is also why there is no
    endpoint: one without a tool is the half-a-capability this project forbids.
    """
    names = {t.name for t in await kept_server.mcp.list_tools()}
    assert "delete_file" not in names
    assert await kept_server.mcp.get_tool("delete_file") is None
    assert "delete" not in files.FILE_ENDPOINTS


def test_the_endpoints_need_the_token(client):
    for method, template in files.FILE_ROUTES.values():
        route = (
            f"/files{template}"
            .replace("{folder}", "downloads")
            .replace("{name}", "report.pdf")
        )
        call = getattr(client, method)
        assert call(route).status_code == 401, route
        assert (
            call(route, headers={"Authorization": "Bearer nope"}).status_code == 401
        ), route


def test_keep_then_list_over_http(client, live):
    with (
        patch.object(browser.Grid, "files", return_value=DOWNLOADS),
        patch.object(browser.Grid, "read_file", return_value=b"PDF"),
    ):
        kept = client.put("/files/downloads/report.pdf/kept", headers=AUTH)
        assert kept.status_code == 200, kept.text
        assert kept.json()["uri"] == "session://files/report.pdf"

        body = client.get("/files", headers=AUTH).json()
    assert [f["name"] for f in body["files"]] == ["report.pdf"]
    assert body["session"] == SESSION
    # The Grid still reports both — keeping a download is a copy, so the
    # original stays exactly where it was until the browser ends (§F1.10).
    downloads = body["folders"][1]
    assert downloads["name"] == "downloads" and downloads["count"] == 2


def test_an_unusable_name_is_a_400_over_http(client, live):
    """A leading dot rather than a traversal: a name with a slash in it never
    reaches the handler, because the path pattern does not match one — which is
    a refusal too, just a 404 shaped one. `valid_file_name` is what refuses the
    rest, and it is unit-tested on its own."""
    response = client.put("/files/downloads/.hidden/kept", headers=AUTH)
    assert response.status_code == 400
    assert "file name" in response.json()["error"]


def test_a_grid_that_says_no_is_not_a_500(client, live):
    """`Grid.files` and `read_file` report a refusal through
    `raise_for_status`, which raises `requests.HTTPError` — not a connection
    failure, and not previously classified. A reaped browser therefore answered
    500 while the published /files contract promised 404, which tells a client
    to stop retrying something it could have fixed by opening a browser."""
    gone = requests.Response()
    gone.status_code = 404
    with patch.object(
        browser.Grid, "read_file", side_effect=requests.HTTPError(response=gone)
    ):
        response = client.put("/files/downloads/report.pdf/kept", headers=AUTH)
    assert response.status_code == 404


def test_a_grid_refusal_does_not_echo_the_grid_url():
    """`raise_for_status` formats its message with the full request URL, and
    GRID_URL may carry credentials in its userinfo — so returning that string
    hands the Grid's credential to whoever made the request, and logs it."""
    response = requests.Response()
    response.status_code = 404
    response.url = "http://user:hunter2@grid.internal:4444/session/abc/se/files"
    exc = requests.HTTPError(
        f"404 Client Error: Not Found for url: {response.url}", response=response
    )
    text = errors.message(exc)
    assert "hunter2" not in text and "grid.internal" not in text
    assert "404" in text, "the useful half survived"


async def test_the_file_listing_never_opens_a_browser(kept_server, named_caller):
    """It cannot call `sessions.resolve`: that opens a browser when the record
    has none, and a listing that opened one would be the leak the status
    resource refuses to be. It asks `browser` instead, which answers "" — and
    the kept files still list, which is what keeping one is for."""
    tool = await kept_server.mcp.get_tool(files.FILES_TOOL)
    opened_before = kept_server.actions.grid
    listing = tool.fn()
    assert listing["files"] == []
    assert kept_server.actions.grid is opened_before


@pytest.mark.parametrize(
    "status,expected",
    [(404, 404), (400, 400), (422, 400), (500, 503), (502, 503), (None, 503)],
)
def test_the_grids_own_status_decides_what_its_refusal_means(status, expected):
    """Its 404 has the same diagnosis and the same fix as a dead session; its
    5xx is worth retrying; anything else it refuses is the request's problem."""
    response = None
    if status is not None:
        response = requests.Response()
        response.status_code = status
    assert errors.status_for(requests.HTTPError(response=response)) == expected


# ---- the name a caller needs in order to keep anything ----------------------


async def test_a_saved_screenshot_tells_the_caller_what_it_was_called(
    live, named_caller
):
    """`keep_file` takes a name, and this is the tool that most often makes one.

    Chrome deduplicates, so `shot.png` can land as `shot (1).png` and no caller
    can derive it. Returning the image alone left the one thing you have to know
    obtainable only by listing the files and guessing which entry was yours —
    while the HTTP endpoint had been returning it all along (§F1.37).
    """
    entry = {"name": "shot (1).png", "size": 3, "creationTime": 1}
    tool = await live.mcp.get_tool("screenshot")
    with patch.object(
        live.actions,
        "screenshot",
        return_value={"image": "", "url": "https://x/", "file": entry},
    ):
        result = tool.fn(save=True)
    assert result.structured_content == {"file": entry}
    # And still an image: the point is to add the name, not to stop showing it.
    assert result.content and result.content[0].type == "image"


async def test_an_unsaved_screenshot_is_still_just_an_image(live, named_caller):
    """Nothing was stored, so there is no name to carry and no reason to wrap
    the result in anything. Storing is now the default, so this is the caller
    that asked not to (§F2.9)."""
    tool = await live.mcp.get_tool("screenshot")
    with patch.object(
        live.actions,
        "screenshot",
        return_value={"image": "", "url": "https://x/"},
    ):
        result = tool.fn(save=False)
    assert not hasattr(result, "structured_content")


async def test_the_tool_saves_by_default_too(live, named_caller):
    """Through the tool, not the action: the default has to travel."""
    entry = {"name": "screenshot.png", "size": 3, "creationTime": 1}
    asked = {}

    def fake(session_id, **kwargs):
        asked.update(kwargs)
        return {"image": "", "url": "https://x/", "file": entry}

    tool = await live.mcp.get_tool("screenshot")
    with patch.object(live.actions, "screenshot", fake):
        result = tool.fn()
    assert asked["save"] is True
    assert result.structured_content == {"file": entry}
    assert result.content and result.content[0].type == "image"


async def test_an_mcp_caller_is_told_why_the_file_is_missing(live, named_caller):
    """The HTTP surface returns file_error; a tool caller used to get the image
    and no explanation, and would wait for a name that is never coming."""
    tool = await live.mcp.get_tool("screenshot")
    with patch.object(
        live.actions,
        "screenshot",
        return_value={"image": "", "url": "https://x/", "file_error": "blocked"},
    ):
        result = tool.fn()
    assert result.structured_content == {"file_error": "blocked"}
    assert result.content and result.content[0].type == "image"


# ---- the admin surface -------------------------------------------------------


def test_the_admin_api_keeps_a_file(client, live):
    with (
        patch.object(browser.Grid, "read_file", return_value=b"PDF"),
        patch.object(browser.Grid, "files", return_value=DOWNLOADS),
    ):
        response = client.post(
            f"/admin/sessions/{KEY}/files/report.pdf/keep", headers=AUTH
        )
    assert response.status_code == 200, response.text
    assert live.flows.read_file(SESSION, "report.pdf") == b"PDF"


def test_the_admin_keep_and_delete_need_the_token(client, live):
    assert client.post(f"/admin/sessions/{KEY}/files/x.pdf/keep").status_code == 401
    assert client.delete(f"/admin/sessions/{KEY}/files/x.pdf").status_code == 401


def test_the_admin_api_deletes_a_kept_file(client, live):
    live.flows.write_file(SESSION, "report.pdf", b"PDF")
    response = client.delete(f"/admin/sessions/{KEY}/files/report.pdf", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert live.flows.files(SESSION) == []


def test_deleting_a_download_does_nothing_rather_than_lying(client, live):
    """There is no per-file delete for a download — the Grid offers none — so a
    trash on one would be a button that cannot work. The UI only draws it on
    kept files; the API says plainly that nothing was removed."""
    with patch.object(browser.Grid, "files", return_value=DOWNLOADS):
        body = client.delete(
            f"/admin/sessions/{KEY}/files/report.pdf", headers=AUTH
        ).json()
        assert body["deleted"] is False
        listed = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()
    assert "report.pdf" in [f["name"] for f in listed["files"]], "the download stayed"


def test_clearing_downloads_leaves_kept_files_alone(client, live):
    """The objection that condemned the old Clear files button — that it takes
    the kept ones with it — cannot happen once keeping is a copy. This is what
    makes the button safe to offer at all (§F1.10)."""
    live.flows.write_file(SESSION, "report.pdf", b"PDF")
    with (
        patch.object(browser.Grid, "files", return_value=[]),
        patch.object(browser.Grid, "clear_files") as clear,
    ):
        assert client.delete(f"/admin/sessions/{KEY}/files", headers=AUTH).status_code == 200
        body = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()
    clear.assert_called_once_with("abc")
    assert [f["name"] for f in body["files"]] == ["report.pdf"]
    assert body["files"][0]["kept"] is True


def test_the_download_names_are_reported_unmerged(client, live):
    """What Clear downloads removes is the Grid's whole store, and the merged
    listing cannot describe it: a download loses to a kept file of the same
    name and disappears from `files` while staying very much on the Grid. A
    confirmation built from the merge would name one of the two files it takes,
    so the response carries the Grid's own list beside the merged one."""
    live.flows.write_file(SESSION, "report.pdf", b"kept copy")
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "files", return_value=DOWNLOADS),
    ):
        body = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()

    # The merge hides the shadowed download, correctly — one tile per name.
    assert [f["name"] for f in body["files"]] == ["report.pdf", "shot.png"]
    assert [f["kept"] for f in body["files"]] == [True, False]
    # The clear list does not.
    assert body["downloads"] == ["report.pdf", "shot.png"]


def test_a_session_with_no_browser_has_nothing_to_clear(client, kept_server):
    """No browser, no Grid store — and the kept files are not downloads, so the
    list stays empty rather than offering to clear something it cannot."""
    kept_server.sessions.store.set("idle", SessionRecord(session_id=""))
    kept_server.flows.write_file("idle", "report.pdf", b"PDF")
    body = client.get("/admin/sessions/idle/files", headers=AUTH).json()
    assert body["downloads"] == []


def test_a_detached_session_still_lists_its_kept_files(client, kept_server):
    """It has no browser and therefore no downloads — but keeping exists exactly
    so that is not the end of the answer."""
    kept_server.sessions.store.set("idle", SessionRecord(session_id=""))
    kept_server.flows.write_file("idle", "report.pdf", b"PDF")
    body = client.get("/admin/sessions/idle/files", headers=AUTH).json()
    assert [f["name"] for f in body["files"]] == ["report.pdf"]
    assert body["session"]["attached"] is False


def test_the_admin_listing_survives_a_reaped_browser(client, live):
    """The record still names a browser; the Grid no longer has it. That is an
    ordinary state — the Grid reaps idle browsers — and the session header is
    the thing that actually checked, so the listing follows its verdict rather
    than the record's optimism. Dialling the dead id would 502 the whole view
    in the exact case kept files exist for."""
    live.flows.write_file(SESSION, "kept.pdf", b"x")
    with (
        patch.object(browser.Grid, "is_alive", return_value=False),
        patch.object(browser.Grid, "sessions", return_value=[]),
        patch.object(
            browser.Grid,
            "files",
            side_effect=AssertionError("dialled a browser the Grid had reaped"),
        ),
    ):
        body = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()
    assert [f["name"] for f in body["files"]] == ["kept.pdf"]
    assert body["session"]["live"] is False


def test_a_grid_outage_is_an_error_not_an_empty_download_list(client, live):
    """The other half of the rule above, and the reason it asks `is_alive`
    rather than reading the header's `live`: that flag is false both when the
    Grid says the browser is gone AND when the Grid could not be read at all.
    Treating an outage as "detached" would render a confident empty list for a
    session that may have had twenty downloads."""
    live.flows.write_file(SESSION, "kept.pdf", b"x")
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "sessions", return_value=[]),
        patch.object(
            browser.Grid, "files", side_effect=requests.ConnectionError("grid down")
        ),
    ):
        response = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH)
    assert response.status_code == 502


BAD_KEY = "my bot"


def test_a_session_whose_name_is_not_a_directory_keeps_nothing(client, kept_server):
    """`session_for` hands such a caller `global`, which is right for a browser
    — the key is opaque there — and catastrophic for storage: this session's
    private file would land in the shared library, where every unnamed caller
    can list it and fetch it through a signed URL. It is the same mistake E6
    fixed for flows, arriving on the file side through the admin surface."""
    kept_server.sessions.store.set(BAD_KEY, SessionRecord(session_id="abc"))
    with patch.object(browser.Grid, "read_file", return_value=b"PDF"):
        response = client.post(
            f"/admin/sessions/{quote(BAD_KEY, safe='')}/files/report.pdf/keep",
            headers=AUTH,
        )
    assert response.status_code == 400
    assert "cannot keep files" in response.json()["error"]
    assert kept_server.flows.files(flows.GLOBAL_SESSION) == [], "it leaked to global"


def test_such_a_session_shows_unknown_counts_not_the_shared_librarys(
    client, kept_server
):
    """Borrowing `global`'s numbers would tell an operator this session has a
    file and a flow it has no way to reach."""
    kept_server.flows.write_file(flows.GLOBAL_SESSION, "shared.pdf", b"x")
    kept_server.flows.save(flows.GLOBAL_SESSION, "shared", {"steps": []})
    kept_server.sessions.store.set(BAD_KEY, SessionRecord(session_id=""))
    with patch.object(browser.Grid, "sessions", return_value=[]):
        body = client.get("/admin/sessions", headers=AUTH).json()
    row = next(r for r in body["sessions"] if r["key"] == BAD_KEY)
    assert row["kept_count"] is None and row["flows_count"] is None


def test_the_session_list_counts_flows_and_kept_files(client, live):
    """The count is the reason to click into a session, and a detached one still
    has things worth counting."""
    live.flows.write_file(SESSION, "report.pdf", b"PDF")
    live.flows.save(SESSION, "login", {"steps": []})
    with (
        patch.object(browser.Grid, "sessions", return_value=[]),
        patch.object(browser.Grid, "files", return_value=[]),
    ):
        body = client.get("/admin/sessions", headers=AUTH).json()
    row = next(r for r in body["sessions"] if r["key"] == KEY)
    assert row["kept_count"] == 1
    assert row["flows_count"] == 1
    assert row["files_count"] == 1


# ---- the signed link ---------------------------------------------------------


def test_a_kept_link_is_bound_to_its_own_route(client):
    """A link to a download is not a link to a kept file of the same name, and
    the reverse. They are different bytes under different lifetimes."""
    download = links.file_url("abc", "report.pdf", TOKEN)
    kept = links.kept_url(SESSION, "report.pdf", TOKEN)
    assert download != kept
    query = kept.split("?", 1)[1]
    assert client.get(f"/files/abc/report.pdf?{query}").status_code == 403


def test_the_kept_route_serves_a_signed_file(client, live):
    live.flows.write_file(SESSION, "shot.png", b"\x89PNG\r\n\x1a\n")
    response = client.get(links.kept_url(SESSION, "shot.png", TOKEN))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert "inline" in response.headers["content-disposition"]


def test_the_disposition_folds_what_a_header_cannot_carry():
    """A header is encoded as latin-1 and a file name is not ours to choose, so
    `emoji😊.txt` raised while the response was being built and a name with a
    quote produced a malformed header. Neither name is invalid; both were
    unfetchable. RFC 6266 says it twice instead."""
    value = admin.disposition('rapport été "x".pdf')
    value.encode("latin-1")  # raised before the fix
    assert value.count('"') == 2, "the quoted filename ends early"
    assert "filename*=UTF-8''" in value, "the real name is lost"


def test_a_name_no_header_could_carry_is_still_fetchable(client, live):
    name = 'emoji😊 "q".txt'
    live.flows.write_file(SESSION, name, b"x")
    response = client.get(links.kept_url(SESSION, name, TOKEN))
    assert response.status_code == 200
    value = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in value
    quoted = value.split(";")[1]
    assert "😊" not in quoted and '"q"' not in quoted


def test_a_control_character_cannot_reach_the_header(client, live):
    """Folding rather than dropping also closes the header-injection route a
    raw CR or LF would open, since the name reaches this from a URL path."""
    assert "\r" not in admin.disposition("a\r\nX-Evil: 1.txt")
    assert "\n" not in admin.disposition("a\r\nX-Evil: 1.txt")


def test_the_kept_route_refuses_without_a_signature(client):
    assert client.get(f"/kept/{SESSION}/shot.png").status_code == 403
    assert client.get(f"/kept/{SESSION}/shot.png?exp=1&sig=x").status_code == 403


def test_a_kept_file_that_is_not_there_is_a_404(client, live):
    response = client.get(links.kept_url(SESSION, "missing.pdf", TOKEN))
    assert response.status_code == 404


# ---- the published contract --------------------------------------------------


@pytest.fixture
async def spec(kept_server):
    return await build_spec(kept_server.mcp, ENDPOINTS, "", authenticated=True)


async def test_every_file_endpoint_is_in_the_published_contract(spec):
    """These paths are written by hand, so the list is held against the one the
    server actually binds — the guard the multipart upload schema lacked until
    it had already drifted."""
    published = {
        (method, path)
        for path, operations in spec["paths"].items()
        for method in operations
        if path.startswith("/files")
    }
    assert published == {
        (method, f"/files{template}")
        for method, template in files.FILE_ROUTES.values()
    }


async def test_the_file_endpoints_are_tagged_apart(spec):
    assert "files" in {t["name"] for t in spec["tags"]}
    for path, operations in spec["paths"].items():
        if path.startswith("/files"):
            for method, operation in operations.items():
                assert operation["tags"] == ["files"], f"{method} {path}"


async def test_every_file_response_schema_it_references_exists(spec):
    """A $ref to a schema nobody defined renders as a blank box in every docs UI
    and fails a strict linter."""
    defined = set(spec["components"]["schemas"])
    for path, operations in spec["paths"].items():
        if not path.startswith("/files"):
            continue
        for method, operation in operations.items():
            for block in operation["responses"].values():
                ref = block["content"]["application/json"]["schema"].get("$ref")
                if ref:
                    assert ref.split("/")[-1] in defined, f"{method} {path} -> {ref}"


async def test_every_file_operation_declares_the_grids_failure_modes(spec):
    """Both remaining operations dial the Grid, so both can fail its way — and
    the contract has to say so, or a generated client writes no handling for the
    reaped browser it will certainly meet."""
    for method, template in files.FILE_ROUTES.values():
        responses = spec["paths"][f"/files{template}"][method]["responses"]
        assert set(responses) == {"200", "400", "401", "404", "500", "503"}, template


def test_the_session_row_counts_distinct_files_not_both_lists(client, live):
    """Keeping is a copy, so a kept file and its download share a name. Adding
    the two lengths counted it twice — the list said 5 where the grid below it
    showed 3 — and the page keys its refresh off this number, so a wrong count
    was a wrong change signal as well as a wrong label."""
    live.flows.write_file(SESSION, "report.pdf", b"kept copy")
    with (
        patch.object(browser.Grid, "sessions", return_value=[{"session_id": "abc"}]),
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "files", return_value=DOWNLOADS),
    ):
        row = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]
        body = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()

    # Two downloads, one of them kept under the same name: two files, not three.
    assert row["files_count"] == 2
    assert row["kept_count"] == 1
    assert row["files_count"] == len(body["files"]), "the row and the grid disagree"


def test_the_file_stamp_notices_a_kept_copy_being_deleted(client, live):
    """A count cannot tell these apart. `report.pdf` exists as a download and
    as a kept copy; deleting the kept one leaves the union at two files while
    the grid switches that tile from a pin to a bubble — different marks, a
    different URL, a different lifetime. The page would have gone on showing a
    kept file that no longer existed."""
    live.flows.write_file(SESSION, "report.pdf", b"kept copy")
    with (
        patch.object(browser.Grid, "sessions", return_value=[{"session_id": "abc"}]),
        patch.object(browser.Grid, "files", return_value=DOWNLOADS),
    ):
        before = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]
        live.flows.delete_file(SESSION, "report.pdf")
        after = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]

    assert before["files_count"] == after["files_count"], "the count is why it is not enough"
    assert before["files_rev"] != after["files_rev"]


def test_the_file_stamp_cannot_be_forged_by_a_files_own_name(client, live):
    """`valid_file_name` permits `:` and `;` on purpose — the site's
    Content-Disposition chose the name, not us — so a delimiter-joined token is
    not injective. A kept file called `a:d;b` and the pair (download `a`, kept
    `b`) both flatten to `a:d;b:k`: two different grids, one token, and the
    second one never repaints."""
    live.flows.write_file(SESSION, "a:d;b", b"x")
    with (
        patch.object(browser.Grid, "sessions", return_value=[{"session_id": "abc"}]),
        patch.object(browser.Grid, "files", return_value=[]),
    ):
        forged = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]

    live.flows.delete_file(SESSION, "a:d;b")
    live.flows.write_file(SESSION, "b", b"x")
    with (
        patch.object(browser.Grid, "sessions", return_value=[{"session_id": "abc"}]),
        patch.object(
            browser.Grid, "files",
            return_value=[{"name": "a", "size": 1, "creationTime": 1}],
        ),
    ):
        real = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]

    # The delimiter-joined form these replaced: same token for both grids.
    def joined(row_kept, row_downloads):
        return ";".join(
            sorted(f"{n}:{'k' if k else 'd'}" for n, k in row_kept + row_downloads)
        )

    assert joined([("a:d;b", True)], []) == joined([("b", True)], [("a", False)])
    # And the tokens actually served, which do not. `filesStamp` is built from
    # this alone — the count is not in it — so a collision here is a panel that
    # never repaints, whatever the counts happen to be.
    assert forged["files_rev"] != real["files_rev"]


# ---- giving a kept file back to a page ---------------------------------------

# `read_kept` is gone with the merged listing it served; `read_file` reading a
# file by its URI, and refusing a name nobody has, is `test_file_sections.py`
# territory now (`test_any_file_can_be_read_back_by_its_uri`,
# `test_reading_a_name_nobody_has_is_the_callers_mistake`). What is left here
# is `upload_file`'s own behaviour, which reads through `actions.read_file`
# rather than calling the domain function directly.


@pytest.mark.parametrize(
    "uri",
    ["session://files/export.csv", "session://files/screenshots/shot.png"],
)
def test_upload_sends_any_file_named_by_its_uri(actions, uri, monkeypatch):
    """Through `upload_file`, not through the helper: the action is where the
    four sources are told apart and where the name defaults. A screenshot's
    uri works exactly like a Files uri — the action never distinguishes them,
    `read_file` does."""
    sent = {}

    class _Element:
        def send_keys(self, path):
            from pathlib import Path

            sent["name"] = Path(path).name
            sent["bytes"] = Path(path).read_bytes()

    class _Driver:
        current_url = "https://example.test/upload"
        title = "Upload"

        def execute_script(self, *_a, **_k):
            return None

    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Driver())
    monkeypatch.setattr(browser, "accept_local_files", lambda _d: None)
    monkeypatch.setattr(
        "kubed.selenium_flow.core.browser.wait_for_element", lambda *a, **k: _Element()
    )
    asked = {}

    def reader(given_uri, session=None):
        asked["uri"], asked["session"] = given_uri, session
        return "x.png", b"..."

    actions.read_file = reader

    result = actions.upload_file("abc", selector={"css": "input[type=file]"}, file=uri)

    assert sent["bytes"] == b"..."
    assert sent["name"] == "x.png", "the file's own name is the default filename"
    assert result["filename"] == "x.png"
    assert asked == {"uri": uri, "session": None}, (
        "an MCP caller names no library - its own key answers"
    )


def test_an_http_caller_can_name_the_library_its_file_was_kept_in(
    actions, monkeypatch
):
    """`/files/keep` takes `session` in its body, and `/browser/upload` had no
    way to say the same thing — so a caller that kept a file into `desktop`
    landed in `global` when it tried to upload it back (Copilot, #31). The HTTP
    surface is always explicit; this is that contract, on this action."""
    asked = {}

    class _Element:
        def send_keys(self, _path):
            pass

    class _Driver:
        current_url = "https://example.test/upload"
        title = "Upload"

        def execute_script(self, *_a, **_k):
            return None

    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Driver())
    monkeypatch.setattr(browser, "accept_local_files", lambda _d: None)
    monkeypatch.setattr(
        "kubed.selenium_flow.core.browser.wait_for_element", lambda *a, **k: _Element()
    )

    def reader(uri, session=None):
        asked["session"] = session
        return "export.csv", b"x"

    actions.read_file = reader
    actions.upload_file(
        "abc",
        selector={"css": "input"},
        file="session://files/export.csv",
        session="desktop",
    )
    assert asked["session"] == "desktop"


def test_the_upload_endpoint_accepts_the_library_name():
    """Through the route table, not the action: `routes.py` derives the body it
    accepts from the signature, so a parameter the action grew is only reachable
    if it is really there."""
    import inspect

    from kubed.selenium_flow.core.actions import Actions

    accepted = set(inspect.signature(Actions.upload_file).parameters)
    assert {"file", "session"} <= accepted


def test_a_kept_upload_is_refused_when_there_is_nowhere_to_keep(actions):
    """Flows off means no file store, so `file` names something that cannot
    exist. Refused with the three sources that do work."""
    with pytest.raises(ValueError, match="not available"):
        actions.upload_file(
            "abc", selector={"css": "input"}, file="session://files/export.csv"
        )


def test_only_one_source_may_be_given(actions):
    with pytest.raises(ValueError, match="only one of"):
        actions.upload_file(
            "abc",
            selector={"css": "input"},
            text="hi",
            file="session://files/export.csv",
        )


def test_upload_says_file_is_a_uri(actions):
    with pytest.raises(ValueError, match="file"):
        actions.upload_file("abc", selector={"css": "input"})


# The `kept` flag is gone — the folder says that now — and `keep_with` on a
# download versus its absence in Files is `test_file_sections.py` territory
# (`test_every_entry_carries_its_own_uri_and_no_kept_flag`,
# `test_a_file_in_files_has_nothing_to_keep`).
