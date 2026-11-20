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
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import directories
from kubed.selenium_flow.mcp import skill as skill_module
from kubed.selenium_flow.mcp.skill import EntryOnlySkillProvider
from kubed.selenium_flow.mcp.skill_extension import EXTENSION_ID
from kubed.selenium_flow.server import SeleniumMCP

from .fakes import request

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


# ---- resources/directory/read --------------------------------------------------

METHOD = "resources/directory/read"


async def read_directory(client, **params) -> dict:
    return await request(client, METHOD, **params)


async def test_the_root_lists_its_direct_children(server):
    async with Client(server.mcp) as client:
        result = await read_directory(client, uri=ROOT)
    assert [r["uri"] for r in result["resources"]] == [f"{ROOT}/SKILL.md", REFERENCES]
    assert result["resources"][1]["mimeType"] == DIRECTORY
    assert "nextCursor" not in result


async def test_descending_lists_the_folder_alone(server):
    async with Client(server.mcp) as client:
        result = await read_directory(client, uri=REFERENCES)
    assert [r["uri"] for r in result["resources"]] == reference_uris()


@pytest.mark.parametrize("folder", [ROOT, REFERENCES])
async def test_the_method_returns_what_the_index_holds(server, folder):
    async with Client(server.mcp) as client:
        result = await read_directory(client, uri=folder)
        index = await read_json(client, f"{folder}/_index.json")
    assert result["resources"] == index


async def test_a_file_child_is_its_resources_list_row(server):
    async with Client(server.mcp) as client:
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
        ({"uri": ""}, ""),
        ({}, ""),
    ],
    ids=["missing", "file", "trailing-slash", "root-slash", "other-scheme", "cursor-text",
         "cursor-zero", "cursor-past-end", "empty-uri", "no-uri"],
)
async def test_refusals_are_invalid_params(server, params, message):
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as caught:
            await read_directory(client, **params)
    assert caught.value.error.code == INVALID_PARAMS
    assert message in str(caught.value)


async def test_pages_join_to_the_whole_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(directories.DirectoryRead, "page_size", 2)
    mcp = make_skill(tmp_path, {f"references/R{n}.md": f"# R{n}\n" for n in range(5)})
    pages = []
    async with Client(mcp) as client:
        result = await read_directory(client, uri="skill://tmp-skill/references")
        pages.append(result)
        while "nextCursor" in result:
            result = await read_directory(
                client, uri="skill://tmp-skill/references", cursor=result["nextCursor"]
            )
            pages.append(result)
        index = await read_json(client, "skill://tmp-skill/references/_index.json")
    assert [len(page["resources"]) for page in pages] == [2, 2, 1]
    assert [r for page in pages for r in page["resources"]] == index
    assert len(index) == 5


async def test_an_empty_folder_returns_no_resources(tmp_path):
    mcp = make_skill(tmp_path, {}, folders=("assets",))
    async with Client(mcp) as client:
        result = await read_directory(client, uri="skill://tmp-skill/assets")
    assert result["resources"] == []
    assert "nextCursor" not in result


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_switching_the_skill_off_removes_the_method(mode):
    off = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "t"},
                 mcp={"skill": False})
    )
    async with Client(off.mcp, mode=mode) as client:
        with pytest.raises(MCPError) as caught:
            await read_directory(client, uri=ROOT)
    assert caught.value.error.code == METHOD_NOT_FOUND


async def test_a_nonconforming_skill_keeps_its_indexes_without_the_method(tmp_path):
    folder = tmp_path / "odd-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: another-name\ndescription: Odd.\n---\n\n# Odd\n", encoding="utf-8"
    )
    (folder / "references" / "A.md").write_text("# A\n", encoding="utf-8")
    mcp = FastMCP("t")
    skill_module.register(mcp, EntryOnlySkillProvider(folder, supporting_files="resources"))
    async with Client(mcp) as client:
        assert EXTENSION_ID not in (client.session.server_capabilities.extensions or {})
        index = await read_json(client, "skill://odd-skill/references/_index.json")
        with pytest.raises(MCPError) as caught:
            await read_directory(client, uri="skill://odd-skill")
    assert [r["uri"] for r in index] == ["skill://odd-skill/references/A.md"]
    assert caught.value.error.code == METHOD_NOT_FOUND


# ---- a folder serves no content -------------------------------------------------


@pytest.mark.parametrize("folder", [ROOT, REFERENCES])
async def test_reading_a_folder_names_its_index(server, folder):
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(folder)
    error = caught.value.error
    assert error.code == INVALID_PARAMS
    assert error.message.startswith(f"Resource not found: {folder!r}. ")
    assert f"{folder}/_index.md" in error.message
    assert error.data == {"uri": folder}


async def test_a_trailing_slash_is_a_plain_miss(server):
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(f"{REFERENCES}/")
    assert caught.value.error.code == INVALID_PARAMS
    assert "_index" not in caught.value.error.message


async def test_read_resource_passes_the_hint_on(server):
    async with Client(server.mcp) as client:
        result = await client.call_tool(
            "read_resource", {"uri": REFERENCES}, raise_on_error=False
        )
    assert result.is_error
    assert f"{REFERENCES}/_index.md" in result.content[0].text


async def test_an_empty_folder_still_names_its_index(tmp_path):
    mcp = make_skill(tmp_path, {}, folders=("assets",))
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource("skill://tmp-skill/assets")
    assert "skill://tmp-skill/assets/_index.md" in caught.value.error.message
