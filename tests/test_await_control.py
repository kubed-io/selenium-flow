"""`await_control`: wait for the person to hand back, and hear what they did
(spec 2026-10-27-live-control, rulings 4, 6, 8, 15-17, 29-31, R1-R6).

Through a real `fastmcp.Client` on the server (in memory, and over streamable
HTTP where the headers matter) and through the ASGI app for the long poll.
Every conversation is bounded, so a wait that never ends fails by name.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from contextlib import asynccontextmanager, contextmanager

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from fastmcp_tasks import call_tool_task

from kubed.selenium_flow.config import ConfigError, Settings, load
from kubed.selenium_flow.core.capabilities import ENDPOINTS
from kubed.selenium_flow.flows.document import InvalidFlow, step_schemas, validate
from kubed.selenium_flow.http import answer as answer_module
from kubed.selenium_flow.live.control import ENDED, NOBODY, STILL, STOPPING
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.mcp import waiting
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace import locks
from kubed.selenium_flow.workspace.store import Workspace

from .conftest import calling_as
from .test_live_tunnels import held

pytestmark = pytest.mark.unit

GRID = "g1"
SOON = 5
# As the pages are heard: raw, credentials and all, oldest first.
PAGES = [
    {"url": "https://u:pw@idp.example/login?code=s3cret#top", "title": "Sign in"},
    {"url": "https://u:pw@app.example/home?tab=2", "title": "Home"},
]
VISITED = [
    {"url": "https://idp.example/login", "title": "Sign in"},
    {"url": "https://app.example/home?tab=2", "title": "Home"},
]
OUTLINE = {
    "url": "https://app.example/home?tab=2",
    "title": "Home",
    "count": 1,
    "total": 1,
    "elements": [{"role": "button", "name": "Go", "css": "#go"}],
}
HISTORY = [{"origin": "https://a.test", "url": "https://a.test/", "at": 1.0}]
HHMM = r"\d\d:\d\d"


class Browser:
    """What the fakes saw: the live Grid ids and every page read."""

    def __init__(self):
        self.alive = {GRID}
        self.read: list[str] = []
        self.opened = 0


@pytest.fixture
def server(monkeypatch):
    server = SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}))
    browser = Browser()
    monkeypatch.setattr(server.grid, "is_alive", lambda sid: sid in browser.alive)

    def opened(*_args, **_kwargs):
        browser.opened += 1
        raise AssertionError("await_control opened a browser")

    monkeypatch.setattr(server.grid, "open", opened)
    monkeypatch.setattr(
        server.actions,
        "outline",
        lambda sid, **_kw: browser.read.append(sid) or dict(OUTLINE),
    )

    async def followed(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server.monitor, "attach_here", followed)
    monkeypatch.setattr(
        server.pages, "listed", lambda _gid: ([dict(p) for p in PAGES], 0)
    )
    server.store.set("w1", Workspace(session_id=GRID, history=list(HISTORY)))
    server.browser = browser
    return server


@pytest.fixture
def named(monkeypatch):
    calling_as(monkeypatch, "w1")


def entered(server, monkeypatch) -> asyncio.Event:
    """Set once a wait for the hand-back has begun."""
    event = asyncio.Event()
    wait = server.controls.wait

    async def waiting(workspace, timeout):
        event.set()
        return await wait(workspace, timeout)

    monkeypatch.setattr(server.controls, "wait", waiting)
    return event


def hand_back(tunnel):
    tunnel.mode = "watch"
    tunnel.on_mode()


async def hold(server, who="kelly"):
    return await asyncio.to_thread(held, server.tunnels, "w1", who, "control", GRID)


def legacy(server):
    return Client(server.mcp, mode="legacy")


# ---- the blocking path -----------------------------------------------------


async def test_it_returns_with_the_summary_the_moment_the_person_hands_back(
    server, named, monkeypatch
):
    tunnel = await hold(server)
    began = entered(server, monkeypatch)
    async with asyncio.timeout(SOON), legacy(server) as client:
        call = asyncio.create_task(client.call_tool("await_control"))
        await began.wait()
        await asyncio.sleep(0.05)
        assert not call.done()
        hand_back(tunnel)
        result = await call
        # Said by this answer, so the next result has nothing to add. (The
        # client's exit stops the server, which forgets every hand-back.)
        assert server.controls.notice("w1") is None
    summary = result.structured_content
    assert summary["handed_back"] is True and summary["ended"] is False
    control = summary["control"]
    assert control["who"] == ["kelly"]
    assert isinstance(control["held_for"], int) and control["held_for"] >= 0
    assert {"since", "until"} <= set(control)
    assert summary["page"] == {"url": OUTLINE["url"], "title": "Home"}
    assert summary["visited"] == VISITED
    assert summary["dropped"] == 0
    assert summary["outline"] == {
        "count": 1, "total": 1, "elements": OUTLINE["elements"],
    }
    assert re.fullmatch(
        rf"kelly handed back at {HHMM} UTC after \d+s; .+", summary["message"]
    )
    assert "visited" in summary["message"]
    # Ruling 29: nothing to History, and the page was read, not reopened.
    assert server.store.get("w1").history == HISTORY
    assert server.browser.read == [GRID] and server.browser.opened == 0


async def test_the_blocking_call_sends_progress_and_answers_at_the_cap(
    server, named, monkeypatch
):
    await hold(server, who="admin")
    monkeypatch.setattr(server.settings.live, "await_cap", 0.2)
    seen = []

    async def progressed(progress, total, message):
        seen.append((progress, total, message))

    async with asyncio.timeout(SOON), legacy(server) as client:
        result = await client.call_tool("await_control", progress_handler=progressed)
        # Still held: the episode is the person's, not the waiter's.
        assert server.controls.notice("w1")["state"] == "held"
    summary = result.structured_content
    assert summary["handed_back"] is False and summary["ended"] is False
    assert summary["message"] == "still in a person's control; call again."
    assert summary["message"] == STILL.format(who="a person")
    assert summary["control"]["who"] == ["a person"]
    assert "page" not in summary and "outline" not in summary
    assert seen, "no progress was sent"
    assert seen[0][1] == pytest.approx(0.2)
    assert "a person" in seen[0][2]


async def test_the_notice_never_rewrites_its_answer(server, named, monkeypatch):
    """`Notices` would add the held notice to a cap's answer and replace its
    own ``control``: await_control says control itself."""
    await hold(server)
    monkeypatch.setattr(server.settings.live, "await_cap", 0.2)
    async with asyncio.timeout(SOON), legacy(server) as client:
        result = await client.call_tool("await_control")
    control = result.structured_content["control"]
    assert "hint" not in control and control["state"] == "held"
    assert [c.text for c in result.content if c.type == "text"] == [
        result.content[0].text
    ]
    assert "has control of this browser" not in result.content[0].text


async def test_nobody_in_control_answers_at_once(server, named):
    async with asyncio.timeout(1), legacy(server) as client:
        result = await client.call_tool("await_control")
    summary = result.structured_content
    assert summary["handed_back"] is False and summary["control"] is None
    assert summary["page"] == {"url": OUTLINE["url"], "title": "Home"}
    assert summary["message"] == NOBODY
    assert NOBODY == "nobody has control of this browser: carry on."
    assert server.store.get("w1").history == HISTORY


async def test_an_unsaid_hand_back_is_returned_once(server, named):
    tunnel = await hold(server)
    hand_back(tunnel)
    async with asyncio.timeout(SOON), legacy(server) as client:
        first = await client.call_tool("await_control")
        assert server.controls.notice("w1") is None
        second = await client.call_tool("await_control")
    assert first.structured_content["handed_back"] is True
    assert first.structured_content["control"]["who"] == ["kelly"]
    assert second.structured_content["handed_back"] is False
    assert second.structured_content["message"] == NOBODY


async def test_a_browser_that_ended_is_said_not_reopened(server, named, monkeypatch):
    await hold(server)
    began = entered(server, monkeypatch)
    async with asyncio.timeout(SOON), legacy(server) as client:
        call = asyncio.create_task(client.call_tool("await_control"))
        await began.wait()
        server.browser.alive.clear()
        server.controls.ended(GRID)  # `session.ended`
        result = await call
    summary = result.structured_content
    assert summary["handed_back"] is True and summary["ended"] is True
    assert "page" not in summary and "outline" not in summary
    assert summary["message"] == ENDED.format(who="kelly")
    assert summary["message"] == (
        "the browser ended while kelly had control: call open_session."
    )
    assert server.browser.opened == 0 and server.browser.read == []


# ---- as a task ---------------------------------------------------------------


async def test_a_tasks_client_gets_a_task_and_the_summary_at_the_hand_back(
    server, named, monkeypatch
):
    tunnel = await hold(server)
    began = entered(server, monkeypatch)
    # The cap is the blocking path's: a task waits until the hand-back.
    monkeypatch.setattr(server.settings.live, "await_cap", 0.2)
    async with asyncio.timeout(SOON), Client(server.mcp) as client:
        task = await call_tool_task(client, "await_control")
        await began.wait()
        await asyncio.sleep(0.4)
        assert (await task.status()).status == "working"
        assert task.create_result.poll_interval_ms == 1000
        hand_back(tunnel)
        result = await task.result()
    assert result.structured_content["handed_back"] is True
    assert result.structured_content["visited"] == VISITED


async def test_a_cancelled_wait_leaves_the_episode_holding(server, named, monkeypatch):
    tunnel = await hold(server)
    began = entered(server, monkeypatch)
    async with asyncio.timeout(SOON), Client(server.mcp) as client:
        task = await call_tool_task(client, "await_control")
        await began.wait()
        await task.cancel()
        for _ in range(100):
            if (await task.status()).status == "cancelled":
                break
            await asyncio.sleep(0.02)
        assert (await task.status()).status == "cancelled"
        assert server.controls.notice("w1")["state"] == "held"
        hand_back(tunnel)
        # Nobody heard it: the hand-back is still to be said.
        assert server.controls.notice("w1")["state"] == "handed_back"


@contextmanager
def turn_held():
    """A call holding the session's turn on the lock, from another thread, as
    a long `assert` would. Taken once control is held: a grant waits for the
    calls that asked before it (ruling 14)."""
    holding, release = threading.Event(), threading.Event()

    def call():
        with locks.driving(GRID):
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    assert holding.wait(5), "the call never took its turn"
    try:
        yield
    finally:
        release.set()
        thread.join(5)


async def test_stopping_the_server_releases_a_waiting_task(server, named, monkeypatch):
    """Docket's worker waits for its running tasks at shutdown: the controls
    must let go of every waiter before it does, and the waiter must answer
    without reading the page - a read waits its turn behind the call holding
    the lock - or a stop hangs."""
    await hold(server)
    began = entered(server, monkeypatch)
    async with asyncio.timeout(SOON):
        held_turn = turn_held()
        held_turn.__enter__()
        client = Client(server.mcp)
        await client.__aenter__()
        try:
            await call_tool_task(client, "await_control")
            await began.wait()
        finally:
            start = time.monotonic()
            try:
                await client.__aexit__(None, None, None)
            finally:
                stopped = time.monotonic() - start
                held_turn.__exit__(None, None, None)
    assert stopped < 2
    assert server.browser.read == []


async def test_a_waiter_let_go_by_a_stop_says_so_and_reads_nothing(
    server, named, monkeypatch
):
    await hold(server)
    began = entered(server, monkeypatch)
    async with asyncio.timeout(SOON), legacy(server) as client:
        call = asyncio.create_task(client.call_tool("await_control"))
        await began.wait()
        with turn_held():
            server.controls.stop()
            result = await call
        server.controls.start()
    summary = result.structured_content
    assert summary["handed_back"] is False and summary["message"] == STOPPING
    assert summary["control"]["who"] == ["kelly"]
    assert "page" not in summary and server.browser.read == []


async def test_a_page_that_cannot_be_read_is_said_and_the_hand_back_spent(
    server, named, monkeypatch, caplog
):
    def unreadable(sid, **_kw):
        raise RuntimeError(f"no page at https://app.example/secret on {sid}")

    monkeypatch.setattr(server.actions, "outline", unreadable)
    hand_back(await hold(server))
    caplog.set_level(logging.DEBUG)
    async with asyncio.timeout(SOON), legacy(server) as client:
        result = await client.call_tool("await_control")
        assert server.controls.notice("w1") is None  # said by this answer
    summary = result.structured_content
    assert summary["handed_back"] is True
    assert "app.example/secret" in summary["page_error"]
    assert "page" not in summary and "outline" not in summary
    assert summary["visited"] == VISITED
    assert summary["message"].startswith("kelly handed back at ")
    logged = [
        r.getMessage() for r in caplog.records
        if r.name == "kubed.selenium_flow.workspace.workspaces"
    ]
    assert any("could not be read" in line for line in logged)
    for line in logged:
        assert GRID not in line and "app.example" not in line and "Home" not in line


# ---- the workspace, carried to the worker ------------------------------------


@asynccontextmanager
async def over_http(server, url, headers=None, mode="auto"):
    app = server.mcp.http_app()

    def factory(headers=None, timeout=None, auth=None, **kwargs):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
            timeout=timeout if timeout is not None else httpx2.Timeout(30),
            **kwargs,
        )

    transport = StreamableHttpTransport(
        url, headers=headers or {}, httpx_client_factory=factory
    )
    async with (
        asyncio.timeout(SOON),
        app.router.lifespan_context(app),
        Client(transport, mode=mode) as client,
    ):
        yield client


async def test_the_workspace_reaches_the_worker_from_the_query(server):
    """``?workspace=`` is lost to the worker (R3): it is carried in a private
    header set at submission, overwriting one the client sent (ruling 31)."""
    hand_back(await hold(server))
    async with over_http(
        server,
        "http://test/mcp?workspace=w1",
        headers={clients_module.CARRIED: "w2"},
    ) as client:
        task = await call_tool_task(client, "await_control")
        result = await task.result()
    summary = result.structured_content
    assert summary["handed_back"] is True
    assert summary["control"]["who"] == ["kelly"]


async def test_a_request_naming_two_workspaces_is_refused_at_submission(server):
    """The worker sees only headers: a task let through would take the header
    for an answer and drop the URL's name, where a request is refused."""
    async with over_http(
        server,
        "http://test/mcp?workspace=w1",
        headers={"X-Workspace": "w2"},
    ) as client:
        with pytest.raises(ToolError, match="did not run as a task"):
            await call_tool_task(client, "await_control")
        with pytest.raises(ToolError, match="name your workspace once"):
            await client.call_tool("await_control")


async def test_the_carried_header_is_ignored_on_a_request(server):
    """Read only where there is no request: a client cannot name a workspace
    with it."""
    async with over_http(
        server,
        "http://test/mcp",
        headers={clients_module.CARRIED: "w1"},
        mode="legacy",
    ) as client:
        result = await client.call_tool("await_control", raise_on_error=False)
    assert result.is_error
    assert "name your workspace" in result.content[0].text


async def test_a_submission_naming_no_workspace_is_refused(server):
    """Refused at submission, as the tool would refuse it inline: a worker
    has nothing to say it with, and must never invent a name (§F2.12)."""
    hand_back(await asyncio.to_thread(held, server.tunnels, "stdio", "kelly", "control"))
    async with over_http(server, "http://test/mcp") as client:
        with pytest.raises(ToolError, match="did not run as a task"):
            await call_tool_task(client, "await_control")
        with pytest.raises(ToolError, match="name your workspace"):
            await client.call_tool("await_control")


async def test_a_worker_with_no_carried_name_is_unnamed_never_stdio(
    server, monkeypatch
):
    """Should a task ever reach its worker without the carried name, the
    snapshot's own X-Workspace does not name it and neither does stdio."""

    async def through(self, context, call_next):
        return await call_next(context)

    monkeypatch.setattr(clients_module.Carry, "on_call_tool", through)
    hand_back(await asyncio.to_thread(held, server.tunnels, "stdio", "kelly", "control"))
    hand_back(await hold(server))
    async with over_http(
        server, "http://test/mcp", headers={"X-Workspace": "w1"}
    ) as client:
        task = await call_tool_task(client, "await_control")
        with pytest.raises(ToolError, match="name your workspace"):
            await task.result()
        assert server.controls.notice("w1")["state"] == "handed_back"


async def test_a_task_over_stdio_is_still_stdio(server):
    """No HTTP request behind the task at all: one process, one client, and
    the stdio name is right."""
    hand_back(await asyncio.to_thread(held, server.tunnels, "stdio", "kelly", "control"))
    async with asyncio.timeout(SOON), Client(server.mcp) as client:
        result = await (await call_tool_task(client, "await_control")).result()
    assert result.structured_content["handed_back"] is True
    assert result.structured_content["control"]["who"] == ["kelly"]


async def test_every_task_tool_is_carried_and_no_tool_hears_a_client_s_header(server):
    """Which calls carry is read off each tool's own task config, so a task
    tool not marked `waits` cannot let a client's header into its worker; and
    the header a client sent is dropped from every call, task or not."""

    @server.mcp.tool(task=waiting.TASK)
    async def whose() -> dict:
        return {"workspace": clients_module.caller().name}

    @server.mcp.tool
    def heard() -> dict:
        return {"carried": get_http_request().headers.get(clients_module.CARRIED)}

    async with over_http(
        server,
        "http://test/mcp?workspace=w1",
        headers={clients_module.CARRIED: "w2"},
    ) as client:
        tasked = await (await call_tool_task(client, "whose")).result()
        plain = await client.call_tool("heard")
    assert tasked.structured_content == {"workspace": "w1"}
    assert plain.structured_content == {"carried": None}


# ---- the long poll -------------------------------------------------------------


@asynccontextmanager
async def web(server):
    app = server.mcp.http_app()
    async with (
        asyncio.timeout(SOON),
        app.router.lifespan_context(app),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client,
    ):
        yield client


async def test_the_long_poll_answers_the_same_summary(server, monkeypatch):
    tunnel = await hold(server)
    began = entered(server, monkeypatch)
    async with web(server) as client:
        call = asyncio.create_task(
            client.post("/browser/await-control", headers={"X-Workspace": "w1"})
        )
        await began.wait()
        await asyncio.sleep(0.05)
        assert not call.done()
        hand_back(tunnel)
        response = await call
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["handed_back"] is True and summary["ended"] is False
    assert summary["control"]["who"] == ["kelly"]
    assert summary["visited"] == VISITED
    assert summary["page"] == {"url": OUTLINE["url"], "title": "Home"}
    assert "control of this browser" not in response.text  # no notice on HTTP
    assert server.store.get("w1").history == HISTORY


async def test_the_long_poll_answers_at_the_cap(server, monkeypatch):
    await hold(server)
    monkeypatch.setattr(server.settings.live, "await_cap", 0.2)

    def no_thread(*_args, **_kwargs):
        raise AssertionError("the long poll held a worker thread")

    # It waits on the loop: a long poll that held one of the forty worker
    # threads would starve every other call while it waited.
    monkeypatch.setattr(answer_module, "run_in_threadpool", no_thread)
    async with web(server) as client:
        response = await client.post(
            "/browser/await-control", headers={"X-Workspace": "w1"}
        )
    assert response.status_code == 200, response.text
    assert response.json()["handed_back"] is False
    assert response.json()["message"] == "still in kelly's control; call again."


# ---- what it is not -------------------------------------------------------------


async def test_it_is_not_a_flow_step(server):
    tools = {}
    for name in sorted(set(ENDPOINTS.values())):
        tools[name] = (await server.mcp.get_tool(name)).parameters or {}
    document = {"description": "Wait", "steps": [{"tool": "await_control"}]}
    with pytest.raises(InvalidFlow, match="await_control is not a step") as refused:
        validate(document, step_schemas(tools))
    assert "A flow cannot wait for a person" in str(refused.value)
    assert "await_control" not in step_schemas(tools)


def test_the_cap_is_at_least_thirty():
    assert load([], {}).settings.live.await_cap == 90
    assert load([], {"LIVE_AWAIT_CAP": "30"}).settings.live.await_cap == 30
    with pytest.raises(ConfigError, match=r"live\.await_cap"):
        load([], {"LIVE_AWAIT_CAP": "10"})
