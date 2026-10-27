"""The pages a person reaches while they hold Control: the monitor's sink for
the ``control`` reason (spec 2026-10-27-live-control, rulings 19, 20 and 26).

Heard on the monitor's held connection, capture's plumbing, never a second one
(R7): the grant follows a Grid id (`follow`), the monitor tells this sink what
the connection hears for ``control``, and the release ends the list (`stop`).
A page is a ``browsingContext.load`` (the page actually reached, after
redirects) or ``browsingContext.historyUpdated`` (a single-page app's route
change) in a top-level context: the top the hold learned, handed in through
`attached`, or one created with no parent. A frame's load is not a page, and
neither is a tab open before the hold other than the top (ruling 26).

BiDi events carry no title, so one ``script.evaluate`` of ``document.title``
is sent on the same connection for each page, bounded by ``REPLY_TIMEOUT``; a
title that does not come back is left out. A URL with no origin
(``about:blank``, ``data:``) records nothing; one the same as the page before
it in the same context is the same page. At most ``HISTORY_CAP`` entries, the
newest, and a count of the ones that went.

Nothing is heard between `attached` and `subscribed`: subscribing
``contextCreated`` re-sends it, parent null, for every tab already open, and
a tab open before the hold is not followed (ruling 26). The monitor flushes
the subscribe before it says `subscribed`, so a tab opened after it is.

The URLs are kept as heard, in memory, and go into the hand-back's summary and
nowhere else (ruling 20): nothing here writes History, and nothing here logs a
URL, a title or a Grid id. The summary shapes them as History does
(`urls.without_userinfo`, `urls.page_of`) when it is built.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field

from ..bidi import REPLY_TIMEOUT, Connection
from ..urls import origin_of
from ..workspace.store import HISTORY_CAP

LOAD = "browsingContext.load"
HISTORY = "browsingContext.historyUpdated"
CREATED = "browsingContext.contextCreated"
EVENTS = (LOAD, HISTORY, CREATED)
PAGES = (LOAD, HISTORY)


@dataclass(eq=False)
class _List:
    """One followed browser's pages, oldest first."""

    entries: list[dict] = field(default_factory=list)
    dropped: int = 0
    tops: set[str] = field(default_factory=set)
    # From `attached` to `subscribed`: what the subscribe re-sends is not heard.
    holding: bool = False
    last: dict[str, str] = field(default_factory=dict)  # context -> its last URL
    reading: set[asyncio.Task] = field(default_factory=set)


class Pages:
    """Implements `monitor.Sink` for ``control``; read by the hand-back.

    ``connection(grid_id)`` is the connection the monitor holds to that browser
    (`Monitor.connection`), which the title is read on; set by the server.
    Every method runs on the loop.
    """

    def __init__(self, *, cap: int = HISTORY_CAP):
        self.connection: Callable[[str], Connection | None] = lambda _grid_id: None
        self._cap = cap
        self._lists: dict[str, _List] = {}

    # ---- the episode's side ------------------------------------------------

    def follow(self, grid_id: str) -> None:
        """Start a list for this browser; one already under way starts again."""
        self._end(self._lists.pop(grid_id, None))
        self._lists[grid_id] = _List()

    def stop(self, grid_id: str) -> list[dict]:
        """The list, ended; [] when the browser was never followed."""
        followed = self._lists.pop(grid_id, None)
        self._end(followed)
        return [] if followed is None else followed.entries

    def listed(self, grid_id: str) -> tuple[list[dict], int]:
        """(pages, dropped): each page ``{"url", "title"?}``, oldest first."""
        followed = self._lists.get(grid_id)
        if followed is None:
            return [], 0
        return [dict(entry) for entry in followed.entries], followed.dropped

    # ---- the Sink ----------------------------------------------------------

    def attached(self, workspace, session_id, browser, top, *, again: bool) -> None:
        followed = self._lists.get(session_id)
        if followed is None:
            return
        if top:
            followed.tops.add(top)
        followed.holding = True

    def ingest(self, workspace, session_id, method, params) -> None:
        followed = self._lists.get(session_id)
        if followed is None or followed.holding or not isinstance(params, dict):
            return
        context = params.get("context")
        if not isinstance(context, str) or not context:
            return
        if method == CREATED:
            if params.get("parent") is None:
                followed.tops.add(context)
            return
        if method not in PAGES or context not in followed.tops:
            return
        url = params.get("url")
        if not isinstance(url, str) or not origin_of(url):
            return
        if followed.last.get(context) == url:
            return  # the same page: a reload, or a route that replaced itself
        followed.last[context] = url
        entry = {"url": url}
        followed.entries.append(entry)
        if len(followed.entries) > self._cap:
            del followed.entries[0]
            followed.dropped += 1
        self._read_title(session_id, followed, context, entry)

    def replayed(self, workspace, session_id) -> None:
        """Chrome's replay on a hold again is over; `subscribed` follows it."""

    def subscribed(self, workspace, session_id) -> None:
        """What the subscribe re-sent is behind: from here, every event is new."""
        followed = self._lists.get(session_id)
        if followed is not None:
            followed.holding = False

    def detached(self, workspace, session_id) -> None:
        """The connection went: a hold again says ``again`` and is replayed."""

    def lost(self, workspace, session_id) -> None:
        """An event the listener dropped. Which one is not known, so it is
        not counted as a page."""

    # ---- inside --------------------------------------------------------------

    def _read_title(
        self, grid_id: str, followed: _List, context: str, entry: dict
    ) -> None:
        conn = self.connection(grid_id)
        if conn is None:
            return
        task = asyncio.get_running_loop().create_task(
            self._title(conn, context, entry)
        )
        followed.reading.add(task)
        task.add_done_callback(followed.reading.discard)

    @staticmethod
    async def _title(conn: Connection, context: str, entry: dict) -> None:
        """Fill in ``entry``'s title; a failure of any kind leaves it out."""
        with contextlib.suppress(Exception):
            async with asyncio.timeout(REPLY_TIMEOUT):
                result = await conn.command(
                    "script.evaluate",
                    {
                        "expression": "document.title",
                        "target": {"context": context},
                        "awaitPromise": False,
                    },
                )
            value = (result.get("result") or {}) if isinstance(result, dict) else {}
            if value.get("type") == "string" and isinstance(value.get("value"), str):
                entry["title"] = value["value"]

    @staticmethod
    def _end(followed: _List | None) -> None:
        if followed is None:
            return
        for task in list(followed.reading):
            task.cancel()
        followed.reading.clear()
