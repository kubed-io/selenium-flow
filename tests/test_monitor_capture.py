"""The monitor holds a capture's connection: attached before the first page,
closed clean at the deadline, held again with ``again`` after it, backed off
when it cannot open, and leaking nothing when cancelled (capture spec, §2)."""

import asyncio

import pytest

from kubed.selenium_flow.faults import BidiUnavailable
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
                   connect=FakeConnection, sink=sink, clock=clock)


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


async def test_reconnects_back_off_one_two_four_to_thirty(running_monitor, clock):
    monitor, _ = running_monitor
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
    await asyncio.to_thread(calls_for(monitor, clock).begin, "ws1", GID)
    clock.now += 2
    await monitor.look()
    assert monitor.capturing(GID)


async def test_the_deadline_never_closes_under_a_call_and_counts_from_its_end(running_monitor, clock):
    monitor, _ = running_monitor
    calls = calls_for(monitor, clock)
    await asyncio.to_thread(monitor.attach, "ws1", GID, "capture", grid_timeout=300)
    begun = await asyncio.to_thread(calls.begin, "ws1", GID)
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
    begun = await asyncio.to_thread(calls.begin, "ws1", GID)
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
    first = await asyncio.to_thread(calls.begin, "ws1", GID)
    second = await asyncio.to_thread(calls.begin, "ws1", GID)
    await asyncio.to_thread(calls.end, first, tool="extract", surface="mcp", browser="")
    clock.now += 900
    await monitor.look()
    assert monitor.capturing(GID)
    await asyncio.to_thread(calls.end, second, tool="extract", surface="mcp", browser="")
    clock.now += 301
    await monitor.look()
    assert not monitor.capturing(GID)


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
        begun = await asyncio.to_thread(calls.begin, "ws1", GID)
        await asyncio.to_thread(calls.end, begun, tool="extract", surface="mcp", browser="")
    assert len(FakeConnection.made) == 2  # the hold, and one failed resume
    assert monitor.owes(GID, "capture")
    FakeConnection.refuse = False
    clock.now += 1
    await asyncio.to_thread(calls.begin, "ws1", GID)
    assert len(FakeConnection.made) == 3 and monitor.capturing(GID)
