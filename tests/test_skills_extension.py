"""The MCP Skills extension (SEP-2640), through real MCP requests in both eras.

A Skills-aware host trusts nothing it cannot verify: it reads each file the entry
names and checks it against the digest. So the strongest tests here do exactly
that over the wire, rather than comparing the entry with the disk.
"""

import hashlib
import json

import pytest
from fastmcp import Client
from fastmcp.skills import SkillsClientExtension
from kubed.mcp.skills import SkillsExtension
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from .conftest import over_http
from .fakes import header, request

pytestmark = pytest.mark.unit

EXTENSION_ID = SkillsExtension.identifier

NAMESPACE = "skill://selenium-flow"
SKILLS = (
    "driving-a-browser",
    "building-flows",
    "signing-in",
    "using-the-admin-ui",
    "deploying-the-server",
)
SKILL_URIS = sorted(f"{NAMESPACE}/{name}/SKILL.md" for name in SKILLS)
SKILL_URI = f"{NAMESPACE}/driving-a-browser/SKILL.md"
MANIFEST_URI = f"{NAMESPACE}/driving-a-browser/_manifest"
EXTENSION = {"Sf-Via": "extension"}


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_extension_is_declared_beside_resources(server, mode):
    async with Client(server.mcp.server, mode=mode) as client:
        caps = client.session.server_capabilities
        assert caps.extensions[EXTENSION_ID] == {"directoryRead": True}
        assert caps.resources is not None


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_listing_is_the_five_skills(server, mode):
    async with over_http(server, EXTENSION, mode=mode) as client:
        listing = await request(client, "skills/list")
    assert listing["resultType"] == "complete"
    assert listing["ttlMs"] == 0
    assert listing["cacheScope"] == "private"
    assert sorted(entry["uri"] for entry in listing["skills"]) == SKILL_URIS


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_get_returns_the_listed_entry(server, mode):
    async with over_http(server, EXTENSION, mode=mode) as client:
        entries = (await request(client, "skills/list"))["skills"]
        assert len(entries) == len(SKILLS)
        for listed in entries:
            got = await request(client, "skills/get", uri=listed["uri"])
            assert got["skill"] == listed
            assert got["resultType"] == "complete"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_every_file_verifies_against_its_digest(server, mode):
    async with over_http(server, EXTENSION, mode=mode) as client:
        entries = (await request(client, "skills/list"))["skills"]
        assert len(entries) == len(SKILLS)
        for entry in entries:
            for resource in entry["resources"]:
                data = (await client.read_resource(resource["uri"]))[0].text.encode()
                assert resource["size"] == len(data), resource["uri"]
                digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
                assert resource["digest"] == digest, resource["uri"]


async def test_each_entry_names_every_file_the_server_serves(server):
    """A reference added later is in the entry without anyone touching it.

    The listing shows each SKILL.md alone, so its manifest is the served set."""
    async with over_http(server, EXTENSION) as client:
        entries = (await request(client, "skills/list"))["skills"]
        assert len(entries) == len(SKILLS)
        for entry in entries:
            root = entry["uri"].removesuffix("/SKILL.md")
            manifest = json.loads((await client.read_resource(f"{root}/_manifest"))[0].text)
            served = {f"{root}/{f['path']}" for f in manifest["files"]}
            named = [resource["uri"] for resource in entry["resources"]]
            assert len(named) == len(set(named))
            assert set(named) == served
            assert entry["uri"] in named


async def test_the_frontmatter_is_the_served_header(server):
    async with over_http(server, EXTENSION) as client:
        entries = (await request(client, "skills/list"))["skills"]
        assert len(entries) == len(SKILLS)
        for entry in entries:
            text = (await client.read_resource(entry["uri"]))[0].text
            assert entry["frontmatter"] == header(text)
            assert entry["uri"] == f"{NAMESPACE}/{entry['frontmatter']['name']}/SKILL.md"


async def test_a_fastmcp_client_gets_the_entry(server):
    async with over_http(server, EXTENSION, extensions=[SkillsClientExtension()]) as client:
        listed = await client.list_skills()
        got = await client.get_skill(SKILL_URI)
    assert sorted(entry.uri for entry in listed) == SKILL_URIS
    assert got == next(entry for entry in listed if entry.uri == SKILL_URI)


@pytest.mark.parametrize(
    "uri", ["skill://nope/SKILL.md", MANIFEST_URI, "skill://selenium-flow/building-flows/references/FLOWS.md"]
)
async def test_get_of_any_other_uri_is_unknown(server, uri):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await request(client, "skills/get", uri=uri)
    assert caught.value.error.code == INVALID_PARAMS
    assert caught.value.error.message == f"Unknown skill URI: {uri}"


async def test_a_list_cursor_is_ignored(server):
    """No list page size is set, so under `extension` the five entries are the one page (PLG-22)."""
    async with over_http(server, EXTENSION) as client:
        first = await request(client, "skills/list")
        assert len(first["skills"]) == len(SKILLS)
        again = await request(client, "skills/list", cursor="2")
    assert again["skills"] == first["skills"]
    assert "nextCursor" not in again


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_the_default_via_lists_the_skills_as_resources_only(server, mode):
    async with over_http(server, mode=mode) as client:
        listing = await request(client, "skills/list")
        rows = {row["uri"] for row in (await request(client, "resources/list"))["resources"]}
        got = await request(client, "skills/get", uri=SKILL_URI)
    assert listing["skills"] == []
    assert set(SKILL_URIS) <= rows
    assert got["skill"]["uri"] == SKILL_URI
