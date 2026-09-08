"""The two surfaces, and the promise that they stay in step.

The whole design rests on one claim: an MCP tool and an HTTP endpoint are two
doors onto the same function. A capability added to one and forgotten in the
other breaks that quietly, so it is asserted here rather than trusted.
"""

import pytest

from kubed.selenium_flow.actions import Actions
from kubed.selenium_flow.routes import ENDPOINTS

pytestmark = pytest.mark.unit

# Named separately from ENDPOINTS so a typo in the route table cannot make this
# test agree with itself.
EXPECTED = {
    "open_session",
    "close_session",
    "navigate",
    "click",
    "write",
    "press_key",
    "extract",
    "execute_script",
    "screenshot",
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
    click = await server.mcp.get_tool("click")
    props = click.parameters["properties"]
    assert {"session_id", "xpath", "url", "wait_timeout"} <= set(props)
    assert props["session_id"]["type"] == "string"
    assert "input" not in props


async def test_press_key_lists_its_keys_in_the_description(server):
    """The key names are a closed set, so the model should be told them."""
    press_key = await server.mcp.get_tool("press_key")
    description = press_key.description
    assert "enter" in description and "escape" in description
    assert "{keys}" not in description, "the placeholder was never filled in"


def test_actions_reject_a_missing_session_id(actions):
    with pytest.raises(ValueError, match="session_id is required"):
        actions._at("", None)
