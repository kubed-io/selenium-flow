"""The skill's folders as SEP-2640 directory resources, and the index files beside them.

A folder is read two ways: ``resources/directory/read``, which a proxy that
does not know the method refuses, and ``_index.json`` / ``_index.md``, which
plain ``resources/read`` reaches anywhere. Every test goes through a real
client, and the two ways are checked against each other rather than against
the provider's own helpers.
"""

import json

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from kubed.selenium_flow.mcp import skill as skill_module

from .fakes import request

pytestmark = pytest.mark.unit

ROOT = "skill://selenium-flow/selenium-flow"
REFERENCES = f"{ROOT}/references"
SKILL_DIR = skill_module.skill_path()
DIRECTORY = "inode/directory"


def reference_uris() -> list[str]:
    """The reference files on disk, as the URIs they are served at."""
    return sorted(
        f"{REFERENCES}/{p.name}" for p in (SKILL_DIR / "references").iterdir() if p.is_file()
    )


async def read_json(client, uri: str):
    contents = await client.read_resource(uri)
    assert contents[0].mime_type == "application/json"
    return json.loads(contents[0].text)


# ---- the index files ---------------------------------------------------------


async def test_index_json_reads_beside_each_folder(server):
    async with Client(server.mcp.server) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
        references = await read_json(client, f"{REFERENCES}/_index.json")
    assert [r["uri"] for r in root] == [f"{ROOT}/SKILL.md", REFERENCES]
    assert [r["uri"] for r in references] == reference_uris()


@pytest.mark.parametrize("folder", [ROOT, REFERENCES])
async def test_index_md_is_one_line_per_child(server, folder):
    async with Client(server.mcp.server) as client:
        children = await read_json(client, f"{folder}/_index.json")
        contents = await client.read_resource(f"{folder}/_index.md")
    assert contents[0].mime_type == "text/markdown"
    assert contents[0].text.splitlines() == [
        f"{c['uri']}: {c['description']}" for c in children
    ]


async def test_a_folder_child_is_a_directory_resource(server):
    async with Client(server.mcp.server) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
    folder = next(r for r in root if r["uri"] == REFERENCES)
    assert folder["mimeType"] == DIRECTORY
    assert folder["name"] == "selenium-flow/selenium-flow/references"
    assert "selenium-flow" in folder["description"]


async def test_no_folder_uri_ends_in_a_slash(server):
    async with Client(server.mcp.server) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
    folders = [r["uri"] for r in root if r["mimeType"] == DIRECTORY]
    assert folders
    assert not [uri for uri in folders if uri.endswith("/")]


async def test_a_markdown_child_is_described_by_its_heading(server):
    async with Client(server.mcp.server) as client:
        references = await read_json(client, f"{REFERENCES}/_index.json")
    flows = next(r for r in references if r["uri"].endswith("/FLOWS.md"))
    assert flows["description"] == "Flows"
    assert flows["mimeType"] == "text/markdown"


async def test_skill_md_is_described_as_the_listing_describes_it(server):
    async with Client(server.mcp.server) as client:
        listed = next(
            r for r in await client.list_resources() if str(r.uri) == f"{ROOT}/SKILL.md"
        )
        root = await read_json(client, f"{ROOT}/_index.json")
    assert root[0]["description"] == listed.description


async def test_folders_and_indexes_are_unlisted(server):
    async with Client(server.mcp.server) as client:
        rows = await client.list_resources()
        templates = await client.list_resource_templates()
    uris = [str(r.uri) for r in rows]
    assert not [r for r in rows if r.mime_type == DIRECTORY]
    assert not [u for u in uris if "/_index." in u or u in (ROOT, REFERENCES)]
    assert not [t for t in templates if t.uri_template.startswith("skill://")]


async def test_the_manifest_is_never_a_child_and_still_reads(server):
    async with Client(server.mcp.server) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
        manifest = await read_json(client, f"{ROOT}/_manifest")
    assert not [r for r in root if "_manifest" in r["uri"] or "_index" in r["uri"]]
    assert "references/FLOWS.md" in [f["path"] for f in manifest["files"]]


async def test_an_index_of_no_folder_is_not_found(server):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(f"{ROOT}/nope/_index.json")
    assert caught.value.error.code == INVALID_PARAMS
    assert caught.value.error.message.startswith("Resource not found")


# ---- resources/directory/read --------------------------------------------------

METHOD = "resources/directory/read"


async def read_directory(client, **params) -> dict:
    return await request(client, METHOD, **params)


async def test_the_root_lists_its_direct_children(server):
    async with Client(server.mcp.server) as client:
        result = await read_directory(client, uri=ROOT)
    assert [r["uri"] for r in result["resources"]] == [f"{ROOT}/SKILL.md", REFERENCES]
    assert result["resources"][1]["mimeType"] == DIRECTORY
    assert "nextCursor" not in result


async def test_descending_lists_the_folder_alone(server):
    async with Client(server.mcp.server) as client:
        result = await read_directory(client, uri=REFERENCES)
    assert [r["uri"] for r in result["resources"]] == reference_uris()


@pytest.mark.parametrize("folder", [ROOT, REFERENCES])
async def test_the_method_returns_what_the_index_holds(server, folder):
    async with Client(server.mcp.server) as client:
        result = await read_directory(client, uri=folder)
        index = await read_json(client, f"{folder}/_index.json")
    assert result["resources"] == index


async def test_a_file_child_is_its_resources_list_row(server):
    async with Client(server.mcp.server) as client:
        listed = next(
            r for r in (await client.session.list_resources()).resources
            if str(r.uri) == f"{ROOT}/SKILL.md"
        )
        result = await read_directory(client, uri=ROOT)
    row = listed.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert result["resources"][0] == row


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"uri": f"{ROOT}/nope"}, "Directory not found"),
        ({"uri": f"{ROOT}/SKILL.md"}, "Directory not found"),
        ({"uri": f"{REFERENCES}/"}, "Directory not found"),
        ({"uri": f"{ROOT}/"}, "Directory not found"),
        ({"uri": "workspace://current"}, "Directory not found"),
        ({"uri": ROOT, "cursor": "abc"}, "Invalid directory cursor"),
        ({"uri": ROOT, "cursor": "0"}, "Invalid directory cursor"),
        ({"uri": ROOT, "cursor": "99"}, "Invalid directory cursor"),
        ({"uri": ROOT, "cursor": "1" * 5000}, "Invalid directory cursor"),
        ({"uri": ""}, ""),
        ({}, ""),
    ],
    ids=["missing", "file", "trailing-slash", "root-slash", "other-scheme", "cursor-text",
         "cursor-zero", "cursor-past-end", "cursor-5000-digits", "empty-uri", "no-uri"],
)
async def test_refusals_are_invalid_params(server, params, message):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await read_directory(client, **params)
    assert caught.value.error.code == INVALID_PARAMS
    assert message in str(caught.value)


# ---- a folder serves no content -------------------------------------------------


@pytest.mark.parametrize("folder", [ROOT, REFERENCES])
async def test_reading_a_folder_names_its_index(server, folder):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(folder)
    error = caught.value.error
    assert error.code == INVALID_PARAMS
    assert error.message.startswith(f"Resource not found: {folder!r}. ")
    assert f"{folder}/_index.md" in error.message
    assert error.data == {"uri": folder}


async def test_a_trailing_slash_is_a_plain_miss(server):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(f"{REFERENCES}/")
    assert caught.value.error.code == INVALID_PARAMS
    assert "_index" not in caught.value.error.message


async def test_read_resource_passes_the_hint_on(server):
    async with Client(server.mcp.server) as client:
        result = await client.call_tool(
            "read_resource", {"uri": REFERENCES}, raise_on_error=False
        )
    assert result.is_error
    assert f"no resource at {REFERENCES!r}. " in result.content[0].text
    assert f"{REFERENCES}/_index.md" in result.content[0].text


async def test_show_on_a_folder_is_a_plain_miss_naming_the_index(server):
    from fastmcp.exceptions import ToolError

    async with Client(server.mcp.server) as client:
        with pytest.raises(ToolError) as refused:
            await client.call_tool("show", {"uri": REFERENCES})
    said = str(refused.value)
    assert f"no resource at {REFERENCES!r}. " in said
    assert f"{REFERENCES}/_index.md" in said
    assert "Resource not found" not in said


async def test_the_method_matches_the_uri_as_sent_while_a_read_normalises(server):
    dotted = f"{ROOT}/x/../references"
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await read_directory(client, uri=dotted)
        index = await read_json(client, f"{dotted}/_index.json")
    assert caught.value.error.code == INVALID_PARAMS
    assert [r["uri"] for r in index] == reference_uris()
