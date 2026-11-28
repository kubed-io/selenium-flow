"""Where workspaces and their sessions are kept: two kinds of record by key.

Backs the workspace manager in ``workspaces.py``, and nothing else. A
workspace's record, under its name, holds the names of its sessions in order
and its history; a session's, under ``<workspace>/<name>``, holds the browser
it may have, never the browser itself, which lives on the Grid (WS-7).

A session's record outlives its browser: one whose browser was reaped, or
ended from the admin UI, keeps the browser choice, and its page stays on the
workspace's history, so the next ``open_session`` picks up where it left off.
``session_id`` is empty when detached.

Nothing here expires a browser: a Grid node reaps a session idle past
``SE_NODE_SESSION_TIMEOUT``. What these backends expire is the mapping, which is
a cache and is allowed to be wrong: ``workspaces.py`` checks a record against
the Grid before trusting it. A key's expiry is decided by its place when it is
written (`_Writes._lifetime`, WS-87).

Both backends honour ``ttl``, so swapping one for the other cannot change
behaviour. Redis does it natively with ``EX``; memory keeps an expiry stamp and
treats a lapsed entry as absent.

Selection is explicit via ``workspace.store`` in the config. Left unset it infers
redis from ``redis.host`` or ``redis.url`` being set.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from .. import faults
from ..core.session_caps import SOURCES
from ..urls import origin_of, page_of, without_userinfo
from . import sessions as session_keys
from .settings import CALLER, SET_BY

if TYPE_CHECKING:
    from ..config import RedisSettings, SessionSettings, WorkspaceSettings

log = logging.getLogger(__name__)

# Every key is written under this prefix, which is what makes sharing a database
# with other applications safe.
DEFAULT_PREFIX = "selenium-flow:workspace:"
# The pointer store writes under the workspace prefix too, in this sub-namespace.
# No caller key has this shape, but a SCAN of the prefix finds it, so
# `records()` skips it.
POINTER_NAMESPACE = "pointer:"
# So does the tab store, for the same reason.
TABS_NAMESPACE = "tabs:"
# Redis's own default: an install with an index convention passes REDIS_DB,
# and the prefix keeps it safe if not.
DEFAULT_DB = 0
# How long a workspace is kept after it was last used: a day, since records are
# the history the admin view shows. The browser it names is still reaped on the
# Grid's schedule.
DEFAULT_TTL_SECONDS = 86400
# A session not at the head of its workspace's list is kept this long after its
# last use (WS-87).
DEFAULT_SUB_TTL_SECONDS = 3600
# How many times a Redis update re-reads after another writer got there first.
# A key rewritten this often within one round trip is a fault, not contention.
UPDATE_RETRIES = 10
# How many origins a workspace's history keeps; the oldest goes first.
HISTORY_CAP = 100

# What `update` applies: the record as it is now in, the record to store out,
# or None to store nothing. Each is the type its key holds (`record_type`).
Change = Callable[["Record"], "Record | None"]
# For `upsert`: the record as it is, or None when the key is absent.
Create = Callable[["Record | None"], "Record | None"]
# For `change`: the same, and a note on what the write found, handed back.
Noted = Callable[["Record | None"], "tuple[Record | None, Any]"]


class StoreUnavailable(RuntimeError):
    """Redis was asked for and cannot be used.

    Raised by ``redis_client`` and left to propagate out of ``from_settings``,
    never downgraded to :class:`MemoryStore`: a pod that refuses to start is
    restarted by Kubernetes until Redis answers, while one that started on the
    wrong store is never corrected. Memory is the answer only when nothing
    asked for Redis.
    """


class StoreConflict(RuntimeError):
    """An update kept losing to other writers and gave up (Redis only)."""


@dataclass(frozen=True)
class Session:
    """One session's record, under ``<workspace>/<name>``: the browser it
    holds, if any, and what makes that browser replaceable.

    ``settings`` make a browser replaceable: reopening the same browser on the
    session's page (its top entry in its workspace's history) makes a refresh
    invisible, and makes ``open_session`` with no arguments carry on after the
    browser has gone.

    ``session_id`` is empty when no browser is attached, an ordinary state: the
    Grid reaped it, or an admin ended it.
    """

    # The subject that holds this session (`principal.subject_of`), set by the
    # `open_session` that made it, or "" for a record no subject holds yet,
    # which the next subject to open it takes (WS-84).
    holder: str = ""
    session_id: str = ""
    opened_at: float = 0.0
    # What the session was opened with, so a reopen uses the same browser.
    settings: dict = field(default_factory=dict)
    # Who set each of those settings: "config", "client" or "caller", one per
    # key. A record whose sources are missing or do not match its settings is
    # not read (see `from_json`).
    set_by: dict = field(default_factory=dict)
    # Cookies and storage the session saved (site_data/). Kept with the
    # record so it expires with it; never on this server's disk (with Redis,
    # as durable as Redis).
    site_data: dict = field(default_factory=dict)
    # A silent reopen's notice, held until the first result from that browser
    # says it: {"browser": id, "cause": "reaped" | "lost", "report": {...} |
    # None}, or {}.
    reopened: dict = field(default_factory=dict)
    # When this session's last call ended, in wall time; 0.0 before any. It
    # is what tells a reap (idle past the window) from a lost browser.
    called_at: float = 0.0
    # When the Grid last answered a keep-alive ping for the held browser, in
    # wall time; 0.0 before any. To the node a ping is activity, so it moves the
    # reap clock like a call does.
    pinged_at: float = 0.0
    # Who opened the browser this session holds, {"kind", "username"}, for
    # the admin list. Shown, never consulted.
    opened_by: dict | None = None
    # Seconds the Grid lets this session sit idle before reaping it, read from
    # its node at the last open. Kept when the session ends; None when the
    # Grid did not say.
    grid_timeout: int | None = None
    # The window the held browser has, as ``WxH``: measured when it opened (the
    # node's default when nothing was asked) and moved by a resize. What the
    # admin list shows while it is live, since asking the browser on every
    # poll would be activity that keeps it from ever being reaped; ``window``
    # is what was asked for.
    window_seen: str | None = None
    # What this session's open sent: [{"key", "value", "sources"}], written at
    # every open and reopen, for the admin's Capabilities section only. Never
    # reused: the next open recomputes it.
    sent: list = field(default_factory=list)
    # What the Grid says ran: {"version", "node", "live_view"}, each None
    # when it did not say. Kept when the session ends, like `sent`.
    ran: dict | None = None
    # How the last session ended, {"at", "by": "admin" | "caller"}, or None:
    # set by `end_browser`, cleared by the next open. A reap writes nothing.
    ended: dict | None = None
    # The `label` a record written before WS-81 stored, read for the move
    # (WS-90) and never written: `to_json` leaves it out.
    label_was: str | None = None

    @property
    def last_used(self) -> float:
        """When this session was last used: the latest of its open, its last
        call and the last ping the Grid answered (WS-88). Derived, never
        stored."""
        return max(self.opened_at, self.called_at, self.pinged_at)

    @property
    def notice(self) -> dict | None:
        """The notice held for the browser this record holds: ``{"cause",
        "report"}``, or None.

        A held notice with no valid cause, one written before causes were
        kept, reads as ``reaped``.
        """
        held = self.reopened
        if not held or held.get("browser") != self.session_id:
            return None
        cause = held.get("cause")
        report = held.get("report")
        return {
            "cause": cause if cause in ("reaped", "lost") else "reaped",
            "report": report if isinstance(report, dict) and report else None,
        }

    @property
    def attached(self) -> bool:
        """Whether a browser is currently held."""
        return bool(self.session_id)

    @property
    def window(self) -> str | None:
        """The window size this session is set to, as ``WxH``.

        Kept current by ``resize``, so it is both the size the browser is now
        and the size it would come back as after a reap. None when the
        session never named one: the window is the Grid node's default, and a
        number here would claim to know it.
        """
        width, height = self.settings.get("width"), self.settings.get("height")
        return f"{width}x{height}" if width and height else None

    def to_json(self) -> str:
        data = asdict(self)
        del data["label_was"]
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Session | None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        # Valid JSON that is not a record (a pointer's `[x, y]`) is a miss too.
        return cls.from_data(data) if isinstance(data, dict) else None

    @classmethod
    def from_data(cls, data: dict) -> Session | None:
        """The session a stored object holds, or None when it does not read:
        a session's own record, or the session fields of a workspace's record
        in the shape before WS-81."""
        try:
            settings = data.get("settings")
            settings = settings if isinstance(settings, dict) else {}
            set_by = _set_by(data.get("set_by"), settings)
            session_id = str(data.get("session_id") or "") if set_by is not None else ""
            site_data = data.get("site_data")
            reopened = data.get("reopened")
            opened_at = data.get("opened_at", 0.0)
            label = data.get("label")
            return cls(
                holder=_holder(data.get("holder")),
                # A record without valid sources keeps no settings and lets go
                # of its browser, so the Grid reaps it and the next call opens
                # afresh; the rest of it stays.
                session_id=session_id,
                # A number, or a string float() reads; anything float() refuses
                # makes the record a miss.
                opened_at=_seconds(
                    opened_at
                    if isinstance(opened_at, (int, float))
                    else float(opened_at)
                ),
                settings=settings if set_by is not None else {},
                set_by=set_by if set_by is not None else {},
                site_data=site_data if isinstance(site_data, dict) else {},
                reopened=reopened if isinstance(reopened, dict) else {},
                called_at=_seconds(data.get("called_at")),
                pinged_at=_seconds(data.get("pinged_at")),
                opened_by=_opener(data.get("opened_by")),
                grid_timeout=_timeout(data.get("grid_timeout")),
                window_seen=_size(data.get("window_seen")),
                sent=_sent(data.get("sent")),
                ran=_ran(data.get("ran")),
                ended=_ended(data.get("ended")),
                label_was=label if isinstance(label, str) and label else None,
            )
        except (ValueError, TypeError):
            # A malformed entry is a cache miss, not an outage.
            return None

    def reshaped(self, settings: dict) -> Session:
        """The same record, with some of its settings replaced.

        A merge: the caller names only the settings it just changed, and the
        others (the browser, the timeouts) survive, or a reopen would replay a
        browser nobody asked for. What is handed in is marked the caller's.
        """
        merged = {**self.settings, **settings}
        set_by = {**self.set_by, **dict.fromkeys(settings, CALLER)}
        width, height = merged.get("width"), merged.get("height")
        seen = f"{width}x{height}" if width and height else self.window_seen
        return replace(self, settings=merged, set_by=set_by, window_seen=seen)

    def with_site_data(self, site_data: dict) -> Session:
        """The same record holding this site data."""
        return replace(self, site_data=dict(site_data or {}))

    def delivered(self) -> Session:
        """The same record, its reopen notice handed to a caller."""
        return replace(self, reopened={})

    def detached(self, ended: dict | None = None) -> Session:
        """The same record with no browser, keeping the context it had.

        Not a delete: the last page and the settings (with their sources) are
        what the next open reads from, so ending a browser must not take them. What
        the last session sent and ran stay too: the admin's idle card shows the
        last browser. ``ended`` says how it ended.
        """
        return replace(self, session_id="", ended=ended)


@dataclass(frozen=True)
class Old:
    """What a workspace's record in the shape before WS-81 held besides its
    history: the default session's fields inline, who owned it, and the names
    of its sub-sessions. Read only, for the move (WS-90); never written."""

    session: Session
    owner: str | None
    subs: list[str]


@dataclass(frozen=True)
class Workspace:
    """A workspace's record, under its name: its sessions, by name in order,
    and its history. Nothing of a browser's: each session's is its own
    record (`Session`).

    The session at the head of the list is the one that lasts as the
    workspace does (WS-87). ``old`` is what a record in the shape before WS-81
    held besides its history (`Old`), None for every other.
    """

    # The names of this workspace's sessions, in the order they were listed
    # (WS-81). The first is the head.
    sessions: list = field(default_factory=list)
    # Where the workspace has been: one {"origin", "url", "at", "session"} per
    # origin and session, newest first, every session's on the workspace's
    # record (WS-54). Written by `visited`; the admin History tab reads it.
    history: list = field(default_factory=list)
    old: Old | None = None
    # The pending notice of each session an open moved off the head (WS-86):
    # its name to ``{"to": the opener, "at": epoch seconds}``. Each is told
    # once (`told`), and dropped when its name leaves the list or heads it
    # again.
    main_moved: dict = field(default_factory=dict)

    @property
    def head(self) -> str | None:
        """The session at the head of the list, or None when it is empty. A
        record in the shape before WS-81 is headed by its default."""
        if self.sessions:
            return self.sessions[0]
        return session_keys.DEFAULT if self.old is not None else None

    def listing(self, name: str) -> Workspace:
        """The same record with ``name`` at the end of its list, when the
        list does not hold it."""
        if name in self.sessions:
            return self
        return replace(self, sessions=[*self.sessions, name])

    def without(self, names: Iterable[str]) -> Workspace:
        """The same record with ``names`` gone from its list, and their
        notices with them (WS-86)."""
        gone = set(names)
        return replace(
            self,
            sessions=[n for n in self.sessions if n not in gone],
            main_moved={k: v for k, v in self.main_moved.items() if k not in gone},
        )

    def headed(self, name: str, idle: str | None, at: float) -> Workspace:
        """The same record with ``name`` moved to the head and ``idle``, the
        head it takes over from, right behind it, the rest in their order
        (WS-86). ``idle`` keeps a notice of the move, made ``at``; ``name``'s
        own notice goes, since it heads the list again.

        ``idle``'s newest entry takes the time ``at`` too: as the head's top it
        was kept whole however old, and from now it is kept for ``sub_ttl``,
        as long as ``idle``'s record, restamped at the move, can reopen to it.
        No other entry changes.

        Only while ``idle`` is still the head and ``name`` is another listed
        name: otherwise the record is ``self``."""
        if idle is None or idle != self.head or name == idle:
            return self
        if name not in self.sessions:
            return self
        rest = [n for n in self.sessions if n not in (name, idle)]
        moved = {k: v for k, v in self.main_moved.items() if k != name}
        moved[idle] = {"to": name, "at": at}
        history = list(self.history)
        top = next((i for i, v in enumerate(history) if _tag(v) == idle), None)
        if top is not None:
            history[top] = {**history[top], "at": at}
        return replace(
            self, sessions=[name, idle, *rest], main_moved=moved, history=history
        )

    def told(self, name: str) -> tuple[Workspace, dict | None]:
        """The same record without ``name``'s notice, and the notice as it is
        told: ``{"from", "to", "at"}``, ``to`` the head now and ``at`` in whole
        seconds. ``(self, None)`` when ``name`` has none (WS-86).

        A ``name`` that heads the list again another way (the head before it
        expired and was pruned), or is no longer listed, has its notice
        dropped untold: it would say that a main session is not main."""
        entry = self.main_moved.get(name)
        if entry is None:
            return self, None
        rest = replace(
            self, main_moved={k: v for k, v in self.main_moved.items() if k != name}
        )
        if name == self.head or name not in self.sessions:
            return rest, None
        return rest, {"from": name, "to": self.head, "at": int(entry["at"])}

    def page(self, session: str | None) -> str:
        """The page ``session`` (a name) is on: its newest entry, or ""."""
        return next((v["url"] for v in self.history if _tag(v) == session), "")

    def entries(self, session: str | None) -> list[dict]:
        """``session``'s own entries, newest first."""
        return [v for v in self.history if _tag(v) == session]

    def to_json(self) -> str:
        # Never `old`: a write is always in the new shape. No notice, no key,
        # so a record no open moved keeps the JSON it had before WS-86.
        data: dict = {"sessions": list(self.sessions), "history": self.history}
        if self.main_moved:
            data["main_moved"] = self.main_moved
        return json.dumps(data)

    @classmethod
    def from_json(cls, raw: str | bytes) -> Workspace | None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            # Valid JSON that is not a record (a pointer's `[x, y]`) is a miss
            # too; `.get` on it would raise AttributeError.
            return None
        history = _visits(data.get("history"))
        if "sessions" in data:
            names = _names(data.get("sessions"))
            return cls(
                sessions=names,
                history=history,
                main_moved=_moves(data.get("main_moved"), names),
            )
        # The shape before WS-81: the default's fields inline, beside an owner
        # and the sub-sessions' names.
        session = Session.from_data(data)
        if session is None:
            return None
        owner = data.get("owner")
        old = Old(
            session,
            owner if isinstance(owner, str) and owner else None,
            _names(data.get("subs")),
        )
        return cls(sessions=[], history=history, old=old)

    def visited(
        self,
        *urls: str | None,
        now: float | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
        session: str = session_keys.DEFAULT,
        sub_ttl: float = DEFAULT_SUB_TTL_SECONDS,
    ) -> Workspace:
        """The same record, ``session`` (a name) having landed on ``urls`` in
        order, then pruned (`pruned`).

        Each one with an origin moves that session's entry for the origin to
        the top with its URL and the time; another session's entry for it
        stays. One without (None for a URL withheld after a secret write,
        ``about:blank``, ``data:``) records nothing.
        """
        now = time.time() if now is None else now
        history = list(self.history)
        for url in urls:
            origin = origin_of(url or "")
            if origin:
                history = [
                    {"origin": origin, "url": url, "at": now, "session": session},
                    *(
                        v for v in history
                        if (v["origin"], _tag(v)) != (origin, session)
                    ),
                ]
        return replace(self, history=history).pruned(now, ttl, sub_ttl)

    def pruned(
        self, now: float, ttl: float, sub_ttl: float = DEFAULT_SUB_TTL_SECONDS
    ) -> Workspace:
        """The same record without the history entries older than ``ttl``,
        except each session's top one while that session can reopen: it is
        where the reopen goes back to. The head's top always stays; another
        session's stays for ``sub_ttl``, as long as its record can live
        unused, and is then an entry like any other.

        Only such a top entry keeps its whole URL. Elsewhere a URL keeps its
        origin, path and hash route (never the route's query): a query string
        or fragment carries OAuth codes and reset tokens, and nothing but a
        reopen needs them.

        At most ``HISTORY_CAP`` entries stay, the oldest dropped first, except
        the head's top.
        """
        head = self.head
        kept, tops, head_top = [], set(), None
        for v in self.history:
            session = _tag(v)
            top = session not in tops
            tops.add(session)
            if top and session == head:
                head_top = v
                kept.append(v)
            elif top and v["at"] >= now - sub_ttl:
                kept.append(v)
            elif v["at"] >= now - ttl:
                kept.append({**v, "url": page_of(v["url"])})
        capped = kept[:HISTORY_CAP]
        if head_top is not None and head_top not in capped:
            # The oldest of what is kept, so it goes last.
            capped = [*kept[: HISTORY_CAP - 1], head_top]
        return replace(self, history=capped)

    def history_cleared(self, live: Iterable[str] = ()) -> Workspace:
        """The same record with only the current page of the head and of each
        session in ``live`` (those that still have a record) left in the
        history: one with no record cannot reopen, so its page is cleared
        too."""
        keep = {*live, *([self.head] if self.head else [])}
        kept, tops = [], set()
        for v in self.history:
            if _tag(v) not in tops and _tag(v) in keep:
                tops.add(_tag(v))
                kept.append(v)
        return replace(self, history=kept)


# What the store holds under a key: a workspace's record under its name, a
# session's under `<workspace>/<name>` (`record_type`).
Record = Workspace | Session


def record_type(key: str) -> type[Workspace] | type[Session]:
    """The type of record ``key`` holds: a session's when it names one."""
    return Session if session_keys.split(key)[1] else Workspace


def _parsed(key: str, raw) -> Record | None:
    """The record stored under ``key``, read as the type its key says."""
    return record_type(key).from_json(raw)


def _opener(value) -> dict | None:
    """A stored `opened_by`, or None for anything that is not one."""
    if not isinstance(value, dict) or value.get("kind") not in ("admin", "oidc"):
        return None
    name = value.get("username")
    return {"kind": value["kind"], "username": name if isinstance(name, str) else None}


def _set_by(raw, settings: dict) -> dict | None:
    """A stored ``set_by``, or None unless it names a known source for exactly
    the keys of ``settings``."""
    if not isinstance(raw, dict) or set(raw) != set(settings):
        return None
    if not all(v in SET_BY for v in raw.values()):
        return None
    return dict(raw)


def _sent(raw) -> list:
    """A stored ``sent``: the entries with a string key and known sources."""
    if not isinstance(raw, list):
        return []
    return [
        {"key": e["key"], "value": e.get("value"), "sources": list(e["sources"])}
        for e in raw
        if isinstance(e, dict)
        and isinstance(e.get("key"), str)
        and isinstance(e.get("sources"), list)
        and e["sources"]
        and all(s in SOURCES for s in e["sources"])
    ]


def _ran(raw) -> dict | None:
    """A stored ``ran``, each fact None when it is not the right type."""
    if not isinstance(raw, dict):
        return None
    version, node, live = raw.get("version"), raw.get("node"), raw.get("live_view")
    return {
        "version": version if isinstance(version, str) else None,
        "node": node if isinstance(node, str) else None,
        "live_view": live if isinstance(live, bool) else None,
    }


def _ended(raw) -> dict | None:
    """A stored ``ended``: a time and who ended it, or None."""
    if not isinstance(raw, dict) or raw.get("by") not in ("admin", "caller"):
        return None
    at = raw.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    return {"at": float(at), "by": raw["by"]}


def _holder(raw) -> str:
    return raw if isinstance(raw, str) else ""


def _names(raw) -> list[str]:
    """A stored list of session names: its strings, each once, in order."""
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(n for n in raw if isinstance(n, str) and n))


def _moves(raw, names: list[str]) -> dict:
    """A stored ``main_moved``: the well-formed entries whose session is in
    ``names``, none when it is missing or no map (WS-86)."""
    if not isinstance(raw, dict):
        return {}
    moves = {}
    for name, entry in raw.items():
        if name not in names or not isinstance(entry, dict):
            continue
        to, at = entry.get("to"), entry.get("at")
        if not isinstance(to, str) or not to:
            continue
        if isinstance(at, bool) or not isinstance(at, (int, float)):
            continue
        try:
            if not math.isfinite(at) or at < 0:
                continue
            moves[name] = {"to": to, "at": float(at)}
        except OverflowError:  # an int too large for a float
            continue
    return moves


def _size(raw) -> str | None:
    """A stored ``window_seen``: ``WxH`` in whole pixels, or None."""
    if isinstance(raw, str) and re.fullmatch(r"[1-9][0-9]{0,5}x[1-9][0-9]{0,5}", raw):
        return raw
    return None


def _seconds(raw) -> float:
    """A stored time: a finite, non-negative number, or 0.0."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    try:
        return float(raw) if math.isfinite(raw) and raw >= 0 else 0.0
    except OverflowError:  # an int too large for a float
        return 0.0


def _timeout(raw) -> int | None:
    """A stored ``grid_timeout``: a positive whole number of seconds, or None."""
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def _tag(visit: dict) -> str:
    """The session a history entry names: the default for one from before
    sessions were named."""
    return visit.get("session", session_keys.DEFAULT)


def _visits(raw) -> list[dict]:
    """The well-formed entries of a stored history, none when there is none. A
    URL that does not parse is no entry, or every later write would trip on
    it. An entry written before sessions were named is the default's."""
    if not isinstance(raw, list):
        return []
    return [
        {
            "origin": v["origin"],
            "url": v["url"],
            "at": float(v["at"]),
            "session": _tag(v),
        }
        for v in raw
        if isinstance(v, dict)
        and isinstance(v.get("origin"), str) and v["origin"]
        and isinstance(v.get("url"), str) and origin_of(v["url"])
        and isinstance(v.get("at"), (int, float)) and not isinstance(v["at"], bool)
        and isinstance(_tag(v), str)
        and _tag(v)
    ]


def _never(_key: str) -> bool:
    return False


class WorkspaceStore(Protocol):
    """Maps a key to its record: a workspace's name to its `Workspace`, and a
    session's key, ``<workspace>/<name>``, to its `Session` (`record_type`)."""

    kind: str
    # How long an entry is kept: things derived from a store keep its retention
    # (`pointer.matching`).
    ttl: int
    # How long a session not at the head of its workspace's list is kept after
    # its last use (WS-87).
    sub_ttl: int
    # Whether a workspace's record, and its head's, are written with no
    # expiry: it has a config (`workspace.config.Configs.kept`). Asked with
    # the workspace's name, once per write.
    kept: Callable[[str], bool]

    def get(self, key: str) -> Record | None: ...

    # `get` of each of ``keys``, in order, in one round trip where the backend
    # allows: a listing of a workspace's sessions reads them all at once.
    def get_many(self, keys: list[str]) -> list[Record | None]: ...

    def set(self, key: str, record: Record) -> None: ...

    # A read-change-write that cannot revert a concurrent write, as a read, a
    # wait on the Grid and a `set` would. `fn` gets the record as it is at write
    # time and returns the one to store, or None to store nothing; it may run
    # more than once, so it must only compute. It is never called for an absent
    # key. Returns what is stored afterwards.
    def update(self, key: str, fn: Change) -> Record | None: ...

    # `update`, and an absent key too: `fn` gets None, so a first write can
    # never land between a read and a fallback `set`.
    def upsert(self, key: str, fn: Create) -> Record | None: ...

    # A write that answers: `fn` returns the record to store and a note on
    # what it found there, and the note comes back beside what is stored.
    # Only the last run's note: a run thrown away by a retry saw a record that
    # was never written. `create` is `upsert`; without it an absent key is
    # `(None, None)` and `fn` never runs.
    def change(
        self, key: str, fn: Noted, *, create: bool = False
    ) -> tuple[Record | None, Any]: ...

    def delete(self, key: str) -> None: ...

    # Apply the expiry `kept` says now to a workspace's record and its head's,
    # each if it has one, without writing either: a config saved or removed
    # changes it at once. A session's key gets its own.
    def retain(self, workspace: str) -> None: ...

    # Optional, for the admin view only: the MCP surface never lists workspaces.
    # A store that cannot enumerate cheaply may leave these out, and the admin
    # view shows an empty list.
    # Workspaces only: a session's record is not a workspace.
    def records(self) -> dict[str, Workspace]: ...

    # `workspace`'s session records by name, from a scan of its keys.
    def sessions_of(self, workspace: str) -> dict[str, Session]: ...

    # Every attached browser's id to its session's key.
    def owners(self) -> dict[str, str]: ...

    # Delete a workspace's record and every session's.
    def forget_workspace(self, workspace: str) -> None: ...


class _Writes:
    """`update` and `change`, built on the `upsert` each store implements, so
    every write runs through it, retries included; and the lifetime every
    write gives its key, the same in both stores (WS-87)."""

    ttl: int
    sub_ttl: int
    kept: Callable[[str], bool]

    def get(self, key: str) -> Record | None:
        raise NotImplementedError

    def upsert(self, key: str, fn: Create) -> Record | None:
        raise NotImplementedError

    def update(self, key: str, fn: Change) -> Record | None:
        return self.upsert(key, lambda r: fn(r) if r is not None else None)

    def change(
        self, key: str, fn: Noted, *, create: bool = False
    ) -> tuple[Record | None, Any]:
        note: list = [None]

        def run(r: Record | None) -> Record | None:
            # Every run overwrites the note, so a retry answers with its last.
            changed, note[0] = fn(r)
            return changed

        stored = (self.upsert if create else self.update)(key, run)
        if stored is None and not create:
            # Absent when the write landed: `update` skipped the last run, so
            # any note is from a run that met a record since gone.
            return None, None
        return stored, note[0]

    def _lifetime(self, key: str) -> int | None:
        """Seconds ``key``'s record lasts from a write, None for no expiry: a
        workspace's, and its head's, last ``ttl``, or for ever when it is kept;
        every other session's ``sub_ttl`` (WS-87). A session that has become
        head takes the head's at its next write."""
        workspace, name = session_keys.split(key)
        shared = self.get(workspace) if name else None
        if not name or (shared is not None and shared.head == name):
            return None if self.kept(workspace) else self.ttl
        return self.sub_ttl

    @staticmethod
    def _head_key(key: str, record: Record | None) -> str | None:
        """The key of the head of ``record``'s list when ``key`` is a
        workspace's: a write of it slides the head's too, so the head never
        lapses while another session is in use (WS-87). None otherwise."""
        if session_keys.split(key)[1] or not isinstance(record, Workspace):
            return None
        head = record.head
        return session_keys.key_of(key, head) if head else None


class MemoryStore(_Writes):
    """Process-local mapping. Correct for a single replica, lost on restart.

    Expiry is enforced here as well as in Redis so that ``WORKSPACE_TTL`` means
    the same thing in both modes and a test can prove it without a server.
    """

    kind = "memory"

    def __init__(
        self,
        ttl: int = DEFAULT_TTL_SECONDS,
        sub_ttl: int = DEFAULT_SUB_TTL_SECONDS,
        clock=time.time,
    ):
        self._data: dict[str, tuple[float, Record]] = {}
        self._ttl = ttl
        self._sub_ttl = sub_ttl
        self._clock = clock
        self.kept: Callable[[str], bool] = _never
        # One lock per key, held only while someone is using it, so a workspace
        # named once does not keep a lock for the life of the process.
        self._guard = threading.Lock()
        self._locks: dict[str, list] = {}

    @property
    def ttl(self) -> int:
        """How long an entry is kept, so anything derived from this store
        (`pointer.matching`) keeps the same retention."""
        return self._ttl

    @property
    def sub_ttl(self) -> int:
        return self._sub_ttl

    def get(self, key: str) -> Record | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, record = entry
        if self._clock() >= expires_at:
            del self._data[key]
            return None
        return record

    def get_many(self, keys: list[str]) -> list[Record | None]:
        return [self.get(key) for key in keys]

    @contextmanager
    def _locked(self, key: str) -> Iterator[None]:
        with self._guard:
            held = self._locks.setdefault(key, [threading.Lock(), 0])
            held[1] += 1
        try:
            with held[0]:
                yield
        finally:
            with self._guard:
                held[1] -= 1
                if not held[1]:
                    del self._locks[key]

    def _deadline(self, key: str) -> float:
        lifetime = self._lifetime(key)
        return math.inf if lifetime is None else self._clock() + lifetime

    def _slide(self, key: str, deadline: float) -> None:
        """Give ``key``'s live record ``deadline``, making none. Under the
        key's own lock, so a write landing on it is never set back; taken
        inside a workspace's, never the other way round."""
        with self._locked(key):
            current = self.get(key)
            if current is not None:
                self._data[key] = (deadline, current)

    def _written(self, key: str, record: Record, deadline: float) -> None:
        """Store ``record`` under ``key``, held under its lock, and slide the
        head's key with a workspace's (`_head_key`): the head's lifetime is
        the workspace's."""
        self._data[key] = (deadline, record)
        head = self._head_key(key, record)
        if head is not None:
            self._slide(head, deadline)

    def set(self, key: str, record: Record) -> None:
        deadline = self._deadline(key)
        # Under the key's lock, so a plain write cannot land inside an update.
        with self._locked(key):
            self._written(key, record, deadline)

    def upsert(self, key: str, fn: Create) -> Record | None:
        deadline = self._deadline(key)
        with self._locked(key):
            current = self.get(key)
            changed = fn(current)
            if changed is None:
                return current
            self._written(key, changed, deadline)
            return changed

    def delete(self, key: str) -> None:
        with self._locked(key):
            self._data.pop(key, None)

    def retain(self, workspace: str) -> None:
        deadline = self._deadline(workspace)
        with self._locked(workspace):
            current = self.get(workspace)
            if current is not None:
                self._written(workspace, current, deadline)

    def _live(self) -> dict[str, Record]:
        """Every live record, sessions' too.

        Purges as it goes: ``get`` expires only the key it was handed, so this
        is what collects an entry nobody asks for again.
        """
        now = self._clock()
        live = {}
        for key, (expires_at, record) in list(self._data.items()):
            if now < expires_at:
                live[key] = record
            else:
                del self._data[key]
        return live

    def records(self) -> dict[str, Workspace]:
        """Every live workspace, keyed the way it is stored."""
        return _workspaces(self._live())

    def sessions_of(self, workspace: str) -> dict[str, Session]:
        return _sessions_of(self._live(), workspace)

    def owners(self) -> dict[str, str]:
        """session id -> the session key holding it, for the browsers attached."""
        return _owners(self._live())

    def forget_workspace(self, workspace: str) -> None:
        for name in self.sessions_of(workspace):
            self.delete(session_keys.key_of(workspace, name))
        self.delete(workspace)


class RedisStore(_Writes):
    """Shared mapping, so any replica resolves the same key. Entries expire
    natively."""

    kind = "redis"

    def __init__(
        self,
        client,
        prefix: str = DEFAULT_PREFIX,
        ttl: int = DEFAULT_TTL_SECONDS,
        sub_ttl: int = DEFAULT_SUB_TTL_SECONDS,
    ):
        # Readable (`client`, `prefix`), so anything that shares this backend
        # (`pointer.matching`) is built from this object, never from a second
        # reading of the environment.
        self._redis = client
        self._prefix = prefix
        self._ttl = ttl
        self._sub_ttl = sub_ttl
        self.kept: Callable[[str], bool] = _never

    @property
    def sub_ttl(self) -> int:
        return self._sub_ttl

    @property
    def client(self):
        """The connected Redis client this store writes through."""
        return self._redis

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def ttl(self) -> int:
        return self._ttl

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Record | None:
        value = self._redis.get(self._k(key))
        if value is None:
            return None
        return _parsed(key, value)

    def get_many(self, keys: list[str]) -> list[Record | None]:
        """One MGET; a key that is absent or does not read is None."""
        if not keys:
            return []
        values = self._redis.mget([self._k(key) for key in keys])
        return [
            _parsed(key, value) if value is not None else None
            for key, value in zip(keys, values, strict=True)
        ]

    def _ex(self, key: str) -> int | None:
        # A SET without EX also drops the expiry the key had.
        return self._lifetime(key)

    @staticmethod
    def _expiry(target, key: str, ex: int | None) -> None:
        """EXPIRE ``key`` to ``ex``, or PERSIST it for None: both no-ops on
        an absent key, so neither makes one."""
        if ex is None:
            target.persist(key)
        else:
            target.expire(key, ex)

    def _write(self, pipe, key: str, record: Record, ex: int | None) -> None:
        """Queue the write of ``record`` on ``pipe``, inside its MULTI, and a
        workspace's head's expiry with it (`_head_key`): the head's lifetime is
        the workspace's."""
        pipe.set(self._k(key), record.to_json(), ex=ex)
        head = self._head_key(key, record)
        if head is not None:
            self._expiry(pipe, self._k(head), ex)

    def set(self, key: str, record: Record) -> None:
        ex = self._ex(key)
        with self._redis.pipeline() as pipe:
            pipe.multi()
            self._write(pipe, key, record, ex)
            pipe.execute()

    def upsert(self, key: str, fn: Create) -> Record | None:
        """WATCH, read, MULTI, write: EXEC refuses if another replica wrote the
        key in between, and the change is re-applied to what it wrote. An
        absent key is watched the same way, so two first writes cannot both
        land."""
        from redis.exceptions import WatchError  # optional dependency, as above

        k = self._k(key)
        ex = self._ex(key)
        with self._redis.pipeline() as pipe:
            for _ in range(UPDATE_RETRIES):
                try:
                    pipe.watch(k)
                    raw = pipe.get(k)
                    current = _parsed(key, raw) if raw is not None else None
                    changed = fn(current)
                    if changed is None:
                        pipe.unwatch()
                        return current
                    pipe.multi()
                    self._write(pipe, key, changed, ex)
                    pipe.execute()
                    return changed
                except WatchError:
                    continue
        raise StoreConflict(
            # `logged`: a session's key names it, and an error text is logged.
            f"workspace {session_keys.logged(key)} kept changing while it was "
            f"being updated; gave up after {UPDATE_RETRIES} tries"
        )

    def delete(self, key: str) -> None:
        self._redis.delete(self._k(key))

    def retain(self, workspace: str) -> None:
        ex = self._ex(workspace)
        self._expiry(self._redis, self._k(workspace), ex)
        head = self._head_key(workspace, self.get(workspace))
        if head is not None:
            self._expiry(self._redis, self._k(head), ex)

    def _live(self, match: str = "*") -> dict[str, Record]:
        """Every live record under ``match``, sessions' too.

        SCAN rather than KEYS: this runs on a database shared with other
        services, and KEYS would block the server while it walked all of it.
        """
        keys = []
        for raw in self._redis.scan_iter(match=f"{self._prefix}{match}", count=100):
            key = raw.decode() if isinstance(raw, bytes) else raw
            skipped = (self._prefix + POINTER_NAMESPACE, self._prefix + TABS_NAMESPACE)
            if not key.startswith(skipped):
                keys.append(key)
        if not keys:
            return {}
        # One MGET: this runs on every poll of every open admin page. A key
        # that expired since the SCAN comes back None and is skipped.
        found: dict[str, Record] = {}
        for key, value in zip(keys, self._redis.mget(keys), strict=True):
            name = key[len(self._prefix) :]
            record = _parsed(name, value or b"")
            if record:
                found[name] = record
        return found

    def records(self) -> dict[str, Workspace]:
        """Every live workspace, keyed the way it is stored."""
        return _workspaces(self._live())

    def sessions_of(self, workspace: str) -> dict[str, Session]:
        return _sessions_of(
            self._live(f"{workspace}{session_keys.SEP}*"), workspace
        )

    def owners(self) -> dict[str, str]:
        """session id -> the session key holding it, for the browsers attached."""
        return _owners(self._live())

    def forget_workspace(self, workspace: str) -> None:
        for name in self.sessions_of(workspace):
            self.delete(session_keys.key_of(workspace, name))
        self.delete(workspace)


def _workspaces(live: dict[str, Record]) -> dict[str, Workspace]:
    """The workspaces' records out of ``live``."""
    return {k: r for k, r in live.items() if isinstance(r, Workspace)}


def _sessions_of(live: dict[str, Record], workspace: str) -> dict[str, Session]:
    """``workspace``'s session records out of ``live``, by name."""
    found = {}
    for key, record in live.items():
        owner, name = session_keys.split(key)
        if name and owner == workspace and isinstance(record, Session):
            found[name] = record
    return found


def _owners(live: dict[str, Record]) -> dict[str, str]:
    """Each attached browser's id out of ``live``, to its session's key."""
    return {
        r.session_id: k
        for k, r in live.items()
        if isinstance(r, Session) and r.attached
    }


def from_settings(
    workspace: WorkspaceSettings,
    conn: RedisSettings,
    session: SessionSettings | None = None,
) -> WorkspaceStore:
    """Build the workspace store the config asks for.

    Redis configured and unreachable, or missing its package, is a startup
    error, never a silent step down to memory. The config refuses an unknown
    backend at load.
    """
    sub_ttl = session.sub_ttl if session else DEFAULT_SUB_TTL_SECONDS
    if workspace.store == "memory":
        log.info("workspace store: memory, ttl %ss", workspace.ttl)
        return MemoryStore(ttl=workspace.ttl, sub_ttl=sub_ttl)
    client = redis_client(conn)  # raises StoreUnavailable rather than returning None
    log.info(
        "workspace store: redis db %s, prefix %s, ttl %ss",
        conn.db, conn.prefix, workspace.ttl,
    )
    return RedisStore(
        client, prefix=conn.prefix, ttl=workspace.ttl, sub_ttl=sub_ttl
    )


def redis_client(conn: RedisSettings):
    """A connected Redis client, or raises ``StoreUnavailable``.

    ``where`` never carries the connection's password, and the driver's
    exception text goes through ``faults.message``. Both raises are ``from
    None``: chaining the driver exception would put its unscrubbed ``str()``,
    URL and userinfo included, back into any traceback printed for this one.
    """
    url = conn.url.get_secret_value() if conn.url else None
    where = (
        without_userinfo(url) if url else f"{conn.host}:{conn.port}/{conn.db}"
    )
    try:
        import redis  # imported here: an optional dependency must not be a hard import
    except ImportError:
        raise StoreUnavailable(
            "Redis is configured but the redis package is missing; "
            "pip install kubed-selenium-flow[redis]"
        ) from None
    try:
        if url:
            # Passed explicitly, so redis.db is honoured with no /<index> in the URL.
            client = redis.Redis.from_url(url, db=conn.db)
        else:
            client = redis.Redis(
                host=conn.host,
                port=conn.port,
                db=conn.db,
                password=conn.password.get_secret_value() if conn.password else None,
                username=conn.username or None,
                ssl=conn.ssl,
            )
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure here is Redis's, not this package's
        raise StoreUnavailable(
            f"Redis is configured but unreachable at {where} "
            f"({type(exc).__name__}: {faults.message(exc)}); "
            "refusing to start on in-memory workspaces"
        ) from None
    return client
