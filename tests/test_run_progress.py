"""A long run is watchable, survives a client's idle timeout, and can be stopped.

The pilot behind §F2.15 lost a run that had succeeded: one `assert` waiting up to
fifteen minutes sent nothing for five, the client gave up, and the flow went on
to finish for nobody. These hold the three things that fix it, through the real
MCP client wherever the property is about what a client sees:

- every step is reported as it starts, and a long step keeps reporting;
- a flow declares its own budget, so a flow that exists to wait may;
- a caller that stops waiting stops the run, down inside a waiting `assert`.
"""

from __future__ import annotations

import threading
import time

import anyio
import pytest

from kubed.selenium_flow.core import cancel
from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.flows import run as flowrun
from kubed.selenium_flow.mcp import progress
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import NAMED, TOKEN

pytestmark = pytest.mark.unit

STEPS = [
    {"tool": "navigate", "args": {"url": "https://example.test/one"}},
    {"tool": "navigate", "args": {"url": "https://example.test/two"}},
    {"tool": "navigate", "args": {"url": "https://example.test/three"}},
]


@pytest.fixture
def slow_server(tmp_path, monkeypatch):
    """A server whose `navigate` takes a moment, and records that it ran."""
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        flow_data_dir=str(tmp_path),
    )
    monkeypatch.setattr(server.sessions, "name", lambda: NAMED)
    monkeypatch.setattr(server.sessions, "resolve", lambda name: "browser-1")
    server.ran = []

    def navigate(session_id, url=None, **_):
        time.sleep(server.pause)
        server.ran.append(url)
        return {"url": url, "title": "t"}

    server.pause = 0.15
    monkeypatch.setattr(server.actions, "navigate", navigate)
    monkeypatch.setattr(progress, "LOOK", 0.01)
    return server


async def _run_watched(server, name):
    from fastmcp import Client

    seen = []

    async def watching(value, total, message):
        seen.append((value, total, message))

    async with Client(server.mcp) as client:
        result = await client.call_tool(
            flowapi.RUN_TOOL, {"name": name}, progress_handler=watching
        )
    return result.data, seen


async def test_a_client_watching_a_run_sees_each_step_as_it_starts(slow_server):
    slow_server.flows.save(NAMED, "three", {"steps": STEPS})
    report, seen = await _run_watched(slow_server, "three")

    assert report["status"] == "ok"
    messages = [message for _, _, message in seen]
    for n in (1, 2, 3):
        assert any(m.startswith(f"step {n}/3: navigate") for m in messages), messages
    # The summary is the report's own safe line, so the URL is there.
    assert any("example.test/two" in m for m in messages)
    # The protocol requires progress to rise with every notification.
    values = [value for value, _, _ in seen]
    assert values == sorted(values) and len(set(values)) == len(values)
    assert {total for _, total, _ in seen if total} == {3}


async def test_one_long_step_keeps_reporting_while_it_waits(slow_server, monkeypatch):
    """The run that was lost was ONE step long, so a report per step would not
    have saved it. A heartbeat inside the step is what keeps a client's idle
    clock from running out."""
    monkeypatch.setattr(progress, "HEARTBEAT", 0.1)
    slow_server.pause = 0.8
    slow_server.flows.save(NAMED, "wait", {"steps": STEPS[:1]})
    report, seen = await _run_watched(slow_server, "wait")

    assert report["status"] == "ok"
    during = [m for _, _, m in seen if m.startswith("step 1/1")]
    assert len(during) >= 4, seen


async def test_a_caller_that_stops_waiting_stops_the_run(slow_server):
    """Before this, cancelling the call cancelled the coroutine and not the
    thread: the flow drove the browser to the end for nobody."""
    slow_server.pause = 0.3
    slow_server.flows.save(NAMED, "three", {"steps": STEPS})
    tool = await slow_server.mcp.get_tool(flowapi.RUN_TOOL)

    with anyio.move_on_after(0.1):
        await tool.fn(name="three")
    # Longer than the whole flow would have taken.
    await anyio.sleep(1.2)

    assert slow_server.ran == ["https://example.test/one"]


def test_a_cancelled_assert_lets_go_at_its_next_poll(monkeypatch):
    """The wait that outlives its caller is `assert`'s, so that is where the
    flag has to be read — through the real action, not a double of it."""
    from kubed.selenium_flow.core import actions as actions_module

    class Driver:
        def execute_script(self, script):
            return False

    actions = actions_module.Actions(actions_module.Grid("http://grid.invalid:4444"))
    monkeypatch.setattr(actions, "_at", lambda *a, **k: Driver())
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()

    started = time.monotonic()
    with cancel.watching(flag), pytest.raises(cancel.Cancelled):
        actions.assert_("abc", "return false", wait_timeout=900)
    assert time.monotonic() - started < 5


# ---- the budget belongs to the flow ------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 1.0
        return self.now


class Recording:
    def __init__(self):
        self.calls = []

    def navigate(self, session_id, **kwargs):
        self.calls.append(kwargs)
        return {"url": kwargs.get("url"), "title": "t"}


FIVE = [{"tool": "navigate", "args": {"url": str(n)}} for n in range(5)]


def test_a_flow_that_declares_a_longer_budget_gets_it(monkeypatch):
    monkeypatch.setattr(flowrun, "time", FakeClock())
    monkeypatch.setattr(flowrun, "RUN_TIMEOUT", 2)
    actions = Recording()
    report = flowrun.run(actions, {"name": "f", "timeout": 100, "steps": FIVE}, "b")
    assert report["status"] == "ok"
    assert len(actions.calls) == 5


def test_a_flow_that_declares_a_shorter_budget_is_held_to_it(monkeypatch):
    monkeypatch.setattr(flowrun, "time", FakeClock())
    actions = Recording()
    report = flowrun.run(actions, {"name": "f", "timeout": 2, "steps": FIVE}, "b")
    assert report["status"] == "failed"
    assert "2s budget" in report["steps"][-1]["error"]
    assert len(actions.calls) < 5


@pytest.mark.parametrize("given", ["soon", 0, -5, True, 1.5])
async def test_a_budget_that_is_not_seconds_is_refused_at_save(tmp_path, given):
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, flow_data_dir=str(tmp_path)
    )
    server.sessions.name = lambda: NAMED
    server.sessions.library = lambda: NAMED
    tool = await server.mcp.get_tool(flowapi.SAVE_TOOL)
    with pytest.raises(ValueError, match="timeout must be a whole number of seconds"):
        await tool.fn(name="bad", steps=STEPS[:1], timeout=given)


def test_a_hand_edited_bad_budget_is_refused_before_step_one():
    actions = Recording()
    report = flowrun.run(actions, {"name": "f", "timeout": "soon", "steps": FIVE}, "b")
    assert report["status"] == "failed"
    assert "timeout" in report["steps"][0]["error"]
    assert actions.calls == []


async def test_a_saved_budget_survives_the_round_trip(slow_server):
    tool = await slow_server.mcp.get_tool(flowapi.SAVE_TOOL)
    slow_server.sessions.library = lambda: NAMED
    await tool.fn(name="patient", steps=STEPS[:1], timeout=900)
    assert slow_server.flows.get(NAMED, "patient")["timeout"] == 900


def test_a_budget_saved_over_http_is_kept_too(slow_server):
    """The PUT body picks its keys by name, so a new key is a second place to
    forget - and the two surfaces may differ in shape, never in capability."""
    from starlette.testclient import TestClient

    client = TestClient(slow_server.mcp.http_app())
    response = client.put(
        "/flows/patient",
        json={"steps": STEPS[:1], "timeout": 900},
        headers={"Authorization": f"Bearer {TOKEN}", "X-Session-Key": NAMED},
    )
    assert response.status_code == 200, response.text
    assert slow_server.flows.get(NAMED, "patient")["timeout"] == 900


async def test_a_budget_given_as_text_is_stored_as_the_number_it_means(slow_server):
    """Coerced on the way in, so it must be kept as what it was coerced to — a
    read would otherwise hand back a string where the schema promises an
    integer (Copilot, #37)."""
    tool = await slow_server.mcp.get_tool(flowapi.SAVE_TOOL)
    slow_server.sessions.library = lambda: NAMED
    saved = await tool.fn(name="patient", steps=STEPS[:1], timeout="900")
    assert saved["timeout"] == 900
    assert slow_server.flows.get(NAMED, "patient")["timeout"] == 900


async def test_the_published_save_request_takes_a_budget(slow_server):
    """The HTTP save accepts it, so its published request body has to say so,
    or a generated client can never send one (Copilot, #37)."""
    from kubed.selenium_flow.routes import ENDPOINTS
    from kubed.selenium_flow.spec import build_spec

    spec = await build_spec(slow_server.mcp, ENDPOINTS, "", authenticated=True)
    body = spec["paths"]["/flows/{name}"]["put"]["requestBody"]["content"]
    schema = body["application/json"]["schema"]
    assert schema["properties"]["timeout"]["type"] == "integer"


def test_the_published_default_is_the_real_one():
    """The spec's schemas are written by hand; the number in them is not allowed
    to drift from the one the engine uses."""
    from kubed.selenium_flow.spec.schemas import FLOW_SCHEMAS

    text = FLOW_SCHEMAS["Flow"]["properties"]["timeout"]["description"]
    assert f"Defaults to {flowrun.RUN_TIMEOUT}" in text
