"""The monitor: watches by the Grid's listing, holds sockets to a deadline."""

import asyncio
import logging
from typing import ClassVar

import pytest

from kubed.selenium_flow.monitor.events import LocalBus
from kubed.selenium_flow.monitor.monitor import DEFAULT_TIMEOUT, MISSES, Monitor

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


class FakeSocket:
    """Stands in for `BidiSocket`: records what the monitor asked of it."""

    made: ClassVar[list] = []

    def __init__(self, url, *, on_event=None, on_close=None):
        self.url, self.on_event, self.on_close = url, on_event, on_close
        self.is_open = False
        self.dropped = False
        self.subscribed = []
        self.closed = 0
        self.fail_open = False
        FakeSocket.made.append(self)

    async def open(self):
        if self.fail_open:
            raise OSError("refused")
        self.is_open = True

    async def subscribe(self, events):
        self.subscribed.append(list(events))
        return "sub-1"

    async def close(self):
        self.is_open = False
        self.closed += 1

    def drop(self):
        self.is_open, self.dropped = False, True
        self.on_close()


@pytest.fixture
def parts():
    FakeSocket.made = []
    bus, heard = LocalBus(), []
    bus.subscribe(("session.opened", "session.ended"), heard.append, name="t")
    listing, clock = Listing({GID: 300}), Clock()
    monitor = Monitor(
        bus,
        listing=listing,
        socket_url=lambda gid: f"ws://grid/session/{gid}/se/bidi",
        events_for={"capture": ("log.entryAdded",)},
        connect=FakeSocket,
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


async def test_recording_alone_holds_no_socket(parts):
    monitor, _listing, _clock, _heard = parts
    monitor.watch(GID, "bot", "recording")
    await monitor.look()
    assert FakeSocket.made == [] and not monitor.socket_held(GID)


async def test_a_reason_with_events_holds_a_socket_until_the_deadline(parts):
    monitor, _listing, clock, _heard = parts
    monitor.watch(GID, "bot", "capture", grid_timeout=300)
    await monitor.look()
    [sock] = FakeSocket.made
    assert sock.url == f"ws://grid/session/{GID}/se/bidi"
    assert sock.subscribed == [["log.entryAdded"]] and monitor.socket_held(GID)
    clock.now += 299
    await monitor.look()
    assert monitor.socket_held(GID)
    clock.now += 2  # past touched + timeout
    await monitor.look()
    assert not monitor.socket_held(GID) and sock.closed == 1
    assert GID in monitor.watches  # still watched by the listing


async def test_a_touch_brings_the_socket_back(parts):
    monitor, _listing, clock, _heard = parts
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    clock.now += DEFAULT_TIMEOUT + 1
    await monitor.look()
    assert not monitor.socket_held(GID)
    monitor.touch(GID)
    await monitor.look()
    assert monitor.socket_held(GID) and len(FakeSocket.made) == 2


async def test_a_dropped_socket_counts_as_a_miss(parts):
    monitor, listing, _clock, heard = parts
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    FakeSocket.made[0].drop()
    await monitor.look()  # still listed: the socket is opened again
    assert heard == [] and monitor.socket_held(GID) and len(FakeSocket.made) == 2
    FakeSocket.made[1].drop()
    listing.running.clear()
    await monitor.look()  # one miss after a drop is enough
    assert [e.cause for e in heard] == ["gone"]


async def test_a_socket_that_cannot_open_is_tried_again_and_logged_once(parts, caplog):
    monitor, _listing, _clock, _heard = parts
    original = FakeSocket.open

    async def refused(self):
        raise OSError("refused")

    FakeSocket.open = refused
    try:
        monitor.watch(GID, "bot", "capture")
        with caplog.at_level(logging.INFO):
            await monitor.look()
            await monitor.look()
    finally:
        FakeSocket.open = original
    assert len(FakeSocket.made) == 2 and not monitor.socket_held(GID)
    assert caplog.text.count("no BiDi socket for bot") == 1
    assert GID not in caplog.text
    await monitor.look()
    assert monitor.socket_held(GID)


async def test_ending_closes_the_socket(parts):
    monitor, _listing, _clock, _heard = parts
    await monitor.start()
    monitor.watch(GID, "bot", "capture")
    await monitor.look()
    [sock] = FakeSocket.made
    monitor.ended("bot", GID, "ended")
    await asyncio.sleep(0)
    assert sock.closed == 1
    await monitor.stop()


async def test_stop_closes_every_socket(parts):
    monitor, listing, _clock, _heard = parts
    listing.running[OTHER] = 300
    await monitor.start()
    monitor.watch(GID, "a", "capture")
    monitor.watch(OTHER, "b", "capture")
    await monitor.look()
    await monitor.stop()
    assert [s.closed for s in FakeSocket.made] == [1, 1]
    assert not monitor.running


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
