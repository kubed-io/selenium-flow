"""What an agent and an HTTP caller can do with a session's files (§F4.6, §F4.7)."""

import asyncio

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit
S = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}", "X-Session-Key": S}


@pytest.fixture
def srv(tmp_path):
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN, flow_data_dir=str(tmp_path))


@pytest.fixture
def http(srv):
    return TestClient(srv.mcp.http_app(), headers=AUTH)


def _run(coro):
    return asyncio.run(coro)


def test_the_resources_are_a_folder_and_two_sub_folders(srv):
    async def go():
        async with Client(srv.mcp) as c:
            listed = {str(r.uri) for r in await c.list_resources()}
            templated = {t.uri_template for t in await c.list_resource_templates()}
            return listed, templated
    listed, templated = _run(go())
    assert {"session://files", "session://files/screenshots", "session://files/downloads"} <= listed
    assert {
        "session://files/{name}",
        "session://files/screenshots/{name}",
        "session://files/downloads/{name}",
    } <= templated


def test_keep_file_takes_a_uri_and_is_not_idempotent(srv):
    async def go():
        async with Client(srv.mcp) as c:
            return {t.name: t for t in await c.list_tools()}["keep_file"]
    tool = _run(go())
    assert list(tool.input_schema["properties"]) == ["uri"]
    assert tool.input_schema["required"] == ["uri"]
    assert tool.annotations.idempotent_hint is False
    # A download keep REPLACES a same-named file in Files (§F4.7) — honest
    # annotations means this one says so rather than leaning on MCP's own
    # default, which happens to be True for the wrong reason.
    assert tool.annotations.destructive_hint is True


def test_the_rest_tree_mirrors_the_folders(http, srv, tmp_path):
    srv.flows.create_file(S, "shot.png", b"p", flows.SCREENSHOTS_DIR)
    got = http.get("/files/screenshots").json()
    assert got["folder"] == "screenshots"
    assert [f["uri"] for f in got["files"]] == ["session://files/screenshots/shot.png"]
    assert http.get("/files").json()["folders"][0]["uri"] == "session://files/screenshots"


def test_keeping_over_http_moves_a_screenshot(http, srv):
    srv.flows.create_file(S, "shot.png", b"p", flows.SCREENSHOTS_DIR)
    got = http.put("/files/screenshots/shot.png/kept")
    assert got.status_code == 200, got.text
    assert got.json()["uri"] == "session://files/shot.png"
    assert srv.flows.files(S, flows.SCREENSHOTS_DIR) == []


@pytest.mark.parametrize("folder", ["files", "flows", "kept"])
def test_keeping_over_http_names_only_the_two_keepable_folders(http, folder):
    assert http.put(f"/files/{folder}/x.png/kept").status_code == 400


def test_reading_a_screenshot_resource_returns_its_bytes(srv):
    from kubed.selenium_flow.session.sessions import STDIO_NAME
    srv.flows.create_file(STDIO_NAME, "shot.png", b"\x89PNG", flows.SCREENSHOTS_DIR)

    async def go():
        async with Client(srv.mcp) as c:  # stdio-like: the session is `stdio`
            return await c.read_resource("session://files/screenshots/shot.png")
    got = _run(go())
    assert got[0].blob or got[0].text
