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
    return serve(folder)


def serve(folder) -> FastMCP:
    """A server over the skill in ``folder``, loaded as the packaged one is."""
    mcp = FastMCP("t")
    skill_module.register(mcp, skill_module.load(folder))
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
        with pytest.raises(MCPError) as caught:
            await client.read_resource(f"{ROOT}/nope/_index.json")
    assert caught.value.error.code == INVALID_PARAMS
    assert caught.value.error.message.startswith("Resource not found")


@pytest.mark.parametrize("name", ["_index.md", "_index.json"])
async def test_a_file_with_an_index_name_never_breaks_its_folder(tmp_path, caplog, name):
    """The index wins: the file is no child, reads as the index, and is named at load."""
    with caplog.at_level("WARNING"):
        mcp = make_skill(tmp_path, {f"sub/{name}": "a file", "sub/a.txt": "a"})
    assert f"sub/{name}" in caplog.text
    async with Client(mcp) as client:
        listed = await read_directory(client, uri="skill://tmp-skill/sub")
        index = await read_json(client, "skill://tmp-skill/sub/_index.json")
        markdown = (await client.read_resource("skill://tmp-skill/sub/_index.md"))[0].text
        entry = (await request(client, "skills/list"))["skills"][0]
    assert [r["uri"] for r in listed["resources"]] == ["skill://tmp-skill/sub/a.txt"]
    assert listed["resources"] == index
    assert markdown == "skill://tmp-skill/sub/a.txt: File from tmp-skill skill\n"
    assert f"skill://tmp-skill/sub/{name}" not in [r["uri"] for r in entry["resources"]]


@pytest.mark.parametrize("name", ["_index.md", "_index.json"])
async def test_a_file_with_an_index_name_is_not_in_the_manifest(tmp_path, name):
    """read_skill verifies every file the manifest names, and this URI reads
    as the index, so the manifest must not name it."""
    mcp = make_skill(tmp_path, {f"sub/{name}": "a file", "sub/a.txt": "a"})
    async with Client(mcp) as client:
        manifest = await read_json(client, "skill://tmp-skill/_manifest")
        read = await client.read_skill("skill://tmp-skill/SKILL.md")
    paths = [f["path"] for f in manifest["files"]]
    assert f"sub/{name}" not in paths
    assert "sub/a.txt" in paths
    assert set(read.files) == set(paths)


@pytest.mark.parametrize("name", ["_index.md", "_index.json"])
async def test_a_folder_with_an_index_name_never_breaks_its_parent(tmp_path, caplog, name):
    with caplog.at_level("WARNING"):
        mcp = make_skill(
            tmp_path, {f"sub/{name}/inner/x.txt": "x", f"sub/{name}/y.txt": "y", "sub/a.txt": "a"}
        )
    assert f"sub/{name}" in caplog.text
    async with Client(mcp) as client:
        listed = await read_directory(client, uri="skill://tmp-skill/sub")
        index = (await client.read_resource(f"skill://tmp-skill/sub/{name}"))[0]
        with pytest.raises(MCPError) as caught:
            await read_directory(client, uri=f"skill://tmp-skill/sub/{name}")
        inner = (await client.read_resource(f"skill://tmp-skill/sub/{name}/y.txt"))[0]
        manifest = await read_json(client, "skill://tmp-skill/_manifest")
        entry = (await request(client, "skills/list"))["skills"][0]
    assert [r["uri"] for r in listed["resources"]] == ["skill://tmp-skill/sub/a.txt"]
    assert f"sub/{name}/y.txt" in [f["path"] for f in manifest["files"]]
    assert f"skill://tmp-skill/sub/{name}/y.txt" in [r["uri"] for r in entry["resources"]]
    assert index.mime_type in ("application/json", "text/markdown")
    assert "skill://tmp-skill/sub/a.txt" in index.text
    assert caught.value.error.code == INVALID_PARAMS
    assert inner.text == "y"


async def test_a_multiline_description_is_one_index_line(tmp_path):
    folder = tmp_path / "tmp-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: tmp-skill\ndescription: |\n  Line one.\n  Line two.\n---\n\n# Tmp\n",
        encoding="utf-8",
    )
    mcp = serve(folder)
    async with Client(mcp) as client:
        children = await read_json(client, "skill://tmp-skill/_index.json")
        markdown = (await client.read_resource("skill://tmp-skill/_index.md"))[0].text
    assert "\n" in children[0]["description"]
    assert markdown == "skill://tmp-skill/SKILL.md: Line one. Line two.\n"


async def test_a_file_child_reads_with_its_own_type(tmp_path):
    mcp = make_skill(tmp_path, {"references/A.md": "# A\n"})
    async with Client(mcp) as client:
        listed = await read_directory(client, uri="skill://tmp-skill/references")
        read = (await client.read_resource("skill://tmp-skill/references/A.md"))[0]
    assert [r["uri"] for r in listed["resources"]] == ["skill://tmp-skill/references/A.md"]
    assert read.mime_type == "text/markdown"


async def test_a_file_that_is_not_markdown_is_a_file_from_the_skill(tmp_path):
    mcp = make_skill(tmp_path, {"scripts/run.sh": "# not a heading\necho hi\n"})
    async with Client(mcp) as client:
        scripts = await read_json(client, "skill://tmp-skill/scripts/_index.json")
        markdown = (await client.read_resource("skill://tmp-skill/scripts/_index.md"))[0]
    assert scripts[0]["description"] == "File from tmp-skill skill"
    assert markdown.text == "skill://tmp-skill/scripts/run.sh: File from tmp-skill skill\n"


async def test_a_markdown_file_with_no_heading_is_a_file_from_the_skill(tmp_path):
    mcp = make_skill(tmp_path, {"references/A.md": "no heading here\n"})
    async with Client(mcp) as client:
        references = await read_json(client, "skill://tmp-skill/references/_index.json")
    assert references[0]["description"] == "File from tmp-skill skill"


async def test_a_heading_inside_a_code_fence_is_not_the_title(tmp_path):
    body = "```bash\n# install it\n```\n\n~~~\n# nor this\n~~~\n\n# Real Title\n"
    mcp = make_skill(tmp_path, {"references/A.md": body})
    async with Client(mcp) as client:
        references = await read_json(client, "skill://tmp-skill/references/_index.json")
    assert references[0]["description"] == "Real Title"


async def test_a_markdown_file_that_is_not_utf8_never_loses_the_skill(tmp_path):
    folder = tmp_path / "tmp-skill"
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: tmp-skill\ndescription: A skill for tests.\n---\n\n# Tmp\n",
        encoding="utf-8",
    )
    (folder / "references" / "BAD.md").write_bytes(b"# Caf\xe9\n")
    mcp = serve(folder)
    async with Client(mcp) as client:
        assert EXTENSION_ID in client.session.server_capabilities.extensions
        references = await read_json(client, "skill://tmp-skill/references/_index.json")
        assert (await client.read_resource("skill://tmp-skill/SKILL.md"))[0].text
        entry = (await request(client, "skills/list"))["skills"][0]
        read = await client.read_skill("skill://tmp-skill/SKILL.md")
    assert references[0]["description"] == "File from tmp-skill skill"
    assert "skill://tmp-skill/references/BAD.md" in [r["uri"] for r in entry["resources"]]
    assert read.files["references/BAD.md"] == b"# Caf\xe9\n"


async def test_an_empty_directory_is_no_folder(tmp_path):
    """Folders come from the skill's files, so a directory with none is none (PLG-64)."""
    mcp = make_skill(tmp_path, {"a/b/c.txt": "c"}, folders=("assets", "a/empty"))
    async with Client(mcp) as client:
        root = await read_json(client, "skill://tmp-skill/_index.json")
        a = await read_json(client, "skill://tmp-skill/a/_index.json")
        misses = []
        for uri in ("skill://tmp-skill/assets", "skill://tmp-skill/a/empty"):
            with pytest.raises(MCPError) as index:
                await client.read_resource(f"{uri}/_index.json")
            with pytest.raises(MCPError) as method:
                await read_directory(client, uri=uri)
            with pytest.raises(MCPError) as read:
                await client.read_resource(uri)
            misses.append((index.value.error, method.value.error, read.value.error))
    assert [r["uri"] for r in root] == ["skill://tmp-skill/SKILL.md", "skill://tmp-skill/a"]
    assert [r["uri"] for r in a] == ["skill://tmp-skill/a/b"]
    for index, method, read in misses:
        assert index.message.startswith("Resource not found")
        assert (method.code, method.message) == (INVALID_PARAMS, "Directory not found")
        assert "_index" not in read.message


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
        ({"uri": ROOT, "cursor": "1" * 5000}, "Invalid directory cursor"),
        ({"uri": ""}, ""),
        ({}, ""),
    ],
    ids=["missing", "file", "trailing-slash", "root-slash", "other-scheme", "cursor-text",
         "cursor-zero", "cursor-past-end", "cursor-5000-digits", "empty-uri", "no-uri"],
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
    assert f"no resource at {REFERENCES!r}. " in result.content[0].text
    assert f"{REFERENCES}/_index.md" in result.content[0].text


async def test_show_on_a_folder_is_a_plain_miss_naming_the_index(server):
    from fastmcp.exceptions import ToolError

    async with Client(server.mcp) as client:
        with pytest.raises(ToolError) as refused:
            await client.call_tool("show", {"uri": REFERENCES})
    said = str(refused.value)
    assert f"no resource at {REFERENCES!r}. " in said
    assert f"{REFERENCES}/_index.md" in said
    assert "Resource not found" not in said


async def test_the_method_matches_the_uri_as_sent_while_a_read_normalises(server):
    dotted = f"{ROOT}/x/../references"
    async with Client(server.mcp) as client:
        with pytest.raises(MCPError) as caught:
            await read_directory(client, uri=dotted)
        index = await read_json(client, f"{dotted}/_index.json")
    assert caught.value.error.code == INVALID_PARAMS
    assert [r["uri"] for r in index] == reference_uris()
