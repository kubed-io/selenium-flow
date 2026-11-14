"""The monitor: watches by the Grid's listing, holds connections to a deadline."""

import asyncio
import inspect
import logging
from typing import ClassVar

import pytest

from kubed.selenium_flow.monitor.events import LocalBus
from kubed.selenium_flow.monitor.monitor import (
    DEFAULT_TIMEOUT,
    KEEPALIVE,
    LISTING_TICK,
    MISSES,
    Monitor,
    ping_interval,
)
from kubed.selenium_flow.workspace.calls import Calls

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"
OTHER = "0123456789abcdef0123456789abcdef"


class Clock:
    def __init__(self, now=1_791_500_000.0):
        self.now = now

    def __call__(self):
        return self.now


class Listing:
    """``GET /status`` as `Grid.listing` returns it, counting calls."""

    def __init__(self, running=None, nodes=1, fails=False):
        self.running = dict(running or {})
        self.nodes = nodes
        self.fails = fails
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.fails:
            raise ConnectionError("grid down")
        return self.nodes, dict(self.running)


class FakeSubscriber:
    """What `FakeConnection.listen` returns: a handler for some events."""

    def __init__(self, events, handler, on_drop=None):
        self.events, self.handler, self.on_drop = frozenset(events), handler, on_drop
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeConnection:
    """Stands in for `bidi.Connection`: records what the monitor asked of it.

    ``refuse`` makes every open raise ConnectionError, ``hang_on_subscribe``
    makes every subscribe and ``hang_on_open`` every open wait until ``gate``
    is set (forever without one); all are reset per test (`reset`)."""

    made: ClassVar[list] = []
    refuse: ClassVar[bool] = False
    hang_on_subscribe: ClassVar[bool] = False
    hang_on_open: ClassVar[bool] = False
    gate: ClassVar[asyncio.Event | None] = None

    def __init__(self, url, *, on_close=None):
        self.url, self.on_close = url, on_close
        self.is_open = False
        self.dropped = False
        self.subscribed = []
        self.collectors = []
        self.listeners = []
        self.commands = []
        self.flushed = 0
        self.closed = 0
        self.closed_clean = False
        self._subscriptions = set()
        self._collectors = set()
        FakeConnection.made.append(self)

    @classmethod
    def reset(cls):
        cls.made, cls.refuse, cls.hang_on_subscribe = [], False, False
        cls.hang_on_open, cls.gate = False, None

    async def open(self):
        if FakeConnection.hang_on_open:
            await (FakeConnection.gate or asyncio.Event()).wait()
        if FakeConnection.refuse:
            raise ConnectionError("refused")
        self.is_open = True

    async def command(self, method, params=None, *, timeout=5.0):
        self.commands.append((method, params))
        if method == "browsingContext.getTree":
            return {"contexts": [{"context": "top-1"}]}
        return {}

    def listen(self, events, handler, *, name, limit=1000, on_drop=None):
        subscriber = FakeSubscriber(events, handler, on_drop)
        self.listeners.append(subscriber)
        return subscriber

    async def subscribe(self, events, contexts=None):
        if FakeConnection.hang_on_subscribe:
            await (FakeConnection.gate or asyncio.Event()).wait()
        self.subscribed.append(list(events))
        subscription = f"sub-{len(self.subscribed)}"
        self._subscriptions.add(subscription)
        return subscription

    async def add_collector(self, params):
        self.collectors.append(params)
        self._collectors.add("col-1")
        return "col-1"

    async def flush(self):
        self.flushed += 1

    async def close(self):
        if self.is_open and (self._subscriptions or self._collectors):
            self._subscriptions.clear()  # what `Connection.close` unsubscribes
            self._collectors.clear()  # and the collectors it removes
            self.closed_clean = True
        self.is_open = False
        self.closed += 1

    def drop(self):
        self.is_open, self.dropped = False, True
        self.on_close()

    async def emit(self, method, params):
        for subscriber in self.listeners:
            if method in subscriber.events and not subscriber.cancelled:
                result = subscriber.handler(method, params)
                if inspect.isawaitable(result):
                    await result


class FakeSink:
    """Stands in for `Capture`: records every call the monitor makes."""

    def __init__(self):
        self.calls = []

    def attached(self, workspace, session_id, browser, top, *, again):
        self.calls.append(("attached", workspace, session_id, browser, top, again))

    def ingest(self, workspace, session_id, method, params):
        self.calls.append(("ingest", workspace, session_id, method, params))

    def replayed(self, workspace, session_id):
        self.calls.append(("replayed", workspace, session_id))

    def detached(self, workspace, session_id):
        self.calls.append(("detached", workspace, session_id))

    def lost(self, workspace, session_id):
        self.calls.append(("lost", workspace, session_id))


@pytest.fixture
def parts():
    FakeConnection.reset()
    bus, heard = LocalBus(), []
    bus.subscribe(("session.opened", "session.ended"), heard.append, name="t")
    listing, clock = Listing({GID: 300}), Clock()
    monitor = Monitor(
        bus,
        listing=listing,
        socket_url=lambda gid: f"ws://grid/session/{gid}/se/bidi",
        events_for={"capture": ("log.entryAdded",)},
        connect=FakeConnection,
        sinks={"capture": FakeSink()},
        clock=clock,
        tick=0.05,
    )
    return monitor, listing, clock, heard


async def test_opened_is_announced(parts):
    monitor, _listing, _clock, heard = parts
    monitor.opened("bot", GID, "chrome", 300)
    assert [(e.kind, e.workspace, e.grid_timeout, e.reopened) for e in heard] == [
        ("session.opened", "bot", 300, False)
    ]


async def test_one_listing_a_look_however_many_are_watched(parts):
    monitor, listing, _clock, _heard = parts
    listing.running[OTHER] = 600
    monitor.watch(GID, "a", "recording")
    monitor.watch(OTHER, "b", "recording")
    await monitor.look()
    assert listing.calls == 1
    assert monitor.watches[OTHER].timeout == 600


async def test_two_misses_are_gone_once(parts):
    monitor, listing, _clock, heard = parts
    monitor.watch(GID, "bot", "recording")
    listing.running.clear()
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 1
    await monitor.look()
    assert MISSES == 2
    assert [(e.kind, e.cause) for e in heard] == [("session.ended", "gone")]
    assert GID not in monitor.watches
    await monitor.look()
    assert len(heard) == 1


async def test_a_listing_again_resets_the_misses(parts):
    monitor, listing, _clock, heard = parts
    monitor.watch(GID, "bot", "recording")
    listing.running.clear()
    await monitor.look()
    listing.running[GID] = 300
    await monitor.look()
    listing.running.clear()
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 1


@pytest.mark.parametrize(
    "listing",
    [
        Listing({}, nodes=0),
        Listing(fails=True),
    ],
    ids=["no nodes", "failed"],
)
async def test_a_listing_that_cannot_say_says_nothing(parts, listing, caplog):
    monitor, _listing, _clock, heard = parts
    monitor.listing = listing
    monitor.watch(GID, "bot", "recording")
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            await monitor.look()
    assert heard == [] and GID in monitor.watches
    assert GID not in caplog.text


async def test_no_nodes_for_a_whole_idle_timeout_is_a_miss(parts):
    monitor, listing, clock, heard = parts
    monitor.tick = 30
    listing.nodes, listing.running = 0, {}
    monitor.watch(GID, "bot", "recording", grid_timeout=300)
    await monitor.look()  # the run of empty listings starts here
    clock.now += 300 + 30 - 1
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 0
    clock.now += 1
    await monitor.look()
    assert monitor.watches[GID].misses == 1
    clock.now += 30
    await monitor.look()
    assert [(e.kind, e.cause) for e in heard] == [("session.ended", "gone")]


async def test_a_listing_with_nodes_starts_the_empty_run_again(parts):
    monitor, listing, clock, heard = parts
    monitor.tick = 30
    monitor.watch(GID, "bot", "recording")
    listing.nodes, listing.running = 0, {}
    await monitor.look()
    clock.now += 200
    listing.nodes, listing.running = 1, {GID: 300}
    await monitor.look()
    listing.nodes, listing.running = 0, {}
    clock.now += 1
    await monitor.look()  # a new run starts here
    clock.now += 300 + 30 - 1
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 0
    clock.now += 1
    await monitor.look()
    assert monitor.watches[GID].misses == 1


async def test_a_failed_listing_neither_starts_nor_extends_the_empty_run(parts):
    monitor, listing, clock, heard = parts
    monitor.tick = 30
    monitor.watch(GID, "bot", "recording")
    listing.fails = True
    await monitor.look()
    clock.now += 330
    await monitor.look()
    listing.fails, listing.nodes, listing.running = False, 0, {}
    await monitor.look()  # the run starts here, not at the first failure
    assert heard == [] and monitor.watches[GID].misses == 0
    clock.now += 330
    listing.fails = True
    await monitor.look()
    assert monitor.watches[GID].misses == 0
    listing.fails = False
    await monitor.look()
    assert monitor.watches[GID].misses == 1


async def test_a_watch_older_than_its_timeout_is_not_ended_by_one_empty_listing(parts):
    monitor, listing, clock, heard = parts
    monitor.tick = 30
    monitor.watch(GID, "bot", "recording")
    await monitor.look()
    clock.now += 1000  # listed all along; then the hub restarts
    listing.nodes, listing.running = 0, {}
    await monitor.look()
    clock.now += 30
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 0


async def test_a_listing_older_than_the_watch_says_nothing(parts):
    monitor, listing, clock, heard = parts
    listing.running.clear()
    monitor.watch(GID, "bot", "recording")
    clock.now -= 10  # this look's listing was taken before the watch began
    await monitor.look()
    await monitor.look()
    assert heard == [] and monitor.watches[GID].misses == 0


async def test_ended_is_announced_once_and_drops_the_watch(parts):
    monitor, _listing, _clock, heard = parts
    monitor.watch(GID, "bot", "recording")
    assert monitor.ended("bot", GID, "ended") is True
    assert monitor.ended("bot", GID, "gone") is False
    assert [(e.kind, e.cause) for e in heard] == [("session.ended", "ended")]
    assert GID not in monitor.watches
    monitor.watch(GID, "bot", "recording")  # nothing left to owe
    assert GID not in monitor.watches


async def test_release_of_the_last_reason_drops_the_watch(parts):
    monitor, _listing, _clock, _heard = parts
    monitor.watch(GID, "bot", "recording")
    monitor.watch(GID, "bot", "capture")
    monitor.release(GID, "recording")
    assert monitor.watching(GID) == {"capture"}
    monitor.release(GID, "capture")
    assert GID not in monitor.watches and monitor.watching(GID) == frozenset()


async def test_the_task_runs_only_while_something_is_watched(parts):
    monitor, listing, _clock, heard = parts
    await monitor.start()
    assert not monitor.running
    monitor.watch(GID, "bot", "recording")
    assert monitor.running
    listing.running.clear()
    for _ in range(100):
        if not monitor.running:
            break
        await asyncio.sleep(0.01)
    assert not monitor.running and [e.cause for e in heard] == ["gone"]
    await monitor.stop()


async def test_a_watch_before_start_is_taken_up_by_start(parts):
    monitor, _listing, _clock, _heard = parts
    monitor.watch(GID, "bot", "recording")
    await monitor.start()
    assert monitor.running
    await monitor.stop()
    assert not monitor.running


async def test_recording_alone_holds_no_connection(parts):
    monitor, _listing, _clock, _heard = parts
    monitor.watch(GID, "bot", "recording")
    await monitor.look()
    assert FakeConnection.made == [] and not monitor.capturing(GID)


async def test_a_reason_with_events_holds_a_connection_until_the_deadline(parts):
    monitor, _listing, clock, _heard = parts
    monitor.watch(GID, "bot", "capture", grid_timeout=300)
    await monitor.look()
    [conn] = FakeConnection.made
    assert conn.url == f"ws://grid/session/{GID}/se/bidi"
    assert conn.subscribed == [["log.entryAdded"]] and monitor.capturing(GID)
    assert monitor.connection(GID) is conn
    clock.now += 299
    await monitor.look()
    assert monitor.capturing(GID)
    clock.now += 2  # past touched + timeout
    await monitor.look()
    assert not monitor.capturing(GID) and conn.closed == 1
    assert monitor.connection(GID) is None
    assert ("detached", "bot", GID) in monitor.sinks["capture"].calls
    assert GID in monitor.watches  # still watched by the listing


async def test_a_touch_brings_the_connection_back(parts):
    monitor, _listing, clock, _heard = parts
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    clock.now += DEFAULT_TIMEOUT + 1
    await monitor.look()
    assert not monitor.capturing(GID)
    monitor.touch(GID)
    await monitor.look()
    assert monitor.capturing(GID) and len(FakeConnection.made) == 2


async def test_a_dropped_connection_counts_as_a_miss(parts):
    monitor, listing, _clock, heard = parts
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    FakeConnection.made[0].drop()
    await monitor.look()  # still listed: the connection is opened again
    assert heard == [] and monitor.capturing(GID) and len(FakeConnection.made) == 2
    assert [c[0] for c in monitor.sinks["capture"].calls] == [
        "attached", "detached", "attached", "replayed"
    ]
    assert monitor.sinks["capture"].calls[2][-1] is True  # again: the same session's capture
    FakeConnection.made[1].drop()
    listing.running.clear()
    await monitor.look()  # one miss after a drop is enough
    assert [e.cause for e in heard] == ["gone"]


async def test_a_connection_that_cannot_open_is_tried_again_and_logged_once(
    parts, caplog
):
    monitor, _listing, clock, _heard = parts
    FakeConnection.refuse = True
    monitor.watch(GID, "bot", "capture")
    with caplog.at_level(logging.INFO):
        await monitor.look()
        clock.now += 1  # the first retry's back-off
        await monitor.look()
    assert len(FakeConnection.made) == 2 and not monitor.capturing(GID)
    assert caplog.text.count("no BiDi connection for bot") == 1
    assert GID not in caplog.text
    FakeConnection.refuse = False
    clock.now += 2
    await monitor.look()
    assert monitor.capturing(GID)
    assert monitor.watches[GID].failures == 0 and GID not in monitor._failed_logged


async def test_a_watch_ended_during_a_hold_is_not_a_failure(parts, caplog):
    monitor, _listing, _clock, _heard = parts
    FakeConnection.hang_on_subscribe, FakeConnection.gate = True, asyncio.Event()
    monitor.watch(GID, "bot", "capture")
    with caplog.at_level(logging.INFO):
        look = asyncio.create_task(monitor.look())
        await asyncio.sleep(0.01)  # the hold waits in subscribe
        monitor.ended("bot", GID, "ended")
        FakeConnection.gate.set()
        await look
    [conn] = FakeConnection.made
    assert conn.closed and ("detached", "bot", GID) in monitor.sinks["capture"].calls
    assert GID not in monitor._failed_logged and "trying again" not in caplog.text


async def test_ending_closes_the_connection(parts):
    monitor, _listing, _clock, _heard = parts
    await monitor.start()
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    [conn] = FakeConnection.made
    monitor.ended("bot", GID, "ended")
    await asyncio.sleep(0)
    assert conn.closed == 1 and conn.closed_clean
    assert monitor.sinks["capture"].calls[-1] == ("detached", "bot", GID)
    await monitor.stop()


async def test_stop_closes_every_connection(parts):
    monitor, listing, _clock, _heard = parts
    listing.running[OTHER] = 300
    await monitor.start()
    monitor.watch(GID, "a", "capture")
    monitor.watch(OTHER, "b", "capture")
    await monitor.look()
    await monitor.stop()
    assert [c.closed for c in FakeConnection.made] == [1, 1]
    assert not monitor.running


async def test_stop_survives_a_watch_ending_while_it_closes(parts):
    monitor, listing, _clock, _heard = parts
    listing.running[OTHER] = 300
    await monitor.start()
    monitor.watch(GID, "a", "capture")
    monitor.watch(OTHER, "b", "capture")
    await monitor.look()
    first = FakeConnection.made[0]
    close = first.close

    async def close_and_end_the_other():
        await close()
        monitor.ended("b", OTHER, "ended")  # on the loop: drops it at once

    first.close = close_and_end_the_other
    await monitor.stop()
    assert first.closed == 1 and OTHER not in monitor.watches


async def test_stop_right_after_a_wake_still_ends_the_loop(parts):
    # Python 3.11's wait_for loses a cancel that lands once what it waits on
    # is done: a release wakes the loop, and stop's cancel then came back as
    # a normal return, so the loop looked again and stop waited on it for ever.
    monitor, _listing, _clock, _heard = parts
    monitor.tick = 60  # parked in its wait, not mid-look, when the wake lands
    await monitor.start()
    monitor.watch(GID, "a", "recording")
    monitor.watch(GID, "a", "control")
    await asyncio.sleep(0.05)
    monitor.release(GID, "control")
    stopping = asyncio.ensure_future(monitor.stop())
    done, _ = await asyncio.wait({stopping}, timeout=2)
    assert done == {stopping} and not monitor.running


async def test_watch_and_ended_from_worker_threads(parts):
    monitor, _listing, _clock, heard = parts
    await monitor.start()
    await asyncio.to_thread(monitor.watch, GID, "bot", "recording")
    await asyncio.sleep(0.01)
    assert GID in monitor.watches
    await asyncio.to_thread(monitor.ended, "bot", GID, "ended")
    await asyncio.sleep(0.01)
    assert GID not in monitor.watches and [e.cause for e in heard] == ["ended"]
    await monitor.stop()


# ---- the keep-alive (spec 2026-11-14-reopen-notice-keep-alive) -------------


class Pings:
    def __init__(self, answer=True):
        self.sent, self.answer = [], answer

    def __call__(self, gid):
        self.sent.append(gid)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def kept():
    bus, heard = LocalBus(), []
    bus.subscribe(("session.opened", "session.ended"), heard.append, name="t")
    clock, pings = Clock(), Pings()
    monitor = Monitor(bus, listing=Listing({GID: 300}), ping=pings, keep_alive=900,
                      clock=clock, tick=LISTING_TICK)
    monitor.opened("bot", GID, "chrome", 300)
    return monitor, clock, pings, heard


async def at(monitor, clock, seconds):
    clock.now += seconds
    await monitor.look()


async def test_a_ping_inside_the_window_and_none_after_it(kept):  # rulings 3, 4, 19
    monitor, clock, pings, _ = kept
    await at(monitor, clock, 239)
    assert pings.sent == []
    await at(monitor, clock, 1)
    assert pings.sent == [GID]
    await at(monitor, clock, 240)
    await at(monitor, clock, 240)
    assert len(pings.sent) == 3          # 240, 480, 720
    await at(monitor, clock, 240)
    assert len(pings.sent) == 3          # 960: past 900
    assert monitor.watching(GID) == {KEEPALIVE}  # still listed, never pinged


@pytest.mark.parametrize("activity", ["call", "presence"])
async def test_activity_moves_the_window(kept, activity):  # rulings 5, 10
    monitor, clock, pings, _ = kept
    clock.now += 800
    if activity == "call":
        monitor.calling(GID)
        monitor.called(GID)
    else:
        monitor.touch(GID)
    await at(monitor, clock, 840)        # 1640 from the open, 840 from the activity
    assert pings.sent == [GID]


async def test_a_ping_is_never_a_touch(kept):  # ruling 20
    monitor, clock, pings, _ = kept
    touched = monitor.watches[GID].touched
    await at(monitor, clock, 240)
    assert pings.sent == [GID] and monitor.watches[GID].touched == touched


async def test_no_ping_while_a_call_is_in_flight(kept):  # Review Focus 4
    monitor, clock, pings, _ = kept
    monitor.calling(GID)
    await at(monitor, clock, 600)        # inside the window, past the interval
    assert pings.sent == []
    monitor.called(GID)
    await at(monitor, clock, 239)
    assert pings.sent == []
    await at(monitor, clock, 1)
    assert pings.sent == [GID]


async def test_no_ping_after_the_session_ended(kept):  # ruling 6
    monitor, clock, pings, _ = kept
    monitor.ended("bot", GID, "ended")
    await at(monitor, clock, 240)
    assert pings.sent == [] and GID not in monitor.watches


async def test_a_ping_that_finds_it_gone_stops_the_pings_and_ends_nothing(kept):  # ruling 21
    monitor, clock, pings, heard = kept
    pings.answer = False
    await at(monitor, clock, 240)
    await at(monitor, clock, 240)
    assert pings.sent == [GID]
    assert [e.kind for e in heard] == ["session.opened"]


async def test_a_ping_that_fails_is_logged_by_type_and_tried_again(kept, caplog):  # Review Focus 5
    monitor, clock, pings, heard = kept
    caplog.set_level(logging.INFO, logger="kubed.selenium_flow.monitor")
    pings.answer = ConnectionError(f"http://u:pw@hub/session/{GID}/url")
    await at(monitor, clock, 240)
    await at(monitor, clock, 240)
    assert pings.sent == [GID, GID]
    assert "ConnectionError" in caplog.text and GID not in caplog.text and "pw" not in caplog.text
    assert [e.kind for e in heard] == ["session.opened"]


@pytest.mark.parametrize("timeout, interval", [(300, 240), (600, 480), (120, 60), (60, 60), (None, 240)])
def test_the_interval_follows_the_nodes_timeout(timeout, interval):  # rulings 11, 25
    assert ping_interval(timeout) == interval


async def test_the_loop_wakes_when_a_ping_is_due(kept):
    monitor, clock, _, _ = kept
    clock.now += 230
    assert monitor._wait() == pytest.approx(10)


async def test_nothing_is_kept_without_a_ping_or_a_window():  # ruling 12
    for kw in ({}, {"ping": Pings(), "keep_alive": 0}):
        monitor = Monitor(LocalBus(), listing=Listing({GID: 300}), **kw)
        monitor.opened("bot", GID, "chrome", 300)
        assert monitor.watches == {}


async def test_a_call_on_an_unwatched_session_starts_its_keep_alive():  # Review Focus 3, ruling 18
    monitor = Monitor(LocalBus(), listing=Listing({GID: 300}), ping=Pings(), keep_alive=900)
    Calls(monitor=monitor).begin("bot", GID, browser="chrome", tool="extract")
    assert monitor.watching(GID) == {KEEPALIVE}


async def test_the_keep_alive_alone_holds_no_connection():  # Dr K, plan review (11-14)
    FakeConnection.reset()
    clock, pings = Clock(), Pings()
    monitor = Monitor(
        LocalBus(), listing=Listing({GID: 300}), ping=pings, keep_alive=900,
        socket_url=lambda gid: f"ws://grid/session/{gid}/se/bidi",
        events_for={"capture": ("log.entryAdded",)}, connect=FakeConnection,
        sinks={"capture": FakeSink()}, clock=clock, tick=LISTING_TICK,
    )
    monitor.opened("bot", GID, "chrome", 300)
    await at(monitor, clock, 240)
    assert pings.sent == [GID]
    assert FakeConnection.made == [] and monitor.watches[GID].connection is None



async def test_capture_beside_the_keep_alive_lets_go_at_the_nodes_timeout():  # Dr K, rulings 9, 20
    FakeConnection.reset()
    clock, pings, sink = Clock(), Pings(), FakeSink()
    monitor = Monitor(
        LocalBus(), listing=Listing({GID: 300}), ping=pings, keep_alive=900,
        socket_url=lambda gid: f"ws://grid/session/{gid}/se/bidi",
        events_for={"capture": ("log.entryAdded",)}, connect=FakeConnection,
        sinks={"capture": sink}, clock=clock, tick=LISTING_TICK,
    )
    monitor.opened("bot", GID, "chrome", 300)
    monitor.watch(GID, "bot", "capture", grid_timeout=300)
    await at(monitor, clock, 0)
    [conn] = FakeConnection.made
    assert monitor.capturing(GID)
    await at(monitor, clock, 240)        # a ping, which extends no deadline
    assert pings.sent == [GID] and monitor.capturing(GID)
    await at(monitor, clock, 61)         # 301: past the last touch + 300
    assert not monitor.capturing(GID) and conn.closed == 1
    assert ("detached", "bot", GID) in sink.calls
    assert monitor.watching(GID) == {"capture", KEEPALIVE}
    await at(monitor, clock, 179)        # 480: inside the window, the pings go on
    assert pings.sent == [GID, GID]
    assert FakeConnection.made == [conn] and monitor.connection(GID) is None


@pytest.mark.parametrize(
    "given",
    [
        {"events_for": {KEEPALIVE: ("log.entryAdded",)}},
        {"collectors_for": {KEEPALIVE: {"dataTypes": ["response"]}}},
        {"sinks": {KEEPALIVE: FakeSink()}},
    ],
    ids=["events", "collector", "sink"],
)
def test_the_keep_alive_takes_no_events_collector_or_sink(given):  # ruling 18
    with pytest.raises(ValueError, match=KEEPALIVE):
        Monitor(LocalBus(), listing=Listing(), **given)
