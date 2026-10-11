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

from cachetools import TTLCache

from . import (
    CONSOLE_LIMIT,
    DEFAULT_TTL,
    NAVIGATIONS,
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
        self._cache: TTLCache = TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock)
        self._sessions: OrderedDict[str, Held] = OrderedDict()
        # workspace -> the idle session evicted from `_sessions`, parked so a
        # hold again is heard and its secrets are kept; bounded like them.
        self._parked: OrderedDict[str, Held] = OrderedDict()
        self._hidden: dict[str, set[str]] = {}
        self._tails: dict[str, list[TailSubscription]] = {}

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
        with self._lock:
            held = self._sessions.get(session_id)
            if again:
                if held is None:
                    held = self._unpark(workspace, session_id)
                if held is None or held.workspace != workspace:
                    log.info("capture: a hold again in %s is not heard", workspace)
                    return
                self._sessions.move_to_end(session_id)
                held.live = True
                if held.top is None:
                    held.top = top
                held.holding = (
                    {ctx: [] for ctx, seen in held.recent.items() if seen}
                    if browser == "chrome"
                    else None
                )
                return
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

    def ingest(self, workspace, session_id, method, params) -> None:
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
                return
            handler = _HANDLERS.get(method)
            if handler is None:
                return
            buffer = self._cache.get(workspace)
            if buffer is None:  # expired, or evicted: it starts again
                buffer = Buffer()
            if handler(self, buffer, held, params, context):
                self._cache[workspace] = buffer

    def replayed(self, workspace, session_id) -> None:
        """Chrome's replay is over: what is still held lands, past its overlap."""
        with self._lock:
            held = self._sessions.get(session_id)
            if held is None or held.workspace != workspace or held.holding is None:
                return
            buffer = self._cache.get(workspace) or Buffer()
            landed = False
            for ctx in list(held.holding):
                landed = self._resolve(buffer, held, ctx) or landed
            held.holding = None
            if landed:
                self._cache[workspace] = buffer

    def detached(self, workspace, session_id) -> None:
        """A hold ended, or failed before it began: anything held is dropped,
        since nothing is left to align it against."""
        with self._lock:
            held = self._sessions.get(session_id)
            if held is not None and held.workspace == workspace:
                held.holding = None
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

    def secret(self, workspace: str, forms: Iterable[str]) -> None:
        """Mask ``forms`` in everything this workspace's session captures from
        now on, and in every session that replaces it."""
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
        held.masker.cookies(request.get("cookies"))  # learned, never shown
        headers, dropped = held.masker.headers(request.get("headers"))
        row = {
            "id": f"{nav}.{buffer.counter}",
            "seq": buffer.seq,
            "at": seconds(params.get("timestamp")),
            "method": held.masker.text(str(request.get("method") or "")),
            "url": held.masker.url(str(request.get("url") or ""), keep_query=True),
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
        row["response_headers"], dropped = held.masker.headers(response.get("headers"))
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
