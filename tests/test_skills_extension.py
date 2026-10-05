"""The MCP Skills extension (SEP-2640), through real MCP requests in both eras.

A Skills-aware host trusts nothing it cannot verify: it reads each file the entry
names and checks it against the digest. So the strongest tests here do exactly
that over the wire, rather than comparing the entry with the disk.
"""

import hashlib
import logging

import pytest
import yaml
from fastmcp import Client, FastMCP
from fastmcp.server.providers.skills.skill_provider import SkillProvider
from mcp.shared.exceptions import MCPError
from mcp_types import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PaginatedRequestParams,
    Request,
    RequestParams,
)
from pydantic import ConfigDict, TypeAdapter

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import skill_extension
from kubed.selenium_flow.mcp.skill_extension import (
    EXTENSION_ID,
    GetSkillParams,
    SkillsExtension,
)
from kubed.selenium_flow.server import SeleniumMCP

pytestmark = pytest.mark.unit

SKILL_URI = "skill://selenium-flow/SKILL.md"
MANIFEST_URI = "skill://selenium-flow/_manifest"


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


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_extension_is_declared_beside_resources(server, mode):
    async with Client(server.mcp, mode=mode) as client:
        caps = client.session.server_capabilities
        assert caps.extensions[EXTENSION_ID] == {}
        assert caps.resources is not None


async def test_the_listing_is_the_one_skill(server):
    async with Client(server.mcp) as client:
        listing = await request(client, "skills/list")
    assert listing["resultType"] == "complete"
    assert listing["ttlMs"] == 0
    assert listing["cacheScope"] == "private"
    assert [entry["uri"] for entry in listing["skills"]] == [SKILL_URI]


async def test_get_returns_the_listed_entry(server):
    async with Client(server.mcp) as client:
        listed = (await request(client, "skills/list"))["skills"][0]
        got = await request(client, "skills/get", uri=SKILL_URI)
    assert got["skill"] == listed
    assert got["resultType"] == "complete"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_every_file_verifies_against_its_digest(server, mode):
    async with Client(server.mcp, mode=mode) as client:
        entry = (await request(client, "skills/list"))["skills"][0]
        for resource in entry["resources"]:
            data = (await client.read_resource(resource["uri"]))[0].text.encode()
            assert resource["size"] == len(data), resource["uri"]
            digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
            assert resource["digest"] == digest, resource["uri"]


async def test_the_entry_names_every_file_the_server_serves(server):
    """A reference added later is in the entry without anyone touching it."""
    async with Client(server.mcp) as client:
        entry = (await request(client, "skills/list"))["skills"][0]
        served = {
            str(r.uri)
            for r in await client.list_resources()
            if str(r.uri).startswith("skill://selenium-flow/")
        }
    named = [resource["uri"] for resource in entry["resources"]]
    assert len(named) == len(set(named))
    assert set(named) == served - {MANIFEST_URI}
    assert SKILL_URI in named


async def test_the_frontmatter_is_the_served_header(server):
    async with Client(server.mcp) as client:
        entry = (await request(client, "skills/list"))["skills"][0]
        text = (await client.read_resource(SKILL_URI))[0].text
    assert entry["frontmatter"] == header(text)
    assert entry["frontmatter"]["name"] == "selenium-flow"


@pytest.mark.parametrize(
    ("method", "params", "message"),
    [
        ("skills/get", {"uri": "skill://nope/SKILL.md"}, "Skill not found"),
        ("skills/get", {"uri": MANIFEST_URI}, "Skill not found"),
        ("skills/list", {"cursor": "2"}, "Invalid skills cursor"),
    ],
)
async def test_refusals_are_invalid_params(server, method, params, message):
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as caught:
            await request(client, method, **params)
    assert caught.value.error.code == INVALID_PARAMS
    assert message in str(caught.value)


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_switching_the_skill_off_removes_the_extension(mode):
    off = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "t"},
                 mcp={"skill": False})
    )
    async with Client(off.mcp, mode=mode) as client:
        extensions = client.session.server_capabilities.extensions or {}
        assert EXTENSION_ID not in extensions
        with pytest.raises(MCPError) as caught:
            await request(client, "skills/list")
    assert caught.value.error.code == METHOD_NOT_FOUND


@pytest.mark.parametrize(
    "front",
    [
        "name: some-other-name\ndescription: Does a thing.",
        "name: odd-skill\ndescription: ''",
        "name: odd-skill\ndescription: " + "x" * 1025,
        "- a list\n- not a mapping",
        "name: odd-skill\ndescription: Dated.\nwhen: 2026-10-05",
        "name: odd-skill\ndescription: NaN.\nweight: .nan",
    ],
    ids=["name", "empty-description", "long-description", "not-a-mapping", "date", "nan"],
)
async def test_a_nonconforming_skill_is_served_but_not_offered(tmp_path, caplog, front):
    folder = tmp_path / "odd-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n\n# Body\n", encoding="utf-8")
    provider = SkillProvider(folder, supporting_files="resources")
    mcp = FastMCP("t")
    mcp.add_provider(provider)

    with caplog.at_level(logging.WARNING):
        assert skill_extension.register(mcp, provider) is False
    assert "not offered through the Skills extension" in caplog.text

    async with Client(mcp) as client:
        assert EXTENSION_ID not in (client.session.server_capabilities.extensions or {})
        assert (await client.read_resource("skill://odd-skill/SKILL.md"))[0].text


async def test_a_caller_cannot_change_what_the_next_caller_is_served(server):
    entry = skill_extension.entry_for(server.skill)
    extension = SkillsExtension(entry)
    first = await extension.list_skills(None, PaginatedRequestParams())
    first["skills"][0]["resources"].clear()
    first["skills"][0]["frontmatter"]["name"] = "changed"
    second = await extension.list_skills(None, PaginatedRequestParams())
    assert second["skills"][0]["resources"]
    assert second["skills"][0]["frontmatter"]["name"] == "selenium-flow"

    got = await extension.get_skill(None, GetSkillParams(uri=SKILL_URI))
    got["skill"]["resources"].clear()
    got["skill"]["frontmatter"]["name"] = "changed"
    again = await extension.get_skill(None, GetSkillParams(uri=SKILL_URI))
    assert again["skill"]["resources"]
    assert again["skill"]["frontmatter"]["name"] == "selenium-flow"


async def test_a_dash_run_inside_a_value_is_not_the_header_end(tmp_path):
    folder = tmp_path / "dashed"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: dashed\ndescription: before --- after\n---\n\n# Body\n",
        encoding="utf-8",
    )
    entry = skill_extension.entry_for(SkillProvider(folder, supporting_files="resources"))
    assert entry is not None
    assert entry["frontmatter"]["description"] == "before --- after"
