"""Tags on a flow, through the surfaces that save, list and read them."""

from urllib.parse import quote

import pytest
import yaml
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.flows import api as flowapi
from kubed.selenium_flow.flows import tags
from kubed.selenium_flow.flows.shape import Shape
from kubed.selenium_flow.names import GLOBAL_WORKSPACE
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


# ---- the listing and the tags in use ---------------------------------------

STEP = {"tool": "navigate", "args": {"url": "https://example.test/"}}
HERE = "https://example.test/login"


def flow(*carried, **more) -> dict:
    document = {"description": "d", "steps": [STEP], **more}
    if carried:
        document["tags"] = list(carried)
    return document


async def test_a_listing_entry_carries_its_tags_when_it_has_any(server):
    server.flows.save(NAMED, "tagged", flow("smoke", "auth:ldap"))
    server.flows.save(NAMED, "plain", flow())
    server.flows.save(GLOBAL_WORKSPACE, "shared", flow("smoke"))
    listing = await resource(server, flowapi.LIST_URI)
    by_name = {entry["name"]: entry for entry in listing["flows"]}
    assert by_name["tagged"]["tags"] == ["smoke", "auth:ldap"]
    assert by_name["shared"]["tags"] == ["smoke"]
    assert "tags" not in by_name["plain"]
    assert list(by_name["tagged"])[:4] == ["name", "workspace", "description", "tags"]


async def test_a_hand_edit_is_listed_with_its_tags_as_read(server):
    server.flows.write_text(NAMED, "odd", "name: odd\ntags: [Smoke, smoke]\nsteps: []\n")
    [entry] = (await resource(server, flowapi.LIST_URI))["flows"]
    assert entry["tags"] == ["smoke"]


async def test_a_flow_whose_tags_cannot_be_read_is_listed_without_them_and_runs(
    server, monkeypatch
):
    server.flows.write_text(
        NAMED, "odd",
        "name: odd\ntags: [not a tag]\nsteps:\n- tool: navigate\n"
        "  args: {url: 'https://example.test/'}\n",
    )
    [entry] = (await resource(server, flowapi.LIST_URI))["flows"]
    assert entry["name"] == "odd" and "tags" not in entry
    assert (await current(server))["tags"] == {}

    monkeypatch.setattr(
        server.actions, "navigate", lambda session_id, **kw: {"url": HERE, "title": ""}
    )
    monkeypatch.setattr(server.workspaces, "resolved", lambda name: ("b-1", "chrome"))
    report = await call(server, flowapi.RUN_TOOL, name="odd")
    assert report["status"] == "ok"


async def current(server) -> dict:
    return await resource(server, "workspace://current")


async def test_the_context_carries_the_tags_in_use_in_tag_order(server):
    server.flows.save(NAMED, "a", flow("smoke", "auth:ldap"))
    server.flows.save(NAMED, "b", flow("smoke"))
    server.flows.save(GLOBAL_WORKSPACE, "c", flow("billing", "smoke"))
    assert (await current(server))["tags"] == {
        "auth:ldap": 1,
        "billing": 1,
        "smoke": 3,
    }
    assert list((await current(server))["tags"]) == ["auth:ldap", "billing", "smoke"]


async def test_no_tagged_flow_is_an_empty_map(server):
    assert (await current(server))["tags"] == {}
    server.flows.save(NAMED, "plain", flow())
    assert (await current(server))["tags"] == {}


async def test_a_shared_flow_hidden_by_one_of_its_name_lends_no_tags(server):
    server.flows.save(GLOBAL_WORKSPACE, "login", flow("shared-only"))
    server.flows.save(NAMED, "login", flow("mine"))
    assert (await current(server))["tags"] == {"mine": 1}


async def test_another_workspaces_flows_lend_no_tags(server):
    server.flows.save("elsewhere", "theirs", flow("theirs"))
    assert (await current(server))["tags"] == {}


async def test_the_tags_in_use_follow_an_edit_of_either_library(server):
    server.flows.save(NAMED, "a", flow("one"))
    assert (await current(server))["tags"] == {"one": 1}
    server.flows.write_text(NAMED, "a", "name: a\ntags: [two]\nsteps: []\n")
    assert (await current(server))["tags"] == {"two": 1}
    server.flows.save(GLOBAL_WORKSPACE, "g", flow("two"))
    assert (await current(server))["tags"] == {"two": 2}
    server.flows.delete(NAMED, "a")
    assert (await current(server))["tags"] == {"two": 1}


def test_a_warm_read_of_the_tags_in_use_parses_no_flow(server, monkeypatch):
    from pathlib import Path

    server.flows.save(NAMED, "a", flow("one"))
    server.flows.save(GLOBAL_WORKSPACE, "g", flow("two"))
    assert server.flows.tags_in_use(NAMED) == {"one": 1, "two": 1}
    reads, real = [], Path.read_text
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: reads.append(self) or real(self, *a, **k)
    )
    assert server.flows.tags_in_use(NAMED) == {"one": 1, "two": 1}
    assert reads == []


def test_a_caller_gets_its_own_copy_of_the_tags_in_use(server):
    server.flows.save(NAMED, "a", flow("one"))
    server.flows.tags_in_use(NAMED)["one"] = 99
    assert server.flows.tags_in_use(NAMED) == {"one": 1}


# ---- which `global` flows a workspace is offered ---------------------------


@pytest.fixture
def hiding(server, monkeypatch):
    """A workspace offered only the `global` flows not tagged `hidden`: what
    the workspace config's filter will do, through the one hook it fills."""
    server.flows.save(GLOBAL_WORKSPACE, "secret", flow("hidden", paths=["/**"]))
    server.flows.save(GLOBAL_WORKSPACE, "open", flow("seen", paths=["/**"]))
    monkeypatch.setattr(
        server.flows, "offered", lambda workspace: lambda carried: "hidden" not in carried
    )
    return server


async def test_a_global_flow_not_offered_is_missing_from_the_listing(hiding):
    names = [e["name"] for e in (await resource(hiding, flowapi.LIST_URI))["flows"]]
    assert names == ["open"]


def test_a_global_flow_not_offered_is_never_named_in_flows_here(hiding):
    assert [e["name"] for e in hiding.flows.flows_here(NAMED, HERE)] == ["open"]


async def test_a_global_flow_not_offered_lends_no_tags(hiding):
    assert (await current(hiding))["tags"] == {"seen": 1}


async def test_a_global_flow_not_offered_reads_as_no_flow_at_all(hiding):
    from mcp.shared.exceptions import MCPError

    with pytest.raises(MCPError, match="no flow called 'secret' in desktop or the shared"):
        await resource(hiding, "flow://flows/secret")


async def test_a_global_flow_not_offered_does_not_run(hiding, monkeypatch):
    monkeypatch.setattr(hiding.workspaces, "resolved", lambda name: ("b-1", "chrome"))
    with pytest.raises(ValueError, match="no flow called 'secret'"):
        await call(hiding, flowapi.RUN_TOOL, name="secret")


async def test_the_shared_library_itself_is_offered_everything(hiding, monkeypatch):
    calling_as(monkeypatch, None)
    names = [e["name"] for e in (await resource(hiding, flowapi.LIST_URI))["flows"]]
    assert names == ["open", "secret"]


def test_the_admin_still_sees_and_opens_every_global_flow(hiding):
    client = TestClient(hiding.mcp.http_app())
    listing = client.get(admin(), headers=AUTH).json()
    assert [e["name"] for e in listing["flows"]] == ["open", "secret"]
    assert client.get(admin("secret"), headers=AUTH).status_code == 200
