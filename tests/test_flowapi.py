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
from kubed.selenium_flow.flows import GLOBAL_SESSION, STDIO_SESSION
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
    acting_as(monkeypatch, server, NAMED)
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


def acting_as(monkeypatch, server, key):
    """Make every surface agree about who is calling.

    Two seams, deliberately. `key` is the *browser* identity and is None when
    SAVED_SESSIONS is off; `library_key` is the *storage* identity and is not,
    because naming a flow library has nothing to do with whether this server
    remembers browsers. A test that patched only one would quietly exercise a
    caller who is two different people.
    """
    monkeypatch.setattr(server.sessions, "key", lambda: key)
    monkeypatch.setattr(server.sessions, "library_key", lambda: key)


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


async def test_an_unnamed_caller_cannot_save_into_the_shared_library(
    flow_server, monkeypatch, store
):
    """This was the exception that swallowed the rule. A caller with no session
    name *is* `global`, so the only kind of caller that could rewrite the shared
    library was the anonymous one — the chapter wrote that down and apologised
    for it. The library is live, so it is now refused like any other write."""
    acting_as(monkeypatch, flow_server, None)
    with pytest.raises(ValueError, match="read-only"):
        await call(flow_server, flowapi.SAVE_TOOL, name="shared", steps=GOOD)
    assert store.names(GLOBAL_SESSION) == []


async def test_an_unnamed_caller_still_reads_and_runs_the_shared_library(
    flow_server, monkeypatch, store
):
    """Read-only has to mean read. The shared library is most of what an unnamed
    caller is for, and losing it would make this a regression, not a fix."""
    store.save(GLOBAL_SESSION, "login", {"steps": GOOD, "description": "shared"})
    acting_as(monkeypatch, flow_server, None)
    listing = await call(flow_server, flowapi.LIST_TOOL)
    assert [f["name"] for f in listing["flows"]] == ["login"]
    assert (await call(flow_server, flowapi.GET_TOOL, name="login"))["steps"] == GOOD


async def test_an_unnamed_caller_cannot_delete_from_it_either(
    flow_server, monkeypatch, store
):
    """Deleting is the worse half: a flow that vanishes mid-run leaves nothing
    behind saying it ever existed, or who removed it."""
    store.save(GLOBAL_SESSION, "login", {"steps": GOOD})
    acting_as(monkeypatch, flow_server, None)
    with pytest.raises(ValueError, match="read-only"):
        await call(flow_server, flowapi.DELETE_TOOL, name="login")
    assert store.get(GLOBAL_SESSION, "login") is not None


async def test_a_name_that_cannot_be_a_library_is_refused_not_redirected(
    flow_server, monkeypatch, store
):
    """`?session=my bot` keys a browser perfectly well, and `session_for` falls
    back to `global` so as not to break it. For the flow tools that fallback was
    a silent redirect: the caller believed it had a private library and saved
    into the shared one, where every unnamed caller could overwrite it."""
    from kubed.selenium_flow.flows import InvalidName
    from kubed.selenium_flow.sessions import CallerKey

    acting_as(monkeypatch, flow_server, CallerKey("named:my bot", "named"))
    with pytest.raises(InvalidName, match="not a usable session name"):
        await call(flow_server, flowapi.SAVE_TOOL, name="login", steps=GOOD)
    assert store.names(GLOBAL_SESSION) == []


async def test_naming_yourself_global_does_not_buy_write_access(
    flow_server, monkeypatch, store
):
    """`global` stays a legal session name landing in the same directory, which
    is consistent rather than a special case. What it no longer does is make
    that directory writable: the rule is about the library being live, not about
    how a caller arrived at it — so the obvious way round it is closed."""
    from kubed.selenium_flow.sessions import CallerKey

    acting_as(monkeypatch, flow_server, CallerKey("named:global", "named"))
    with pytest.raises(ValueError, match="read-only"):
        await call(flow_server, flowapi.SAVE_TOOL, name="shared", steps=GOOD)
    assert store.names(GLOBAL_SESSION) == []


async def test_a_stdio_caller_has_a_writable_library(flow_server, monkeypatch, store):
    """The regression the read-only rule nearly shipped. Stdio cannot send a
    query parameter or a header, so it cannot name itself — if it resolved to
    the shared library it would be permanently unable to save a flow, and the
    refusal would tell it to do something it has no way of doing."""
    from kubed.selenium_flow.sessions import CallerKey

    acting_as(monkeypatch, flow_server, CallerKey("stdio", "stdio"))
    await call(flow_server, flowapi.SAVE_TOOL, name="login", steps=GOOD)
    assert store.names(STDIO_SESSION) == ["login"]
    assert store.names(GLOBAL_SESSION) == [], "it wrote into the shared library"


async def test_a_stdio_caller_still_reads_the_shared_library(
    flow_server, monkeypatch, store
):
    """Its own library is additional to `global`, not instead of it."""
    from kubed.selenium_flow.sessions import CallerKey

    store.save(GLOBAL_SESSION, "shared", {"steps": GOOD})
    acting_as(monkeypatch, flow_server, CallerKey("stdio", "stdio"))
    listing = await call(flow_server, flowapi.LIST_TOOL)
    assert [f["name"] for f in listing["flows"]] == ["shared"]
    assert listing["flows"][0]["shared"] is True


async def test_with_flows_off_an_unnamed_caller_is_told_that_not_to_rename_itself(
    monkeypatch, tmp_path
):
    """Two refusals could apply and only one is true. With no FLOW_DATA_DIR
    there is nowhere to keep a flow for anybody, so sending an unnamed caller
    off to name its session would point it at the wrong problem entirely — and
    a *named* caller already got the right answer, so the two disagreed."""
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    server = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)
    acting_as(monkeypatch, server, None)
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        await call(server, flowapi.SAVE_TOOL, name="x", steps=GOOD)
    with pytest.raises(ValueError, match="FLOW_DATA_DIR"):
        await call(server, flowapi.DELETE_TOOL, name="x")


async def test_turning_off_saved_sessions_does_not_take_away_the_library(
    monkeypatch, tmp_path
):
    """SAVED_SESSIONS decides whether this server remembers a *browser* for a
    caller. A flow library is not a browser: a caller that put ?session= in its
    URL has named itself whatever that switch says.

    Deriving one from the other meant switching off browser memory silently
    removed every caller's ability to save a flow — and told the ones that HAD
    named themselves to go and do the thing they had already done."""
    from kubed.selenium_flow import sessions as sessions_module

    from .conftest import http

    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "research-bot"})
    )
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        flow_data_dir=str(tmp_path),
        saved_sessions=False,
    )
    assert server.sessions.key() is None, "the browser half is not actually off"
    await call(server, flowapi.SAVE_TOOL, name="login", steps=GOOD)
    assert server.flows.names("research-bot") == ["login"]
    assert server.flows.names(GLOBAL_SESSION) == []


async def test_the_write_tool_prompts_do_not_lie_to_a_stdio_caller(flow_server):
    """These descriptions are prompt, not documentation: a stdio client reads
    one and acts on it. They told it to set a URL parameter it has no way to
    send, in order to obtain a library it already has."""
    for name in (flowapi.SAVE_TOOL, flowapi.DELETE_TOOL):
        description = (await flow_server.mcp.get_tool(name)).description
        assert "stdio" in description, f"{name} still tells stdio it cannot save"


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
    n8n HTTP node: no session name anywhere, so it reads and runs the shared
    `global` library and has no library of its own to write to."""
    monkeypatch.delenv("FLOW_DATA_DIR", raising=False)
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        auth_token=TOKEN,
        flow_data_dir=str(tmp_path),
    )
    acting_as(monkeypatch, server, None)
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


def test_an_http_caller_that_names_nothing_reads_global_but_cannot_write_it(
    unkeyed_client,
):
    """The ordinary case for an n8n HTTP node. It still reaches the shared
    library to list and run; saving into it is a 400 rather than a silent write
    to somewhere every other session can overwrite."""
    saved = unkeyed_client.post(
        "/flows/save", json={"name": "shared", "steps": GOOD}, headers=AUTH
    )
    assert saved.status_code == 400
    assert "read-only" in saved.json()["error"]
    listing = unkeyed_client.post("/flows/list", json={}, headers=AUTH)
    assert listing.json()["session"] == GLOBAL_SESSION
    assert listing.json()["flows"] == []


def test_naming_global_in_the_body_is_refused_too(client):
    """This surface is always explicit, so it can ask for the shared library by
    name — and that is the same write, refused the same way."""
    response = client.post(
        "/flows/save",
        json={"session": GLOBAL_SESSION, "name": "x", "steps": GOOD},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "read-only" in response.json()["error"]


def test_the_refusal_says_how_to_get_a_library_of_your_own(client):
    """A refusal that does not say what to do instead only moves the problem —
    and this one is reachable by a caller that did nothing wrong except not name
    itself."""
    error = client.post(
        "/flows/delete",
        json={"session": GLOBAL_SESSION, "name": "x"},
        headers=AUTH,
    ).json()["error"]
    assert "session=" in error, "it does not say how to get a library"
    assert "admin" in error, "it does not say who can change global"


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


async def test_the_published_secret_reference_refuses_empty_values(flow_server):
    """`save_flow` refuses an empty name or key, so the schema a client builds
    from must say so too — a bare `string` told it `""` was fine."""
    schema = await call(flow_server, flowapi.SCHEMA_TOOL)
    branch = next(b for b in schema["x-value-from"]["oneOf"] if "secret" in b["required"])
    fields = branch["properties"]["secret"]["properties"]
    assert fields["name"]["minLength"] == 1
    assert fields["key"]["minLength"] == 1
