"""Reading: resources for the clients that read them, two tools for the rest.

Everything to read is a resource, and the text an agent reads names it by URI.
A client whose model cannot read resources — VS Code Copilot is the one
measured — gets `list_resources` and `read_resource`, which take the same URIs
(saga §F3.1, §F3.6). These go through the real MCP client wherever the property
is about what a client is shown or reads.
"""

import base64
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import Implementation

from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.mcp import mirror
from kubed.selenium_flow.mcp.resources import RESOURCE_URI
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import NAMED, TOKEN, http

pytestmark = pytest.mark.unit


@pytest.fixture
def reader(tmp_path):
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, flow_data_dir=str(tmp_path)
    )
    server.sessions.name = lambda: NAMED
    server.sessions.library = lambda: NAMED
    return server


async def listed(server, name="a spec-complete client"):
    async with Client(server.mcp, client_info=Implementation(name=name, version="1")) as c:
        return {tool.name for tool in await c.list_tools()}


async def read(server, uri):
    async with Client(server.mcp) as c:
        return await c.call_tool(mirror.READ_TOOL, {"uri": uri})


# ---- who reads resources -----------------------------------------------------


def test_resources_are_assumed_supported(monkeypatch):
    """A spec-complete client is the default assumption."""
    monkeypatch.setattr(clients_module, "_http", lambda: http())
    assert clients_module.reads_resources() is True


@pytest.mark.parametrize("value", ["off", "false", "0", "no", "none", "OFF"])
def test_a_client_can_declare_it_cannot_read_resources(monkeypatch, value):
    monkeypatch.setattr(clients_module, "_http", lambda: http({"resources": value}))
    assert clients_module.reads_resources() is False


def test_the_header_wins_here_too(monkeypatch):
    """Same precedence rule as session names, for the same reason."""
    monkeypatch.setattr(
        clients_module, "_http", lambda: http({"resources": "on"}, {"x-mcp-resources": "off"})
    )
    assert clients_module.reads_resources() is False


def test_stdio_clients_are_assumed_to_read_resources(monkeypatch):
    monkeypatch.setattr(clients_module, "_http", lambda: None)
    assert clients_module.reads_resources() is True


async def test_vs_code_is_given_the_reading_tools_without_asking(reader):
    """Its model cannot list or read a resource, so the default is decided by
    who is calling (§F3.1)."""
    assert await listed(reader, "Visual Studio Code") >= mirror.MIRROR_TOOLS
    assert await listed(reader, "Visual Studio Code - Insiders") >= mirror.MIRROR_TOOLS


async def test_a_client_that_reads_resources_is_not_shown_them(reader):
    assert not mirror.MIRROR_TOOLS & await listed(reader)


async def test_saying_so_beats_being_recognised(reader, monkeypatch):
    """VS Code with resources=on is taken at its word — the day it can read
    them, nobody should have to wait for a release to say so."""
    monkeypatch.setattr(clients_module, "_http", lambda: http({"resources": "on"}))
    assert not mirror.MIRROR_TOOLS & await listed(reader, "Visual Studio Code")


async def test_the_old_per_resource_tools_are_gone(reader, monkeypatch):
    """Seven tools, one per resource, became two that take a URI (§F3.6)."""
    monkeypatch.setattr(clients_module, "_http", lambda: http({"resources": "off"}))
    names = await listed(reader)
    for gone in (
        "current_session", "selenium_flow_skill", "list_flows", "get_flow",
        "flow_schema", "list_secrets",
    ):
        assert gone not in names
        assert await reader.mcp.get_tool(gone) is None, gone


# ---- what the tools read -----------------------------------------------------


async def test_the_listing_is_every_uri_to_read_and_nothing_to_draw(reader):
    async with Client(reader.mcp) as c:
        rows = (await c.call_tool(mirror.LIST_TOOL, {})).structured_content["result"]
    uris = {row.get("uri") or row.get("uri_template") for row in rows}
    assert RESOURCE_URI in uris
    assert "skill://selenium-flow/SKILL.md" in uris
    assert "flow://flows/{name}" in uris
    assert not any(u.startswith("ui://") for u in uris), "the app shell is not reading"


async def test_a_resource_reads_the_same_through_the_tool(reader):
    reader.flows.save(NAMED, "login", {"steps": [{"tool": "navigate", "args": {"url": "u"}}]})
    got = json.loads((await read(reader, "flow://flows/login")).content[0].text)
    assert got["name"] == "login" and got["steps"]
    skill = (await read(reader, "skill://selenium-flow/SKILL.md")).content[0].text
    assert "name: selenium-flow" in skill


async def test_a_uri_that_is_not_there_is_told_the_templates(reader):
    with pytest.raises(ToolError) as refused:
        await read(reader, "flows://login")
    assert "flow://flows/{name}" in str(refused.value)


async def test_the_app_shell_is_refused_rather_than_read(reader):
    with pytest.raises(ToolError, match="not to read"):
        await read(reader, "ui://selenium-flow/component")


def _serving(reader, data: bytes):
    reader.sessions.browser = lambda name: "abc"
    reader.actions.grid.read_file = lambda session, name: data


async def test_an_image_comes_back_as_an_image(reader):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    _serving(reader, png)
    result = await read(reader, "session://files/shot.png")
    assert result.content[0].type == "image"
    assert result.content[0].mimeType == "image/png"


async def test_any_other_binary_is_described_not_dumped(reader):
    """Base64 of a downloaded PDF is megabytes of text a model can do nothing
    with, spent out of its own context."""
    pdf = b"%PDF-1.4" + b"x" * 50_000
    _serving(reader, pdf)
    result = await read(reader, "session://files/report.pdf")
    said = json.loads(result.content[0].text)
    assert said["binary"] is True and said["bytes"] == len(pdf)
    assert said["mime_type"] == "application/pdf"
    assert "session://files" in said["read"]
    assert len(result.content[0].text) < 1_000


async def test_both_tools_only_read(reader, monkeypatch):
    monkeypatch.setattr(clients_module, "_http", lambda: http({"resources": "off"}))
    async with Client(reader.mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
    for name in mirror.MIRROR_TOOLS:
        assert tools[name].annotations.read_only_hint is True, name


async def test_the_status_resource_is_always_offered(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert RESOURCE_URI in uris


# ---- session_id is advertised per mode --------------------------------------


def named(monkeypatch):
    monkeypatch.setattr(
        "kubed.selenium_flow.session.sessions.http_request", lambda: http({"session": "d"})
    )


def unnamed(monkeypatch):
    monkeypatch.setattr("kubed.selenium_flow.session.sessions.http_request", lambda: http())


async def test_no_tool_advertises_a_session_id(server, monkeypatch):
    """A model cannot pass what it cannot see, which is the point."""
    named(monkeypatch)
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("navigate", "extract", "interact", "end_browser"):
        assert "session_id" not in tools[name].parameters["properties"], name


async def test_open_session_is_the_same_named_or_not(server, monkeypatch):
    """It has no session_id to shape, whoever calls it."""
    named(monkeypatch)
    a = {t.name: t for t in await server.mcp.list_tools()}["open_session"].parameters
    unnamed(monkeypatch)
    b = {t.name: t for t in await server.mcp.list_tools()}["open_session"].parameters
    assert a == b
    assert "session_id" not in a["properties"]




# ---- for a person picking one --------------------------------------------------


async def test_a_person_picking_a_flow_is_offered_the_names_they_can_read(reader):
    """VS Code asks for completions when a template or a prompt is picked
    (§F3.4). They are the caller's listing read back, so they cannot offer a
    flow the caller could not open."""
    from mcp.types import PromptReference, ResourceTemplateReference

    for name in ("login", "logout", "checkout"):
        reader.flows.save(NAMED, name, {"steps": [{"tool": "navigate", "args": {"url": "u"}}]})
    reader.flows.save("someone-else", "log-secret", {"steps": [{"tool": "navigate", "args": {"url": "u"}}]})
    async with Client(reader.mcp) as c:
        template = await c.complete(
            ResourceTemplateReference(type="ref/resource", uri="flow://flows/{name}"),
            {"name": "name", "value": "log"},
        )
        prompt = await c.complete(
            PromptReference(type="ref/prompt", name="repair_flow"),
            {"name": "flow", "value": ""},
        )
    assert sorted(template.values) == ["login", "logout"]
    assert sorted(prompt.values) == ["checkout", "login", "logout"]


async def test_resources_are_named_for_a_person_to_read(reader):
    names = {str(r.uri): r.name for r in await reader.mcp.list_resources()}
    assert names["flow://flows"] == "Saved Flows"
    assert names[RESOURCE_URI] == "Current Session"
    assert not any(n.endswith("_resource") for n in names.values())


async def test_a_person_picking_a_file_is_offered_their_own_files(reader):
    from mcp.types import ResourceTemplateReference

    reader.flows.write_file(NAMED, "report.pdf", b"%PDF")
    reader.flows.write_file(NAMED, "shot.png", b"png")
    reader.flows.write_file("someone-else", "report-secret.pdf", b"%PDF")
    async with Client(reader.mcp) as c:
        offered = await c.complete(
            ResourceTemplateReference(type="ref/resource", uri="session://files/{name}"),
            {"name": "name", "value": "rep"},
        )
    assert offered.values == ["report.pdf"]


async def test_an_svg_reads_back_as_the_text_it_is(reader):
    """An image to a browser, XML to a model — which cannot view it as a picture
    (Copilot, #39)."""
    _serving(reader, b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>")
    result = await read(reader, "session://files/chart.svg")
    assert result.content[0].type == "text"
    assert result.content[0].text.startswith("<svg")


async def test_a_flow_that_is_not_there_says_where_the_flows_are(reader):
    """A URI that matches a template reaches the resource, whose own refusal
    names the listing — better advice than the templates the caller already
    used."""
    with pytest.raises(ToolError) as refused:
        await read(reader, "flow://flows/nope")
    assert "no flow called 'nope'" in str(refused.value)
    assert "flow://flows" in str(refused.value)


async def test_the_listing_publishes_both_row_shapes(reader):
    """A tool-only client learns from the output schema which rows have a uri
    and which a uri_template (Copilot, #39)."""
    tool = await reader.mcp.get_tool(mirror.LIST_TOOL)
    schema = json.dumps(tool.output_schema)
    assert "uri_template" in schema and "mime_type" in schema
