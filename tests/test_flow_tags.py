"""Tags on a flow, through the surfaces that save, list and read them."""

from urllib.parse import quote

import pytest
import yaml
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.flows import tags
from kubed.selenium_flow.flows.shape import Shape
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import NAMED, TOKEN, calling_as

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}
GOOD = [{"tool": "navigate", "args": {"url": "https://example.test/"}}]


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    built = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path)},
    ))
    calling_as(monkeypatch, NAMED)
    return built


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


async def call(server, tool_name, /, **kwargs):
    tool = await server.mcp.get_tool(tool_name)
    result = tool.fn(**kwargs)
    if hasattr(result, "__await__"):
        result = await result
    return result


async def resource(server, uri: str):
    import json

    result = await server.mcp.read_resource(uri)
    return json.loads(result.contents[0].content)


def admin(name: str = "", suffix: str = "", key: str = NAMED) -> str:
    base = f"/admin/workspaces/{quote(key, safe='')}/flows"
    return f"{base}/{quote(name, safe='')}{suffix}" if name else base


# ---- saving -----------------------------------------------------------------


async def test_save_flow_writes_the_tags_as_read_after_the_description(server):
    await call(
        server, flowapi.SAVE_TOOL, name="login", steps=GOOD, description="d",
        tags=[" Smoke", "auth:LDAP", "smoke"], sites=["example.com"],
    )
    text = server.flows.read_text(NAMED, "login")
    stored = yaml.safe_load(text)
    assert stored["tags"] == ["smoke", "auth:ldap"]
    assert list(stored)[:4] == ["description", "tags", "sites", "steps"]


async def test_save_flow_refuses_a_bad_tag_naming_it_and_the_rule(server):
    with pytest.raises(ValueError) as caught:
        await call(
            server, flowapi.SAVE_TOOL, name="login", steps=GOOD,
            tags=["fine", "not fine"],
        )
    assert "'not fine'" in str(caught.value)
    assert tags.RULE in str(caught.value)
    assert server.flows.get(NAMED, "login") is None


async def test_save_flow_takes_no_tags_as_none(server):
    await call(server, flowapi.SAVE_TOOL, name="login", steps=GOOD, tags=None)
    assert "tags" not in server.flows.get(NAMED, "login")


def test_an_http_save_takes_tags_from_the_body(client, server):
    response = client.put(
        "/flows/login",
        headers={**AUTH, "Sf-Workspace": NAMED},
        json={"tags": ["Smoke"], "steps": GOOD},
    )
    assert response.status_code == 200, response.text
    assert server.flows.get(NAMED, "login")["tags"] == ["smoke"]


def test_the_admin_save_keeps_its_text_and_validates_the_tags(client, server):
    text = "name: login\ndescription: d\ntags: [Smoke, auth:ldap]  # mine\nsteps:\n" \
        "- tool: navigate\n  args: {url: 'https://example.test/'}\n"
    assert client.put(admin("login"), json={"yaml": text}, headers=AUTH).status_code == 200
    assert server.flows.read_text(NAMED, "login") == text

    bad = text.replace("auth:ldap", "auth ldap")
    response = client.put(admin("login"), json={"yaml": bad}, headers=AUTH)
    assert response.status_code == 400
    assert "'auth ldap'" in response.json()["error"]
    assert server.flows.read_text(NAMED, "login") == text


async def test_the_schema_describes_tags_and_points_at_the_tags_in_use(server):
    schema = await resource(server, flowapi.SCHEMA_URI)
    described = schema["properties"]["tags"]
    assert described["type"] == "array"
    assert described["maxItems"] == tags.MAX_TAGS
    assert tags.RULE[1:] in described["description"]
    assert "workspace://current/tags" in described["description"]
    assert "reuse" in described["description"]
    assert list(schema["properties"]).index("tags") == 2  # after name, description


def test_a_shape_reads_tags_without_trusting_them():
    assert Shape({"tags": ["A", "b"]}).tags == ["a", "b"]
    assert Shape({"tags": ["a b"]}).tags == []
    assert Shape({"tags": "a"}).tags == []
    assert Shape({}).tags == []
    assert Shape(None).tags == []
