"""The synchronous face of a BiDi connection, from a worker thread.

The browser runs on a loop in a portal thread, the way production's worker
threads see the monitor's loop, and the test is the worker.
"""

import asyncio
import logging

import pytest

from kubed.selenium_flow.bidi import BidiError, Channel, Channels
from kubed.selenium_flow.faults import BidiUnavailable

from .bidi_fakes import _event, _opened, browser_methods, browser_params

pytestmark = pytest.mark.unit


def test_call_blocks_only_the_worker_and_returns_the_reply(browser_in_thread):
    url, _portal, loop = browser_in_thread
    channels = Channels(lambda _s: url, loop=lambda: loop)
    with channels.open_channel("s") as ch:
        assert ch.call("browsingContext.getTree") == {"contexts": []}


def test_an_error_reply_is_a_bidi_error_and_silence_is_a_503(browser_in_thread):
    url, _portal, loop = browser_in_thread
    with Channels(lambda _s: url, loop=lambda: loop).open_channel("s") as ch:
        with pytest.raises(BidiError, match="unknown command"):
            ch.call("nope.nothing")
        with pytest.raises(BidiUnavailable):
            ch.call("silence", timeout=0.1)


def test_a_held_connection_is_lent_and_not_closed(browser_in_thread):
    url, portal, loop = browser_in_thread
    held = portal.call(_opened, url)
    channels = Channels(lambda _s: "ws://unused", held=lambda _s: held, loop=lambda: loop)
    with channels.open_channel("s") as ch:
        ch.call("browsingContext.getTree")
    assert held.is_open
    portal.call(held.close)


def test_without_a_loop_a_private_one_serves_the_block(browser_in_thread):
    url, _portal, _loop = browser_in_thread
    with Channels(lambda _s: url).open_channel("s") as ch:
        assert ch.call("browsingContext.getTree") == {"contexts": []}


def test_an_unreachable_route_is_a_503():
    with pytest.raises(BidiUnavailable), Channels(lambda _s: "ws://127.0.0.1:9/x").open_channel("s"):
        pass


def test_listen_subscribes_scoped_and_unsubscribes_on_exit(browser_in_thread):
    url, portal, loop = browser_in_thread
    got = []
    channels = Channels(lambda _s: url, loop=lambda: loop)
    with channels.open_channel("s") as ch, ch.listen(
        ["network.beforeRequestSent"], lambda m, p: got.append(p), contexts=["tab-1"]
    ):
        portal.call(_event, "network.beforeRequestSent", {"context": "tab-1"})
        ch.flush()
    assert got == [{"context": "tab-1"}]
    assert {"events": ["network.beforeRequestSent"], "contexts": ["tab-1"]} in browser_params("session.subscribe")
    assert "session.unsubscribe" in browser_methods()


def test_post_never_raises(browser_in_thread, caplog):
    url, portal, loop = browser_in_thread
    conn = portal.call(_opened, url)
    portal.call(conn.close)
    caplog.set_level(logging.DEBUG, logger="kubed.selenium_flow.bidi")
    assert Channel(conn, loop).post("network.provideResponse", {"request": "r-1"}) is None
    portal.call(asyncio.sleep, 0.05)
    assert any("ConnectionError" in r.getMessage() for r in caplog.records)


def test_a_call_on_the_loops_own_thread_is_refused(browser_in_thread):
    url, portal, loop = browser_in_thread
    conn = portal.call(_opened, url)

    async def on_the_loop():
        return Channel(conn, loop).call("browsingContext.getTree")

    with pytest.raises(RuntimeError, match="deadlock"):
        portal.call(on_the_loop)
    portal.call(conn.close)


def test_post_from_the_loop_is_sent(browser_in_thread):
    url, portal, loop = browser_in_thread
    conn = portal.call(_opened, url)

    async def answer_on_the_loop():
        Channel(conn, loop).post("network.provideResponse", {"request": "r-1"})
        await asyncio.sleep(0.05)

    portal.call(answer_on_the_loop)
    assert browser_params("network.provideResponse") == [{"request": "r-1"}]
    portal.call(conn.close)
