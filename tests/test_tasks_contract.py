"""What FastMCP's background tasks do, pinned before await_control rests on it.

`await_control` runs as an MCP task (SEP-2663, `fastmcp[tasks]`, Docket in
memory), and its design (spec 2026-10-27-live-control, R1-R7) assumes a handful
of things about FastMCP that its documentation does not promise. Each test here
is one of them, against a toy server, so an upgrade that changes one fails here,
by name, rather than somewhere in await_control.

Over real streamable HTTP (an in-process ASGI app), because headers only exist
there and the worker's lack of a request is the point of one test.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta

import httpx2
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension, call_tool_task
from fastmcp_tasks.models import CreateTaskResult

from kubed.selenium_flow.mcp import clients

pytestmark = pytest.mark.unit

TASK = TaskConfig(mode="optional", poll_interval=timedelta(seconds=1))
# The private header middleware sets at submission (ruling 31), the one
# await_control's worker reads. A client's own value for it must not survive.
CARRIED = clients.CARRIED
# Every test's whole conversation is bounded by this: a task an upgrade leaves
# "working" must fail the test by name, not hang the run.
SOON = 5


@dataclass
class Seen:
    """What the server saw, for the test to read back."""

    returned: list = field(default_factory=list)
    worker: dict = field(default_factory=dict)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)


class Carry(Middleware):
    """Sets CARRIED on the request, then records what `call_next` returned.

    The header goes into `scope["headers"]` in place: Starlette's `Request`
    caches a `Headers` over that very list, and `MutableHeaders(scope=...)`
    copies it, so a header set that way is invisible to `get_http_headers()`.
    """

    def __init__(self, seen: Seen):
        self.seen = seen

    async def on_call_tool(self, context, call_next):
        headers = get_http_request().scope["headers"]
        headers[:] = [h for h in headers if h[0] != CARRIED.encode()]
        headers.append((CARRIED.encode(), b"w1"))
        result = await call_next(context)
        self.seen.returned.append(result)
        return result


def toy() -> tuple[FastMCP, Seen]:
    seen = Seen()
    mcp = FastMCP("toy")
    mcp.add_middleware(Carry(seen))
    mcp.add_extension(TasksExtension(url="memory://"))

    @mcp.tool(task=TASK)
    async def noted(ctx: Context) -> dict:
        try:
            get_http_request()
            seen.worker["request"] = True
        except RuntimeError:
            seen.worker["request"] = False
        seen.worker["headers"] = get_http_headers(include_all=True)
        seen.worker["loop"] = asyncio.get_running_loop()
        seen.worker["background"] = ctx.is_background_task
        return {"handed_back": True, "who": ["kelly"]}

    @mcp.tool(task=TASK)
    async def held() -> dict:
        seen.worker["loop"] = asyncio.get_running_loop()
        seen.started.set()
        try:
            # Bounded too: shutting down, Docket's worker waits for the tasks it
            # is running, so a gate never opened would hang the test's exit.
            async with asyncio.timeout(SOON):
                await seen.gate.wait()
        except asyncio.CancelledError:
            seen.cancelled.set()
            raise
        return {"released": True}

    return mcp, seen


@asynccontextmanager
async def connect(mcp: FastMCP, mode: str = "auto"):
    """A FastMCP client over streamable HTTP to `mcp`, all on this loop.

    Connecting and every call are bounded by SOON. The timeout cancels once,
    so closing after it relies on `held` bounding itself: the lifespan's exit
    waits for a task the worker is still running.
    """
    app = mcp.http_app()

    def factory(headers=None, timeout=None, auth=None, **kwargs):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
            timeout=timeout if timeout is not None else httpx2.Timeout(30),
            **kwargs,
        )

    transport = StreamableHttpTransport(
        "http://test/mcp?workspace=q1",
        headers={"Sf-Workspace": "desk", CARRIED: "spoofed"},
        httpx_client_factory=factory,
    )
    async with (
        asyncio.timeout(SOON),
        app.router.lifespan_context(app),
        Client(transport, mode=mode) as client,
    ):
        yield client


def test_task_on_a_sync_function_is_refused():
    """R6: a task tool must be async, and this repo's tools are sync."""
    mcp = FastMCP("toy")

    def sync() -> dict:
        return {}

    with pytest.raises(ValueError, match="sync function"):
        mcp.tool(task=TASK)(sync)


async def test_call_next_returns_a_create_task_result():
    """R1: middleware wrapping a tasked call gets the task stub, not a ToolResult."""
    mcp, seen = toy()
    async with connect(mcp) as client:
        await (await call_tool_task(client, "noted")).result()
    [returned] = seen.returned
    assert isinstance(returned, CreateTaskResult)
    assert not isinstance(returned, ToolResult)
    assert returned.status == "working"


async def test_a_legacy_client_runs_the_tool_inline():
    """An `initialize` connection never tasks an optional tool."""
    mcp, seen = toy()
    async with connect(mcp, mode="legacy") as client:
        result = await client.call_tool("noted")
    [returned] = seen.returned
    assert isinstance(returned, ToolResult)
    assert result.structured_content == {"handed_back": True, "who": ["kelly"]}
    assert seen.worker["background"] is False
    assert seen.worker["request"] is True


async def test_the_worker_has_no_request_but_the_headers():
    """R3: the worker gets the submission's headers, as middleware left them."""
    mcp, seen = toy()
    async with connect(mcp) as client:
        await (await call_tool_task(client, "noted")).result()
    assert seen.worker["background"] is True
    assert seen.worker["request"] is False
    headers = seen.worker["headers"]
    assert headers[CARRIED] == "w1"  # set by middleware, the client's overwritten
    assert headers["sf-workspace"] == "desk"  # the client's own header crosses
    # No URL is carried, so `?workspace=` is lost to the worker.
    assert "q1" not in str(headers)


async def test_the_worker_runs_on_the_server_loop():
    """An asyncio.Event made on the server's loop wakes the task."""
    mcp, seen = toy()
    async with connect(mcp) as client:
        task = await call_tool_task(client, "held")
        await seen.started.wait()
        seen.gate.set()
        result = await task.result()
    assert result.structured_content == {"released": True}
    assert seen.worker["loop"] is asyncio.get_running_loop()


async def test_structured_content_survives_tasks_get():
    """R2: the tool's dict is the task's result, with no middleware on the way."""
    mcp, _ = toy()
    async with connect(mcp) as client:
        result = await (await call_tool_task(client, "noted")).result()
    assert result.structured_content == {"handed_back": True, "who": ["kelly"]}
    assert result.is_error is False


async def test_the_poll_interval_is_one_second():
    """R4: the client is told to poll every second, not the default five."""
    mcp, seen = toy()
    async with connect(mcp) as client:
        task = await call_tool_task(client, "noted")
        await task.result()
    assert task.create_result.poll_interval_ms == 1000
    assert seen.returned[0].poll_interval_ms == 1000


async def test_cancel_cancels_the_wait():
    """tasks/cancel reaches the waiting tool as CancelledError."""
    mcp, seen = toy()
    async with connect(mcp) as client:
        task = await call_tool_task(client, "held")
        await seen.started.wait()
        await task.cancel()
        await seen.cancelled.wait()
        assert (await task.status()).status == "cancelled"
