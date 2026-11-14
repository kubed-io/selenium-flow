"""The secrets in scope on a browser's pages, and the scrub that keeps them out.

No result or error an agent reads may carry the value of a secret allowed on
the site it came from, typed or not (spec 2026-11-14-secret-scope, ruling 1).
The values worth looking for change only with the origin, so they are read when
it changes (`origins.OriginChanged`, ruling 6), once, off the event loop, and
held as `Forms` for that workspace's browser alone: never written, logged, put
in a record or returned (ruling 12).

**What a call is scrubbed with** (rulings 10, 11, 14, 15): the sets of the
origins before and after it, plus everything this browser kept - the sets of
origins it left and the forms from before a rotation. An origin is *known* once
its set was built for this browser; one that is missing (None), not yet built
here, or a selected frame is *unknown*, and adds every leashed secret
(`leashed()`), read then. An opaque page ("") is known and empty (ruling 13):
nothing is read for it.

**Secrets off** (the catalogue None): every answer is empty, nothing is read and
nothing is scanned, and a result comes back as the very object it was.

**Fails closed** (ruling 2). Anything that goes wrong in a scrub - a read past
`BUILD_TIMEOUT`, a read that fails, an error it cannot reach the inside of -
becomes `faults.ScrubFailed`, raised from nothing so neither the failure nor a
value is reachable from it, and the failure is logged by its type alone.

**Whose browser: the slot.** A workspace has one entry, held for one browser.
Who takes it:

- a browser, implicitly, on its first call, when the slot is free and the
  workspace has not finished with it;
- a browser named by `opened` (`open_session`, a reopen that starts afresh),
  explicitly, whoever held it: the holder is finished with and its forms go;
- the browser named by `carry` (a reap's reopen that keeps the pages),
  explicitly, whoever held it: it takes the reaped browser's sets and kept
  forms with the slot, or, when another browser held it, that holder's sets
  and kept forms as its own kept forms - over-masked, never under.

Who frees it: `ended`, which finishes with its browser, and the TTL - an entry
nothing touches for ``ttl`` seconds (the workspace's) goes, since the old
browser's id may be lost with the workspace's record.

**What a browser holds is kept until the browser ends, or carried to the
browser that replaces it.** `ended` parks the forms the browser held (its sets
and kept forms) for its workspace rather than dropping them, because the Grid's
reap is often heard (`session.ended`) before the reopen that carries it: a
later `carry` from that browser claims them, `opened` (a fresh start) discards
them, and the park goes with the TTL. A parked workspace's forms are part of
what a stale call and a resource are scrubbed with.

Anything else is *stale*: a call still in flight on a browser that was ended,
replaced or carried from while it ran, or a browser not yet given the slot. It
is scrubbed with what the workspace holds and every leashed secret, and it never
installs, replaces, slides or builds anything, so a late call can cost a read
and never a value the current browser holds. A stale streak is logged once, by
workspace, never by browser.

**Locking.** One `threading.Lock` guards the per-workspace entries and the
builds in flight; it is never held while the catalogue, capture, a `done`
callback or a future is called or waited on. A build stores its forms and
leaves the in-flight table in one step under it, so a caller sees the set or
the build, never neither. The entries are process memory (AGENTS.md, "Scaling:
one replica").

**No loop.** The executor runs the reads something asked for - an origin
change, a scrub - and nothing else; `close()` stops its threads.

Plain: no Selenium, no protocol layer (`tests/test_boundaries.py`). The
catalogue is duck-typed (`forms_for`, `leashed`, `revision`): `workspace/`
never imports `secrets`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, InvalidStateError, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from cachetools import TTLCache

from .. import faults
from ..capture import WORKSPACES
from ..redaction import Forms, scrub, scrub_error, scrub_values, taints
from ..urls import scope_origin
from .origins import OriginChanged, Origins, Retired, Streaks

log = logging.getLogger(__name__)

# Seconds a scrub waits for a read of the catalogue, all of its reads together.
BUILD_TIMEOUT = 10
# Reads at once: one per origin change, so a handful is plenty.
WORKERS = 4


@dataclass
class _Entry:
    """One workspace's browser and what it holds.

    ``sets`` are the forms per origin built for this browser at ``revision``;
    ``kept`` is what it carries besides: the sets from before a rotation, and
    what a holder it was carried over held, until the browser ends (ruling 10).
    ``former`` are the browsers it was carried from (`Scrubber.carry`), so a
    build that started for one before the carry still lands here; a call on one
    is stale, and starts nothing.
    """

    browser: str
    revision: object = None
    sets: dict[str, Forms] = field(default_factory=dict)
    kept: Forms = field(default_factory=Forms)
    former: set[str] = field(default_factory=set)

    def serves(self, browser: str) -> bool:
        return browser == self.browser or browser in self.former

    def adopt(self, revision) -> bool:
        """Take the catalogue's ``revision``; True when it moved, the sets now kept."""
        if self.revision is None:
            self.revision = revision
            return False
        if revision == self.revision:
            return False
        self.kept = self.held()
        self.sets = {}
        self.revision = revision
        return True

    def held(self) -> Forms:
        forms = self.kept
        for built in self.sets.values():
            forms = forms | built
        return forms


def _settle(future: Future, result=None, failed: bool = False) -> None:
    """Complete ``future`` once; a second completion (after `close`) is dropped."""
    try:
        if failed:
            future.set_exception(faults.ScrubFailed())
        else:
            future.set_result(result)
    except InvalidStateError:
        pass


def _done_with(done: Callable[[Forms | None], object]) -> Callable[[Future], None]:
    def call(future: Future) -> None:
        try:
            done(None if future.exception() is not None else future.result())
        except Exception as exc:  # noqa: BLE001 - a callback never fails a build
            log.warning("a secret build's callback failed: %s", type(exc).__name__)

    return call


class Scrubber:
    """Reads the secrets in scope per workspace browser, and scrubs what it answers."""

    def __init__(
        self,
        catalogue,
        capture=None,
        *,
        origins: Origins | None = None,
        ttl: float,
        workers: int = WORKERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._catalogue = catalogue
        self._capture = capture
        self._origins = origins
        self._lock = threading.Lock()
        self._entries: TTLCache = TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock)
        self._retired = Retired(TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock))
        # workspace -> the entry of a browser that ended, until a carry claims it.
        self._parked: TTLCache = TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock)
        # (workspace, origin) -> (revision, forms) `carries` read for a page a
        # browser is about to be given, so its build does not read them again.
        self._primed: TTLCache = TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock)
        self._streaks = Streaks()
        # (workspace, browser, origin, revision) -> the build in flight.
        self._flight: dict[tuple, Future] = {}
        self._leashing: Future | None = None
        self._workers = workers
        self._pool = self._new_pool()

    def _new_pool(self) -> ThreadPoolExecutor:
        """This scrubber's threads, started as reads are asked for."""
        return ThreadPoolExecutor(
            max_workers=self._workers, thread_name_prefix="secret-scope"
        )

    # --- building -----------------------------------------------------------

    def changed(self, event: OriginChanged) -> None:
        """The `Origins` listener: start the new origin's build, return at once.

        Safe on the event loop: it takes a short lock and submits, and reads
        nothing itself.
        """
        if self._catalogue is None:
            return
        try:
            self.build(event.workspace, event.browser, event.new)
        except Exception as exc:  # noqa: BLE001 - a listener never fails the call
            log.warning("a secret build could not start: %s", type(exc).__name__)

    def build(
        self,
        workspace: str,
        browser: str,
        origin: str,
        done: Callable[[Forms | None], object] | None = None,
    ) -> Future:
        """The forms of the secrets allowed on ``origin``, built for ``browser``.

        Shared: one read in flight per (workspace, browser, origin, revision),
        and none for an origin already built for this browser, so a navigation
        and the call's result URL read once. The read runs on this scrubber's
        own threads. An opaque origin ("") completes at once, empty, with no
        read. A read that fails is retried as every leashed secret; if that
        fails too the future fails with `faults.ScrubFailed`. So does a build
        for a stale browser, which reads nothing.

        ``done(forms)`` is called with the forms, or with None when it failed.
        It runs on one of this scrubber's threads (or at once, in the caller's,
        when the build is already complete), so it must return promptly and
        never wait on this scrubber: a callback that blocks on a scrub holds the
        thread its own read may need.
        """
        future, key, primed = self._start(workspace, browser, origin)
        if primed is not None:
            self._hand_to_capture(workspace, primed)
        if key is not None:
            try:
                self._pool.submit(self._run, key, future)
            except RuntimeError:  # closed
                with self._lock:
                    if self._flight.get(key) is future:
                        del self._flight[key]
                _settle(future, failed=True)
        if done is not None:
            future.add_done_callback(_done_with(done))
        return future

    def _start(
        self, workspace, browser, origin
    ) -> tuple[Future, tuple | None, Forms | None]:
        """The future for a build; its key when it is new and must be submitted;
        and the forms `carries` already read for it, installed here, which
        capture is still to be handed."""
        future: Future = Future()
        if self._catalogue is None or origin == "":
            future.set_result(Forms())
            return future, None, None
        if not isinstance(origin, str):  # unknown is never built as empty
            future.set_exception(faults.ScrubFailed())
            return future, None, None
        with self._lock:
            entry = self._entry(workspace, browser)
            if entry is None:  # stale: reads nothing, installs nothing
                future.set_exception(faults.ScrubFailed())
                return future, None, None
            built = entry.sets.get(origin)
            if built is not None:
                future.set_result(built)
                return future, None, None
            primed = self._primed.pop((workspace, origin), None)
            if primed is not None and entry.revision in (None, primed[0]):
                entry.revision = primed[0]
                entry.sets[origin] = primed[1]
                future.set_result(primed[1])
                return future, None, primed[1]
            pending = self._pending(workspace, browser, origin, entry.revision)
            if pending is not None:
                return pending, None, None
            key = (workspace, browser, origin, entry.revision)
            self._flight[key] = future
            return future, key, None

    def _run(self, key: tuple, future: Future) -> None:
        """One build, on a worker: read, store, hand to capture, complete."""
        workspace, browser, origin, _ = key
        catalogue = self._catalogue
        forms = None
        try:
            # The revision first: read after the values, a rotation between the
            # two would stamp old values as current and they would never be rebuilt.
            revision = catalogue.revision()
            try:
                forms = catalogue.forms_for(origin)
            except Exception as exc:  # noqa: BLE001 - retried as every leashed secret
                log.warning(
                    "reading the secrets allowed on an origin failed: %s; "
                    "reading every leashed secret",
                    type(exc).__name__,
                )
                forms = catalogue.leashed()
        except Exception as exc:  # noqa: BLE001 - logged by type, never its text
            log.warning("a secret build failed: %s", type(exc).__name__)
            forms = None
        with self._lock:
            if self._flight.get(key) is future:
                del self._flight[key]
            entry = self._entries.get(workspace)
            if forms is not None and entry is not None and entry.serves(browser):
                if entry.revision is None:
                    entry.revision = revision
                if revision == entry.revision:
                    entry.sets[origin] = entry.sets.get(origin, Forms()) | forms
                else:  # read at another revision: kept, never taken as current
                    entry.kept = entry.kept | forms
                self._entries[workspace] = entry
        if forms is None:
            _settle(future, failed=True)
            return
        self._hand_to_capture(workspace, forms)
        _settle(future, forms)

    def _hand_to_capture(self, workspace: str, forms: Forms) -> None:
        """Mask ``forms`` in what the workspace captures; never fails a build."""
        if self._capture is None:
            return
        try:
            self._capture.secret(workspace, forms)
        except Exception as exc:  # noqa: BLE001 - capture never fails a build
            log.warning("capture could not take a secret set: %s", type(exc).__name__)

    # --- reading ------------------------------------------------------------

    def _entry(self, workspace: str, browser: str) -> _Entry | None:
        """The workspace's entry for ``browser``, under the lock, or None when
        ``browser`` is stale; slides its TTL.

        The slot is taken only when free, and never by a browser the workspace
        has finished with: an entry is never replaced by another browser here,
        only by `opened` and `carry`. A browser it was carried from is stale
        too: it neither slides the entry nor builds for it.
        """
        entry = self._entries.get(workspace)
        if entry is None and not self._retired.has(workspace, browser):
            entry = _Entry(browser)
        if entry is None or entry.browser != browser:
            if self._streaks.stale(workspace):
                log.info(
                    "workspace %s: a call on a browser that does not hold its slot "
                    "was scrubbed with every leashed secret (a call that outlived "
                    "its browser, or a browser not yet opened)",
                    workspace,
                )
            return None
        self._entries[workspace] = entry
        self._streaks.held(workspace)
        return entry

    @property
    def active(self) -> bool:
        """Whether there are secrets to look for at all (a catalogue)."""
        return self._catalogue is not None

    def _held_by(self, workspace: str) -> Forms:
        """What the workspace holds, whichever browser's, parked included; under
        the lock."""
        forms = Forms()
        for held in (self._entries.get(workspace), self._parked.get(workspace)):
            if held is not None:
                forms = forms | held.held()
        return forms

    def _stale(self, workspace: str, deadline: float) -> Forms:
        """What a stale browser's call is scrubbed with: what the workspace holds,
        and every leashed secret. It writes nothing."""
        with self._lock:
            held = self._held_by(workspace)
        return held | self._leashed(deadline)

    def _pending(self, workspace, browser, origin, revision) -> Future | None:
        """A build in flight for this origin, under the lock: at this revision, or
        one started before the revision was known."""
        pending = self._flight.get((workspace, browser, origin, revision))
        if pending is None:
            pending = self._flight.get((workspace, browser, origin, None))
        return pending

    def _bounded(self, read: Callable[[], object], deadline: float):
        """``read()`` on a worker, waited on until ``deadline``, else TimeoutError.

        A read that timed out is cancelled, so one still queued never runs.
        """
        future = self._pool.submit(read)
        try:
            return future.result(timeout=max(0.0, deadline - time.monotonic()))
        except TimeoutError:
            future.cancel()
            raise

    def _leashed(self, deadline: float) -> Forms:
        """Every leashed secret, read now: one read in flight, shared, never kept."""
        with self._lock:
            future = self._leashing
            fresh = future is None
            if fresh:
                future = self._leashing = self._pool.submit(self._catalogue.leashed)
        if fresh:
            future.add_done_callback(self._leashed_done)
        return future.result(timeout=max(0.0, deadline - time.monotonic()))

    def _leashed_done(self, future: Future) -> None:
        with self._lock:
            if self._leashing is future:
                self._leashing = None

    def forms(self, workspace: str, browser: str, origins: Iterable) -> Forms:
        """What a call on these ``origins`` (before and after) is scrubbed with.

        Blocking, for a worker thread. Raises `faults.ScrubFailed`.
        """
        if self._catalogue is None:
            return Forms()
        origins = list(origins)
        return self._guarded(lambda: self._forms(workspace, browser, origins))

    def _forms(self, workspace: str, browser: str, origins: list) -> Forms:
        deadline = time.monotonic() + BUILD_TIMEOUT
        if not browser:  # no browser: a stale call, which takes no slot
            return self._stale(workspace, deadline)
        revision = self._bounded(self._catalogue.revision, deadline)
        with self._lock:
            entry = self._entry(workspace, browser)
            rotated = entry is not None and entry.adopt(revision)
        if entry is None:
            return self._stale(workspace, deadline)
        if rotated and self._origins is not None:
            current = self._origins.current(workspace, browser)
            if current:
                self.build(workspace, browser, current)
        unknown = bool(
            self._origins is not None and self._origins.framed(workspace, browser)
        )
        waiting: dict[str, Future] = {}
        unbuilt: list[str] = []
        with self._lock:
            entry = self._entry(workspace, browser)
            for origin in set(origins) if entry is not None else ():
                if not isinstance(origin, str):
                    unknown = True
                elif origin and origin not in entry.sets:
                    pending = self._pending(workspace, browser, origin, entry.revision)
                    if pending is None:
                        unbuilt.append(origin)
                    else:
                        waiting[origin] = pending
        if entry is None:  # ended meanwhile
            return self._stale(workspace, deadline)
        if unbuilt:  # unknown now; built for the calls after this one
            unknown = True
            for origin in unbuilt:
                self.build(workspace, browser, origin)
        if waiting:
            _, late = wait(
                list(waiting.values()), timeout=max(0.0, deadline - time.monotonic())
            )
            if late:
                raise TimeoutError
            for future in waiting.values():
                future.result()  # a build that failed fails the scrub
        with self._lock:
            entry = self._entry(workspace, browser)
            if entry is None:
                held, unknown = self._held_by(workspace), True
            else:
                # A build read at another revision went to kept: not known.
                unknown = unknown or any(o not in entry.sets for o in waiting)
                held = entry.held()
        return held | self._leashed(deadline) if unknown else held

    def _guarded(self, scrubbing: Callable[[], object]):
        """``scrubbing()``, or `faults.ScrubFailed` from nothing when it raises."""
        failure = None
        try:
            return scrubbing()
        except Exception as exc:  # noqa: BLE001 - fails closed, whatever it was
            failure = type(exc).__name__
        log.warning("a secret scrub failed: %s", failure)
        raise faults.ScrubFailed() from None

    # --- scrubbing ----------------------------------------------------------

    def result(
        self, workspace: str, browser: str, value, origins: Iterable
    ) -> tuple[object, bool]:
        """``value`` scrubbed, and whether its top-level ``url`` carried a value.

        That URL is withheld from the record (ruling 21). With nothing in scope
        the value comes back as it is, the same object, unscanned.
        """
        if self._catalogue is None:
            return value, False
        origins = list(origins)

        def scrubbing():
            forms = self._forms(workspace, browser, origins)
            if not forms:
                return value, False
            url = value.get("url") if isinstance(value, dict) else None
            return scrub_values(value, forms), taints(url, forms)

        return self._guarded(scrubbing)

    def error(self, workspace: str, browser: str, exc: BaseException, origins) -> None:
        """Scrub ``exc`` in place, its chain included; its type is kept.

        Raises `faults.ScrubFailed` when that cannot be done. Call it, and raise
        what it raises, outside the ``except`` that caught ``exc``: Python sets a
        raised exception's ``__context__`` to the one being handled.
        """
        if self._catalogue is None:
            return
        origins = list(origins)
        self._guarded(
            lambda: scrub_error(exc, self._forms(workspace, browser, origins))
        )

    def resource(self, workspace: str, value):
        """A resource's text, or an answer built of plain values (a dict, a
        list), scrubbed with what the workspace kept and every leashed secret
        (ruling 16): a resource read has no before and after. It writes
        nothing, and takes no browser's slot. With nothing to look for the
        value comes back as it is, the same object, unscanned.
        """
        if self._catalogue is None or value is None or value == "":
            return value

        def scrubbing():
            deadline = time.monotonic() + BUILD_TIMEOUT
            forms = self._stale(workspace, deadline)
            if not forms:
                return value
            if isinstance(value, str):
                return scrub(value, forms)
            return scrub_values(value, forms)

        return self._guarded(scrubbing)

    def carries(self, workspace: str, url) -> bool:
        """Whether ``url`` carries the value of a secret allowed on its own
        origin, or one the workspace holds: asked of a page before a browser
        not yet given the slot records it (an open, a reopen; ruling 21).

        Reads the secrets allowed on that origin, unless the workspace holds
        them at the catalogue's revision, and none for an opaque one; every
        leashed secret when that read fails. What it read is kept for the
        workspace's next build of that origin, at that revision, which takes it
        rather than reading again (ruling 6: one read per origin change); it
        writes nothing else. Raises `faults.ScrubFailed`.
        """
        if self._catalogue is None or not isinstance(url, str) or not url:
            return False
        origin = scope_origin(url)

        def asking():
            deadline = time.monotonic() + BUILD_TIMEOUT
            # The revision first, as a build reads it (`_run`).
            revision = self._bounded(self._catalogue.revision, deadline)
            with self._lock:
                forms = self._held_by(workspace)
                known = any(
                    held is not None
                    and held.revision == revision
                    and origin in held.sets
                    for held in (
                        self._entries.get(workspace), self._parked.get(workspace)
                    )
                )
            if origin and not known:
                try:
                    read = self._bounded(
                        lambda: self._catalogue.forms_for(origin), deadline
                    )
                except Exception as exc:  # noqa: BLE001 - every leashed instead
                    log.warning(
                        "reading the secrets allowed on an origin failed: %s; "
                        "reading every leashed secret",
                        type(exc).__name__,
                    )
                    read = self._leashed(deadline)
                else:  # the build for this page takes it rather than reading
                    with self._lock:
                        self._primed[(workspace, origin)] = (revision, read)
                forms = forms | read
            return taints(url, forms)

        return self._guarded(asking)

    def every(self, done: Callable[[Forms | None], object] | None = None) -> Future:
        """Every leashed secret, read on this scrubber's threads: for a page
        whose origin nobody knows (capture's, ruling 9). Not kept.

        ``done(forms)`` is called with the forms, or with None when the read
        failed, as `build`'s is; with secrets off, at once, with no forms.
        """
        future: Future = Future()
        if self._catalogue is None:
            future.set_result(Forms())
        else:
            try:
                self._pool.submit(self._every, future)
            except RuntimeError:  # closed
                _settle(future, failed=True)
        if done is not None:
            future.add_done_callback(_done_with(done))
        return future

    def _every(self, future: Future) -> None:
        try:
            forms = self._catalogue.leashed()
        except Exception as exc:  # noqa: BLE001 - logged by type, never its text
            log.warning("reading every leashed secret failed: %s", type(exc).__name__)
            _settle(future, failed=True)
            return
        _settle(future, forms)

    # --- lifetime -----------------------------------------------------------

    def opened(self, workspace: str, browser: str) -> None:
        """Give the workspace's slot to ``browser``, a browser just opened, afresh.

        Whoever held it is finished with, as `ended` would make it, and its
        forms go: keeping them is `carry`'s. Calling it again for the holder
        changes nothing. What an ended browser parked is discarded.
        """
        with self._lock:
            self._parked.pop(workspace, None)
            entry = self._entries.get(workspace)
            if entry is not None and entry.browser == browser:
                self._entries[workspace] = entry
            else:
                if entry is not None:
                    self._retired.add(workspace, entry.browser)
                self._entries[workspace] = _Entry(browser)
            self._streaks.held(workspace)

    def carry(self, workspace: str, old: str, new: str) -> None:
        """Give the workspace's slot to ``new``, a reap's reopen of ``old``, keeping
        what the slot held: it is the same workspace on the same pages.

        When ``old`` holds the slot, or ended and parked what it held, its
        entry moves to ``new``, sets and kept alike: its sets stay known while
        the catalogue's revision is the one they were read at, and become kept
        forms once it moves. When another browser holds it, or nobody does,
        ``new`` takes it
        as `opened` gives it - whoever held it is finished with - but fails
        safe: everything the holder held (its sets and kept forms) becomes
        ``new``'s kept forms, so nothing masked before is unmasked after. Its
        origins are unknown until built for ``new``. ``old`` is finished with
        either way, and never takes the slot again. Calling it again for the
        holder changes nothing.

        A park the slot did not become is claimed too, as kept forms, in every
        branch: ``new`` may have taken the free slot implicitly before this
        runs (capture hears its first page first), and then holds it already.
        """
        with self._lock:
            self._retired.add(workspace, old)
            parked = self._parked.pop(workspace, None)
            entry = self._entries.get(workspace)
            if entry is None:  # what an ended browser parked
                entry, parked = parked, None
            if entry is not None and entry.browser == old:
                entry.former.add(old)
                entry.browser = new
            elif entry is None or entry.browser != new:
                fresh = _Entry(new)
                if entry is not None:
                    self._retired.add(workspace, entry.browser)
                    fresh.kept = entry.held()
                    fresh.former = entry.former | {entry.browser}
                entry = fresh
            if parked is not None:  # over-masked, never under
                entry.kept = entry.kept | parked.held()
            self._entries[workspace] = entry
            self._streaks.held(workspace)

    def ended(self, workspace: str, browser: str) -> None:
        """Free the workspace's slot when it is ``browser``'s, parking what it
        held: kept until the browser ends, or carried to the browser that
        replaces it. A reap is often heard before its reopen, so a `carry`
        from ``browser`` claims the park; `opened` and the TTL drop it.

        A park already there (a browser that ended before this one was carried
        to) is kept in this one's kept forms, not replaced.

        ``browser`` is finished with: a late call on it is stale and never
        takes the slot.
        """
        with self._lock:
            self._retired.add(workspace, browser)
            entry = self._entries.get(workspace)
            if entry is not None and entry.browser == browser:
                del self._entries[workspace]
                earlier = self._parked.get(workspace)
                if earlier is not None:
                    entry.kept = entry.kept | earlier.held()
                self._parked[workspace] = entry

    def close(self) -> None:
        """Stop the threads; a build still owed fails, so nothing waits on it.

        Usable after: a read asked for later starts a thread again. The
        server's lifespan, which closes it, runs again when FastMCP enters it
        for a later session.
        """
        with self._lock:
            pool, self._pool = self._pool, self._new_pool()
            owed = list(self._flight.values())
            self._flight.clear()
        pool.shutdown(wait=False, cancel_futures=True)
        for future in owed:
            _settle(future, failed=True)
