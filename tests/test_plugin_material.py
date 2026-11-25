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

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp.skill import plugin_path
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN
from .fakes import untabbed

pytestmark = pytest.mark.unit

NAMESPACE = "selenium-flow"
PROMPTS = f"prompts://{NAMESPACE}"
AGENTS = f"agents://{NAMESPACE}"
DIRECTORY = "inode/directory"
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
    made = untabbed(SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN},
        secrets={"dirs": str(tmp_path / "secrets")},
    )))
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
