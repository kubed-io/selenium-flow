"""One WebDriver BiDi connection to one session's browser, and its dispatcher.

Everything that speaks BiDi — capture, site data, the monitor — goes through a
`Connection` (capture spec, ruling 1), on ``websockets``' asyncio client. This
is the only module that imports it (`tests/test_boundaries.py`).

**A socket is a channel, never a liveness signal.** Measured against the
cluster's Grid (session monitor spec, Verify first): a held socket stays open
after its session is deleted and after the Grid reaps it, and a command sent on
it is never answered. The hub still answers its pings, so a ping proves the
hub, never the browser. A command that gets no reply is the sign of a zombie
socket, not of a session to declare gone, and a connection is closed when the
reason it was held ends.

**Nothing runs on the read loop** (ruling 8). The reader parses and routes: a
reply resolves the command waiting on its id, and an event is queued for each
subscriber that asked for it. Each subscriber has its own bounded queue,
drained by its own task, overflow dropped and counted. A slow subscriber on the
receive path would starve every reply behind it — a site-data command or a
spare tab's intercept answer would wait on a page flooding the console — so the
reader never waits on one.

**A connection closes clean** (ruling 7): it unsubscribes every subscription it
made and removes every data collector it added before the socket goes, so
Firefox's fan-out never floods another socket and no body outlives its reader.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Sequence

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

log = logging.getLogger(__name__)

# Seconds to open a socket, and to wait for one command's reply.
OPEN_TIMEOUT = 5.0
REPLY_TIMEOUT = 5.0
# A ping is not activity (measured): it costs the browser nothing and notices a
# dead path to the hub. It proves the hub, never the browser.
PING_INTERVAL = 20.0
# A network event or a body can be large; the default 1 MiB is not enough.
MAX_MESSAGE = 16 * 2**20
# Events one subscriber may have waiting; past it, new ones are dropped.
QUEUE_LIMIT = 1000
# How many of a subscriber's most recent lags it keeps.
LAGS_KEPT = 1000
# Seconds `flush` sleeps between its round trip and its barriers. Zero while
# `session.status` alone is a flush (capture spec, Verify first 2).
FLUSH_GRACE = 0.0


class BidiError(Exception):
    """The browser answered a command with an error."""

    def __init__(self, error: str, message: str = ""):
        super().__init__(f"{error}: {message}" if message else error)
        self.error = error
        self.message = message


Handler = Callable[[str, dict], Awaitable[None] | None]


def _connect(url: str):
    return ws_connect(
        url,
        open_timeout=OPEN_TIMEOUT,
        ping_interval=PING_INTERVAL,
        ping_timeout=PING_INTERVAL,
        max_size=MAX_MESSAGE,
    )


class Subscriber:
    """The events one listener asked for, queued and handed over one at a time.

    ``deliver`` is the reader's side and never awaits. The subscriber's own task
    pops each event and calls ``handler(method, params)``, awaiting what it
    returns when that is awaitable. Made on the loop it serves.
    """

    def __init__(
        self,
        events: Iterable[str],
        handler: Handler,
        *,
        name: str,
        limit: int = QUEUE_LIMIT,
        clock: Callable[[], float] = time.monotonic,
        on_cancel: Callable[[Subscriber], None] | None = None,
    ):
        self.events = frozenset(events)
        self.handler = handler
        self.name = name
        self.limit = limit
        self.dropped = 0
        self.peak = 0
        self.lags: deque[float] = deque(maxlen=LAGS_KEPT)
        self._clock = clock
        self._on_cancel = on_cancel
        # (method, params, at) for an event; a future for a barrier.
        self._queue: deque[tuple[str, dict, float] | asyncio.Future] = deque()
        self._depth = 0  # events in the queue, barriers not counted
        self._wake = asyncio.Event()
        self._full = False  # in a streak of dropped events
        self._failing: str | None = None  # the type of a streak of failures
        self._cancelled = False
        self._task = asyncio.get_running_loop().create_task(
            self._drain(), name=f"bidi subscriber {name}"
        )

    def deliver(self, method: str, params: dict, at: float) -> None:
        """Queue one event the reader received at ``at``; drop it when full."""
        if self._cancelled:
            return
        if self._depth >= self.limit:
            self.dropped += 1
            if not self._full:
                self._full = True
                log.warning(
                    "bidi: subscriber %s fell %d events behind; dropping until it "
                    "catches up", self.name, self.limit,
                )
            return
        self._full = False
        self._queue.append((method, params, at))
        self._depth += 1
        self.peak = max(self.peak, self._depth)
        self._wake.set()

    async def barrier(self) -> None:
        """Return once everything queued before this call has been handled."""
        if self._cancelled:
            return
        reached = asyncio.get_running_loop().create_future()
        self._queue.append(reached)  # past the limit: a barrier is never dropped
        self._wake.set()
        await reached

    def cancel(self) -> None:
        """Stop handling events, and let every waiting barrier go."""
        if self._cancelled:
            return
        self._cancelled = True
        self._task.cancel()
        while self._queue:
            item = self._queue.popleft()
            if isinstance(item, asyncio.Future) and not item.done():
                item.set_result(None)
        self._depth = 0
        if self._on_cancel is not None:
            self._on_cancel(self)

    async def _drain(self) -> None:
        while True:
            while not self._queue:
                self._wake.clear()
                await self._wake.wait()
            item = self._queue.popleft()
            if isinstance(item, asyncio.Future):
                if not item.done():
                    item.set_result(None)
                continue
            method, params, at = item
            self._depth -= 1
            self.lags.append(self._clock() - at)
            try:
                result = self.handler(method, params)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - one event, not the subscriber
                kind = type(exc).__name__
                if kind != self._failing:
                    self._failing = kind
                    # The type only: params carry page data, and so can a message.
                    log.warning(
                        "bidi: subscriber %s's handler failed (%s); failures of "
                        "that type are not logged again until one succeeds",
                        self.name, kind,
                    )
            else:
                self._failing = None
            # A turn for the reader and the other subscribers between events.
            await asyncio.sleep(0)


class Connection:
    """One held socket: commands by id, events to the subscribers that asked.

    ``on_close()`` runs on the loop when the socket closed and we did not close
    it, and must not block. ``connect(url)`` returns an awaitable connection;
    it is the seam a test replaces. ``clock`` times each event's lag.
    """

    def __init__(
        self,
        url: str,
        *,
        on_close: Callable[[], None] | None = None,
        connect=None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.url = url
        self.on_close = on_close
        self._connect = connect or _connect
        self._clock = clock
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._closing = False
        self._closed = True
        self._subscribers: list[Subscriber] = []
        # Events no subscriber asked for: Firefox fans every event out to every
        # socket on the session, whoever subscribed.
        self.unsolicited = 0
        # subscription id -> (events, contexts), so a reopen can ask again.
        self.subscriptions: dict[
            str, tuple[tuple[str, ...], tuple[str, ...] | None]
        ] = {}
        # The data collectors this connection added, removed when it closes.
        self.collectors: set[str] = set()

    @property
    def is_open(self) -> bool:
        return self._ws is not None and not self._closed

    @property
    def dropped(self) -> bool:
        """Closed, and not by us."""
        return self._closed and not self._closing and self._ws is not None

    async def open(self) -> None:
        """Open the socket; a ConnectionError when it cannot be opened."""
        self._closing = False
        try:
            self._ws = await asyncio.wait_for(self._connect(self.url), OPEN_TIMEOUT)
        except ConnectionError:
            raise
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise ConnectionError(
                f"the BiDi socket did not open ({type(exc).__name__})"
            ) from exc
        self._closed = False
        self._reader = asyncio.get_running_loop().create_task(self._read(self._ws))

    async def command(
        self, method: str, params: dict | None = None, *, timeout: float = REPLY_TIMEOUT
    ) -> dict:
        """The result of one command, or BidiError / TimeoutError /
        ConnectionError."""
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

    async def subscribe(
        self, events: Sequence[str], contexts: Sequence[str] | None = None
    ) -> str:
        """Subscribe to ``events``, in ``contexts`` when given, else across the
        browser; the subscription's id."""
        params: dict = {"events": list(events)}
        if contexts is not None:
            params["contexts"] = list(contexts)
        result = await self.command("session.subscribe", params)
        subscription = str(result.get("subscription") or "")
        if not subscription:
            raise BidiError("no subscription id", "session.subscribe returned none")
        self.subscriptions[subscription] = (
            tuple(events),
            tuple(contexts) if contexts is not None else None,
        )
        return subscription

    async def unsubscribe(self, subscription: str) -> None:
        await self.command("session.unsubscribe", {"subscriptions": [subscription]})
        self.subscriptions.pop(subscription, None)

    def listen(
        self,
        events: Iterable[str],
        handler: Handler,
        *,
        name: str,
        limit: int = QUEUE_LIMIT,
    ) -> Subscriber:
        """Hand ``events`` to ``handler`` on a queue and task of its own, until
        the subscriber is cancelled or the connection closes. Subscribing is a
        separate call: register the listener first, so nothing is missed."""
        subscriber = Subscriber(
            events,
            handler,
            name=name,
            limit=limit,
            clock=self._clock,
            on_cancel=self._forget,
        )
        self._subscribers.append(subscriber)
        return subscriber

    async def add_collector(self, params: dict) -> str:
        """Add a network data collector; its id, removed again on close."""
        result = await self.command("network.addDataCollector", params)
        collector = str(result.get("collector") or "")
        if not collector:
            raise BidiError("no collector id", "network.addDataCollector returned none")
        self.collectors.add(collector)
        return collector

    async def remove_collector(self, collector: str) -> None:
        await self.command("network.removeDataCollector", {"collector": collector})
        self.collectors.discard(collector)

    async def flush(self, timeout: float = REPLY_TIMEOUT) -> None:
        """Return once every event the browser sent before now was handled.

        One `session.status` round trip, so everything sent before its reply
        has been read and queued, then a barrier on every subscriber. A
        TimeoutError past ``timeout``."""
        async with asyncio.timeout(timeout):
            await self.command("session.status", timeout=timeout)
            if FLUSH_GRACE > 0:
                await asyncio.sleep(FLUSH_GRACE)
            await asyncio.gather(*(s.barrier() for s in tuple(self._subscribers)))

    async def close(self) -> None:
        """Unsubscribe, remove the collectors, stop every subscriber, close.

        Never raises, and always releases the socket and its reader, even when
        cancelled part-way (the cancellation is then re-raised)."""
        await self._shut(listeners=False)

    async def reopen(self) -> None:
        """Open again and ask for the same events in the same contexts: an id
        is its connection's. Collectors are not added again; listeners stay."""
        asked = list(self.subscriptions.values())
        await self._shut(listeners=True)
        self.subscriptions = {}
        self.collectors = set()
        await self.open()
        for events, contexts in asked:
            await self.subscribe(events, contexts)

    async def _shut(self, *, listeners: bool) -> None:
        self._closing = True
        reader, self._reader = self._reader, None
        try:
            if self.is_open:
                for subscription in list(self.subscriptions):
                    with contextlib.suppress(Exception):
                        await self.unsubscribe(subscription)
                for collector in list(self.collectors):
                    with contextlib.suppress(Exception):
                        await self.remove_collector(collector)
            ws = self._ws
            if ws is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ws.close(), OPEN_TIMEOUT)
        finally:
            if not listeners:
                for subscriber in tuple(self._subscribers):
                    subscriber.cancel()
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

    def _forget(self, subscriber: Subscriber) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(subscriber)

    def _route(self, method: str, params: dict, at: float) -> None:
        wanted = False
        for subscriber in tuple(self._subscribers):
            if method in subscriber.events:
                subscriber.deliver(method, params, at)
                wanted = True
        if not wanted:
            self.unsolicited += 1

    async def _read(self, ws) -> None:
        try:
            async for raw in ws:
                at = self._clock()
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
                elif message.get("type") == "event":
                    self._route(
                        str(message.get("method", "")), message.get("params") or {}, at
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
