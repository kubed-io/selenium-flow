"""Pointers name the session a call acted in (#181): `workspace://…` and
`session://downloads…` for the caller's own, `session://<name>/…` for any
other; a pinned connection and an unnamed call see exactly what they always
did; and every `session://<name>/…` a pointer names can be read."""

from __future__ import annotations

import base64
import contextlib
import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from selenium.common.exceptions import TimeoutException
from starlette.testclient import TestClient

from kubed.selenium_flow import faults
from kubed.selenium_flow.capture.store import Capture
from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.flows import store as flow_store
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.principal import Principal
from kubed.selenium_flow.secrets import perform_write
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import Workspace
from kubed.selenium_flow.workspace.workspaces import (
    Caller,
    Workspaces,
    _acting_in,
    acting,
)

from .capture_events import done, log, nav_started, req
from .conftest import NAMED, TOKEN, calling_as, http, manager
from .fakes import Anywhere, FakeMonitor, PageActions, ScriptedDriver
from .test_site_data_workspaces import SiteActions

pytestmark = pytest.mark.unit

WS = "ws"
SUB = sessions.key_of(WS, "tester")
TESTER = sessions.key_of(NAMED, "tester")
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NAMELESS = "{name} being the session this call named"


def unnamed(workspace=WS):
    return Caller(workspace, "query")


def pinned(session, workspace=WS):
    return Caller.from_request({"workspace": [workspace], "session": [session]}, {})


# ---- the host every call goes through ---------------------------------------


@pytest.fixture
def two():
    """`ws` with its default and `tester` open, both captured."""
    capture = Capture()
    workspaces = Workspaces(PageActions(), monitor=FakeMonitor(), capture=capture)
    workspaces.open_browser(unnamed())
    workspaces.open_browser(unnamed(), session="tester")
    for key in (WS, SUB):
        sid = workspaces.store.get(key).session_id
        capture.attached(key, sid, "chrome", "top-1", again=False)
    return workspaces, capture


def erring(capture):
    def call(sid):
        capture.ingest(acting(), sid, *log("boom", level="error"))
        return {"url": "https://a.test/", "title": "A"}

    return call


def failing(capture):
    def call(sid):
        capture.ingest(acting(), sid, *log("boom", level="error"))
        raise TimeoutException("waited 30s for //div")

    return call


def test_a_named_sessions_hint_points_at_that_session(two):
    workspaces, capture = two
    page = workspaces.act(unnamed(), erring(capture), session="tester", tool="interact")
    assert page["page"]["see"] == "session://tester/console?level=error"


def test_an_unnamed_or_pinned_call_points_at_workspace(two):
    workspaces, capture = two
    for caller, session in (
        (unnamed(), None),
        (unnamed(), "default"),
        (pinned("tester"), None),
        (pinned("tester"), "tester"),
    ):
        result = workspaces.act(caller, erring(capture), session=session, tool="interact")
        assert result["page"]["see"] == "workspace://console?level=error", (caller, session)


def test_a_named_sessions_error_note_never_names_it(two):
    workspaces, capture = two
    with pytest.raises(TimeoutException) as raised:
        workspaces.act(unnamed(), failing(capture), session="tester", tool="interact")
    said = faults.message(raised.value)
    assert said.endswith(
        "This call caused 1 page error(s) and 0 failed request(s): read "
        f"session://{{name}}/console?level=error, {NAMELESS}."
    )
    assert "tester" not in said


def test_a_pinned_calls_error_note_is_as_it_was(two):
    workspaces, capture = two
    with pytest.raises(TimeoutException) as raised:
        workspaces.act(pinned("tester"), failing(capture), tool="interact")
    assert faults.message(raised.value).endswith(
        "This call caused 1 page error(s) and 0 failed request(s): read "
        "workspace://console?level=error."
    )


def body(sid):
    return {"id": "1.1", "uri": "workspace://network/1.1", "mime": "text/plain",
            "size": 2, "binary": False, "text": "ok", "truncated": False}


def test_a_bodys_uri_names_the_session_it_was_fetched_in(two):
    workspaces, _ = two
    named = workspaces.act(unnamed(), body, session="tester", tool="network_body")
    assert named["uri"] == "session://tester/network/1.1"
    assert workspaces.act(unnamed(), body, tool="network_body")["uri"] == (
        "workspace://network/1.1"
    )
    pin = workspaces.act(pinned("tester"), body, session="tester", tool="network_body")
    assert pin["uri"] == "workspace://network/1.1"
    # A URI that is no session's state is left as it is.
    kept = workspaces.act(
        unnamed(), lambda s: {"uri": "workspace://files/a.png"}, session="tester"
    )
    assert kept["uri"] == "workspace://files/a.png"


def test_a_bound_writes_hint_points_at_the_session_it_typed_in(two):
    workspaces, capture = two

    def typed(sid, text, **kw):
        capture.ingest(acting() or SUB, sid, *log("saw it", level="error"))
        return {"url": "https://a.test/", "value": None}

    workspaces.actions.write = typed
    kwargs = {"secret": {"name": "demo", "key": "password"}, "selector": {"css": "#p"}}
    shown = perform_write(Anywhere(), workspaces.actions, workspaces, SUB, dict(kwargs), own=WS)
    assert shown["page"]["see"] == "session://tester/console?level=error"
    shown = perform_write(Anywhere(), workspaces.actions, workspaces, SUB, dict(kwargs))
    assert shown["page"]["see"] == "workspace://console?level=error"


def test_own_is_the_session_reading_would_reach(two):
    workspaces, _ = two
    assert workspaces.own(unnamed(), None, SUB) == SUB, "an unnamed call acts in its own"
    assert workspaces.own(unnamed(), "tester", SUB) == WS
    assert workspaces.own(pinned("tester"), "tester", SUB) == SUB


def test_a_caller_with_no_own_session_is_pointed_at_the_one_it_named(two):
    """A subject that cannot name a session, where another owns the default,
    has no own session to read: what it named is never taken for it."""
    workspaces, capture = two
    workspaces.store.update(WS, lambda r: replace(r, owner="sub-a"))
    caller = replace(unnamed(), principal=Principal("oidc", subject="current"))
    assert workspaces.own(caller, "tester", SUB) == ""
    result = workspaces.act(caller, erring(capture), session="tester", tool="interact")
    assert result["page"]["see"] == "session://tester/console?level=error"


def test_a_store_failing_on_the_callers_own_reopens_nothing(two, monkeypatch):
    """`own` is read before the browser is touched: a reaped browser is not
    reopened for a call that then fails, with its notice stranded."""
    workspaces, capture = two
    reaped = workspaces.store.get(SUB).session_id
    workspaces.actions.grid.alive.discard(reaped)
    opened = workspaces.actions.n

    def down(caller, named=None):
        raise ConnectionError("store unreachable")

    monkeypatch.setattr(workspaces, "reading", down)
    with pytest.raises(ConnectionError):
        workspaces.act(unnamed(), erring(capture), session="tester", tool="interact")
    assert workspaces.actions.n == opened
    assert workspaces.store.get(SUB).session_id == reaped


# ---- a flow run --------------------------------------------------------------


class FlowActions(PageActions):
    """``interact`` makes the page throw, then fails or not as planned."""

    def __init__(self, capture):
        super().__init__()
        self.capture, self.fails = capture, False

    def interact(self, session_id, **kwargs):
        self.capture.ingest(acting(), session_id, *log("broken", level="error"))
        if self.fails:
            raise TimeoutException("waited 30s for button")
        return {"url": "https://a.test/", "title": "A"}


@pytest.fixture
def flow(tmp_path):
    capture = Capture()
    actions = FlowActions(capture)
    workspaces = Workspaces(actions, monitor=FakeMonitor(), capture=capture)
    workspaces.open_browser(unnamed())
    workspaces.open_browser(unnamed(), session="tester")
    for key in (WS, SUB):
        sid = workspaces.store.get(key).session_id
        capture.attached(key, sid, "chrome", "top-1", again=False)
    store = flow_store.LocalFlowStore(tmp_path)
    store.save(WS, "trip", {"steps": [
        {"tool": "interact", "args": {"action": "click", "selector": {"css": "button"}}}
    ]})

    def run(**kw):
        return flowapi.run_for(store, actions, workspaces, WS, "trip", **kw)

    run.actions = actions
    return run


def test_a_runs_hint_points_at_the_session_it_ran_in(flow):
    assert flow(key=SUB, own=WS)["page"]["see"] == "session://tester/console?level=error"
    assert flow(key=SUB, own=SUB)["page"]["see"] == "workspace://console?level=error"
    assert flow()["page"]["see"] == "workspace://console?level=error"


def test_a_failed_steps_error_never_names_the_session(flow, caplog):
    flow.actions.fails = True
    with caplog.at_level("INFO"):
        report = flow(key=SUB, own=WS)
    [step] = report["steps"]
    assert step["error"].endswith(
        f"read session://{{name}}/console?level=error, {NAMELESS}."
    )
    assert "tester" not in step["error"]
    assert not [r for r in caplog.records if "tester" in r.getMessage()]


# ---- site data ---------------------------------------------------------------


def test_a_save_names_the_session_it_was_saved_in(named_caller):
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    m.open_browser(Caller(NAMED), session="tester")
    save = lambda s: m.actions.save_site_data(s)  # noqa: E731
    assert m.act(Caller(NAMED), save, session="tester")["uri"] == (
        "session://tester/site-data"
    )
    assert m.act(Caller(NAMED), save)["uri"] == "workspace://site-data"
    assert m.act(pinned("tester", NAMED), save)["uri"] == "workspace://site-data"


def test_a_restore_names_the_session_it_restored(named_caller):
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    m.open_browser(Caller(NAMED), session="tester")
    save = lambda s: m.actions.save_site_data(s)  # noqa: E731
    m.act(Caller(NAMED), save, session="tester")
    m.act(Caller(NAMED), save)
    told = m.open_browser(Caller(NAMED), session="tester")
    assert told["site_data"]["uri"] == "session://tester/site-data"
    assert m.open_browser(Caller(NAMED))["site_data"]["uri"] == "workspace://site-data"
    told = m.open_browser(pinned("tester", NAMED))
    assert told["site_data"]["uri"] == "workspace://site-data"
    # A silent reopen hands its report to the next call.
    m.actions.grid.alive.clear()
    result = m.act(Caller(NAMED), lambda s: {"url": "https://a.test/"}, session="tester")
    assert result["site_data"]["uri"] == "session://tester/site-data"
    result = m.act(Caller(NAMED), lambda s: {"url": "https://a.test/"})
    assert result["site_data"]["uri"] == "workspace://site-data"


def test_every_listed_session_carries_its_uri(named_caller):
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    told = m.open_browser(Caller(NAMED), session="tester")
    assert [(s["name"], s["uri"]) for s in told["others"]] == [
        ("default", "session://default"),
    ]
    listed = m.describe(Caller(NAMED))["sessions"]
    assert [(s["name"], s["uri"]) for s in listed] == [
        ("default", "session://default"), ("tester", "session://tester"),
    ]


# ---- the session:// reads ------------------------------------------------------


class AliveGrid:
    """Every browser lives, and each has one download named for it."""

    def is_alive(self, session_id):
        return True

    def files(self, session_id):
        return [{"name": f"{session_id}.pdf", "size": 3, "creationTime": 1}]

    def read_file(self, session_id, name):
        return f"PDF of {session_id}".encode()


JAR = {"cookies": [{"name": "n", "value": "1", "domain": "b.example.test"}]}


@pytest.fixture
def server(data_server, monkeypatch):
    """`NAMED` with its default (`g-default`) and `tester` (`g-tester`), each
    capturing, `tester` with saved site data and a request of its own."""
    data_server.workspaces.actions.grid = AliveGrid()
    store = data_server.workspaces.store
    store.set(NAMED, Workspace(
        session_id="g-default", settings={"capture": True}, subs=["tester"],
    ))
    store.set(TESTER, Workspace(
        session_id="g-tester", settings={"capture": True}, label="tester",
    ))
    store.update(TESTER, lambda r: r.with_site_data(JAR))
    capture = data_server.capture
    for key, sid in ((NAMED, "g-default"), (TESTER, "g-tester")):
        capture.attached(key, sid, "chrome", "top-1", again=False)
        for method, params in (
            nav_started("https://b.example.test/"),
            req("r1", "https://b.example.test/api", initiator="fetch"),
            done("r1", 500),
        ):
            capture.ingest(key, sid, method, params)
    calling_as(monkeypatch, NAMED)
    return data_server


async def read(server, uri):
    async with Client(server.mcp.server) as c:
        return json.loads((await c.read_resource(uri))[0].text)


async def test_a_named_sessions_context_points_at_its_site_data(server):
    tester = await read(server, "session://tester")
    assert tester["site_data"]["uri"] == "session://tester/site-data"
    assert [s["uri"] for s in tester["sessions"]] == [
        "session://default", "session://tester",
    ]


async def test_a_named_sessions_site_data_is_read_at_its_own_uri(server):
    listing = await read(server, "session://tester/site-data")
    assert listing["uri"] == "session://tester/site-data"
    assert [(r["site"], r["uri"]) for r in listing["sites"]] == [
        ("b.example.test", "session://tester/site-data/b.example.test"),
    ]
    one = await read(server, "session://tester/site-data/b.example.test")
    assert one["uri"] == "session://tester/site-data/b.example.test"
    assert [c["name"] for c in one["cookies"]] == ["n"]
    # The caller's own has none, and still says workspace://.
    own = await read(server, "workspace://site-data")
    assert own == {"sites": [], "saved_at": None, "uri": "workspace://site-data"}


async def test_a_named_sessions_request_is_read_at_its_own_uri(server):
    [row] = (await read(server, "session://tester/network"))["requests"]
    one = await read(server, f"session://tester/network/{row['id']}")
    assert one["uri"] == f"session://tester/network/{row['id']}"
    assert one["url"] == "https://b.example.test/api"
    own = await read(server, f"workspace://network/{row['id']}")
    assert own["uri"] == f"workspace://network/{row['id']}"


async def test_the_new_reads_of_an_unknown_session_are_not_found(server):
    store = server.workspaces.store
    before = store.get(NAMED)
    for uri in (
        "session://nobody/network/1.1",
        "session://nobody/site-data",
        "session://nobody/site-data/b.example.test",
        "session://nobody/downloads/a.pdf",
    ):
        with pytest.raises(MCPError) as missing:
            await read(server, uri)
        assert missing.value.error.code == -32002, uri
    assert store.get(NAMED) == before
    assert store.get(sessions.key_of(NAMED, "nobody")) is None


async def test_a_named_sessions_downloads_name_it(server):
    listing = await read(server, "session://tester/downloads")
    assert listing["uri"] == "session://tester/downloads"
    [entry] = listing["files"]
    assert entry["uri"] == "session://tester/downloads/g-tester.pdf"
    assert entry["keep_with"] == 'keep_file("session://tester/downloads/g-tester.pdf")'
    own = await read(server, "session://downloads")
    assert own["uri"] == "session://downloads"
    assert own["files"][0]["uri"] == "session://downloads/g-default.pdf"
    assert own["files"][0]["keep_with"] == 'keep_file("session://downloads/g-default.pdf")'


async def test_a_named_sessions_download_reads_from_its_browser(server):
    async with Client(server.mcp.server) as c:
        [content] = await c.read_resource("session://tester/downloads/g-tester.pdf")
    assert base64.b64decode(content.blob) == b"PDF of g-tester"


async def test_keep_file_takes_the_session_from_the_uri(server):
    uri = "session://tester/downloads/report.pdf"
    async with Client(server.mcp.server) as c:
        kept = (await c.call_tool("keep_file", {"uri": uri})).structured_content
        assert kept["from"] == uri
        assert server.flows.read_file(NAMED, "report.pdf") == b"PDF of g-tester"
        again = await c.call_tool("keep_file", {"uri": uri, "session": "tester"})
        assert again.structured_content["from"] == uri
        with pytest.raises(ToolError) as refused:
            await c.call_tool("keep_file", {"uri": uri, "session": "default"})
        assert "tester" not in str(refused.value)
        # The old form with a session names that session's download.
        old = await c.call_tool(
            "keep_file", {"uri": "session://downloads/report.pdf", "session": "tester"}
        )
        assert old.structured_content["from"] == uri
        mine = await c.call_tool("keep_file", {"uri": "session://downloads/report.pdf"})
        assert mine.structured_content["from"] == "session://downloads/report.pdf"


def test_upload_file_reads_the_download_the_uri_names(server):
    """Whichever session the call acts in: the file is read from the session
    its address names, and typed into the browser the call acts in."""
    read = server.actions.read_file
    uri = "session://tester/downloads/report.pdf"
    for acting_in in (TESTER, NAMED, sessions.key_of(NAMED, "zed")):
        with _acting_in(acting_in):
            assert read(uri) == ("report.pdf", b"PDF of g-tester"), acting_in
    with _acting_in(TESTER):
        assert read("session://downloads/report.pdf") == ("report.pdf", b"PDF of g-tester")


async def test_an_address_naming_no_file_never_names_the_session(server):
    async with Client(server.mcp.server) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("keep_file", {"uri": "session://tester/downloads/"})
    assert "session://{name}/downloads/ names no file" in str(refused.value)
    assert "tester" not in str(refused.value)


def test_a_rest_keep_says_whose_download_it_kept(server):
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    path = f"/files/downloads/report.pdf/kept?workspace={NAMED}"
    theirs = client.put(path, json={"session": "tester"})
    assert theirs.status_code == 200, theirs.text
    assert theirs.json()["from"] == "session://tester/downloads/report.pdf"
    mine = client.put(path, json={})
    assert mine.json()["from"] == "session://downloads/report.pdf"
    own = client.put(path, json={"session": "default"})
    assert own.json()["from"] == "session://downloads/report.pdf"


async def test_a_hand_back_names_the_session_it_restored(server, monkeypatch):
    report = {"restored": [], "skipped": [], "uri": "workspace://site-data"}
    server.workspaces.store.update(TESTER, lambda r: replace(
        r, reopened={"browser": "g-tester", "cause": "reaped", "report": report}
    ))
    monkeypatch.setattr(
        server.actions, "outline",
        lambda sid, **kw: {"url": "https://b.example.test/", "title": "B"},
        raising=False,
    )
    async with Client(server.mcp.server) as c:
        back = await c.call_tool("await_control", {"session": "tester"})
    assert back.structured_content["site_data"]["uri"] == "session://tester/site-data"


async def test_reading_your_downloads_claims_nothing(server, monkeypatch):
    first = Principal("oidc", subject="sub-a", username="first")
    monkeypatch.setattr(clients_module, "principal", lambda: first)
    store = server.workspaces.store
    assert store.get(NAMED).owner is None
    listing = await read(server, "session://downloads")
    assert listing["files"][0]["name"] == "g-default.pdf"
    assert store.get(NAMED).owner is None, "reading is not claiming"


async def test_mcp_tools_point_at_the_session_they_named(server, monkeypatch):
    actions = server.actions
    monkeypatch.setattr(actions, "network_body", lambda sid, id: body(sid), raising=False)
    async with Client(server.mcp.server) as c:
        named = await c.call_tool("network_body", {"id": "1.1", "session": "tester"})
        assert named.structured_content["uri"] == "session://tester/network/1.1"
        own = await c.call_tool("network_body", {"id": "1.1"})
        assert own.structured_content["uri"] == "workspace://network/1.1"
    calling_as_pinned = lambda: http({"workspace": NAMED, "session": "tester"})  # noqa: E731
    monkeypatch.setattr(clients_module, "request_values", calling_as_pinned)
    async with Client(server.mcp.server) as c:
        pin = await c.call_tool("network_body", {"id": "1.1", "session": "tester"})
        assert pin.structured_content["uri"] == "workspace://network/1.1"


def test_the_admin_says_where_a_sub_sessions_request_is(server):
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    listed = client.get(f"/admin/workspaces/{NAMED}/network?session=tester").json()
    rid = listed["requests"][0]["id"]
    one = client.get(f"/admin/workspaces/{NAMED}/network/{rid}?session=tester")
    assert one.status_code == 200, one.text
    assert one.json()["uri"] == f"session://tester/network/{rid}"
    own = client.get(f"/admin/workspaces/{NAMED}/network/{rid}").json()
    assert own["uri"] == f"workspace://network/{rid}"



async def test_the_mirror_says_where_a_named_sessions_download_is_linked(server):
    from kubed.selenium_flow.mcp import mirror

    server.workspaces.actions.grid.read_file = lambda sid, name: b"%PDF" + b"x" * 100
    async with Client(server.mcp.server) as c:
        result = await c.call_tool(
            mirror.READ_TOOL, {"uri": "session://tester/downloads/g-tester.pdf"}
        )
    said = json.loads(result.content[0].text)
    assert "Read session://tester/downloads for this file's link" in said["read"]


async def test_show_draws_what_a_named_session_points_at(server, built_ui):
    rid = (await read(server, "session://tester/network"))["requests"][0]["id"]
    drawn = {
        f"session://tester/network/{rid}": "request",
        "session://tester/site-data": "sites",
        "session://tester/site-data/b.example.test": "site",
        "session://tester/downloads/g-tester.pdf": "file",
    }
    async with Client(server.mcp.server) as c:
        for uri, component in drawn.items():
            shown = await c.call_tool("show", {"uri": uri})
            assert shown.structured_content["component"] == component, uri
    entry = shown.structured_content["data"]
    assert entry["uri"] == "session://tester/downloads/g-tester.pdf"


# ---- every surface's wiring of `own` -------------------------------------------

B_SITE = "https://b.example.test/"
SIDS = {"g-default": NAMED, "g-tester": TESTER}
SEE = "session://tester/console?level=error"
OWN_SEE = "workspace://console?level=error"


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A real server, its browsers doubled at the action: every `navigate`
    and `write` makes the page throw, in the session whose browser it is."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.core.actions import Actions
    from kubed.selenium_flow.secrets import ALLOWED_URLS
    from kubed.selenium_flow.server import SeleniumMCP

    from .fakes import untabbed
    from .test_secrets import make_secret

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    make_secret(
        tmp_path / "secrets", "demo", password="hunter2",
        **{ALLOWED_URLS: "https://b.example.test"},
    )
    built = {}

    def threw(session_id):
        built["server"].capture.ingest(
            SIDS[session_id], session_id, *log("boom", level="error")
        )
        return {"url": B_SITE, "title": "B"}

    # On the class, before the server is built: `routes.py` binds each method
    # at registration.
    def navigate(self, session_id, url):
        landed = threw(session_id)
        if url.endswith("/fail"):
            raise TimeoutException("waited 30s for the page")
        return landed

    def write(
        self, session_id, text, selector=None, url=None, clear=True,
        submit=False, wait_timeout=30, read_back=True,
    ):
        return {**threw(session_id), "value": None}

    monkeypatch.setattr(Actions, "navigate", navigate)
    monkeypatch.setattr(Actions, "write", write)
    monkeypatch.setattr(Actions, "page", lambda self, sid: {"url": B_SITE, "title": "B"})
    server = built["server"] = untabbed(SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path / "data")},
        secrets={"dirs": str(tmp_path / "secrets")},
    )))
    server.workspaces.actions.grid = AliveGrid()
    store = server.workspaces.store
    store.set(NAMED, Workspace(
        session_id="g-default", settings={"capture": True}, subs=["tester"],
    ))
    store.set(TESTER, Workspace(
        session_id="g-tester", settings={"capture": True}, label="tester",
    ))
    for sid, key in SIDS.items():
        server.capture.attached(key, sid, "chrome", "top-1", again=False)
    server.flows.save(NAMED, "trip", {"steps": [
        {"tool": "navigate", "args": {"url": B_SITE}},
    ]})
    calling_as(monkeypatch, NAMED)
    return server


TYPED = {"selector": {"css": "#p"}, "secret": {"name": "demo", "key": "password"}}


async def test_mcp_run_flow_and_write_point_at_the_session_they_named(wired):
    async with Client(wired.mcp.server) as c:
        for tool, args in (("run_flow", {"name": "trip"}), ("write", TYPED)):
            named = await c.call_tool(tool, {**args, "session": "tester"})
            assert named.structured_content["page"]["see"] == SEE, tool
            own = await c.call_tool(tool, args)
            assert own.structured_content["page"]["see"] == OWN_SEE, tool


def test_rest_runs_writes_and_actions_point_at_the_session_they_named(wired):
    client = TestClient(wired.mcp.server.http_app(), headers={**AUTH, "Sf-Workspace": NAMED})
    for path, body in (
        ("/flows/trip/runs", {}),
        ("/browser/write", TYPED),
        ("/browser/navigate", {"url": B_SITE}),
    ):
        named = client.post(path, json={**body, "session": "tester"})
        assert named.status_code in (200, 201), (path, named.text)
        assert named.json()["page"]["see"] == SEE, path
        own = client.post(path, json=body)
        assert own.json()["page"]["see"] == OWN_SEE, path


async def test_upload_file_types_another_sessions_download(server, monkeypatch):
    """Over the tool: a download named by its session's address is read from
    that session, and typed into the page of the session the call acts in,
    whichever that is (ACT-40)."""
    store = server.workspaces.store
    store.update(NAMED, lambda r: replace(r, subs=[*r.subs, "zed"]))
    store.set(sessions.key_of(NAMED, "zed"), Workspace(session_id="g-zed", label="zed"))
    typed = []

    def reconnect(session_id):
        driver = ScriptedDriver()
        send = driver.element.send_keys
        driver.element.send_keys = lambda path: (
            typed.append((session_id, Path(path).read_bytes())) or send(path)
        )
        return driver

    server.actions.grid.reconnect = reconnect
    uri = "session://tester/downloads/r.pdf"
    async with Client(server.mcp.server) as c:
        for session, into in (("zed", "g-zed"), (None, "g-default")):
            args = {"selector": {"css": "input"}, "file": uri}
            if session:
                args["session"] = session
            result = await c.call_tool("upload_file", args)
            assert result.structured_content["filename"] == "r.pdf"
            assert typed[-1] == (into, b"PDF of g-tester"), session


# ---- no log line names a sub-session (WS-49 (3), ERR-35) -----------------------

SUBJECT = "subject-7f3a"
GONE = "gone-7f3a"


def named_anywhere(caplog):
    """Every record of every logger, as a line and with its traceback, that
    carries the distinctive part of a sub-session's name."""
    out = []
    for record in caplog.records:
        text = record.getMessage()
        if record.exc_info:
            text += " " + " ".join(map(str, record.exc_info[1:2]))
        if record.exc_text:
            text += " " + record.exc_text
        if "7f3a" in text:
            out.append((record.name, text))
    return out


async def test_no_log_line_names_a_sub_session(server, caplog, monkeypatch):
    store = server.workspaces.store
    key = sessions.key_of(NAMED, SUBJECT)
    store.set(key, Workspace(
        session_id="g-subject", settings={"capture": True}, label=SUBJECT,
    ))
    store.update(NAMED, lambda r: replace(r, subs=[*r.subs, SUBJECT]))
    server.capture.attached(key, "g-subject", "chrome", "top-1", again=False)
    caplog.set_level(logging.DEBUG)
    async with Client(server.mcp.server) as c:
        # A tool call in a session that is not there.
        with pytest.raises(ToolError) as by_tool:
            await c.call_tool("extract", {"session": GONE, "selector": {"css": "h1"}})
        assert GONE not in str(by_tool.value)
        assert "open_session" in str(by_tool.value)
        # Reads of a missing session, a missing download, request and site.
        for uri in (
            f"session://{GONE}",
            f"session://{GONE}/console",
            f"session://{GONE}/console?level=bogus",
            f"session://{GONE}/downloads/a.pdf",
            f"session://{SUBJECT}/network/9.9",
            f"session://{SUBJECT}/site-data/nowhere.test",
        ):
            with pytest.raises(MCPError):
                await c.read_resource(uri)
        # A file the fake Grid serves whatever its name: refused or not, nothing
        # may name the session.
        for tail in ("console?level=bogus", "console?pages=9", "network?filter=bogus"):
            with pytest.raises(MCPError):
                await c.read_resource(f"session://{SUBJECT}/{tail}")
        with contextlib.suppress(MCPError):
            await c.read_resource(f"session://{SUBJECT}/downloads/nothing.pdf")
        # The mirror passes the URI in its error.
        for uri in (f"session://{GONE}/network", f"session://{SUBJECT}/nothing"):
            with pytest.raises(ToolError) as refused:
                await c.call_tool("read_resource", {"uri": uri})
            assert "7f3a" not in str(refused.value)
        with pytest.raises(ToolError) as shown:
            await c.call_tool("show", {"uri": f"session://{SUBJECT}/nothing"})
        assert "7f3a" not in str(shown.value)
    # A connection pinned to the sub-session, and a call naming another.
    calling_as_pinned = http({"workspace": NAMED, "session": SUBJECT})
    monkeypatch.setattr(clients_module, "request_values", lambda: calling_as_pinned)
    async with Client(server.mcp.server) as c:
        with pytest.raises(ToolError, match="pinned to a session"):
            await c.call_tool("extract", {"session": "other", "selector": {"css": "h1"}})
    assert named_anywhere(caplog) == []
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("fastmcp")]
    assert any("session://…" in line for line in lines), lines


def test_the_mask_names_no_session_but_keeps_the_callers_own():
    from kubed.selenium_flow.mcp.failures import nameless

    assert nameless("Error reading resource 'session://a-b/downloads/x?y=1'") == (
        "Error reading resource 'session://…/downloads/x?y=1'"
    )
    assert nameless("session://a-b and session://c") == "session://… and session://…"
    for own in ("session://downloads", "session://downloads/x.pdf", "'session://downloads'"):
        assert nameless(own) == own
    # A session that is called "downloads-2" is a name.
    assert nameless("see session://{name}/network") == "see session://{name}/network"
    assert nameless("SESSION://a-b/x and Session://c") == "session://…/x and session://…"
    assert nameless("session://downloads-2/network") == "session://…/network"
    # The reserved word only: followed by `/`, `?`, `#` or the end.
    for own in ("session://downloads?x=1", "session://downloads#a", "session://console"):
        assert nameless(own) == own
    for name in ("downloads-2", "downloads.bot", "downloadsX", "downloads_7f3a"):
        assert nameless(f"read session://{name}/console") == "read session://…/console"
    # Session names are compared as written: `Downloads` is a name, not the word.
    for name in ("Downloads", "Console", "Files", "Site-Data", "CURRENT"):
        assert nameless(f"'session://{name}/nothing'") == "'session://…/nothing'", name
    # A placeholder an error text writes is kept as written.
    for written in (
        "session://<session>/downloads/<name>",
        "session://<name>/network",
        "session://{name}/console?level=error",
        "session://{session}/downloads",
    ):
        assert nameless(f"use {written}.") == f"use {written}.", written


def test_the_texts_name_no_session_and_point_both_ways():
    from kubed.selenium_flow.workspace.workspaces import NO_SUB, PIN_DIFFERS

    assert "{name}" not in NO_SUB.format(workspace="w")
    assert "{name!r}" not in NO_SUB and "same session" in NO_SUB
    assert "{pinned" not in PIN_DIFFERS and "{named" not in PIN_DIFFERS


def test_a_missing_request_or_site_names_both_listings_and_no_session():
    from kubed.selenium_flow.capture import NETWORK_LISTINGS, network_request_at
    from kubed.selenium_flow.site_data.snapshot import SITE_LISTINGS

    for text in (NETWORK_LISTINGS, network_request_at("3.7"), SITE_LISTINGS):
        assert "workspace://" in text and "session://{name}/" in text


# ---- a name that starts with a reserved word, or is one capitalised ------------

DL = "downloads-7f3a"
DL_KEY = sessions.key_of(NAMED, DL)
FAIL = B_SITE + "fail"


def _with_session(server, monkeypatch, name, sid):
    """``name`` open in `NAMED` on ``sid``, capturing, its page throwing as
    `wired`'s do."""
    key = sessions.key_of(NAMED, name)
    store = server.workspaces.store
    store.set(key, Workspace(session_id=sid, settings={"capture": True}, label=name))
    store.update(NAMED, lambda r: replace(r, subs=[*r.subs, name]))
    monkeypatch.setitem(SIDS, sid, key)
    server.capture.attached(key, sid, "chrome", "top-1", again=False)
    return key


def _sessions_named(caplog, *needles):
    """Every record of every logger naming any of ``needles``."""
    return [
        (r.name, line)
        for r in caplog.records
        for line in [r.getMessage() + " " + (r.exc_text or "")]
        if any(n in line for n in needles)
    ]


async def test_a_failed_runs_note_never_names_a_downloads_like_session(
    wired, caplog, monkeypatch
):
    """I1: `downloads-7f3a` is a name, so its note is nameless, in the run's
    report, a REST refusal and every log line."""
    _with_session(wired, monkeypatch, DL, "g-dl")
    _with_session(wired, monkeypatch, "Downloads", "g-cap")
    wired.flows.save(NAMED, "broken", {"steps": [
        {"tool": "navigate", "args": {"url": FAIL}},
    ]})
    caplog.set_level(logging.DEBUG)
    note = f"read session://{{name}}/console?level=error, {NAMELESS}."
    async with Client(wired.mcp.server) as c:
        for name in (DL, "Downloads"):
            run = await c.call_tool("run_flow", {"name": "broken", "session": name})
            [step] = run.structured_content["steps"]
            assert step["error"].endswith(note), step["error"]
            assert name not in step["error"]
    client = TestClient(wired.mcp.server.http_app(), headers={**AUTH, "Sf-Workspace": NAMED})
    for name in (DL, "Downloads"):
        refused = client.post("/browser/navigate", json={"url": FAIL, "session": name})
        assert refused.status_code >= 400, refused.text
        assert refused.json()["error"].endswith(note), refused.text
        ran = client.post("/flows/broken/runs", json={"session": name})
        assert ran.json()["steps"][0]["error"].endswith(note), ran.text
    assert _sessions_named(caplog, "7f3a", "session://Downloads") == []


async def test_a_refused_address_names_no_session_on_any_surface(
    wired, caplog, monkeypatch
):
    """I2: a listing given where a file goes is refused without the name, over
    REST, as a flow step and over MCP; and the log never names it."""
    _with_session(wired, monkeypatch, DL, "g-dl")
    caplog.set_level(logging.DEBUG)
    upload = {"selector": {"css": "input"}}
    addresses = [
        f"session://{DL}/downloads",
        f"session://{DL}/console",
        "session://Downloads/downloads",
    ]
    client = TestClient(wired.mcp.server.http_app(), headers={**AUTH, "Sf-Workspace": NAMED})
    for uri in addresses:
        refused = client.post("/browser/upload", json={**upload, "file": uri})
        assert refused.status_code == 400, refused.text
        assert "session://…" in refused.json()["error"], refused.text
        assert DL not in refused.text and "Downloads/" not in refused.text
    wired.flows.save(NAMED, "listing", {"steps": [
        {"tool": "upload_file", "args": {**upload, "file": addresses[0]}},
    ]})
    ran = client.post("/flows/listing/runs", json={})
    assert ran.json()["status"] == "failed", ran.text
    async with Client(wired.mcp.server) as c:
        for uri in addresses:
            with pytest.raises(ToolError) as said:
                await c.call_tool("keep_file", {"uri": uri})
            assert "7f3a" not in str(said.value) and "Downloads/" not in str(said.value)
    assert _sessions_named(caplog, "7f3a", "session://Downloads") == []


async def test_an_error_texts_placeholders_reach_the_caller_as_written(server):
    """M2: the mask keeps `<…>` and `{…}`, so a caller reads the shapes."""
    async with Client(server.mcp.server) as c:
        with pytest.raises(ToolError) as shapes:
            await c.call_tool("keep_file", {"uri": "workspace://nowhere/x"})
        assert "session://<session>/downloads/<name>" in str(shapes.value)
        with pytest.raises(ToolError) as step:
            await c.call_tool("save_flow", {"name": "n", "steps": [
                {"tool": "network_body", "args": {"id": "1.1"}},
            ]})
        assert "session://<name>/network" in str(step.value)


async def test_a_named_sessions_capture_off_says_how_for_that_session(server):
    """M5: a named session's capture is turned on by naming it."""
    store = server.workspaces.store
    store.update(TESTER, lambda r: replace(r, settings={"capture": False}))
    store.update(NAMED, lambda r: replace(r, settings={"capture": False}))
    for uri in ("session://tester/console", "session://tester/network",
                "session://tester/network?filter=failed", "session://default/console"):
        off = await read(server, uri)
        assert off == {"capture": False, "how": "open_session(session=…, capture=true)"}, uri
    own = await read(server, "workspace://console")
    assert own == {"capture": False, "how": "open_session(capture=true)"}


# ---- a named session's reads, scrubbed with that session's secrets -------------

OLD = "hunter2"


class TextGrid(AliveGrid):
    """Each browser has a text download that holds what was typed in it."""

    def files(self, session_id):
        return [{"name": f"{OLD}.txt", "size": 9, "creationTime": 1}]

    def read_file(self, session_id, name):
        return f"typed {OLD} at b".encode()


async def test_a_named_sessions_download_is_scrubbed_with_its_own_secrets(
    wired, tmp_path
):
    """M3: a value `tester` typed, rotated out of the catalogue since, is still
    `tester`'s to hide: its download, its listing and keep_file of it are
    scrubbed with `tester`'s forms, not the default's."""
    wired.workspaces.actions.grid = TextGrid()
    async with Client(wired.mcp.server) as c:
        await c.call_tool("write", {**TYPED, "session": "tester"})
        # A rotation: the value tester typed is no longer a secret's.
        (tmp_path / "secrets" / "demo" / "password").write_text("hunter3")
        wired.workspaces.scrubber._catalogue._cache = None
        [content] = await c.read_resource(f"session://tester/downloads/{OLD}.txt")
        assert OLD not in base64.b64decode(content.blob).decode(), "the download"
        [listing] = await c.read_resource("session://tester/downloads")
        assert OLD not in listing.text, "the listing"
        kept = await c.call_tool("keep_file", {"uri": f"session://tester/downloads/{OLD}.txt"})
        assert OLD not in json.dumps(kept.structured_content), "keep_file"
        # The default never typed it: its own reads are as they were.
        [mine] = await c.read_resource(f"session://downloads/{OLD}.txt")
        assert OLD in base64.b64decode(mine.blob).decode()
    client = TestClient(wired.mcp.server.http_app(), headers={**AUTH, "Sf-Workspace": NAMED})
    kept = client.put(f"/files/downloads/{OLD}.txt/kept", json={"session": "tester"})
    assert kept.status_code == 200, kept.text
    assert OLD not in kept.text, "REST keep"
