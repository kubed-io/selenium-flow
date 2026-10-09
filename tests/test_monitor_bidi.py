"""The held BiDi socket, against a real WebSocket server on localhost."""

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from kubed.selenium_flow.monitor.bidi import BidiError, BidiSocket

pytestmark = pytest.mark.unit


class FakeBrowser:
    """Enough BiDi to hold a socket against: subscribe, unsubscribe, a few
    commands, an error, silence, and events on demand. ``received`` is every
    command in the order it arrived, across connections."""

    def __init__(self):
        self.received = []
        self.connections = []
        self.subscriptions = 0
        self.server = None
        self.url = ""

    async def handler(self, ws):
        self.connections.append(ws)
        async for raw in ws:
            message = json.loads(raw)
            self.received.append(message["method"])
            method, cid = message["method"], message["id"]
            if method == "session.subscribe":
                self.subscriptions += 1
                reply = {"subscription": f"sub-{self.subscriptions}"}
            elif method == "session.unsubscribe":
                reply = {}
            elif method == "browsingContext.getTree":
                reply = {"contexts": []}
            elif method == "silence":
                continue  # what a socket whose browser ended does (measured)
            else:
                await ws.send(
                    json.dumps(
                        {
                            "id": cid,
                            "type": "error",
                            "error": "unknown command",
                            "message": f"Unknown command '{method}'.",
                        }
                    )
                )
                continue
            await ws.send(json.dumps({"id": cid, "type": "success", "result": reply}))

    async def event(self, method="log.entryAdded", params=None):
        await self.connections[-1].send(
            json.dumps({"type": "event", "method": method, "params": params or {}})
        )

    async def __aenter__(self):
        self.server = await serve(self.handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/session/x/se/bidi"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


@pytest.fixture
async def browser():
    async with FakeBrowser() as fake:
        yield fake


async def test_a_command_is_answered_by_its_id(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    assert await sock.command("browsingContext.getTree") == {"contexts": []}
    await sock.close()


async def test_an_error_reply_raises_bidi_error(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    with pytest.raises(BidiError, match="unknown command"):
        await sock.command("nope.nothing")
    await sock.close()


async def test_a_command_with_no_reply_times_out(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    with pytest.raises(asyncio.TimeoutError):
        await sock.command("silence", timeout=0.1)
    await sock.close()


async def test_an_intercept_is_refused_before_anything_is_sent(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    with pytest.raises(ValueError, match="never sent"):
        await sock.command("network.addIntercept", {"phases": ["beforeRequestSent"]})
    await sock.close()
    assert "network.addIntercept" not in browser.received


async def test_events_reach_on_event(browser):
    got = []
    sock = BidiSocket(browser.url, on_event=lambda m, p: got.append((m, p)))
    await sock.open()
    sub = await sock.subscribe(["log.entryAdded"])
    assert sub == "sub-1" and sock.subscriptions == {"sub-1": ("log.entryAdded",)}
    await browser.event(params={"level": "info"})
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0.01)
    assert got == [("log.entryAdded", {"level": "info"})]
    await sock.close()


async def test_close_unsubscribes_first(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    await sock.subscribe(["log.entryAdded"])
    await sock.close()
    assert browser.received == ["session.subscribe", "session.unsubscribe"]
    assert not sock.is_open and not sock.dropped


async def test_a_server_close_fails_waiting_commands_and_calls_on_close(browser):
    closed = []
    sock = BidiSocket(browser.url, on_close=lambda: closed.append(1))
    await sock.open()
    waiting = asyncio.ensure_future(sock.command("silence", timeout=5))
    await asyncio.sleep(0.05)
    await browser.connections[-1].close()
    with pytest.raises(ConnectionError):
        await waiting
    for _ in range(50):
        if closed:
            break
        await asyncio.sleep(0.01)
    assert closed == [1] and sock.dropped and not sock.is_open
    await sock.close()


async def test_reopen_asks_again_for_the_same_events(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    await sock.subscribe(["log.entryAdded", "network.responseCompleted"])
    await browser.connections[-1].close()
    await asyncio.sleep(0.05)
    await sock.reopen()
    assert len(browser.connections) == 2
    assert sock.subscriptions == {
        "sub-2": ("log.entryAdded", "network.responseCompleted")
    }
    await sock.close()


async def test_a_cancelled_close_still_releases_and_propagates(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    await sock.subscribe(["log.entryAdded"])
    reader = sock._reader
    sock.command = lambda *a, **k: asyncio.sleep(60)  # the unsubscribe hangs
    closing = asyncio.ensure_future(sock.close())
    await asyncio.sleep(0.05)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert reader.done() and not sock.is_open
    assert sock._ws.transport.is_closing()
    for _ in range(50):  # the server sees the connection go
        if browser.connections[-1].close_code is not None:
            break
        await asyncio.sleep(0.01)
    assert browser.connections[-1].close_code is not None


async def test_a_command_on_a_dropped_socket_is_a_connection_error(browser):
    sock = BidiSocket(browser.url)
    await sock.open()
    await browser.connections[-1].close()
    await asyncio.sleep(0.05)
    sock._closed = False  # the reader has seen the drop; send must fail alone
    with pytest.raises(ConnectionError):
        await sock.command("browsingContext.getTree")
    sock._closed = True
    await sock.close()
