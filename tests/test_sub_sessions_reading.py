"""Reading a session: the context lists every session, `session://{name}` reads
a named one, and capture is kept and read per session (WS-72, WS-73, CAP-81)."""

from __future__ import annotations

import json
import time

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError

from kubed.selenium_flow.principal import Principal
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import MemoryStore, Workspace
from kubed.selenium_flow.workspace.workspaces import CONTEXT_KEYS

from .capture_events import log, nav_started
from .conftest import NAMED, calling_as

pytestmark = pytest.mark.unit

A_SITE = "https://a.example.test/"
B_SITE = "https://b.example.test/"
TESTER = sessions.key_of(NAMED, "tester")


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class AliveGrid:
    """Every browser lives; nothing else is asked of it."""

    def is_alive(self, session_id):
        return True

    def files(self, session_id):
        return []


def visit(url, session):
    return {"origin": url.rstrip("/"), "url": url, "at": time.time(), "session": session}


@pytest.fixture
def server(data_server, monkeypatch):
    """The named workspace with its default open on `A_SITE` and `tester` on
    `B_SITE`, both capturing."""
    data_server.workspaces.actions.grid = AliveGrid()
    clock = Clock()
    store = MemoryStore(clock=clock)
    data_server.workspaces.store = store
    data_server.clock = clock
    store.set(NAMED, Workspace(
        session_id="s1", settings={"capture": True}, subs=["tester"],
        history=[visit(B_SITE, "tester"), visit(A_SITE, "default")],
    ))
    store.set(TESTER, Workspace(
        session_id="s2", settings={"capture": True}, label="tester",
    ))
    calling_as(monkeypatch, NAMED)
    return data_server


def heard(server, key, session_id, text):
    capture = server.capture
    capture.attached(key, session_id, "chrome", f"top-{session_id}", again=False)
    for method, params in (nav_started("https://a.test/"), log(text, level="error")):
        capture.ingest(key, session_id, method, params)


async def read(server, uri):
    async with Client(server.mcp.server) as c:
        return json.loads((await c.read_resource(uri))[0].text)


def test_the_context_lists_every_session_default_first(server):
    context = server.workspaces.describe(_caller())
    assert context["session"] == "default" and context["default"] is True
    assert [(s["name"], s["default"], s["live"], s["url"]) for s in context["sessions"]] == [
        ("default", True, True, A_SITE),
        ("tester", False, True, B_SITE),
    ]
    assert {"session", "default", "sessions"} <= set(CONTEXT_KEYS)
    # The caller's own session describes itself, wherever it is pinned.
    there = server.workspaces.describe(_caller("tester"))
    assert (there["session"], there["default"], there["url"]) == ("tester", False, B_SITE)
    assert there["sessions"] == context["sessions"]
    part = server.workspaces.describe_part(_caller("tester"), "session")
    assert part == {"workspace": NAMED, "session": "tester"}


def stored(store):
    """Every record in full, by key."""
    return {
        **store.records(),
        **store.sub_records(NAMED),
    }


def test_reading_the_context_makes_no_record_and_refuses_an_unopened_session(server):
    store = server.workspaces.store
    before = stored(store)
    with pytest.raises(ValueError, match="no such session"):
        server.workspaces.describe(_caller("typo"))
    assert stored(store) == before


def test_reading_as_a_subject_claims_nothing_and_makes_nothing(server):
    from dataclasses import replace

    store = server.workspaces.store
    first = Principal("oidc", subject="sub-a", username="first")
    second = Principal("oidc", subject="sub-b", username="second")
    # An unowned default: reading is not claiming it.
    assert store.get(NAMED).owner is None
    before = stored(store)
    context = server.workspaces.describe(replace(_caller(), principal=first))
    assert context["session"] == "default"
    assert stored(store) == before and store.get(NAMED).owner is None
    # Another subject owns it: a subject with no sub-session yet reads an
    # empty one under its own name, and none is made or listed.
    store.update(NAMED, lambda r: replace(r, owner="sub-a"))
    before = stored(store)
    context = server.workspaces.describe(replace(_caller(), principal=second))
    assert (context["session"], context["default"], context["live"]) == (
        "sub-b", False, False,
    )
    assert context["url"] is None
    assert stored(store) == before
    assert store.get(NAMED).subs == ["tester"]
    assert store.get(sessions.key_of(NAMED, "sub-b")) is None


async def test_reading_a_named_session_leaves_the_records_as_they_were(server):
    store = server.workspaces.store
    before = stored(store)
    await read(server, "session://tester")
    await read(server, "session://tester/console")
    assert stored(store) == before
    assert store.get(NAMED).subs == ["tester"]


async def test_session_resource_reads_a_named_session(server):
    tester = await read(server, "session://tester")
    assert (tester["session"], tester["default"], tester["url"]) == (
        "tester", False, B_SITE,
    )
    default = await read(server, "session://default")
    assert (default["session"], default["default"], default["url"]) == (
        "default", True, A_SITE,
    )
    assert default["workspace"] == tester["workspace"] == NAMED
    assert (await read(server, "session://tester/downloads"))["files"] == []


async def test_session_name_resource_for_an_unknown_name_is_not_found(server):
    for uri in ("session://nobody", "session://nobody/console", "session://nobody/network"):
        with pytest.raises(MCPError) as missing:
            await read(server, uri)
        assert missing.value.error.code == -32002, uri
    assert server.workspaces.store.get(sessions.key_of(NAMED, "nobody")) is None


async def test_capture_is_kept_per_session(server):
    heard(server, NAMED, "s1", "from the default")
    heard(server, TESTER, "s2", "from tester")
    texts = lambda view: [e["text"] for e in view["entries"]]  # noqa: E731
    assert texts(await read(server, "workspace://console")) == ["from the default"]
    assert texts(await read(server, "session://tester/console")) == ["from tester"]
    assert texts(await read(server, "session://default/console")) == ["from the default"]
    network = await read(server, "session://tester/network")
    assert network["requests"] == []
    narrowed = await read(server, "session://tester/console?level=error")
    assert texts(narrowed) == ["from tester"]
    assert (await read(server, "session://tester"))["captured"]["errors"] == 1
    assert (await read(server, "workspace://current"))["captured"]["errors"] == 1


async def test_a_sub_sessions_capture_lasts_as_its_record(server):
    heard(server, TESTER, "s2", "from tester")
    capture = server.capture
    assert capture.counts(TESTER) is not None
    server.clock.now += 3601
    with pytest.raises(MCPError) as gone:
        await read(server, "session://tester/console")
    assert gone.value.error.code == -32002
    assert capture.counts(TESTER) is None, "the buffer goes with the record"
    heard(server, NAMED, "s1", "default")
    server.workspaces.forget(NAMED)
    assert capture.counts(NAMED) is None
    assert server.workspaces.store.get(NAMED) is None


def test_a_recreated_sub_session_starts_with_no_capture(server):
    heard(server, TESTER, "s2", "old")
    server.clock.now += 3601
    server.workspaces.session_of(_caller(), "tester", opening=True)
    assert server.capture.counts(TESTER) is None


def test_site_data_orders_by_the_callers_sessions_own_history(server):
    from kubed.selenium_flow.mcp import resources

    store = server.workspaces.store
    jar = {"cookies": [
        {"name": "n", "value": "1", "domain": "a.example.test"},
        {"name": "n", "value": "1", "domain": "b.example.test"},
    ]}
    store.update(TESTER, lambda r: r.with_site_data(jar))
    store.update(NAMED, lambda r: r.with_site_data(jar))
    # The default went to a last: read as every session's, a would lead.
    store.update(NAMED, lambda r: Workspace(**{
        **r.__dict__, "history": [visit(A_SITE, "default"), visit(B_SITE, "tester")],
    }))

    def sites(session):
        listing = resources.site_listing(server.workspaces, _caller(session))
        return [row["site"] for row in listing["sites"]]

    assert sites("tester") == ["b.example.test", "a.example.test"]
    assert sites(None) == ["a.example.test", "b.example.test"]


def _caller(session=None):
    from kubed.selenium_flow.workspace.workspaces import Caller

    params = {"workspace": [NAMED]}
    if session:
        params["session"] = [session]
    return Caller.from_request(params, {})




async def test_a_named_sessions_console_and_network_honour_their_query(server):
    """The same narrowing and refusals as workspace://console and /network."""
    heard(server, TESTER, "s2", "from tester")
    for tail, says in (
        ("console?level=bogus", "level is one of"),
        ("console?pages=9", "1 to 5"),
        ("network?filter=bogus", "filter is one of"),
        ("network?pages=0", "1 to 5"),
    ):
        with pytest.raises(MCPError, match=says):
            await read(server, "session://tester/" + tail)
    errors = await read(server, "session://tester/console?level=error")
    assert [e["text"] for e in errors["entries"]] == ["from tester"]
    assert (await read(server, "session://tester/console?level=warn"))["entries"] == []
    assert "entries" in await read(server, "session://tester/console")
    assert "requests" in await read(server, "session://tester/network?filter=failed")
    assert "requests" in await read(server, "session://tester/network")
