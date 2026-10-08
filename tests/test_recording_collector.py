"""The collector files a recording as soon as it is finished, and only then."""

import asyncio
import logging

import pytest

from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.names import RECORDINGS_DIR
from kubed.selenium_flow.recordings import collector as collector_module
from kubed.selenium_flow.recordings import mp4

pytestmark = pytest.mark.unit

GID = "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d"
BODY = b"\x00\x00\x00\x10moof" + b"\x00" * 100


class Clock:
    def __init__(self, now=1_791_500_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def parts(tmp_path):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    alive = {GID}
    filed = []
    clock = Clock()
    c = collector_module.Collector(
        store, inbox, live=lambda: set(alive), wait=600, polling=True,
        poll_ms=50, on_filed=lambda: filed.append(1), clock=clock, tick=0.1,
        idle_after=60.0,
    )
    return c, store, inbox, alive, filed, clock


async def test_nothing_is_filed_while_the_file_grows(parts):
    c, store, inbox, _alive, filed, _clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    await c.sweep()
    assert store.files("bot", RECORDINGS_DIR) == [] and filed == []


async def test_a_file_that_ends_in_mfro_is_filed_and_its_note_goes(parts):
    c, store, inbox, _alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    names = [f["name"] for f in store.files("bot", RECORDINGS_DIR)]
    assert names == [collector_module.name_for(int(clock.now * 1000))]
    assert store.notes() == [] and GID not in c.owed and filed == [1]
    assert not (inbox / f"bot_{GID}.mp4").exists()


async def test_a_match_deep_in_the_inbox_is_found_and_partials_are_not(parts):
    c, _store, inbox, _alive, filed, _clock = parts
    c.expect("bot", GID, "chrome")
    deep = inbox / "a" / GID
    deep.mkdir(parents=True)
    (deep / f"x_{GID}.mp4.partial").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == []
    (deep / f"x_{GID}.mp4.partial").rename(deep / f"x_{GID}.mp4")
    await c.sweep()
    assert filed == [1]


async def test_a_cut_off_file_waits_for_the_browser_and_a_minute_of_quiet(parts):
    c, _store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    await c.sweep()
    clock.now += 61
    await c.sweep()  # quiet, but the browser lives: never early
    assert filed == []
    alive.clear()
    clock.now += 61
    await c.sweep()
    assert filed == [1]


async def test_a_file_that_never_comes_is_dropped_after_wait_with_a_warning(parts, caplog):
    c, store, _inbox, alive, _filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    alive.clear()
    clock.now += 599
    await c.sweep()
    assert store.notes() != []
    clock.now += 2
    with caplog.at_level(logging.WARNING):
        await c.sweep()
    assert store.notes() == [] and GID not in c.owed
    assert "never reached" in caplog.text and GID not in caplog.text


async def test_a_reap_is_noticed_by_the_tick(parts):
    c, store, _inbox, alive, _filed, clock = parts
    c.expect("bot", GID, "chrome")
    alive.clear()
    await c.sweep()
    assert c.owed[GID].ended == int(clock.now * 1000)
    assert store.notes()[0][2]["ended"] == int(clock.now * 1000)


async def test_the_task_runs_only_while_something_is_owed_and_survives_a_restart(parts):
    c, store, inbox, _alive, _filed, clock = parts
    await c.start()
    assert c.running is False
    c.expect("bot", GID, "chrome")
    await asyncio.sleep(0.05)
    assert c.running is True
    await c.stop()
    # A new process: the note is the queue.
    c2 = collector_module.Collector(
        store, inbox, live=set, wait=600, polling=True, poll_ms=50,
        clock=clock, tick=0.1,
    )
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c2.start()
    for _ in range(50):
        if not c2.owed:
            break
        await asyncio.sleep(0.05)
    assert c2.owed == {} and c2.running is False
    assert len(store.files("bot", RECORDINGS_DIR)) == 1
    await c2.stop()


async def test_two_sessions_never_claim_each_others_files(parts):
    c, store, inbox, alive, _filed, _clock = parts
    other = "0123456789abcdef0123456789abcdef"
    alive.add(other)
    c.expect("a.b", GID, "chrome")
    c.expect("ab", other, "chrome")
    # The recorder strips "." from names: both files start "ab_".
    (inbox / f"ab_{other}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert store.files("a.b", RECORDINGS_DIR) == []
    assert len(store.files("ab", RECORDINGS_DIR)) == 1
    assert GID in c.owed and other not in c.owed


async def test_an_unknown_end_is_ignored(parts):
    c, *_ = parts
    c.ended("ffffffffffffffffffffffffffffffff")
    await asyncio.sleep(0)
    assert c.owed == {}


def test_a_server_with_recording_on_needs_a_usable_inbox(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.server import SeleniumMCP

    settings = config.Settings(
        grid={"url": "http://grid.invalid:4444"},
        data={"dir": str(tmp_path)},
        recording={"enabled": True},
    )
    with pytest.raises(config.ConfigError, match=r"recording\.dir"):
        SeleniumMCP(settings)
    (tmp_path / "recordings").mkdir()
    server = SeleniumMCP(settings)
    assert server.collector is not None and server.sessions.recordings is server.collector


class Listing:
    """The Grid's running sessions, counting how often it is asked."""

    def __init__(self, ids=(), fails=False):
        self.ids = set(ids)
        self.fails = fails
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.fails:
            raise ConnectionError("grid down")
        return set(self.ids)


def collector(store, inbox, live, clock, **kw):
    kw.setdefault("tick", 100.0)
    return collector_module.Collector(
        store, inbox, live=live, wait=600, polling=True, poll_ms=50,
        clock=clock, **kw,
    )


async def test_one_listing_a_tick_however_many_are_owed(tmp_path):
    """Liveness comes from the Grid's status, never from touching a session (a
    command a node counts as activity, so asking would keep it alive forever),
    and one listing serves every owed browser and both branches for a tick."""
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    ids = [f"{n:032x}" for n in range(1, 5)]
    live, clock = Listing(ids), Clock()
    c = collector(store, inbox, live, clock)
    for n, gid in enumerate(ids):
        c.expect(f"s{n}", gid, "chrome")
    (inbox / f"s0_{ids[0]}.mp4").write_bytes(BODY)  # cut off: the quiet branch
    for step in (0, 10, 61, 70, 99):
        clock.now = 1_791_500_000.0 + step
        await c.sweep()
    assert live.calls == 1 and set(c.owed) == set(ids)
    clock.now += 2  # a tick on: one more listing, for all of them
    await c.sweep()
    assert live.calls == 2


async def test_a_cut_off_file_whose_browser_is_gone_marks_it_ended(parts):
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    alive.clear()
    await c.sweep()  # not quiet yet, so not filed: but known to have ended
    assert filed == [] and c.owed[GID].ended == int(clock.now * 1000)
    assert store.notes()[0][2]["ended"] == int(clock.now * 1000)


async def test_a_grid_that_cannot_list_means_every_browser_lives(tmp_path, caplog):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    live, clock = Listing(fails=True), Clock()
    c = collector(store, inbox, live, clock, tick=0.1)
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            clock.now += 61
            await c.sweep()
    assert live.calls == 3 and c.owed[GID].ended is None
    assert store.files("bot", RECORDINGS_DIR) == []
    assert GID not in caplog.text


async def test_a_failed_sweep_is_retried_a_tick_later(tmp_path, caplog):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    filed = []
    c = collector(
        store, inbox, Listing([GID]), Clock(), tick=0.1,
        on_filed=lambda: filed.append(1),
    )
    real, failures = c._inbox_files, []

    def flaky():
        if not failures:
            failures.append(1)
            raise PermissionError(f"/inbox/bot_{GID}.mp4")
        return real()

    c._inbox_files = flaky
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    with caplog.at_level(logging.WARNING):
        await c.start()
        c.expect("bot", GID, "chrome")
        for _ in range(50):
            if filed:
                break
            await asyncio.sleep(0.05)
    assert filed == [1] and c.owed == {} and failures == [1]
    assert "PermissionError" in caplog.text and "bot" in caplog.text
    assert GID not in caplog.text
    await c.stop()


async def test_a_note_that_cannot_be_read_is_skipped_by_session(tmp_path, caplog):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("good", GID, {"opened": "1791500000000", "ended": 5.0})
    store.write_note("bad", other, {"opened": ["no"], "browser": "chrome"})
    c = collector(store, inbox, Listing([GID]), Clock())
    with caplog.at_level(logging.WARNING):
        await c.start()
    assert set(c.owed) == {GID}
    assert c.owed[GID].opened == 1_791_500_000_000 and c.owed[GID].ended == 5
    assert "bad" in caplog.text and other not in caplog.text
    await c.stop()
