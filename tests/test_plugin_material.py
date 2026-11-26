"""The plugin's prompts and agent, served by kubed-mcp as unlisted resources.

kubed-mcp serves the files of a plugin as `prompts://` and `agents://`
resources; selenium-flow proves that for its own plugin: the bytes a client
reads are the plugin's bytes, with secrets on too, because the text is the
server's own and the scrub middleware exempts both schemes.
"""

import json
from pathlib import Path

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from kubed.selenium_flow.mcp.skill import plugin_path
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN, over_http
from .fakes import request, untabbed

pytestmark = pytest.mark.unit

NAMESPACE = "selenium-flow"
PROMPTS = f"prompts://{NAMESPACE}"
AGENTS = f"agents://{NAMESPACE}"
VALUE = "selenium-flow"


def plugin_files() -> dict[str, Path]:
    """Each served URI, mapped to the plugin file it is served from."""
    root = plugin_path()
    return {
        **{f"{PROMPTS}/{p.name}": p for p in sorted((root / "commands").glob("*.md"))},
        **{f"{AGENTS}/{p.name}": p for p in sorted((root / "agents").glob("*.md"))},
    }


async def read_json(client, uri: str):
    contents = await client.read_resource(uri)
    assert contents[0].mime_type == "application/json"
    return json.loads(contents[0].text)


@pytest.fixture
def secret_server(tmp_path):
    """A server with secrets on, one of whose values is the name the plugin's text bears."""
    secret = tmp_path / "secrets" / "a"
    secret.mkdir(parents=True)
    (secret / "password").write_text(VALUE)
    (secret / "_allowed_urls").write_text("https://a.example.com\n")
    made = untabbed(SeleniumMCP({
        "grid": {"url": "http://grid.invalid:4444"}, "auth": {"token": TOKEN},
        "secrets": {"dirs": str(tmp_path / "secrets")},
    }))
    yield made
    made.workspaces.scrubber.close()


async def test_each_file_reads_as_the_plugins_bytes_with_secrets_on(secret_server):
    assert secret_server.workspaces.scrubber.active
    files = plugin_files()
    assert sorted(files) == [
        f"{AGENTS}/explorer.md", f"{PROMPTS}/build_flow.md", f"{PROMPTS}/repair_flow.md",
    ]
    async with Client(secret_server.mcp.server) as client:
        for uri, path in files.items():
            contents = await client.read_resource(uri)
            assert contents[0].mime_type == "text/markdown", uri
            assert contents[0].text.encode("utf-8") == path.read_bytes(), uri
        for folder in (PROMPTS, AGENTS):
            for kind in ("json", "md"):
                text = (await client.read_resource(f"{folder}/_index.{kind}"))[0].text
                assert VALUE in text and "[secret:" not in text, (folder, kind)


FOLDERS = [
    (PROMPTS, [f"{PROMPTS}/build_flow.md", f"{PROMPTS}/repair_flow.md"]),
    (AGENTS, [f"{AGENTS}/explorer.md"]),
]


@pytest.mark.parametrize(("folder", "uris"), FOLDERS)
async def test_each_index_json_lists_the_folders_files(server, folder, uris):
    async with Client(server.mcp.server) as client:
        index = await read_json(client, f"{folder}/_index.json")
    assert [r["uri"] for r in index] == uris
    assert {r["mimeType"] for r in index} == {"text/markdown"}


@pytest.mark.parametrize("folder", [PROMPTS, AGENTS])
async def test_index_md_is_one_line_per_child(server, folder):
    async with Client(server.mcp.server) as client:
        children = await read_json(client, f"{folder}/_index.json")
        contents = await client.read_resource(f"{folder}/_index.md")
    assert contents[0].mime_type == "text/markdown"
    assert contents[0].text.splitlines() == [
        f"{c['uri']}: {' '.join(c.get('description', '').split())}" for c in children
    ]


@pytest.mark.parametrize("folder", [PROMPTS, AGENTS])
async def test_the_method_returns_what_the_index_holds(server, folder):
    async with Client(server.mcp.server) as client:
        result = await request(client, "resources/directory/read", uri=folder)
        index = await read_json(client, f"{folder}/_index.json")
    assert result["resources"] == index
    assert "nextCursor" not in result


async def test_a_prompt_row_carries_its_prompt(server):
    async with Client(server.mcp.server) as client:
        index = await read_json(client, f"{PROMPTS}/_index.json")
        listed = {p["name"]: p for p in (await request(client, "prompts/list"))["prompts"]}
    metas = [row["_meta"]["io.kubed/prompt"] for row in index]
    assert [m["name"] for m in metas] == ["build_flow", "repair_flow"]
    for meta in metas:
        assert meta["arguments"] == listed[meta["name"]].get("arguments", [])
        assert "disable-model-invocation" not in meta and "user-invocable" not in meta


async def test_the_agent_row_carries_its_agent(server):
    async with Client(server.mcp.server) as client:
        (row,) = await read_json(client, f"{AGENTS}/_index.json")
        got = await request(client, "agents/get", name="explorer")
    agent = {**got["_meta"]["io.kubed/agent"], "name": "explorer"}
    assert row["_meta"] == {"io.kubed/agent": agent}


async def test_neither_scheme_is_listed(server):
    async with Client(server.mcp.server) as client:
        rows = await request(client, "resources/list")
        templates = await request(client, "resources/templates/list")
    named = json.dumps([rows, templates])
    assert "prompts://" not in named and "agents://" not in named


async def test_a_client_reading_no_resources_reads_them_through_the_tool(server):
    async with over_http(server, {"X-MCP-Resources": "off"}) as client:
        listed = await client.call_tool("list_resources", {})
        assert "prompts://" not in listed.content[0].text
        assert "agents://" not in listed.content[0].text
        for uri, path in plugin_files().items():
            read = await client.call_tool("read_resource", {"uri": uri})
            assert not read.is_error, uri
            assert path.read_text(encoding="utf-8") in read.content[0].text, uri


@pytest.mark.parametrize("folder", [PROMPTS, AGENTS])
async def test_a_folder_is_no_file(server, folder):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(folder)
    assert caught.value.error.code == INVALID_PARAMS


@pytest.mark.parametrize("uri", [f"{PROMPTS}%2Fbuild_flow.md", f"{PROMPTS}%2fbuild_flow.md"])
async def test_an_encoded_slash_reads_nothing(server, uri):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError) as caught:
            await client.read_resource(uri)
    assert caught.value.error.code == INVALID_PARAMS
