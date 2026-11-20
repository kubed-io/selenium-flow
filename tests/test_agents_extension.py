"""Agents are served through the agents extension (spec 2026-11-12-plugin-agents).

kubed-mcp's plugin layer reads `plugin/agents/` (PLUG-27 to PLUG-34). These are
the rules its own tests leave out, each through a real `Client` against a plugin
of the case's own, and the declaration on selenium-flow's server.
"""

import copy
import logging
from pathlib import Path

import pytest
import yaml
from fastmcp import Client
from kubed.mcp.agents import (
    EXTENSION_ID,
    META_KEY,
    AgentsExtension,
    GetAgentParams,
    parse_agent,
)
from kubed.mcp.protocol import MCPError
from kubed.mcp.skills import SkillsExtension
from mcp_types import INVALID_PARAMS, PaginatedRequestParams

from kubed.selenium_flow.mcp import skill

from .fakes import plugin_server, request

pytestmark = pytest.mark.unit

FRONT = (
    "name: explorer\ndescription: |\n  Explores\n  a site.\nmodel: sonnet\n"
    "tools: [navigate, extract]\ncapabilities: [browse]\nskills: [selenium-flow, nope]"
)
BODY = "# Explore\n\nRead the skill first.\n"
SKILL = "---\nname: selenium-flow\ndescription: The skill.\n---\n\n# Skill\n"
# On a server named "t", the plugin's skill is served under it.
SKILL_URI = "skill://t/selenium-flow/SKILL.md"
FRESH = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
SERVER_INFO = "io.modelcontextprotocol/serverInfo"
GOOD = f"---\nname: explorer\ndescription: Explores.\n---\n{BODY}"


def agent(front, body=BODY):
    return f"---\n{front}\n---\n{body}"


def plugin(tmp_path, agents: dict, *, with_skill=True):
    files = {f"agents/{name}": text for name, text in agents.items()}
    if with_skill:
        files["skills/selenium-flow/SKILL.md"] = SKILL
    return plugin_server(tmp_path / "p", files)


def _without_server_info(result: dict) -> dict:
    """In auto mode FastMCP adds its serverInfo under _meta to extension results."""
    meta = result.get("_meta", {})
    meta.pop(SERVER_INFO, None)
    if not meta:
        result.pop("_meta", None)
    return result


async def listed(mcp):
    async with Client(mcp.server) as client:
        return _without_server_info(await request(client, "agents/list"))


async def detail(mcp, name="explorer"):
    async with Client(mcp.server) as client:
        return _without_server_info(await request(client, "agents/get", name=name))


async def names(mcp):
    return [a["name"] for a in (await listed(mcp))["agents"]]


# ---- a file becomes a card and a definition ----------------------------------


async def test_a_file_becomes_a_card_and_a_detail(tmp_path):
    mcp = plugin(tmp_path, {"explorer.md": agent(FRONT)})
    assert (await listed(mcp))["agents"] == [
        {"name": "explorer", "description": "Explores a site.", "capabilities": ["browse"]}
    ]
    assert await detail(mcp) == {
        **FRESH,
        "agent": "explorer",
        "instructions": "# Explore\n\nRead the skill first.",
        "modelRoutingHint": {"role": "medium", "vision": False},
        "tools": [],
        "_meta": {
            META_KEY: {
                "frontmatter": yaml.safe_load(FRONT),
                "skills": {"selenium-flow": SKILL_URI},
            }
        },
    }


@pytest.mark.parametrize(
    ("value", "role"),
    [
        ("opus", "large"),
        ("claude-opus-4-1", "large"),
        ("Sonnet", "medium"),
        ("claude-sonnet-4-5", "medium"),
        ("haiku", "small"),
        ("inherit", None),
        ("gpt-5", None),
        ("null", None),
        ("4", None),
    ],
)
async def test_the_routing_hint(tmp_path, value, role):
    mcp = plugin(tmp_path, {"x.md": agent(f"name: explorer\ndescription: x\nmodel: {value}")})
    got = await detail(mcp)
    assert got.get("modelRoutingHint") == ({"role": role, "vision": False} if role else None)


@pytest.mark.parametrize("extra", ["", "\ncapabilities: [1, x]"])
async def test_capabilities_default_to_none(tmp_path, extra):
    mcp = plugin(tmp_path, {"x.md": agent("name: explorer\ndescription: x" + extra)})
    assert (await listed(mcp))["agents"][0]["capabilities"] == []


async def test_a_blank_body_has_no_instructions(tmp_path):
    mcp = plugin(tmp_path, {"x.md": agent("name: explorer\ndescription: x", body="\n  \n")})
    assert "instructions" not in await detail(mcp)


@pytest.mark.parametrize(
    "skills",
    ["skills: selenium-flow", "skills: [1, selenium-flow]", "skills: {selenium-flow: x}", ""],
)
async def test_skills_resolve_only_from_a_list_of_names(tmp_path, skills):
    mcp = plugin(tmp_path, {"x.md": agent(f"name: explorer\ndescription: x\n{skills}")})
    assert (await detail(mcp))["_meta"][META_KEY]["skills"] == {}


async def test_skills_resolve_only_to_served_skills(tmp_path):
    mcp = plugin(
        tmp_path,
        {"x.md": agent("name: explorer\ndescription: x\nskills: [selenium-flow]")},
        with_skill=False,
    )
    assert (await detail(mcp))["_meta"][META_KEY]["skills"] == {}


async def test_the_cards_are_in_name_order(tmp_path):
    mcp = plugin(tmp_path, {
        "a.md": agent("name: zeta\ndescription: x"),
        "b.md": agent("name: alpha\ndescription: x"),
    })
    assert await names(mcp) == ["alpha", "zeta"]


# ---- a file that does not conform costs only itself --------------------------


@pytest.mark.parametrize(
    "text",
    [
        "# no header\n",
        "---\n- a\n- list\n---\n",
        "---\n{}\n---\n",
        "---\nname: [unclosed\n---\n",
        "---\nname: ex plorer\ndescription: x\n---\n",
        "---\nname: a/b\ndescription: x\n---\n",
        "---\nname: a@b\ndescription: x\n---\n",
        "---\nname: ex​plorer\ndescription: x\n---\n",
        "---\nname: 123\ndescription: x\n---\n",
        "---\nname: bad\ndescription: ''\n---\n",
        "---\nname: bad\ndescription: '   '\n---\n",
        "---\nname: bad\n---\n",
        "---\nname: bad\ndescription: x\nwhen: 2026-11-12\n---\n",
        "---\nname: bad\ndescription: x\nmetadata: {1: one, '1': also}\n---\n",
    ],
)
async def test_a_nonconforming_file_is_skipped_and_logged(tmp_path, caplog, text):
    with caplog.at_level(logging.WARNING):
        mcp = plugin(tmp_path, {"explorer.md": GOOD, "bad.md": text})
    assert await names(mcp) == ["explorer"]
    assert "is skipped" in caplog.text
    assert "bad.md" in caplog.text


async def test_a_file_that_is_not_utf8_is_skipped(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        mcp = plugin(tmp_path, {
            "explorer.md": GOOD,
            "bad.md": b"---\nname: caf\xe9\ndescription: x\n---\n",
        })
    assert await names(mcp) == ["explorer"]
    assert "is skipped" in caplog.text
    assert "bad.md" in caplog.text


async def test_crlf_line_endings_load(tmp_path):
    mcp = plugin(tmp_path, {"explorer.md": GOOD.replace("\n", "\r\n").encode()})
    assert (await detail(mcp))["instructions"] == "# Explore\n\nRead the skill first."


async def test_the_first_of_two_files_with_one_name_wins(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        mcp = plugin(tmp_path, {
            "a.md": agent("name: explorer\ndescription: First."),
            "b.md": agent("name: explorer\ndescription: Second."),
        })
    assert (await listed(mcp))["agents"][0]["description"] == "First."
    # kubed-mcp's warning names the agent, not the two files.
    assert "agent explorer is skipped" in caplog.text


async def test_only_markdown_at_the_top_level_is_read(tmp_path):
    mcp = plugin(tmp_path, {
        "drafts/x.md": agent("name: x\ndescription: x"),
        "notes.txt": agent("name: notes\ndescription: x"),
    })
    assert await names(mcp) == []


async def test_no_agents_folder_is_an_empty_listing(tmp_path):
    mcp = plugin_server(tmp_path / "p", {"skills/selenium-flow/SKILL.md": SKILL})
    async with Client(mcp.server, mode="legacy") as client:
        assert client.session.server_capabilities.extensions[EXTENSION_ID] == {
            "listChanged": False
        }
        listing = await request(client, "agents/list")
        with pytest.raises(MCPError) as caught:
            await request(client, "agents/get", name="explorer")
    assert listing == {**FRESH, "agents": []}
    assert caught.value.error.message == "Agent not found"


@pytest.mark.xfail(
    strict=True,
    raises=PermissionError,
    reason="kubed-mcp gap: plugins._md_files lets an OSError from iterdir() out of "
    "add_plugin; selenium-flow's agents.load skipped the folder with a warning",
)
async def test_a_folder_that_cannot_be_listed_is_no_agents(tmp_path, monkeypatch, caplog):
    folder = (tmp_path / "p" / "agents").resolve()
    real = Path.iterdir

    def iterdir(self):
        if self.resolve() == folder:
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with caplog.at_level(logging.WARNING):
        mcp = plugin(tmp_path, {"explorer.md": GOOD})
    assert await names(mcp) == []
    assert "is skipped" in caplog.text


# ---- where the plugin is found -------------------------------------------------


def _tree(root, *, plugin):
    module = root / "kubed" / "selenium_flow" / "mcp"
    module.mkdir(parents=True)
    if plugin:
        (module / "plugin" / "agents").mkdir(parents=True)
    return module


def test_an_installed_package_never_falls_back_to_the_root(tmp_path):
    module = _tree(tmp_path, plugin=True)
    found = skill.plugin_folder(str(module / "skill.py"), "agents")
    assert found == module / "plugin" / "agents"


def test_a_checkout_falls_back_to_the_repo_root(tmp_path):
    module = _tree(tmp_path, plugin=False)
    found = skill.plugin_folder(str(module / "skill.py"), "agents")
    assert found == tmp_path / "plugin" / "agents"


# ---- served by selenium-flow ---------------------------------------------------


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_extension_is_declared_beside_skills(server, mode):
    """Both declare in `server/discover` and, for a handshake-era client, in a
    legacy `initialize` too."""
    async with Client(server.mcp.server, mode=mode) as client:
        extensions = client.session.server_capabilities.extensions
    assert extensions[EXTENSION_ID] == {"listChanged": False}
    assert extensions[SkillsExtension.identifier] == {"directoryRead": True}


async def test_the_listing_is_the_shipped_agents(server):
    async with Client(server.mcp.server) as client:
        listing = _without_server_info(await request(client, "agents/list"))
    assert {k: listing[k] for k in FRESH} == FRESH
    assert [a["name"] for a in listing["agents"]] == ["explorer"]


async def test_get_returns_the_detail(server):
    async with Client(server.mcp.server) as client:
        got = await request(client, "agents/get", name="explorer")
        uri = got["_meta"][META_KEY]["skills"]["selenium-flow"]
        assert (await client.read_resource(uri))[0].text.startswith("---")
    assert uri == "skill://selenium-flow/selenium-flow/SKILL.md"
    assert got["agent"] == "explorer" and got["tools"] == [] and "capabilities" not in got
    assert {k: got[k] for k in FRESH} == FRESH


@pytest.mark.parametrize(
    ("method", "params", "message"),
    [
        ("agents/get", {"name": "nope"}, "Agent not found"),
        ("agents/get", {"name": ""}, None),
        ("agents/get", {}, None),
        ("agents/list", {"cursor": "2"}, "Invalid agents cursor"),
    ],
)
async def test_refusals_are_invalid_params(server, method, params, message):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await request(client, method, **params)
    assert caught.value.error.code == INVALID_PARAMS
    if message:
        assert caught.value.error.message == message
    else:
        # The schema refused it, not the handler's own lookup.
        assert caught.value.error.message != "Agent not found"
    if params.get("name") == "nope":
        assert caught.value.error.data == {"reason": "agent_not_found"}


async def test_the_declaration_stands_without_the_skills_one(tmp_path):
    mcp = plugin(tmp_path, {"explorer.md": GOOD}, with_skill=False)
    async with Client(mcp.server, mode="legacy") as client:
        extensions = client.session.server_capabilities.extensions
    assert extensions == {EXTENSION_ID: {"listChanged": False}}


async def test_a_caller_cannot_change_what_the_next_caller_is_served():
    """The handlers hand out copies: a caller in the same process that edits a
    result changes nothing the next one gets."""
    parsed = parse_agent(agent(FRONT.replace("name: explorer", "name: zeta")))
    extension = AgentsExtension([copy.deepcopy(parsed)])
    first = await extension.list_agents(None, PaginatedRequestParams())
    first["agents"][0]["capabilities"].append("hacked")
    got = await extension.get_agent(None, GetAgentParams(name="zeta"))
    got["_meta"][META_KEY]["frontmatter"]["name"] = "hacked"
    again = await extension.list_agents(None, PaginatedRequestParams())
    assert again["agents"][0]["capabilities"] == ["browse"]
    got = await extension.get_agent(None, GetAgentParams(name="zeta"))
    assert got["_meta"][META_KEY]["frontmatter"]["name"] == "zeta"
