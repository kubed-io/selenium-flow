"""One broadcaster behind every open admin page.

The session list is computed once a tick however many pages are connected, and
only while one is. What each page sees is what it saw when every page polled on
its own: its first event on connect, one event per change, none when nothing
changed, and a failed tick that says nothing.

Ticks are paced by the test where a count must be exact: `grid.status` blocks on
a semaphore, so "N entered" means the Nth tick is under way.
"""

import asyncio
import json
import threading

import pytest

from kubed.selenium_flow.http.admin import sessions as admin
from kubed.selenium_flow.session.store import SessionRecord

from .conftest import TOKEN
from .test_files_and_admin import _listen, _until

pytestmark = pytest.mark.unit


async def _request(app, method, path):
    """One plain request over raw ASGI, on the loop the streams run on."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return json.loads(body)


@pytest.fixture
def gated(server, monkeypatch):
    """`grid.status` that blocks until the test lets it through, and counts."""
    monkeypatch.setattr(admin, "POLL_SECONDS", 0.05)
    gate = threading.Semaphore(0)
    entered = []

    def status():
        entered.append(1)
        gate.acquire()
        return {"value": {"nodes": []}}

    monkeypatch.setattr(server.actions.grid, "status", status)
    yield gate, entered
    gate.release(1000)


@pytest.fixture
def counted(server, monkeypatch):
    """`grid.status` that answers at once, and counts."""
    calls = []

    def status():
        calls.append(1)
        return {"value": {"nodes": []}}

    monkeypatch.setattr(server.actions.grid, "status", status)
    return calls


async def _close(stop, tasks, gate=None):
    """Close every page. The gate opens first: a page stuck behind it would
    hold the close, and a failing test would hang rather than fail."""
    stop.set()
    if gate is not None:
        gate.release(1000)
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)


async def test_three_pages_cost_one_grid_listing_a_tick(server, gated):
    gate, entered = gated
    server.sessions.store.set("one", SessionRecord(session_id=""))
    app = server.mcp.http_app()
    stop = asyncio.Event()
    sinks = [[], [], []]
    tasks = [asyncio.create_task(_listen(app, sink, stop)) for sink in sinks]
    try:
        for tick in (1, 2, 3):
            await _until(lambda t=tick: len(entered) >= t, f"tick {tick}")
            assert len(entered) == tick, "one Grid listing per tick, not per page"
            gate.release(1)
        await _until(lambda: len(entered) >= 4, "the fourth tick")
        assert len(entered) == 4
        assert all(len(s) == 1 for s in sinks) and sinks[0] == sinks[1] == sinks[2]
    finally:
        await _close(stop, tasks, gate)


async def test_nothing_polls_once_the_last_page_has_gone(server, counted, monkeypatch):
    monkeypatch.setattr(admin, "POLL_SECONDS", 0.02)
    app = server.mcp.http_app()
    stop = asyncio.Event()
    sinks = [[], []]
    tasks = [asyncio.create_task(_listen(app, sink, stop)) for sink in sinks]
    await _until(lambda: len(counted) >= 3, "a few ticks")
    await _close(stop, tasks)

    settled = len(counted)
    await asyncio.sleep(0.2)  # ten ticks' worth
    assert len(counted) == settled, "the broadcaster outlived its pages"

    # And the next page starts it again.
    again, stop = [], asyncio.Event()
    task = asyncio.create_task(_listen(app, again, stop))
    try:
        await _until(lambda: len(again) == 1, "the first event after a restart")
        assert len(counted) > settled
    finally:
        await _close(stop, [task])


async def test_a_page_joining_mid_tick_is_sent_each_state_once(
    server, gated, monkeypatch
):
    """Joining while a tick is under way neither loses that tick's payload nor
    sends a page the same state twice."""
    monkeypatch.setattr(admin, "FRESH_SECONDS", 60.0)
    gate, entered = gated
    server.sessions.store.set("one", SessionRecord(session_id=""))
    app = server.mcp.http_app()
    stop = asyncio.Event()
    first, second, third = [], [], []
    tasks = [asyncio.create_task(_listen(app, first, stop))]
    try:
        # Nothing broadcast yet: the joiner gets the tick under way.
        await _until(lambda: len(entered) >= 1, "tick 1")
        tasks.append(asyncio.create_task(_listen(app, second, stop)))
        await asyncio.sleep(0.05)
        gate.release(1)
        await _until(lambda: len(first) == len(second) == 1, "tick 1's event")
        assert first == second

        # A recent broadcast: the joiner is sent it at once, and the tick under
        # way, having found the same state, sends it nothing more.
        await _until(lambda: len(entered) >= 2, "tick 2")
        tasks.append(asyncio.create_task(_listen(app, third, stop)))
        await _until(lambda: len(third) == 1, "the joiner's first event")
        assert third == first
        gate.release(1)
        await _until(lambda: len(entered) >= 3, "tick 3")
        assert len(entered) == 3, "a fresh join is not a Grid call"

        # Tick 3 read the store before it blocked; the change lands in tick 4.
        server.sessions.store.set("two", SessionRecord(session_id=""))
        gate.release(1)
        await _until(lambda: len(entered) >= 4, "tick 4")
        gate.release(1)
        await _until(lambda: all(len(s) == 2 for s in (first, second, third)), "2")
        assert first == second == third
        assert first[0] != first[1]
    finally:
        await _close(stop, tasks, gate)


async def test_the_listing_is_the_last_broadcast_while_it_is_fresh(
    server, counted, monkeypatch
):
    monkeypatch.setattr(admin, "POLL_SECONDS", 60.0)
    monkeypatch.setattr(admin, "FRESH_SECONDS", 60.0)
    server.sessions.store.set("one", SessionRecord(session_id="abc"))
    app = server.mcp.http_app()
    stop = asyncio.Event()
    sink = []
    task = asyncio.create_task(_listen(app, sink, stop))
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        asked = len(counted)

        listing = await _request(app, "GET", "/admin/sessions")
        assert len(counted) == asked, "a fresh broadcast was computed again"
        assert {"sessions": listing["sessions"]} == json.loads(sink[0])

        # Ending a browser changes the list, so the listing after it is new.
        def end_browser(caller):
            server.sessions.store.set(caller.name, SessionRecord(session_id=""))

        monkeypatch.setattr(server.sessions, "end_browser", end_browser)
        await _request(app, "DELETE", "/admin/sessions/one")
        listing = await _request(app, "GET", "/admin/sessions")
        assert len(counted) == asked + 1
        assert listing["sessions"][0]["attached"] is False

        # And a stale one is never served.
        monkeypatch.setattr(admin, "FRESH_SECONDS", 0.0)
        await _request(app, "GET", "/admin/sessions")
        assert len(counted) == asked + 2
    finally:
        await _close(stop, [task])


async def test_a_failed_tick_sends_nothing_and_the_stream_carries_on(
    server, counted, monkeypatch
):
    monkeypatch.setattr(admin, "POLL_SECONDS", 0.02)
    server.sessions.store.set("one", SessionRecord(session_id=""))
    failures = []
    real = admin.owner_label

    def owner_label(key):
        if len(failures) < 2:
            failures.append(key)
            raise RuntimeError("blip")
        return real(key)

    monkeypatch.setattr(admin, "owner_label", owner_label)
    app = server.mcp.http_app()
    stop = asyncio.Event()
    sink = []
    task = asyncio.create_task(_listen(app, sink, stop))
    try:
        await _until(lambda: len(sink) == 1, "the event after the blips")
        assert len(failures) == 2
        assert [r["key"] for r in json.loads(sink[0])["sessions"]] == ["one"]
    finally:
        await _close(stop, [task])
