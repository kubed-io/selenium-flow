"""One broadcaster behind every open admin page.

The workspace list is computed on a tick that finds it stale (marked since, or
older than the refresh bound) however many pages are connected, and only while
one is. What each page sees is what it saw when every page polled on
its own: its first event on connect, one event per change, none when nothing
changed, and a failed tick that says nothing.

Ticks are paced by the test where a count must be exact: `grid.status` blocks on
a semaphore, so "N entered" means the Nth tick is under way.
"""

import asyncio
import json
import threading
import time

import pytest

from kubed.selenium_flow.http.admin import workspaces as admin
from kubed.selenium_flow.monitor.events import CallFinished
from kubed.selenium_flow.workspace.store import Workspace

from .conftest import TOKEN

pytestmark = pytest.mark.unit


async def _listen(app, sink, stop):
    """One `/admin/events` client, speaking raw ASGI.

    Not a TestClient: its portal buffers a response until the app finishes, and
    this one never does. ``sink`` collects each event's data as it is sent;
    setting ``stop`` is the browser closing the tab.
    """

    async def receive():
        await stop.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            for line in message.get("body", b"").decode().splitlines():
                if line.startswith("data: "):
                    sink.append(line[len("data: "):])

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/admin/events",
            "raw_path": b"/admin/events",
            "root_path": "",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        },
        receive,
        send,
    )


async def _until(condition, what, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.01)


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
    monkeypatch.setattr(admin, "REFRESH_SECONDS", 0.0)  # every tick builds
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


@pytest.fixture
def under_way(monkeypatch):
    """The listings being computed in a worker thread right now. A test that
    closes its pages waits for this to empty before it counts Grid calls: a
    tick started before the last page left may reach the Grid after it."""
    running, used = [], []
    real = admin.run_in_threadpool

    async def tracked(fn, *args, **kwargs):
        running.append(fn)
        used.append(fn)
        try:
            return await real(fn, *args, **kwargs)
        finally:
            running.remove(fn)

    monkeypatch.setattr(admin, "run_in_threadpool", tracked)
    yield running
    assert used, "the broadcast no longer computes through run_in_threadpool"


async def _close(stop, tasks, gate=None):
    """Close every page. The gate opens first: a page stuck behind it would
    hold the close, and a failing test would hang rather than fail."""
    stop.set()
    if gate is not None:
        gate.release(1000)
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)


async def test_every_connected_page_is_sent_the_same_events(server, monkeypatch):
    """One broadcaster sits behind every connection, so three open pages ask the
    Grid once a tick. What must survive is the rest — one payload when something
    changed, none when nothing did, and a late joiner's first event is the state
    as it is now.

    Ticks are paced by the test, not the clock: `grid.status` blocks on a
    semaphore, so "one entered" means the broadcaster is mid-tick and the counts
    are exact on any runner. A tick reads the store *before* it asks the
    Grid, so a change made while a tick is blocked is seen by the one after."""
    monkeypatch.setattr(admin, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(admin, "REFRESH_SECONDS", 0.0)  # every tick builds
    gate = threading.Semaphore(0)
    entered = []

    def status():
        entered.append(1)
        gate.acquire()
        return {"value": {"nodes": []}}

    monkeypatch.setattr(server.actions.grid, "status", status)
    server.workspaces.store.set("one", Workspace(session_id=""))

    app = server.mcp.server.http_app()
    stop = asyncio.Event()
    sinks = [[], [], []]
    tasks = [asyncio.create_task(_listen(app, sink, stop)) for sink in sinks]

    async def tick(number, pages=1):
        """Wait for the `number`th Grid call, then let it through."""
        await _until(lambda: len(entered) >= number, f"Grid call {number}")
        assert len(entered) == number, "one Grid listing per tick, for every page"
        gate.release(pages)

    try:
        await tick(1)  # first tick: the state as it is
        await _until(lambda: all(len(s) == 1 for s in sinks), "the first events")
        assert sinks[0] == sinks[1] == sinks[2]

        await tick(2)  # second tick: nothing changed
        await tick(3)  # reaching the third means the second finished
        assert all(len(s) == 1 for s in sinks), "a tick that found no change spoke"

        # Tick three is blocked having already read the store; this lands in four.
        server.workspaces.store.set("two", Workspace(session_id=""))
        await tick(4)
        await _until(lambda: all(len(s) == 2 for s in sinks), "the change")
        assert sinks[0] == sinks[1] == sinks[2]
        assert sinks[0][0] != sinks[0][1]
        assert [r["key"] for r in json.loads(sinks[0][1])["workspaces"]] != []

        late = []
        tasks.append(asyncio.create_task(_listen(app, late, stop)))
        # The broadcaster is mid-tick again, and one permit lets it through.
        await _until(lambda: len(entered) >= 5, "the next Grid call")
        gate.release(1)
        await _until(lambda: len(late) == 1, "the late joiner's first event")
        assert late[0] == sinks[0][1], "its first event is the state now"
    finally:
        stop.set()
        gate.release(1000)
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)


async def test_nothing_polls_once_the_last_page_has_gone(
    server, counted, under_way, monkeypatch
):
    monkeypatch.setattr(admin, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(admin, "REFRESH_SECONDS", 0.0)  # every tick builds
    app = server.mcp.server.http_app()
    stop = asyncio.Event()
    sinks = [[], []]
    tasks = [asyncio.create_task(_listen(app, sink, stop)) for sink in sinks]
    await _until(lambda: len(counted) >= 3, "a few ticks")
    await _close(stop, tasks)
    await _until(lambda: not under_way, "the tick under way")

    settled = len(counted)
    await asyncio.sleep(0.2)  # ten ticks' worth
    assert len(counted) == settled, "the broadcaster outlived its pages"

    # And the next page starts it again. Its first event can be the recent
    # broadcast handed out on joining, before the new tick reaches the Grid.
    again, stop = [], asyncio.Event()
    task = asyncio.create_task(_listen(app, again, stop))
    try:
        await _until(lambda: len(again) == 1, "the first event after a restart")
        await _until(lambda: len(counted) > settled, "a Grid listing after a restart")
    finally:
        await _close(stop, [task])


async def test_a_page_joining_mid_tick_is_sent_each_state_once(
    server, gated, monkeypatch
):
    """Joining while a tick is under way neither loses that tick's payload nor
    sends a page the same state twice."""
    monkeypatch.setattr(admin, "FRESH_SECONDS", 60.0)
    gate, entered = gated
    server.workspaces.store.set("one", Workspace(session_id=""))
    app = server.mcp.server.http_app()
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
        server.workspaces.store.set("two", Workspace(session_id=""))
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
    server.workspaces.store.set("one", Workspace(session_id="abc"))
    app = server.mcp.server.http_app()
    stop = asyncio.Event()
    sink = []
    task = asyncio.create_task(_listen(app, sink, stop))
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        asked = len(counted)

        listing = await _request(app, "GET", "/admin/workspaces")
        assert len(counted) == asked, "a fresh broadcast was computed again"
        assert {
            key: listing[key] for key in ("workspaces", "live_enabled")
        } == json.loads(sink[0])

        # Ending a browser changes the list, so the listing after it is new.
        def end_browser(caller, *, admin=False):
            server.workspaces.store.set(caller.name, Workspace(session_id=""))

        monkeypatch.setattr(server.workspaces, "end_browser", end_browser)
        await _request(app, "DELETE", "/admin/workspaces/one/session")
        listing = await _request(app, "GET", "/admin/workspaces")
        assert len(counted) == asked + 1
        assert listing["workspaces"][0]["attached"] is False

        # And a stale one is never served: past the bound, or marked since.
        monkeypatch.setattr(admin, "FRESH_SECONDS", 0.0)
        monkeypatch.setattr(admin, "REFRESH_SECONDS", 0.0)
        await _request(app, "GET", "/admin/workspaces")
        assert len(counted) == asked + 2
    finally:
        await _close(stop, [task])


async def test_a_failed_tick_sends_nothing_and_the_stream_carries_on(
    server, counted, monkeypatch
):
    monkeypatch.setattr(admin, "POLL_SECONDS", 0.02)
    server.workspaces.store.set("one", Workspace(session_id=""))
    failures = []
    real = admin.owner_label

    def owner_label(key):
        if len(failures) < 2:
            failures.append(key)
            raise RuntimeError("blip")
        return real(key)

    monkeypatch.setattr(admin, "owner_label", owner_label)
    app = server.mcp.server.http_app()
    stop = asyncio.Event()
    sink = []
    task = asyncio.create_task(_listen(app, sink, stop))
    try:
        await _until(lambda: len(sink) == 1, "the event after the blips")
        assert len(failures) == 2
        assert [r["key"] for r in json.loads(sink[0])["workspaces"]] == ["one"]
    finally:
        await _close(stop, [task])


async def test_a_page_that_drops_mid_send_leaves_at_once(
    server, counted, under_way, monkeypatch
):
    """A client gone while its event is being written leaves a paused generator
    that only the garbage collector would close. The page must not wait for it:
    with the collector off, the polling stops when the response returns."""
    import gc

    monkeypatch.setattr(admin, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(admin, "REFRESH_SECONDS", 0.0)  # every tick builds
    app = server.mcp.server.http_app()
    gone = asyncio.Event()
    never = asyncio.Event()

    async def receive():
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message.get("body", b"").startswith(b"data"):
            gone.set()  # the tab closes while its event is on the wire
            await never.wait()

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/admin/events",
        "raw_path": b"/admin/events", "root_path": "", "query_string": b"",
        "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    enabled = gc.isenabled()
    gc.disable()
    try:
        await asyncio.wait_for(app(scope, receive, send), 5)
        await _until(lambda: not under_way, "the tick under way")
        settled = len(counted)
        await asyncio.sleep(0.2)  # ten ticks' worth
        assert len(counted) == settled, "the Grid is still polled for a closed page"
    finally:
        if enabled:
            gc.enable()


async def test_a_forget_with_a_page_open_is_in_the_next_listing(
    server, counted, monkeypatch
):
    """The admin's own changes are never answered with the list from before
    them, however fresh: Forget here, and End, Clear, keep and delete alike."""
    monkeypatch.setattr(admin, "POLL_SECONDS", 60.0)
    monkeypatch.setattr(admin, "FRESH_SECONDS", 60.0)
    jar = {
        "cookies": [{"name": "a", "value": "1", "domain": "app.example.com",
                     "path": "/"}],
        "origins": {}, "session": {}, "saved_at": 1.0,
    }
    server.workspaces.store.set(
        "one",
        Workspace(session_id="").visited("https://app.example.com/").with_site_data(jar),
    )
    app = server.mcp.server.http_app()
    stop = asyncio.Event()
    sink = []
    task = asyncio.create_task(_listen(app, sink, stop))
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        before = (await _request(app, "GET", "/admin/workspaces"))["workspaces"][0]
        assert before["site_data_count"] == 1

        await _request(app, "DELETE", "/admin/workspaces/one/site-data/app.example.com")
        after = (await _request(app, "GET", "/admin/workspaces"))["workspaces"][0]
        assert after["site_data_count"] == 0
        assert after["site_data_rev"] != before["site_data_rev"]
    finally:
        await _close(stop, [task])


def test_a_session_opening_or_ending_pokes_the_admin_broadcast(monkeypatch):
    """An open page shows a session appear or end now, not a poll later."""
    from kubed.selenium_flow.http.admin.workspaces import Broadcast
    from kubed.selenium_flow.monitor.events import SessionEnded, SessionOpened
    from kubed.selenium_flow.server import SeleniumMCP

    pokes, marks = [], []
    monkeypatch.setattr(Broadcast, "poke", lambda self: pokes.append(self))
    monkeypatch.setattr(Broadcast, "mark", lambda self: marks.append(self))
    server = SeleniumMCP({"grid": {"url": "http://grid.invalid:4444"}})
    gid = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"
    server.bus.publish(SessionOpened("bot", gid, "chrome", 300, False, 1.0))
    server.bus.publish(SessionEnded("bot", gid, "ended", 2.0))
    server.bus.publish(_finished())
    assert len(pokes) == 2 and isinstance(pokes[0], Broadcast)
    assert len(marks) == 1 and marks[0] is pokes[0]


def _finished(workspace="one"):
    return CallFinished(workspace, "", "chrome", "navigate", "mcp", "ok", 1.0, 0.1)


@pytest.fixture
def computed(monkeypatch):
    """Every `workspaces_payload` run through `run_in_threadpool`: the tick's, and an inline
    `GET /admin/workspaces`."""
    runs = []
    real = admin.run_in_threadpool

    async def counting(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "workspaces_payload":
            runs.append(1)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(admin, "run_in_threadpool", counting)
    return runs


def test_an_unchanged_broadcast_is_current_until_the_refresh_bound(monkeypatch):
    monkeypatch.setattr(admin, "FRESH_SECONDS", 0.0)
    b = admin.Broadcast(compute=dict)
    b._latest = (time.monotonic(), {"workspaces": []}, "{}")
    assert b.fresh() == {"workspaces": []} and not b._stale()
    b._latest = (time.monotonic() - admin.REFRESH_SECONDS, {"workspaces": []}, "{}")
    assert b.fresh() is None and b._stale()


def test_a_mark_makes_the_broadcast_stale_without_a_nudge(monkeypatch):
    monkeypatch.setattr(admin, "FRESH_SECONDS", 0.0)
    b = admin.Broadcast(compute=dict)
    b._latest = (time.monotonic(), {"workspaces": []}, "{}")
    b.mark()
    assert b._stale() and b.fresh() is None
    assert not b._nudge.is_set()


def test_a_marked_broadcast_is_still_handed_out_while_fresh():
    b = admin.Broadcast(compute=dict)
    b._latest = (time.monotonic(), {"workspaces": []}, "{}")
    b.mark()
    assert b._stale() and b.fresh() == {"workspaces": []}  # under FRESH_SECONDS


async def _open(server, monkeypatch, poll=0.02, refresh=60.0, pages=1):
    """Start `pages` listeners; returns (app, stop, sinks, tasks)."""
    monkeypatch.setattr(admin, "POLL_SECONDS", poll)
    monkeypatch.setattr(admin, "REFRESH_SECONDS", refresh)
    app = server.mcp.server.http_app()
    stop = asyncio.Event()
    sinks = [[] for _ in range(pages)]
    tasks = [asyncio.create_task(_listen(app, sink, stop)) for sink in sinks]
    return app, stop, sinks, tasks


async def test_a_quiet_list_is_computed_once(server, computed, monkeypatch):
    # 10 ticks with nothing marked: one build, the first.
    server.workspaces.store.set("one", Workspace(session_id=""))
    _app, stop, (sink,), tasks = await _open(server, monkeypatch)
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        await asyncio.sleep(0.2)
        assert len(computed) == 1
    finally:
        await _close(stop, tasks)


async def test_a_changing_route_is_computed_on_the_next_tick(server, computed, monkeypatch):
    jar = {
        "cookies": [{"name": "a", "value": "1", "domain": "app.example.com",
                     "path": "/"}],
        "origins": {}, "session": {}, "saved_at": 1.0,
    }
    server.workspaces.store.set(
        "one",
        Workspace(session_id="").visited("https://app.example.com/").with_site_data(jar),
    )
    app, stop, (sink,), tasks = await _open(server, monkeypatch)
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        await asyncio.sleep(0.1)
        await _request(app, "DELETE", "/admin/workspaces/one/site-data/app.example.com")
        await _until(lambda: len(sink) == 2, "the change")
        await asyncio.sleep(0.2)
        assert len(computed) == 2
        assert json.loads(sink[1])["workspaces"][0]["site_data_count"] == 0
    finally:
        await _close(stop, tasks)


async def test_a_finished_call_is_computed_on_the_next_tick(server, computed, monkeypatch):
    server.workspaces.store.set("one", Workspace(session_id=""))
    _app, stop, (sink,), tasks = await _open(server, monkeypatch)
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        server.workspaces.store.set("two", Workspace(session_id=""))
        server.bus.publish(_finished())
        await _until(lambda: len(sink) == 2, "the change")
        await asyncio.sleep(0.2)
        assert len(computed) == 2
    finally:
        await _close(stop, tasks)


async def test_many_finished_calls_are_one_computation(server, computed, monkeypatch):
    server.workspaces.store.set("one", Workspace(session_id=""))
    _app, stop, (sink,), tasks = await _open(server, monkeypatch, poll=0.4)
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        for _ in range(50):
            server.bus.publish(_finished())
        await asyncio.sleep(0.1)
        assert len(computed) == 1, "a mark nudged the tick"
        await _until(lambda: len(computed) == 2, "the next tick")
        await asyncio.sleep(0.6)
        assert len(computed) == 2
    finally:
        await _close(stop, tasks)


async def test_an_unchanged_list_is_computed_again_past_the_refresh_bound(
    server, computed, monkeypatch
):
    server.workspaces.store.set("one", Workspace(session_id=""))
    _app, stop, (sink,), tasks = await _open(server, monkeypatch, refresh=0.1)
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        await asyncio.sleep(0.35)  # about 17 ticks, about 3 bounds
        assert 2 <= len(computed) <= 5
    finally:
        await _close(stop, tasks)


async def test_a_page_joining_a_quiet_list_is_sent_it_at_once(server, computed, monkeypatch):
    # FRESH_SECONDS stays 1.0: the first build is older than that when the second page joins.
    server.workspaces.store.set("one", Workspace(session_id=""))
    app, stop, (first,), tasks = await _open(server, monkeypatch)
    second = []
    try:
        await _until(lambda: len(first) == 1, "the first event")
        await asyncio.sleep(1.2)
        tasks.append(asyncio.create_task(_listen(app, second, stop)))
        await _until(lambda: len(second) == 1, "the joiner's first event", seconds=0.5)
        assert second == first and len(computed) == 1
    finally:
        await _close(stop, tasks)


async def test_a_mark_during_a_computation_is_not_seen_by_it(server, gated, monkeypatch):
    monkeypatch.setattr(admin, "REFRESH_SECONDS", 60.0)  # over the fixture's 0.0
    gate, entered = gated
    server.workspaces.store.set("one", Workspace(session_id=""))
    app = server.mcp.server.http_app()
    stop = asyncio.Event()
    sink = []
    tasks = [asyncio.create_task(_listen(app, sink, stop))]
    try:
        await _until(lambda: len(entered) >= 1, "build 1 under way")
        server.bus.publish(_finished())
        gate.release(1)
        await _until(lambda: len(entered) >= 2, "build 2, for the mark")
        gate.release(1)
        await asyncio.sleep(0.3)
        assert len(entered) == 2
    finally:
        await _close(stop, tasks, gate)


async def test_a_failed_computation_leaves_the_mark_unseen(server, computed, monkeypatch):
    server.workspaces.store.set("one", Workspace(session_id=""))
    failing = []
    real = admin.owner_label

    def owner_label(key):
        if failing:
            failing.pop()
            raise RuntimeError("blip")
        return real(key)

    monkeypatch.setattr(admin, "owner_label", owner_label)
    _app, stop, (sink,), tasks = await _open(server, monkeypatch)
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        failing.append(True)
        server.workspaces.store.set("two", Workspace(session_id=""))
        server.bus.publish(_finished())
        await _until(lambda: len(sink) == 2, "the change, after the blip")
        await asyncio.sleep(0.2)
        assert len(computed) == 3  # the first, the blip, the retry
    finally:
        await _close(stop, tasks)


async def test_a_finished_call_is_never_answered_from_before_it(server, counted, monkeypatch):
    monkeypatch.setattr(admin, "FRESH_SECONDS", 0.0)
    server.workspaces.store.set("one", Workspace(session_id=""))
    app, stop, (sink,), tasks = await _open(server, monkeypatch, poll=60.0)
    try:
        await _until(lambda: len(sink) == 1, "the first event")
        asked = len(counted)
        await _request(app, "GET", "/admin/workspaces")
        assert len(counted) == asked, "an unchanged list within the bound was computed again"
        server.bus.publish(_finished())
        await _request(app, "GET", "/admin/workspaces")
        assert len(counted) == asked + 1
    finally:
        await _close(stop, tasks)
