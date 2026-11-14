"""`Capture`: the monitor's `Sink`, and what the readers read.

Per workspace, one `Buffer` in a `cachetools.TTLCache`: the console and the
requests, oldest evicted, the last navigations marked, counters per
navigation, and the index from our request ids to the browser's. Every event
that lands re-assigns the workspace's entry, which is what slides its TTL; a
read never does.

What belongs to the browser rather than to what it logged — its Grid id, its
top-level context, the secrets bound in it, and what Chrome's replay is aligned
against — is a `Held` session, kept beside the buffer: it outlives an expired
buffer, so a page that logs again after hours of quiet is captured again, with
its secrets still masked. A new session in a workspace (``attached`` with
``again=False``) is a new `Held`, holding the secrets the one it replaces
held — a value typed before a reap can be echoed after it; the buffer goes on.

Threading: ``attached``, ``ingest``, ``replayed``, ``detached`` and ``tail``
run on the event loop; the reads run on worker threads. One `threading.Lock`
covers everything, and nothing slow happens under it.

**The secrets in scope** (spec 2026-11-14-secret-scope, rulings 8, 9 and 22).
With ``heard`` set (`Workspaces.capture_heard`, only while secrets are
configured), capture is the origin's second source: a context's navigation —
``navigationStarted``, ``navigationCommitted``, or a navigation request, a
redirect included, in whichever order they come — gives it a new origin, and
an origin whose forms are not yet in the masker is asked for, once. Until
every origin a context's events need has landed, its events wait raw in a
`Pending`, and nothing is built from them, masked, stored or tailed. A context
nothing navigated is ``""`` after a first attach, and ``"*"`` (every leashed
secret) after a hold again or a lost event, since a navigation may have gone
unheard. ``""`` is known-empty only for the top page (ruling 13); any other
context's ``""`` inherits, as the browser's does, so it waits with its parent
(a popup's opener; every leashed secret for a tab with neither). The ask
carries a URL on the origin, and is made after the lock is
released, because a build already done calls back at once; its ``done`` runs
on a scrubber thread, so when ``ingest`` ran on a loop the landing is handed to
that loop with ``call_soon_threadsafe`` (the tails are asyncio queues), and
with none it lands at once. `_landed` replays the raw events through the same
handlers, so Chrome's replay hold and ``_started``'s marks behave as if they
had just arrived. Bounds (ruling 22): `PENDING_EVENTS` per context and
`QUEUE_BYTES` (estimated) per session, the oldest going first, and an event
older than `PENDING_SECONDS` dropped when the next arrives — checked on
arrival, since nothing here runs a timer; a failed read drops what waited, and
``detached`` a replay still waiting, as it drops one held; all of it is
counted in ``dropped``, none kept raw.
With ``heard`` None nothing is held or asked.

Bounded memory is the rule throughout, because a page decides how much it
logs, fetches and frames: two buffers of 1 000, requests in flight capped at
1 000, the index dropped with its row, the contexts remembered for a replay
capped at `CONTEXTS`, what a replay holds capped at what was remembered, every
string capped, and the sessions remembered capped at `WORKSPACES` — past it
the oldest not held go, never one still held (whose events would then go
unheard): those are bounded by the monitor's holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal, NamedTuple
from urllib.parse import urlsplit

from cachetools import TTLCache

from ..bidi.connection import QUEUE_BYTES
from ..redaction import Forms
from ..urls import scope_origin
from . import (
    CONSOLE_LIMIT,
    DEFAULT_TTL,
    NAVIGATIONS,
    PENDING_EVENTS,
    PENDING_SECONDS,
    REPLAY_DEPTH,
    REQUEST_LIMIT,
    STATIC_TYPES,
    TAIL_QUEUE,
    WORKSPACES,
    views,
)
from .align import overlap
from .masking import URL_HEADERS, Masker, strip_query
from .merge import (
    console_entry,
    counts_in_hint,
    entry_key,
    is_static_success,
    number,
    resource_type,
    seconds,
    took_ms,
)

log = logging.getLogger(__name__)

# The browsing contexts a session remembers the last entries of, for aligning
# Chrome's replay: a page can make a frame per entry, and each remembered
# context costs up to REPLAY_DEPTH digests. The least recently heard goes first.
CONTEXTS = 64

KINDS = ("console", "network")

# The origin of a context whose origin is not known: every leashed secret.
EVERY = "*"

# What `Capture.heard` is: (workspace, session_id, url | None, frame, done).
Heard = Callable[[str, str, "str | None", bool, Callable[["Forms | None"], None]], None]

_NAVIGATED = frozenset(
    {"browsingContext.navigationStarted", "browsingContext.navigationCommitted"}
)


class Mark(NamedTuple):
    """The running totals at a call's start; `Capture.caused` subtracts."""

    errors: int
    failed: int


class Located(NamedTuple):
    """Where a kept request's body lives: which browser, and its own id there."""

    session_id: str
    request: str
    mime: str
    size: int | None


@dataclass(eq=False)
class Pending:
    """A context's events, raw, until the secrets of every origin they need
    are in the masker: `Waiting` entries, oldest first, at most
    `PENDING_EVENTS`."""

    # origin -> a URL on it to ask with (None for EVERY).
    needs: dict[str, str | None]
    events: deque = field(default_factory=deque)
    # How many of the oldest arrived before the last hold again: they land
    # past Chrome's replay hold, which aligns only what came after.
    before: int = 0


class Waiting(NamedTuple):
    """One held event, raw: what it was, when it arrived, roughly how large it
    is (`_weight`), and its place among every event the session holds."""

    method: str
    params: dict
    arrived: float
    weight: int
    order: int


@dataclass(eq=False)
class Held:
    """A session as capture knows it: one live browser in one workspace."""

    workspace: str
    session_id: str
    browser: str
    top: str | None
    masker: Masker = field(default_factory=Masker)
    # context -> digests of the last entries landed from it, for the replay.
    recent: OrderedDict[str, deque] = field(default_factory=OrderedDict)
    # While Chrome's replay is awaited: context -> [(digest, entry)] held back.
    holding: dict[str, list] | None = None
    live: bool = True  # a connection is held to it: never evicted
    # The secrets in scope (only with `Capture.heard`): context -> (its
    # origin, a URL on it), the least recently heard going first past
    # CONTEXTS; context -> what it inherits "" from (its parent, a popup's
    # opener, or EVERY for a tab with neither), bounded alike; the origins
    # whose forms are in the masker ("" always is, unstored), bounded alike;
    # the contexts whose events wait, and the estimated size of all they hold
    # (bounded by QUEUE_BYTES); the origins asked for and not yet landed.
    origins: OrderedDict = field(default_factory=OrderedDict)
    parents: OrderedDict = field(default_factory=OrderedDict)
    ready: OrderedDict = field(default_factory=OrderedDict)
    waiting: dict = field(default_factory=dict)
    weight: int = 0
    order: int = 0
    asked: set[str] = field(default_factory=set)
    # A context's origin when none was heard: "" after a first attach, EVERY
    # once a navigation may have gone unheard.
    unknown: str = ""
    # `replayed` came while some held contexts were still waiting: each is
    # aligned as soon as its events land.
    replay_over: bool = False


@dataclass(eq=False)
class _Flight:
    """A request between its start and its end."""

    row: dict
    request: str  # the browser's id
    begin: float | None  # the start's timestamp, epoch ms
    kind: dict  # destination and initiator, for re-deriving the type
    navigation: bool


@dataclass(eq=False)
class Buffer:
    """One workspace's capture."""

    console: deque = field(default_factory=lambda: deque(maxlen=CONSOLE_LIMIT))
    requests: deque = field(default_factory=lambda: deque(maxlen=REQUEST_LIMIT))
    marks: deque = field(default_factory=lambda: deque(maxlen=NAVIGATIONS))
    nav: int = 0
    seq: int = 0
    counter: int = 0  # requests started in this navigation
    # Per navigation, pruned to the marked ones.
    seen: dict[int, int] = field(default_factory=dict)
    static: dict[int, int] = field(default_factory=dict)
    debug: dict[int, int] = field(default_factory=dict)
    errors: dict[int, int] = field(default_factory=dict)
    failed: dict[int, int] = field(default_factory=dict)
    errors_total: int = 0
    failed_total: int = 0
    # Events the capture listener's queue dropped, since the buffer began: a
    # dropped navigation cannot be placed on a page.
    dropped: int = 0
    # Credentials too long to learn, so shown wherever a page echoes them:
    # each counted once (`Masker.skipped`), since the buffer began.
    unmasked: int = 0
    in_flight: OrderedDict[tuple[str, int], _Flight] = field(
        default_factory=OrderedDict
    )
    index: dict[str, Located] = field(default_factory=dict)

    def counters(self) -> tuple[dict[int, int], ...]:
        return (self.seen, self.static, self.debug, self.errors, self.failed)


class TailSubscription:
    """One listening page's live rows: masked, normalised, never stored.

    A bounded queue; what overflows is counted, and `next` says so before the
    next row whenever the count has moved.
    """

    def __init__(
        self, capture: Capture, workspace: str, kind: Literal["console", "network"]
    ):
        self.kind = kind
        self.workspace = workspace
        self.dropped = 0
        self._said = 0
        self._capture = capture
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=TAIL_QUEUE)

    def offer(self, row: dict) -> None:
        try:
            self._queue.put_nowait(row)
        except asyncio.QueueFull:
            self.dropped += 1

    async def next(self) -> dict:
        """A row, or ``{"dropped": total}`` when that moved since it last said."""
        if self.dropped != self._said:
            self._said = self.dropped
            return {"dropped": self.dropped}
        return await self._queue.get()

    def close(self) -> None:
        self._capture._untail(self)


class Capture:
    """Implements `monitor.Sink`; read by the resources, the admin and the hint."""

    def __init__(
        self, *, ttl: int = DEFAULT_TTL, clock: Callable[[], float] = time.time
    ):
        self._lock = threading.Lock()
        self._clock = clock
        self._cache: TTLCache = TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock)
        self._sessions: OrderedDict[str, Held] = OrderedDict()
        # workspace -> the idle session evicted from `_sessions`, parked so a
        # hold again is heard and its secrets are kept; bounded like them.
        self._parked: OrderedDict[str, Held] = OrderedDict()
        self._hidden: dict[str, set[str]] = {}
        self._tails: dict[str, list[TailSubscription]] = {}
        # Asks for the secrets in scope on an origin, set by `server.py` while
        # secrets are configured (`Workspaces.capture_heard`); None: no gate.
        self.heard: Heard | None = None

    @property
    def ttl(self) -> int:
        """Seconds entries are kept after the last one arrived."""
        return int(self._cache.ttl)

    # ---- the Sink ------------------------------------------------------------

    def attached(self, workspace, session_id, browser, top, *, again: bool) -> None:
        """A hold started. A new session (or the first) gets the secrets of the
        workspace's session it replaces and nothing to align; a hold again
        keeps all of it, and for Chrome holds
        back what it replays until `replayed` (plan ruling 18). A hold again of
        a session capture no longer knows — discarded, or replaced by a newer
        browser in its workspace — is not heard: it must not take the
        workspace back from the browser that replaced it. One evicted while
        it was paused is: it is held again whole, secrets and all."""
        if again:
            heard, asks = self.heard, []
            with self._lock:
                self._again(workspace, session_id, browser, top, heard, asks)
            self._ask(heard, asks)
            return
        with self._lock:
            masker = Masker()
            for sid in [
                s for s, h in self._sessions.items() if h.workspace == workspace
            ]:
                masker.carry(self._sessions.pop(sid).masker)
            parked = self._parked.pop(workspace, None)
            if parked is not None:
                masker.carry(parked.masker)
            self._sessions[session_id] = Held(
                workspace, session_id, browser, top, masker=masker
            )
            self._evict()
            buffer = self._cache.get(workspace)
            if buffer is None:
                buffer = Buffer()
            else:
                # The old browser's requests will never end, and its ids are
                # the new one's to reuse.
                buffer.in_flight.clear()
            self._cache[workspace] = buffer

    def _again(self, workspace, session_id, browser, top, heard, asks) -> None:
        """`attached` with ``again``, under the lock; what to ask for goes into
        ``asks``, asked once the lock is released."""
        held = self._sessions.get(session_id)
        if held is None:
            held = self._unpark(workspace, session_id)
        if held is None or held.workspace != workspace:
            log.info("capture: a hold again in %s is not heard", workspace)
            return
        self._sessions.move_to_end(session_id)
        held.live = True
        if held.top is None:
            held.top = top
        if heard is not None:
            self._unheard(held, asks, again=True)
        held.replay_over = False
        # What waited from before the hold again was sent before it, so it is
        # in what Chrome replays too: those contexts are held for alignment.
        held.holding = (
            {ctx: [] for ctx, seen in held.recent.items() if seen}
            | {ctx: [] for ctx, p in held.waiting.items() if p.before}
            if browser == "chrome"
            else None
        )

    def ingest(self, workspace, session_id, method, params) -> None:
        heard, asks = self.heard, []
        with self._lock:
            held = self._sessions.get(session_id)
            if (
                held is None
                or held.workspace != workspace
                or not isinstance(params, dict)
            ):
                return
            context = _context(method, params)
            if context is not None and context in self._hidden.get(session_id, ()):
                return  # the spare tab (ruling 15)
            if method == "browsingContext.contextCreated":
                if params.get("parent") is None and held.top is None:
                    held.top = context
                elif heard is not None and context != held.top:
                    _inherits(held, context, params)
                return
            handler = _HANDLERS.get(method)
            if handler is None:
                return
            if heard is None or not self._gate(held, method, params, context, asks):
                buffer = self._cache.get(workspace)
                if buffer is None:  # expired, or evicted: it starts again
                    buffer = Buffer()
                if handler(self, buffer, held, params, context):
                    self._cache[workspace] = buffer
        self._ask(heard, asks)

    def replayed(self, workspace, session_id) -> None:
        """Chrome's replay is over: what is still held lands, past its overlap."""
        with self._lock:
            held = self._sessions.get(session_id)
            if held is None or held.workspace != workspace or held.holding is None:
                return
            buffer = self._cache.get(workspace) or Buffer()
            landed = False
            for ctx in list(held.holding):
                if ctx not in held.waiting:  # a waiting one is aligned as it lands
                    landed = self._resolve(buffer, held, ctx) or landed
            held.replay_over = True
            if not held.holding:
                held.holding = None
            if landed:
                self._cache[workspace] = buffer

    def lost(self, workspace, session_id) -> None:
        """An event the browser sent was dropped before it landed: counted."""
        with self._lock:
            held = self._sessions.get(session_id)
            if held is None or held.workspace != workspace:
                return
            if self.heard is not None:  # it may have been a navigation
                self._unheard(held, [], again=False)
            buffer = self._cache.get(workspace) or Buffer()
            buffer.dropped += 1
            self._cache[workspace] = buffer

    def detached(self, workspace, session_id) -> None:
        """A hold ended, or failed before it began: anything held is dropped,
        since nothing is left to align it against."""
        with self._lock:
            held = self._sessions.get(session_id)
            if held is not None and held.workspace == workspace:
                # A replay still waiting for its read is held too: dropped and
                # counted, all but what waited from before the hold again.
                for ctx in held.holding or ():
                    pending = held.waiting.get(ctx)
                    if pending is not None:
                        self._cut(held, pending, pending.before)
                held.holding = None
                held.replay_over = False
                held.live = False
                self._evict()

    # ---- the workspace -------------------------------------------------------

    def discard(self, workspace: str) -> None:
        """Forget the workspace's capture and its session (programme R9)."""
        with self._lock:
            self._cache.pop(workspace, None)
            self._parked.pop(workspace, None)
            for sid in [
                s for s, h in self._sessions.items() if h.workspace == workspace
            ]:
                del self._sessions[sid]

    def secret(self, workspace: str, forms: Forms | Iterable[str]) -> None:
        """Mask ``forms`` in everything this workspace's session captures from
        now on, and in every session that replaces it."""
        if not isinstance(forms, Forms):
            forms = list(forms)
        with self._lock:
            for held in self._sessions.values():
                if held.workspace == workspace:
                    held.masker.add(forms)
            parked = self._parked.get(workspace)
            if parked is not None:
                parked.masker.add(forms)

    @contextlib.contextmanager
    def hidden(self, session_id: str, context: str) -> Iterator[None]:
        """Ignore ``context`` for the block: the spare tab's whole life."""
        with self._lock:
            self._hidden.setdefault(session_id, set()).add(context)
        try:
            yield
        finally:
            with self._lock:
                contexts = self._hidden.get(session_id)
                if contexts is not None:
                    contexts.discard(context)
                    if not contexts:
                        del self._hidden[session_id]

    # ---- the readers ---------------------------------------------------------

    def mark(self, workspace: str) -> Mark | None:
        with self._lock:
            buffer = self._cache.get(workspace)
            return (
                None
                if buffer is None
                else Mark(buffer.errors_total, buffer.failed_total)
            )

    def caused(self, workspace: str, since: Mark) -> dict:
        """What landed since ``since``: page errors and hinted failures."""
        with self._lock:
            buffer = self._cache.get(workspace)
            if buffer is None:
                return {"errors": 0, "failed_requests": 0}
            # A buffer that expired and began again starts its totals at zero.
            return {
                "errors": max(0, buffer.errors_total - since.errors),
                "failed_requests": max(0, buffer.failed_total - since.failed),
            }

    def console(
        self, workspace: str, *, level: str = "all", pages: int = 1
    ) -> dict | None:
        level, pages = views.check_level(level), views.check_pages(pages)
        with self._lock:
            buffer = self._cache.get(workspace)
            return None if buffer is None else views.console(buffer, level, pages)

    def network(
        self, workspace: str, *, filter: str = "all", pages: int = 1
    ) -> dict | None:
        filter, pages = views.check_filter(filter), views.check_pages(pages)
        with self._lock:
            buffer = self._cache.get(workspace)
            return None if buffer is None else views.network(buffer, filter, pages)

    def request(self, workspace: str, id: str) -> dict | None:
        with self._lock:
            buffer = self._cache.get(workspace)
            return None if buffer is None else views.request(buffer, id)

    def known(self, workspace: str, id: str) -> bool:
        """Whether request ``id`` is kept in ``workspace``'s capture, whatever
        browser made it."""
        with self._lock:
            buffer = self._cache.get(workspace)
            return buffer is not None and id in buffer.index

    def counts(self, workspace: str) -> dict | None:
        with self._lock:
            buffer = self._cache.get(workspace)
            return None if buffer is None else views.counts(buffer)

    def request_for(self, session_id: str, id: str) -> Located | None:
        """Where request ``id`` of the workspace ``session_id`` is open in
        lives. Its ``session_id`` is the browser that made it, which a new
        browser in the same workspace is not."""
        with self._lock:
            held = self._sessions.get(session_id)
            buffer = None if held is None else self._cache.get(held.workspace)
            return None if buffer is None else buffer.index.get(id)

    def mask(self, session_id: str, text: str, cap: int) -> str | None:
        """``text`` with the secrets bound in ``session_id``'s browser masked,
        cut to ``cap`` characters (`Masker.text`); None when capture no longer
        knows that browser. The masking runs outside the lock: it reads at
        most ``cap`` plus the longest secret."""
        with self._lock:
            held = self._sessions.get(session_id)
        return None if held is None else held.masker.text(text, cap)

    def tail(self, workspace: str, kind: str) -> TailSubscription:
        """Listen to ``kind``'s rows as they land. On the loop only."""
        if kind not in KINDS:
            raise ValueError("kind is console or network")
        subscription = TailSubscription(self, workspace, kind)  # type: ignore[arg-type]
        with self._lock:
            self._tails.setdefault(workspace, []).append(subscription)
        return subscription

    def _untail(self, subscription: TailSubscription) -> None:
        with self._lock:
            subs = self._tails.get(subscription.workspace)
            if subs is not None and subscription in subs:
                subs.remove(subscription)
                if not subs:
                    del self._tails[subscription.workspace]

    # ---- the secrets in scope -------------------------------------------------

    def _gate(self, held: Held, method: str, params: dict, context, asks) -> bool:
        """Under the lock: hold the event back, raw, when its context waits or
        its origin's forms are not in the masker yet, and note what to ask for
        in ``asks``. False: it lands now."""
        navigated = _navigated(method, params)
        if navigated is not None:
            held.origins[context] = navigated
            held.origins.move_to_end(context)
            while len(held.origins) > CONTEXTS:
                _, (gone, _url) = held.origins.popitem(last=False)
                if not _is_ready(held, gone):  # forgotten unread: none is known
                    held.unknown = EVERY
        elif context in held.origins:
            held.origins.move_to_end(context)
        pending = held.waiting.get(context)
        if pending is None and method == "browsingContext.contextDestroyed":
            return False  # it builds nothing, and what it lands is masked already
        needs = self._needs(held, context)
        if pending is None:
            if not needs:
                return False
            pending = self._wait(held, context)
        for need, url in needs.items():
            pending.needs.setdefault(need, url)
        now = self._clock()
        while pending.events and now - pending.events[0].arrived > PENDING_SECONDS:
            self._shed(held, pending)
        if len(pending.events) >= PENDING_EVENTS:
            self._shed(held, pending)
        held.order += 1
        event = Waiting(method, params, now, _weight(params), held.order)
        pending.events.append(event)
        held.weight += event.weight
        while held.weight > QUEUE_BYTES:  # the oldest the session holds goes
            oldest = min(
                (p for p in held.waiting.values() if p.events),
                key=lambda p: p.events[0].order,
            )
            self._shed(held, oldest)
        # True for a None context, and for every context while the top is
        # unknown: harmless, since a frame's arrival never moves the top origin
        # `Origins` holds - only a call's result (source 1) does then.
        frame = context != held.top
        for need, url in pending.needs.items():
            if need not in held.asked:
                held.asked.add(need)
                asks.append((held.workspace, held.session_id, need, url, frame))
        return True

    def _needs(self, held: Held, context) -> dict[str, str | None]:
        """What ``context``'s events wait on: its origin while that is not
        ready. A top page's "" is known-empty (ruling 13); any other context's
        "" inherits (an `about:blank` or `about:srcdoc` frame, a child that
        never navigated, a popup), so it waits on what its parent - a popup's
        opener, the top for a context never heard created - waits on and is,
        and on every leashed secret for a tab with neither (controller ruling,
        review round 1). Empty: it lands now."""
        needs: dict[str, str | None] = {}
        seen: set = set()
        while True:
            origin, url = held.origins.get(context, (held.unknown, None))
            if origin != "":
                if not _is_ready(held, origin):
                    needs.setdefault(origin, url)
                return needs
            if context == held.top or context in seen:
                return needs
            seen.add(context)
            parent = held.parents.get(context, held.top)
            if parent == EVERY:
                if not _is_ready(held, EVERY):
                    needs.setdefault(EVERY, None)
                return needs
            if parent is None:  # no top known: nothing to inherit from
                return needs
            waits = held.waiting.get(parent)
            if waits is not None:
                for need, need_url in waits.needs.items():
                    if not _is_ready(held, need):
                        needs.setdefault(need, need_url)
            context = parent

    def _wait(self, held: Held, context) -> Pending:
        """A new `Pending` for ``context``; past CONTEXTS waiting, the oldest
        lets go of its events, counted (it waits again at its next event)."""
        while len(held.waiting) >= CONTEXTS:
            oldest = next(iter(held.waiting))
            self._cut(held, held.waiting.pop(oldest))
            if held.replay_over and held.holding is not None:
                buffer = self._cache.get(held.workspace) or Buffer()
                if self._aligned(held, oldest, buffer):
                    self._cache[held.workspace] = buffer
        pending = held.waiting[context] = Pending({})
        return pending

    def _shed(self, held: Held, pending: Pending) -> None:
        """``pending``'s oldest event goes, counted (ruling 22)."""
        event = pending.events.popleft()
        held.weight -= event.weight
        pending.before = max(0, pending.before - 1)
        self._dropped(held, 1)

    def _cut(self, held: Held, pending: Pending, keep: int = 0) -> None:
        """``pending``'s events past the first ``keep`` go, counted."""
        dropped = 0
        while len(pending.events) > keep:
            held.weight -= pending.events.pop().weight
            dropped += 1
        pending.before = min(pending.before, keep)
        self._dropped(held, dropped)

    def _unheard(self, held: Held, asks: list, *, again: bool) -> None:
        """Under the lock: a navigation may have gone unheard, so no context's
        origin is known: each waits for every leashed secret until it is read.
        A hold again also reads it afresh and asks at once, since what waits
        from before it (``before``) needs it too."""
        held.origins.clear()
        held.unknown = EVERY
        if not again:
            return
        held.ready.pop(EVERY, None)
        for pending in held.waiting.values():
            pending.needs.setdefault(EVERY, None)
            pending.before = len(pending.events)
        if EVERY not in held.asked:
            held.asked.add(EVERY)
            asks.append((held.workspace, held.session_id, EVERY, None, False))

    def _ask(self, heard: Heard | None, asks: list) -> None:
        """Ask for each origin's forms, after the lock: a build already done
        calls ``done`` at once, and `_landed` takes the lock. The ask carries a
        URL on the origin, never the origin itself, which for an IPv6 literal
        is no URL (`https://::1:8443`): `Workspaces` reads the origin from it
        by the same rule (review, round 1)."""
        if heard is None or not asks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        for workspace, session_id, origin, url, frame in asks:
            done = self._deliver(session_id, origin, loop)
            try:
                heard(workspace, session_id, url, frame, done)
            except Exception as exc:  # noqa: BLE001 - a failed ask is a failed read
                log.warning("capture could not ask for a page's secrets: %s",
                            type(exc).__name__)
                done(None)

    def _deliver(self, session_id: str, origin: str, loop) -> Callable:
        """``done`` for an ask: `_landed` on ``loop`` when ingest ran on one
        (the tails are asyncio queues), else at once. It runs on a scrubber
        thread, so it only hands over and returns."""

        def done(forms: Forms | None) -> None:
            if loop is None:
                self._landed(session_id, origin, forms)
                return
            with contextlib.suppress(RuntimeError):  # the loop closed: nothing lands
                loop.call_soon_threadsafe(self._landed, session_id, origin, forms)

        return done

    def _landed(self, session_id: str, origin: str, forms: Forms | None) -> None:
        """``origin``'s forms are read (or None: they could not be). With
        forms, they go into the masker, and every context whose wait is over
        replays its raw events through the handlers, in order. Without, what
        waited on it is dropped and counted, and the next event asks again."""
        with self._lock:
            held = self._sessions.get(session_id)
            if held is None:
                held = next(
                    (h for h in self._parked.values() if h.session_id == session_id),
                    None,
                )
            if held is None:  # discarded, or replaced: its events are not heard
                return
            held.asked.discard(origin)
            if forms is None:
                for pending in held.waiting.values():
                    if origin in pending.needs:
                        self._cut(held, pending)
                return
            if forms:
                held.masker.add(forms)
            if origin != "":
                held.ready[origin] = None
                held.ready.move_to_end(origin)
                while len(held.ready) > CONTEXTS:  # asked again when next needed
                    held.ready.popitem(last=False)
            buffer = self._cache.get(held.workspace) or Buffer()
            landed = False
            for context, pending in list(held.waiting.items()):
                pending.needs = {
                    n: u for n, u in pending.needs.items() if not _is_ready(held, n)
                }
                if not pending.needs:
                    del held.waiting[context]
                    landed = self._replay(buffer, held, context, pending) or landed
            if landed:
                self._cache[held.workspace] = buffer

    def _replay(self, buffer: Buffer, held: Held, context, pending: Pending) -> bool:
        """``pending``'s events through the handlers, as if they had just
        arrived; those from before a hold again past its replay hold."""
        handled = False
        saved = None
        if pending.before and held.holding is not None:
            saved = held.holding.pop(context, None)
        for i, event in enumerate(pending.events):
            if i == pending.before and saved is not None and held.holding is not None:
                held.holding[context] = saved
                saved = None
            held.weight -= event.weight
            handler = _HANDLERS[event.method]
            handled = handler(self, buffer, held, event.params, context) or handled
        if saved is not None and held.holding is not None:
            held.holding[context] = saved
        if held.replay_over and held.holding is not None:
            handled = self._aligned(held, context, buffer) or handled
        return handled

    def _aligned(self, held: Held, context, buffer: Buffer) -> bool:
        """`replayed` already came: ``context``'s replay hold is decided now."""
        landed = self._resolve(buffer, held, context)
        if not held.holding:
            held.holding = None
        return landed

    def _dropped(self, held: Held, count: int) -> None:
        """``count`` events never landed: counted in ``dropped``, never kept."""
        if count:
            buffer = self._cache.get(held.workspace) or Buffer()
            buffer.dropped += count
            self._cache[held.workspace] = buffer

    # ---- landing, under the lock ---------------------------------------------

    def _evict(self) -> None:
        """The oldest sessions past `WORKSPACES` that no connection holds,
        parked: their events are not heard until a hold again unparks them.
        Past `WORKSPACES` parked, the oldest is forgotten."""
        over = len(self._sessions) - WORKSPACES
        if over > 0:
            idle = [s for s, h in self._sessions.items() if not h.live][:over]
            for sid in idle:
                held = self._sessions.pop(sid)
                self._parked.pop(held.workspace, None)
                self._parked[held.workspace] = held
            while len(self._parked) > WORKSPACES:
                self._parked.popitem(last=False)

    def _unpark(self, workspace: str, session_id: str) -> Held | None:
        """``session_id``'s parked `Held`, back among the sessions; None when
        it was not this workspace's parked one, or the workspace has another."""
        parked = self._parked.get(workspace)
        if parked is None or parked.session_id != session_id:
            return None
        if any(h.workspace == workspace for h in self._sessions.values()):
            return None
        del self._parked[workspace]
        self._sessions[session_id] = parked
        parked.live = True  # held again: another idle one is parked instead
        self._evict()
        return parked

    def _tail(self, workspace: str, kind: str, row: dict, kept: bool) -> None:
        subs = self._tails.get(workspace)
        if not subs:
            return  # nobody listens: nothing is buffered
        shown = {**views.without_headers(row), "kept": kept}
        for sub in subs:
            if sub.kind == kind:
                sub.offer(shown)

    def _log(self, buffer: Buffer, held: Held, params: dict, context) -> bool:
        digest = _digest(entry_key(params))
        entry = console_entry(params, held.masker)
        pending = None if held.holding is None else held.holding.get(context)
        if pending is None:
            self._land(buffer, held, context, digest, entry)
            return True
        pending.append((digest, entry))
        # The overlap reads at most as many as were seen, so once that many are
        # held it is decided: they land now, and nothing more is held for this
        # context (what is held never exceeds REPLAY_DEPTH).
        if len(pending) >= len(held.recent.get(context, ())):
            return self._resolve(buffer, held, context)
        return False

    def _resolve(self, buffer: Buffer, held: Held, context) -> bool:
        pending = held.holding.pop(context, None) if held.holding else None
        if not pending:
            return False
        seen = held.recent.get(context, ())
        skip = overlap(list(seen), [digest for digest, _ in pending])
        for digest, entry in pending[skip:]:
            self._land(buffer, held, context, digest, entry)
        return skip < len(pending)

    def _land(
        self, buffer: Buffer, held: Held, context, digest: bytes, entry: dict
    ) -> None:
        recent = held.recent.get(context)
        if recent is None:
            recent = held.recent[context] = deque(maxlen=REPLAY_DEPTH)
            while len(held.recent) > CONTEXTS:
                held.recent.popitem(last=False)
        else:
            held.recent.move_to_end(context)
        recent.append(digest)
        nav = buffer.nav
        buffer.seq += 1
        row = {**entry, "seq": buffer.seq, "nav": nav}
        if row["level"] == "debug":  # counted, not kept (ruling 4); tailed
            _bump(buffer.debug, nav)
            self._tail(held.workspace, "console", row, kept=False)
            return
        buffer.console.append(row)
        if row["level"] == "error" or row["type"] == "javascript":
            _bump(buffer.errors, nav)
            buffer.errors_total += 1
        self._tail(held.workspace, "console", row, kept=True)

    def _sent(self, buffer: Buffer, held: Held, params: dict, context) -> bool:
        request = params.get("request") or {}
        rid = str(request.get("request") or "")
        navigation = params.get("navigation") is not None
        kind = {
            "destination": request.get("destination"),
            "initiatorType": request.get("initiatorType"),
        }
        buffer.counter += 1
        buffer.seq += 1
        nav = buffer.nav
        url = str(request.get("url") or "")  # whose cookies they are
        skipped = held.masker.skipped
        held.masker.cookies(request.get("cookies"), url)  # learned, never shown
        headers, dropped = held.masker.headers(request.get("headers"), url)
        buffer.unmasked += held.masker.skipped - skipped
        row = {
            "id": f"{nav}.{buffer.counter}",
            "seq": buffer.seq,
            "at": seconds(params.get("timestamp")),
            "method": held.masker.text(str(request.get("method") or "")),
            "url": held.masker.url(url, keep_query=True),
            "status": None,
            "status_text": "",
            "type": resource_type(kind, "", navigation),
            "mime": "",
            "size": None,
            "took_ms": None,
            "failed": False,
            "error": None,
            "context": context,
            "nav": nav,
            "navigation": navigation and context == held.top,
            "request_headers": headers,
            "response_headers": [],
            "headers_dropped": dropped,
        }
        _bump(buffer.seen, nav)
        timestamp = params.get("timestamp")
        buffer.in_flight[(rid, _redirect(params))] = _Flight(
            row, rid, timestamp if number(timestamp) else None, kind, navigation
        )
        while len(buffer.in_flight) > REQUEST_LIMIT:
            buffer.in_flight.popitem(last=False)
        if row["type"] not in STATIC_TYPES:  # a static one may still fail
            self._keep(buffer, row, Located(held.session_id, rid, "", None))
        return True

    def _flight(self, buffer: Buffer, params: dict) -> _Flight | None:
        """The request this end belongs to, or None for one never seen
        starting: its start was evicted, came before the hold, or was the
        spare tab's (whose end can arrive after the tab stops being hidden)."""
        request = params.get("request") or {}
        return buffer.in_flight.pop(
            (str(request.get("request") or ""), _redirect(params)), None
        )

    def _completed(self, buffer: Buffer, held: Held, params: dict, context) -> bool:
        flight = self._flight(buffer, params)
        if flight is None:
            return False
        row = flight.row
        response = params.get("response") or {}
        mask = held.masker.text  # every text a server or page chose (ruling 13)
        status = response.get("status")
        size = response.get("bodySize")
        if not number(size):  # never content.size
            size = response.get("bytesReceived")
        row["status"] = (
            status if isinstance(status, int) and not isinstance(status, bool) else None
        )
        row["status_text"] = mask(str(response.get("statusText") or ""))
        row["mime"] = mask(str(response.get("mimeType") or ""))
        row["size"] = size if number(size) else None
        skipped = held.masker.skipped
        row["response_headers"], dropped = held.masker.headers(
            response.get("headers"), str((params.get("request") or {}).get("url") or "")
        )
        buffer.unmasked += held.masker.skipped - skipped
        row["headers_dropped"] += dropped
        row["took_ms"] = took_ms(params, flight.begin)
        if row["type"] == "other":
            row["type"] = resource_type(flight.kind, row["mime"], flight.navigation)
        if row["nav"] != buffer.nav:  # it began on an older page
            _strip(row)
        self._settle(buffer, held, flight)
        return True

    def _errored(self, buffer: Buffer, held: Held, params: dict, context) -> bool:
        flight = self._flight(buffer, params)
        if flight is None:
            return False
        row = flight.row
        row["failed"] = True
        row["error"] = held.masker.text(str(params.get("errorText") or ""))
        row["took_ms"] = took_ms(params, flight.begin)
        self._settle(buffer, held, flight)
        return True

    def _settle(self, buffer: Buffer, held: Held, flight: _Flight) -> None:
        """An ended request: a successful static one is counted and let go;
        anything else is kept and indexed. Both are tailed."""
        row = flight.row
        visible = row["id"] in buffer.index
        if is_static_success(row):
            _bump(buffer.static, row["nav"])
            if visible:
                del buffer.index[row["id"]]
                buffer.requests.remove(row)
            self._tail(held.workspace, "network", row, kept=False)
        else:
            located = Located(held.session_id, flight.request, row["mime"], row["size"])
            if visible:
                buffer.index[row["id"]] = located
            else:
                self._keep(buffer, row, located)
            self._tail(held.workspace, "network", row, kept=True)
        if counts_in_hint(row):
            _bump(buffer.failed, row["nav"])
            buffer.failed_total += 1

    def _keep(self, buffer: Buffer, row: dict, located: Located) -> None:
        if len(buffer.requests) == buffer.requests.maxlen:
            evicted = buffer.requests.popleft()
            buffer.index.pop(evicted["id"], None)
        buffer.requests.append(row)
        buffer.index[row["id"]] = located

    def _started(self, buffer: Buffer, held: Held, params: dict, context) -> bool:
        """A navigation of the top-level context (plan ruling 17): a new page.
        What the old one holds keeps no query from here on."""
        if context is None or context != held.top:
            return False
        old = buffer.nav
        for entry in buffer.console:
            if entry["nav"] == old and entry["source"] is not None:
                entry["source"] = {
                    **entry["source"],
                    "url": strip_query(entry["source"]["url"]),
                }
        for row in buffer.requests:
            if row["nav"] == old:
                _strip(row)
        for flight in buffer.in_flight.values():  # static ones are only here
            if flight.row["nav"] == old:
                _strip(flight.row)
        if buffer.marks:
            buffer.marks[-1]["url"] = strip_query(buffer.marks[-1]["url"])
        buffer.nav += 1
        buffer.counter = 0
        buffer.marks.append(
            {
                "nav": buffer.nav,
                "url": held.masker.url(str(params.get("url") or ""), keep_query=True),
                "at": seconds(params.get("timestamp")),
            }
        )
        marked = {m["nav"] for m in buffer.marks}
        for counter in buffer.counters():
            for nav in [n for n in counter if n not in marked]:
                del counter[nav]
        return True

    def _moved(self, buffer: Buffer, held: Held, params: dict, context) -> bool:
        """The top-level page committed, or changed its URL without a new page."""
        if context is None or context != held.top or not buffer.marks:
            return False
        buffer.marks[-1]["url"] = held.masker.url(
            str(params.get("url") or ""), keep_query=True
        )
        return True

    def _destroyed(self, buffer: Buffer, held: Held, params: dict, context) -> bool:
        """A context is gone: what it held lands, and it is remembered no more."""
        landed = self._resolve(buffer, held, context)
        held.recent.pop(context, None)
        held.origins.pop(context, None)
        return landed


_HANDLERS: dict[str, Callable[..., bool]] = {
    "log.entryAdded": Capture._log,
    "network.beforeRequestSent": Capture._sent,
    "network.responseCompleted": Capture._completed,
    "network.fetchError": Capture._errored,
    "browsingContext.navigationStarted": Capture._started,
    "browsingContext.navigationCommitted": Capture._moved,
    "browsingContext.historyUpdated": Capture._moved,
    "browsingContext.fragmentNavigated": Capture._moved,
    "browsingContext.contextDestroyed": Capture._destroyed,
}


def _navigated(method: str, params: dict) -> tuple[str, str | None] | None:
    """The origin a navigation event takes its context to, and a URL on it to
    ask with; None when the event is not one: a start, a commit, or a
    navigation's request (a redirect included). A navigation with no URL
    leaves the origin unknown (EVERY)."""
    if method in _NAVIGATED:
        url = params.get("url")
    elif method == "network.beforeRequestSent" and params.get("navigation") is not None:
        url = (params.get("request") or {}).get("url")
    else:
        return None
    if not isinstance(url, str):
        return EVERY, None
    origin = scope_origin(url)
    return origin, (_site(url, origin) if origin else None)


def _site(url: str, origin: str) -> str:
    """``url`` cut to its scheme and authority, without userinfo, when that
    has the same origin (it does, but for a URL that will not split): what is
    kept to ask again with carries no path, query or password."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    site = f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}/"
    return site if scope_origin(site) == origin else url


def _inherits(held: Held, context, params: dict) -> None:
    """Note what a new context's "" inherits: its parent; a popup's opener;
    or, for a tab with neither, every leashed secret. Bounded by CONTEXTS; a
    context forgotten inherits from the top."""
    parent = params.get("parent")
    if parent is None:
        opener = params.get("originalOpener")
        parent = opener if isinstance(opener, str) and opener else EVERY
    held.parents[context] = parent
    held.parents.move_to_end(context)
    while len(held.parents) > CONTEXTS:
        held.parents.popitem(last=False)


def _weight(params) -> int:
    """Roughly how many bytes an event holds: its strings' lengths and a few
    for every other value. One pass, no copies, no serialising."""
    total, stack = 0, [params]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            total += len(item)
        elif isinstance(item, dict):
            total += 8 * len(item)
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            total += 8 * len(item)
            stack.extend(item)
        else:
            total += 8
    return total


def _is_ready(held: Held, origin: str) -> bool:
    """Whether ``origin``'s forms are in ``held``'s masker: "" always is."""
    return origin == "" or origin in held.ready


def _context(method: str, params: dict):
    if method == "log.entryAdded":
        return (params.get("source") or {}).get("context")
    return params.get("context")


def _digest(key) -> bytes:
    """A console key in 16 bytes: equal keys, equal digests. A key carries the
    whole raw text, and a replay remembers REPLAY_DEPTH of them per context."""
    return hashlib.blake2b(
        repr(key).encode("utf-8", "replace"), digest_size=16
    ).digest()


def _strip(row: dict) -> None:
    """A row of an older page: its URL and URL-bearing headers lose their query.
    New lists and dicts, so a copy already handed out does not change."""
    row["url"] = strip_query(row["url"])
    for name in ("request_headers", "response_headers"):
        row[name] = [
            {"name": h["name"], "value": strip_query(h["value"])}
            if h["name"].lower() in URL_HEADERS
            else h
            for h in row[name]
        ]


def _bump(counter: dict[int, int], nav: int) -> None:
    counter[nav] = counter.get(nav, 0) + 1


def _redirect(params: dict) -> int:
    count = params.get("redirectCount")
    return count if isinstance(count, int) and not isinstance(count, bool) else 0
