"""The explorer agent (spec 2026-11-13-explorer-agent-design).

These tests are the only place a broken agent file is caught: ``add_plugin``
skips a file that does not conform and logs it, so the server boots without it.
"""

import logging
import re

import pytest
from fastmcp import Client
from kubed.mcp.agents import META_KEY, Agent, parse_agent

from kubed.selenium_flow import faults
from kubed.selenium_flow.mcp import skill
from kubed.selenium_flow.server import create_app

from .fakes import request

pytestmark = pytest.mark.unit

IN = {
    "open_session",
    "navigate",
    "extract",
    "outline",
    "screenshot",
    "interact",
    "write",
    "press_key",
    "frame",
    "dialog",
    "await_control",
    "run_flow",
    "save_flow",
    "execute_script",
    "end_browser",
}
# The host's resource reader (Claude Code's names), listed beside the
# registered tools (PLG-46).
HOST = {"ReadMcpResourceTool", "ToolSearch"}
OUT = {
    "delete_flow",
    "resize",
    "save_site_data",
    "upload_file",
    "drag",
    "print",
    "keep_file",
    "show",
    "assert",
    "network_body",
}
MIRROR = {"list_resources", "read_resource"}
NAMESPACE = "skill://selenium-flow/"
SKILL_URI = f"{NAMESPACE}driving-a-browser/SKILL.md"
# The skills the explorer names: the entry point, the flows skill and the sign-in skill.
SKILLS = {
    "driving-a-browser": SKILL_URI,
    "building-flows": f"{NAMESPACE}building-flows/SKILL.md",
    "signing-in": f"{NAMESPACE}signing-in/SKILL.md",
}
HEADINGS = ["Found", "Flows used", "Flows saved", "Couldn't", "Browser"]
QUOTED_URI = re.compile(r"`([a-z]+://[^`]+)`")
QUOTED_NAME = re.compile(r"`([a-z_]+)`")


AGENTS = skill.plugin_path() / "agents"


def shipped() -> Agent:
    text = (AGENTS / "explorer.md").read_text(encoding="utf-8")
    return parse_agent(text, SKILLS)


def body() -> str:
    return shipped().detail["instructions"]


def front() -> dict:
    return shipped().detail["_meta"][META_KEY]["frontmatter"]


def test_it_loads_strictly(caplog):  # spec test 1
    caplog.set_level(logging.WARNING)
    files = sorted(AGENTS.glob("*.md"))
    server = create_app({"grid": {"url": "http://grid.invalid:4444"}})
    assert "explorer.md" in [p.name for p in files]
    assert len(server.plugin.agents) == len(files)
    assert "explorer" in server.plugin.agents
    assert "is skipped" not in caplog.text
    assert front()["model"] == "sonnet"
    assert front()["maxTurns"] == 40
    assert front()["skills"] == ["driving-a-browser", "building-flows", "signing-in"]


async def test_every_listed_tool_exists(server):  # spec test 2
    registered = {t.name for t in await server.mcp.server.list_tools()}
    assert set(front()["tools"]) - HOST <= registered


def test_the_tools_are_ruling_3s():  # spec test 3
    tools = front()["tools"]
    assert len(tools) == len(set(tools))
    assert set(tools) == IN | HOST
    assert not set(tools) & OUT


async def test_it_routes_as_medium(server):  # spec test 5
    async with Client(server.mcp.server) as client:
        got = await request(client, "agents/get", name="explorer")
    assert got["modelRoutingHint"] == {"role": "medium", "vision": False}
    assert got["_meta"]["io.kubed/agent"]["frontmatter"] == front()


def test_no_name_is_host_qualified():  # spec test 6, Review Focus 3
    text = (AGENTS / "explorer.md").read_text(encoding="utf-8")
    assert "mcp__" not in text
    assert all(re.fullmatch(r"[a-z_]+", t) for t in set(front()["tools"]) - HOST)


def test_the_description_carries_both_rules():  # spec test 9, Review Focus 5
    description = shipped().card["description"]
    assert "in a session of its own" in description
    assert "keep using your own browser" in description
    assert "no sign-ins of its own" in description
    assert "you may submit the search form" in description


def test_it_works_in_a_session_of_its_own():  # PLG-47
    text = body()
    assert "`explorer`" in text
    assert "`open_session(session=…)`" in text
    assert "`end_browser(session=…)`" in text
    assert "pass that same `session` on every call" in text


def test_it_stops_on_a_pinned_connection():  # PLG-47
    """A pinned connection refuses every call naming its session: the explorer
    stops and says so, and never drops `session`, which would drive the main
    agent's browser."""
    text = " ".join(body().split())
    assert "pinned to another session" in text
    assert "Stop at the first one" in text
    assert "under *Couldn't* that this connection is pinned to one session" in text
    assert "Never drop `session`" in text
    assert "drives the main agent's browser" in text


def test_it_shares_only_when_told():  # PLG-47
    text = " ".join(body().split())
    assert "tells you to share the main agent's session" in text
    assert "no other option, which joins its live browser" in text
    assert "never call `end_browser` on it" in text
    assert "Leave a session you shared open" in text


def test_it_retries_once_on_a_held_session():  # PLG-47
    text = " ".join(body().split())
    assert f'"{faults.HELD}"' in text
    assert "Try once more" in text
    assert "`explorer-2`" in text
    assert "If that is refused too, stop" in text


async def test_it_names_each_skill_by_name(server):  # spec test 4, PLG-76
    text = body()
    assert all(f"`{name}`" in text for name in SKILLS) and "skill://" not in text
    async with Client(server.mcp.server) as client:
        got = await request(client, "agents/get", name="explorer")
    assert got["_meta"]["io.kubed/agent"]["skills"] == SKILLS
    assert got["instructions"] == body()


async def test_it_names_no_tool_it_does_not_have(server):  # spec test 7, Review Focus 1
    known = {t.name for t in await server.mcp.server.list_tools()} | OUT | MIRROR
    named = set(QUOTED_NAME.findall(body() + " " + shipped().card["description"]))
    assert named & known <= IN, named & known - IN


async def test_every_uri_it_names_is_served(server):  # spec test 8, Review Focus 2
    text = body()
    named = set(QUOTED_URI.findall(text))
    assert text.count("://") == len(QUOTED_URI.findall(text)), "a URI outside backticks"
    assert {
        "session://<name>",
        "session://<name>/console",
        "session://<name>/network",
        "flow://flows",
        "flow://flows/{name}",
    } <= named
    # A scheme named only to be avoided, as `workspace://…`, is not a read,
    # and a session's `<name>` is the template's `{name}`.
    named = {
        uri.replace("<name>", "{name}") for uri in named if not uri.endswith("…")
    }
    async with Client(server.mcp.server) as client:
        served = {str(r.uri) for r in await client.list_resources()} | {
            # MCP SDK v2 renamed the field; the older one warns on the new SDK.
            getattr(t, "uri_template", None) or t.uriTemplate
            for t in await client.list_resource_templates()
        }
    for uri in named:
        assert uri in served, uri


def test_the_report_has_its_five_headings_in_order():  # spec test 9, Review Focus 4
    text = body()
    at = [text.find(f"**{h}:**") for h in HEADINGS]
    assert -1 not in at, dict(zip(HEADINGS, at, strict=True))
    assert at == sorted(at)


def test_the_body_says_its_turn_budget():
    assert f"You have {front()['maxTurns']} turns" in body()
