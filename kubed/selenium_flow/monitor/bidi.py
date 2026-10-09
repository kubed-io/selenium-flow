"""One held WebDriver BiDi connection to one session's browser.

The per-call socket site data uses (`Grid.bidi`) is Selenium's: a thread per
connection and replies found by polling, right for one command and wrong for a
listener held on the event loop. This is the held one, on ``websockets``'
asyncio client — the only module that imports it (`tests/test_boundaries.py`).

**A socket is a channel, never a liveness signal.** Measured against the
cluster's Grid (session monitor spec, Verify first): a held socket stays open
after its session is deleted and after the Grid reaps it, the hub still answers
its pings, and a command sent on it is never answered. So the monitor closes a
socket when its watch ends, and a command that gets no reply is the sign of a
zombie socket, not of a session to declare gone.

**Never an intercept.** An intercept belongs to the Grid session, outlives
the socket, and leaves every request it matches hanging with nobody to answer.
`command` refuses one before anything is sent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Sequence

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)

# Seconds to open a socket, and to wait for one command's reply.
OPEN_TIMEOUT = 5.0
REPLY_TIMEOUT = 5.0
# A ping is not activity (measured): it costs the browser nothing and notices a
# dead path to the hub. It proves the hub, never the browser.
PING_INTERVAL = 20.0
# A network event or a body can be large; the default 1 MiB is not enough.
MAX_MESSAGE = 16 * 2**20

FORBIDDEN = frozenset({"network.addIntercept"})


class BidiError(Exception):
    """The browser answered a command with an error."""

    def __init__(self, error: str, message: str = ""):
        super().__init__(f"{error}: {message}" if message else error)
        self.error = error


def _connect(url: str):
    return ws_connect(
        url,
        open_timeout=OPEN_TIMEOUT,
        ping_interval=PING_INTERVAL,
        ping_timeout=PING_INTERVAL,
        max_size=MAX_MESSAGE,
    )


class BidiSocket:
    """A held socket: commands by id, events to ``on_event``.

    ``on_event(method, params)`` and ``on_close()`` run on the loop and must
    not block. ``connect(url)`` returns an awaitable connection; it is the
    seam a test replaces.
    """

    def __init__(
        self,
        url: str,
        *,
        on_event: Callable[[str, dict], None] | None = None,
        on_close: Callable[[], None] | None = None,
        connect=None,
    ):
        self.url = url
        self.on_event = on_event
        self.on_close = on_close
        self._connect = connect or _connect
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._closing = False
        self._closed = True
        # subscription id -> the events it named, so a reopen can ask again.
        self.subscriptions: dict[str, tuple[str, ...]] = {}

    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed

    @property
    def dropped(self) -> bool:
        """Closed, and not by us."""
        return self._closed and not self._closing and self._ws is not None

    async def open(self) -> None:
        self._closing = False
        self._ws = await asyncio.wait_for(self._connect(self.url), OPEN_TIMEOUT)
        self._closed = False
        self._reader = asyncio.get_running_loop().create_task(self._read(self._ws))

    async def command(
        self, method: str, params: dict | None = None, *, timeout: float = REPLY_TIMEOUT
    ) -> dict:
        """The result of one command, or BidiError / TimeoutError /
        ConnectionError."""
        if method in FORBIDDEN:
            raise ValueError(
                f"{method} is never sent: an intercept outlives the socket and "
                "hangs every request it matches"
            )
        if not self.is_open:
            raise ConnectionError("the BiDi socket is not open")
        self._next_id += 1
        cid = self._next_id
        reply = asyncio.get_running_loop().create_future()
        self._pending[cid] = reply
        try:
            try:
                await self._ws.send(
                    json.dumps({"id": cid, "method": method, "params": params or {}})
                )
            except ConnectionClosed as exc:
                raise ConnectionError("the BiDi socket closed") from exc
            return await asyncio.wait_for(reply, timeout)
        finally:
            self._pending.pop(cid, None)
            if reply.done() and not reply.cancelled():
                reply.exception()  # retrieved, so an unawaited one never logs

    async def subscribe(self, events: Sequence[str]) -> str:
        """Subscribe to ``events`` across the browser; the subscription's id."""
        result = await self.command("session.subscribe", {"events": list(events)})
        subscription = str(result.get("subscription") or "")
        if not subscription:
            raise BidiError("no subscription id", "session.subscribe returned none")
        self.subscriptions[subscription] = tuple(events)
        return subscription

    async def unsubscribe(self, subscription: str) -> None:
        await self.command("session.unsubscribe", {"subscriptions": [subscription]})
        self.subscriptions.pop(subscription, None)

    async def close(self) -> None:
        """Unsubscribe each subscription, then close. Never raises, and always
        releases the socket and its reader, even when cancelled part-way (the
        cancellation is then re-raised)."""
        self._closing = True
        reader, self._reader = self._reader, None
        try:
            if self.is_open:
                for subscription in list(self.subscriptions):
                    with contextlib.suppress(Exception):
                        await self.unsubscribe(subscription)
            ws = self._ws
            if ws is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ws.close(), OPEN_TIMEOUT)
        finally:
            self._closed = True
            # Synchronous, so a pending cancellation cannot skip it: a no-op
            # when the graceful close finished, the release when it did not.
            if self._ws is not None and self._ws.transport is not None:
                self._ws.transport.abort()
            if reader is not None:
                reader.cancel()
                # wait, not await: the reader's own CancelledError stays its own,
                # while a cancellation aimed at close() still propagates.
                await asyncio.wait({reader})
                if not reader.cancelled():
                    reader.exception()

    async def reopen(self) -> None:
        """Open again and ask for the same events: an id is its connection's."""
        events = sorted({e for named in self.subscriptions.values() for e in named})
        await self.close()
        self.subscriptions = {}
        await self.open()
        if events:
            await self.subscribe(events)

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                cid = message.get("id")
                if cid is not None:
                    reply = self._pending.get(cid)
                    if reply is None or reply.done():
                        continue
                    if message.get("type") == "error":
                        reply.set_exception(
                            BidiError(
                                str(message.get("error", "")),
                                str(message.get("message", "")),
                            )
                        )
                    else:
                        reply.set_result(message.get("result") or {})
                elif message.get("type") == "event" and self.on_event is not None:
                    try:
                        self.on_event(
                            str(message.get("method", "")), message.get("params") or {}
                        )
                    except Exception as exc:  # noqa: BLE001 - one event, not the socket
                        log.debug(
                            "bidi: an event handler failed (%s)", type(exc).__name__
                        )
        except ConnectionClosed:
            pass
        finally:
            self._closed = True
            for reply in self._pending.values():
                if not reply.done():
                    reply.set_exception(ConnectionError("the BiDi socket closed"))
            if not self._closing and self.on_close is not None:
                with contextlib.suppress(Exception):
                    self.on_close()
