"""The flow surfaces: resources, tools and the /flows endpoints.

`/flows` is a layer above `/browser` rather than more of it, so it is held to
its own contract here instead of being folded into test_surfaces.py. What that
contract has to protect:

- reads see the shared library, writes never touch it (§F1.2);
- the reads are resources, with tools mirroring them only for clients that
  cannot read resources (§F1.5);
- a broken flow is refused when it is saved, not when it runs (§F1.6).
"""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow import flowapi
from kubed.selenium_flow import resources as resources_module
from kubed.selenium_flow.flows import GLOBAL_SESSION
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import NAMED, TOKEN

pytestmark = pytest.mark.unit

GOOD = [{"tool": "navigate", "params": {"url": "https://example.test/"}}]


@pytest.fixture
def flow_server(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        flow_data_dir=str(tmp_path),
    )
    monkeypatch.setattr(server.sessions, "key", lambda: NAMED)
    return server


@pytest.fixture
def store(flow_server):
    return flow_server.flows


async def call(server, tool_name, /, **kwargs):
    """Call a tool by name. Positional-only, because `name` is also a *tool's*
    argument and the obvious signature collides with it."""
    tool = await server.mcp.get_tool(tool_name)
    result = tool.fn(**kwargs)
    if hasattr(result, "__await__"):
        result = await result
    return result


# ---- the surface itself -----------------------------------------------------


async def test_a_client_with_resources_sees_only_the_write_tools(flow_server):
    """Five operations, two tools. The reads are resources, and mirroring them
    into the listing as well would be noise (§F1.5)."""
    names = {t.name for t in await flow_server.mcp.list_tools()}
    assert flowapi.SAVE_TOOL in names
    assert flowapi.DELETE_TOOL in names
    assert flowapi.LIST_TOOL not in names
    assert flowapi.GET_TOOL not in names


async def test_a_client_without_resources_gets_the_reads_as_tools(
    flow_server, monkeypatch
):
    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    names = {t.name for t in await flow_server.mcp.list_tools()}
    assert {flowapi.LIST_TOOL, flowapi.GET_TOOL, flowapi.SCHEMA_TOOL} <= names


async def test_the_hidden_reads_are_still_callable(flow_server):
    """Hiding a tool from a listing is presentation; refusing to run it would
    be a different and worse contract."""
    assert await call(flow_server, flowapi.LIST_TOOL) == {
        "session": "desktop",
        "count": 0,
        "flows": [],
    }


async def test_every_flow_tool_declares_honest_annotations(flow_server, monkeypatch):
    monkeypatch.setattr(resources_module, "_http", lambda: ({"resources": "off"}, {}))
    tools = {t.name: t for t in await flow_server.mcp.list_tools()}
    for name in (flowapi.LIST_TOOL, flowapi.GET_TOOL, flowapi.SCHEMA_TOOL):
        assert tools[name].annotations.read_only_hint is True, name
    assert tools[flowapi.SCHEMA_TOOL].annotations.open_world_hint is False
    assert tools[flowapi.DELETE_TOOL].annotations.destructive_hint is True


# ---- saving -----------------------------------------------------------------


async def test_a_flow_saves_and_reads_back(flow_server, store):
    await call(flow_server, flowapi.SAVE_TOOL, name="login", steps=GOOD,
               description="Log in")
    assert store.names("desktop") == ["login"]
    read = await call(flow_server, flowapi.GET_TOOL, name="login")
    assert read["description"] == "Log in"
    assert read["steps"] == GOOD
    assert read["shared"] is False


async def test_a_broken_flow_is_refused_at_save_with_every_reason(flow_server, store):
    with pytest.raises(ValueError) as caught:
        await call(
            flow_server,
            flowapi.SAVE_TOOL,
            name="broken",
            steps=[{"tool": "nope", "params": {}}, {"tool": "write", "params": {}}],
        )
    assert "no tool called 'nope'" in str(caught.value)
    assert "write needs 'text'" in str(caught.value)
    # And nothing was written.
    assert store.names("desktop") == []


async def test_saving_the_same_name_replaces_it(flow_server, store):
    await call(flow_server, flowapi.SAVE_TOOL, name="login", steps=GOOD, description="a")
    await call(flow_server, flowapi.SAVE_TOOL, name="login", steps=GOOD, description="b")
    assert store.names("desktop") == ["login"]
    assert store.get("desktop", "login")["description"] == "b"


# ---- the shared library -----------------------------------------------------


async def test_reads_see_the_shared_library(flow_server, store):
    store.save(GLOBAL_SESSION, "cookie-banner", {"steps": GOOD, "description": "shared"})
    listing = await call(flow_server, flowapi.LIST_TOOL)
    assert [f["name"] for f in listing["flows"]] == ["cookie-banner"]
    assert listing["flows"][0]["shared"] is True
    assert (await call(flow_server, flowapi.GET_TOOL, name="cookie-banner"))["shared"]


async def test_your_own_flow_wins_a_name_collision(flow_server, store):
    store.save(GLOBAL_SESSION, "login", {"steps": GOOD, "description": "shared"})
    await call(flow_server, flowapi.SAVE_TOOL, name="login", steps=GOOD,
               description="mine")
    listing = await call(flow_server, flowapi.LIST_TOOL)
    assert listing["count"] == 1
    assert listing["flows"][0]["description"] == "mine"
    assert listing["flows"][0]["shared"] is False
    assert (await call(flow_server, flowapi.GET_TOOL, name="login"))["description"] == "mine"
    # Shadowed, not overwritten.
    assert store.get(GLOBAL_SESSION, "login")["description"] == "shared"


async def test_a_save_never_writes_to_the_shared_library(flow_server, store):
    """An agent cannot publish. Promotion to global is an admin action (§F1.2)."""
    store.save(GLOBAL_SESSION, "login", {"steps": GOOD, "description": "shared"})
    await call(flow_server, flowapi.SAVE_TOOL, name="login", steps=GOOD,
               description="mine")
    assert store.get(GLOBAL_SESSION, "login")["description"] == "shared"


async def test_a_delete_never_touches_the_shared_library(flow_server, store):
    store.save(GLOBAL_SESSION, "login", {"steps": GOOD})
    result = await call(flow_server, flowapi.DELETE_TOOL, name="login")
    assert result["deleted"] is False
    assert store.get(GLOBAL_SESSION, "login") is not None


async def test_an_unnamed_caller_is_the_global_session(flow_server, monkeypatch, store):
    monkeypatch.setattr(flow_server.sessions, "key", lambda: None)
    await call(flow_server, flowapi.SAVE_TOOL, name="shared", steps=GOOD)
    assert store.names(GLOBAL_SESSION) == ["shared"]


# ---- the published schema ---------------------------------------------------


async def test_the_schema_is_derived_from_the_live_tools(flow_server):
    schema = await call(flow_server, flowapi.SCHEMA_TOOL)
    steps = schema["properties"]["steps"]["items"]["properties"]["tool"]["enum"]
    assert "write" in steps and "interact" in steps
    # Lifecycle is not a step, so it cannot appear in the shape we publish.
    assert "open_session" not in steps and "end_browser" not in steps
    assert "css" in schema["x-step-params"]["write"]["properties"]


# ---- flows turned off --------------------------------------------------------


async def test_with_no_data_directory_the_tools_say_so(monkeypatch):
    """A disabled capability and a missing one look identical from outside, and
    only one of them is something an operator can fix."""
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    server = SeleniumMCP(grid_url="http://grid.invalid:4444")
    monkeypatch.setattr(server.sessions, "key", lambda: NAMED)
    assert server.flows is None
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        await call(server, flowapi.LIST_TOOL)


# ---- the HTTP half -----------------------------------------------------------


@pytest.fixture
def client(flow_server):
    return TestClient(flow_server.mcp.http_app())


@pytest.fixture
def unkeyed_client(tmp_path, monkeypatch):
    """A client the server cannot identify, which is the ordinary case for an
    n8n HTTP node: no session name anywhere, so everything is `global`."""
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        flow_data_dir=str(tmp_path),
    )
    monkeypatch.setattr(server.sessions, "key", lambda: None)
    return TestClient(server.mcp.http_app())


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_the_endpoints_need_the_token(client):
    assert client.post("/flows/list", json={}).status_code == 401


def test_save_then_list_then_get_over_http(client):
    saved = client.post(
        "/flows/save",
        json={"session": "workflow", "name": "login", "steps": GOOD, "description": "x"},
        headers=AUTH,
    )
    assert saved.status_code == 200, saved.text
    listing = client.post("/flows/list", json={"session": "workflow"}, headers=AUTH)
    assert [f["name"] for f in listing.json()["flows"]] == ["login"]
    got = client.post(
        "/flows/get", json={"session": "workflow", "name": "login"}, headers=AUTH
    )
    assert got.json()["steps"] == GOOD


def test_an_http_caller_may_name_the_session_in_the_body(client):
    """The explicit half of the contract: this surface takes the session as an
    argument, the way /browser takes a session_id."""
    client.post(
        "/flows/save",
        json={"session": "workflow", "name": "theirs", "steps": GOOD},
        headers=AUTH,
    )
    listing = client.post("/flows/list", json={"session": "workflow"}, headers=AUTH)
    assert listing.json()["session"] == "workflow"


def test_an_http_caller_that_names_nothing_gets_global(unkeyed_client):
    unkeyed_client.post("/flows/save", json={"name": "shared", "steps": GOOD},
                        headers=AUTH)
    listing = unkeyed_client.post("/flows/list", json={}, headers=AUTH)
    assert listing.json()["session"] == GLOBAL_SESSION
    assert [f["name"] for f in listing.json()["flows"]] == ["shared"]


def test_a_broken_flow_is_a_400_over_http_too(client):
    response = client.post(
        "/flows/save",
        json={"name": "bad", "steps": [{"tool": "nope", "params": {}}]},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "no tool called" in response.json()["error"]


def test_a_traversing_session_name_is_a_400(client):
    response = client.post(
        "/flows/list", json={"session": "../../etc"}, headers=AUTH
    )
    assert response.status_code == 400


def test_a_missing_flow_is_a_400_that_says_where_to_look(client):
    response = client.post("/flows/get", json={"name": "nope"}, headers=AUTH)
    assert response.status_code == 400
    assert "list_flows" in response.json()["error"]


def test_the_schema_endpoint_serves_the_document_shape(client):
    response = client.post("/flows/schema", json={}, headers=AUTH)
    assert response.status_code == 200
    assert "steps" in response.json()["properties"]


def test_delete_is_idempotent_over_http(client):
    client.post("/flows/save", json={"name": "gone", "steps": GOOD}, headers=AUTH)
    assert client.post("/flows/delete", json={"name": "gone"}, headers=AUTH).json()[
        "deleted"
    ]
    assert not client.post("/flows/delete", json={"name": "gone"}, headers=AUTH).json()[
        "deleted"
    ]


# ---- running one ------------------------------------------------------------


@pytest.fixture
def ran(flow_server, monkeypatch):
    """The server with a resolved browser and an action layer that records."""
    calls = []

    def record(tool):
        def action(session_id, **kwargs):
            calls.append((tool, session_id, kwargs))
            return {"url": "https://example.test/after", "title": "After"}

        return action

    for tool in ("navigate", "write", "interact"):
        monkeypatch.setattr(flow_server.actions, tool, record(tool))
    monkeypatch.setattr(flow_server.sessions, "resolve", lambda key, sid: "browser-1")
    return flow_server, calls


async def test_a_saved_flow_runs_end_to_end(ran):
    server, calls = ran
    await call(
        server,
        flowapi.SAVE_TOOL,
        name="login",
        steps=[
            {"tool": "navigate", "params": {"url": "https://example.test/login"}},
            {"tool": "write", "params": {"css": "#email", "text": "a@b.c"}},
            {"tool": "interact", "params": {"action": "click", "css": "button"}},
        ],
    )
    report = await call(server, flowapi.RUN_TOOL, name="login")
    assert report["status"] == "ok"
    assert report["steps_run"] == 3
    assert [tool for tool, _, _ in calls] == ["navigate", "write", "interact"]
    # One browser for the whole run, which is the point (§F1.1).
    assert {session for _, session, _ in calls} == {"browser-1"}


async def test_running_a_shared_flow_works_and_says_it_was_shared(ran):
    server, _calls = ran
    server.flows.save(
        GLOBAL_SESSION,
        "banner",
        {"steps": [{"tool": "navigate", "params": {"url": "x"}}]},
    )
    report = await call(server, flowapi.RUN_TOOL, name="banner")
    assert report["status"] == "ok"
    assert report["session"] == GLOBAL_SESSION


async def test_running_a_flow_that_is_not_there_says_where_to_look(ran):
    server, _ = ran
    with pytest.raises(ValueError, match="list_flows"):
        await call(server, flowapi.RUN_TOOL, name="nope")


async def test_parameters_reach_the_step_that_names_them(ran):
    server, calls = ran
    await call(
        server,
        flowapi.SAVE_TOOL,
        name="login",
        parameters={"type": "object", "properties": {"email": {"type": "string"}}},
        steps=[
            {
                "tool": "write",
                "params": {"css": "#email", "value_from": {"param": "email"}},
            }
        ],
    )
    await call(server, flowapi.RUN_TOOL, name="login",
               params={"email": "someone@example.test"})
    assert calls[0][2]["text"] == "someone@example.test"


async def test_run_flow_admits_it_is_destructive(flow_server):
    """It can click anything, and this server cannot tell which click."""
    tools = {t.name: t for t in await flow_server.mcp.list_tools()}
    assert tools[flowapi.RUN_TOOL].annotations.destructive_hint is True


def test_running_over_http_needs_an_explicit_browser(client):
    """This surface is always explicit — session id in, session id out."""
    client.post("/flows/save", json={"name": "f", "steps": GOOD}, headers=AUTH)
    response = client.post("/flows/run", json={"name": "f"}, headers=AUTH)
    assert response.status_code == 400
    assert "session_id is required" in response.json()["error"]


def test_verbose_false_over_http_does_not_turn_verbose_on(client, flow_server,
                                                          monkeypatch):
    """bool("false") is True. Over HTTP everything arrives as a string, so the
    boundary has to coerce — the reason as_bool exists at all."""
    monkeypatch.setattr(
        flow_server.actions,
        "navigate",
        lambda session_id, **kw: {"url": "u", "title": "t", "big": "x" * 100},
    )
    client.post("/flows/save", json={"name": "f", "steps": GOOD}, headers=AUTH)
    response = client.post(
        "/flows/run",
        json={"name": "f", "session_id": "b", "verbose": "false"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert "result" not in response.json()["steps"][0]


async def test_a_resize_step_is_written_back_to_the_session(flow_server, monkeypatch):
    """Otherwise the flow resizes the live browser and the session comes back
    the old size the next time the Grid reaps it."""
    monkeypatch.setattr(
        flow_server.actions,
        "resize",
        lambda session_id, **kw: {"width": kw["width"], "height": kw["height"],
                                  "url": "u", "title": "t"},
    )
    monkeypatch.setattr(flow_server.sessions, "resolve", lambda key, sid: "browser-1")
    flow_server.sessions.remember(NAMED, "browser-1", "", {"browser": "firefox"})
    await call(
        flow_server,
        flowapi.SAVE_TOOL,
        name="widen",
        steps=[{"tool": "resize", "params": {"width": 1400, "height": 900}}],
    )
    await call(flow_server, flowapi.RUN_TOOL, name="widen")
    record = flow_server.sessions.store.get(NAMED.value)
    assert record.window == "1400x900"
    # And the browser choice survived, as reshape's merge promises.
    assert record.settings["browser"] == "firefox"


async def test_the_published_schema_describes_the_flow_side_sources(flow_server):
    """`x-step-params` comes from the direct tool schemas, where value_from can
    only name a secret — a flow's own parameters mean nothing to a direct
    caller. Inside a flow they do, and a caller following only the tool schema
    would have thought `param` was invalid."""
    schema = await call(flow_server, flowapi.SCHEMA_TOOL)
    sources = schema["x-value-from"]["oneOf"]
    required = {tuple(branch["required"]) for branch in sources}
    assert required == {("secret",), ("param",)}
    # And the tool half still describes the secret shape properly.
    assert "value_from" in schema["x-step-params"]["write"]["properties"]


def _objects(node):
    """Every JSON-Schema object node under `node`, however deep."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


async def test_every_object_in_the_published_sources_is_closed(flow_server):
    """JSON Schema allows any extra property by default, so an open branch
    published `{secret, config}` and a misspelt reference as valid while
    `save_flow` refused both. Walked rather than listed, so a branch added later
    cannot be left open without this noticing."""
    schema = await call(flow_server, flowapi.SCHEMA_TOOL)
    objects = list(_objects(schema["x-value-from"]))
    # Both branches and the nested secret reference, at least.
    assert len(objects) >= 3
    for node in objects:
        assert node.get("additionalProperties") is False, node


async def test_the_published_secret_reference_is_the_tool_model(flow_server):
    """Derived, not copied: the flow schema and the direct tool must describe a
    secret reference identically, or one of them is wrong."""
    from kubed.selenium_flow.tools import SecretRef

    schema = await call(flow_server, flowapi.SCHEMA_TOOL)
    branch = next(b for b in schema["x-value-from"]["oneOf"] if "secret" in b["required"])
    assert branch["properties"]["secret"] == SecretRef.model_json_schema()
