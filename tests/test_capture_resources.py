"""`workspace://console` and `workspace://network`: five forms, one rule - they
read what capture kept, and never open or ask a browser (spec §7, plan ruling 12)."""

import json

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError

from kubed.selenium_flow.capture import OFF
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace.store import Workspace

from .capture_events import done, log, nav_started, req
from .conftest import NAMED, TOKEN, calling_as

pytestmark = pytest.mark.unit


class SilentGrid:
    """A Grid that records any question put to it."""

    def __init__(self):
        self.calls = []

    def is_alive(self, session_id):  # what `workspace://current` peeks with
        return True

    def __getattr__(self, name):
        def ask(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"the Grid was asked: {name}")

        return ask


@pytest.fixture
def server(tmp_path, monkeypatch):
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path)},
    ))
    server.workspaces.actions.grid = SilentGrid()
    calling_as(monkeypatch, NAMED)
    return server


def capturing(server):
    """The named workspace holds a captured browser that has seen a failure."""
    server.workspaces.store.set(NAMED, Workspace(session_id="s1", settings={"capture": True}))
    capture = server.capture
    capture.attached(NAMED, "s1", "chrome", "top-1", again=False)
    for method, params in (
        nav_started("https://a.test/"),
        log("boom", level="error"),
        req("r1", "https://a.test/api", initiator="fetch"),
        done("r1", 500),
    ):
        capture.ingest(NAMED, "s1", method, params)


async def read(server, uri):
    async with Client(server.mcp) as c:
        return json.loads((await c.read_resource(uri))[0].text)


async def test_the_five_forms_are_served(server):
    async with Client(server.mcp) as c:
        listed = {str(r.uri) for r in await c.list_resources()}
        templates = {t.uri_template for t in await c.list_resource_templates()}
    assert {"workspace://console", "workspace://network"} <= listed
    assert {
        "workspace://console{?level,pages}",
        "workspace://network{?filter,pages}",
        "workspace://network/{id}",
    } <= templates


async def test_each_answers_off_when_capture_is_not_on(server):
    for uri in ("workspace://console", "workspace://console?level=error",
                "workspace://network", "workspace://network?filter=failed"):
        assert await read(server, uri) == OFF, uri
    assert await read(server, "workspace://network/1.1") == OFF


async def test_the_console_passes_level_and_pages_through(server):
    capturing(server)
    assert [e["text"] for e in (await read(server, "workspace://console"))["entries"]] == ["boom"]
    narrowed = await read(server, "workspace://console?level=error&pages=2")
    assert [e["text"] for e in narrowed["entries"]] == ["boom"]
    assert (await read(server, "workspace://console?level=warn"))["entries"] == []


async def test_pages_out_of_range_is_refused_naming_the_range(server):
    capturing(server)
    with pytest.raises(MCPError, match="1 to 5"):
        await read(server, "workspace://console?pages=9")
    with pytest.raises(MCPError, match="1 to 5"):
        await read(server, "workspace://network?pages=9")


BAD_READS = [
    ("workspace://console?pages=9", "1 to 5"),
    ("workspace://console?pages=x", "1 to 5"),
    ("workspace://console?level=bogus", "level is one of"),
    ("workspace://network?pages=0", "1 to 5"),
    ("workspace://network?filter=bogus", "filter is one of"),
    ("workspace://network/9.9", "workspace://network lists them"),
]


@pytest.mark.parametrize(("uri", "says"), BAD_READS)
async def test_a_bad_argument_is_invalid_params_not_an_internal_error(server, uri, says):
    """The caller's mistake, like every other bad argument: -32602, never
    -32603, which tells a client the server broke (spotless 3)."""
    from mcp_types import INVALID_PARAMS

    capturing(server)
    with pytest.raises(MCPError, match=says) as refused:
        await read(server, uri)
    assert refused.value.error.code == INVALID_PARAMS


@pytest.mark.parametrize(("uri", "says"), BAD_READS)
async def test_a_bad_argument_through_read_resource_says_the_same(server, uri, says):
    from fastmcp.exceptions import ToolError

    capturing(server)
    async with Client(server.mcp) as c:
        with pytest.raises(ToolError, match=says) as refused:
            await c.call_tool("read_resource", {"uri": uri})
    assert "refused its arguments" not in str(refused.value)


async def test_the_network_lists_a_request_and_one_opens(server):
    capturing(server)
    listing = await read(server, "workspace://network?filter=failed")
    row = listing["requests"][0]
    assert row["id"] == "1.1" and row["status"] == 500
    one = await read(server, "workspace://network/1.1")
    assert one["id"] == "1.1"


async def test_an_unknown_request_names_the_listing(server):
    capturing(server)
    with pytest.raises(MCPError, match="workspace://network"):
        await read(server, "workspace://network/9.9")


async def test_reading_asks_the_grid_nothing(server):
    capturing(server)
    for uri in ("workspace://console", "workspace://network",
                "workspace://network/1.1"):
        await read(server, uri)
    assert server.workspaces.actions.grid.calls == []


async def test_current_carries_the_capture_fields(server):
    capturing(server)
    current = await read(server, "workspace://current")
    assert current["capture"] is True
    assert "capturing" in current and "captured" in current
