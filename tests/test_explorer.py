"""The explorer agent (spec 2026-11-13-explorer-agent-design).

These tests are the only place a broken agent file is caught: ``agents.load``
skips a file that does not conform and logs it, so the server boots without it.
"""

import logging
import re

import pytest
from fastmcp import Client
from mcp_types import Request, RequestParams
from pydantic import ConfigDict, TypeAdapter

from kubed.selenium_flow.mcp import agents, skill

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
}
OUT = {
    "end_browser",
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
SKILL_URI = "skill://selenium-flow/SKILL.md"
HEADINGS = ["Found", "Flows used", "Flows saved", "Couldn't", "Browser"]
QUOTED_URI = re.compile(r"`([a-z]+://[^`]+)`")
QUOTED_NAME = re.compile(r"`([a-z_]+)`")


class ExtensionParams(RequestParams):
    model_config = ConfigDict(extra="allow")


async def request(client, method, **params):
    return await client.session.send_request(
        Request(method=method, params=ExtensionParams(**params)), TypeAdapter(dict)
    )


def shipped() -> agents.Agent:
    return next(a for a in agents.load(skills={"selenium-flow"}) if a.name == "explorer")


def body() -> str:
    return shipped().detail["instructions"]


def front() -> dict:
    return shipped().detail["_meta"][agents.META_KEY]["frontmatter"]


def test_it_loads_strictly(caplog):  # spec test 1
    caplog.set_level(logging.WARNING)
    files = sorted(agents.agents_path().glob("*.md"))
    loaded = agents.load(skills={"selenium-flow"})
    assert "explorer.md" in [p.name for p in files]
    assert len(loaded) == len(files)
    assert "explorer" in [a.name for a in loaded]
    assert "is skipped" not in caplog.text
    assert front()["model"] == "sonnet"
    assert front()["maxTurns"] == 40
    assert front()["skills"] == ["selenium-flow"]


async def test_every_listed_tool_exists(server):  # spec test 2
    registered = {t.name for t in await server.mcp.list_tools()}
    assert set(front()["tools"]) <= registered


def test_the_tools_are_ruling_3s():  # spec test 3
    tools = front()["tools"]
    assert len(tools) == len(set(tools))
    assert set(tools) == IN
    assert not set(tools) & OUT


async def test_it_routes_as_medium(server):  # spec test 5
    async with Client(server.mcp) as client:
        got = await request(client, "agents/get", name="explorer")
    assert got["modelRoutingHint"] == {"role": "medium", "vision": False}
    assert got["_meta"]["io.kubed/agent"]["frontmatter"] == front()


def test_no_name_is_host_qualified():  # spec test 6, Review Focus 3
    text = (agents.agents_path() / "explorer.md").read_text(encoding="utf-8")
    assert "mcp__" not in text
    assert all(re.fullmatch(r"[a-z_]+", t) for t in front()["tools"])


def test_the_description_carries_both_rules():  # spec test 9, Review Focus 5
    description = shipped().card["description"]
    assert "do not call selenium-flow tools until it reports" in description
    assert "you may submit the search form" in description


async def test_it_names_the_skill_by_uri(server):  # spec test 4
    assert f"`{SKILL_URI}`" in body()
    async with Client(server.mcp) as client:
        got = await request(client, "agents/get", name="explorer")
    assert got["_meta"]["io.kubed/agent"]["skills"] == {"selenium-flow": SKILL_URI}
    assert got["instructions"] == body()


async def test_it_names_no_tool_it_does_not_have(server):  # spec test 7, Review Focus 1
    known = {t.name for t in await server.mcp.list_tools()} | OUT | MIRROR
    named = set(QUOTED_NAME.findall(body() + " " + shipped().card["description"]))
    assert named & known <= IN, named & known - IN


async def test_every_uri_it_names_is_served(server):  # spec test 8, Review Focus 2
    text = body()
    named = set(QUOTED_URI.findall(text))
    assert text.count("://") == len(QUOTED_URI.findall(text)), "a URI outside backticks"
    assert {
        SKILL_URI,
        "skill://selenium-flow/references/FLOWS.md",
        "workspace://current",
        "flow://flows",
        "flow://flows/{name}",
    } <= named
    async with Client(server.mcp) as client:
        served = {str(r.uri) for r in await client.list_resources()} | {
            # MCP SDK v2 renamed the field; the older one warns on the new SDK.
            getattr(t, "uri_template", None) or t.uriTemplate
            for t in await client.list_resource_templates()
        }
    for uri in named:
        if uri.startswith("skill://selenium-flow/"):
            assert (skill.skill_path() / uri.removeprefix("skill://selenium-flow/")).is_file(), uri
        else:
            assert uri in served, uri


def test_the_report_has_its_five_headings_in_order():  # spec test 9, Review Focus 4
    text = body()
    at = [text.find(f"**{h}:**") for h in HEADINGS]
    assert -1 not in at, dict(zip(HEADINGS, at, strict=True))
    assert at == sorted(at)


def test_the_body_says_its_turn_budget():
    assert f"You have {front()['maxTurns']} turns" in body()
