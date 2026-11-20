"""What a workspace's sessions share and what each keeps (WS-52, WS-54): history
on the workspace's record, tagged by session; files, flows and config by the
workspace; downloads by the session's own browser (FILE-31, UI-65)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow.core import browser
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.names import GLOBAL_WORKSPACE
from kubed.selenium_flow.workspace.store import HISTORY_CAP, RedisStore, Workspace
from kubed.selenium_flow.workspace.workspaces import _acting_in, acting

from .conftest import NAMED, TOKEN, http, manager
from .fakes import FakeRedis, RecordingActions
from .test_sub_sessions import A_SITE, B_SITE, as_, ids, two_open

pytestmark = pytest.mark.unit

C_SITE = "https://c.example.test/"
DAY = 86400.0
HOUR = 3600.0
D_SITE = "https://d.example.test/"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def origin(url):
    return url.rstrip("/")


def tagged(record):
    return [(v["session"], v["origin"]) for v in record.history]


# ---- history: the workspace's, each entry naming its session (WS-54) --------


def test_a_sub_sessions_visit_is_in_the_workspaces_history_tagged():
    actions = RecordingActions()
    workspaces, store = two_open(actions)
    workspaces.act(as_(), lambda s: {"url": C_SITE}, session="tester")
    record = store.get("shop")
    assert tagged(record) == [
        ("tester", origin(C_SITE)),
        ("tester", origin(B_SITE)),
        ("default", origin(A_SITE)),
    ]
    assert store.get("shop/tester").history == [], "never on the hour-long record"
    # Each session's page is its own newest entry (WS-7).
    assert record.url == A_SITE
    assert workspaces.context("shop")["url"] == A_SITE
    assert workspaces.context("shop/tester")["url"] == C_SITE
    # A reaped sub-session reopens on its own page, the default's left alone.
    actions.grid.alive.discard(ids(store)[1])
    workspaces.act(as_(), lambda s: {}, session="tester")
    assert actions.opened_urls[-1] == C_SITE
    assert store.get("shop").url == A_SITE


def test_a_single_player_entry_is_the_defaults():
    workspaces = manager()
    workspaces.open_browser(as_(), url=A_SITE)
    workspaces.act(as_(), lambda s: {"url": B_SITE})
    assert [
        {k: v for k, v in entry.items() if k != "at"}
        for entry in workspaces.store.get("shop").history
    ] == [
        {"origin": origin(B_SITE), "url": B_SITE, "session": "default"},
        {"origin": origin(A_SITE), "url": A_SITE, "session": "default"},
    ]


def test_two_sessions_on_one_origin_keep_an_entry_each():
    workspaces, store = two_open()
    workspaces.act(as_(), lambda s: {"url": A_SITE + "x"}, session="tester")
    assert tagged(store.get("shop")) == [
        ("tester", origin(A_SITE)),
        ("tester", origin(B_SITE)),
        ("default", origin(A_SITE)),
    ]
    assert store.get("shop").url == A_SITE, "the default's page is still its own"


def test_each_live_sessions_current_page_is_kept_whole():
    record = (
        Workspace()
        .visited("https://a.test/cb?code=1", now=0.0)
        .visited("https://b.test/reset?token=2", now=1.0, session="tester")
        .visited("https://c.test/?q=3", now=2.0, session="tester")
    )
    assert record.page("tester") == "https://c.test/?q=3"
    assert [v["url"] for v in record.entries("tester")] == [
        "https://c.test/?q=3", "https://b.test/reset"
    ], "below a session's top a query goes"
    # The default's top stays whole past the TTL: its reopen goes there.
    later = record.visited(None, now=DAY + 10.0, ttl=DAY, sub_ttl=HOUR)
    assert [(v["session"], v["url"]) for v in later.history] == [
        ("default", "https://a.test/cb?code=1"),
    ]


def test_a_dead_sub_sessions_page_loses_its_query_then_ages_out():
    """A sub-session unused past `sub_ttl` has no record to reopen from, so its
    top is an entry like any other: no query, gone after the TTL."""
    record = (
        Workspace()
        .visited("https://a.test/", now=0.0)
        .visited("https://o.test/cb?code=SECRET#state=1", now=0.0, session="tester")
    )
    alive = record.visited(None, now=HOUR - 1.0, ttl=DAY, sub_ttl=HOUR)
    assert alive.page("tester") == "https://o.test/cb?code=SECRET#state=1"
    dead = record.visited(None, now=HOUR + 1.0, ttl=DAY, sub_ttl=HOUR)
    assert dead.page("tester") == "https://o.test/cb"
    assert "SECRET" not in json.dumps(dead.history)
    gone = record.visited(None, now=DAY + 1.0, ttl=DAY, sub_ttl=HOUR)
    assert tagged(gone) == [("default", "https://a.test")]


def test_sub_sessions_never_push_the_defaults_page_out():
    record = Workspace().visited("https://a.test/home", now=0.0)
    for i in range(HISTORY_CAP + 5):
        record = record.visited(f"https://h{i}.test/", now=1.0 + i, session=f"s{i}")
    assert len(record.history) == HISTORY_CAP
    assert record.url == "https://a.test/home"
    assert record.history[-1]["session"] == "default"


def test_a_stored_entry_names_its_session_or_is_the_defaults():
    raw = json.dumps({"history": [
        {"origin": "https://a.test", "url": "https://a.test/", "at": 3.0, "session": "tester"},
        {"origin": "https://b.test", "url": "https://b.test/", "at": 2.0},
        {"origin": "https://c.test", "url": "https://c.test/", "at": 1.0, "session": 7},
        {"origin": "https://d.test", "url": "https://d.test/", "at": 0.5, "session": ""},
    ]})
    assert tagged(Workspace.from_json(raw)) == [
        ("tester", "https://a.test"), ("default", "https://b.test"),
    ]


def test_the_admins_clear_keeps_every_live_sessions_current_page(server):
    store = server.workspaces.store
    store.set(
        NAMED,
        Workspace(subs=["tester", "gone"])
        .visited("https://old.example.test/")
        .visited(A_SITE)
        .visited(C_SITE, session="gone")
        .visited(B_SITE, session="tester"),
    )
    # `gone`'s record expired: it cannot reopen, so its page is cleared too.
    store.set(f"{NAMED}/tester", Workspace(label="tester"))
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    url = f"/admin/workspaces/{NAMED}/history"
    assert client.get(url).json()["clears"] == [
        origin(C_SITE), "https://old.example.test"
    ]
    assert client.delete(url).json() == {
        "cleared": ["c.example.test", "old.example.test"]
    }
    assert tagged(store.get(NAMED)) == [
        ("tester", origin(B_SITE)), ("default", origin(A_SITE)),
    ]


def test_two_sessions_append_history_without_losing_an_entry():  # Review Focus 4
    fake = FakeRedis()
    store = RedisStore(fake, prefix="p:")
    workspaces = manager(RecordingActions(), store)
    workspaces.open_browser(as_(), url=A_SITE)
    workspaces.open_browser(as_(), url=B_SITE, session="tester")
    default, tester = ids(store)

    def the_default_lands_first():
        # Only as the workspace's record is written: the default's visit lands
        # between the sub-session's read of it and its write.
        if "p:shop" not in fake.watching:
            return
        fake.interfere = None
        workspaces.touch("shop", C_SITE, browser=default)

    fake.interfere = the_default_lands_first
    workspaces.touch("shop/tester", D_SITE, browser=tester)
    assert fake.interfere is None, "the race was run"
    assert tagged(store.get("shop")) == [
        ("tester", origin(D_SITE)),
        ("default", origin(C_SITE)),
        ("tester", origin(B_SITE)),
        ("default", origin(A_SITE)),
    ]
    assert store.get("shop/tester").history == []


def test_a_replaced_sub_session_browser_writes_no_history():
    workspaces, store = two_open()
    workspaces.touch("shop/tester", C_SITE, browser="someone-elses")
    assert origin(C_SITE) not in [v["origin"] for v in store.get("shop").history]


# ---- visited: a save reads its own session's sites (R10) --------------------


def test_a_call_knows_the_session_it_acts_in():
    workspaces, _ = two_open()
    seen = []
    workspaces.act(as_(), lambda s: seen.append(acting()) or {}, session="tester")
    workspaces.act(as_(), lambda s: seen.append(acting()) or {})
    assert seen == ["shop/tester", "shop"]
    assert acting() == "", "and only while it runs"


def test_a_sub_sessions_save_site_data_reads_its_own_sites(server, monkeypatch):
    store = server.workspaces.store
    store.set(
        NAMED,
        Workspace(subs=["tester"])
        .visited(A_SITE)
        .visited(B_SITE, session="tester")
        .visited(C_SITE),
    )
    store.set(f"{NAMED}/tester", Workspace(label="tester"))
    monkeypatch.setattr(
        clients_module, "request_values", lambda: http({"workspace": NAMED})
    )
    # As `server.py` wires `Actions.visited`, which `save_site_data` reads.
    with _acting_in(f"{NAMED}/tester"):
        assert server.actions.visited() == [origin(B_SITE)]
    with _acting_in(NAMED):
        assert server.actions.visited() == [origin(C_SITE), origin(A_SITE)]
    assert server.workspaces.visited(f"{NAMED}/tester") == [origin(B_SITE)]


# ---- files, flows and config: the workspace's, from every session -----------


def test_flows_files_and_config_are_the_same_from_every_session(data_server):
    store = data_server.workspaces.store
    store.set(NAMED, Workspace(subs=["tester"]))
    store.set(f"{NAMED}/tester", Workspace(label="tester"))
    data_server.flows.write_file(NAMED, "report.pdf", b"PDF")
    data_server.flows.save(NAMED, "mine", {"steps": [], "description": "mine"})
    data_server.flows.save(GLOBAL_WORKSPACE, "offered", {"steps": [], "tags": ["web"]})
    data_server.flows.save(GLOBAL_WORKSPACE, "hidden", {"steps": [], "tags": ["mail"]})
    data_server.configs.save(NAMED, "flows:\n  global: [web]\n")
    client = TestClient(data_server.mcp.server.http_app(), headers=AUTH)

    def read(session):
        pin = f"&session={session}" if session else ""
        files = client.get(f"/files?workspace={NAMED}{pin}")
        flows = client.get(f"/flows?workspace={NAMED}{pin}")
        assert files.status_code == flows.status_code == 200, (files.text, flows.text)
        return files.json(), flows.json()

    default = read("")
    assert [f["name"] for f in default[0]["files"]] == ["report.pdf"]
    names = json.dumps(default[1])
    assert "mine" in names and "offered" in names and "hidden" not in names
    assert read("tester") == default
    # A config the workspace refuses refuses every session's calls.
    data_server.flows.write_config(NAMED, "colour: blue\n")
    for pin in ("", "&session=tester"):
        refused = client.get(f"/flows?workspace={NAMED}{pin}")
        assert refused.status_code == 409, refused.text


# ---- downloads: each session's browser's own (WS-52, FILE-31) ---------------


def _two_browsers(server):
    store = server.workspaces.store
    store.set(NAMED, Workspace(session_id="g-default", subs=["tester"]))
    store.set(f"{NAMED}/tester", Workspace(session_id="g-tester", label="tester"))


def _listing(session_id):
    return [{"name": f"{session_id}.pdf", "size": 3, "creationTime": 1}]


def _names(listing):
    return [f["name"] for f in listing]


async def test_each_session_has_its_own_downloads(data_server, monkeypatch):
    _two_browsers(data_server)
    client = TestClient(data_server.mcp.server.http_app(), headers=AUTH)
    admin = f"/admin/workspaces/{NAMED}/files/downloads"
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "files", side_effect=_listing),
        patch.object(browser.Grid, "clear_files") as clear,
    ):
        # REST: the caller's session, by its pin.
        mine = client.get(f"/browser/downloads?workspace={NAMED}").json()
        theirs = client.get(f"/browser/downloads?workspace={NAMED}&session=tester")
        assert _names(mine["files"]) == ["g-default.pdf"]
        assert _names(theirs.json()["files"]) == ["g-tester.pdf"]
        # MCP: session://downloads answers the caller's session.
        monkeypatch.setattr(
            clients_module,
            "request_values",
            lambda: http({"workspace": NAMED, "session": "tester"}),
        )
        async with Client(data_server.mcp.server) as mcp:
            read = await mcp.read_resource("session://downloads")
        assert _names(json.loads(read[0].text)["files"]) == ["g-tester.pdf"]
        # The admin's: `session` names one, the default when absent.
        listed = client.get(admin)
        assert listed.status_code == 200, listed.text
        assert _names(listed.json()["downloads"]) == ["g-default.pdf"]
        listed = client.get(f"{admin}?session=tester").json()
        assert (listed["session"], listed["browser"]) == ("tester", True)
        assert _names(listed["downloads"]) == ["g-tester.pdf"]
        assert client.delete(f"{admin}?session=tester").status_code == 200
        clear.assert_called_once_with("g-tester")
        assert client.delete(admin).status_code == 200
        assert clear.call_args_list[-1].args == ("g-default",)
        # A sub-session with no record is a 404, as on every session route.
        assert client.get(f"{admin}?session=nobody").status_code == 404
        assert client.get(f"{admin}?session=a%2Fb").status_code == 400


async def test_keep_file_reads_the_callers_sessions_downloads(
    data_server, monkeypatch
):
    _two_browsers(data_server)
    reads = []

    def read_file(self, session_id, name):
        reads.append(session_id)
        return b"PDF"

    uri = "session://downloads/report.pdf"
    monkeypatch.setattr(
        clients_module, "request_values", lambda: http({"workspace": NAMED})
    )
    client = TestClient(data_server.mcp.server.http_app(), headers=AUTH)
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "read_file", read_file),
    ):
        async with Client(data_server.mcp.server) as mcp:
            await mcp.call_tool("keep_file", {"uri": uri, "session": "tester"})
            await mcp.call_tool("keep_file", {"uri": uri})
        kept = client.put(
            f"/files/downloads/report.pdf/kept?workspace={NAMED}",
            json={"session": "tester"},
        )
        assert kept.status_code == 200, kept.text
        admin = client.post(
            f"/admin/workspaces/{NAMED}/files/downloads/report.pdf/keep?session=tester"
        )
        assert admin.status_code == 200, admin.text
        # upload_file(file=uri) reads the downloads of the session it acts in.
        with _acting_in(f"{NAMED}/tester"):
            data_server.actions.read_file(uri)
        monkeypatch.setattr(
            clients_module,
            "request_values",
            lambda: http({"workspace": NAMED, "session": "tester"}),
        )
        async with Client(data_server.mcp.server) as mcp:
            await mcp.call_tool("keep_file", {"uri": uri})
    assert reads == [
        "g-tester", "g-default", "g-tester", "g-tester", "g-tester", "g-tester"
    ]
