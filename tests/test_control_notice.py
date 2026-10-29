"""The agent hears, on every MCP result, that a person has control of its browser."""

import asyncio
import base64
import re
from datetime import timedelta

import anyio.from_thread
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import call_tool_task
from fastmcp_tasks.models import CreateTaskResult
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.http import files as files_module
from kubed.selenium_flow.live.control import HANDED_BACK, HELD, NOTICE_REQUESTED
from kubed.selenium_flow.live.tunnel import Tunnel
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.names import RECORDINGS_DIR
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace import locks

from .conftest import http
from .fakes import FakePeer
from .test_live_tunnels import held

pytestmark = pytest.mark.unit

URI = "workspace://files/recordings/rec-20261008-1403.mp4"


def make(tmp_path):
    server = SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)}))
    for name in ("w1", "w2"):
        server.flows.write_file(name, "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
    return server


def name_as(monkeypatch, workspace):
    values = http({"workspace": workspace} if workspace else None)
    monkeypatch.setattr(clients_module, "request_values", lambda: values)


def keep(server, uri=URI):
    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool("keep_file", {"uri": uri})
    return asyncio.run(go())


def texts(result):
    return [c.text for c in result.content if c.type == "text"]


ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
HHMM = re.compile(r"\d\d:\d\d")


def hint_at(sentence, notice):
    """``sentence`` as the notice says it, its HH:MM taken from the hint."""
    at = HHMM.search(notice["hint"]).group(0)
    return sentence.format(who=" and ".join(notice["who"]), at=at)


def on_loop(fn):
    """Run ``fn`` on the event loop, from a tool's worker thread or the loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return anyio.from_thread.run_sync(fn)
    return fn()


def handed_back(server):
    tunnel = held(server.tunnels, "w1", "drk", "control")
    tunnel.mode = "watch"
    tunnel.on_mode()


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


@pytest.fixture
def server(tmp_path, monkeypatch):
    name_as(monkeypatch, "w1")
    return make(tmp_path)


def test_a_controlled_workspace_s_result_carries_the_notice(server):
    held(server.tunnels, "w1", "drk", "control")

    async def go():
        async with Client(server.mcp) as c:
            result = await c.call_tool("keep_file", {"uri": URI})
            return result, server.controls.notice("w1")

    result, notice = asyncio.run(go())
    assert result.content[-1].text.startswith("drk has control of this browser since")
    assert result.content[-1].text == notice["hint"]
    assert result.structured_content["uri"] == "workspace://files/rec-20261008-1403.mp4"
    assert result.structured_content["control"] == notice


def test_a_failed_call_carries_it_after_its_own_sentence(server):
    held(server.tunnels, "w1", "drk", "control")

    async def go():
        async with Client(server.mcp) as c:
            with pytest.raises(ToolError) as caught:
                await c.call_tool("keep_file", {"uri": "workspace://files/recordings/nope.mp4"})
            return str(caught.value), server.controls.notice("w1")

    said, notice = asyncio.run(go())
    assert "nope.mp4" in said
    assert said.endswith("\n\n" + notice["hint"])
    assert said.count("has control of this browser") == 1


def test_handed_back_is_said_once(server):
    tunnel = held(server.tunnels, "w1", "drk", "control")
    tunnel.mode = "watch"
    tunnel.on_mode()
    first = keep(server)
    assert first.content[-1].text.startswith("control of this browser was handed back at")
    server.flows.write_file("w1", "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
    second = keep(server)
    assert all("control of this browser" not in t for t in texts(second))


def test_a_workspace_with_no_controller_hears_nothing(server):
    result = keep(server)
    assert all("control of this browser" not in t for t in texts(result))


def test_another_workspace_hears_nothing(server, monkeypatch):
    held(server.tunnels, "w1", "drk", "control")
    name_as(monkeypatch, "w2")
    assert all("control of this browser" not in t for t in texts(keep(server)))


def test_an_unnamed_caller_hears_nothing_and_its_answer_is_its_own(server, monkeypatch):
    held(server.tunnels, "w1", "drk", "control")
    name_as(monkeypatch, None)
    with pytest.raises(ToolError) as caught:
        keep(server)
    assert "control of this browser" not in str(caught.value)


def test_a_fresh_server_hears_nothing(server, tmp_path):
    held(server.tunnels, "w1", "drk", "control")
    fresh = make(tmp_path / "fresh")
    assert all("control of this browser" not in t for t in texts(keep(fresh)))


def test_the_http_surface_is_unchanged(server):
    web = TestClient(server.mcp.http_app())
    before = web.post("/browser", json={}, headers={"Sf-Workspace": "w1"})
    held(server.tunnels, "w1", "drk", "control")
    after = web.post("/browser", json={}, headers={"Sf-Workspace": "w1"})
    assert (after.status_code, after.text) == (before.status_code, before.text)
    assert "control of this browser" not in after.text
    assert server.controls.notice("w1") is not None


async def test_an_in_flight_call_carries_control_in_structured_content(server, monkeypatch):
    """Asked mid-call: the call before the request still holds its turn, so the
    grant waits, and the call's result says ``requested``."""
    tunnel = Tunnel(FakePeer(), who="kelly", workspace="w1", session="s", grace=1)
    server.tunnels.add("g1", tunnel)
    parse = files_module.parse_uri

    def asked_mid_call(uri):
        tunnel.mode = on_loop(tunnel.on_ask)
        return parse(uri)

    monkeypatch.setattr(files_module, "parse_uri", asked_mid_call)
    async with Client(server.mcp) as c:
        with locks.hold("g1").lock:  # the call's own turn on the session
            result = await c.call_tool("keep_file", {"uri": URI})
    control = result.structured_content["control"]
    assert control["state"] == "requested" and control["who"] == ["kelly"]
    assert ISO.fullmatch(control["since"])
    assert control["hint"] == hint_at(NOTICE_REQUESTED, control)
    assert result.content[-1].text == control["hint"]
    assert result.structured_content["uri"] == "workspace://files/rec-20261008-1403.mp4"


async def test_a_read_while_held_carries_state_held(server):
    await asyncio.to_thread(held, server.tunnels, "w1", "drk", "control")
    async with Client(server.mcp) as c:
        result = await c.call_tool("keep_file", {"uri": URI})
        assert server.controls.notice("w1")["state"] == "held"  # said every time
    control = result.structured_content["control"]
    assert (control["state"], control["who"]) == ("held", ["drk"])
    assert ISO.fullmatch(control["since"])
    assert control["hint"] == hint_at(HELD, control)
    assert result.content[-1].text == control["hint"]


async def test_the_unsaid_hand_back_is_said_once(server):
    await asyncio.to_thread(handed_back, server)
    async with Client(server.mcp) as c:
        first = await c.call_tool("keep_file", {"uri": URI})
        server.flows.write_file("w1", "rec-20261008-1403.mp4", b"\x00" * 64, RECORDINGS_DIR)
        second = await c.call_tool("keep_file", {"uri": URI})
    control = first.structured_content["control"]
    assert (control["state"], control["who"]) == ("handed_back", ["drk"])
    assert ISO.fullmatch(control["since"])
    assert control["hint"] == HANDED_BACK.format(at=HHMM.search(control["hint"]).group(0))
    assert first.content[-1].text == control["hint"]
    assert "control" not in second.structured_content
    assert all("control of this browser" not in t for t in texts(second))


async def test_a_refusal_carries_no_second_notice(server, monkeypatch):
    await asyncio.to_thread(held, server.tunnels, "w1", "drk", "control")

    def refused(name, call, **kw):
        raise server.controls.refusal("g1", "navigate")

    monkeypatch.setattr(server.workspaces, "act", refused)
    async with Client(server.mcp) as c:
        with pytest.raises(ToolError) as caught:
            await c.call_tool("navigate", {"url": "https://example.test"})
    said = str(caught.value)
    assert said.count("has control of this browser") == 1
    assert said.count("await_control") == 1
    assert said.endswith("which returns when they hand back.")


async def test_a_screenshot_image_gains_structured_content_with_only_control(server, monkeypatch):
    await asyncio.to_thread(held, server.tunnels, "w1", "drk", "control")
    monkeypatch.setattr(server.workspaces, "act", lambda name, call, **kw: {"image": PNG})
    async with Client(server.mcp) as c:
        result = await c.call_tool("screenshot", {})
        notice = server.controls.notice("w1")
    assert result.content[0].type == "image"
    assert result.structured_content == {"control": notice}
    assert result.content[-1].text == notice["hint"]


class Seen(Middleware):
    def __init__(self, into):
        self.into = into

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        self.into.append(result)
        return result


async def test_a_create_task_result_passes_untouched(server):
    """R1: a call that becomes a task returns its stub, untouched, and a pending
    hand-back is not spent on it: the stub cannot say it."""
    outer, inner = [], []
    # The server registers the tasks extension itself (`mcp.waiting.Tasks`).
    server.mcp.middleware.insert(0, Seen(outer))  # outside `Notices`
    server.mcp.add_middleware(Seen(inner))  # inside it

    @server.mcp.tool(task=TaskConfig(mode="optional", poll_interval=timedelta(seconds=1)))
    async def tasked() -> dict:
        return {"done": True}

    await asyncio.to_thread(handed_back, server)
    async with Client(server.mcp) as c:
        task = await call_tool_task(c, "tasked")
        result = await task.result()
        pending = server.controls.notice("w1")
    [stub] = inner
    assert isinstance(stub, CreateTaskResult)
    assert outer == [stub] and outer[0] is stub
    # The wire adds the server's own `meta`; everything the stub says arrives.
    assert task.create_result.model_dump(exclude={"meta"}) == stub.model_dump(exclude={"meta"})
    assert result.structured_content == {"done": True}
    assert pending is not None and pending["state"] == "handed_back"
