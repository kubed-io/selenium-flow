"""Pointers name the session a call acted in (#181): `workspace://…` and
`session://downloads…` for the caller's own, `session://<name>/…` for any
other; a pinned connection and an unnamed call see exactly what they always
did; and every `session://<name>/…` a pointer names can be read."""

from __future__ import annotations

import base64
import json
from dataclasses import replace

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
from .fakes import Anywhere, FakeMonitor, PageActions
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
    async with Client(server.mcp) as c:
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
    async with Client(server.mcp) as c:
        [content] = await c.read_resource("session://tester/downloads/g-tester.pdf")
    assert base64.b64decode(content.blob) == b"PDF of g-tester"


async def test_keep_file_takes_the_session_from_the_uri(server):
    uri = "session://tester/downloads/report.pdf"
    async with Client(server.mcp) as c:
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
    read = server.actions.read_file
    uri = "session://tester/downloads/report.pdf"
    with _acting_in(TESTER):
        assert read(uri) == ("report.pdf", b"PDF of g-tester")
    # Acting in its own session, a call may take another's download.
    with _acting_in(NAMED, NAMED):
        assert read(uri) == ("report.pdf", b"PDF of g-tester")
    # A call that named another session is refused, without the name.
    with _acting_in(sessions.key_of(NAMED, "zed"), NAMED), pytest.raises(
        ValueError
    ) as refused:
        read(uri)
    assert "tester" not in str(refused.value) and "zed" not in str(refused.value)
    with _acting_in(TESTER, NAMED):
        assert read("session://downloads/report.pdf") == ("report.pdf", b"PDF of g-tester")


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
    async with Client(server.mcp) as c:
        named = await c.call_tool("network_body", {"id": "1.1", "session": "tester"})
        assert named.structured_content["uri"] == "session://tester/network/1.1"
        own = await c.call_tool("network_body", {"id": "1.1"})
        assert own.structured_content["uri"] == "workspace://network/1.1"
    calling_as_pinned = lambda: http({"workspace": NAMED, "session": "tester"})  # noqa: E731
    monkeypatch.setattr(clients_module, "request_values", calling_as_pinned)
    async with Client(server.mcp) as c:
        pin = await c.call_tool("network_body", {"id": "1.1", "session": "tester"})
        assert pin.structured_content["uri"] == "workspace://network/1.1"


def test_the_admin_says_where_a_sub_sessions_request_is(server):
    client = TestClient(server.mcp.http_app(), headers=AUTH)
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
    async with Client(server.mcp) as c:
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
    async with Client(server.mcp) as c:
        for uri, component in drawn.items():
            shown = await c.call_tool("show", {"uri": uri})
            assert shown.structured_content["component"] == component, uri
    entry = shown.structured_content["data"]
    assert entry["uri"] == "session://tester/downloads/g-tester.pdf"
