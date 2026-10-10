"""One BiDi connection, against a real WebSocket server on localhost."""

import asyncio

import pytest

from kubed.selenium_flow.bidi import BidiError, Connection
from kubed.selenium_flow.bidi.connection import Subscriber

pytestmark = pytest.mark.unit


async def test_a_command_is_answered_by_its_id(browser):
    conn = Connection(browser.url)
    await conn.open()
    assert await conn.command("browsingContext.getTree") == {"contexts": []}
    await conn.close()


async def test_an_error_reply_raises_bidi_error(browser):
    conn = Connection(browser.url)
    await conn.open()
    with pytest.raises(BidiError, match="unknown command") as raised:
        await conn.command("nope.nothing")
    assert raised.value.error == "unknown command"
    assert raised.value.message == "Unknown command 'nope.nothing'."
    await conn.close()


async def test_a_command_with_no_reply_times_out(browser):
    conn = Connection(browser.url)
    await conn.open()
    with pytest.raises(TimeoutError):
        await conn.command("silence", timeout=0.1)
    await conn.close()


async def test_subscribe_records_the_id_with_its_events_and_contexts(browser):
    conn = Connection(browser.url)
    await conn.open()
    assert await conn.subscribe(["log.entryAdded"]) == "sub-1"
    assert await conn.subscribe(["network.fetchError"], ["tab-1"]) == "sub-2"
    assert conn.subscriptions == {
        "sub-1": (("log.entryAdded",), None),
        "sub-2": (("network.fetchError",), ("tab-1",)),
    }
    assert browser.received_params[-1] == {
        "events": ["network.fetchError"], "contexts": ["tab-1"]
    }
    await conn.close()


async def test_close_unsubscribes_first(browser):
    conn = Connection(browser.url)
    await conn.open()
    await conn.subscribe(["log.entryAdded"])
    await conn.close()
    assert browser.received == ["session.subscribe", "session.unsubscribe"]
    assert browser.received_params[-1] == {"subscriptions": ["sub-1"]}
    assert not conn.is_open and not conn.dropped


async def test_a_server_close_fails_waiting_commands_and_calls_on_close(browser):
    closed = []
    conn = Connection(browser.url, on_close=lambda: closed.append(1))
    await conn.open()
    waiting = asyncio.ensure_future(conn.command("silence", timeout=5))
    await asyncio.sleep(0.05)
    await browser.connections[-1].close()
    with pytest.raises(ConnectionError):
        await waiting
    for _ in range(50):
        if closed:
            break
        await asyncio.sleep(0.01)
    assert closed == [1] and conn.dropped and not conn.is_open
    await conn.close()
    assert closed == [1]


async def test_reopen_asks_again_for_the_same_events(browser):
    conn = Connection(browser.url)
    await conn.open()
    got = []
    conn.listen(["log.entryAdded"], lambda m, p: got.append(p), name="kept")
    await conn.subscribe(["log.entryAdded", "network.responseCompleted"])
    await conn.add_collector({"dataTypes": ["response"]})
    await browser.connections[-1].close()
    await asyncio.sleep(0.05)
    await conn.reopen()
    assert len(browser.connections) == 2
    assert conn.subscriptions == {
        "sub-2": (("log.entryAdded", "network.responseCompleted"), None)
    }
    assert conn.collectors == set()
    assert browser.received.count("network.addDataCollector") == 1
    # A listener is this side's, not the connection's: it hears the new one.
    await browser.event("log.entryAdded", {"n": 1})
    await conn.flush()
    assert got == [{"n": 1}]
    await conn.close()


async def test_a_cancelled_close_still_releases_and_propagates(browser):
    conn = Connection(browser.url)
    await conn.open()
    await conn.subscribe(["log.entryAdded"])
    reader = conn._reader
    conn.command = lambda *a, **k: asyncio.sleep(60)  # the unsubscribe hangs
    closing = asyncio.ensure_future(conn.close())
    await asyncio.sleep(0.05)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert reader.done() and not conn.is_open
    assert conn._ws.transport.is_closing()
    for _ in range(50):  # the server sees the connection go
        if browser.connections[-1].close_code is not None:
            break
        await asyncio.sleep(0.01)
    assert browser.connections[-1].close_code is not None


async def test_a_command_on_a_dropped_socket_is_a_connection_error(browser):
    conn = Connection(browser.url)
    await conn.open()
    await browser.connections[-1].close()
    await asyncio.sleep(0.05)
    conn._closed = False  # the reader has seen the drop; send must fail alone
    with pytest.raises(ConnectionError):
        await conn.command("browsingContext.getTree")
    conn._closed = True
    await conn.close()


async def test_an_open_that_fails_is_a_connection_error():
    with pytest.raises(ConnectionError):
        await Connection("ws://127.0.0.1:9/x").open()


# ---- the dispatcher ------------------------------------------------------------


async def test_an_event_reaches_only_the_subscribers_that_asked(browser):
    conn = Connection(browser.url)
    await conn.open()
    logs, nets = [], []
    conn.listen(["log.entryAdded"], lambda m, p: logs.append(p), name="logs")
    conn.listen(["network.fetchError"], lambda m, p: nets.append(p), name="nets")
    await browser.event("log.entryAdded", {"n": 1})
    await browser.event("browsingContext.load", {"n": 2})   # nobody asked
    await conn.flush()
    assert logs == [{"n": 1}] and nets == [] and conn.unsolicited == 1
    await conn.close()


async def test_a_blocked_subscriber_does_not_delay_a_reply(browser):
    conn = Connection(browser.url)
    await conn.open()
    gate = asyncio.Event()

    async def stuck(_m, _p):
        await gate.wait()

    sub = conn.listen(["log.entryAdded"], stuck, name="stuck", limit=10)
    await browser.events(50, "log.entryAdded")
    started = asyncio.get_running_loop().time()
    assert await conn.command("browsingContext.getTree") == {"contexts": []}
    assert asyncio.get_running_loop().time() - started < 0.5
    assert sub.dropped >= 39 and sub.peak == 10
    gate.set()
    await conn.close()


async def test_a_full_queue_is_logged_once_a_streak(browser, caplog):
    conn = Connection(browser.url)
    await conn.open()
    gate = asyncio.Event()

    async def stuck(_m, _p):
        await gate.wait()

    conn.listen(["log.entryAdded"], stuck, name="stuck", limit=2)
    await browser.events(20, "log.entryAdded")
    await conn.command("browsingContext.getTree")
    gate.set()
    full = [r for r in caplog.records if "stuck" in r.getMessage()]
    assert len(full) == 1 and full[0].levelname == "WARNING"
    await conn.close()


async def test_flush_returns_after_every_earlier_event_was_handled(browser):
    conn = Connection(browser.url)
    await conn.open()
    seen = []

    async def slow(_m, p):
        await asyncio.sleep(0.01)
        seen.append(p["i"])

    conn.listen(["log.entryAdded"], slow, name="slow")
    for i in range(5):
        await browser.event("log.entryAdded", {"i": i})
    await conn.flush()
    assert seen == [0, 1, 2, 3, 4]
    assert browser.received == ["session.status"]
    await conn.close()


async def test_a_raising_handler_is_logged_once_and_the_next_event_still_arrives(browser, caplog):
    conn = Connection(browser.url)
    await conn.open()
    got = []

    def handler(_m, p):
        if p.get("boom"):
            raise KeyError("x")
        got.append(p)

    conn.listen(["log.entryAdded"], handler, name="h")
    for params in ({"boom": 1}, {"boom": 1}, {"ok": 1}):
        await browser.event("log.entryAdded", params)
    await conn.flush()
    assert got == [{"ok": 1}]
    assert len([r for r in caplog.records if "KeyError" in r.getMessage()]) == 1
    await conn.close()


async def test_close_removes_collectors_and_subscriptions_then_closes(browser):
    conn = Connection(browser.url)
    await conn.open()
    await conn.subscribe(["log.entryAdded"])
    await conn.add_collector({"dataTypes": ["response"], "maxEncodedDataSize": 10_000_000, "collectorType": "blob"})
    await conn.close()
    assert sorted(browser.received[-2:]) == ["network.removeDataCollector", "session.unsubscribe"]
    assert not conn.is_open and not conn.dropped


async def test_close_cancels_every_subscriber(browser):
    conn = Connection(browser.url)
    await conn.open()
    sub = conn.listen(["log.entryAdded"], lambda m, p: None, name="l")
    await conn.close()
    await asyncio.sleep(0)
    assert sub._task.done()
    await sub.barrier()  # a cancelled subscriber holds nobody up


async def test_lags_are_recorded_per_event(browser):
    conn = Connection(browser.url)
    await conn.open()
    sub = conn.listen(["log.entryAdded"], lambda m, p: None, name="l")
    await browser.events(3, "log.entryAdded")
    await conn.flush()
    assert len(sub.lags) == 3 and all(lag >= 0 for lag in sub.lags)
    await conn.close()


async def test_a_stalled_subscriber_drops_on_bytes_before_the_count(browser, monkeypatch):
    monkeypatch.setattr("kubed.selenium_flow.bidi.connection.QUEUE_BYTES", 30_000)
    conn = Connection(browser.url)
    await conn.open()
    gate = asyncio.Event()

    async def stuck(_m, _p):
        await gate.wait()

    sub = conn.listen(["log.entryAdded"], stuck, name="stuck", limit=100)
    for _ in range(10):
        await browser.event("log.entryAdded", {"text": "x" * 10_000})
    await conn.command("browsingContext.getTree")
    # Each frame is just over 10 kB: two fit the budget, far below the count.
    assert sub.peak == 2 and sub.dropped >= 7
    gate.set()
    await conn.flush()
    dropped = sub.dropped
    sub.deliver("log.entryAdded", {}, 0.0, 20_000)  # popped events free their bytes
    assert sub.dropped == dropped
    await conn.close()


async def test_a_burst_of_5000_events_reaches_a_fast_handler_without_a_drop(browser):
    """The reader yields every few frames and the subscriber drains a batch per
    turn: a burst already buffered in one read must not fill the queue before
    the subscriber gets a turn (Task 13 measured drops at 5 000)."""
    conn = Connection(browser.url)
    await conn.open()
    got = []
    sub = conn.listen(["log.entryAdded"], lambda m, p: got.append(p["i"]), name="capture")
    await browser.events(5000, "log.entryAdded")
    await conn.flush(timeout=30)
    assert sub.dropped == 0 and got == list(range(5000))
    assert sub.peak < sub.limit
    await conn.close()


async def test_the_fell_behind_warning_re_arms_only_once_the_queue_has_drained(caplog):
    """A saturated queue alternates accepting and dropping; that is one streak,
    one warning, until the subscriber catches up to empty."""
    tokens = asyncio.Semaphore(0)

    async def slow(_m, _p):
        await tokens.acquire()

    sub = Subscriber(["e"], slow, name="slow", limit=3)

    async def turns(n=5):
        for _ in range(n):
            await asyncio.sleep(0)

    for _ in range(200):  # two arrive for every one handled: never empty
        sub.deliver("e", {}, 0.0)
        sub.deliver("e", {}, 0.0)
        tokens.release()
        await turns()
    assert sub.dropped > 100
    for _ in range(sub.limit + 1):
        tokens.release()
    await sub.barrier()  # drained to empty: the next streak is a new one
    for _ in range(10):
        sub.deliver("e", {}, 0.0)
    warned = [r for r in caplog.records if "fell behind" in r.getMessage()]
    assert len(warned) == 2
    sub.cancel()


async def test_close_removes_the_intercepts_it_added_and_not_the_removed_ones(browser):
    """An intercept outliving its user would block its tab's requests; a
    connection closed under a save removes the one the save added (PR review I1)."""
    conn = Connection(browser.url)
    await conn.open()
    kept = (await conn.command("network.addIntercept", {"phases": ["beforeRequestSent"]}))["intercept"]
    gone = (await conn.command("network.addIntercept", {"phases": ["beforeRequestSent"]}))["intercept"]
    await conn.command("network.removeIntercept", {"intercept": gone})
    await conn.close()
    removed = [p["intercept"] for m, p in zip(browser.received, browser.received_params, strict=True)
               if m == "network.removeIntercept"]
    assert removed == [gone, kept]


@pytest.mark.parametrize("ours", [True, False], ids=["we-close-1009", "peer-closes-1009"])
async def test_a_frame_past_the_limit_fails_waiting_commands_as_message_too_big(browser, monkeypatch, ours):
    from kubed.selenium_flow.bidi import MessageTooBig
    from kubed.selenium_flow.bidi import connection as connection_module

    if ours:
        monkeypatch.setattr(connection_module, "MAX_MESSAGE", 256)
        browser.bodies["r1"] = {"bytes": {"type": "string", "value": "x" * 1000}}
    conn = Connection(browser.url)
    await conn.open()
    with pytest.raises(MessageTooBig):
        await conn.command("network.getData" if ours else "too.big", {"request": "r1"})
    assert conn.dropped
    await conn.close()
