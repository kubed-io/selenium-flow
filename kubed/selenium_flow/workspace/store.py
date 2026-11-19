"""Where a workspace is kept: a caller key to workspace-record map.

Backs the workspace manager in ``workspaces.py``, and nothing else. It stores a
small record per caller, never a browser, which lives on the Grid.

A workspace is the thing, and a browser is something it may or may not have.
The record outlives the browser: a workspace whose browser was reaped, or ended
from the admin UI, keeps the browser choice and the page it was on, so the next
``open_session`` picks up where it left off. ``session_id`` is empty when
detached.

Nothing here expires a browser: a Grid node reaps a session idle past
``SE_NODE_SESSION_TIMEOUT``. What these backends expire is the mapping, which is
a cache and is allowed to be wrong: ``workspaces.py`` checks a record against
the Grid before trusting it.

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
from . import sessions
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
# A sub-session's record is kept this long after its last use.
DEFAULT_SUB_TTL_SECONDS = 3600
# How many times a Redis update re-reads after another writer got there first.
# A key rewritten this often within one round trip is a fault, not contention.
UPDATE_RETRIES = 10
# How many origins a workspace's history keeps; the oldest goes first.
HISTORY_CAP = 100

# What `update` applies: the record as it is now in, the record to store out,
# or None to store nothing.
Change = Callable[["Workspace"], "Workspace | None"]
# For `upsert`: the record as it is, or None when the key is absent.
Create = Callable[["Workspace | None"], "Workspace | None"]
# For `change`: the same, and a note on what the write found, handed back.
Noted = Callable[["Workspace | None"], "tuple[Workspace | None, Any]"]


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
class Workspace:
    """One workspace: its context, and the browser it currently holds.

    ``history`` and ``settings`` make a browser replaceable: reopening the same
    browser on the top of the history makes a refresh invisible, and makes
    ``open_session`` with no arguments carry on after the browser has gone.

    ``session_id`` is empty when no browser is attached, an ordinary state: the
    Grid reaped it, or an admin ended it.
    """

    session_id: str = ""
    opened_at: float = 0.0
    # What the workspace was opened with, so a reopen uses the same browser.
    settings: dict = field(default_factory=dict)
    # Who set each of those settings: "config", "client" or "caller", one per
    # key. A record whose sources are missing or do not match its settings is
    # not read (see `from_json`).
    set_by: dict = field(default_factory=dict)
    # Where the workspace has been: one {"origin", "url", "at", "session"} per
    # origin and session, newest first, every session's on the workspace's
    # record (WS-54). Written by `visited`; the admin History tab reads it.
    history: list = field(default_factory=list)
    # Cookies and storage the workspace saved (site_data/). Kept with the
    # record so it expires with it; never on this server's disk (with Redis,
    # as durable as Redis).
    site_data: dict = field(default_factory=dict)
    # A silent reopen's notice, held until the first result from that browser
    # says it: {"browser": id, "cause": "reaped" | "lost", "report": {...} |
    # None}, or {}.
    reopened: dict = field(default_factory=dict)
    # When this workspace's last call ended, in wall time; 0.0 before any. It
    # is what tells a reap (idle past the window) from a lost browser.
    called_at: float = 0.0
    # When the Grid last answered a keep-alive ping for the held browser, in
    # wall time; 0.0 before any. To the node a ping is activity, so it moves the
    # reap clock like a call does.
    pinged_at: float = 0.0
    # Who opened the browser this workspace holds, {"kind", "username"}, for
    # the admin list. Shown, never consulted.
    opened_by: dict | None = None
    # Seconds the Grid lets this workspace's session sit idle before reaping
    # it, read from its node at the last open. Kept when the session ends;
    # None when the Grid did not say.
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
    # The subject that owns this workspace's default session, or None while no
    # one has claimed it. Written on a workspace's record only.
    owner: str | None = None
    # The names of this workspace's sub-sessions, kept on its record so a listing
    # needs no scan. Written on a workspace's record only.
    subs: list = field(default_factory=list)
    # What a sub-session is shown as: its name, or the token's
    # `preferred_username` for a subject's own (WS-50). Written on a
    # sub-session's record only.
    label: str = ""

    @property
    def url(self) -> str:
        """The page the default session is on: the top of its history, or ""."""
        return self.page(sessions.DEFAULT)

    def page(self, session: str) -> str:
        """The page ``session`` (a name) is on: its newest entry, or ""."""
        return next((v["url"] for v in self.history if _tag(v) == session), "")

    def entries(self, session: str) -> list[dict]:
        """``session``'s own entries, newest first."""
        return [v for v in self.history if _tag(v) == session]

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
        """The window size this workspace is set to, as ``WxH``.

        Kept current by ``resize``, so it is both the size the browser is now
        and the size it would come back as after a reap. None when the
        workspace never named one: the window is the Grid node's default, and a
        number here would claim to know it.
        """
        width, height = self.settings.get("width"), self.settings.get("height")
        return f"{width}x{height}" if width and height else None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | bytes) -> Workspace | None:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                # Valid JSON that is not a record (a pointer's `[x, y]`) is a
                # miss too; `.get` on it would raise AttributeError, which the
                # clause below does not catch.
                return None
            settings = data.get("settings")
            settings = settings if isinstance(settings, dict) else {}
            set_by = _set_by(data.get("set_by"), settings)
            session_id = str(data.get("session_id") or "") if set_by is not None else ""
            site_data = data.get("site_data")
            reopened = data.get("reopened")
            opened_at = data.get("opened_at", 0.0)
            return cls(
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
                history=_visits(data.get("history")),
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
                owner=_owner(data.get("owner")),
                subs=_subs(data.get("subs")),
                label=_label(data.get("label")),
            )
        except (ValueError, TypeError):
            # A malformed entry is a cache miss, not an outage.
            return None

    def visited(
        self,
        *urls: str | None,
        now: float | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
        session: str = sessions.DEFAULT,
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
        where the reopen goes back to. The default's top always stays; a
        sub-session's stays for ``sub_ttl``, as long as its record can live
        unused, and is then an entry like any other.

        Only such a top entry keeps its whole URL. Elsewhere a URL keeps its
        origin, path and hash route (never the route's query): a query string
        or fragment carries OAuth codes and reset tokens, and nothing but a
        reopen needs them.

        At most ``HISTORY_CAP`` entries stay, the oldest dropped first, except
        the default's top.
        """
        kept, tops, default_top = [], set(), None
        for v in self.history:
            session = _tag(v)
            top = session not in tops
            tops.add(session)
            if top and session == sessions.DEFAULT:
                default_top = v
                kept.append(v)
            elif top and v["at"] >= now - sub_ttl:
                kept.append(v)
            elif v["at"] >= now - ttl:
                kept.append({**v, "url": page_of(v["url"])})
        capped = kept[:HISTORY_CAP]
        if default_top is not None and default_top not in capped:
            # The oldest of what is kept, so it goes last.
            capped = [*kept[: HISTORY_CAP - 1], default_top]
        return replace(self, history=capped)

    def reshaped(self, settings: dict) -> Workspace:
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

    def with_site_data(self, site_data: dict) -> Workspace:
        """The same record holding this site data."""
        return replace(self, site_data=dict(site_data or {}))

    def history_cleared(self, live: Iterable[str] = ()) -> Workspace:
        """The same record with only the current page of the default and of
        each sub-session in ``live`` (those that still have a record) left in
        the history: one with no record cannot reopen, so its page is
        cleared too."""
        keep = {sessions.DEFAULT, *live}
        kept, tops = [], set()
        for v in self.history:
            if _tag(v) not in tops and _tag(v) in keep:
                tops.add(_tag(v))
                kept.append(v)
        return replace(self, history=kept)

    def delivered(self) -> Workspace:
        """The same record, its reopen notice handed to a caller."""
        return replace(self, reopened={})

    def detached(self, ended: dict | None = None) -> Workspace:
        """The same record with no browser, keeping the context it had.

        Not a delete: the last page and the settings (with their sources) are
        what the next open reads from, so ending a browser must not take them. What
        the last session sent and ran stay too: the admin's idle card shows the
        last browser. ``ended`` says how it ended.
        """
        return replace(self, session_id="", ended=ended)


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


def _owner(raw) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def _label(raw) -> str:
    return raw if isinstance(raw, str) else ""


def _subs(raw) -> list[str]:
    return [n for n in raw if isinstance(n, str)] if isinstance(raw, list) else []


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
    return visit.get("session", sessions.DEFAULT)


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
    """Maps a caller key to its workspace: the record of what it was doing, and
    the session (the live browser) it holds, if any."""

    kind: str
    # How long an entry is kept: things derived from a store keep its retention
    # (`pointer.matching`).
    ttl: int
    # How long a sub-session's record is kept after its last use.
    sub_ttl: int
    # Whether a key's record is written with no expiry: its workspace has a
    # config (`workspace.config.Configs.kept`). Asked once per write.
    kept: Callable[[str], bool]

    def get(self, key: str) -> Workspace | None: ...

    # `get` of each of ``keys``, in order, in one round trip where the backend
    # allows: a listing of a workspace's sub-sessions reads them all at once.
    def get_many(self, keys: list[str]) -> list[Workspace | None]: ...

    def set(self, key: str, record: Workspace) -> None: ...

    # A read-change-write that cannot revert a concurrent write, as a read, a
    # wait on the Grid and a `set` would. `fn` gets the record as it is at write
    # time and returns the one to store, or None to store nothing; it may run
    # more than once, so it must only compute. It is never called for an absent
    # key. Returns what is stored afterwards.
    def update(self, key: str, fn: Change) -> Workspace | None: ...

    # `update`, and an absent key too: `fn` gets None, so a first write can
    # never land between a read and a fallback `set`.
    def upsert(self, key: str, fn: Create) -> Workspace | None: ...

    # A write that answers: `fn` returns the record to store and a note on
    # what it found there, and the note comes back beside what is stored.
    # Only the last run's note: a run thrown away by a retry saw a record that
    # was never written. `create` is `upsert`; without it an absent key is
    # `(None, None)` and `fn` never runs.
    def change(
        self, key: str, fn: Noted, *, create: bool = False
    ) -> tuple[Workspace | None, Any]: ...

    def delete(self, key: str) -> None: ...

    # Apply the expiry `kept` says now to a key's record, if it has one, without
    # writing the record: a config saved or removed changes it at once.
    def retain(self, key: str) -> None: ...

    # Optional, for the admin view only: the MCP surface never lists workspaces.
    # A store that cannot enumerate cheaply may leave these out, and the admin
    # view shows an empty list.
    # Workspaces only: a sub-session's record is not a workspace.
    def records(self) -> dict[str, Workspace]: ...

    # `workspace`'s sub-sessions by name.
    def sub_records(self, workspace: str) -> dict[str, Workspace]: ...

    # Every attached browser's id to its session key, sub-sessions included.
    def owners(self) -> dict[str, str]: ...

    # Delete a workspace's record and every sub-session's.
    def forget_workspace(self, workspace: str) -> None: ...


class _Writes:
    """`update` and `change`, built on the `upsert` each store implements, so
    every write runs through it, retries included."""

    def upsert(self, key: str, fn: Create) -> Workspace | None:
        raise NotImplementedError

    def update(self, key: str, fn: Change) -> Workspace | None:
        return self.upsert(key, lambda r: fn(r) if r is not None else None)

    def change(
        self, key: str, fn: Noted, *, create: bool = False
    ) -> tuple[Workspace | None, Any]:
        note: list = [None]

        def run(r: Workspace | None) -> Workspace | None:
            # Every run overwrites the note, so a retry answers with its last.
            changed, note[0] = fn(r)
            return changed

        stored = (self.upsert if create else self.update)(key, run)
        if stored is None and not create:
            # Absent when the write landed: `update` skipped the last run, so
            # any note is from a run that met a record since gone.
            return None, None
        return stored, note[0]


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
        self._data: dict[str, tuple[float, Workspace]] = {}
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

    def get(self, key: str) -> Workspace | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, record = entry
        if self._clock() >= expires_at:
            del self._data[key]
            return None
        return record

    def get_many(self, keys: list[str]) -> list[Workspace | None]:
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
        if sessions.is_sub(key):
            # Never kept: a sub-session is a scratch browser, not the
            # workspace's own config.
            return self._clock() + self._sub_ttl
        return math.inf if self.kept(key) else self._clock() + self._ttl

    def set(self, key: str, record: Workspace) -> None:
        deadline = self._deadline(key)
        # Under the key's lock, so a plain write cannot land inside an update.
        with self._locked(key):
            self._data[key] = (deadline, record)

    def upsert(self, key: str, fn: Create) -> Workspace | None:
        deadline = self._deadline(key)
        with self._locked(key):
            current = self.get(key)
            changed = fn(current)
            if changed is None:
                return current
            self._data[key] = (deadline, changed)
            return changed

    def delete(self, key: str) -> None:
        with self._locked(key):
            self._data.pop(key, None)

    def retain(self, key: str) -> None:
        deadline = self._deadline(key)
        with self._locked(key):
            current = self.get(key)
            if current is not None:
                self._data[key] = (deadline, current)

    def _live(self) -> dict[str, Workspace]:
        """Every live record, sub-sessions too.

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
        return {k: r for k, r in self._live().items() if not sessions.is_sub(k)}

    def sub_records(self, workspace: str) -> dict[str, Workspace]:
        return _subs_of(self._live(), workspace)

    def owners(self) -> dict[str, str]:
        """session id -> the session key holding it, for the browsers attached."""
        return {r.session_id: k for k, r in self._live().items() if r.attached}

    def forget_workspace(self, workspace: str) -> None:
        for name in self.sub_records(workspace):
            self.delete(sessions.key_of(workspace, name))
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

    def get(self, key: str) -> Workspace | None:
        value = self._redis.get(self._k(key))
        if value is None:
            return None
        return Workspace.from_json(value)

    def get_many(self, keys: list[str]) -> list[Workspace | None]:
        """One MGET; a key that is absent or does not read is None."""
        if not keys:
            return []
        values = self._redis.mget([self._k(key) for key in keys])
        return [
            Workspace.from_json(value) if value is not None else None
            for value in values
        ]

    def _ex(self, key: str) -> int | None:
        # A SET without EX also drops the expiry the key had.
        if sessions.is_sub(key):
            return self._sub_ttl
        return None if self.kept(key) else self._ttl

    def set(self, key: str, record: Workspace) -> None:
        self._redis.set(self._k(key), record.to_json(), ex=self._ex(key))

    def upsert(self, key: str, fn: Create) -> Workspace | None:
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
                    current = Workspace.from_json(raw) if raw is not None else None
                    changed = fn(current)
                    if changed is None:
                        pipe.unwatch()
                        return current
                    pipe.multi()
                    pipe.set(k, changed.to_json(), ex=ex)
                    pipe.execute()
                    return changed
                except WatchError:
                    continue
        raise StoreConflict(
            f"workspace {key!r} kept changing while it was being updated; "
            f"gave up after {UPDATE_RETRIES} tries"
        )

    def delete(self, key: str) -> None:
        self._redis.delete(self._k(key))

    def retain(self, key: str) -> None:
        # Both are no-ops on an absent key.
        ex = self._ex(key)
        if ex is None:
            self._redis.persist(self._k(key))
        else:
            self._redis.expire(self._k(key), ex)

    def _live(self, match: str = "*") -> dict[str, Workspace]:
        """Every live record under ``match``, sub-sessions too.

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
        found: dict[str, Workspace] = {}
        for key, value in zip(keys, self._redis.mget(keys), strict=True):
            record = Workspace.from_json(value or b"")
            if record:
                found[key[len(self._prefix) :]] = record
        return found

    def records(self) -> dict[str, Workspace]:
        """Every live workspace, keyed the way it is stored."""
        return {k: r for k, r in self._live().items() if not sessions.is_sub(k)}

    def sub_records(self, workspace: str) -> dict[str, Workspace]:
        return _subs_of(self._live(f"{workspace}{sessions.SEP}*"), workspace)

    def owners(self) -> dict[str, str]:
        """session id -> the session key holding it, for the browsers attached."""
        return {r.session_id: k for k, r in self._live().items() if r.attached}

    def forget_workspace(self, workspace: str) -> None:
        for name in self.sub_records(workspace):
            self.delete(sessions.key_of(workspace, name))
        self.delete(workspace)


def _subs_of(live: dict[str, Workspace], workspace: str) -> dict[str, Workspace]:
    """``workspace``'s sub-sessions out of ``live``, by name."""
    found = {}
    for key, record in live.items():
        owner, name = sessions.split(key)
        if sessions.is_sub(key) and owner == workspace:
            found[name] = record
    return found


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
