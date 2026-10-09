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
taken after its watch began, do not list it — or one, after its socket
dropped. A listing that failed says nothing, and so does one that shows no
nodes (a hub restarting before its nodes register again) — until listings with
no nodes have run unbroken for the watch's idle timeout plus a tick, from the
run's first or the watch's start, whichever is later: then each is a miss.
Every WebDriver command and BiDi frame reaches a node through the hub, so a
node unlisted that long received no activity and has reaped the session, or is
gone itself (KEDA at zero, an evicted node, a Grid redeployed).

**A socket is a channel** (`bidi.py`): held only for a reason that names BiDi
events (``events_for``), until the watch's deadline — its last activity plus
its node's ``sessionTimeout`` — and then closed, so a busy page's events cannot
keep the browser alive past the Grid's own timer (programme R3). The Grid
reaps; this server ends no session on a timer. A socket outlives its session
(measured), so the monitor closes it when the watch ends.

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

import anyio

from .bidi import BidiSocket
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
    socket: BidiSocket | None = field(default=None, repr=False)
    dropped: bool = False

    @property
    def deadline(self) -> float:
        return self.touched + (self.timeout or DEFAULT_TIMEOUT)


class Monitor:
    """The watch registry, the listing and the held sockets.

    ``listing()`` returns ``(nodes, {session_id: timeout_seconds | None})``
    from one ``GET /status`` (`Grid.listing`). ``socket_url(session_id)`` is
    the browser's BiDi address (`Grid.bidi_url`). ``events_for`` maps a reason
    to the BiDi events it needs: a watch holds a socket only while it owes such
    a reason. ``connect`` builds a socket; a test seam. ``on_bidi(session_id,
    method, params)`` hears what a socket delivers.
    """

    def __init__(
        self,
        bus: Bus,
        *,
        listing: Callable[[], tuple[int, Mapping[str, int | None]]],
        socket_url: Callable[[str], str] | None = None,
        events_for: Mapping[str, tuple[str, ...]] | None = None,
        connect: Callable[..., BidiSocket] | None = None,
        on_bidi: Callable[[str, str, dict], None] | None = None,
        clock: Callable[[], float] = time.time,
        tick: float = LISTING_TICK,
    ):
        self.bus = bus
        self.listing = listing
        self.socket_url = socket_url
        self.events_for = dict(events_for or {})
        self.connect = connect or BidiSocket
        self.on_bidi = on_bidi
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
        self._listing_failed = False
        self._empty_since: float | None = None  # the current run of no-node listings
        self._socket_failed: set[str] = set()

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
            with self._ended_lock:
                if session_id in self._ended:
                    return  # nothing left to owe
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
            w.owed.add(reason)
            if grid_timeout:
                w.timeout = grid_timeout
            self._ensure()

        self._post(apply)

    def release(self, session_id: str, reason: str) -> None:
        """Owe ``reason`` no more; a watch owing nothing is dropped."""

        def apply() -> None:
            w = self.watches.get(session_id)
            if w is None:
                return
            w.owed.discard(reason)
            if not w.owed:
                self._drop(session_id)
            else:
                self._wake()

        self._post(apply)

    def touch(self, session_id: str, at: float | None = None) -> None:
        """A call reached this session: its deadline moves on."""
        at = self.clock() if at is None else at

        def apply() -> None:
            w = self.watches.get(session_id)
            if w is not None and at > w.touched:
                w.touched = at
                self._wake()

        self._post(apply)

    # ---- on the loop -------------------------------------------------------

    def watching(self, session_id: str) -> frozenset[str]:
        w = self.watches.get(session_id)
        return frozenset(w.owed) if w else frozenset()

    def socket_held(self, session_id: str) -> bool:
        w = self.watches.get(session_id)
        return bool(w and w.socket and w.socket.is_open)

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
        for w in list(self.watches.values()):  # a close can end a watch
            if w.socket is not None:
                sock, w.socket = w.socket, None
                await sock.close()
        for closing in list(self._closing):
            with contextlib.suppress(Exception):
                await closing
        self._loop = None

    async def look(self) -> None:
        """One pass: the listing, what it says is gone, and the sockets."""
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
            if w.socket is not None and w.socket.dropped:
                w.socket, w.dropped = None, True
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
            await self._socket(w, now)

    # ---- inside -------------------------------------------------------------

    def _unlisted(self, w: Watch, now: float) -> bool:
        """No node listed for the whole of this watch's idle timeout and a tick."""
        start = max(self._empty_since or now, w.since)
        return now - start >= (w.timeout or DEFAULT_TIMEOUT) + self.tick

    def _wanted(self, w: Watch) -> list[str]:
        return sorted({e for reason in w.owed for e in self.events_for.get(reason, ())})

    async def _socket(self, w: Watch, now: float) -> None:
        """Hold a socket while events are owed and the deadline has not passed."""
        wanted = self._wanted(w)
        sock = w.socket
        if not wanted or now >= w.deadline:
            if sock is not None:
                w.socket = None
                await sock.close()
            return
        if sock is None and self.socket_url is not None:
            try:
                sock = self.connect(
                    self.socket_url(w.session_id),
                    on_event=functools.partial(self._on_event, w.session_id),
                    on_close=self._wake,
                )
                await sock.open()
                await sock.subscribe(wanted)
            except Exception as exc:  # noqa: BLE001 - tried again next look
                if sock is not None:
                    await sock.close()
                if w.workspace not in self._socket_failed:
                    self._socket_failed.add(w.workspace)
                    log.info(
                        "monitor: no BiDi socket for %s (%s); trying again",
                        w.workspace,
                        type(exc).__name__,
                    )
                return
            self._socket_failed.discard(w.workspace)
            w.socket = sock

    def _on_event(self, session_id: str, method: str, params: dict) -> None:
        if self.on_bidi is not None:
            self.on_bidi(session_id, method, params)

    def _drop(self, session_id: str) -> None:
        """Forget the watch; its socket is closed in a task `stop` waits for."""
        w = self.watches.pop(session_id, None)
        if w is None or w.socket is None:
            return
        sock, w.socket = w.socket, None
        if self._loop is None:
            return  # not started: nothing was ever opened
        closing = self._loop.create_task(sock.close())
        self._closing.add(closing)
        closing.add_done_callback(self._closing.discard)

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
        deadlines = [w.deadline - now for w in self.watches.values() if w.socket]
        return max(0.05, min([self.tick, *deadlines]))

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
