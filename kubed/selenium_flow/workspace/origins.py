"""Where each workspace's browser is: its current origin, and an event when it moves.

The secrets in scope on a page are the ones whose `_allowed_urls` name its
origin, so the values worth looking for change only when the origin does (spec
2026-11-14-secret-scope, ruling 6). This keeps each workspace browser's current
origin, in memory, compared on every result's URL (ruling 7), and tells its
listeners once per change: the scrubber is the first, and reads the secrets
allowed on the new origin then, not on every result.

An origin is scheme, host and port, as the leash compares them
(`urls.scope_origin`). An opaque page (`about:blank`, `data:`, an error page) is
the origin "", which has no secret in scope (ruling 13) - known, and empty. Only
a missing URL is unknown, and it changes nothing here.

**One browser per workspace: the slot.** Who takes it:

- a browser, implicitly, on its first arrival, when the slot is free and the
  workspace has not finished with it;
- a browser named by `opened` (`open_session`, the reap's reopen), explicitly,
  whoever held it: the holder is finished with. This is the same rule as the
  scrubber's `opened` and `carry`, which give the slot whoever held it; there
  is no carry here, since a reopened browser starts on the top page and its
  first arrival is a change, so the reap's reopen calls `opened` beside
  `Scrubber.carry`, and the browser it reopened is framed (unknown) after.

Who frees it: `forget`, which finishes with its browser, and the TTL - a slot
nobody touches for ``ttl`` seconds (the workspace's) heals on its own, since
the old browser's id may be lost with the workspace's record. A new holder
starts with no current origin and no frames heard. Its frame is known to be
unselected only when `opened` gave it the slot; a browser that took a free slot
implicitly may be one whose slot was evicted or expired with a frame selected,
so its frame is *unknown* - `framed` answers True, the safe side (ruling 15) -
until a `framed(..., False)` or `framed(..., True)` write says which.

Anything else is **stale** - a call still in flight on a browser that was ended
or replaced while it ran, or a browser not yet given the slot: it is answered
and changes nothing, so it cannot move the current browser's origin or clear
its selected frame (ruling 15). Asked about, a browser that does not hold the
slot is framed: unknown, the safe side. A stale streak is logged once, by
workspace, never by browser. Process memory, like the session's lock (AGENTS.md,
"Scaling: one replica"). Not on the monitor bus, whose events carry no URLs.

Plain: no Selenium, no protocol layer (`tests/test_boundaries.py`).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from typing import NamedTuple

from cachetools import LRUCache, TTLCache

from ..capture import WORKSPACES
from ..urls import scope_origin
from .store import DEFAULT_TTL_SECONDS

log = logging.getLogger(__name__)

# Browsers remembered per workspace as finished with. A late call comes from
# the browser just ended, so a few cover it; one older than these is not stale
# by a call's length, and a workspace holding a browser still refuses it.
RETIRED = 16


class Retired:
    """The browsers each workspace has finished with: they never take its slot again.

    Not locked: its owner (`Origins`, `Scrubber`) holds its own lock around it.
    ``cache`` bounds the workspaces, as a `cachetools` cache does.
    """

    def __init__(self, cache: MutableMapping) -> None:
        self._cache = cache

    def add(self, workspace: str, browser: str) -> None:
        done = self._cache.get(workspace)
        if done is None:
            done = deque(maxlen=RETIRED)
        if browser not in done:
            done.append(browser)
        self._cache[workspace] = done

    def has(self, workspace: str, browser: str) -> bool:
        return browser in self._cache.get(workspace, ())


class Streaks:
    """The workspaces in a stale streak, so each streak is logged once.

    Not locked, like `Retired`. A streak ends when the slot's holder is heard.
    """

    def __init__(self) -> None:
        self._cache: LRUCache = LRUCache(maxsize=WORKSPACES)

    def stale(self, workspace: str) -> bool:
        """Note a stale call; True when it begins a streak (log it)."""
        began = workspace not in self._cache
        self._cache[workspace] = True
        return began

    def held(self, workspace: str) -> None:
        self._cache.pop(workspace, None)


class OriginChanged(NamedTuple):
    """A workspace's browser reached a new origin.

    ``old`` is the origin it left, or None when it had none yet. ``frame`` is a
    frame's origin heard for the first time in this browser, which never moves
    the top's: its ``old`` is always None.
    """

    workspace: str
    browser: str
    old: str | None
    new: str
    frame: bool


@dataclass
class _Held:
    browser: str
    origin: str | None = None
    frames: set[str] = field(default_factory=set)
    # None: unknown, which `framed` answers as True. Only `opened` knows False.
    framed: bool | None = None


class Origins:
    """Each workspace browser's current origin, and the listeners told when it moves."""

    def __init__(
        self,
        *,
        ttl: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self._listeners: list[Callable[[OriginChanged], object]] = []
        self._held: TTLCache = TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock)
        self._retired = Retired(TTLCache(maxsize=WORKSPACES, ttl=ttl, timer=clock))
        self._streaks = Streaks()

    def listen(self, fn: Callable[[OriginChanged], object]) -> None:
        """Call ``fn(event)`` on every `OriginChanged` from now on.

        It runs in the thread that reported the arrival, after this object's
        lock is released and outside any store write; it should only start
        work. One that raises is logged by its type and never fails the call.
        """
        with self._lock:
            self._listeners.append(fn)

    def _browser(self, workspace: str, browser: str) -> _Held | None:
        """The workspace's entry for ``browser``, or None when it is stale; under
        the lock. Slides the slot's TTL.

        A browser takes a free slot, starting afresh; it never takes one another
        browser holds, and one the workspace finished with never takes it again.
        """
        held = self._held.get(workspace)
        if held is None and not self._retired.has(workspace, browser):
            held = _Held(browser)
        if held is None or held.browser != browser:
            return None
        self._held[workspace] = held
        self._streaks.held(workspace)
        return held

    @staticmethod
    def _said_stale(workspace: str) -> None:
        log.info(
            "workspace %s: a browser that does not hold its slot was ignored (a "
            "call that outlived its browser, or a browser not yet opened)",
            workspace,
        )

    def opened(self, workspace: str, browser: str) -> None:
        """Give the workspace's slot to ``browser``, a browser just opened.

        Whoever held it is finished with, as `forget` would make it; the new
        holder starts afresh. Calling it again for the holder changes nothing.
        """
        with self._lock:
            held = self._held.get(workspace)
            if held is not None and held.browser != browser:
                self._retired.add(workspace, held.browser)
                held = None
            if held is None:
                held = _Held(browser, framed=False)
            self._held[workspace] = held
            self._streaks.held(workspace)

    def arrived(
        self, workspace: str, browser: str, url, *, frame: bool = False
    ) -> str | None:
        """Note that ``browser`` is on ``url``; answer its origin, or None for no URL.

        A top-level arrival on another origin than the browser's current one
        (none counts as another) moves it and emits. A frame's origin emits the
        first time this browser hears it. A path change, a repeat and a missing
        URL emit nothing.
        """
        if not isinstance(url, str):
            return None
        origin = scope_origin(url)
        began = False
        with self._lock:
            held = self._browser(workspace, browser)
            event = None
            if held is None:  # stale: answered, nothing changes
                began = self._streaks.stale(workspace)
            elif frame:
                if origin not in held.frames:
                    held.frames.add(origin)
                    event = OriginChanged(workspace, browser, None, origin, True)
            elif held.origin != origin:
                event = OriginChanged(workspace, browser, held.origin, origin, False)
                held.origin = origin
            listeners = list(self._listeners) if event is not None else []
        if began:
            self._said_stale(workspace)
        for fn in listeners:
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001 - a listener never fails the call
                log.warning("an origin-changed listener failed: %s", type(exc).__name__)
        return origin

    def current(self, workspace: str, browser: str) -> str | None:
        """``browser``'s current top-level origin, or None when it has none yet."""
        with self._lock:
            held = self._held.get(workspace)
            return held.origin if held is not None and held.browser == browser else None

    def framed(self, workspace: str, browser: str, value: bool | None = None) -> bool:
        """Whether a frame is selected in ``browser``; set first when ``value`` is.

        A selected frame's origin is not in the result, so while one is the
        scrub treats the page as unknown (ruling 15). A `frame` result's
        ``in_frame`` sets it. A browser `opened` gave the slot starts with
        none; one that took a free slot implicitly, or does not hold the slot
        at all, is unknown and answered True. A stale browser's write is
        ignored: a stale call never clears the current browser's frame.
        """
        began = False
        with self._lock:
            if value is None:
                held = self._held.get(workspace)
                if held is None or held.browser != browser or held.framed is None:
                    return True
                return held.framed
            held = self._browser(workspace, browser)
            if held is not None:
                held.framed = bool(value)
                return held.framed
            began = self._streaks.stale(workspace)
        if began:
            self._said_stale(workspace)
        return True

    def forget(self, workspace: str, browser: str) -> None:
        """Drop ``browser``'s entry, when the workspace's entry is that browser's,
        freeing the slot; ``browser`` is finished with and never takes it again."""
        with self._lock:
            self._retired.add(workspace, browser)
            held = self._held.get(workspace)
            if held is not None and held.browser == browser:
                del self._held[workspace]
