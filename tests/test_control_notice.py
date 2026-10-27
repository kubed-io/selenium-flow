"""The agent hears, on every MCP result, that a person has control of its browser."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.names import RECORDINGS_DIR
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import http
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


@pytest.fixture
def server(tmp_path, monkeypatch):
    name_as(monkeypatch, "w1")
    return make(tmp_path)


def test_a_controlled_workspace_s_result_carries_the_notice(server):
    held(server.tunnels, "w1", "drk", "control")
    result = keep(server)
    assert result.content[-1].text.startswith("drk took control of this browser at")
    assert result.content[-1].text == server.tunnels.notice("w1")
    assert result.structured_content["uri"] == "workspace://files/rec-20261008-1403.mp4"


def test_a_failed_call_carries_it_after_its_own_sentence(server):
    held(server.tunnels, "w1", "drk", "control")
    with pytest.raises(ToolError) as caught:
        keep(server, "workspace://files/recordings/nope.mp4")
    said = str(caught.value)
    assert "nope.mp4" in said
    assert said.endswith("\n\n" + server.tunnels.notice("w1"))
    assert said.count("took control of this browser") == 1


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
    before = web.post("/browser", json={}, headers={"X-Workspace": "w1"})
    held(server.tunnels, "w1", "drk", "control")
    after = web.post("/browser", json={}, headers={"X-Workspace": "w1"})
    assert (after.status_code, after.text) == (before.status_code, before.text)
    assert "control of this browser" not in after.text
    assert server.tunnels.notice("w1").startswith("drk took control")
