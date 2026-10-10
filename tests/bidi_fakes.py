"""A browser's BiDi end, on a real WebSocket server on localhost.

`FakeBrowser` answers enough of BiDi to hold a connection against: subscribe,
unsubscribe, a data collector, an intercept, a tab, a body, a few commands, an error, silence, a slow
reply, any method's reply set in ``replies``, and events on demand. ``received`` is every command's method in the
order it arrived, across connections, and ``received_params`` its params beside
it.

Two ways to run one: the ``browser`` fixture in `conftest.py`, on the test's
own loop, and ``browser_in_thread`` here, inside a blocking portal, which is
how a worker thread sees the monitor's loop in production. The portal's helpers
(``_opened``, ``_event``, ``browser_params``, ``browser_methods``) reach the
browser that fixture started.
"""

import asyncio
import json

import pytest
from anyio.from_thread import start_blocking_portal
from websockets.asyncio.server import serve

from kubed.selenium_flow.bidi import Connection


class FakeBrowser:
    def __init__(self):
        self.received = []
        self.received_params = []
        self.connections = []
        self.subscriptions = 0
        self.collectors = 0
        self.intercepts = 0
        self.bodies: dict[str, dict] = {}
        self.replies: dict[str, dict] = {}  # a method's result, ahead of the rest
        self.delay = 0.0
        self.server = None
        self.url = ""
        self._later = set()

    async def handler(self, ws):
        self.connections.append(ws)
        async for raw in ws:
            message = json.loads(raw)
            method, cid = message["method"], message["id"]
            params = message.get("params") or {}
            self.received.append(method)
            self.received_params.append(params)
            if method in self.replies:
                reply = self.replies[method]
            elif method == "session.subscribe":
                self.subscriptions += 1
                reply = {"subscription": f"sub-{self.subscriptions}"}
            elif method == "session.unsubscribe":
                reply = {}
            elif method == "session.status":
                reply = {"ready": False, "message": "already connected"}
            elif method == "network.addDataCollector":
                self.collectors += 1
                reply = {"collector": f"col-{self.collectors}"}
            elif method == "network.removeDataCollector":
                reply = {}
            elif method == "network.addIntercept":
                self.intercepts += 1
                reply = {"intercept": f"int-{self.intercepts}"}
            elif method in ("network.removeIntercept", "browsingContext.close"):
                reply = {}
            elif method == "browsingContext.create":
                reply = {"context": "tab-2"}
            elif method == "network.getData" and params.get("request") in self.bodies:
                reply = self.bodies[params["request"]]
            elif method == "network.getData":
                await self._error(ws, cid, "no such network data", "No data found.")
                continue
            elif method == "browsingContext.getTree":
                reply = {"contexts": []}
            elif method == "too.big":
                await ws.close(1009, "message too big")  # what a peer past its limit sends
                return
            elif method == "silence":
                continue  # what a socket whose browser ended does (measured)
            elif method == "slow":
                task = asyncio.get_running_loop().create_task(self._answer_later(ws, cid))
                self._later.add(task)
                task.add_done_callback(self._later.discard)
                continue
            else:
                await self._error(ws, cid, "unknown command", f"Unknown command '{method}'.")
                continue
            await ws.send(json.dumps({"id": cid, "type": "success", "result": reply}))

    async def _error(self, ws, cid, error, message):
        await ws.send(
            json.dumps({"id": cid, "type": "error", "error": error, "message": message})
        )

    async def _answer_later(self, ws, cid):
        await asyncio.sleep(self.delay)
        await ws.send(json.dumps({"id": cid, "type": "success", "result": {}}))

    async def event(self, method="log.entryAdded", params=None):
        await self.connections[-1].send(
            json.dumps({"type": "event", "method": method, "params": params or {}})
        )

    async def events(self, n, method="log.entryAdded"):
        """``n`` events in one burst, sent before this returns."""
        ws = self.connections[-1]
        for i in range(n):
            await ws.send(json.dumps({"type": "event", "method": method, "params": {"i": i}}))

    async def __aenter__(self):
        self.server = await serve(self.handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/session/x/se/bidi"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


# ---- one browser in a portal thread ------------------------------------------

# The browser `browser_in_thread` started, for the helpers below.
_running: FakeBrowser | None = None


@pytest.fixture
def browser_in_thread():
    """A FakeBrowser on a loop in a portal thread: ``(url, portal, loop)``."""
    global _running
    with start_blocking_portal() as portal:
        fake = FakeBrowser()
        portal.call(fake.__aenter__)
        _running = fake
        try:
            yield fake.url, portal, portal.call(asyncio.get_running_loop)
        finally:
            _running = None
            portal.call(fake.__aexit__)


async def _opened(url: str) -> Connection:
    """An open Connection to ``url``, made on the calling loop."""
    connection = Connection(url)
    await connection.open()
    return connection


async def _event(method: str, params: dict | None = None) -> None:
    """One event from the running browser, run with ``portal.call``."""
    await _running.event(method, params)


def browser_methods() -> list[str]:
    """Every command the running browser received, in order."""
    return list(_running.received)


def browser_params(method: str) -> list[dict]:
    """The params of each ``method`` command the running browser received."""
    return [
        params
        for name, params in zip(_running.received, _running.received_params, strict=True)
        if name == method
    ]
