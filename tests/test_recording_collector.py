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
        store, inbox, alive=lambda gid: gid in alive, wait=600, polling=True,
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
        store, inbox, alive=lambda gid: False, wait=600, polling=True, poll_ms=50,
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
