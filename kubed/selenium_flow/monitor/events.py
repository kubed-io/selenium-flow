"""What the monitor says, and the bus that carries it.

**Events carry no values** (programme R6). Every field is a scalar — a name,
the session's ``session_id``, a kind, a time — never an argument, a URL, a
header, a body or a secret, so any subscriber is safe by construction.
`EVENTS` lists every type, and a test holds the rule over all of them.

**Publishing never blocks a call.** `publish` is called from worker threads
(FastMCP's sync tools, Starlette's routes) and hands the event to the event
loop; each subscriber has a bounded queue, drained by a callback the loop
schedules, and an event that does not fit is dropped and counted. A subscriber
is a plain function run on the loop: it must not block, and one that needs to
await starts its own task. No task waits on the bus, so nothing here runs when
nothing is published (AGENTS.md, "Refresh, not cleanup").

`Bus` is the interface; `LocalBus` is this process's. A Redis bus could stand
behind the same two methods for a second replica (programme R5); it is not
built.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections import deque
from collections.abc import Callable, Iterable
from typing import ClassVar, Literal, Protocol

log = logging.getLogger(__name__)

# How many events one subscriber may fall behind before the next is dropped.
QUEUE_LIMIT = 1000

Cause = Literal["ended", "gone", "lost"]
Surface = Literal["mcp", "http", "flow"]
Outcome = Literal["ok", "4xx", "5xx"]


@dataclasses.dataclass(frozen=True)
class Event:
    """The base of every event: its kind, and nothing else."""

    KIND: ClassVar[str] = ""

    @property
    def kind(self) -> str:
        return self.KIND


@dataclasses.dataclass(frozen=True)
class SessionOpened(Event):
    """A session this server opened is bound to its workspace."""

    KIND: ClassVar[str] = "session.opened"
    workspace: str
    session_id: str
    browser: str
    grid_timeout: int | None
    reopened: bool
    at: float


@dataclasses.dataclass(frozen=True)
class SessionEnded(Event):
    """A session has ended: ``ended`` (we quit it), ``gone`` (the Grid's
    listing stopped showing it) or ``lost`` (a call found it gone)."""

    KIND: ClassVar[str] = "session.ended"
    workspace: str
    session_id: str
    cause: Cause
    at: float


@dataclasses.dataclass(frozen=True)
class CallFinished(Event):
    """One call finished. Declared here, emitted by its first consumer (the
    session monitor spec, ruling 8): once per call, at the recipe's host."""

    KIND: ClassVar[str] = "call.finished"
    workspace: str
    session_id: str
    browser: str
    tool: str
    surface: Surface
    outcome: Outcome
    started: float
    duration: float


EVENTS: dict[str, type[Event]] = {
    cls.KIND: cls for cls in (SessionOpened, SessionEnded, CallFinished)
}


class Subscription:
    """One subscriber: the kinds it hears, its handler and its queue."""

    def __init__(self, bus, kinds, handler, name: str, limit: int):
        self.kinds = frozenset(kinds)
        self.handler = handler
        self.name = name
        self.limit = limit
        self.dropped = 0
        self.queue: deque[Event] = deque()
        self._bus = bus
        self._scheduled = False
        self._overflowing = False
        self._failing = False

    def cancel(self) -> None:
        """Hear nothing more."""
        self._bus._remove(self)


class Bus(Protocol):
    def publish(self, event: Event) -> None: ...

    def subscribe(
        self,
        kinds: str | Iterable[str],
        handler: Callable[[Event], None],
        *,
        name: str,
        limit: int = QUEUE_LIMIT,
    ) -> Subscription: ...


class LocalBus:
    """The bus in this process. ``start`` binds it to the running loop."""

    def __init__(self):
        # Replaced, never mutated: a publisher iterating it in another thread
        # sees one list or the other, never one half changed.
        self._subs: tuple[Subscription, ...] = ()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped = False

    def start(self) -> None:
        """Deliver on the running loop from now on. Called on it."""
        self._loop = asyncio.get_running_loop()
        self._stopped = False

    async def stop(self) -> None:
        """Deliver nothing more; what is queued already is handed out."""
        self._stopped = True
        await asyncio.sleep(0)
        self._loop = None

    def subscribe(
        self,
        kinds: str | Iterable[str],
        handler: Callable[[Event], None],
        *,
        name: str,
        limit: int = QUEUE_LIMIT,
    ) -> Subscription:
        kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
        unknown = set(kinds) - set(EVENTS)
        if unknown:
            raise ValueError(f"no such event: {', '.join(sorted(unknown))}")
        sub = Subscription(self, kinds, handler, name, limit)
        self._subs = (*self._subs, sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        self._subs = tuple(s for s in self._subs if s is not sub)

    def publish(self, event: Event) -> None:
        """Hand ``event`` to every subscriber of its kind. Never blocks."""
        if self._stopped:
            return
        loop = self._loop
        if loop is None:
            # Not started: tests, and nothing in production (the lifespan
            # starts the bus before any request). Delivered here and now.
            self._fan_out(event, inline=True)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fan_out(event)
            return
        # A loop closed under a late caller refuses; the event is dropped.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._fan_out, event)

    def _fan_out(self, event: Event, inline: bool = False) -> None:
        for sub in self._subs:
            if event.kind not in sub.kinds:
                continue
            if len(sub.queue) >= sub.limit:
                sub.dropped += 1
                if not sub._overflowing:
                    sub._overflowing = True
                    log.warning(
                        "bus: %s is %d events behind; dropping %s",
                        sub.name,
                        sub.limit,
                        event.kind,
                    )
                continue
            sub.queue.append(event)
            if inline or self._loop is None:
                self._drain(sub)
            elif not sub._scheduled:
                sub._scheduled = True
                self._loop.call_soon(self._drain, sub)

    def _drain(self, sub: Subscription) -> None:
        sub._scheduled = False
        while sub.queue:
            event = sub.queue.popleft()
            try:
                sub.handler(event)
            except Exception as exc:  # noqa: BLE001 - one subscriber, not the bus
                if not sub._failing:
                    sub._failing = True
                    # The type alone: a message could carry what an event never does.
                    log.warning(
                        "bus: %s failed on %s (%s)",
                        sub.name,
                        event.kind,
                        type(exc).__name__,
                    )
            else:
                sub._failing = False
        sub._overflowing = False
