"""show(uri): one MCP App tool that draws a resource by its URI."""

import json
from unittest.mock import patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import apps, show
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit

GOOD = [{"tool": "navigate", "args": {"url": "https://example.test/"}}]


@pytest.fixture
def flow_server(tmp_path, named_caller):
    """A server with a flow library and a caller who has named their session."""
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        flow={"data_dir": str(tmp_path)},
    ))
    server.flows.save(named_caller, "login", {"steps": GOOD, "description": "Log in"})
    return server


@pytest.mark.parametrize(
    ("uri", "component"),
    [
        ("session://current", "context"),
        ("session://files", "files"),
        ("session://files/screenshots", "folder"),
        ("session://files/downloads", "folder"),
        ("flow://flows", "flows"),
        ("flow://flows/login", "flow"),
    ],
)
def test_every_showable_uri_has_one_view(uri, component):
    assert show.view_for(uri) == component


def test_the_table_and_the_list_of_showable_uris_agree():
    """SHOWABLE is what the tool says it draws, VIEWS what it can: every URI it
    names has a view, and every view is reachable from one it names."""
    reached = {show.view_for(uri.replace("{name}", "x")) for uri in show.SHOWABLE}
    assert reached == {component for _, component in show.VIEWS}


@pytest.mark.parametrize(
    "uri",
    ["skill://selenium-flow/SKILL.md", "flow://schema", "session://files/a.png",
     "flow://flows/", "session://current/x", "secret://secrets"],
)
def test_anything_else_is_refused_naming_what_can_be_shown(uri):
    with pytest.raises(ValueError) as refused:
        show.view_for(uri)
    for showable in show.SHOWABLE:
        assert showable in str(refused.value)


@pytest.mark.parametrize(
    ("uri", "component"),
    [
        ("flow://flows/login", "flow"),
        ("flow://flows", "flows"),
        ("session://files", "files"),
        ("session://files/downloads", "folder"),
        ("session://files/screenshots", "folder"),
    ],
)
async def test_show_returns_the_resources_own_json(flow_server, uri, component):
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool("show", {"uri": uri})).structured_content
        read = json.loads((await c.read_resource(uri))[0].text)
    assert shown == {"component": component, "uri": uri, "data": read}


async def test_a_kept_file_shows_as_the_resource_lists_it(flow_server, named_caller):
    flow_server.flows.write_file(named_caller, "report.pdf", b"%PDF-1.4")
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool("show", {"uri": "session://files"})).structured_content
        read = json.loads((await c.read_resource("session://files"))[0].text)

    def unsigned(data):
        # The signed url carries an expiry that can cross a second between calls.
        return {**data, "files": [{k: v for k, v in f.items() if k != "url"} for f in data["files"]]}

    assert [f["name"] for f in shown["data"]["files"]] == ["report.pdf"]
    assert unsigned(shown["data"]) == unsigned(read)


async def test_a_missing_flow_is_refused(flow_server):
    async with Client(flow_server.mcp) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "flow://flows/nope"})
    assert "flow://flows/nope" in str(refused.value)


async def test_an_unshowable_uri_is_refused_through_the_client(flow_server):
    async with Client(flow_server.mcp) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "flow://schema"})
    assert "flow://flows/{name}" in str(refused.value)


async def test_showing_the_files_never_opens_a_browser(flow_server):
    """Reading a listing must not call `sessions.resolve`: that opens a browser
    when the record has none, the leak the status resource refuses to be."""
    opened_before = flow_server.actions.grid
    async with Client(flow_server.mcp) as c:
        files = (await c.call_tool("show", {"uri": "session://files"})).structured_content
        folder = (await c.call_tool(
            "show", {"uri": "session://files/downloads"}
        )).structured_content
    assert files["data"]["files"] == []
    assert folder["component"] == "folder"
    assert flow_server.actions.grid is opened_before


async def test_show_is_listed_only_for_a_client_that_renders_apps(built_ui, server):
    names = {t.name for t in await server.mcp.list_tools()}
    assert "show" not in names
    with patch.object(apps, "supported", return_value=True):
        tools = {t.name: t for t in await server.mcp.list_tools()}
    assert "show" in tools
    assert "session_files" not in tools


async def test_the_app_may_call_show_itself(built_ui, server):
    """Asserted on what a client receives in the tool's `_meta`."""
    with patch.object(apps, "supported", return_value=True):
        async with Client(server.mcp) as c:
            tool = next(t for t in await c.list_tools() if t.name == "show")
    ui = (tool.meta or {}).get("ui") or {}
    assert ui.get("visibility") == ["model", "app"]
    assert ui.get("resourceUri") == apps.RESOURCE_URI


async def test_the_app_may_frame_its_own_files(built_ui):
    """The Lightbox previews a PDF in an <iframe>: without frameDomains the
    host's CSP refuses it."""
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN},
        public_base_url="https://flow.example.com/base",
    ))
    with patch.object(apps, "supported", return_value=True):
        async with Client(server.mcp) as c:
            tool = next(t for t in await c.list_tools() if t.name == "show")
    csp = ((tool.meta or {}).get("ui") or {}).get("csp") or {}
    assert csp.get("frameDomains") == ["https://flow.example.com"]


async def test_the_session_status_shows_through_the_client(flow_server):
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool("show", {"uri": "session://current"})).structured_content
        read = json.loads((await c.read_resource("session://current"))[0].text)
    assert shown["component"] == "context"
    assert shown["uri"] == "session://current"
    # Only the stable keys: liveness fields may differ between two probes.
    for key in ("session", "named_by", "browser", "store", "principal"):
        assert shown["data"][key] == read[key]


@pytest.mark.parametrize("content", ["not json", b"\x89PNG"])
async def test_content_that_is_not_json_is_refused_cleanly(flow_server, content):
    class Item:
        pass

    item = Item()
    item.content = content
    result = type("R", (), {"contents": [item]})()

    async def read(uri):
        return result

    async with Client(flow_server.mcp) as c:
        with (
            patch.object(flow_server.mcp, "read_resource", read),
            pytest.raises(ToolError) as refused,
        ):
            await c.call_tool("show", {"uri": "session://current"})
    assert "not a resource show can draw" in str(refused.value)
