"""The synchronous face of a BiDi connection, for the worker threads.

Tools and routes run on worker threads; a `Connection` lives on an event loop.
A `Channel` hands each command to that loop and blocks only the worker that
asked. `Channels.open_channel` lends the monitor's held connection for a
session when there is one, and otherwise opens one for the block (capture
spec §1).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine, Iterable, Iterator, Sequence
from typing import Any

from anyio.from_thread import start_blocking_portal

from ..faults import BidiUnavailable
from .connection import (
    OPEN_TIMEOUT,
    REPLY_TIMEOUT,
    BidiError,
    Connection,
    MessageTooBig,
    Subscriber,
)

log = logging.getLogger(__name__)

CHANNEL_DOWN = (
    "the browser's BiDi channel did not answer: retry, and if it persists the "
    "Grid's /se/bidi route is down"
)


def _on(loop: asyncio.AbstractEventLoop) -> bool:
    """Whether this thread is running ``loop``."""
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _wait(
    coro: Coroutine[Any, Any, Any],
    loop: asyncio.AbstractEventLoop,
    timeout: float | None,
):
    """Run ``coro`` on ``loop`` and block this thread for its result, at most
    ``timeout`` seconds (a TimeoutError, and ``coro`` cancelled)."""
    if _on(loop):
        coro.close()
        raise RuntimeError(
            "a BiDi channel was used on its own loop's thread, where waiting would "
            "deadlock: await the Connection instead"
        )
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout)
    except TimeoutError:
        future.cancel()
        raise


def _context_of(params: dict) -> str | None:
    """The browsing context an event is about: ``context``, or a log entry's
    ``source.context``."""
    context = params.get("context")
    if context is None:
        source = params.get("source")
        if isinstance(source, dict):
            context = source.get("context")
    return context if isinstance(context, str) else None


def _scoped(
    handler: Callable[[str, dict], None], contexts: Sequence[str]
) -> Callable[[str, dict], None]:
    """``handler``, for events in ``contexts`` only. Routing is by method, and
    Firefox fans an event out to every socket, so an event a scoped listener
    gets may be another context's; one with no context is nobody's."""
    scope = frozenset(contexts)

    def in_scope(method: str, params: dict) -> None:
        if _context_of(params) in scope:
            handler(method, params)

    return in_scope


@contextlib.contextmanager
def _unavailable() -> Iterator[None]:
    """A closed connection or a reply that never came is a 503; the browser's
    own error reply passes through, and so does a frame too large for the
    socket: already a 503 (`MessageTooBig`), unless its caller knows better."""
    try:
        yield
    except (BidiError, MessageTooBig):
        raise
    except (ConnectionError, TimeoutError) as exc:
        raise BidiUnavailable(CHANNEL_DOWN) from exc


class Channel:
    """``connection``, driven from a worker thread through ``loop``."""

    def __init__(self, connection: Connection, loop: asyncio.AbstractEventLoop):
        self.connection = connection
        self.loop = loop
        self._posted: set[asyncio.Task] = set()

    def call(
        self, method: str, params: dict | None = None, *, timeout: float = REPLY_TIMEOUT
    ) -> dict:
        """The result of one command: a BidiError when the browser refuses it,
        BidiUnavailable when the connection is down or silent."""
        with _unavailable():
            return _wait(
                self.connection.command(method, params, timeout=timeout),
                self.loop,
                timeout + 1,
            )

    def post(self, method: str, params: dict | None = None) -> None:
        """Send one command without waiting for its reply, from the loop (a
        handler answering an event) or a worker. A failure is logged, never
        raised."""
        coro = self._send(method, params)
        try:
            if _on(self.loop):
                task = self.loop.create_task(coro)
                self._posted.add(task)
                task.add_done_callback(self._posted.discard)
            else:
                asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError as exc:  # the loop has closed
            coro.close()
            log.debug("bidi: %s was not posted (%s)", method, type(exc).__name__)

    @contextlib.contextmanager
    def listen(
        self,
        events: Iterable[str],
        handler: Callable[[str, dict], None],
        *,
        contexts: Sequence[str] | None = None,
        name: str = "channel",
    ) -> Iterator[Subscriber]:
        """Hand ``events`` (in ``contexts`` when given) to ``handler`` on the
        loop for the block, then unsubscribe and stop. ``handler`` runs on the
        loop and must not block; it answers with `post`. Scoped, it sees only
        events whose context is in ``contexts``."""
        events = tuple(events)
        if contexts is not None:
            handler = _scoped(handler, contexts)
        with _unavailable():
            subscriber, subscription = _wait(
                self._listen(events, handler, contexts, name),
                self.loop,
                REPLY_TIMEOUT + 1,
            )
        try:
            yield subscriber
        finally:
            try:
                _wait(
                    self._unlisten(subscription, subscriber),
                    self.loop,
                    REPLY_TIMEOUT + 1,
                )
            except (BidiError, ConnectionError, TimeoutError) as exc:
                # The block's own outcome matters more; a connection that
                # closes later unsubscribes what is still recorded.
                log.debug("bidi: %s did not unsubscribe (%s)", name, type(exc).__name__)

    def flush(self, timeout: float = REPLY_TIMEOUT) -> None:
        """Return once every event the browser sent before now was handled."""
        with _unavailable():
            _wait(self.connection.flush(timeout), self.loop, timeout + 1)

    async def _send(self, method: str, params: dict | None) -> None:
        try:
            await self.connection.command(method, params)
        except Exception as exc:  # noqa: BLE001 - posted: nobody is waiting
            log.debug("bidi: posted %s failed (%s)", method, type(exc).__name__)

    async def _listen(self, events, handler, contexts, name):
        # Listen before subscribing, so no event lands between the two.
        subscriber = self.connection.listen(events, handler, name=name)
        try:
            subscription = await self.connection.subscribe(events, contexts)
        except BaseException:
            subscriber.cancel()
            raise
        return subscriber, subscription

    async def _unlisten(self, subscription: str, subscriber: Subscriber) -> None:
        try:
            await self.connection.unsubscribe(subscription)
        finally:
            subscriber.cancel()


class Channels:
    """Where a worker gets a `Channel` for a session.

    ``url_for(session_id)`` is the browser's BiDi address; ``held(session_id)``
    the connection the monitor holds for it, if any; ``loop()`` the loop that
    runs them, if any. ``connect`` is `Connection`'s test seam.
    """

    def __init__(
        self,
        url_for: Callable[[str], str],
        *,
        held: Callable[[str], Connection | None] = lambda _s: None,
        loop: Callable[[], asyncio.AbstractEventLoop | None] = lambda: None,
        connect=None,
    ):
        self.url_for = url_for
        self.held = held
        self.loop = loop
        self.connect = connect

    @contextlib.contextmanager
    def open_channel(
        self, session_id: str, *, private: bool = False
    ) -> Iterator[Channel]:
        """A channel to ``session_id``'s browser for the block: the held
        connection, lent and left open, or one opened for the block and closed
        after — always the latter when ``private``, for a command whose reply
        may be too large for a socket and so close it. BidiUnavailable when
        none can be opened."""
        loop = self.loop()
        if loop is not None and not loop.is_running():
            loop = None
        if loop is not None:
            connection = None if private else self.held(session_id)
            if connection is not None and connection.is_open:
                yield Channel(connection, loop)
                return
            with self._opened(session_id, loop) as channel:
                yield channel
            return
        # No loop to borrow (a unit test, a CLI): a private one for the block.
        with start_blocking_portal() as portal:
            private = portal.call(asyncio.get_running_loop)
            with self._opened(session_id, private) as channel:
                yield channel

    @contextlib.contextmanager
    def _opened(
        self, session_id: str, loop: asyncio.AbstractEventLoop
    ) -> Iterator[Channel]:
        connection = Connection(self.url_for(session_id), connect=self.connect)
        with _unavailable():
            _wait(connection.open(), loop, OPEN_TIMEOUT + 1)
        try:
            yield Channel(connection, loop)
        finally:
            # `close` bounds every step itself and never raises.
            _wait(connection.close(), loop, None)
