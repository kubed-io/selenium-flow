"""show(uri): one MCP App tool that draws a resource by its URI."""

import json
import re
from unittest.mock import patch

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import apps, show
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace.store import Workspace

from .conftest import TOKEN

pytestmark = pytest.mark.unit

GOOD = [{"tool": "navigate", "args": {"url": "https://example.test/"}}]


@pytest.fixture
def flow_server(tmp_path, named_caller):
    """A server with a flow library and a caller who has named their workspace."""
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path)},
    ))
    server.flows.save(named_caller, "login", {"steps": GOOD, "description": "Log in"})
    return server


@pytest.fixture
def secrets_server(tmp_path, named_caller):
    """A server with one secret whose values are recognisable."""
    demo = tmp_path / "secrets" / "demo"
    demo.mkdir(parents=True)
    (demo / "username").write_text("user-7f3a")
    (demo / "password").write_text("pass-9c1e")
    return SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        secrets={"dirs": str(tmp_path / "secrets")},
    ))


@pytest.mark.parametrize(
    ("uri", "component"),
    [
        ("workspace://current", "context"),
        ("workspace://site-data", "sites"),
        ("workspace://site-data/app.example.com", "site"),
        ("workspace://files", "files"),
        ("workspace://files/screenshots", "folder"),
        ("workspace://files/recordings", "folder"),
        ("workspace://files/downloads", "folder"),
        ("workspace://files/screenshots/a.png", "file"),
        ("workspace://files/recordings/run.mp4", "file"),
        ("workspace://files/downloads/report.csv", "file"),
        ("workspace://files/report.pdf", "file"),
        ("flow://flows", "flows"),
        ("flow://flows/login", "flow"),
        ("flow://schema", "document"),
        ("secret://secrets", "secrets"),
        ("skill://selenium-flow/SKILL.md", "document"),
        ("skill://selenium-flow/_manifest", "document"),
        ("skill://selenium-flow/references/FLOWS.md", "document"),
    ],
)
def test_every_showable_uri_has_one_view(uri, component):
    assert show.view_for(uri) == component


@pytest.mark.parametrize(
    ("uri", "entry"),
    [
        ("workspace://files/screenshots/a.png", True),
        ("workspace://files/recordings/run.mp4", True),
        ("workspace://files/downloads/report.csv", True),
        ("workspace://files/report.pdf", True),
        ("workspace://files/screenshots", False),
        ("workspace://site-data/app.example.com", False),
        ("skill://selenium-flow/SKILL.md", False),
    ],
)
def test_only_a_single_file_is_drawn_from_its_entry(uri, entry):
    assert show.row_for(uri).entry is entry


def test_every_row_matches_its_own_display_form():
    """One table: each row's display form, made concrete, is drawn by that row's
    own component, not an earlier row's."""
    for view in show.VIEWS:
        concrete = re.sub(r"\{[^}]+\}", "x", view.form)
        assert show.row_for(concrete) is view


@pytest.mark.parametrize(
    "uri",
    ["flow://flows/", "workspace://current/x", "secret://secrets/demo",
     "skill://other/SKILL.md", "workspace://files/screenshots/a/b",
     "workspace://site-data/a/b"],
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
        ("workspace://files", "files"),
        ("workspace://files/downloads", "folder"),
        ("workspace://files/screenshots", "folder"),
        ("workspace://files/recordings", "folder"),
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
        shown = (await c.call_tool("show", {"uri": "workspace://files"})).structured_content
        read = json.loads((await c.read_resource("workspace://files"))[0].text)

    def unsigned(data):
        # The signed url carries an expiry that can cross a second between calls.
        return {**data, "files": [{k: v for k, v in f.items() if k != "url"} for f in data["files"]]}

    assert [f["name"] for f in shown["data"]["files"]] == ["report.pdf"]
    assert unsigned(shown["data"]) == unsigned(read)


async def test_a_result_claude_would_drop_is_refused(flow_server, monkeypatch):
    """Claude never hands an app a result over ~150k characters, so the view
    would wait forever; a flow may be 1 MiB of YAML."""
    async with Client(flow_server.mcp) as c:
        assert (await c.call_tool("show", {"uri": "flow://flows/login"})).structured_content
        monkeypatch.setattr(show, "MAX_SHOWN", 50)
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "flow://flows/login"})
    message = str(refused.value)
    assert "flow://flows/login is too large to draw here (" in message
    assert "read it with the resource instead" in message


@pytest.mark.parametrize(
    ("uri", "summary"),
    [
        ("flow://flows/login", "Showing flow://flows/login to the person (flow)."),
        ("workspace://current", "Showing workspace://current to the person (context)."),
        ("flow://flows", "Showing flow://flows to the person: 1 flow."),
        ("workspace://files", "Showing workspace://files to the person: 0 kept files."),
        ("workspace://files/screenshots", "Showing workspace://files/screenshots to the person: 0 files."),
    ],
)
async def test_the_payload_goes_once_and_the_model_gets_one_line(flow_server, uri, summary):
    """A plain dict would also go out as a JSON text block, doubling it. The
    model reads resources to think; the text is a line saying what was shown."""
    async with Client(flow_server.mcp) as c:
        result = await c.call_tool("show", {"uri": uri})
        read = json.loads((await c.read_resource(uri))[0].text)
    assert [block.text for block in result.content] == [summary]
    assert result.structured_content["data"].keys() == read.keys()
    assert result.structured_content["uri"] == uri


async def test_the_limit_counts_the_whole_result_as_sent(flow_server, monkeypatch):
    """Structured payload and summary line together: what Claude would drop."""
    async with Client(flow_server.mcp) as c:
        result = await c.call_tool("show", {"uri": "flow://flows/login"})
        sent = len(json.dumps({
            "content": [{"type": "text", "text": result.content[0].text}],
            "structuredContent": result.structured_content,
        }))
        assert sent > len(json.dumps(result.structured_content)) + len(result.content[0].text)
        monkeypatch.setattr(show, "MAX_SHOWN", sent)
        await c.call_tool("show", {"uri": "flow://flows/login"})
        monkeypatch.setattr(show, "MAX_SHOWN", sent - 1)
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "flow://flows/login"})
    assert f"({sent} characters)" in str(refused.value)


async def test_a_missing_flow_is_refused(flow_server):
    async with Client(flow_server.mcp) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "flow://flows/nope"})
    assert "flow://flows/nope" in str(refused.value)


async def test_an_unshowable_uri_is_refused_through_the_client(flow_server):
    async with Client(flow_server.mcp) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "secret://secrets/demo"})
    assert "flow://flows/{name}" in str(refused.value)


async def test_showing_the_files_never_opens_a_browser(flow_server):
    """Reading a listing must not call `Workspaces.resolve`: that opens a browser
    when the record has none, the leak the status resource refuses to be."""
    opened_before = flow_server.actions.grid
    async with Client(flow_server.mcp) as c:
        files = (await c.call_tool("show", {"uri": "workspace://files"})).structured_content
        folder = (await c.call_tool(
            "show", {"uri": "workspace://files/downloads"}
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
        shown = (await c.call_tool("show", {"uri": "workspace://current"})).structured_content
        read = json.loads((await c.read_resource("workspace://current"))[0].text)
    assert shown["component"] == "context"
    assert shown["uri"] == "workspace://current"
    # Only the stable keys: liveness fields may differ between two probes.
    for key in ("workspace", "named_by", "browser", "store", "principal"):
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
            await c.call_tool("show", {"uri": "workspace://current"})
    assert "not a resource show can draw" in str(refused.value)


async def test_the_secrets_show_as_the_resource_lists_them(secrets_server):
    async with Client(secrets_server.mcp) as c:
        result = await c.call_tool("show", {"uri": "secret://secrets"})
        read = json.loads((await c.read_resource("secret://secrets"))[0].text)
    assert result.structured_content == {
        "component": "secrets", "uri": "secret://secrets", "data": read,
    }
    assert [s["name"] for s in read["secrets"]] == ["demo"]
    assert result.content[0].text == "Showing secret://secrets to the person: 1 secret."


async def test_showing_the_secrets_never_sends_a_value(secrets_server):
    async with Client(secrets_server.mcp) as c:
        result = await c.call_tool("show", {"uri": "secret://secrets"})
    sent = json.dumps(result.structured_content) + "".join(
        block.text for block in result.content
    )
    assert "demo" in sent
    assert "user-7f3a" not in sent and "pass-9c1e" not in sent


async def test_showing_secrets_on_a_server_without_them_says_why(flow_server):
    async with Client(flow_server.mcp) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "secret://secrets"})
    assert "secrets are not enabled" in str(refused.value)


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def never(*args, **kwargs):
    raise AssertionError("show read bytes, or opened a browser, to draw an entry")


@pytest.mark.parametrize(
    ("folder", "name", "uri"),
    [
        ("screenshots", "shot 1.png", "workspace://files/screenshots/shot%201.png"),
        ("recordings", "run.mp4", "workspace://files/recordings/run.mp4"),
        (None, "report.pdf", "workspace://files/report.pdf"),
    ],
)
async def test_one_file_is_drawn_from_its_listing_entry(
    flow_server, named_caller, monkeypatch, folder, name, uri
):
    if folder:
        flow_server.flows.write_file(named_caller, name, PNG, folder)
    else:
        flow_server.flows.write_file(named_caller, name, PNG)
    listing = uri.rpartition("/")[0]
    monkeypatch.setattr(flow_server.flows, "read_file", never)
    monkeypatch.setattr(flow_server.workspaces, "resolve", never)
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool("show", {"uri": uri})).structured_content
        listed = json.loads((await c.read_resource(listing))[0].text)
    entry = next(f for f in listed["files"] if f["name"] == name)

    def unsigned(e):
        return {k: v for k, v in e.items() if k != "url"}

    assert shown["component"] == "file"
    assert shown["uri"] == uri
    assert unsigned(shown["data"]) == unsigned(entry)
    assert shown["data"]["url"]


async def test_a_download_is_drawn_from_the_grids_listing(flow_server, monkeypatch):
    monkeypatch.setattr(flow_server.workspaces, "browser", lambda name: "abc")
    monkeypatch.setattr(flow_server.workspaces, "resolve", never)
    monkeypatch.setattr(
        flow_server.actions.grid,
        "files",
        lambda session_id: [{"name": "report.csv", "size": 3, "creationTime": 1}],
    )
    monkeypatch.setattr(flow_server.actions.grid, "read_file", never)
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool(
            "show", {"uri": "workspace://files/downloads/report.csv"}
        )).structured_content
    assert shown["component"] == "file"
    assert shown["data"]["name"] == "report.csv"
    assert shown["data"]["keep_with"].startswith("keep_file(")


async def test_a_file_the_listing_lacks_is_refused_naming_the_listing(flow_server):
    async with Client(flow_server.mcp) as c:
        with pytest.raises(ToolError) as refused:
            await c.call_tool("show", {"uri": "workspace://files/screenshots/gone.png"})
    assert (
        "no file at workspace://files/screenshots/gone.png. "
        "workspace://files/screenshots lists what there is"
    ) in str(refused.value)


async def test_a_skill_page_is_drawn_from_its_text(flow_server):
    uri = "skill://selenium-flow/references/TROUBLESHOOTING.md"
    async with Client(flow_server.mcp) as c:
        result = await c.call_tool("show", {"uri": uri})
        read = (await c.read_resource(uri))[0].text
    assert result.structured_content == {"component": "document", "uri": uri, "data": read}
    assert read.startswith("# When something goes wrong")
    assert [b.text for b in result.content] == [
        f"Showing {uri} to the person (document)."
    ]


@pytest.mark.parametrize("uri", ["flow://schema", "skill://selenium-flow/_manifest"])
async def test_a_json_document_is_drawn_from_its_json(flow_server, uri):
    async with Client(flow_server.mcp) as c:
        shown = (await c.call_tool("show", {"uri": uri})).structured_content
        read = json.loads((await c.read_resource(uri))[0].text)
    assert shown == {"component": "document", "uri": uri, "data": read}


async def test_the_site_data_pair_draws_with_httponly_masked(flow_server, named_caller):
    site = "app.example.com"
    flow_server.workspaces.store.set(named_caller, Workspace(site_data={
        "cookies": [
            {"name": "sid", "value": "s3cret", "domain": site, "http_only": True},
            {"name": "theme", "value": "dark", "domain": site},
        ],
        "origins": {f"https://{site}": {"local": {"k": "v"}}},
        "session": {"origin": f"https://{site}", "items": {}},
        "saved_at": 1000.0,
    }).visited(f"https://{site}/x"))
    async with Client(flow_server.mcp) as c:
        listing = (await c.call_tool("show", {"uri": "workspace://site-data"})).structured_content
        one = (await c.call_tool(
            "show", {"uri": f"workspace://site-data/{site}"}
        )).structured_content
    assert listing["component"] == "sites"
    assert [r["uri"] for r in listing["data"]["sites"]] == [f"workspace://site-data/{site}"]
    assert one["component"] == "site"
    sent = json.dumps(one, ensure_ascii=False)
    assert "\u2022\u2022\u2022" in sent
    assert "s3cret" not in sent


async def test_the_description_names_the_schemes(built_ui, server):
    with patch.object(apps, "supported", return_value=True):
        tool = next(t for t in await server.mcp.list_tools() if t.name == "show")
    for scheme in ("workspace://", "flow://", "secret://", "skill://"):
        assert scheme in tool.description
    assert "read the resource instead" in tool.description
