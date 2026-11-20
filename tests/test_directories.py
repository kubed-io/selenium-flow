"""The skill's folders as SEP-2640 directory resources, and the index files beside them.

A folder is read two ways: ``resources/directory/read``, which a proxy that
does not know the method refuses, and ``_index.json`` / ``_index.md``, which
plain ``resources/read`` reaches anywhere. Every test goes through a real
client, and the two ways are checked against each other rather than against
the provider's own helpers.
"""

import json

import pytest
from fastmcp import Client, FastMCP

from kubed.selenium_flow.mcp import skill as skill_module
from kubed.selenium_flow.mcp.skill import EntryOnlySkillProvider

pytestmark = pytest.mark.unit

ROOT = "skill://selenium-flow"
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


def make_skill(tmp_path, files: dict[str, str], folders: tuple[str, ...] = ()) -> FastMCP:
    """A server over a throwaway skill called ``tmp-skill``."""
    folder = tmp_path / "tmp-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: tmp-skill\ndescription: A skill for tests.\n---\n\n# Tmp\n",
        encoding="utf-8",
    )
    for path, body in files.items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(body, encoding="utf-8")
    for path in folders:
        (folder / path).mkdir(parents=True)
    mcp = FastMCP("t")
    skill_module.register(mcp, EntryOnlySkillProvider(folder, supporting_files="resources"))
    return mcp


# ---- the index files ---------------------------------------------------------


async def test_index_json_reads_beside_each_folder(server):
    async with Client(server.mcp) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
        references = await read_json(client, f"{REFERENCES}/_index.json")
    assert [r["uri"] for r in root] == [f"{ROOT}/SKILL.md", REFERENCES]
    assert [r["uri"] for r in references] == reference_uris()


@pytest.mark.parametrize("folder", [ROOT, REFERENCES])
async def test_index_md_is_one_line_per_child(server, folder):
    async with Client(server.mcp) as client:
        children = await read_json(client, f"{folder}/_index.json")
        contents = await client.read_resource(f"{folder}/_index.md")
    assert contents[0].mime_type == "text/markdown"
    assert contents[0].text.splitlines() == [
        f"{c['uri']}: {c['description']}" for c in children
    ]


async def test_a_folder_child_is_a_directory_resource(server):
    async with Client(server.mcp) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
    folder = next(r for r in root if r["uri"] == REFERENCES)
    assert folder["mimeType"] == DIRECTORY
    assert folder["name"] == "selenium-flow/references"
    assert "selenium-flow" in folder["description"]


async def test_no_folder_uri_ends_in_a_slash(server):
    async with Client(server.mcp) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
    folders = [r["uri"] for r in root if r["mimeType"] == DIRECTORY]
    assert folders
    assert not [uri for uri in folders if uri.endswith("/")]


async def test_a_markdown_child_is_described_by_its_heading(server):
    async with Client(server.mcp) as client:
        references = await read_json(client, f"{REFERENCES}/_index.json")
    flows = next(r for r in references if r["uri"].endswith("/FLOWS.md"))
    assert flows["description"] == "Flows"
    assert flows["mimeType"] == "text/markdown"


async def test_skill_md_is_described_as_the_listing_describes_it(server):
    async with Client(server.mcp) as client:
        listed = next(
            r for r in await client.list_resources() if str(r.uri) == f"{ROOT}/SKILL.md"
        )
        root = await read_json(client, f"{ROOT}/_index.json")
    assert root[0]["description"] == listed.description


async def test_folders_and_indexes_are_unlisted(server):
    async with Client(server.mcp) as client:
        rows = await client.list_resources()
        templates = await client.list_resource_templates()
    uris = [str(r.uri) for r in rows]
    assert not [r for r in rows if r.mime_type == DIRECTORY]
    assert not [u for u in uris if "/_index." in u or u in (ROOT, REFERENCES)]
    assert not [t for t in templates if t.uri_template.startswith("skill://")]


async def test_the_manifest_is_never_a_child_and_still_reads(server):
    async with Client(server.mcp) as client:
        root = await read_json(client, f"{ROOT}/_index.json")
        manifest = await read_json(client, f"{ROOT}/_manifest")
    assert not [r for r in root if "_manifest" in r["uri"] or "_index" in r["uri"]]
    assert "references/FLOWS.md" in [f["path"] for f in manifest["files"]]


async def test_an_index_of_no_folder_is_not_found(server):
    from mcp.shared.exceptions import MCPError

    async with Client(server.mcp) as client:
        with pytest.raises(MCPError):
            await client.read_resource(f"{ROOT}/nope/_index.json")


async def test_an_empty_folder_has_an_empty_index(tmp_path):
    mcp = make_skill(tmp_path, {}, folders=("assets",))
    async with Client(mcp) as client:
        root = await read_json(client, "skill://tmp-skill/_index.json")
        assets = await read_json(client, "skill://tmp-skill/assets/_index.json")
        markdown = await client.read_resource("skill://tmp-skill/assets/_index.md")
    assert [r["uri"] for r in root] == ["skill://tmp-skill/SKILL.md", "skill://tmp-skill/assets"]
    assert assets == []
    assert markdown[0].text == ""


async def test_a_nested_folder_is_listed_once_by_its_parent(tmp_path):
    mcp = make_skill(tmp_path, {"a/b/c.txt": "c", "a/d.md": "# Dee\n"})
    async with Client(mcp) as client:
        root = await read_json(client, "skill://tmp-skill/_index.json")
        a = await read_json(client, "skill://tmp-skill/a/_index.json")
        b = await read_json(client, "skill://tmp-skill/a/b/_index.json")
    assert [r["uri"] for r in root] == ["skill://tmp-skill/SKILL.md", "skill://tmp-skill/a"]
    assert [(r["uri"], r["mimeType"]) for r in a] == [
        ("skill://tmp-skill/a/b", DIRECTORY),
        ("skill://tmp-skill/a/d.md", "text/markdown"),
    ]
    assert a[1]["description"] == "Dee"
    assert [r["uri"] for r in b] == ["skill://tmp-skill/a/b/c.txt"]
