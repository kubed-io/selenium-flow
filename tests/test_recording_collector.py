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


class Watcher:
    """Stands in for the monitor: what the collector asked it to watch."""

    def __init__(self):
        self.watched, self.released = [], []

    def watch(self, grid_id, workspace, reason, *, browser=""):
        self.watched.append((grid_id, workspace, reason, browser))

    def release(self, grid_id, reason):
        self.released.append((grid_id, reason))


@pytest.fixture
def parts(tmp_path):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    monitor = Watcher()
    filed = []
    clock = Clock()
    c = collector_module.Collector(
        store, inbox, wait=600, polling=True, poll_ms=50,
        on_filed=lambda: filed.append(1), monitor=monitor, clock=clock,
        tick=0.1,
        # Filed at once: the settle has tests of its own, below.
        idle_after=60.0, settle=0,
    )
    return c, store, inbox, monitor, filed, clock


async def test_nothing_is_filed_while_the_file_grows(parts):
    c, store, inbox, _monitor, filed, _clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    await c.sweep()
    assert store.files("bot", RECORDINGS_DIR) == [] and filed == []


async def test_a_file_that_ends_in_mfro_is_filed_and_its_note_goes(parts):
    c, store, inbox, _monitor, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()  # settle 0 (the fixture's): the first sweep that sees it
    names = [f["name"] for f in store.files("bot", RECORDINGS_DIR)]
    assert names == [collector_module.name_for(int(clock.now * 1000))]
    assert store.notes() == [] and GID not in c.owed and filed == [1]
    assert not (inbox / f"bot_{GID}.mp4").exists()


async def test_a_finished_file_is_filed_only_once_it_has_settled(parts):
    """rclone checks an upload after writing it, and uploads one it finds gone
    again: filed the moment it ended in mfro, it came back as a copy no note
    claimed, in the inbox for good."""
    c, store, inbox, _monitor, filed, clock = parts
    c.settle = 10
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    clock.now += 9.5
    await c.sweep()
    assert filed == [] and store.notes() != [] and (inbox / f"bot_{GID}.mp4").exists()
    clock.now += 0.5
    await c.sweep()
    assert filed == [1] and store.notes() == []
    assert not (inbox / f"bot_{GID}.mp4").exists()


@pytest.mark.parametrize("change", ["size", "mtime"])
async def test_a_finished_file_that_changes_while_settling_starts_again(parts, change):
    c, _store, inbox, _monitor, filed, clock = parts
    c.settle = 10
    c.expect("bot", GID, "chrome")
    video = inbox / f"bot_{GID}.mp4"
    video.write_bytes(BODY + mp4.trailer())
    await c.sweep()
    clock.now += 6
    if change == "size":
        video.write_bytes(BODY + b"\x00" * 50 + mp4.trailer())
    else:
        os.utime(video, (1_000_000.0, 1_000_000.0))
    await c.sweep()  # changed: the 10 s start from here
    clock.now += 9
    await c.sweep()
    assert filed == []
    clock.now += 1
    await c.sweep()
    assert filed == [1]


async def test_a_discarded_video_settles_before_it_is_deleted(parts):
    c, store, inbox, _monitor, _filed, clock = parts
    c.settle = 10
    c.expect("bot", GID, "chrome", discard=True)
    video = inbox / f"bot_{GID}.mp4"
    video.write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert video.exists() and store.notes() != []
    clock.now += 10
    await c.sweep()
    assert not video.exists() and store.notes() == []


async def test_a_settling_file_in_a_quiet_inbox_is_filed_without_waiting_a_tick(tmp_path):
    """Nothing changes in the inbox once a file is finished, so the watch has
    to time out to look again: after ``settle``, not after a tick."""
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    filed = []
    c = collector_module.Collector(
        store, inbox, wait=600, polling=True, poll_ms=50,
        on_filed=lambda: filed.append(1), tick=100.0, settle=0.3,
    )
    await c.start()
    try:
        c.expect("bot", GID, "chrome")
        await asyncio.sleep(0.1)
        (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
        for _ in range(60):
            if filed:
                break
            await asyncio.sleep(0.05)
        assert filed == [1]
    finally:
        await c.stop()


@pytest.mark.parametrize(
    ("tick", "settle", "look"), [(30, 10, 10), (30, 60, 30), (30, 0, 30)],
)
def test_the_watch_looks_again_within_a_settle(tmp_path, tick, settle, look):
    c = collector_module.Collector(
        None, tmp_path, wait=600, polling=True, poll_ms=50,
        tick=tick, settle=settle,
    )
    assert c._look == look


def test_the_server_passes_the_settle_to_the_collector(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.server import SeleniumMCP

    (tmp_path / "recordings").mkdir()
    server = SeleniumMCP(config.Settings(
        grid={"url": "http://grid.invalid:4444"},
        data={"dir": str(tmp_path)},
        recording={"enabled": True, "settle": 3},
    ))
    assert server.collector.settle == 3


async def test_a_match_deep_in_the_inbox_is_found_and_partials_are_not(parts):
    c, _store, inbox, _monitor, filed, _clock = parts
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
    c, _store, inbox, _monitor, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    await c.sweep()
    clock.now += 61
    await c.sweep()  # quiet, but the browser lives: never early
    assert filed == []
    c.ended(GID)
    clock.now += 61
    await c.sweep()
    assert filed == [1]


async def test_a_file_that_never_comes_is_dropped_after_wait_with_a_warning(parts, caplog):
    c, store, _inbox, _monitor, _filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
    clock.now += 599
    await c.sweep()
    assert store.notes() != []
    clock.now += 2
    with caplog.at_level(logging.WARNING):
        await c.sweep()
    assert store.notes() == [] and GID not in c.owed
    assert "never reached" in caplog.text and GID not in caplog.text


async def test_a_discarded_browsers_video_is_deleted_not_filed(parts):
    """Its browser lost the race to bind and was quit: the video is no
    workspace's, so it neither joins this one nor sits in the inbox forever."""
    c, store, inbox, _monitor, filed, _clock = parts
    c.expect("bot", GID, "chrome", discard=True)
    assert store.notes()[0][2]["discard"] is True
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert not (inbox / f"bot_{GID}.mp4").exists()
    assert store.files("bot", RECORDINGS_DIR) == [] and filed == []
    assert store.notes() == [] and GID not in c.owed


async def test_a_discard_survives_a_restart(parts):
    c, store, inbox, *_ = parts
    c.expect("bot", GID, "chrome", discard=True)
    c2 = collector_module.Collector(
        store, inbox, wait=600, polling=True, poll_ms=50, settle=0,
    )
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c2._load()
    await c2.sweep()
    assert store.files("bot", RECORDINGS_DIR) == [] and store.notes() == []
    assert not (inbox / f"bot_{GID}.mp4").exists()


async def test_a_discard_that_cannot_delete_keeps_its_note(parts, monkeypatch, caplog):
    c, store, inbox, *_ = parts
    c.expect("bot", GID, "chrome", discard=True)
    video = inbox / f"bot_{GID}.mp4"
    video.write_bytes(BODY + mp4.trailer())
    real = type(video).unlink

    def refuse(self, *a, **kw):
        if self == video:
            raise PermissionError(13, "denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(type(video), "unlink", refuse)
    with caplog.at_level(logging.WARNING):
        await c.sweep()
    assert video.exists() and GID in c.owed and store.notes() != []
    assert "could not discard" in caplog.text and GID not in caplog.text
    monkeypatch.undo()
    await c.sweep()
    assert not video.exists() and store.notes() == []


async def test_a_discard_whose_video_never_comes_goes_quietly(parts, caplog):
    c, store, _inbox, _monitor, _filed, clock = parts
    c.expect("bot", GID, "chrome", discard=True)
    c.ended(GID)
    with caplog.at_level(logging.WARNING):
        await c.sweep()
        clock.now += 601
        await c.sweep()
    assert store.notes() == [] and GID not in c.owed
    assert caplog.records == []


async def test_an_end_the_monitor_announces_is_written_to_the_note(parts):
    c, store, _inbox, _monitor, _filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)  # the monitor's session.ended: any cause
    assert c.owed[GID].ended == int(clock.now * 1000)
    assert store.notes()[0][2]["ended"] == int(clock.now * 1000)


async def test_the_task_runs_only_while_something_is_owed_and_survives_a_restart(parts):
    c, store, inbox, _monitor, _filed, clock = parts
    await c.start()
    assert c.running is False
    c.expect("bot", GID, "chrome")
    await asyncio.sleep(0.05)
    assert c.running is True
    await c.stop()
    # A new process: the note is the queue.
    c2 = collector_module.Collector(
        store, inbox, wait=600, polling=True, poll_ms=50,
        clock=clock, tick=0.1, settle=0,
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


async def test_two_workspaces_never_claim_each_others_files(parts):
    c, store, inbox, _monitor, _filed, _clock = parts
    other = "0123456789abcdef0123456789abcdef"
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
    assert server.collector is not None and server.workspaces.recordings is server.collector
    assert server.collector.monitor is server.monitor


def collector(store, inbox, clock, **kw):
    kw.setdefault("tick", 100.0)
    kw.setdefault("settle", 0)
    return collector_module.Collector(
        store, inbox, wait=600, polling=True, poll_ms=50,
        clock=clock, **kw,
    )


async def test_a_failed_sweep_is_retried_a_tick_later(tmp_path, caplog):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    filed = []
    c = collector(
        store, inbox, Clock(), tick=0.1,
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


async def test_a_note_that_cannot_be_read_is_skipped_by_workspace(tmp_path, caplog):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("good", GID, {"opened": "1791500000000", "ended": 5.0})
    store.write_note("bad", other, {"opened": ["no"], "browser": "chrome"})
    c = collector(store, inbox, Clock())
    with caplog.at_level(logging.WARNING):
        await c.start()
    assert set(c.owed) == {GID}
    assert c.owed[GID].opened == 1_791_500_000_000 and c.owed[GID].ended == 5
    assert "bad" in caplog.text and other not in caplog.text
    await c.stop()


async def test_an_inbox_copy_that_cannot_be_removed_is_filed_once(parts, caplog):
    """A recorder-owned or sticky inbox folder: the link lands, the unlink is
    refused. Filed, the note goes, the inbox copy stays — and never a (1)."""
    c, store, inbox, _monitor, filed, _clock = parts
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
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("good", GID, {"opened": 1_791_500_000_000})
    pending = store.root / "bad" / RECORDINGS_DIR / ".pending"
    pending.mkdir(parents=True)
    (pending / f"{other}.json").write_text("{" + field + "}")
    c = collector(store, inbox, Clock())
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
    c, store, inbox, _monitor, filed, clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / f"bot_{GID}.log").write_bytes(b"ffmpeg says hello")
    (inbox / f"bot_{GID}.json").write_bytes(b"{}")
    c.ended(GID)
    for _ in range(3):
        clock.now += 61
        await c.sweep()
    assert filed == [] and store.files("bot", RECORDINGS_DIR) == []
    (inbox / f"bot_{GID}.MP4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == [1] and (inbox / f"bot_{GID}.log").exists()


async def test_note_writes_never_run_on_the_loop(parts):
    """DATA_DIR is NFS in the cluster: a write on the loop stalls every request."""
    import threading

    c, store, _inbox, _monitor, _filed, clock = parts
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
        threads.clear()
        await asyncio.to_thread(c.ended, other)  # end_browser's path
        c.ended(GID)  # the monitor's session.ended, on the loop
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
    from kubed.selenium_flow.http.admin.workspaces import Broadcast

    b = Broadcast(compute=dict)
    b._latest = (time.monotonic(), {"workspaces": []}, "{}")
    assert b.fresh() is not None and not b._nudge.is_set()
    b.poke()
    assert b.fresh() is None and b._nudge.is_set()


def test_a_filed_recording_pokes_the_admin_broadcast(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.http.admin.workspaces import Broadcast
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
    c, store, inbox, _monitor, _filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
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
    c, store, inbox, _monitor, _filed, _clock = parts
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
    c, store, inbox, _monitor, _filed, clock = parts
    (inbox / "bad").mkdir()
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
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
    c, store, inbox, _monitor, filed, clock = parts
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
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    store.write_note("bot", GID, {
        "opened": 1_791_500_000_000, "ended": None, "browser": "chrome",
        "filed": "rec-20261008-1013.mp4",
    })
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    c = collector(store, inbox, Clock(), tick=0.1)
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
    c, store, inbox, _monitor, filed, clock = parts
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
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

    store = flows.LocalFlowStore(tmp_path / "workspaces")
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
        store, inbox, Clock(), tick=0.1,
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


async def test_one_workspace_that_cannot_be_read_does_not_block_another(tmp_path, monkeypatch, caplog):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    other = "0123456789abcdef0123456789abcdef"
    store.write_note("good", GID, {"opened": 1_791_500_000_000, "browser": "chrome"})
    store.write_note("bad", other, {"opened": 1_791_500_000_000, "browser": "chrome"})
    (inbox / f"good_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    (inbox / f"bad_{other}.mp4").write_bytes(BODY + mp4.trailer())
    _eio_on(store.root / "bad", monkeypatch)
    c = collector(store, inbox, Clock(), tick=0.1)
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
    store = flows.LocalFlowStore(tmp_path / "workspaces")
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
    c = collector(store, inbox, Clock(), tick=0.1)
    try:
        await c.start()
        await asyncio.sleep(0.2)
        await c.stop()
        assert store.notes() == []
        assert len(store.files("bot", RECORDINGS_DIR)) == 1
        c2 = collector(store, inbox, Clock(), tick=0.1)
        await c2.start()
        await c2.stop()
        assert c2.owed == {} and len(store.files("bot", RECORDINGS_DIR)) == 1
    finally:
        sub.chmod(0o755)


async def test_a_note_found_done_that_cannot_be_deleted_is_logged_once(tmp_path, caplog):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    store.write_note("bot", GID, {"opened": 1_791_500_000_000, "filed": "rec.mp4"})

    def refused(*_a, **_kw):
        raise PermissionError(13, "denied")

    store.delete_note = refused
    c = collector(store, inbox, Clock())
    with caplog.at_level(logging.WARNING):
        await c.start()
        await c.stop()
        for _ in range(3):
            await c.sweep()
    assert GID in c.owed and caplog.text.count("could not be removed") == 1
    assert "bot" in caplog.text and GID not in caplog.text


async def test_a_failed_sweep_with_only_notes_unread_says_so(tmp_path, monkeypatch, caplog):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()

    def unreadable(on_error=None):
        raise OSError(5, "Input/output error")

    store.notes = unreadable
    c = collector(store, inbox, Clock(), tick=0.1)

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

    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    filed = []
    c = collector(
        store, inbox, Clock(), on_filed=lambda: filed.append(1),
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

    c, store, inbox, _monitor, _filed, clock = parts
    other = "0123456789abcdef0123456789abcdef"
    c.expect("bot", GID, "chrome")
    c.expect("bot2", other, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
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
    c, _store, inbox, _monitor, _filed, _clock = parts
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    _stat_fails_on(inbox / f"bot_{GID}.mp4", FileNotFoundError(2, "gone"), monkeypatch)
    assert c._inbox_files() == [] and c._blind is False


async def test_a_symlink_named_with_an_owed_id_is_never_filed(parts):
    c, store, inbox, _monitor, filed, clock = parts
    outside = inbox.parent / "outside"
    outside.mkdir()
    for name, body in (("plain.bin", BODY), ("done.bin", BODY + mp4.trailer())):
        (outside / name).write_bytes(body)
    c.expect("bot", GID, "chrome")
    c.ended(GID)
    await asyncio.sleep(0)
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
    c, _store, inbox, _monitor, _filed, _clock = parts
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY)
    assert [p.name for p, _s, _m in c._inbox_files()] == [f"bot_{GID}.mp4"]


async def test_a_read_fault_on_one_file_files_nothing_and_blocks_drops(parts, monkeypatch, caplog):
    import errno

    c, store, inbox, _monitor, _filed, clock = parts
    other = "0123456789abcdef0123456789abcdef"
    nofile = "fedcba9876543210fedcba9876543210"
    c.expect("bot", GID, "chrome")
    c.expect("bot2", other, "chrome")
    c.expect("bot3", nofile, "chrome")
    c.ended(GID)
    c.ended(nofile)
    await asyncio.sleep(0)
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
    c, store, inbox, _monitor, filed, _clock = parts
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


async def test_the_grid_id_may_be_only_in_a_folder(parts):
    c, store, inbox, _monitor, filed, _clock = parts
    c.expect("bot", GID, "chrome")
    (inbox / "x").mkdir()
    (inbox / "x" / "spike.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == [] and GID in c.owed
    (inbox / GID).mkdir()
    (inbox / GID / "spike.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == [1] and GID not in c.owed
    assert len(store.files("bot", RECORDINGS_DIR)) == 1
    assert (inbox / "x" / "spike.mp4").exists()


async def test_a_path_naming_two_owed_ids_is_skipped_and_logged_without_them(parts, caplog):
    c, _store, inbox, _monitor, filed, _clock = parts
    other = "1" * 32
    c.expect("bot", GID, "chrome")
    c.expect("bot2", other, "chrome")
    (inbox / GID).mkdir()
    (inbox / GID / f"{other}.mp4").write_bytes(BODY + mp4.trailer())
    with caplog.at_level(logging.WARNING):
        await c.sweep()
        await c.sweep()
    assert filed == []
    warned = [r.getMessage() for r in caplog.records if "more than one" in r.getMessage()]
    assert len(warned) == 1 and GID not in warned[0] and other not in warned[0]


async def test_one_sweep_reads_each_inbox_path_once_however_many_are_owed(parts):
    """The inbox is the operator's and only grows: a sweep that looked every
    path over again for each owed id was quadratic, on the loop."""
    c, store, inbox, _monitor, filed, _clock = parts
    owed = [f"{n:x}" * 32 for n in range(1, 6)]
    for i, gid in enumerate(owed):
        c.expect(f"s{i}", gid, "chrome")
    for n in range(200):
        (inbox / f"old-{n:04d}.mp4").write_bytes(BODY + mp4.trailer())
    (inbox / f"s1_{owed[1]}.mp4").write_bytes(BODY + mp4.trailer())
    (inbox / f"s3_{owed[3]}.mp4").write_bytes(BODY + mp4.trailer())
    real, calls = c._ids_in, []

    def counted(path, ids):
        calls.append(path)
        return real(path, ids)

    c._ids_in = counted
    await c.sweep()
    assert len(calls) == 202
    assert filed == [1, 1] and set(c.owed) == {owed[0], owed[2], owed[4]}
    assert [len(store.files(f"s{i}", RECORDINGS_DIR)) for i in range(5)] == [
        0, 1, 0, 1, 0,
    ]
    assert len(list(inbox.iterdir())) == 200


def test_an_id_is_found_anywhere_in_the_path_as_before(parts):
    c, _store, inbox, _monitor, _filed, _clock = parts
    other = "abcd-1234-efgh"
    ids = frozenset({GID, other})
    assert c._ids_in(inbox / f"x{GID}y.mp4", ids) == [GID]
    assert c._ids_in(inbox / f"a{other}" / "b.MP4", ids) == [other]
    assert c._ids_in(inbox / f"{GID}.webm", ids) == []
    assert c._ids_in(inbox / f"{GID[:-1]}.mp4", ids) == []
    assert c._ids_in(inbox / f"{GID}.mp4", frozenset()) == []


async def _settled(c):
    for _ in range(100):
        if not c._saves:
            return
        await asyncio.sleep(0.02)


async def test_a_provisional_discard_expected_again_is_owed_once_and_filed(parts):
    """A recorded browser is noted for discard the moment it exists and again,
    ordinarily, once its workspace holds it: one owed recording, timed from the
    first, and a note that says so across a restart (Copilot, #59)."""
    c, store, inbox, _monitor, _filed, clock = parts
    await c.start()
    first = int(clock.now * 1000)

    def open_session():  # one worker thread, in the order the sessions call it
        c.expect("bot", GID, "chrome", discard=True)
        clock.now += 90
        c.expect("bot", GID, "chrome")

    try:
        await asyncio.to_thread(open_session)
        await asyncio.sleep(0.05)
        await _settled(c)
        assert list(c.owed) == [GID]
        assert (c.owed[GID].discard, c.owed[GID].opened) == (False, first)
        [(_session, _gid, note)] = store.notes()
        assert "discard" not in note and note["opened"] == first
    finally:
        await c.stop()
    c2 = collector_module.Collector(
        store, inbox, wait=600, polling=True, poll_ms=50,
        clock=clock, settle=0,
    )
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c2._load()
    assert c2.owed[GID].discard is False
    await c2.sweep()
    names = [f["name"] for f in store.files("bot", RECORDINGS_DIR)]
    assert names == [collector_module.name_for(first)] and store.notes() == []


async def test_an_upgrade_lands_after_a_write_of_the_provisional_note(parts):
    """The loop was writing the provisional note (its browser marked ended)
    when the upgrade's own write landed: the old mark must not win on disk."""
    import threading

    c, store, _inbox, _monitor, _filed, _clock = parts
    # The loop, without its sweeping task: this is about the writes alone.
    c._loop, c._stopping = asyncio.get_running_loop(), True
    c.expect("bot", GID, "chrome", discard=True)
    real, gate, held = store.write_note, threading.Event(), threading.Event()

    def slow_once(*a, **kw):
        if not held.is_set():
            held.set()
            gate.wait(5)
        return real(*a, **kw)

    store.write_note = slow_once
    c.ended(GID)  # a `_save` of the provisional note, now in its thread
    await asyncio.to_thread(held.wait, 5)
    await asyncio.to_thread(c.expect, "bot", GID, "chrome")
    await asyncio.sleep(0.05)
    gate.set()
    await _settled(c)
    [(_session, _gid, note)] = store.notes()
    assert "discard" not in note and note["ended"] is not None
    assert c.owed[GID].discard is False


async def test_an_upgrade_that_cannot_be_written_still_keeps_the_video(parts):
    """Told it cannot be filed, the workspace's video is not deleted on the
    strength of a provisional note: the collector owes it ordinarily and
    writes the note again."""
    c, store, inbox, _monitor, filed, _clock = parts
    c.expect("bot", GID, "chrome", discard=True)
    store.write_note = _failing_once(store.write_note, [])
    with pytest.raises(OSError):
        c.expect("bot", GID, "chrome")
    assert c.owed[GID].discard is False
    assert "discard" not in store.notes()[0][2]
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == [1] and len(store.files("bot", RECORDINGS_DIR)) == 1


def test_a_first_expect_that_cannot_be_written_owes_nothing(parts):
    c, store, *_ = parts
    store.write_note = _failing_once(store.write_note, [])
    with pytest.raises(OSError):
        c.expect("bot", GID, "chrome")
    assert c.owed == {} and store.notes() == []


async def test_an_expected_browser_is_watched_and_released_once_filed(parts):
    c, _store, inbox, monitor, filed, _clock = parts
    c.expect("bot", GID, "chrome")
    assert monitor.watched == [(GID, "bot", "recording", "chrome")]
    (inbox / f"bot_{GID}.mp4").write_bytes(BODY + mp4.trailer())
    await c.sweep()
    assert filed == [1] and monitor.released == [(GID, "recording")]


async def test_a_provisional_discard_is_watched_too(parts):
    """Its deadline needs an end as much as a kept one's does."""
    c, _store, _inbox, monitor, _filed, _clock = parts
    c.expect("bot", GID, "chrome", discard=True)
    assert monitor.watched == [(GID, "bot", "recording", "chrome")]


async def test_start_watches_what_the_notes_still_owe(tmp_path):
    store = flows.LocalFlowStore(tmp_path / "workspaces")
    inbox = tmp_path / "recordings"
    inbox.mkdir()
    ended = "0123456789abcdef0123456789abcdef"
    done = "fedcba9876543210fedcba9876543210"
    store.write_note("a", GID, {"opened": 1_791_500_000_000, "browser": "chrome"})
    store.write_note("b", ended, {"opened": 1_791_500_000_000, "ended": 1})
    store.write_note("c", done, {"opened": 1_791_500_000_000, "filed": "x.mp4"})
    monitor = Watcher()
    c = collector(store, inbox, Clock(), monitor=monitor)
    await c.start()
    assert monitor.watched == [(GID, "a", "recording", "chrome")]
    await c.stop()


def test_an_end_on_the_bus_reaches_the_collector(tmp_path):
    from kubed.selenium_flow import config
    from kubed.selenium_flow.monitor.events import SessionEnded
    from kubed.selenium_flow.server import SeleniumMCP

    (tmp_path / "recordings").mkdir()
    server = SeleniumMCP(config.Settings(
        grid={"url": "http://grid.invalid:4444"},
        data={"dir": str(tmp_path)},
        recording={"enabled": True},
    ))
    server.collector.expect("bot", GID, "chrome")
    assert server.monitor.watching(GID) == {"recording"}
    server.bus.publish(SessionEnded("bot", GID, "gone", 1.0))
    assert server.collector.owed[GID].ended is not None
