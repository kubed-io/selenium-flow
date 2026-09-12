"""Kept files: the copy out of the browser, and everything that follows from it.

The Grid's file API is **list, read-one, delete-all** — there is no write and no
per-file delete. Almost every rule tested here falls out of that one fact, so
they are worth naming together:

- keeping is a **copy**, because the original cannot be removed;
- only a **kept** file has a per-file delete, because only kept files are ours;
- **clearing the downloads is therefore safe**, because kept files are somewhere
  else by definition — which is the whole reason that button can exist;
- and the listing **survives the browser**, because half of it always did.

The Grid is never dialled. What is asserted is the part this server decides.
"""

from unittest.mock import patch
from urllib.parse import quote

import pytest
import requests
from starlette.testclient import TestClient

from kubed.selenium_flow import admin, browser, errors, files, flows, links
from kubed.selenium_flow import resources as resources_module
from kubed.selenium_flow.openapi import build_spec
from kubed.selenium_flow.routes import ENDPOINTS
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.store import SessionRecord

from .conftest import NAMED, TOKEN

pytestmark = pytest.mark.unit

KEY = "named:desktop"
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
    return TestClient(kept_server.mcp.http_app())


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
    """A session manager double: only what `listing` actually reads."""

    def __init__(self, **status):
        self.status = status

    def describe(self):
        return dict(self.status)

    def key(self):
        return NAMED

    def library_key(self):
        # Which library a caller owns does not depend on browser persistence.
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


def test_keeping_asks_the_grid_for_the_name_it_was_given(store):
    grid = FakeGrid([], b"x")
    files.keep_one(FakeActions(grid), store, SESSION, "abc", " report.pdf ")
    assert grid.reads == [("abc", " report.pdf ")]


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


# ---- one list, out of two sources -------------------------------------------


def test_both_halves_appear_in_one_list(store):
    store.write_file(SESSION, "kept.pdf", b"x")
    actions = FakeActions(FakeGrid(DOWNLOADS))
    listed = files.merged(actions, store, SESSION, "abc", TOKEN)
    assert {f["name"] for f in listed} == {"report.pdf", "shot.png", "kept.pdf"}
    assert {f["name"]: f["kept"] for f in listed} == {
        "report.pdf": False,
        "shot.png": False,
        "kept.pdf": True,
    }


def test_a_kept_file_wins_a_name_collision(store):
    """The same report.pdf downloaded twice, or kept and then downloaded again.
    The kept one wins because it is the one that will still be there — and the
    only one with a per-file delete."""
    store.write_file(SESSION, "report.pdf", b"x")
    actions = FakeActions(FakeGrid(DOWNLOADS))
    listed = files.merged(actions, store, SESSION, "abc", TOKEN)
    assert [f["name"] for f in listed].count("report.pdf") == 1
    report = next(f for f in listed if f["name"] == "report.pdf")
    assert report["kept"] is True
    assert "/kept/" in report["url"], "the kept copy is what the link points at"


def test_the_list_is_newest_first(store):
    actions = FakeActions(FakeGrid(DOWNLOADS))
    listed = files.merged(actions, store, SESSION, "abc", TOKEN)
    assert [f["name"] for f in listed] == ["report.pdf", "shot.png"]


def test_the_list_survives_the_browser(store):
    """The point of keeping one. A listing that emptied when the Grid reaped a
    browser would make the durable half look lost."""
    store.write_file(SESSION, "kept.pdf", b"x")
    actions = FakeActions(FakeGrid(DOWNLOADS))
    listed = files.merged(actions, store, SESSION, "", TOKEN)
    assert [f["name"] for f in listed] == ["kept.pdf"]


def test_a_grid_failure_is_not_hidden(store):
    """The browser being gone costs no Grid call at all, so an error from one we
    were told is live is a real fault. Swallowing it would make a broken Grid
    look like an empty session."""

    class Broken(FakeGrid):
        def files(self, session_id):
            raise RuntimeError("grid is down")

    with pytest.raises(RuntimeError):
        files.merged(FakeActions(Broken()), store, SESSION, "abc", TOKEN)


def test_the_listing_ignores_a_browser_the_grid_has_reaped(store):
    """A reaped browser stays in the session record until something refreshes
    it, and `describe` reports that id beside `live: false`. Trusting it dials
    the Grid for a browser that is gone and fails the whole listing — in exactly
    the state kept files exist to survive."""
    store.write_file(SESSION, "kept.pdf", b"x")

    class Reaped(FakeGrid):
        def files(self, session_id):
            raise AssertionError(f"dialled the Grid for reaped {session_id!r}")

    listed = files.listing(
        FakeActions(Reaped()), Sessions(session_id="dead", live=False), store, TOKEN
    )
    assert [f["name"] for f in listed["files"]] == ["kept.pdf"]


def test_the_listing_still_uses_a_browser_that_is_live(store):
    listed = files.listing(
        FakeActions(FakeGrid(DOWNLOADS)),
        Sessions(session_id="abc", live=True),
        store,
        TOKEN,
    )
    assert [f["name"] for f in listed["files"]] == ["report.pdf", "shot.png"]


def test_an_explicitly_passed_browser_is_trusted(store):
    """The caller owns that id and may well have opened it elsewhere, so it is
    not second-guessed against a status this server holds."""
    listed = files.listing(
        FakeActions(FakeGrid(DOWNLOADS)),
        Sessions(session_id=None, live=False),
        store,
        TOKEN,
        session_id="abc",
    )
    assert len(listed["files"]) == 2


def test_with_no_store_only_downloads_are_listed():
    actions = FakeActions(FakeGrid(DOWNLOADS))
    listed = files.merged(actions, None, "", "abc", TOKEN)
    assert [f["name"] for f in listed] == ["report.pdf", "shot.png"]
    assert all(f["kept"] is False for f in listed)


# ---- keeping and deleting ----------------------------------------------------


def test_keeping_copies_the_bytes_out_of_the_grid(store):
    grid = FakeGrid(DOWNLOADS, b"PDF bytes")
    kept = files.keep_one(FakeActions(grid), store, SESSION, "abc", "report.pdf")
    assert kept["kept"] is True and kept["session"] == SESSION
    assert store.read_file(SESSION, "report.pdf") == b"PDF bytes"
    assert grid.reads == [("abc", "report.pdf")]


def test_keeping_is_a_copy_and_never_a_move(store):
    """The Grid has no per-file delete, so the original necessarily stays. This
    is not a choice, and the UI says so by keeping the download visible."""
    grid = FakeGrid(DOWNLOADS)
    files.keep_one(FakeActions(grid), store, SESSION, "abc", "report.pdf")
    assert [e["name"] for e in grid.files("abc")] == ["report.pdf", "shot.png"]


def test_keeping_refuses_when_there_is_nowhere_to_keep():
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        files.keep_one(FakeActions(FakeGrid()), None, "", "abc", "report.pdf")


def test_keeping_refuses_a_bad_name_before_dialling_the_grid(store):
    """An unusable name should cost nothing and say what was wrong with it,
    rather than surfacing as a download failure from the Grid."""
    grid = FakeGrid(DOWNLOADS)
    with pytest.raises(flows.InvalidName):
        files.keep_one(FakeActions(grid), store, SESSION, "abc", "../passwd")
    assert grid.reads == [], "the Grid was dialled for a name we had already refused"


def test_keeping_needs_a_browser_to_copy_from(store):
    with pytest.raises(ValueError, match="session_id is required"):
        files.keep_one(FakeActions(FakeGrid()), store, SESSION, "", "report.pdf")


def test_deleting_a_kept_file_is_idempotent(store):
    store.write_file(SESSION, "report.pdf", b"x")
    assert files.delete_one(store, SESSION, "report.pdf")["deleted"] is True
    assert files.delete_one(store, SESSION, "report.pdf")["deleted"] is False


def test_deleting_refuses_when_keeping_is_off():
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        files.delete_one(None, "", "report.pdf")


# ---- the two surfaces --------------------------------------------------------

# Which tool answers which endpoint. Written out rather than derived, so a typo
# in either table cannot make the parity test agree with itself.
TOOL_FOR = {
    "list": files.FILES_TOOL,
    "keep": files.KEEP_TOOL,
}


async def test_every_file_action_is_reachable_from_both_surfaces(
    kept_server, client, monkeypatch
):
    """The promise `test_surfaces.py` makes for browser actions, kept for these.

    They are subtracted from that module's assertions because they are not
    browser actions — they act on a session's files, live under /files, and have
    their own route table. So this is where the one-to-one is actually held, and
    subtracting them there is a narrowing rather than an excuse.

    Resources are turned off so the listing includes `session_files`, which is a
    resource mirror and hidden from a client that can read the resource itself.
    """
    assert set(TOOL_FOR) == set(files.FILE_ENDPOINTS), (
        "an endpoint has no tool named for it, or the other way round"
    )
    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    names = {t.name for t in await kept_server.mcp.list_tools()}
    for path, tool in TOOL_FOR.items():
        assert tool in names, f"/files/{path} has no tool"
        # 401 rather than 404: the route exists and refused the credential,
        # which is what proves it is bound.
        assert client.post(f"/files/{path}").status_code == 401, f"{tool} has no route"


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
    assert keep.idempotent_hint is True


async def test_deleting_a_kept_file_is_not_offered_to_an_agent(
    kept_server, monkeypatch
):
    """Keeping is one-way on purpose. An agent is not the thing that runs out of
    disk, and a one-way verb is a simpler promise than a reversible one whose
    meaning depends on whether a browser still exists. Removing a kept file is
    an operator action on the admin surface — which is also why there is no
    endpoint: one without a tool is the half-a-capability this project forbids.
    """
    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    names = {t.name for t in await kept_server.mcp.list_tools()}
    assert "delete_file" not in names
    assert "delete" not in files.FILE_ENDPOINTS


def test_the_endpoints_need_the_token(client):
    for path in files.FILE_ENDPOINTS:
        assert client.post(f"/files/{path}").status_code == 401, path
        assert (
            client.post(
                f"/files/{path}", headers={"Authorization": "Bearer nope"}
            ).status_code
            == 401
        ), path


def test_keep_then_list_over_http(client, live):
    with (
        patch.object(browser.Grid, "files", return_value=DOWNLOADS),
        patch.object(browser.Grid, "read_file", return_value=b"PDF"),
    ):
        kept = client.post(
            "/files/keep",
            json={"session_id": "abc", "name": "report.pdf", "session": SESSION},
            headers=AUTH,
        )
        assert kept.status_code == 200, kept.text
        assert kept.json()["kept"] is True

        body = client.post(
            "/files/list",
            json={"session_id": "abc", "session": SESSION},
            headers=AUTH,
        ).json()
    assert [(f["name"], f["kept"]) for f in body["files"]] == [
        ("report.pdf", True),
        ("shot.png", False),
    ]
    assert body["session"] == SESSION


def test_an_unusable_name_is_a_400_over_http(client, live):
    response = client.post(
        "/files/keep",
        json={"session_id": "abc", "name": "../passwd", "session": SESSION},
        headers=AUTH,
    )
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
        response = client.post(
            "/files/keep",
            json={"session_id": "abc", "name": "report.pdf", "session": SESSION},
            headers=AUTH,
        )
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


async def test_the_file_listing_tool_enforces_the_session_mode(
    kept_server, named_caller
):
    """Every other tool enforces this through `sessions.resolve`, which this one
    cannot call: resolve opens a browser when the record has none, and a listing
    that opened one would be the leak the status resource refuses to be. A
    browser id is the whole credential for driving that browser, so being handed
    another caller's downloads — with a signed URL each — is not nothing."""
    tool = await kept_server.mcp.get_tool(files.FILES_TOOL)
    with pytest.raises(ValueError, match="do not pass session_id"):
        tool.fn(session_id="somebody-elses-browser")


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


def test_a_detached_session_still_lists_its_kept_files(client, kept_server):
    """It has no browser and therefore no downloads — but keeping exists exactly
    so that is not the end of the answer."""
    kept_server.sessions.store.set("named:idle", SessionRecord(session_id=""))
    kept_server.flows.write_file("idle", "report.pdf", b"PDF")
    body = client.get("/admin/sessions/named:idle/files", headers=AUTH).json()
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


BAD_KEY = "named:my bot"


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
    return await build_spec(kept_server.mcp, ENDPOINTS, "/browser", authenticated=True)


async def test_every_file_endpoint_is_in_the_published_contract(spec):
    """These paths are written by hand, so the list is held against the one the
    server actually binds — the guard the multipart upload schema lacked until
    it had already drifted."""
    published = {p for p in spec["paths"] if p.startswith("/files/")}
    assert published == {f"/files/{path}" for path in files.FILE_ENDPOINTS}


async def test_the_file_endpoints_are_tagged_apart(spec):
    assert "files" in {t["name"] for t in spec["tags"]}
    for path, operations in spec["paths"].items():
        if path.startswith("/files/"):
            assert operations["post"]["tags"] == ["files"], path


async def test_every_file_response_schema_it_references_exists(spec):
    """A $ref to a schema nobody defined renders as a blank box in every docs UI
    and fails a strict linter."""
    defined = set(spec["components"]["schemas"])
    for path, operations in spec["paths"].items():
        if not path.startswith("/files/"):
            continue
        for block in operations["post"]["responses"].values():
            ref = block["content"]["application/json"]["schema"].get("$ref")
            if ref:
                assert ref.split("/")[-1] in defined, f"{path} -> {ref}"


async def test_every_file_operation_declares_the_grids_failure_modes(spec):
    """Both remaining operations dial the Grid, so both can fail its way — and
    the contract has to say so, or a generated client writes no handling for the
    reaped browser it will certainly meet."""
    for path in files.FILE_ENDPOINTS:
        responses = spec["paths"][f"/files/{path}"]["post"]["responses"]
        assert set(responses) == {"200", "400", "401", "404", "500", "503"}, path
