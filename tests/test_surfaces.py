"""The two surfaces, and the promise that they stay in step.

The whole design rests on one claim: an MCP tool and an HTTP endpoint are two
doors onto the same function. A capability added to one and forgotten in the
other breaks that quietly, so it is asserted here rather than trusted.
"""

import pytest

from kubed.selenium_flow import resources as resources_module
from kubed.selenium_flow.routes import ENDPOINTS

pytestmark = pytest.mark.unit

# Named separately from ENDPOINTS so a typo in the route table cannot make this
# test agree with itself.
#
# The resource-mirror tools (`current_session`, `selenium_flow_skill`) are
# deliberately absent: neither is a browser action, neither has an HTTP
# counterpart, and both are hidden from tools/list unless a client declares it
# cannot read resources. See test_resources.py and test_skill.py.
EXPECTED = {
    "open_session",
    "end_browser",
    "navigate",
    "interact",
    "frame",
    "resize",
    "dialog",
    "upload_file",
    "write",
    "press_key",
    "extract",
    "execute_script",
    "screenshot",
    "save_pdf",
}


def test_every_route_maps_to_a_real_action(actions):
    for path, method_name in ENDPOINTS.items():
        assert hasattr(actions, method_name), f"{path} points at a missing action"
        assert callable(getattr(actions, method_name))


def test_route_table_covers_every_action():
    assert set(ENDPOINTS.values()) == EXPECTED


async def test_tool_names_match_the_action_names(server):
    names = {t.name for t in await server.mcp.list_tools()}
    assert names == EXPECTED


async def test_every_action_is_reachable_from_both_surfaces(server):
    tools = {t.name for t in await server.mcp.list_tools()}
    routed = set(ENDPOINTS.values())
    assert tools == routed, (
        "an action is exposed on one surface only: "
        f"tools-only={tools - routed}, routes-only={routed - tools}"
    )


async def test_tools_declare_real_parameter_schemas(server):
    """Not a formality.

    The n8n MCP trigger advertised every tool as a single opaque `input` string,
    which silently dropped every argument. Typed schemas are the reason this
    server exists, so assert the parameters are actually published.
    """
    click = await server.mcp.get_tool("interact")
    props = click.parameters["properties"]
    assert {"session_id", "xpath", "url", "wait_timeout"} <= set(props)
    assert click.parameters["required"] == ["action", "xpath"]
    assert "input" not in props
    # ctx is injected by FastMCP and must never reach the model as a parameter
    assert "ctx" not in props


@pytest.mark.parametrize("resources", ["on", "off"])
async def test_every_tool_declares_its_safety_hints(server, monkeypatch, resources):
    """A client reads these to decide whether to ask the user first.

    An unannotated tool falls back to the MCP defaults — `readOnlyHint` false
    and `destructiveHint` TRUE — so a missing annotation is not neutral: it
    makes a harmless tool look dangerous and earns it a confirmation prompt it
    does not need. Every tool must say what it is.

    Parametrised over `resources` because that switch CHANGES THE TOOL LIST: a
    client that cannot read resources is also given the mirror tools
    (`current_session`, `session_files`, `selenium_flow_skill`), and checking
    only the default mode left all three unannotated — advertised as destructive
    when every one of them merely reads.
    """
    monkeypatch.setattr(
        resources_module, "_http", lambda: ({"resources": resources}, {})
    )
    tools = await server.mcp.list_tools()
    assert tools, "no tools listed, so this proves nothing"
    for tool in tools:
        hints = tool.annotations
        assert hints is not None, f"{tool.name} declares no annotations"
        assert hints.title, f"{tool.name} has no display title"


async def test_the_mirror_tools_are_reads(server, monkeypatch):
    """They exist so a client with no resource support can still ask a question.

    Asking a question changes nothing, and `selenium_flow_skill` does not even
    leave the process — it is answered from files inside the installed package.
    """
    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("current_session", "session_files", "selenium_flow_skill"):
        assert tools[name].annotations.read_only_hint is True, name

    assert tools["selenium_flow_skill"].annotations.open_world_hint is False


async def test_only_reading_the_page_is_marked_read_only(server):
    """The hint is a promise a client is entitled to act on, so it is asserted
    against a named list rather than left to whoever adds the next tool.

    `screenshot` is the interesting exclusion: it looks like a pure read, but
    `save=true` writes a file into the session's store, and a tool cannot be
    read-only only sometimes.
    """
    read_only = {
        t.name for t in await server.mcp.list_tools() if t.annotations.read_only_hint
    }
    assert read_only == {"extract"}, "of the browser ACTIONS, only extract reads"


async def test_a_tool_that_can_act_on_the_page_admits_it(server):
    """These hand the page an instruction it is free to interpret — a click, a
    keypress, an answered confirm, arbitrary JavaScript. Any of them can place
    an order or delete a record, and this server cannot tell which, so none may
    claim to be non-destructive."""
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("interact", "press_key", "dialog", "execute_script"):
        assert tools[name].annotations.destructive_hint is True, name


async def test_press_key_lists_its_keys_in_the_description(server):
    """The key names are a closed set, so the model should be told them."""
    press_key = await server.mcp.get_tool("press_key")
    description = press_key.description
    assert "enter" in description and "escape" in description
    assert "{keys}" not in description, "the placeholder was never filled in"


def test_actions_reject_a_missing_session_id(actions):
    with pytest.raises(ValueError, match="session_id is required"):
        actions._at("", None)


async def test_stateless_mode_keeps_both_surfaces_intact(server):
    """Statelessness is a transport setting, not a capability change.

    It exists so more than one replica can serve the /mcp surface — MCP sessions
    otherwise live in one process's memory. The browser is unaffected either way,
    because its session lives on the Grid and the caller carries the id.
    """
    from kubed.selenium_flow.server import SeleniumMCP

    stateless = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token="t", stateless=True
    )
    assert stateless.stateless is True
    assert server.stateless is False
    assert {t.name for t in await stateless.mcp.list_tools()} == EXPECTED
