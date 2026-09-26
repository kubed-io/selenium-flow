"""The server keeps answering while one caller's browser is busy.

Not a flow, and the one exception to the rule in ``conftest.py``: what breaks
here is not something a flow can see. Every `/browser/*` action used to run on
the event loop, so one `assert` waiting minutes stalled MCP, the admin page and
`/health` until it finished (§F4.19). The unit suite proves the calls leave the
loop; this proves the property a person feels, against the real server, Grid
and Redis, with MCP and the admin API asked at the same time.

The timings are written to ``PROFILE_DIR`` when it is set, for the job summary.
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from .conftest import TOKEN

pytestmark = pytest.mark.integration

SESSION = "responsive"
# Long enough that a stalled loop cannot hide inside it, short enough to keep
# the job quick. An assert that can never pass waits exactly this long.
BUSY_SECONDS = 8
# Generous: a shared runner is slow, and a stalled loop is BUSY_SECONDS.
BUDGET_SECONDS = 1.0
HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Session-Key": SESSION}


def _request(url: str, method: str = "GET", body: dict | None = None) -> float:
    """Seconds one request took. Its answer is not the point; any is fine."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=BUSY_SECONDS * 4) as answer:
            answer.read()
    except urllib.error.HTTPError:
        pass  # a failed assert is a 4xx, and that is what this one is for
    return time.perf_counter() - started


async def _list_tools(server: str) -> float:
    transport = StreamableHttpTransport(f"{server}/mcp", headers=HEADERS)
    started = time.perf_counter()
    async with Client(transport) as client:
        await client.list_tools()
    return time.perf_counter() - started


def _write_timings(busy: float, timings: dict[str, list[float]]) -> None:
    directory = os.environ.get("PROFILE_DIR")
    if not directory:
        return
    rows = "\n".join(
        f"| {name} | {len(values)} | {max(values) * 1000:.0f} | "
        f"{sorted(values)[len(values) // 2] * 1000:.0f} |"
        for name, values in timings.items()
    )
    Path(directory).mkdir(parents=True, exist_ok=True)
    Path(directory, "responsiveness.md").write_text(
        f"### While one browser was busy for {busy:.1f}s\n\n"
        "| Request | Count | Worst (ms) | Median (ms) |\n"
        "|---|---|---|---|\n"
        f"{rows}\n"
    )


async def test_the_server_answers_while_a_browser_is_busy(server):
    await asyncio.to_thread(_request, f"{server}/browser", "POST", {})
    try:
        busy = asyncio.create_task(
            asyncio.to_thread(
                _request,
                f"{server}/browser/assert",
                "POST",
                {"script": "return false", "wait_timeout": BUSY_SECONDS},
            )
        )
        await asyncio.sleep(1)  # let the assert start waiting
        timings: dict[str, list[float]] = {
            "GET /health": [],
            "GET /admin/sessions": [],
            "MCP tools/list": [],
        }
        while not busy.done() and len(timings["GET /health"]) < 10:
            health, admin, tools = await asyncio.gather(
                asyncio.to_thread(_request, f"{server}/health"),
                asyncio.to_thread(_request, f"{server}/admin/sessions"),
                _list_tools(server),
            )
            timings["GET /health"].append(health)
            timings["GET /admin/sessions"].append(admin)
            timings["MCP tools/list"].append(tools)
            await asyncio.sleep(0.3)
        took = await busy
    finally:
        await asyncio.to_thread(_request, f"{server}/browser", "DELETE")

    _write_timings(took, timings)
    assert took >= BUSY_SECONDS - 3, "the busy call was not busy; this proves nothing"
    assert timings["GET /health"], "nothing was asked while the browser was busy"
    worst = {name: max(values) for name, values in timings.items()}
    assert all(t < BUDGET_SECONDS for t in worst.values()), worst
