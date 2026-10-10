"""The session monitor: which sessions this server opened, which ended and
why, and which it still owes something.

A *session* is the live browser a workspace holds, and it holds the Grid's
session, whose id is its ``session_id``. **Every session is announced; only
owed ones are watched.** `opened` and `ended` publish ``session.opened`` and
``session.ended`` for every session the workspaces open or end. A session is
*watched* only while something is owed on it — a recording (``"recording"``),
and from E3 capture — and only watched ones cost anything: one task, which runs
while a watch exists and ends with the last (AGENTS.md: a bounded wait for
something this server was told to expect).

**Whether a watched session still runs is read off the Grid's status, never
asked of the browser.** Each look takes one ``GET /status`` listing
(``listing``, in a worker thread), shared by every watch; a WebDriver command
would be activity and would stop the Grid ever reaping the browser. A session
is **gone** when two listings in a row, each showing at least one node and
taken after its watch began, do not list it — or one, after its connection
dropped. A listing that failed says nothing, and so does one that shows no
nodes (a hub restarting before its nodes register again) — until listings with
no nodes have run unbroken for the watch's idle timeout plus a tick, from the
run's first or the watch's start, whichever is later: then each is a miss.
Every WebDriver command and BiDi frame reaches a node through the hub, so a
node unlisted that long received no activity and has reaped the session, or is
gone itself (KEDA at zero, an evicted node, a Grid redeployed).

**A connection is a channel** (`bidi.Connection`): held only for a reason
that names BiDi events (``events_for``), until the watch's deadline — its last
activity plus its node's ``sessionTimeout`` — and then closed, so a busy page's
events cannot keep the browser alive past the Grid's own timer (programme R3).
The Grid reaps; this server ends no session on a timer. A connection outlives
its session (measured), so the monitor closes it when the watch ends, and
closes it clean: `Connection.close` unsubscribes and removes the data
collectors it added first (capture spec, ruling 7).

**Capture attaches before the first page** (capture spec, ruling 6). `attach`
holds the connection now — open, subscribe, add the data collector — and
returns only then, so the open path can navigate knowing nothing is missed; a
failure is `BidiUnavailable`, a 503. What the connection delivers goes to the
`Sink` (`capture.Capture`), told when a hold starts (``again`` when this
session was held before: Chrome replays what it already sent), when that
replay is over, and when a hold ends. A hold that fails is tried again on a
back-off of 1, 2, 4 … 30 seconds.

Calls come from worker threads (FastMCP's sync tools, Starlette's routes) and
are handed to the loop, as the recordings collector's are. Nothing here logs a
``session_id`` (AGENTS.md).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

import anyio

from ..bidi import Connection
from ..faults import BidiUnavailable
from .events import Bus, SessionEnded, SessionOpened

log = logging.getLogger(__name__)

# Seconds between listings: the Grid's own clean-up runs every 30 s.
LISTING_TICK = 30.0
# Listings in a row that must miss a session before it is gone.
MISSES = 2
# The Grid's default idle timeout, for a watch whose node did not say.
DEFAULT_TIMEOUT = 300
# Session ids remembered as ended, so each is announced once.
ENDED_MEMORY = 4096
# Seconds `attach` waits for the connection to be held.
ATTACH_TIMEOUT = 15.0
# The longest wait between attempts to hold a connection: 1, 2, 4 … 30 s.
BACKOFF_MAX = 30.0
ATTACH_FAILED = (
    "capture needs the browser's BiDi channel and the Grid's /se/bidi route "
    "did not answer, so this browser was ended: retry, or open without capture"
)


class Sink(Protocol):
    """What hears a held connection: `capture.Capture`. Called on the loop."""

    def attached(
        self,
        workspace: str,
        session_id: str,
        browser: str,
        top: str | None,
        *,
        again: bool,
    ) -> None: ...

    def ingest(
        self, workspace: str, session_id: str, method: str, params: dict
    ) -> None: ...

    def replayed(self, workspace: str, session_id: str) -> None: ...

    def detached(self, workspace: str, session_id: str) -> None: ...


class _NoSink:
    """The sink of a monitor given none: hears everything, keeps nothing."""

    def attached(self, workspace, session_id, browser, top, *, again) -> None:
        pass

    def ingest(self, workspace, session_id, method, params) -> None:
        pass

    def replayed(self, workspace, session_id) -> None:
        pass

    def detached(self, workspace, session_id) -> None:
        pass


@dataclass
class Watch:
    """One session the monitor owes something, and what it knows of it."""

    session_id: str
    workspace: str
    browser: str
    owed: set[str]
    since: float
    touched: float
    timeout: int | None = None
    misses: int = 0
    connection: Connection | None = field(default=None, repr=False)
    dropped: bool = False
    failures: int = 0  # holds failed in a row
    retry_at: float = 0.0  # no hold is tried before this
    subscribed: frozenset[str] = frozenset()  # the events the connection asked for
    attached_once: bool = False  # a hold succeeded on this session before
    # Held while the connection is opened, extended or closed, so a look and an
    # attach never work on one watch's connection at once.
    busy: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    @property
    def deadline(self) -> float:
        return self.touched + (self.timeout or DEFAULT_TIMEOUT)


class Monitor:
    """The watch registry, the listing and the held connections.

    ``listing()`` returns ``(nodes, {session_id: timeout_seconds | None})``
    from one ``GET /status`` (`Grid.listing`). ``socket_url(session_id)`` is
    the browser's BiDi address (`Grid.bidi_url`). ``events_for`` maps a reason
    to the BiDi events it needs: a watch holds a connection only while it owes
    such a reason. ``collectors_for`` maps a reason to the parameters of the
    data collector it adds when a connection is held. ``connect(url,
    on_close=)`` builds a `Connection`; a test seam. ``sink`` hears what a
    connection delivers.
    """

    def __init__(
        self,
        bus: Bus,
        *,
        listing: Callable[[], tuple[int, Mapping[str, int | None]]],
        socket_url: Callable[[str], str] | None = None,
        events_for: Mapping[str, tuple[str, ...]] | None = None,
        collectors_for: Mapping[str, dict] | None = None,
        connect: Callable[..., Connection] | None = None,
        sink: Sink | None = None,
        clock: Callable[[], float] = time.time,
        tick: float = LISTING_TICK,
    ):
        self.bus = bus
        self.listing = listing
        self.socket_url = socket_url
        self.events_for = dict(events_for or {})
        self.collectors_for = dict(collectors_for or {})
        self.connect = connect or Connection
        self.sink: Sink = sink if sink is not None else _NoSink()
        self.clock = clock
        self.tick = tick
        self.watches: dict[str, Watch] = {}
        self._ended: OrderedDict[str, None] = OrderedDict()
        self._ended_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._nudge: asyncio.Event | None = None
        self._stopping = False
        self._closing: set[asyncio.Task] = set()
        self._attaching: set[asyncio.Task] = set()
        self._listing_failed = False
        self._empty_since: float | None = None  # the current run of no-node listings
        self._failed_logged: set[str] = set()  # sessions whose failing hold was logged

    # ---- from any thread ---------------------------------------------------

    def opened(
        self,
        workspace: str,
        session_id: str,
        browser: str,
        grid_timeout: int | None,
        *,
        reopened: bool = False,
    ) -> None:
        self.bus.publish(
            SessionOpened(
                workspace, session_id, browser, grid_timeout, reopened, self.clock()
            )
        )

    def ended(self, workspace: str, session_id: str, cause: str) -> bool:
        """Announce that ``session_id`` ended, once. False if it already was."""
        with self._ended_lock:
            if session_id in self._ended:
                return False
            self._ended[session_id] = None
            while len(self._ended) > ENDED_MEMORY:
                self._ended.popitem(last=False)
        self.bus.publish(SessionEnded(workspace, session_id, cause, self.clock()))
        self._post(lambda: self._drop(session_id))
        return True

    def watch(
        self,
        session_id: str,
        workspace: str,
        reason: str,
        *,
        browser: str = "",
        grid_timeout: int | None = None,
    ) -> None:
        """Owe ``reason`` on this session, watching it from now."""
        now = self.clock()

        def apply() -> None:
            w, _ = self._owe(session_id, workspace, reason, browser, grid_timeout, now)
            if w is not None:
                self._ensure()

        self._post(apply)

    def attach(
        self,
        workspace: str,
        session_id: str,
        reason: str,
        *,
        browser: str = "",
        grid_timeout: int | None = None,
    ) -> None:
        """Owe ``reason`` and hold its connection before returning: opened,
        subscribed and collecting, so nothing the next page does is missed.

        From a worker thread, never the loop's own. `BidiUnavailable` when the
        connection cannot be held within ``ATTACH_TIMEOUT``; the reason is then
        owed no more."""
        loop = self._loop
        if loop is None:
            raise RuntimeError("the monitor is not running")
        future = asyncio.run_coroutine_threadsafe(
            self._attach(workspace, session_id, reason, browser, grid_timeout), loop
        )
        try:
            future.result(ATTACH_TIMEOUT)
        except Exception as exc:
            future.cancel()  # a hold still under way closes what it opened
            log.info(
                "monitor: capture did not attach for %s (%s)",
                workspace,
                type(exc).__name__,
            )
            raise BidiUnavailable(ATTACH_FAILED) from exc

    def release(self, session_id: str, reason: str) -> None:
        """Owe ``reason`` no more; a watch owing nothing is dropped."""
        self._post(lambda: self._unowe(session_id, reason))

    def touch(self, session_id: str, at: float | None = None) -> None:
        """A call reached this session: its deadline moves on."""
        at = self.clock() if at is None else at

        def apply() -> None:
            w = self.watches.get(session_id)
            if w is not None and at > w.touched:
                w.touched = at
                self._wake()

        self._post(apply)

    def connection(self, session_id: str) -> Connection | None:
        """The connection held to this session's browser, if one is open."""
        w = self.watches.get(session_id)
        conn = w.connection if w is not None else None
        return conn if conn is not None and conn.is_open else None

    def capturing(self, session_id: str) -> bool:
        """Whether a connection to this session's browser is held right now."""
        w = self.watches.get(session_id)
        return bool(w and w.connection and w.connection.is_open)

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """The loop the connections live on, while the monitor runs."""
        return self._loop

    # ---- on the loop -------------------------------------------------------

    def watching(self, session_id: str) -> frozenset[str]:
        w = self.watches.get(session_id)
        return frozenset(w.owed) if w else frozenset()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._nudge = asyncio.Event()
        if self.watches:
            self._ensure()

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        attaching = set(self._attaching)
        for pending in attaching:
            pending.cancel()  # each closes what it opened
        if attaching:
            await asyncio.wait(attaching)
        for w in list(self.watches.values()):  # a close can end a watch
            if w.connection is not None:
                conn, w.connection, w.subscribed = w.connection, None, frozenset()
                await conn.close()
                self._detached(w)
        for closing in list(self._closing):
            with contextlib.suppress(Exception):
                await closing
        self._loop = None

    async def look(self) -> None:
        """One pass: the listing, what it says is gone, and the connections."""
        now = self.clock()
        try:
            nodes, running = await anyio.to_thread.run_sync(self.listing)
        except Exception as exc:  # noqa: BLE001 - the Grid can blip; it says nothing
            if not self._listing_failed:
                self._listing_failed = True
                log.info(
                    "monitor: the Grid's status is unavailable (%s); every watched "
                    "session counts as running",
                    type(exc).__name__,
                )
            listed, nodes, running = False, 0, {}
        else:
            listed = True
            if self._listing_failed:
                self._listing_failed = False
                log.info("monitor: the Grid's status answers again")
            if nodes:
                self._empty_since = None
            elif self._empty_since is None:
                self._empty_since = now
        for w in list(self.watches.values()):
            if self.watches.get(w.session_id) is not w:
                continue  # dropped while this look awaited
            self._notice_drop(w)
            if listed and now >= w.since and (nodes or self._unlisted(w, now)):
                if w.session_id in running:
                    w.misses = 0
                    w.dropped = False
                    if running[w.session_id]:
                        w.timeout = running[w.session_id]
                else:
                    w.misses += 1
                    if w.misses >= MISSES or w.dropped:
                        self.ended(w.workspace, w.session_id, "gone")  # drops it
                        continue
            await self._keep(w, now)

    # ---- inside -------------------------------------------------------------

    def _unlisted(self, w: Watch, now: float) -> bool:
        """No node listed for the whole of this watch's idle timeout and a tick."""
        start = max(self._empty_since or now, w.since)
        return now - start >= (w.timeout or DEFAULT_TIMEOUT) + self.tick

    def _wanted(self, w: Watch) -> list[str]:
        return sorted({e for reason in w.owed for e in self.events_for.get(reason, ())})

    def _owe(
        self,
        session_id: str,
        workspace: str,
        reason: str,
        browser: str,
        grid_timeout: int | None,
        now: float,
    ) -> tuple[Watch | None, bool]:
        """The watch now owing ``reason``, and whether it was not owed before;
        None for a session already ended, which has nothing left to owe."""
        with self._ended_lock:
            if session_id in self._ended:
                return None, False
        w = self.watches.get(session_id)
        if w is None:
            w = self.watches[session_id] = Watch(
                session_id,
                workspace,
                browser,
                set(),
                since=now,
                touched=now,
                timeout=grid_timeout,
            )
        added = reason not in w.owed
        w.owed.add(reason)
        if grid_timeout:
            w.timeout = grid_timeout
        if browser and not w.browser:
            w.browser = browser
        return w, added

    def _unowe(self, session_id: str, reason: str) -> None:
        w = self.watches.get(session_id)
        if w is None:
            return
        w.owed.discard(reason)
        if not w.owed:
            self._drop(session_id)
        else:
            self._wake()

    async def _attach(
        self,
        workspace: str,
        session_id: str,
        reason: str,
        browser: str,
        grid_timeout: int | None,
    ) -> None:
        """`attach` on the loop: owe the reason, then hold the connection now
        rather than at the next look. On any failure the reason is released."""
        task = asyncio.current_task()
        self._attaching.add(task)
        try:
            if self._stopping or self.socket_url is None:
                raise ConnectionError("this monitor holds no connections")
            now = self.clock()
            w, added = self._owe(
                session_id, workspace, reason, browser, grid_timeout, now
            )
            if w is None:
                raise ConnectionError("the session has already ended")
            w.touched = max(w.touched, now)  # an open is activity
            try:
                async with w.busy:
                    self._notice_drop(w)
                    if w.connection is None:
                        await self._hold(w)
                    else:
                        await self._extend(w, w.connection)
            except BaseException:
                if added:
                    self._unowe(session_id, reason)
                raise
            self._ensure()
        finally:
            self._attaching.discard(task)

    async def _keep(self, w: Watch, now: float) -> None:
        """Hold a connection while events are owed and the deadline has not
        passed, past it close it clean, and back off a hold that fails."""
        if w.busy.locked():
            return  # an attach is at it
        if not self._wanted(w) or now >= w.deadline:
            if w.connection is not None:
                async with w.busy:
                    conn, w.connection, w.subscribed = w.connection, None, frozenset()
                    await conn.close()  # unsubscribes and removes the collector
                self._detached(w)
            return
        if self.socket_url is None or now < w.retry_at:
            return
        try:
            async with w.busy:
                if w.connection is None:
                    await self._hold(w)
                else:
                    await self._extend(w, w.connection)
        except Exception as exc:  # noqa: BLE001 - tried again after a back-off
            self._failed(w, now, exc)

    async def _hold(self, w: Watch) -> None:
        """Open a connection, learn the top context, listen, subscribe and add
        the collectors; after a hold before, flush Chrome's replay. On any
        failure, a cancellation included, close what was opened and tell the
        sink, then raise."""
        wanted = self._wanted(w)
        again = w.attached_once
        conn = None
        try:
            conn = self.connect(self.socket_url(w.session_id), on_close=self._wake)
            await conn.open()
            tree = await conn.command("browsingContext.getTree", {"maxDepth": 0})
            top = next(
                (
                    c.get("context")
                    for c in tree.get("contexts") or ()
                    if isinstance(c, dict)
                ),
                None,
            )
            self.sink.attached(w.workspace, w.session_id, w.browser, top, again=again)
            conn.listen(wanted, self._deliverer(w), name="capture")
            await conn.subscribe(wanted)
            for reason in sorted(w.owed):
                params = self.collectors_for.get(reason)
                if params is not None:
                    await conn.add_collector(params)
            if again:
                await conn.flush()
                self.sink.replayed(w.workspace, w.session_id)
            if self.watches.get(w.session_id) is not w:
                raise ConnectionError("the session ended while it was attached")
        except BaseException:
            try:
                if conn is not None:
                    await asyncio.shield(self._close_later(conn))
            finally:
                self._detached(w)
            raise
        w.connection, w.subscribed, w.attached_once = conn, frozenset(wanted), True
        self._succeeded(w)

    async def _extend(self, w: Watch, conn: Connection) -> None:
        """Subscribe the events a reason owed since the hold added."""
        added = [e for e in self._wanted(w) if e not in w.subscribed]
        if not added:
            return
        listener = conn.listen(added, self._deliverer(w), name="capture")
        try:
            await conn.subscribe(added)
        except BaseException:
            listener.cancel()
            raise
        w.subscribed = w.subscribed | frozenset(added)
        self._succeeded(w)

    def _deliverer(self, w: Watch) -> Callable[[str, dict], None]:
        return functools.partial(self._deliver, w.workspace, w.session_id)

    def _deliver(
        self, workspace: str, session_id: str, method: str, params: dict
    ) -> None:
        self.sink.ingest(workspace, session_id, method, params)

    def _notice_drop(self, w: Watch) -> None:
        """A connection closed under us: let it go, and look at the session now."""
        conn = w.connection
        if conn is not None and conn.dropped:
            w.connection, w.subscribed, w.dropped = None, frozenset(), True
            self._detached(w)
            if self._loop is not None:
                self._close_later(conn)  # stops its listeners

    def _succeeded(self, w: Watch) -> None:
        w.failures, w.retry_at = 0, 0.0
        self._failed_logged.discard(w.session_id)

    def _failed(self, w: Watch, now: float, exc: Exception) -> None:
        w.retry_at = now + min(BACKOFF_MAX, 2 ** min(w.failures, 5))
        w.failures += 1
        if w.session_id not in self._failed_logged:
            self._failed_logged.add(w.session_id)
            log.info(
                "monitor: no BiDi connection for %s (%s); trying again",
                w.workspace,
                type(exc).__name__,
            )

    def _detached(self, w: Watch) -> None:
        try:
            self.sink.detached(w.workspace, w.session_id)
        except Exception as exc:  # noqa: BLE001 - a hold ends whatever the sink does
            log.warning("monitor: the capture sink failed (%s)", type(exc).__name__)

    def _close_later(self, conn: Connection) -> asyncio.Task:
        """Close ``conn`` in a task `stop` waits for."""
        closing = self._loop.create_task(conn.close())
        self._closing.add(closing)
        closing.add_done_callback(self._closing.discard)
        return closing

    def _drop(self, session_id: str) -> None:
        """Forget the watch; its connection is closed in a task `stop` waits for."""
        w = self.watches.pop(session_id, None)
        self._failed_logged.discard(session_id)
        if w is None or w.connection is None:
            return
        conn, w.connection, w.subscribed = w.connection, None, frozenset()
        self._detached(w)
        if self._loop is None:
            return  # not started: nothing was ever opened
        self._close_later(conn)

    def _post(self, fn) -> None:
        loop = self._loop
        if loop is None:
            fn()  # not started: applied here; `start` takes it from there
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn()
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(fn)

    def _wake(self) -> None:
        if self._nudge is not None:
            self._nudge.set()

    def _ensure(self) -> None:
        loop = self._loop
        if loop is None or self._stopping or self.running:
            return
        self._task = loop.create_task(self._run())

    def _wait(self) -> float:
        now = self.clock()
        waits = [w.deadline - now for w in self.watches.values() if w.connection]
        waits += [  # a hold backing off is tried again when its wait is over
            w.retry_at - now
            for w in self.watches.values()
            if w.connection is None and w.retry_at > now
        ]
        return max(0.05, min([self.tick, *waits]))

    async def _run(self) -> None:
        try:
            while self.watches:
                # Replaced before the look, so a wake during it is not lost.
                self._nudge = asyncio.Event()
                try:
                    await self.look()
                except Exception as exc:  # noqa: BLE001 - logged; the next look retries
                    log.warning("monitor: a look failed (%s)", type(exc).__name__)
                if not self.watches:
                    break
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._nudge.wait(), self._wait())
        finally:
            if self._task is asyncio.current_task():
                self._task = None
