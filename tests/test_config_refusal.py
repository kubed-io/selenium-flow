"""What an agent sees of its workspace's config, and the refusal a broken one is.

`workspace://current` carries the config's rows and the link to the admin UI's
view of it. A file that does not parse or validate refuses every MCP call and
every Token-door request that names its workspace, at the edge, before anything
the call would do, until an admin fixes it.
"""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.names import CONFIG_FILE
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import NAMED, OTHER, TOKEN, calling_as

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}
BROKEN = "colour: blue\n"
REFUSED = "this workspace's config is invalid"


def built(tmp_path, **settings) -> SeleniumMCP:
    return SeleniumMCP(
        Settings(
            grid={"url": "http://grid.invalid:4444"},
            auth={"token": TOKEN},
            data={"dir": str(tmp_path)},
            **settings,
        )
    )


def put_file(tmp_path, text, name=NAMED):
    folder = tmp_path / "workspaces" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CONFIG_FILE).write_text(text)
    return folder / CONFIG_FILE


async def current(server, uri="workspace://current"):
    async with Client(server.mcp.server) as client:
        return json.loads((await client.read_resource(uri))[0].text)


# ---- the context -----------------------------------------------------------------


async def test_the_context_carries_every_setting_with_its_source(tmp_path, monkeypatch):
    server = built(tmp_path)
    calling_as(monkeypatch, NAMED)
    assert (await current(server))["config"] == {
        "flows.global": {"value": None, "source": "default"}
    }
    put_file(tmp_path, "flows:\n  global: [shop]\n")
    assert (await current(server))["config"] == {
        "flows.global": {"value": ["shop"], "source": "workspace"}
    }


async def test_the_context_links_the_config_tab_when_the_server_knows_its_url(
    tmp_path, monkeypatch
):
    server = built(tmp_path, public_base_url="https://flows.example.com/", route_prefix="/sf")
    calling_as(monkeypatch, NAMED)
    assert (await current(server))["config_url"] == (
        f"https://flows.example.com/sf/#/workspaces/{NAMED}/config"
    )


async def test_the_config_is_a_part_of_the_context(tmp_path, monkeypatch):
    server = built(tmp_path, public_base_url="https://flows.example.com")
    calling_as(monkeypatch, NAMED)
    part = await current(server, "workspace://current/config")
    assert part == {
        "workspace": NAMED,
        "config": {"flows.global": {"value": None, "source": "default"}},
    }
    link = await current(server, "workspace://current/config_url")
    assert link["config_url"].endswith(f"/#/workspaces/{NAMED}/config")


async def test_a_refused_config_refuses_the_parts_too(broken):
    with pytest.raises(MCPError, match=REFUSED):
        await current(broken, "workspace://current/config")


async def test_without_a_public_url_there_is_no_link(tmp_path, monkeypatch):
    server = built(tmp_path)
    calling_as(monkeypatch, NAMED)
    assert "config_url" not in await current(server)


async def test_without_a_data_dir_there_is_no_config(server, monkeypatch):
    calling_as(monkeypatch, NAMED)
    context = await current(server)
    assert "config" not in context and "config_url" not in context


def test_the_rest_route_carries_it_too(tmp_path):
    server = built(tmp_path)
    put_file(tmp_path, "flows:\n  global: [shop]\n")
    body = TestClient(server.mcp.server.http_app(), headers=AUTH).get(
        "/browser", params={"workspace": NAMED}
    ).json()
    assert body["config"]["flows.global"]["source"] == "workspace"


# ---- a refused file refuses every MCP call that names the workspace ---------------


@pytest.fixture
def broken(tmp_path, monkeypatch):
    server = built(tmp_path, public_base_url="https://flows.example.com")
    put_file(tmp_path, BROKEN)
    calling_as(monkeypatch, NAMED)
    return server


async def test_every_tool_is_refused_before_it_runs(broken):
    async with Client(broken.mcp.server) as client:
        tools = await client.list_tools()
        assert tools, "listing is not a call: a client still sees the tools"
        for tool in tools:
            with pytest.raises(ToolError) as refused:
                await client.call_tool(tool.name, {})
            said = str(refused.value)
            assert REFUSED in said, tool.name
            assert "`colour`" in said
            assert said.endswith(
                f"An admin fixes it in the admin UI: "
                f"https://flows.example.com/#/workspaces/{NAMED}/config"
            ), tool.name
    assert broken.workspaces.store.get(NAMED) is None, "nothing ran"


async def test_every_resource_is_refused_the_context_included(broken):
    async with Client(broken.mcp.server) as client:
        uris = [str(r.uri) for r in await client.list_resources()]
        uris += ["workspace://current", "flow://flows/login", "workspace://files/a.txt"]
        for uri in uris:
            if uri.startswith("skill://"):
                continue
            with pytest.raises(MCPError) as refused:
                await client.read_resource(uri)
            assert REFUSED in str(refused.value), uri


async def test_a_refused_workspace_is_refused_once_through_read_resource(broken):
    """The call is refused before kubed-mcp's tool reads through the middleware, so
    the sentence is said once, not again by the read inside it (Review Focus 2)."""
    async with Client(broken.mcp.server) as client:
        with pytest.raises(ToolError) as refused:
            await client.call_tool("read_resource", {"uri": "workspace://current"})
    assert str(refused.value).count(REFUSED) == 1
    assert broken.workspaces.store.get(NAMED) is None, "nothing ran"


async def test_the_skill_is_read_while_refused(broken):
    """Static, shipped in the image, and how the agent learns what to do now."""
    async with Client(broken.mcp.server) as client:
        uris = [str(r.uri) for r in await client.list_resources()]
        skills = [uri for uri in uris if uri.startswith("skill://")]
        assert "skill://selenium-flow/driving-a-browser/SKILL.md" in skills
        for uri in skills:
            assert (await client.read_resource(uri))[0], uri


async def test_a_prompt_is_refused_too(broken):
    async with Client(broken.mcp.server) as client:
        prompts = await client.list_prompts()
        for prompt in prompts:
            with pytest.raises(MCPError, match=REFUSED):
                await client.get_prompt(prompt.name, {"flow": "login"})


async def test_another_workspace_is_not_refused(broken, monkeypatch):
    calling_as(monkeypatch, OTHER)
    assert (await current(broken))["workspace"] == OTHER


async def test_a_caller_that_names_no_workspace_reads_the_shared_library(
    broken, monkeypatch
):
    calling_as(monkeypatch, None)
    async with Client(broken.mcp.server) as client:
        listed = json.loads((await client.read_resource("flow://flows"))[0].text)
    assert "flows" in listed


async def test_fixed_on_disk_it_applies_from_the_next_call(broken, tmp_path):
    with pytest.raises(MCPError):
        await current(broken)
    put_file(tmp_path, "flows:\n  global: [shop]\n")
    assert (await current(broken))["config"]["flows.global"]["value"] == ["shop"]


async def test_a_stdio_caller_is_refused_by_its_own_file(tmp_path, monkeypatch):
    from kubed.selenium_flow.mcp import clients

    server = built(tmp_path)
    put_file(tmp_path, BROKEN, name="stdio")
    monkeypatch.setattr(clients, "request_values", lambda: None)
    with pytest.raises(MCPError, match=REFUSED):
        await current(server)


# ---- and every Token-door request that names it ------------------------------------


def _token_door_routes(server):
    """Every route behind the Token door: not the admin's, not a signed or open
    one. The path parameters are filled with a plain name."""
    skipped = ("/admin", "/files/", "/kept/", "/screenshots/", "/recordings/")
    open_paths = {
        "/health", "/started", "/ready", "/info", "/openapi.yaml", "/openapi.json",
        "/", "/live.js", "/admin",
    }
    for route in server.mcp.server._additional_http_routes:
        path = route.path
        if path in open_paths or path.startswith(skipped) or path == "/mcp":
            continue
        for method in sorted(route.methods - {"HEAD"}):
            filled = path
            for param in route.param_convertors:
                filled = filled.replace(f"{{{param}}}", "x.txt")
                filled = filled.replace(f"{{{param}:path}}", "x.txt")
            yield method, filled


def test_every_token_door_route_refuses_the_workspace(broken):
    client = TestClient(broken.mcp.server.http_app(), headers=AUTH)
    routes = list(_token_door_routes(broken))
    assert ("GET", "/browser") in routes and ("GET", "/secrets") in routes
    for method, path in routes:
        response = client.request(method, path, params={"workspace": NAMED}, json={})
        assert response.status_code == 409, (method, path, response.text)
        assert REFUSED in response.json()["error"], (method, path)
    assert broken.workspaces.store.get(NAMED) is None, "nothing ran"


def test_the_token_door_still_answers_first(broken):
    client = TestClient(broken.mcp.server.http_app())
    assert client.get("/browser", params={"workspace": NAMED}).status_code == 401


def test_a_token_door_request_for_another_workspace_is_not_refused(broken):
    client = TestClient(broken.mcp.server.http_app(), headers=AUTH)
    assert client.get("/browser", params={"workspace": OTHER}).status_code == 200
    assert client.get("/flows").status_code == 200
