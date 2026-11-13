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

from kubed.selenium_flow.mcp import agents

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


class ExtensionParams(RequestParams):
    model_config = ConfigDict(extra="allow")


async def request(client, method, **params):
    return await client.session.send_request(
        Request(method=method, params=ExtensionParams(**params)), TypeAdapter(dict)
    )


def shipped() -> agents.Agent:
    return next(a for a in agents.load(skills={"selenium-flow"}) if a.name == "explorer")


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
