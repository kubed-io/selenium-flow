"""Every MCP tool call is timed (RUN-32, RUN-34): one ``call <tool>`` span and
one ``selenium_flow.call.duration`` point, whatever the tool and however it
came out. A call that takes a turn is timed by `Calls.begin` and `Calls.end`;
one that takes none (`open_session`, `end_browser`, `await_control`, `show`,
`keep_file` and the flow tools) by `Calls.timed`.

The coverage test lists the registered tools, so a tool added later without its
timing fails it by name."""

from __future__ import annotations

import asyncio

import pytest
import requests
from fastmcp import Client
from opentelemetry.trace import StatusCode
from starlette.testclient import TestClient

from kubed.selenium_flow import faults
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.capabilities import capability
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.telemetry import CALL_DURATION
from kubed.selenium_flow.workspace.workspaces import Caller

from .conftest import NAMED, calling_as
from .fakes import points, recorded, untabbed

pytestmark = pytest.mark.unit

GRID = {"url": "http://grid.invalid:4444"}
# Each call returns well inside this, or fails by name.
SOON = 10

# A tool timed by nothing. An exemption is a decision, named in RUN-32 with its
# reason before it is added here; there is none.
EXEMPT: frozenset = frozenset()

FLOW = [{"tool": "navigate", "args": {"url": "https://example.com/"}}]

# Every tool's arguments for one call. A tool missing from here fails the
# coverage test by name, so a new tool cannot slip through untimed.
ARGUMENTS: dict[str, dict] = {
    "open_session": {},
    "end_browser": {},
    "await_control": {},
    "network_body": {"id": "3.7"},
    "save_site_data": {},
    "navigate": {"url": "https://example.com/"},
    "interact": {"action": "click", "selector": {"css": "button"}},
    "drag": {"selector": {"css": "#a"}, "by_x": 5, "by_y": 5},
    "frame": {"action": "default"},
    "tab": {"action": "list"},
    "resize": {"width": 800, "height": 600},
    "dialog": {"action": "accept"},
    "upload_file": {"selector": {"css": "input"}, "filename": "a.txt", "text": "hi"},
    "write": {"text": "hi", "selector": {"css": "input"}},
    "press_key": {"key": "Enter"},
    "extract": {},
    "outline": {},
    "execute_script": {"script": "return 1"},
    "assert": {"script": "return true"},
    "screenshot": {},
    "print": {},
    "keep_file": {"uri": "workspace://files/missing.png"},
    "show": {"uri": "flow://flows"},
    "save_flow": {"name": "f", "steps": FLOW},
    "run_flow": {"name": "missing"},
    "delete_flow": {"name": "f"},
}


def answered(*_args, **_kwargs) -> dict:
    """An action that returns at once, on a page."""
    return {"url": "https://example.com/", "title": "Example"}


def grid_down(*_args, **_kwargs):
    raise requests.ConnectionError("the Grid is not there")


@pytest.fixture
def timed(tmp_path, monkeypatch):
    """A server on recording telemetry with flows and files, whose Grid is never
    dialled: every action answers at once and an open fails at the Grid."""
    monkeypatch.delenv("DATA_DIR", raising=False)
    rec = recorded()
    server = untabbed(
        SeleniumMCP(
            Settings(grid=GRID, data={"dir": str(tmp_path)}), telemetry=rec.telemetry
        )
    )
    calling_as(monkeypatch, NAMED)
    monkeypatch.setattr(server.workspaces, "resolved", lambda name: ("grid-1", "chrome"))
    monkeypatch.setattr(server.actions, "open_session", grid_down)
    return server, rec


def act_quickly(server, monkeypatch, tools) -> None:
    """Every action behind ``tools`` answers at once, so each reaches `_said`."""
    for name in tools:
        try:
            method = capability(name).method
        except KeyError:
            continue
        if method and hasattr(server.actions, method) and name not in (
            "open_session", "end_browser"
        ):
            monkeypatch.setattr(server.actions, method, answered)


async def call(client, name: str, arguments: dict):
    """One bounded call; a wait that never ends fails by the tool's name."""
    try:
        async with asyncio.timeout(SOON):
            return await client.call_tool(name, arguments, raise_on_error=False)
    except TimeoutError:
        pytest.fail(f"{name} never returned")


def duration_count(rec, tool: str, surface: str) -> int:
    return sum(
        point.count
        for attributes, point in points(rec.metrics, CALL_DURATION)
        if attributes["selenium_flow.tool"] == tool
        and attributes["selenium_flow.surface"] == surface
    )


def spans_of(rec, tool: str) -> list:
    return [s for s in rec.spans.get_finished_spans() if s.name == f"call {tool}"]


async def test_every_mcp_tool_call_has_a_span_and_a_duration(timed, monkeypatch):
    server, rec = timed
    # Read without the middleware that hides the app tools: `show` is a tool
    # whether a client can draw it or not.
    registered = {t.name for t in await server.mcp.server.list_tools(run_middleware=False)}
    assert "show" in registered
    missing = sorted(registered - ARGUMENTS.keys() - EXEMPT)
    assert not missing, f"a tool with no arguments in ARGUMENTS: {missing}"
    assert not ARGUMENTS.keys() - registered, "ARGUMENTS names a tool that is gone"
    act_quickly(server, monkeypatch, registered)
    async with Client(server.mcp.server) as client:
        for name in sorted(registered - EXEMPT):
            await call(client, name, ARGUMENTS[name])
    wrong = {}
    for name in sorted(registered - EXEMPT):
        spans = spans_of(rec, name)
        surfaces = [s.attributes["selenium_flow.surface"] for s in spans]
        counted = duration_count(rec, name, "mcp")
        if surfaces != ["mcp"] or counted != 1:
            wrong[name] = f"spans on {surfaces}, {counted} mcp point(s)"
    assert not wrong, f"tools not timed once on mcp: {wrong}"


# ---- open_session and end_browser -------------------------------------------


async def test_an_open_that_fails_at_the_grid_is_a_5xx(timed):
    server, rec = timed
    async with Client(server.mcp.server) as client:
        result = await call(client, "open_session", {"browser": "firefox"})
    assert result.is_error
    [span] = spans_of(rec, "open_session")
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["selenium_flow.outcome"] == "5xx"
    assert span.attributes["selenium_flow.browser"] == "firefox"
    assert span.attributes["selenium_flow.workspace"] == NAMED
    [(attributes, point)] = points(rec.metrics, CALL_DURATION)
    assert (attributes["selenium_flow.outcome"], point.count) == ("5xx", 1)


def test_an_open_with_a_bad_browser_is_a_4xx(timed):
    server, rec = timed
    with pytest.raises(ValueError, match="safari"):
        server.workspaces.open_browser(Caller(NAMED), browser="safari")
    [span] = spans_of(rec, "open_session")
    assert span.attributes["selenium_flow.outcome"] == "4xx"
    # Never what the caller typed: a metric's attribute is a closed set.
    assert span.attributes["selenium_flow.browser"] == ""
    [(attributes, _)] = points(rec.metrics, CALL_DURATION)
    assert attributes["selenium_flow.outcome"] == "4xx"
    assert attributes["selenium_flow.surface"] == "mcp"


class Shut:
    """A person holds every browser."""

    def refusal(self, session_id, tool):
        return faults.Controlled("a person has control of this browser")


def held(server) -> None:
    server.workspaces.remember(NAMED, "grid-1", "", {"browser": "chrome"})
    server.workspaces.calls.control = Shut()


def test_a_refused_open_records_nothing(timed):
    server, rec = timed
    held(server)
    with pytest.raises(faults.Controlled):
        server.workspaces.open_browser(Caller(NAMED))
    assert rec.spans.get_finished_spans() == ()
    assert points(rec.metrics, CALL_DURATION) == []


def test_an_end_with_no_browser_open_is_ok(timed):
    server, rec = timed
    server.workspaces.end_browser(Caller(NAMED))
    [span] = spans_of(rec, "end_browser")
    assert span.attributes["selenium_flow.outcome"] == "ok"
    assert span.attributes["selenium_flow.browser"] == ""
    [(attributes, point)] = points(rec.metrics, CALL_DURATION)
    assert (attributes["selenium_flow.tool"], attributes["selenium_flow.outcome"]) == (
        "end_browser", "ok"
    )
    assert point.count == 1


def test_an_end_names_the_browser_it_ends(timed, monkeypatch):
    server, rec = timed
    server.workspaces.remember(NAMED, "grid-1", "", {"browser": "firefox"})
    monkeypatch.setattr(server.actions, "end_browser", lambda session_id: {})
    server.workspaces.end_browser(Caller(NAMED), surface="http")
    [span] = spans_of(rec, "end_browser")
    assert span.attributes["selenium_flow.browser"] == "firefox"
    assert span.attributes["selenium_flow.surface"] == "http"


def test_a_refused_end_records_nothing(timed):
    server, rec = timed
    held(server)
    with pytest.raises(faults.Controlled):
        server.workspaces.end_browser(Caller(NAMED))
    assert rec.spans.get_finished_spans() == ()
    assert points(rec.metrics, CALL_DURATION) == []


def test_the_admins_end_is_not_a_call(timed, monkeypatch):
    server, rec = timed
    held(server)
    monkeypatch.setattr(server.actions, "end_browser", lambda session_id: {})
    server.workspaces.end_browser(Caller(NAMED, "admin"), admin=True)
    assert rec.spans.get_finished_spans() == ()
    assert points(rec.metrics, CALL_DURATION) == []


def test_the_browser_resource_over_http_is_timed_as_http(timed):
    server, rec = timed
    client = TestClient(server.mcp.server.http_app(), headers={"Sf-Workspace": NAMED})
    assert client.post("/browser", json={}).status_code == 503
    assert client.delete("/browser").status_code == 200
    for tool in ("open_session", "end_browser"):
        [span] = spans_of(rec, tool)
        assert span.attributes["selenium_flow.surface"] == "http", tool
        assert duration_count(rec, tool, "http") == 1, tool
        assert duration_count(rec, tool, "mcp") == 0, tool


# ---- run_flow ---------------------------------------------------------------


async def test_a_run_is_one_mcp_call_beside_its_steps(timed, monkeypatch):
    server, rec = timed
    monkeypatch.setattr(server.actions, "navigate", answered)
    async with Client(server.mcp.server) as client:
        await call(client, "save_flow", {"name": "f", "steps": FLOW * 2})
        result = await call(client, "run_flow", {"name": "f"})
    assert not result.is_error, result
    assert duration_count(rec, "run_flow", "mcp") == 1
    assert duration_count(rec, "navigate", "flow") == 2


async def test_a_runs_steps_are_its_siblings_never_its_children(timed, monkeypatch):
    """The run's span is never made current (RUN-34): it and its steps' spans
    are children of the span current around the call, as `tools/call` is."""
    server, rec = timed
    monkeypatch.setattr(server.actions, "navigate", answered)
    save = (await server.mcp.get_tool("save_flow")).fn
    run_flow = (await server.mcp.get_tool("run_flow")).fn
    await save(name="f", steps=FLOW * 2)
    with rec.telemetry.tracer.start_as_current_span("tools/call") as outer:
        async with asyncio.timeout(SOON):
            report = await run_flow(name="f")
    assert report["status"] == "ok"
    [run] = spans_of(rec, "run_flow")
    steps = spans_of(rec, "navigate")
    assert len(steps) == 2
    parent = outer.get_span_context().span_id
    assert {s.parent.span_id for s in [run, *steps]} == {parent}
