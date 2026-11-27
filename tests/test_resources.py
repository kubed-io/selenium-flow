"""Reading: resources for the clients that read them, two tools for the rest.

Everything to read is a resource, and the text an agent reads names it by URI.
A client whose model cannot read resources — VS Code Copilot is the one
measured — gets `list_resources` and `read_resource`, which take the same URIs.
These go through the real MCP client wherever the property
is about what a client is shown or reads.
"""

import base64
import json
import logging

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.types import Implementation, ResourceTemplateReference

from kubed.selenium_flow.core.guidance import ENTRY
from kubed.selenium_flow.mcp import instructions
from kubed.selenium_flow.mcp.resources import RESOURCE_URI, where_linked

from .conftest import NAMED, RESOURCE_TOOLS, calling_as, delivery_meta, http, over_http
from .fakes import request

pytestmark = pytest.mark.unit


@pytest.fixture
def reader(data_server, monkeypatch):
    calling_as(monkeypatch, NAMED)
    return data_server


async def read(server, uri):
    async with Client(server.mcp.server) as c:
        return await c.call_tool("read_resource", {"uri": uri})


# ---- who reads resources -----------------------------------------------------


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_handshake_reports_the_default_delivery(reader, mode):
    async with over_http(reader, mode=mode) as c:
        assert await delivery_meta(c) == {
            "resources": "on", "listed": ["skills"], "via": "mcp", "prefix": None,
        }


async def listed(client):
    return {t.name for t in await client.list_tools()}


VS_CODE = ["Visual Studio Code", "Visual Studio Code - Insiders"]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("name", VS_CODE)
async def test_vs_code_with_no_header_gets_todays_delivery(reader, name, mode):  # Review Focus 3
    async with over_http(reader, mode=mode, client_info=Implementation(name=name, version="1")) as c:
        assert await listed(c) >= RESOURCE_TOOLS
        assert c.instructions == instructions.instructions(False)


@pytest.mark.parametrize("info", [Implementation(name="a spec-complete client", version="1"), None])
async def test_any_other_client_with_no_header_is_on(reader, info):
    async with over_http(reader, client_info=info) as c:
        assert not RESOURCE_TOOLS & await listed(c)
        assert c.instructions == instructions.instructions(True)


@pytest.mark.parametrize("name", VS_CODE)
async def test_vs_code_saying_on_is_taken_at_its_word(reader, name):
    """The request's value beats the table (DLV-3)."""
    info = Implementation(name=name, version="1")
    async with over_http(reader, {"X-MCP-Resources": "on"}, client_info=info) as c:
        assert not RESOURCE_TOOLS & await listed(c)
        assert c.instructions == instructions.instructions(True)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize(
    "given",
    [
        {"headers": {"X-MCP-Resources": "off"}},
        {"headers": {"Sf-Resources": "off"}},
        {"params": {"resources": "off"}},
    ],
)
async def test_off_by_header_or_query_lists_the_tools_and_points_at_them(reader, mode, given):
    async with over_http(reader, mode=mode, **given) as c:
        assert await listed(c) >= RESOURCE_TOOLS
        assert c.instructions == instructions.instructions(False)


async def test_the_header_beats_the_query(reader):
    async with over_http(reader, {"X-MCP-Resources": "on"}, {"resources": "off"}) as c:
        assert not RESOURCE_TOOLS & await listed(c)
    async with over_http(reader, {"X-MCP-Resources": "off"}, {"resources": "on"}) as c:
        assert await listed(c) >= RESOURCE_TOOLS


async def test_an_unrecognised_value_is_ignored_with_one_warning(reader, caplog):  # named difference 1
    caplog.set_level(logging.WARNING, logger="kubed.mcp.delivery")
    # Legacy: one session id for the connection. On 2026-07-28 each request is its own (DLV-5).
    async with over_http(
        reader, {"X-MCP-Resources": "bogus-190"}, {"resources": "off"}, mode="legacy"
    ) as c:
        assert await listed(c) >= RESOURCE_TOOLS
        await listed(c)
    said = [r for r in caplog.records if "bogus-190" in r.getMessage()]
    assert len(said) == 1 and "header X-MCP-Resources" in said[0].getMessage()


@pytest.mark.parametrize("given", [
    {}, {"headers": {"X-MCP-Resources": "off"}}, {"headers": {"X-MCP-Resources": "on"}},
    {"headers": {"Sf-Resources": "off"}},
    {"headers": {"Sf-Via": "local", "Sf-Resources": "off"}},
    {"headers": {"X-MCP-Resources": "bogus-190"}, "params": {"resources": "off"}},
    {"params": {"resources": "none"}},
    {"client_info": Implementation(name="Visual Studio Code", version="1")},
])
async def test_the_instructions_agree_with_the_listing(reader, given):  # Review Focus 4
    async with over_http(reader, **given) as c:
        tools_shown = "read_resource" in await listed(c)
        assert ("read_resource(uri)" in c.instructions) is tools_shown


PATTERN = "skill://selenium-flow/<name>/SKILL.md"
EXAMPLE = "skill://selenium-flow/building-flows/references/FLOWS.md"


def test_the_mcp_pointer_gives_every_skill_its_address():  # PLG-58, PROT-29
    for reads in (True, False):
        text = instructions.instructions(reads)
        assert PATTERN in text and "references/FLOWS.md in building-flows is " + EXAMPLE in text
    for via in ("extension", "local"):
        assert PATTERN not in instructions.instructions(True, via=via)


async def test_the_instructions_carry_the_gateway_prefix(reader):  # named difference 5
    async with over_http(reader, {"X-MCP-Resource-Prefix": "sf+"}) as c:
        assert "sf+skill://selenium-flow/driving-a-browser/SKILL.md" in c.instructions
        assert "sf+" + PATTERN in c.instructions and "sf+" + EXAMPLE in c.instructions
    async with over_http(reader, {"Sf-Resource-Prefix": "sf+"}) as c:
        assert "sf+skill://selenium-flow/driving-a-browser/SKILL.md" in c.instructions
        assert "sf+" + PATTERN in c.instructions and "sf+" + EXAMPLE in c.instructions
    both = {"Sf-Resource-Prefix": "sf+", "X-MCP-Resource-Prefix": "gw+"}
    async with over_http(reader, both) as c:
        assert "sf+skill://" in c.instructions and "gw+" not in c.instructions
    async with over_http(reader) as c:
        assert c.instructions == instructions.instructions(True)


PLUGIN_PROMPTS = {"build_flow", "repair_flow"}


async def names(client, method, key):
    return {row["name"] for row in (await request(client, method))[key]}


def test_the_skill_line_follows_via():
    for reads in (True, False):
        assert instructions.instructions(reads, via="mcp") == instructions.instructions(reads)
        assert instructions.instructions(reads, via="bogus") == instructions.instructions(reads)
    held = instructions.instructions(True, via="extension")
    assert "the driving-a-browser skill, which your client already has from this server" in held
    installed = instructions.instructions(True, via="local")
    assert "the selenium-flow:driving-a-browser skill, installed with this server's plugin" in installed
    assert "this version" not in installed
    for via in ("extension", "local"):
        assert "skill://" not in instructions.instructions(True, via=via)
        off = instructions.instructions(False, via=via)
        assert instructions.READING_POINTER in off and "Read it with read_resource" not in off


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_sf_name_beats_the_x_mcp_name(reader, mode):
    async with over_http(reader, {"Sf-Resources": "on", "X-MCP-Resources": "off"}, mode=mode) as c:
        assert not RESOURCE_TOOLS & await listed(c)
        assert c.instructions == instructions.instructions(True)
    async with over_http(reader, {"Sf-Resources": "off", "X-MCP-Resources": "on"}, mode=mode) as c:
        assert await listed(c) >= RESOURCE_TOOLS


@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("via", ["extension", "local"])
@pytest.mark.parametrize("sent", ["headers", "params"])
async def test_via_fits_the_instructions_and_the_listings(reader, mode, via, sent):
    given = {"headers": {"Sf-Via": via}} if sent == "headers" else {"params": {"via": via}}
    async with over_http(reader, mode=mode, **given) as c:
        assert c.instructions == instructions.instructions(True, via=via)
        assert "skill://" not in c.instructions
        assert PLUGIN_PROMPTS.isdisjoint(await names(c, "prompts/list", "prompts"))
        skills = (await request(c, "skills/list"))["skills"]
        agents = await names(c, "agents/list", "agents")
        if via == "local":
            assert skills == [] and agents == set()
        else:
            assert len(skills) == 5 and agents == {"explorer"}
        assert await delivery_meta(c) == {
            "resources": "on", "listed": [], "via": via, "prefix": None,
        }


@pytest.mark.parametrize("via", ["extension", "local"])
async def test_what_via_hides_still_answers(reader, via):  # DLV-10
    async with over_http(reader, {"Sf-Via": via}) as c:
        assert (await c.read_resource(ENTRY))[0].text.startswith("---")
        assert (await request(c, "agents/get", name="explorer"))["agent"] == "explorer"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_local_with_resources_off_lists_the_tools(reader, mode):
    async with over_http(reader, {"Sf-Via": "local", "Sf-Resources": "off"}, mode=mode) as c:
        assert await listed(c) >= RESOURCE_TOOLS
        assert c.instructions == instructions.instructions(False, via="local")


async def test_the_handshake_reports_the_prefix_and_off(reader):
    async with over_http(reader, {"Sf-Resources": "off", "Sf-Resource-Prefix": "sf+"}) as c:
        assert await delivery_meta(c) == {
            "resources": "off", "listed": ["skills"], "via": "mcp", "prefix": "sf+",
        }


async def test_the_sf_via_beats_the_x_mcp_via(reader):  # Review Focus 1
    async with over_http(reader, {"Sf-Via": "local", "X-MCP-Via": "extension"}) as c:
        assert c.instructions == instructions.instructions(True, via="local")
        assert await names(c, "agents/list", "agents") == set()


async def test_an_unknown_via_reads_as_mcp(reader):  # Review Focus 2
    async with over_http(reader, {"Sf-Via": "bogus-210"}) as c:
        assert c.instructions == instructions.instructions(True)
        assert await names(c, "prompts/list", "prompts") >= PLUGIN_PROMPTS
        assert (await delivery_meta(c))["via"] == "mcp"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_vs_code_with_the_plugin_installed(reader, mode):  # Review Focus 3
    info = Implementation(name="Visual Studio Code", version="1")
    async with over_http(reader, {"Sf-Via": "local"}, mode=mode, client_info=info) as c:
        assert await listed(c) >= RESOURCE_TOOLS
        assert c.instructions == instructions.instructions(False, via="local")


async def test_a_gateway_prefix_leaves_the_local_line_alone(reader):  # Review Focus 4
    async with over_http(reader, {"Sf-Via": "local", "Sf-Resource-Prefix": "sf+"}) as c:
        assert c.instructions == instructions.instructions(True, via="local")


async def test_listing_skills_under_local_keeps_the_local_line(reader):  # Review Focus 5
    async with over_http(reader, {"Sf-Via": "local", "Sf-Listed": "skills"}) as c:
        assert ENTRY in {r["uri"] for r in (await request(c, "resources/list"))["resources"]}
        assert c.instructions == instructions.instructions(True, via="local")


async def test_the_old_per_resource_tools_are_gone(reader):
    """Seven tools, one per resource, became two that take a URI."""
    async with over_http(reader, {"X-MCP-Resources": "off"}) as c:
        names = await listed(c)
    for gone in (
        "current_session", "selenium_flow_skill", "list_flows", "get_flow",
        "flow_schema", "list_secrets",
    ):
        assert gone not in names
        assert await reader.mcp.get_tool(gone) is None, gone


# ---- what the tools read -----------------------------------------------------


async def test_the_listing_is_every_uri_to_read_and_nothing_to_draw(built_ui, reader):
    async with Client(reader.mcp.server) as c:
        result = await c.call_tool("list_resources", {})
    rows = json.loads(result.content[0].text)["resources"]
    uris = {row.get("uri") or row.get("uri_template") for row in rows}
    assert RESOURCE_URI in uris
    assert "skill://selenium-flow/driving-a-browser/SKILL.md" in uris
    assert not any(u.startswith("ui://") for u in uris), "the app shell is not reading"


async def test_a_resource_reads_the_same_through_the_tool(reader):
    reader.flows.save(NAMED, "login", {"steps": [{"tool": "navigate", "args": {"url": "u"}}]})
    got = json.loads((await read(reader, "flow://flows/login")).content[0].text)
    assert got["name"] == "login" and got["steps"]
    skill = (await read(reader, "skill://selenium-flow/driving-a-browser/SKILL.md")).content[0].text
    assert "name: driving-a-browser" in skill


async def test_the_app_shell_is_refused_rather_than_read(reader):
    with pytest.raises(ToolError, match="not to read"):
        await read(reader, "ui://selenium-flow/component")


def _serving(reader, data: bytes):
    reader.workspaces.browser = lambda name: "abc"
    reader.actions.grid.read_file = lambda session, name: data


async def test_an_image_comes_back_as_an_image(reader):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    _serving(reader, png)
    result = await read(reader, "session://downloads/shot.png")
    assert result.content[0].type == "image"
    assert result.content[0].mime_type == "image/png"


async def test_any_other_binary_is_described_not_dumped(reader):
    """Base64 of a downloaded PDF is megabytes of text a model can do nothing
    with, spent out of its own context."""
    pdf = b"%PDF-1.4" + b"x" * 50_000
    _serving(reader, pdf)
    result = await read(reader, "session://downloads/report.pdf")
    said = json.loads(result.content[0].text)
    assert said["binary"] is True and said["bytes"] == len(pdf)
    assert said["mime_type"] == "application/pdf"
    assert said["read"] == (
        "Not returned: binary content is not text. Read session://downloads for this "
        "file's link, which any HTTP client can download."
    )
    assert len(result.content[0].text) < 1_000


def test_where_linked_names_the_listing_that_holds_the_link():
    said = "Read {} for this file's link, which any HTTP client can download."
    assert where_linked("workspace://files/report.pdf") == said.format("workspace://files")
    assert where_linked("session://downloads/report.pdf") == said.format("session://downloads")
    assert where_linked("session://tester/downloads/g.pdf") == said.format("session://tester/downloads")
    assert where_linked("flow://flows/login") is None


async def test_both_tools_only_read(reader):
    async with over_http(reader, {"X-MCP-Resources": "off"}) as c:
        tools = {t.name: t for t in await c.list_tools()}
    for name in RESOURCE_TOOLS:
        assert tools[name].annotations.read_only_hint is True, name


async def test_the_status_resource_is_always_offered(server):
    uris = {str(r.uri) for r in await server.mcp.server.list_resources()}
    assert RESOURCE_URI in uris


# ---- session_id is advertised per mode --------------------------------------


def named(monkeypatch):
    monkeypatch.setattr(
        "kubed.selenium_flow.mcp.clients.request_values", lambda: http({"workspace": "d"})
    )


def unnamed(monkeypatch):
    monkeypatch.setattr("kubed.selenium_flow.mcp.clients.request_values", lambda: http())


async def test_no_tool_advertises_a_session_id(server, monkeypatch):
    """A model cannot pass what it cannot see, which is the point."""
    named(monkeypatch)
    tools = {t.name: t for t in await server.mcp.server.list_tools()}
    for name in ("navigate", "extract", "interact", "end_browser"):
        assert "session_id" not in tools[name].parameters["properties"], name


async def test_open_session_is_the_same_named_or_not(server, monkeypatch):
    """It has no session_id to shape, whoever calls it."""
    named(monkeypatch)
    a = {t.name: t for t in await server.mcp.server.list_tools()}["open_session"].parameters
    unnamed(monkeypatch)
    b = {t.name: t for t in await server.mcp.server.list_tools()}["open_session"].parameters
    assert a == b
    assert "session_id" not in a["properties"]




# ---- for a person picking one --------------------------------------------------


async def test_a_person_picking_a_flow_is_offered_the_names_they_can_read(reader):
    """VS Code asks for completions when a template or a prompt is picked.
    They are the caller's listing read back, so they cannot offer a
    flow the caller could not open."""
    from mcp.types import PromptReference, ResourceTemplateReference

    for name in ("login", "logout", "checkout"):
        reader.flows.save(NAMED, name, {"steps": [{"tool": "navigate", "args": {"url": "u"}}]})
    reader.flows.save("someone-else", "log-secret", {"steps": [{"tool": "navigate", "args": {"url": "u"}}]})
    async with Client(reader.mcp.server) as c:
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
    names = {str(r.uri): r.name for r in await reader.mcp.server.list_resources()}
    assert names["flow://flows"] == "Saved Flows"
    assert names[RESOURCE_URI] == "Current Workspace"
    assert not any(n.endswith("_resource") for n in names.values())


async def test_a_person_picking_a_file_is_offered_their_own_files(reader):
    from mcp.types import ResourceTemplateReference

    reader.flows.write_file(NAMED, "report.pdf", b"%PDF")
    reader.flows.write_file(NAMED, "shot.png", b"png")
    reader.flows.write_file("someone-else", "report-secret.pdf", b"%PDF")
    async with Client(reader.mcp.server) as c:
        offered = await c.complete(
            ResourceTemplateReference(type="ref/resource", uri="workspace://files/{name}"),
            {"name": "name", "value": "rep"},
        )
    assert offered.values == ["report.pdf"]


DOWNLOAD = ResourceTemplateReference(type="ref/resource", uri="session://downloads/{name}")


async def test_download_names_complete_to_nothing_with_no_session(reader):  # Review Focus 5
    reader.workspaces.browser = lambda name: ""
    async with Client(reader.mcp.server) as c:
        offered = await c.complete(DOWNLOAD, {"name": "name", "value": ""})
    assert offered.values == []


async def test_download_names_complete_from_the_open_browser(reader):
    reader.workspaces.browser = lambda name: "abc"
    reader.actions.grid.files = lambda session: [{"name": "export.csv", "size": 1}]
    async with Client(reader.mcp.server) as c:
        offered = await c.complete(DOWNLOAD, {"name": "name", "value": "exp"})
    assert offered.values == ["export.csv"]


async def test_an_svg_reads_back_as_the_text_it_is(reader):
    """An image to a browser, XML to a model — which cannot view it as a picture
    (Copilot, #39)."""
    _serving(reader, b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>")
    result = await read(reader, "session://downloads/chart.svg")
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


async def test_read_resource_refuses_an_old_uri_naming_the_new_one(reader):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="`session://current` is now `workspace://current`"):
        await read(reader, "session://current")


# ---- workspace://current/{part} ----------------------------------------------


async def read_part(server, part):
    async with Client(server.mcp.server) as c:
        result = await c.read_resource(f"{RESOURCE_URI}/{part}")
    return json.loads(result[0].text)


async def test_a_part_answers_the_workspace_and_that_key(reader):
    assert await read_part(reader, "grid_timeout") == {
        "workspace": NAMED, "grid_timeout": None,
    }
    assert await read_part(reader, "store") == {
        "workspace": NAMED, "store": "memory",
    }


async def test_a_key_the_context_leaves_out_answers_null(reader):
    assert (await read_part(reader, "site_data"))["site_data"] is None
    assert "site_data" not in await read_whole(reader)


async def read_whole(server):
    async with Client(server.mcp.server) as c:
        return json.loads((await c.read_resource(RESOURCE_URI))[0].text)


async def test_a_part_is_the_wholes_value_through_read_resource_too(reader):
    result = await read(reader, f"{RESOURCE_URI}/browser")
    assert json.loads(result.content[0].text) == {"workspace": NAMED, "browser": None}


async def test_a_part_the_context_cannot_carry_is_refused_with_the_keys(reader):
    from mcp.shared.exceptions import MCPError

    from kubed.selenium_flow.workspace.workspaces import CONTEXT_KEYS

    with pytest.raises(MCPError) as raised:
        await read_part(reader, "nonsense")
    assert all(key in str(raised.value) for key in CONTEXT_KEYS)


async def test_the_part_template_is_listed_beside_the_whole(reader):
    async with Client(reader.mcp.server) as c:
        templates = {t.uriTemplate for t in await c.list_resource_templates()}
    assert f"{RESOURCE_URI}/{{part}}" in templates


async def test_a_part_never_opens_a_browser(reader):
    assert (await read_part(reader, "live"))["live"] is False
    assert reader.store.get(NAMED) is None


def test_every_key_the_context_carries_is_in_the_list_of_keys():
    from dataclasses import replace

    from kubed.selenium_flow.workspace.workspaces import CONTEXT_KEYS, Caller

    from .conftest import manager

    m = manager()
    m.hints = lambda name, url: [{"name": "login"}]
    m.open_browser(Caller(NAMED), url="https://a.test/")
    m.store.update(NAMED, lambda r: replace(
        r, settings={**r.settings, "capture": True}, site_data={"origins": {"https://a.test": {"cookies": [], "local": {}, "session": {}}}},
    ))
    m.capture = type("C", (), {"counts": lambda self, name: None, "kept": lambda self: []})()
    carried = set(m.describe(Caller(NAMED)))
    assert carried - set(CONTEXT_KEYS) == set()
    assert {"flows_here", "guidance", "captured"} <= carried


def test_the_status_schema_declares_every_key_the_context_carries():
    from kubed.selenium_flow.spec.schemas import RESPONSES
    from kubed.selenium_flow.workspace.workspaces import CONTEXT_KEYS

    declared = set(RESPONSES["current_workspace"]["properties"])
    assert set(CONTEXT_KEYS) - declared == set()
