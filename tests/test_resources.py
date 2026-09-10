"""The session status: one thing, offered as a resource and as a tool.

Resources are the least widely implemented part of MCP, so the same status has
to be reachable by a client that has no concept of them. These tests pin both
shapes and the switch between them.
"""

import pytest

from kubed.selenium_flow import resources as resources_module
from kubed.selenium_flow.resources import RESOURCE_URI, STATUS_TOOL, client_reads_resources
from kubed.selenium_flow.sessions import CallerKey, SessionManager
from kubed.selenium_flow.store import MemoryStore, SessionRecord

pytestmark = pytest.mark.unit

NAMED = CallerKey("named:desktop", "named")


class FakeGrid:
    def __init__(self):
        self.alive = set()

    def is_alive(self, session_id):
        return session_id in self.alive


class RecordingActions:
    def __init__(self):
        self.opened = 0
        self.grid = FakeGrid()

    def open_session(self, url=None, **_kwargs):
        self.opened += 1
        return {"session_id": f"generated-{self.opened}", "url": url or "about:blank"}


def http(params=None, headers=None):
    return dict(params or {}), dict(headers or {})


# ---- which shape a client gets ---------------------------------------------


def test_resources_are_assumed_supported(monkeypatch):
    """A spec-complete client is the default assumption."""
    monkeypatch.setattr(resources_module, "_http", lambda: http())
    assert client_reads_resources() is True


@pytest.mark.parametrize("value", ["off", "false", "0", "no", "none", "OFF"])
def test_a_client_can_declare_it_cannot_read_resources(monkeypatch, value):
    monkeypatch.setattr(resources_module, "_http", lambda: http({"resources": value}))
    assert client_reads_resources() is False


def test_the_header_wins_here_too(monkeypatch):
    """Same precedence rule as session names, for the same reason."""
    monkeypatch.setattr(
        resources_module,
        "_http",
        lambda: http({"resources": "on"}, {"x-mcp-resources": "off"}),
    )
    assert client_reads_resources() is False


def test_stdio_clients_are_assumed_to_read_resources(monkeypatch):
    monkeypatch.setattr(resources_module, "_http", lambda: None)
    assert client_reads_resources() is True


# ---- how that shows up on the server ---------------------------------------


async def test_the_resource_is_always_offered(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert RESOURCE_URI in uris


async def test_the_status_tool_is_hidden_from_clients_that_read_resources(server):
    """Two ways to ask one question is noise, so the tool is filtered out."""
    names = {t.name for t in await server.mcp.list_tools()}
    assert STATUS_TOOL not in names


async def test_the_status_tool_appears_for_a_client_that_cannot(server, monkeypatch):
    monkeypatch.setattr(
        resources_module, "_http", lambda: http({"resources": "off"})
    )
    names = {t.name for t in await server.mcp.list_tools()}
    assert STATUS_TOOL in names


async def test_the_hidden_tool_is_still_registered_and_callable(server):
    """Hiding is a presentation choice; refusing to run it would be a contract."""
    assert await server.mcp.get_tool(STATUS_TOOL) is not None


# ---- session_id is advertised per mode --------------------------------------


def saved(monkeypatch):
    monkeypatch.setattr(
        "kubed.selenium_flow.sessions.http_request", lambda: http({"session": "d"})
    )


def stateless(monkeypatch):
    monkeypatch.setattr("kubed.selenium_flow.sessions.http_request", lambda: http())


async def test_saved_mode_does_not_advertise_session_id(server, monkeypatch):
    """A model cannot pass what it cannot see, which is the point."""
    saved(monkeypatch)
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("navigate", "extract", "interact", "end_browser"):
        assert "session_id" not in tools[name].parameters["properties"], name


async def test_stateless_mode_makes_session_id_required(server, monkeypatch):
    """So a caller reads that it is mandatory instead of discovering it by
    failing a call."""
    stateless(monkeypatch)
    tools = {t.name: t for t in await server.mcp.list_tools()}
    schema = tools["navigate"].parameters
    assert "session_id" in schema["required"]
    # and the null branch is gone, since null is never valid here
    assert schema["properties"]["session_id"] == {
        "type": "string",
        "description": "Required: this server cannot identify you, so you own the session.",
    }


async def test_open_session_is_the_same_in_both_modes(server, monkeypatch):
    """It has no session_id to shape, and both modes call it identically."""
    saved(monkeypatch)
    a = {t.name: t for t in await server.mcp.list_tools()}["open_session"].parameters
    stateless(monkeypatch)
    b = {t.name: t for t in await server.mcp.list_tools()}["open_session"].parameters
    assert a == b
    assert "session_id" not in a["properties"]


async def test_shaping_does_not_leak_between_clients(server, monkeypatch):
    """The registered tools are shared, so shaping must copy rather than mutate.

    Otherwise the first client to list tools decides what every later client
    sees, which is the worst kind of bug: correct in testing, wrong in use.
    """
    saved(monkeypatch)
    await server.mcp.list_tools()
    registered = await server.mcp.get_tool("navigate")
    assert "session_id" in registered.parameters["properties"], (
        "the registered tool was mutated in place"
    )
    stateless(monkeypatch)
    tools = {t.name: t for t in await server.mcp.list_tools()}
    assert "session_id" in tools["navigate"].parameters["properties"]


# ---- what the status says --------------------------------------------------


def manager(actions=None, store=None):
    return SessionManager(actions or RecordingActions(), store or MemoryStore())


def test_describe_reports_nothing_held_without_opening_one(monkeypatch):
    """Reading a status resource must never create a browser."""
    monkeypatch.setattr(
        "kubed.selenium_flow.sessions.http_request", lambda: http({"session": "desktop"})
    )
    actions = RecordingActions()
    status = manager(actions).describe()
    assert status["session_id"] is None
    assert status["key"] == "named:desktop"
    assert actions.opened == 0, "describe must never open a browser"


def test_describe_reports_a_held_session_and_its_liveness(monkeypatch):
    monkeypatch.setattr(
        "kubed.selenium_flow.sessions.http_request", lambda: http({"session": "desktop"})
    )
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.store.set(
        NAMED.value, SessionRecord(session_id="abc", url="https://example.com")
    )
    status = sessions.describe()
    assert status["session_id"] == "abc"
    assert status["url"] == "https://example.com"
    assert status["live"] is True
    assert status["key_source"] == "named"


def test_describe_flags_a_session_the_grid_has_reaped(monkeypatch):
    """The question a caller actually has: is my browser still there?"""
    monkeypatch.setattr(
        "kubed.selenium_flow.sessions.http_request", lambda: http({"session": "desktop"})
    )
    sessions = manager()
    sessions.store.set(NAMED.value, SessionRecord(session_id="dead"))
    assert sessions.describe()["live"] is False


def test_describe_says_when_there_is_no_key_at_all(monkeypatch):
    monkeypatch.setattr("kubed.selenium_flow.sessions.http_request", lambda: http())
    status = manager().describe()
    assert status["key"] is None and status["session_id"] is None
