"""Agent files load into cards and details (spec 2026-11-12-plugin-agents)."""

import logging
from pathlib import Path

import pytest
import yaml
from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    Request,
    RequestParams,
)
from pydantic import ConfigDict, TypeAdapter

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import agents, skill_extension
from kubed.selenium_flow.mcp.agents import EXTENSION_ID
from kubed.selenium_flow.mcp.frontmatter import split
from kubed.selenium_flow.server import SeleniumMCP

pytestmark = pytest.mark.unit

FRONT = (
    "name: explorer\ndescription: |\n  Explores\n  a site.\nmodel: sonnet\n"
    "tools: [navigate, extract]\ncapabilities: [browse]\nskills: [selenium-flow, nope]"
)
BODY = "# Explore\n\nRead the skill first.\n"


def agent_file(folder, file, front, body=BODY):
    path = folder / file
    path.write_text(f"---\n{front}\n---\n{body}", encoding="utf-8")
    return path


def good(folder):
    return agent_file(folder, "explorer.md", "name: explorer\ndescription: Explores.")


def names(folder, **kw):
    return [a.name for a in agents.load(folder, **kw)]


def test_a_file_becomes_a_card_and_a_detail(tmp_path):
    agent_file(tmp_path, "explorer.md", FRONT)
    [agent] = agents.load(tmp_path, skills={"selenium-flow"})
    assert agent.name == "explorer"
    assert agent.card == {"name": "explorer", "description": "Explores a site.", "capabilities": ["browse"]}
    assert agent.detail == {
        "agent": "explorer",
        "instructions": "# Explore\n\nRead the skill first.",
        "modelRoutingHint": {"role": "medium", "vision": False},
        "tools": [],
        "_meta": {
            "io.kubed/agent": {
                "frontmatter": yaml.safe_load(FRONT),
                "skills": {"selenium-flow": "skill://selenium-flow/SKILL.md"},
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
        (None, None),
        (4, None),
    ],
)
def test_the_routing_hint(value, role):
    assert agents.routing_hint(value) == ({"role": role, "vision": False} if role else None)


def test_no_model_has_no_hint(tmp_path):
    good(tmp_path)
    [agent] = agents.load(tmp_path)
    assert "modelRoutingHint" not in agent.detail


@pytest.mark.parametrize("extra", ["", "\ncapabilities: [1, x]"])
def test_capabilities_default_to_none(tmp_path, extra):
    agent_file(tmp_path, "explorer.md", "name: explorer\ndescription: x" + extra)
    [agent] = agents.load(tmp_path)
    assert agent.card["capabilities"] == []


def test_a_blank_body_has_no_instructions(tmp_path):
    agent_file(tmp_path, "explorer.md", "name: explorer\ndescription: x", body="\n  \n")
    [agent] = agents.load(tmp_path)
    assert "instructions" not in agent.detail


@pytest.mark.parametrize(
    "skills",
    ["skills: selenium-flow", "skills: [1, selenium-flow]", "skills: {selenium-flow: x}", ""],
)
def test_skills_resolve_only_to_served_skills(tmp_path, skills):
    agent_file(tmp_path, "explorer.md", f"name: explorer\ndescription: x\n{skills}")
    [agent] = agents.load(tmp_path, skills={"selenium-flow"})
    assert agent.detail["_meta"][agents.META_KEY]["skills"] == {}


def test_skills_resolve_to_nothing_by_default(tmp_path):
    agent_file(tmp_path, "explorer.md", "name: explorer\ndescription: x\nskills: [selenium-flow]")
    [agent] = agents.load(tmp_path)
    assert agent.detail["_meta"][agents.META_KEY]["skills"] == {}


def test_the_cards_are_in_name_order(tmp_path):
    agent_file(tmp_path, "a.md", "name: zeta\ndescription: x")
    agent_file(tmp_path, "b.md", "name: alpha\ndescription: x")
    assert names(tmp_path) == ["alpha", "zeta"]


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
def test_a_nonconforming_file_is_skipped_and_logged(tmp_path, caplog, text):
    good(tmp_path)
    (tmp_path / "bad.md").write_text(text, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert names(tmp_path) == ["explorer"]
    assert "is skipped" in caplog.text
    assert "bad.md" in caplog.text


def test_a_file_that_is_not_utf8_is_skipped(tmp_path, caplog):
    good(tmp_path)
    (tmp_path / "bad.md").write_bytes(b"---\nname: caf\xe9\ndescription: x\n---\n")
    with caplog.at_level(logging.WARNING):
        assert names(tmp_path) == ["explorer"]
    assert "is skipped" in caplog.text
    assert "bad.md" in caplog.text


def test_crlf_line_endings_load(tmp_path):
    with (tmp_path / "explorer.md").open("w", encoding="utf-8", newline="\r\n") as f:
        f.write(f"---\nname: explorer\ndescription: x\n---\n{BODY}")
    [agent] = agents.load(tmp_path)
    assert agent.detail["instructions"] == "# Explore\n\nRead the skill first."


def test_the_first_of_two_files_with_one_name_wins(tmp_path, caplog):
    agent_file(tmp_path, "a.md", "name: explorer\ndescription: First.")
    agent_file(tmp_path, "b.md", "name: explorer\ndescription: Second.")
    with caplog.at_level(logging.WARNING):
        [agent] = agents.load(tmp_path)
    assert agent.card["description"] == "First."
    assert "b.md" in caplog.text
    assert "a.md" in caplog.text


def test_only_markdown_at_the_top_level_is_read(tmp_path):
    (tmp_path / "drafts").mkdir()
    agent_file(tmp_path / "drafts", "x.md", "name: x\ndescription: x")
    agent_file(tmp_path, "notes.txt", "name: notes\ndescription: x")
    assert agents.load(tmp_path) == []


def test_a_missing_folder_is_no_agents(tmp_path):
    assert agents.load(tmp_path / "missing") == []


def test_a_folder_that_cannot_be_listed_is_no_agents(tmp_path, monkeypatch, caplog):
    good(tmp_path)
    real = Path.iterdir

    def iterdir(self):
        if self == tmp_path:
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with caplog.at_level(logging.WARNING):
        assert agents.load(tmp_path) == []
    assert f"agent folder {tmp_path} is skipped" in caplog.text


def _tree(root, *, plugin, agents_dir=False):
    module = root / "kubed" / "selenium_flow" / "mcp"
    module.mkdir(parents=True)
    if plugin:
        (module / "plugin").mkdir()
    if agents_dir:
        (module / "plugin" / "agents").mkdir()
    return module


def test_an_installed_package_never_falls_back_to_the_root(tmp_path, monkeypatch):
    module = _tree(tmp_path, plugin=True)
    monkeypatch.setattr(agents, "__file__", str(module / "agents.py"))
    assert agents.agents_path() == module / "plugin" / "agents"


def test_a_checkout_falls_back_to_the_repo_root(tmp_path, monkeypatch):
    module = _tree(tmp_path, plugin=False)
    monkeypatch.setattr(agents, "__file__", str(module / "agents.py"))
    assert agents.agents_path() == tmp_path / "plugin" / "agents"


def test_split_returns_the_header_and_the_body():
    assert split("---\na: 1\n---\nbody\n") == ({"a": 1}, "body")
    assert split("no header") is None
    assert split("---\na: 1\n") is None
    assert split("---\na: [\n---\n") is None


class ExtensionParams(RequestParams):
    model_config = ConfigDict(extra="allow")


async def request(client, method, **params):
    return await client.session.send_request(
        Request(method=method, params=ExtensionParams(**params)), TypeAdapter(dict)
    )


def header(text: str) -> object:
    lines = text.splitlines()
    end = next(i for i, line in enumerate(lines[1:], start=1) if line.rstrip() == "---")
    return yaml.safe_load("\n".join(lines[1:end]))


FRESH = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}


def serving(monkeypatch, folder, **settings):
    monkeypatch.setattr(agents, "agents_path", lambda: folder)
    return SeleniumMCP(
        Settings(
            grid={"url": "http://grid.invalid:4444"}, auth={"token": "t"}, **settings
        )
    )


@pytest.fixture
def folder(tmp_path):
    base = tmp_path / "agents"
    base.mkdir()
    agent_file(base, "zeta.md", FRONT.replace("name: explorer", "name: zeta"))
    agent_file(base, "alpha.md", "name: alpha\ndescription: First.", body="")
    return base


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_extension_is_declared_beside_skills(monkeypatch, folder, mode):
    async with Client(serving(monkeypatch, folder).mcp, mode=mode) as client:
        extensions = client.session.server_capabilities.extensions
    assert extensions[EXTENSION_ID] == {"listChanged": False}
    assert extensions[skill_extension.EXTENSION_ID] == {}


async def test_the_listing_is_the_cards_in_name_order(monkeypatch, folder):
    async with Client(serving(monkeypatch, folder).mcp) as client:
        listing = await request(client, "agents/list")
    # In auto mode FastMCP adds its serverInfo under _meta to extension results.
    assert set(listing.pop("_meta", {})) <= {"io.modelcontextprotocol/serverInfo"}
    assert listing == {
        **FRESH,
        "agents": [
            {"name": "alpha", "description": "First.", "capabilities": []},
            {
                "name": "zeta",
                "description": "Explores a site.",
                "capabilities": ["browse"],
            },
        ],
    }


async def test_get_returns_the_detail(monkeypatch, folder):
    async with Client(serving(monkeypatch, folder).mcp) as client:
        got = await request(client, "agents/get", name="zeta")
        alpha = await request(client, "agents/get", name="alpha")
        skill = got["_meta"]["io.kubed/agent"]["skills"]["selenium-flow"]
        assert (await client.read_resource(skill))[0].text.startswith("---")
    assert got["agent"] == "zeta" and got["tools"] == [] and "capabilities" not in got
    assert got["modelRoutingHint"] == {"role": "medium", "vision": False}
    assert got["_meta"]["io.kubed/agent"]["frontmatter"] == header(
        (folder / "zeta.md").read_text()
    )
    assert {k: got[k] for k in FRESH} == FRESH
    assert "instructions" not in alpha and "modelRoutingHint" not in alpha


@pytest.mark.parametrize(
    ("method", "params", "message"),
    [
        ("agents/get", {"name": "nope"}, "Agent not found"),
        ("agents/get", {"name": ""}, None),
        ("agents/get", {}, None),
        ("agents/list", {"cursor": "2"}, "Invalid agents cursor"),
    ],
)
async def test_refusals_are_invalid_params(monkeypatch, folder, method, params, message):
    async with Client(serving(monkeypatch, folder).mcp) as client:
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


async def test_a_broken_file_costs_only_itself(monkeypatch, folder):
    (folder / "broken.md").write_text("# no header\n", encoding="utf-8")
    async with Client(serving(monkeypatch, folder).mcp) as client:
        listing = await request(client, "agents/list")
    assert [a["name"] for a in listing["agents"]] == ["alpha", "zeta"]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_switching_the_skill_off_removes_the_extension(monkeypatch, folder, mode):
    server = serving(monkeypatch, folder, mcp={"skill": False})
    async with Client(server.mcp, mode=mode) as client:
        extensions = client.session.server_capabilities.extensions or {}
        assert EXTENSION_ID not in extensions
        with pytest.raises(MCPError) as caught:
            await request(client, "agents/list")
    assert caught.value.error.code == METHOD_NOT_FOUND


async def test_no_agents_folder_is_an_empty_listing(monkeypatch, tmp_path):
    server = serving(monkeypatch, tmp_path / "missing")
    async with Client(server.mcp, mode="legacy") as client:
        assert client.session.server_capabilities.extensions[EXTENSION_ID] == {
            "listChanged": False
        }
        assert await request(client, "agents/list") == {**FRESH, "agents": []}
        with pytest.raises(MCPError) as caught:
            await request(client, "agents/get", name="explorer")
    assert caught.value.error.message == "Agent not found"


async def test_the_declaration_stands_without_the_skills_one():
    mcp = FastMCP("t")
    agents.register(mcp, [])
    async with Client(mcp, mode="legacy") as client:
        extensions = client.session.server_capabilities.extensions
    assert extensions == {EXTENSION_ID: {"listChanged": False}}


async def test_a_caller_cannot_change_what_the_next_caller_is_served(folder):
    extension = agents.AgentsExtension(agents.load(folder, skills={"selenium-flow"}))
    first = await extension.list_agents(None, agents.PaginatedRequestParams())
    first["agents"][1]["capabilities"].append("hacked")
    got = await extension.get_agent(None, agents.GetAgentParams(name="zeta"))
    got["_meta"]["io.kubed/agent"]["frontmatter"]["name"] = "hacked"
    again = await extension.list_agents(None, agents.PaginatedRequestParams())
    assert again["agents"][1]["capabilities"] == ["browse"]
    got = await extension.get_agent(None, agents.GetAgentParams(name="zeta"))
    assert got["_meta"]["io.kubed/agent"]["frontmatter"]["name"] == "zeta"
