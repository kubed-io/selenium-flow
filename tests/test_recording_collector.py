"""The collector files a recording as soon as it is finished, and only then."""

import asyncio
import logging
import os
import time

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


async def test_an_inbox_copy_that_cannot_be_removed_is_filed_once(parts, caplog):
    """A recorder-owned or sticky inbox folder: the link lands, the unlink is
    refused. Filed, the note goes, the inbox copy stays — and never a (1)."""
    c, store, inbox, _alive, filed, _clock = parts
    c.expect("bot", GID, "chrome")
    sub = inbox / "node"
    sub.mkdir()
    source = sub / f"bot_{GID}.mp4"
    source.write_bytes(BODY + mp4.trailer())
    sub.chmod(0o555)
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                await c.sweep()
        names = [f["name"] for f in store.files("bot", RECORDINGS_DIR)]
        assert names == [collector_module.name_for(int(_clock.now * 1000))]
        assert store.notes() == [] and c.owed == {} and filed == [1]
        assert source.exists()
        assert "left" in caplog.text and GID not in caplog.text
    finally:
        sub.chmod(0o755)


@pytest.mark.parametrize(
    "field", ['"opened": 1e999', '"opened": 1e20', '"opened": -5',
              '"opened": NaN', '"ended": 1e999', '"opened": "99999999999999999999"'],
)
async def test_a_note_with_an_impossible_time_is_skipped_not_fatal(tmp_path, caplog, field):
    """Overflow at boot would stop the lifespan; a year past gmtime's range
    would fail every sweep and starve every other recording."""
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("good", GID, {"opened": 1_791_500_000_000})
    pending = store.root / "bad" / RECORDINGS_DIR / ".pending"
    pending.mkdir(parents=True)
    (pending / f"{other}.json").write_text("{" + field + "}")
    c = collector(store, inbox, Listing([GID, other]), Clock())
    with caplog.at_level(logging.WARNING):
        await c.start()
    assert set(c.owed) == {GID}
    assert "bad" in caplog.text and other not in caplog.text
    await c.stop()  # the sweep below is the only one
    (inbox / f"good_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert c.owed == {} and len(store.files("good", RECORDINGS_DIR)) == 1


async def test_only_an_mp4_is_ever_filed(parts):
    """A recorder's sidecar names the same id; it is not a recording."""
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.log").write_bytes(b"ffmpeg says hello")
    (inbox / f"bot_{GID}.json").write_bytes(b"{}")
    alive.clear()
    for _ in range(3):
        clock.now += 61
        await c.sweep()
    assert filed == [] and store.files("bot", RECORDINGS_DIR) == []
    (inbox / f"bot_{GID}.MP4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == [1] and (inbox / f"bot_{GID}.log").exists()


async def test_a_browser_seen_running_again_is_no_longer_ended(parts):
    """An empty listing from a hub that restarted reads as every browser gone;
    a later listing that shows it running takes the deadline back."""
    c, store, _inbox, alive, _filed, clock = parts
    c.expect("bot", GID, "chrome")
    alive.clear()
    await c.sweep()
    assert c.owed[GID].ended is not None
    alive.add(GID)
    clock.now += 1  # a tick on: a fresh listing
    await c.sweep()
    assert c.owed[GID].ended is None
    assert store.notes()[0][2]["ended"] is None
    clock.now += 700  # past RECORDING_WAIT from the blip: still owed
    await c.sweep()
    assert GID in c.owed


async def test_note_writes_never_run_on_the_loop(parts):
    """DATA_DIR is NFS in the cluster: a write on the loop stalls every request."""
    import threading

    c, store, _inbox, alive, _filed, clock = parts
    real, threads = store.write_note, []

    def write_note(*a, **kw):
        threads.append(threading.current_thread())
        return real(*a, **kw)

    store.write_note = write_note
    await c.start()
    loop_thread = threading.current_thread()
    try:
        await asyncio.to_thread(c.expect, "bot", GID, "chrome")
        other = "0123456789abcdef0123456789abcdef"
        await asyncio.to_thread(c.expect, "two", other, "chrome")
        alive.add(other)
        threads.clear()
        await asyncio.to_thread(c.ended, other)  # end_browser's path
        alive.clear()  # GID reaped: the sweep's path
        clock.now += 1
        await c.sweep()
        for _ in range(50):
            if c.owed[other].ended is not None and len(threads) >= 2:
                break
            await asyncio.sleep(0.02)
        assert len(threads) >= 2 and loop_thread not in threads
        assert {n[1]: n[2]["ended"] for n in store.notes()}[other] is not None
    finally:
        await c.stop()


async def test_a_poke_forgets_the_last_broadcast_and_ticks_now():
    from kubed.selenium_flow.http.admin.sessions import Broadcast

    b = Broadcast(compute=dict)
    b._latest = (time.monotonic(), {"sessions": []}, "{}")
    assert b.fresh() is not None and not b._nudge.is_set()
    b.poke()
    assert b.fresh() is None and b._nudge.is_set()


def test_a_filed_recording_pokes_the_admin_broadcast(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.http.admin.sessions import Broadcast
    from kubed.selenium_flow.server import SeleniumMCP

    (tmp_path / "recordings").mkdir()
    server = SeleniumMCP(config.Settings(
        grid={"url": "http://grid.invalid:4444"},
        data={"dir": str(tmp_path)},
        recording={"enabled": True},
    ))
    poke = server.collector.on_filed
    assert isinstance(poke.__self__, Broadcast) and poke.__func__ is Broadcast.poke


async def test_an_unreadable_inbox_keeps_every_note_past_wait(parts, monkeypatch, caplog):
    c, store, inbox, alive, _filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    alive.clear()
    real = collector_module.os.scandir

    def denied(path="."):
        if str(path) == str(inbox):
            raise PermissionError(13, "denied", str(inbox))
        return real(path)

    monkeypatch.setattr(collector_module.os, "scandir", denied)
    clock.now += 1200
    with pytest.raises(PermissionError):
        await c.sweep()
    assert store.notes() != [] and GID in c.owed
    monkeypatch.undo()
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert GID not in c.owed


async def test_an_unreadable_inbox_is_logged_without_the_grid_id(parts, monkeypatch, caplog):
    c, store, inbox, _alive, _filed, _clock = parts
    c.expect("bot", GID, "chrome")

    real = collector_module.os.scandir

    def denied(path="."):
        if str(path) == str(inbox):
            raise PermissionError(13, "denied", str(inbox))
        return real(path)

    monkeypatch.setattr(collector_module.os, "scandir", denied)
    with caplog.at_level(logging.WARNING):
        await c.start()
        for _ in range(20):
            if c._failing:
                break
            await asyncio.sleep(0.05)
        await c.stop()
    assert c._failing and "PermissionError" in caplog.text
    assert GID not in caplog.text and store.notes() != []


async def test_an_unreadable_subfolder_does_not_starve_others_nor_drop_notes(parts, monkeypatch, caplog):
    c, store, inbox, alive, _filed, clock = parts
    (inbox / "bad").mkdir()
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    alive.clear()
    bad = str(inbox / "bad")
    real = collector_module.os.scandir

    def denied(path="."):
        if str(path) == bad:
            raise PermissionError(13, "denied", bad)
        return real(path)

    monkeypatch.setattr(collector_module.os, "scandir", denied)
    clock.now += 1200
    with caplog.at_level(logging.WARNING):
        await c.sweep()
        await c.sweep()
    assert GID in c.owed and store.notes() != []
    assert "cannot be read" in caplog.text and caplog.text.count("cannot be read") == 1
    assert GID not in caplog.text
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert GID not in c.owed


def _failing_once(real, failures):
    def flaky(*a, **kw):
        if not failures:
            failures.append(1)
            raise OSError(5, "Input/output error")
        return real(*a, **kw)
    return flaky


async def test_a_note_that_cannot_be_deleted_stays_owed_until_it_is(parts, caplog):
    """Filed, but its note survives a delete: it stays owed only to be deleted
    — never matched again, so the inbox copy left behind is not a (1)."""
    c, store, inbox, _alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    sub = inbox / "node"
    sub.mkdir()
    (sub / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    sub.chmod(0o555)  # the inbox copy stays behind (store._release)
    failures = []
    store.delete_note = _failing_once(store.delete_note, failures)
    name = collector_module.name_for(int(clock.now * 1000))
    try:
        with caplog.at_level(logging.WARNING):
            await c.sweep()
        assert filed == [1] and failures == [1] and GID in c.owed
        assert [n[2].get("filed") for n in store.notes()] == [name]
        assert GID not in caplog.text
        clock.now += 10_000  # far past the deadline: still only a delete
        await c.sweep()
        assert c.owed == {} and store.notes() == [] and filed == [1]
        assert [f["name"] for f in store.files("bot", RECORDINGS_DIR)] == [name]
    finally:
        sub.chmod(0o755)


async def test_a_filed_note_after_a_restart_is_only_deleted(tmp_path):
    """A note that says it was filed, and the inbox copy that could not be
    removed: the next process deletes the note and files nothing."""
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    store.write_note("bot", GID, {
        "opened": 1_791_500_000_000, "ended": None, "browser": "chrome",
        "filed": "rec-20261008-1013.mp4",
    })
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    c = collector(store, inbox, Listing([GID]), Clock(), tick=0.1)
    await c.start()
    for _ in range(50):
        if not c.owed:
            break
        await asyncio.sleep(0.05)
    assert c.owed == {} and store.notes() == []
    assert store.files("bot", RECORDINGS_DIR) == []
    assert (inbox / f"bot_{GID}.mp4").exists()
    await c.stop()


async def test_a_late_note_that_cannot_be_deleted_is_never_filed(parts):
    """Dropped at the deadline but its note survives the delete: a file that
    comes after is not filed, and the next sweep deletes the note."""
    c, store, inbox, alive, filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    alive.clear()
    clock.now += 601
    failures = []
    store.delete_note = _failing_once(store.delete_note, failures)
    await c.sweep()
    assert failures == [1] and GID in c.owed
    assert store.notes()[0][2].get("dropped") is True
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert c.owed == {} and store.notes() == [] and filed == []
    assert store.files("bot", RECORDINGS_DIR) == []


async def test_notes_that_cannot_be_read_at_boot_are_read_on_a_later_tick(
    tmp_path, monkeypatch, caplog,
):
    """A storage fault reading the notes neither stops the boot nor abandons
    them: the task reads them again a tick later. A broken note is still
    skipped."""
    from pathlib import Path

    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    store.write_note("bot", GID, {"opened": 1_791_500_000_000, "browser": "chrome"})
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("bad", other, {})
    (store.root / "bad" / RECORDINGS_DIR / ".pending" / f"{other}.json").write_text("{no")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    real, failures = Path.read_text, []

    def flaky(self, *a, **kw):
        if self.name == f"{GID}.json" and not failures:
            failures.append(1)
            raise OSError(5, "Input/output error", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky)
    filed = []
    c = collector(
        store, inbox, Listing([GID]), Clock(), tick=0.1,
        on_filed=lambda: filed.append(1),
    )
    with caplog.at_level(logging.WARNING):
        await c.start()  # does not raise
        assert failures == [1] and c.owed == {} and len(store.notes()) == 1
        for _ in range(50):
            if filed:
                break
            await asyncio.sleep(0.05)
    assert filed == [1] and c.owed == {}
    assert len(store.files("bot", RECORDINGS_DIR)) == 1
    assert "OSError" in caplog.text and GID not in caplog.text
    assert (store.root / "bad" / RECORDINGS_DIR / ".pending" / f"{other}.json").exists()
    await c.stop()


def _eio_on(path, monkeypatch):
    """Every stat of ``path`` fails as NFS fails: EIO. Both calls, since
    Python 3.14's `Path.lstat` is `os.lstat` and 3.13's is `os.stat`."""
    import errno
    import os

    def failing(real):
        def stat(p, *a, **kw):
            if os.fspath(p) == str(path):
                raise OSError(errno.EIO, "Input/output error", str(path))
            return real(p, *a, **kw)
        return stat

    for name in ("stat", "lstat"):
        monkeypatch.setattr(os, name, failing(getattr(os, name)))


async def test_one_session_that_cannot_be_read_does_not_block_another(tmp_path, monkeypatch, caplog):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("good", GID, {"opened": 1_791_500_000_000, "browser": "chrome"})
    store.write_note("bad", other, {"opened": 1_791_500_000_000, "browser": "chrome"})
    (inbox / f"good_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    (inbox / f"bad_{other}.mp4").write_bytes(BODY + mp4.trailer())
    _eio_on(store.root / "bad", monkeypatch)
    c = collector(store, inbox, Listing([GID, other]), Clock(), tick=0.1)
    with caplog.at_level(logging.WARNING):
        await c.start()
        for _ in range(50):
            if store.files("good", RECORDINGS_DIR):
                break
            await asyncio.sleep(0.05)
    assert len(store.files("good", RECORDINGS_DIR)) == 1
    assert c._unread and other not in c.owed and len(store.notes(on_error=lambda *a: None)) == 0
    assert caplog.text.count("cannot be read") == 1 and "bad" in caplog.text
    assert GID not in caplog.text and other not in caplog.text
    monkeypatch.undo()
    for _ in range(50):
        if not c.running:
            break
        await asyncio.sleep(0.05)
    assert c.owed == {} and len(store.files("bad", RECORDINGS_DIR)) == 1
    await c.stop()


async def test_a_stop_during_a_filing_lets_it_finish_and_mark_its_note(tmp_path):
    """A rolling deploy mid-copy: the move completes in its thread, so its note
    must be settled too, or the next process files the inbox copy again."""
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    store.write_note("bot", GID, {"opened": 1_791_500_000_000, "browser": "chrome"})
    sub = inbox / "node"
    sub.mkdir()
    (sub / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    real = store.move_in

    def slow(*a, **kw):
        time.sleep(0.5)
        sub.chmod(0o555)  # and the inbox copy stays behind
        return real(*a, **kw)

    store.move_in = slow
    c = collector(store, inbox, Listing([GID]), Clock(), tick=0.1)
    try:
        await c.start()
        await asyncio.sleep(0.2)
        await c.stop()
        assert store.notes() == []
        assert len(store.files("bot", RECORDINGS_DIR)) == 1
        c2 = collector(store, inbox, Listing([GID]), Clock(), tick=0.1)
        await c2.start()
        await c2.stop()
        assert c2.owed == {} and len(store.files("bot", RECORDINGS_DIR)) == 1
    finally:
        sub.chmod(0o755)


async def test_a_note_found_done_that_cannot_be_deleted_is_logged_once(tmp_path, caplog):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    store.write_note("bot", GID, {"opened": 1_791_500_000_000, "filed": "rec.mp4"})

    def refused(*_a, **_kw):
        raise PermissionError(13, "denied")

    store.delete_note = refused
    c = collector(store, inbox, Listing([GID]), Clock())
    with caplog.at_level(logging.WARNING):
        await c.start()
        await c.stop()
        for _ in range(3):
            await c.sweep()
    assert GID in c.owed and caplog.text.count("could not be removed") == 1
    assert "bot" in caplog.text and GID not in caplog.text


async def test_a_failed_sweep_with_only_notes_unread_says_so(tmp_path, monkeypatch, caplog):
    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()

    def unreadable(on_error=None):
        raise OSError(5, "Input/output error")

    store.notes = unreadable
    c = collector(store, inbox, Listing(), Clock(), tick=0.1)

    def blind():
        raise PermissionError(13, "denied")

    c._inbox_files = blind
    with caplog.at_level(logging.WARNING):
        await c.start()
        for _ in range(20):
            if c._failing:
                break
            await asyncio.sleep(0.05)
        await c.stop()
    assert c._failing and "notes not yet read" in caplog.text
    assert "owed for ;" not in caplog.text


async def test_a_stale_read_of_the_notes_never_brings_a_filed_one_back(tmp_path):
    """A read that began before a sweep filed and deleted a note must not put
    it back, unmarked, when it lands after."""
    import threading

    store = flows.LocalFlowStore(tmp_path / "sessions")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    filed = []
    c = collector(
        store, inbox, Listing([GID]), Clock(), on_filed=lambda: filed.append(1),
    )
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    real, gate, read = store.notes, threading.Event(), threading.Event()

    def stale(*a, **kw):
        snapshot = real(*a, **kw)
        read.set()
        gate.wait(5)
        return snapshot

    store.notes = stale
    c._unread = True
    load = asyncio.ensure_future(c._load())
    await asyncio.to_thread(read.wait, 5)
    sweep = asyncio.ensure_future(c.sweep())
    for _ in range(20):
        await asyncio.sleep(0.02)
    gate.set()
    await asyncio.gather(load, sweep)
    assert filed == [1] and c.owed == {} and real() == []


def _stat_fails_on(path, exc, monkeypatch):
    """stat of ``path`` raises ``exc`` through both os.stat and os.lstat."""
    import os

    def failing(real):
        def stat(p, *a, **kw):
            if os.fspath(p) == str(path):
                raise exc
            return real(p, *a, **kw)
        return stat

    for name in ("stat", "lstat"):
        monkeypatch.setattr(os, name, failing(getattr(os, name)))


async def test_a_file_whose_stat_fails_blocks_drops_but_others_are_filed(parts, monkeypatch, caplog):
    import errno

    c, store, inbox, alive, _filed, clock = parts
    other = "0123456789abcdef0123456789abcdef"
    c.expect("bot", GID, "chrome")
    c.expect("bot2", other, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    alive.clear()
    alive.add(other)
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    (inbox / f"bot2_{other}.mp4").write_bytes(BODY + mp4.trailer())
    _stat_fails_on(inbox / f"bot_{GID}.mp4", OSError(errno.EIO, "Input/output error"), monkeypatch)
    clock.now += 1200
    with caplog.at_level(logging.WARNING):
        await c.sweep()
        await c.sweep()
    assert GID in c.owed and store.notes() != []  # not dropped
    assert len(store.files("bot2", RECORDINGS_DIR)) == 1  # the readable one filed
    assert caplog.text.count("cannot be read") == 1
    assert GID not in caplog.text and "bot_" not in caplog.text
    monkeypatch.undo()
    await c.sweep()
    assert GID not in c.owed and len(store.files("bot", RECORDINGS_DIR)) == 1


async def test_a_file_that_vanishes_mid_scan_is_not_blindness(parts, monkeypatch):
    c, _store, inbox, _alive, _filed, _clock = parts
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    _stat_fails_on(inbox / f"bot_{GID}.mp4", FileNotFoundError(2, "gone"), monkeypatch)
    assert c._inbox_files() == [] and c._blind is False


async def test_a_symlink_named_with_an_owed_id_is_never_filed(parts):
    c, store, inbox, alive, filed, clock = parts
    outside = inbox.parent / "outside"
    outside.mkdir()
    for name, body in (("plain.bin", BODY), ("done.bin", BODY + mp4.trailer())):
        (outside / name).write_bytes(body)
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    alive.clear()
    (inbox / f"bot_{GID}.mp4").symlink_to(outside / "plain.bin")
    assert c._inbox_files() == []
    clock.now += 100  # past idle_after, short of wait
    await c.sweep()
    await c.sweep()
    assert filed == [] and store.files("bot", RECORDINGS_DIR) == []
    assert (outside / "plain.bin").exists() and GID in c.owed
    (inbox / f"bot_{GID}.mp4").unlink()
    (inbox / f"bot_{GID}.mp4").symlink_to(outside / "done.bin")
    assert c._inbox_files() == []
    await c.sweep()
    assert filed == [] and (outside / "done.bin").exists() and GID in c.owed
    # a linked directory is not walked into
    (inbox / f"bot_{GID}.mp4").unlink()
    (inbox / "dirlink").symlink_to(outside, target_is_directory=True)
    assert c._inbox_files() == []


async def test_a_regular_file_is_still_a_candidate(parts):
    c, _store, inbox, _alive, _filed, _clock = parts
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    assert [p.name for p, _s, _m in c._inbox_files()] == [f"bot_{GID}.mp4"]


async def test_a_read_fault_on_one_file_files_nothing_and_blocks_drops(parts, monkeypatch, caplog):
    import errno

    c, store, inbox, alive, _filed, clock = parts
    other = "0123456789abcdef0123456789abcdef"
    nofile = "fedcba9876543210fedcba9876543210"
    c.expect("bot", GID, "chrome")
    c.expect("bot2", other, "chrome")
    c.expect("bot3", nofile, "chrome")
    c.ended(GID)
    c.ended(nofile)
    await asyncio.sleep(0)
    alive.clear()
    alive.add(other)
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)  # cut off: filed as it is once quiet
    (inbox / f"bot2_{other}.mp4").write_bytes(BODY + mp4.trailer())
    real = mp4.is_complete

    def flaky(path):
        if GID in str(path):
            raise OSError(errno.EIO, "Input/output error")
        return real(path)

    monkeypatch.setattr(mp4, "is_complete", flaky)
    with caplog.at_level(logging.WARNING):
        await c.sweep()
        clock.now += 1200
        await c.sweep()
        await c.sweep()
    assert GID in c.owed and store.notes() != []
    assert nofile in c.owed  # past wait, but a read fault blocks the drop
    assert store.files("bot", RECORDINGS_DIR) == []
    assert len(store.files("bot2", RECORDINGS_DIR)) == 1
    assert caplog.text.count("cannot be read") == 1
    assert GID not in caplog.text and "bot_" not in caplog.text
    monkeypatch.setattr(mp4, "is_complete", real)
    await c.sweep()  # readable again: its quiet starts now
    assert nofile not in c.owed  # dropped on the first sweep after recovery
    clock.now += 120
    await c.sweep()
    assert GID not in c.owed and len(store.files("bot", RECORDINGS_DIR)) == 1


async def test_a_source_swapped_for_a_link_is_a_fault_not_a_filing(parts, monkeypatch):
    c, store, inbox, _alive, filed, _clock = parts
    outside = inbox.parent / "token"
    outside.write_bytes(b"AUTH_TOKEN")
    src = inbox / f"bot_{GID}.mp4"
    src.write_bytes(BODY + mp4.trailer())
    c.expect("bot", GID, "chrome")
    real_link = os.link

    def swap(*a, **k):
        src.unlink()
        src.symlink_to(outside)
        return real_link(*a, **k)

    monkeypatch.setattr(os, "link", swap)
    await c.sweep()
    assert filed == [] and GID in c.owed and store.files("bot", RECORDINGS_DIR) == []
    assert outside.read_bytes() == b"AUTH_TOKEN"
