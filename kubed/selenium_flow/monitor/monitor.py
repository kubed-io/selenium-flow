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

**The deadline never closes a connection under a call.** A call is
`calling` from its start and `called` at its end (`workspace.calls`), each a
touch; while any call on a session is in flight its connection is kept, and
the deadline counts again from the last one's end. A call borrows the held
connection (a save's spare tab, a body's fetch, the call's own flush), and a
close in the middle would take those with it.

**Capture attaches before the first page** (capture spec, ruling 6). `attach`
holds the connection now — open, subscribe, add the data collector — and
returns only then, so the open path can navigate knowing nothing is missed; a
failure is `BidiUnavailable`, a 503. `attach_here` is the same from the loop:
a person's Control follows the pages that way (live control spec, ruling 27).
A hold that fails is tried again on a back-off of 1, 2, 4 … 30 seconds.

**A sink per reason** (live control spec, R7). ``sinks`` maps a reason to its
`Sink` — ``capture`` to `capture.Capture`, ``control`` to `live.pages.Pages`
— and each hears only its own: ``attached`` when a hold opens with its reason
owed, or when `_extend` adds the reason to a hold already open (``again``
false, the top context the hold learned); then each event of its
``events_for``, ``replayed`` (``again``: Chrome replays what it already sent)
and ``lost``; and ``detached`` when the hold ends, or when its reason is let go
while the connection stays for another. One connection serves them all: two
reasons owed at once share one socket, and an event both asked for reaches
both.

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
UNAVAILABLE = "the browser's BiDi channel (the Grid's /se/bidi route) did not answer"


class Sink(Protocol):
    """What hears a held connection for one reason: `capture.Capture`,
    `live.pages.Pages`. Called on the loop."""

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

    def lost(self, workspace: str, session_id: str) -> None: ...


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
    # The reasons whose sinks were told `attached` on the connection and not
    # yet `detached`: what an event, a drop or a replay is routed to.
    heard: set[str] = field(default_factory=set)
    top: str | None = None  # the top-level context the hold learned
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
    on_close=)`` builds a `Connection`; a test seam. ``sinks`` maps a reason
    to the `Sink` that hears what a connection delivers for it.
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
        sinks: Mapping[str, Sink] | None = None,
        clock: Callable[[], float] = time.time,
        tick: float = LISTING_TICK,
    ):
        self.bus = bus
        self.listing = listing
        self.socket_url = socket_url
        self.events_for = dict(events_for or {})
        self.collectors_for = dict(collectors_for or {})
        self.connect = connect or Connection
        self.sinks: dict[str, Sink] = dict(sinks or {})
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
        # Calls in flight per session, counted from the worker threads that
        # make them, so a look sees a call the moment it starts.
        self._in_flight: dict[str, int] = {}
        self._in_flight_lock = threading.Lock()

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
        resume: bool = False,
    ) -> None:
        """Owe ``reason`` and hold its connection before returning: opened,
        subscribed and collecting, so nothing the next page does is missed.

        From a worker thread, never the loop's own. `BidiUnavailable` when the
        connection cannot be held within ``ATTACH_TIMEOUT``; the reason is then
        owed no more. A call's ``resume`` returns without trying when, once it
        has the watch, a back-off is pending: the call decided to hold on its
        own thread, and a look's hold that was in flight then may have failed
        since (`backing_off`)."""
        loop = self._loop
        if loop is None:
            raise RuntimeError("the monitor is not running")
        expired = threading.Event()  # set before the cancel it explains
        future = asyncio.run_coroutine_threadsafe(
            self._attach(
                workspace,
                session_id,
                reason,
                browser,
                grid_timeout,
                resume,
                expired=expired.is_set,
            ),
            loop,
        )
        try:
            future.result(ATTACH_TIMEOUT)
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                expired.set()
            future.cancel()  # a hold still under way closes what it opened
            log.info(
                "monitor: capture did not attach for %s (%s)",
                workspace,
                type(exc).__name__,
            )
            raise BidiUnavailable(ATTACH_FAILED) from exc

    async def attach_here(
        self, workspace: str, session_id: str, reason: str, *, browser: str = ""
    ) -> None:
        """`attach` from the loop: owe ``reason`` and hold its connection, or
        extend the one held, before returning. `BidiUnavailable` on any
        failure within ``ATTACH_TIMEOUT``, the reason then owed no more; a
        cancellation closes what the hold opened and propagates."""
        try:
            if self._loop is None:
                raise ConnectionError("the monitor is not running")
            async with asyncio.timeout(ATTACH_TIMEOUT) as timeout:
                await self._attach(
                    workspace,
                    session_id,
                    reason,
                    browser,
                    None,
                    expired=timeout.expired,
                )
        except Exception as exc:
            log.info(
                "monitor: %s did not attach for %s (%s)",
                reason,
                workspace,
                type(exc).__name__,
            )
            raise BidiUnavailable(UNAVAILABLE) from exc

    def release(self, session_id: str, reason: str) -> None:
        """Owe ``reason`` no more; a watch owing nothing is dropped."""
        self._post(lambda: self._unowe(session_id, reason))

    def touch(self, session_id: str, at: float | None = None) -> None:
        """A call reached this session: its deadline moves on."""
        at = self.clock() if at is None else at
        self._post(lambda: self._touched(session_id, at))

    def calling(self, session_id: str) -> None:
        """A call on this session starts: a touch, and its connection is kept
        until the call is `called`, whatever the deadline says."""
        with self._in_flight_lock:
            self._in_flight[session_id] = self._in_flight.get(session_id, 0) + 1
        self.touch(session_id)

    def called(self, session_id: str) -> None:
        """A call on this session ended: a touch, from which the deadline
        counts once no other call is in flight.

        The count drops on the loop, after the touch, in one step: dropped
        here first, a look that ran before the touch reached the loop would
        find a call longer than the timeout neither counted nor touched, and
        close the connection under its flush (PR review, round 2)."""
        at = self.clock()

        def apply() -> None:
            self._touched(session_id, at)
            self._uncount(session_id)

        if not self._post(apply):
            self._uncount(session_id)  # no loop to take it: the count still drops

    def connection(self, session_id: str) -> Connection | None:
        """The connection held to this session's browser, if one is open."""
        w = self.watches.get(session_id)
        conn = w.connection if w is not None else None
        return conn if conn is not None and conn.is_open else None

    def owes(self, session_id: str, reason: str) -> bool:
        """Whether this session's watch owes ``reason`` right now, held or not."""
        w = self.watches.get(session_id)
        return w is not None and reason in w.owed

    def capturing(self, session_id: str) -> bool:
        """Whether capture's connection to this session's browser is held right
        now: held, and capture among the reasons it is held for. A connection
        held only for a person's control is not capture (live control spec,
        ruling 28)."""
        w = self.watches.get(session_id)
        return bool(
            w and w.connection and w.connection.is_open and "capture" in w.heard
        )

    def backing_off(self, session_id: str) -> bool:
        """Whether a hold on this session failed and its back-off has not run
        out: a call then does not try again (`Calls`), nor wait for it."""
        w = self.watches.get(session_id)
        return w is not None and w.retry_at > self.clock()

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

    def _told(self, w: Watch) -> list[str]:
        """The owed reasons that have a sink to tell, in order."""
        return sorted(reason for reason in w.owed if reason in self.sinks)

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
            return
        if reason in w.heard:
            # The connection stays for another reason: this one's sink is done.
            w.heard.discard(reason)
            self._tell_detached(w, reason)
        self._wake()

    async def _attach(
        self,
        workspace: str,
        session_id: str,
        reason: str,
        browser: str,
        grid_timeout: int | None,
        resume: bool = False,
        *,
        expired: Callable[[], bool] = lambda: False,
    ) -> None:
        """`attach` on the loop: owe the reason, then hold the connection now
        rather than at the next look. On any failure the reason is released.

        A failure backs the watch off, and so does a cancellation ``expired``
        explains: the caller's ``ATTACH_TIMEOUT`` ran out on a route that
        hangs. Any other cancellation — a person's Control released while its
        attach was under way, the monitor stopping — is no failure: it is not
        logged and holds no later call back."""
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
                    if resume and w.connection is None and self.backing_off(session_id):
                        return  # a hold failed while this waited: it answers this too
                    if w.connection is None:
                        await self._hold(w)
                    else:
                        await self._extend(w, w.connection)
            except BaseException as exc:
                # Backed off like a look's hold, so a call that resumes it
                # does not pay ATTACH_TIMEOUT again on every call.
                if not isinstance(exc, asyncio.CancelledError) or expired():
                    self._failed(w, self.clock(), exc)
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
        if not self._wanted(w) or (now >= w.deadline and not self._in_call(w)):
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
            if self.watches.get(w.session_id) is w:  # an ended watch owes nothing
                # From when it failed, not the look's ``now``: that was read
                # before the Grid's status, and a slow one spent the back-off
                # before it began.
                self._failed(w, self.clock(), exc)

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
            w.top = top
            for reason in self._told(w):
                w.heard.add(reason)  # first: a sink that raises is still detached
                self.sinks[reason].attached(
                    w.workspace, w.session_id, w.browser, top, again=again
                )
            conn.listen(
                wanted,
                self._deliverer(w),
                name=self._listener_name(w),
                on_drop=self._loser(w, wanted),
            )
            await conn.subscribe(wanted)
            for reason in sorted(w.owed):
                params = self.collectors_for.get(reason)
                if params is not None:
                    await conn.add_collector(params)
            if again:
                await conn.flush()
                for reason in sorted(w.heard):
                    self.sinks[reason].replayed(w.workspace, w.session_id)
            await self._subscribed(w, conn, sorted(w.heard), flushed=again)
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
        """Subscribe the events a reason owed since the hold added, add its
        collector and tell its sink `attached`: the hold's top, not again."""
        added = [e for e in self._wanted(w) if e not in w.subscribed]
        joining = [reason for reason in self._told(w) if reason not in w.heard]
        if not added and not joining:
            return
        try:
            for reason in joining:
                w.heard.add(reason)
                self.sinks[reason].attached(
                    w.workspace, w.session_id, w.browser, w.top, again=False
                )
            if added:
                listener = conn.listen(
                    added,
                    self._deliverer(w),
                    name=self._listener_name(w),
                    on_drop=self._loser(w, added),
                )
                try:
                    await conn.subscribe(added)
                except BaseException:
                    listener.cancel()
                    raise
                w.subscribed = w.subscribed | frozenset(added)
            for reason in joining:
                params = self.collectors_for.get(reason)
                if params is not None:
                    await conn.add_collector(params)
            await self._subscribed(w, conn, joining, flushed=not added)
        except BaseException:
            for reason in joining:
                if reason in w.heard:
                    w.heard.discard(reason)
                    self._tell_detached(w, reason)
            raise
        self._succeeded(w)

    async def _subscribed(
        self, w: Watch, conn: Connection, reasons: list[str], *, flushed: bool
    ) -> None:
        """Tell each of ``reasons`` whose sink asks (an optional
        ``subscribed``: `live.pages.Pages`) that what the subscribe re-sent is
        behind it. Subscribing ``browsingContext.contextCreated`` re-sends it
        for every context already open, on both browsers (capture spec,
        measured), on a first hold as on a hold again; flushed first, unless
        the hold already was or nothing new was subscribed. Capture does not
        ask, and costs nothing here."""
        told = [
            r for r in reasons if callable(getattr(self.sinks[r], "subscribed", None))
        ]
        if not told:
            return
        if not flushed:
            await conn.flush()
        for reason in told:
            self.sinks[reason].subscribed(w.workspace, w.session_id)

    @staticmethod
    def _listener_name(w: Watch) -> str:
        return "+".join(sorted(w.heard)) or "monitor"

    def _deliverer(self, w: Watch) -> Callable[[str, dict], None]:
        return functools.partial(self._deliver, w)

    def _deliver(self, w: Watch, method: str, params: dict) -> None:
        """Hand an event to every sink heard on the connection that asked for
        it; one sink failing does not cost the others it."""
        failed: Exception | None = None
        for reason in sorted(w.heard):
            if method not in self.events_for.get(reason, ()):
                continue
            try:
                self.sinks[reason].ingest(w.workspace, w.session_id, method, params)
            except Exception as exc:  # noqa: BLE001 - raised after the others
                failed = failed or exc
        if failed is not None:
            raise failed  # the subscriber logs it by type

    def _loser(self, w: Watch, events: list[str]) -> Callable[[], None]:
        """Tells each sink that asked for one of this listener's events that
        its queue dropped one, so a reader sees the count rather than only a
        log line."""
        events = frozenset(events)

        def lost() -> None:
            for reason in sorted(w.heard):
                if events & frozenset(self.events_for.get(reason, ())):
                    self.sinks[reason].lost(w.workspace, w.session_id)

        return lost

    def _notice_drop(self, w: Watch) -> None:
        """A connection closed under us: let it go, and look at the session now."""
        conn = w.connection
        if conn is not None and conn.dropped:
            w.connection, w.subscribed, w.dropped = None, frozenset(), True
            self._detached(w)
            self._close_later(conn)  # stops its listeners

    def _succeeded(self, w: Watch) -> None:
        w.failures, w.retry_at = 0, 0.0
        self._failed_logged.discard(w.session_id)

    def _in_call(self, w: Watch) -> bool:
        with self._in_flight_lock:
            return self._in_flight.get(w.session_id, 0) > 0

    def _failed(self, w: Watch, now: float, exc: BaseException) -> None:
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
        """The hold ended, or failed: every sink heard on it, or owed on it,
        is told, and none is heard any more."""
        told = sorted(w.heard | set(self._told(w)))
        w.heard = set()
        for reason in told:
            self._tell_detached(w, reason)

    def _tell_detached(self, w: Watch, reason: str) -> None:
        sink = self.sinks.get(reason)
        if sink is None:
            return
        try:
            sink.detached(w.workspace, w.session_id)
        except Exception as exc:  # noqa: BLE001 - a hold ends whatever the sink does
            log.warning(
                "monitor: the %s sink failed (%s)", reason, type(exc).__name__
            )

    def _close_later(self, conn: Connection) -> asyncio.Task:
        """Close ``conn`` in a task `stop` waits for. On the loop, which is
        the running one even when `look` is called before `start`."""
        closing = asyncio.get_running_loop().create_task(conn.close())
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

    def _post(self, fn) -> bool:
        """Apply ``fn`` on the loop; False when it is closed and ``fn`` is lost."""
        loop = self._loop
        if loop is None:
            fn()  # not started: applied here; `start` takes it from there
            return True
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            fn()
            return True
        try:
            loop.call_soon_threadsafe(fn)
        except RuntimeError:
            return False
        return True

    def _touched(self, session_id: str, at: float) -> None:
        w = self.watches.get(session_id)
        if w is not None and at > w.touched:
            w.touched = at
            self._wake()

    def _uncount(self, session_id: str) -> None:
        with self._in_flight_lock:
            left = self._in_flight.get(session_id, 0) - 1
            if left > 0:
                self._in_flight[session_id] = left
            else:
                self._in_flight.pop(session_id, None)

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
        waits = [  # a call in flight holds past it; its end wakes the loop
            w.deadline - now
            for w in self.watches.values()
            if w.connection and not self._in_call(w)
        ]
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
