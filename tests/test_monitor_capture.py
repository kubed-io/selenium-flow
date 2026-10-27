"""The monitor holds a capture's connection: attached before the first page,
closed clean at the deadline, held again with ``again`` after it, backed off
when it cannot open, and leaking nothing when cancelled (capture spec, §2)."""

import asyncio
import logging

import pytest

from kubed.selenium_flow import capture as capture_module
from kubed.selenium_flow.capture.store import Capture
from kubed.selenium_flow.faults import BidiUnavailable
from kubed.selenium_flow.live import pages as pages_module
from kubed.selenium_flow.live.pages import Pages
from kubed.selenium_flow.monitor import monitor as monitor_module
from kubed.selenium_flow.monitor.events import LocalBus
from kubed.selenium_flow.monitor.monitor import Monitor
from kubed.selenium_flow.workspace.calls import Calls

from .test_monitor import GID, Clock, FakeConnection, FakeSink

pytestmark = pytest.mark.unit

EVENTS = ("log.entryAdded", "network.beforeRequestSent")
COLLECTOR = {"dataTypes": ["response"], "maxEncodedDataSize": 10_000_000, "collectorType": "blob"}


def monitor_for(sink, clock):
    return Monitor(LocalBus(), listing=lambda: (1, {GID: 300}), socket_url=lambda s: f"ws://x/{s}",
                   events_for={"capture": EVENTS}, collectors_for={"capture": COLLECTOR},
                   connect=FakeConnection, sinks={"capture": sink}, clock=clock)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def running_monitor(clock):
    FakeConnection.reset()
    sink = FakeSink()
    monitor = monitor_for(sink, clock)
    monitor.bus.start()
    await monitor.start()
    try:
        yield monitor, sink
    finally:
        await monitor.stop()
        await monitor.bus.stop()
        FakeConnection.reset()


async def test_attach_holds_subscribes_and_collects_before_returning(running_monitor):
    monitor, sink = running_monitor
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", browser="chrome")
    [conn] = FakeConnection.made
    assert conn.subscribed == [list(EVENTS)] and conn.collectors == [COLLECTOR]
    assert sink.calls[0] == ("attached", "ws1", GID, "chrome", "top-1", False)
    assert monitor.capturing(GID)


async def test_an_event_the_capture_listener_drops_is_told_to_the_sink(running_monitor):
    monitor, sink = running_monitor
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture")
    [listener] = FakeConnection.made[0].listeners
    listener.on_drop()
    assert sink.calls[-1] == ("lost", "ws1", GID)


async def test_a_failed_attach_raises_503_and_holds_nothing(running_monitor):
    monitor, sink = running_monitor
    FakeConnection.refuse = True
    with pytest.raises(BidiUnavailable, match="/se/bidi"):
        await asyncio.to_thread(monitor.attach, "ws1", GID, "capture")
    assert not monitor.capturing(GID) and monitor.watching(GID) == frozenset()
    assert ("detached", "ws1", GID) in sink.calls


async def test_the_deadline_closes_clean_and_a_touch_reattaches_as_again(running_monitor, clock):
    monitor, sink = running_monitor
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    clock.now += 301
    await monitor.look()
    assert not monitor.capturing(GID) and FakeConnection.made[0].closed_clean
    monitor.touch(GID)
    await monitor.look()
    assert monitor.capturing(GID)
    assert sink.calls[-2:] == [("attached", "ws1", GID, "", "top-1", True), ("replayed", "ws1", GID)]


async def test_past_the_deadline_capture_is_still_owed_and_an_attach_holds_it_again(running_monitor, clock):
    """What a call's start does after an agent's pause (`Calls.begin`): the
    watch still owes capture, and attaching holds it again, replay aligned."""
    monitor, sink = running_monitor
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", browser="chrome", grid_timeout=300)
    clock.now += 301
    await monitor.look()
    assert monitor.owes(GID, "capture") and not monitor.capturing(GID)
    assert not monitor.owes(GID, "other") and not monitor.owes("nobody", "capture")
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", browser="chrome")
    assert monitor.capturing(GID) and len(FakeConnection.made) == 2
    assert sink.calls[-2:] == [("attached", "ws1", GID, "chrome", "top-1", True), ("replayed", "ws1", GID)]


async def test_a_reason_added_later_subscribes_what_it_adds(running_monitor):
    monitor, _ = running_monitor
    monitor.events_for["other"] = ("browsingContext.load",)
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture")
    monitor.watch(GID, "ws1", "other")
    await monitor.look()
    assert FakeConnection.made[0].subscribed[-1] == ["browsingContext.load"]


async def test_reconnects_back_off_one_two_four_to_thirty(running_monitor, clock, monkeypatch):
    """The schedule of this test's own looks: the monitor's own loop is kept
    out, or its look's hold, failing after the clock moved, backs off from
    then and shifts the schedule (`test_a_looks_failed_hold_backs_off_from_when_it_failed`)."""
    monitor, _ = running_monitor
    monkeypatch.setattr(monitor, "_ensure", lambda: None)  # no background look
    monitor.watch(GID, "ws1", "capture")
    FakeConnection.refuse = True
    tries = []
    for _ in range(8):
        before = len(FakeConnection.made)
        await monitor.look()
        tries.append(len(FakeConnection.made) - before)
        clock.now += 1
    assert tries == [1, 1, 0, 1, 0, 0, 0, 1]   # retries at +1, +2, +4 …


async def test_a_cancelled_hold_leaks_nothing(running_monitor):
    monitor, sink = running_monitor
    FakeConnection.hang_on_subscribe = True
    task = asyncio.create_task(monitor._attach("ws1", GID, "capture", "", None))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert FakeConnection.made[0].closed and ("detached", "ws1", GID) in sink.calls


async def test_the_sink_hears_every_event_with_its_workspace(running_monitor):
    monitor, sink = running_monitor
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture")
    await FakeConnection.made[0].emit("log.entryAdded", {"text": "hi"})
    assert ("ingest", "ws1", GID, "log.entryAdded", {"text": "hi"}) in sink.calls


async def test_an_attach_that_times_out_raises_503_and_lets_go(running_monitor, monkeypatch):
    monitor, sink = running_monitor
    monkeypatch.setattr(monitor_module, "ATTACH_TIMEOUT", 0.05)
    FakeConnection.hang_on_subscribe = True
    with pytest.raises(BidiUnavailable, match="/se/bidi"):
        await asyncio.to_thread(monitor.attach, "ws1", GID, "capture")
    for _ in range(100):  # the cancelled hold cleans up on the loop
        if monitor.watching(GID) == frozenset():
            break
        await asyncio.sleep(0.01)
    assert monitor.watching(GID) == frozenset() and FakeConnection.made[0].closed
    assert ("detached", "ws1", GID) in sink.calls


async def test_attach_before_start_is_a_runtime_error(clock):
    monitor = monitor_for(FakeSink(), clock)
    with pytest.raises(RuntimeError, match="not running"):
        await asyncio.to_thread(monitor.attach, "ws1", GID, "capture")


# ---- a call in flight (PR review I1) ------------------------------------------


class Marks:
    """A capture that marks every call, so `Calls` resumes a lapsed hold."""

    def mark(self, workspace):
        return None


def calls_for(monitor, clock):
    return Calls(monitor=monitor, capture=Marks(), clock=clock)


async def test_a_call_begun_a_second_before_the_deadline_keeps_its_connection(running_monitor, clock):
    """The reviewer's probe: a call that starts at 299 s is not closed under
    two seconds later, so its late events and its flush still land."""
    monitor, _ = running_monitor
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    clock.now += 299
    await asyncio.to_thread(calls_for(monitor, clock).begin, "ws1", GID, tool="interact")
    clock.now += 2
    await monitor.look()
    assert monitor.capturing(GID)


async def test_the_deadline_never_closes_under_a_call_and_counts_from_its_end(running_monitor, clock):
    monitor, _ = running_monitor
    calls = calls_for(monitor, clock)
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    begun = await asyncio.to_thread(calls.begin, "ws1", GID, tool="interact")
    clock.now += 900  # one long call: an assert that waits, a slow save
    await monitor.look()
    assert monitor.capturing(GID)
    assert monitor._wait() == monitor.tick  # no spin while the call runs on
    await asyncio.to_thread(calls.end, begun, tool="assert", surface="mcp", browser="chrome")
    clock.now += 299
    await monitor.look()
    assert monitor.capturing(GID)
    clock.now += 2
    await monitor.look()
    assert not monitor.capturing(GID) and FakeConnection.made[0].closed_clean


async def test_a_look_before_the_end_reaches_the_loop_does_not_close_a_long_call(
    running_monitor, clock, monkeypatch
):
    """PR review, round 2: a call longer than the timeout ends from a worker
    thread, and a look can run before what `called` posts reaches the loop.
    The count must not drop before the touch lands, or that look closes the
    connection under the call's flush."""
    monitor, _ = running_monitor
    calls = calls_for(monitor, clock)
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    begun = await asyncio.to_thread(calls.begin, "ws1", GID, tool="interact")
    clock.now += 900
    held = []
    # Posted, and the loop has not got to them yet.
    monkeypatch.setattr(monitor, "_post", lambda fn: held.append(fn) or True)
    await asyncio.to_thread(calls.end, begun, tool="assert", surface="mcp", browser="")
    await monitor.look()
    assert monitor.capturing(GID) and not FakeConnection.made[0].closed
    monkeypatch.undo()
    for fn in held:
        fn()
    clock.now += 299
    await monitor.look()
    assert monitor.capturing(GID)
    clock.now += 2
    await monitor.look()
    assert not monitor.capturing(GID)


def test_a_call_that_ends_after_the_loop_closed_is_not_counted_for_ever(clock):
    monitor = monitor_for(FakeSink(), clock)
    loop = asyncio.new_event_loop()
    loop.close()
    monitor._loop = loop  # stopping: the loop is gone before `stop` cleared it
    monitor.calling(GID)
    monitor.called(GID)
    assert monitor._in_flight == {}


async def test_two_calls_in_flight_hold_until_the_last_ends(running_monitor, clock):
    monitor, _ = running_monitor
    calls = calls_for(monitor, clock)
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    first = await asyncio.to_thread(calls.begin, "ws1", GID, tool="interact")
    second = await asyncio.to_thread(calls.begin, "ws1", GID, tool="interact")
    await asyncio.to_thread(calls.end, first, tool="extract", surface="mcp", browser="")
    clock.now += 900
    await monitor.look()
    assert monitor.capturing(GID)
    await asyncio.to_thread(calls.end, second, tool="extract", surface="mcp", browser="")
    clock.now += 301
    await monitor.look()
    assert not monitor.capturing(GID)


async def until(condition, turns=2000):
    for _ in range(turns):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError("never happened")


async def test_a_resume_waiting_on_a_hold_that_fails_does_not_try_again(running_monitor, clock):
    """The flaky back-off test's race, forced: a call's resume decides to hold
    while a look's hold is in flight, waits for it, and that hold fails. The
    back-off it set answers the call too: no second open."""
    monitor, _ = running_monitor
    calls = calls_for(monitor, clock)
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    clock.now += 301
    await monitor.look()  # the deadline let it go
    FakeConnection.refuse, FakeConnection.hang_on_open = True, True
    FakeConnection.gate = asyncio.Event()
    monitor.calling(GID)  # a touch: the woken look holds again, and hangs
    await until(lambda: len(FakeConnection.made) == 2)
    begun = asyncio.ensure_future(asyncio.to_thread(calls.begin, "ws1", GID, tool="interact"))
    await until(lambda: monitor._attaching)  # the resume waits on the hold
    FakeConnection.gate.set()  # the look's hold fails, and backs off
    await begun
    assert len(FakeConnection.made) == 2 and monitor.backing_off(GID)
    monitor.called(GID)


async def test_a_looks_failed_hold_backs_off_from_when_it_failed(clock):
    """The flaky back-off test's cause: a look reads the clock, then waits on
    the Grid's status; a hold it tries after that and fails was backed off
    from the look's start, so a slow listing spent the back-off before it
    began, and the next call held again at once."""
    import threading

    FakeConnection.reset()
    began, answer = threading.Event(), threading.Event()

    def listing():
        began.set()
        answer.wait(5)
        return 1, {GID: 300}

    monitor = Monitor(LocalBus(), listing=listing, socket_url=lambda s: f"ws://x/{s}",
                      events_for={"capture": EVENTS}, collectors_for={"capture": COLLECTOR},
                      connect=FakeConnection, sinks={"capture": FakeSink()}, clock=clock)
    monitor.bus.start()
    await monitor.start()
    try:
        await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
        await asyncio.to_thread(began.wait, 5)       # a look began, and asks the Grid
        FakeConnection.made[0].dropped = True        # meanwhile the connection drops
        FakeConnection.refuse = True
        clock.now += 60                              # and the Grid takes a minute
        answer.set()
        await until(lambda: monitor.watches[GID].failures)
        assert monitor.backing_off(GID)
    finally:
        answer.set()
        await monitor.stop()
        await monitor.bus.stop()
        FakeConnection.reset()


async def test_a_failed_resume_backs_off_instead_of_costing_every_call(running_monitor, clock):
    """A hanging /se/bidi would cost each call ATTACH_TIMEOUT under the
    session's lock; past one failure, the calls skip it until the back-off ends."""
    monitor, _ = running_monitor
    calls = calls_for(monitor, clock)
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    clock.now += 301
    await monitor.look()  # the deadline let it go
    FakeConnection.refuse = True
    for _ in range(3):
        begun = await asyncio.to_thread(calls.begin, "ws1", GID, tool="interact")
        await asyncio.to_thread(calls.end, begun, tool="extract", surface="mcp", browser="")
    assert len(FakeConnection.made) == 2  # the hold, and one failed resume
    assert monitor.owes(GID, "capture")
    FakeConnection.refuse = False
    clock.now += 1
    await asyncio.to_thread(calls.begin, "ws1", GID, tool="interact")
    assert len(FakeConnection.made) == 3 and monitor.capturing(GID)


# ---- control, a second reason with its own sink (live control spec, R7) --------

LOAD = "browsingContext.load"


class HeardCapture(Capture):
    """The real capture, recording what the monitor tells it."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = []

    def attached(self, workspace, session_id, browser, top, *, again):
        self.calls.append(("attached", workspace, session_id))
        super().attached(workspace, session_id, browser, top, again=again)

    def ingest(self, workspace, session_id, method, params):
        self.calls.append(("ingest", method))
        super().ingest(workspace, session_id, method, params)

    def replayed(self, workspace, session_id):
        self.calls.append(("replayed", workspace, session_id))
        super().replayed(workspace, session_id)

    def detached(self, workspace, session_id):
        self.calls.append(("detached", workspace, session_id))
        super().detached(workspace, session_id)

    def lost(self, workspace, session_id):
        self.calls.append(("lost", workspace, session_id))
        super().lost(workspace, session_id)


class PagesSink(FakeSink):
    """Stands in for `Pages`: a sink that asks to hear the subscribe is behind."""

    def subscribed(self, workspace, session_id):
        self.calls.append(("subscribed", workspace, session_id))


@pytest.fixture
async def both(clock):
    """A running monitor with capture's events and control's, each to its sink."""
    FakeConnection.reset()
    capture, pages = HeardCapture(), PagesSink()
    monitor = Monitor(
        LocalBus(), listing=lambda: (1, {GID: 300}), socket_url=lambda s: f"ws://x/{s}",
        events_for={"capture": capture_module.EVENTS, "control": pages_module.EVENTS},
        collectors_for={"capture": capture_module.COLLECTOR},
        connect=FakeConnection, sinks={"capture": capture, "control": pages}, clock=clock,
    )
    monitor.bus.start()
    await monitor.start()
    try:
        yield monitor, capture, pages
    finally:
        await monitor.stop()
        await monitor.bus.stop()
        FakeConnection.reset()


async def test_a_control_only_hold_never_reaches_capture(both):
    monitor, capture, pages = both
    await monitor.attach_here("w1", GID, "control")
    [conn] = FakeConnection.made
    assert conn.subscribed == [sorted(pages_module.EVENTS)] and conn.collectors == []
    assert monitor.connection(GID) is conn
    await conn.emit(LOAD, {"context": "top-1", "url": "https://a.example/"})
    await conn.emit("browsingContext.contextCreated", {"context": "t2", "parent": None})
    assert capture.calls == []
    assert capture.mark("w1") is None and capture.counts("w1") is None  # no buffer opened
    assert not monitor.capturing(GID)
    assert conn.flushed == 1  # what subscribing contextCreated re-sent is behind
    assert pages.calls == [
        ("attached", "w1", GID, "", "top-1", False),
        ("subscribed", "w1", GID),
        ("ingest", "w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"}),
        ("ingest", "w1", GID, "browsingContext.contextCreated", {"context": "t2", "parent": None}),
    ]


async def test_control_added_to_a_capture_hold_subscribes_its_events_on_the_same_connection(both):
    monitor, capture, pages = both
    await asyncio.to_thread(monitor.attach, "w1", GID, "capture", browser="chrome")
    assert FakeConnection.made[0].flushed == 0  # capture's first hold: no flush
    await monitor.attach_here("w1", GID, "control")
    [conn] = FakeConnection.made
    # contextCreated and historyUpdated are capture's already: one subscribe more.
    assert conn.subscribed == [sorted(capture_module.EVENTS), [LOAD]]
    assert monitor.capturing(GID)
    assert pages.calls == [
        ("attached", "w1", GID, "chrome", "top-1", False),
        ("subscribed", "w1", GID),
    ]
    await conn.emit(LOAD, {"context": "top-1", "url": "https://a.example/"})
    assert ("ingest", LOAD) not in capture.calls
    assert pages.calls[-1] == ("ingest", "w1", GID, LOAD, {"context": "top-1", "url": "https://a.example/"})
    history = {"context": "top-1", "url": "https://a.example/#/inbox"}
    await conn.emit("browsingContext.historyUpdated", history)
    assert ("ingest", "browsingContext.historyUpdated") in capture.calls
    assert pages.calls[-1] == ("ingest", "w1", GID, "browsingContext.historyUpdated", history)
    await conn.emit("log.entryAdded", {"level": "info", "text": "hi"})
    assert pages.calls[-1][3] != "log.entryAdded"
    [first, second] = conn.listeners
    second.on_drop()  # control's listener dropped one: control's to count, not capture's
    assert pages.calls[-1] == ("lost", "w1", GID) and ("lost", "w1", GID) not in capture.calls
    first.on_drop()
    assert ("lost", "w1", GID) in capture.calls and pages.calls[-1][0] == "lost"


async def test_releasing_control_keeps_capture_held_and_tells_pages_detached(both):
    monitor, capture, pages = both
    await asyncio.to_thread(monitor.attach, "w1", GID, "capture", browser="chrome")
    await monitor.attach_here("w1", GID, "control")
    [conn] = FakeConnection.made
    monitor.release(GID, "control")
    assert pages.calls[-1] == ("detached", "w1", GID)
    assert monitor.capturing(GID) and not conn.closed
    assert ("detached", "w1", GID) not in capture.calls
    await conn.emit(LOAD, {"context": "top-1", "url": "https://a.example/"})
    assert pages.calls[-1] == ("detached", "w1", GID)  # control hears nothing now
    await monitor.attach_here("w1", GID, "control")  # granted again: told again
    assert pages.calls[-2:] == [
        ("attached", "w1", GID, "chrome", "top-1", False),
        ("subscribed", "w1", GID),
    ]
    assert len(conn.subscribed) == 2  # load is still subscribed


async def test_releasing_control_alone_closes_the_connection_clean(both):
    monitor, capture, pages = both
    await monitor.attach_here("w1", GID, "control")
    [conn] = FakeConnection.made
    monitor.release(GID, "control")
    await until(lambda: conn.closed)
    assert conn.closed_clean and monitor.connection(GID) is None
    assert GID not in monitor.watches
    assert pages.calls[-1] == ("detached", "w1", GID) and capture.calls == []


async def test_a_hold_with_both_owed_tells_each_sink_once(both, clock):
    monitor, capture, pages = both
    await asyncio.to_thread(monitor.attach, "w1", GID, "capture", browser="chrome", grid_timeout=300)
    await monitor.attach_here("w1", GID, "control")
    clock.now += 301
    await monitor.look()  # the deadline lets the connection go: both told
    assert capture.calls[-1] == ("detached", "w1", GID) and pages.calls[-1] == ("detached", "w1", GID)
    monitor.touch(GID)
    await monitor.look()  # held again, for both, as again
    [held_again] = [c for c in pages.calls if c[0] == "attached" and c[-1] is True]
    assert held_again == ("attached", "w1", GID, "chrome", "top-1", True)
    assert capture.calls[-2:] == [("attached", "w1", GID), ("replayed", "w1", GID)]
    assert pages.calls[-2:] == [("replayed", "w1", GID), ("subscribed", "w1", GID)]
    assert monitor.capturing(GID)


async def test_attach_here_that_cannot_hold_is_bidi_unavailable_and_owes_nothing(both, monkeypatch):
    monitor, capture, _pages = both
    FakeConnection.refuse = True
    with pytest.raises(BidiUnavailable):
        await monitor.attach_here("w1", GID, "control")
    assert monitor.watching(GID) == frozenset() and capture.calls == []
    FakeConnection.refuse = False
    monkeypatch.setattr(monitor_module, "ATTACH_TIMEOUT", 0.05)
    FakeConnection.hang_on_subscribe = True
    with pytest.raises(BidiUnavailable):
        await monitor.attach_here("w1", GID, "control")
    await until(lambda: monitor.watching(GID) == frozenset())
    assert FakeConnection.made[-1].closed


async def test_attach_here_on_a_monitor_not_running_is_bidi_unavailable(clock):
    monitor = monitor_for(FakeSink(), clock)
    with pytest.raises(BidiUnavailable):
        await monitor.attach_here("w1", GID, "control")


class Resending(FakeConnection):
    """Re-sends contextCreated, parent null, for a tab already open when it is
    subscribed, as both browsers do (capture spec, measured)."""

    async def subscribe(self, events, contexts=None):
        subscription = await super().subscribe(events, contexts)
        if "browsingContext.contextCreated" in events:
            await self.emit(
                "browsingContext.contextCreated",
                {"context": "old-tab", "parent": None, "url": "https://o.example/"},
            )
        return subscription


async def test_a_tab_open_before_a_control_only_hold_is_not_followed(clock):
    """Ruling 26, through the real `Pages` on a first hold."""
    FakeConnection.reset()
    pages = Pages()
    monitor = Monitor(
        LocalBus(), listing=lambda: (1, {GID: 300}), socket_url=lambda s: f"ws://x/{s}",
        events_for={"control": pages_module.EVENTS}, connect=Resending,
        sinks={"control": pages}, clock=clock,
    )
    pages.connection = monitor.connection
    monitor.bus.start()
    await monitor.start()
    try:
        pages.follow(GID)
        await monitor.attach_here("w1", GID, "control")
        [conn] = FakeConnection.made
        await conn.emit(LOAD, {"context": "old-tab", "url": "https://o.example/next"})
        assert pages.listed(GID) == ([], 0)
        await conn.emit("browsingContext.contextCreated", {"context": "new-tab", "parent": None})
        await conn.emit(LOAD, {"context": "new-tab", "url": "https://n.example/"})
        await conn.emit(LOAD, {"context": "top-1", "url": "https://t.example/"})
        assert [e["url"] for e in pages.listed(GID)[0]] == [
            "https://n.example/", "https://t.example/"
        ]
    finally:
        await monitor.stop()
        await monitor.bus.stop()
        FakeConnection.reset()


async def lapsed(monitor, clock):
    """Capture owed, its connection let go at the deadline."""
    await asyncio.to_thread(monitor.attach, "w1", GID, "capture", grid_timeout=300)
    clock.now += 301
    await monitor.look()
    assert monitor.owes(GID, "capture") and not monitor.capturing(GID)


async def test_a_released_control_attach_is_no_failed_hold(both, clock, caplog, monkeypatch):
    monitor, _capture, _pages = both
    monkeypatch.setattr(monitor, "_ensure", lambda: None)  # no background look
    await lapsed(monitor, clock)
    FakeConnection.hang_on_open = True
    with caplog.at_level(logging.INFO):
        attaching = asyncio.create_task(monitor.attach_here("w1", GID, "control"))
        await until(lambda: len(FakeConnection.made) == 2)
        attaching.cancel()  # the person let go while the attach was under way
        with pytest.raises(asyncio.CancelledError):
            await attaching
    assert FakeConnection.made[1].closed  # what the hold opened is closed
    assert not monitor.backing_off(GID) and monitor.watches[GID].failures == 0
    assert "trying again" not in caplog.text
    assert monitor.owes(GID, "capture") and not monitor.owes(GID, "control")


async def test_a_control_attach_that_times_out_still_backs_off(both, clock, monkeypatch):
    monitor, _capture, _pages = both
    monkeypatch.setattr(monitor, "_ensure", lambda: None)
    await lapsed(monitor, clock)
    monkeypatch.setattr(monitor_module, "ATTACH_TIMEOUT", 0.05)
    FakeConnection.hang_on_open = True
    with pytest.raises(BidiUnavailable):
        await monitor.attach_here("w1", GID, "control")
    assert monitor.backing_off(GID)


async def test_a_resume_that_times_out_still_backs_off(both, clock, monkeypatch):
    monitor, _capture, _pages = both
    monkeypatch.setattr(monitor, "_ensure", lambda: None)
    await lapsed(monitor, clock)
    monkeypatch.setattr(monitor_module, "ATTACH_TIMEOUT", 0.05)
    FakeConnection.hang_on_open = True
    with pytest.raises(BidiUnavailable):
        await asyncio.to_thread(monitor.attach, "w1", GID, "capture", resume=True)
    await until(lambda: monitor.backing_off(GID))
