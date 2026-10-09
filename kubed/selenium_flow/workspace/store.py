"""Where a workspace is kept: a caller key to workspace-record map.

Backs the workspace manager in ``workspaces.py``, and nothing else. It stores a
small record per caller — never a browser, which lives on the Grid.

**A workspace is the thing, and a browser is something it may or may not
have.** The record outlives the browser deliberately: a workspace whose browser
was reaped, or ended from the admin UI, keeps the browser choice and the page it
was on, so the next ``open_session`` can pick up where it left off instead of
starting from the server's defaults. ``session_id`` is empty when detached.

Nothing here expires a *browser*. Selenium Grid already does that: a session
idle past ``SE_NODE_SESSION_TIMEOUT`` is reaped by the node that owns it, so an
abandoned browser cleans itself up with no scheduler on this side. What these
backends expire is the *mapping*, which is a cache and is allowed to be wrong —
``workspaces.py`` validates a record against the Grid before trusting it.

Both backends honour ``ttl`` so that swapping one for the other cannot change
behaviour. Redis does it natively with ``EX``; memory keeps an expiry stamp and
treats a lapsed entry as absent.

Selection is explicit via ``workspace.store`` in the config. Left unset it infers
redis from ``redis.host`` or ``redis.url`` being set, so an existing deployment
keeps working.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from .. import faults
from ..urls import origin_of, page_of, without_userinfo

if TYPE_CHECKING:
    from ..config import RedisSettings, WorkspaceSettings

log = logging.getLogger(__name__)

# Every key is written under this prefix, which is what makes sharing a database
# with other applications safe.
DEFAULT_PREFIX = "selenium-flow:workspace:"
# The pointer store writes under the workspace prefix too, in this sub-namespace.
# No caller key has this shape, so it is never a workspace — but a SCAN of the
# prefix finds it, and `records()` reading one as a record emptied the admin
# list the moment any browser had been hovered.
POINTER_NAMESPACE = "pointer:"
# Redis's own default. Deliberately not a guess about the deployment: an install
# with an index convention passes REDIS_DB, and the prefix keeps it safe if not.
DEFAULT_DB = 0
# How long a workspace is kept after it was last used. A day, because these
# are now the history the admin view shows rather than a short-lived cache: a
# named workspace in daily use never expires, one abandoned yesterday is gone.
# The browser it names is still reaped on the Grid's schedule, not this one.
DEFAULT_TTL_SECONDS = 86400
# How many times a Redis update re-reads after another writer got there first.
# Each retry is one round trip, and a key rewritten this often in the time one
# takes is something wrong rather than something to wait out.
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
    """Redis was asked for and cannot be used (§F4.12).

    Raised by ``redis_client`` and left to propagate out of ``from_settings``,
    rather than caught and downgraded to :class:`MemoryStore`. A pod that
    refuses to start is restarted by Kubernetes until Redis answers; a pod
    that started anyway, on the wrong store, is never corrected — the live
    server once ran three days on an in-memory store because Redis was
    refusing connections at boot, with nothing but a log line saying so.
    Memory is the answer only when nothing asked for Redis in the first
    place.
    """


class StoreConflict(RuntimeError):
    """An update kept losing to other writers and gave up (Redis only)."""


@dataclass(frozen=True)
class Workspace:
    """One workspace: its context, and the browser it currently holds.

    ``history`` and ``settings`` are the point of storing a record rather than
    a bare id. They are what makes a browser replaceable: reopening and
    navigating back to the last known page — the top of the history — in the
    browser it was using makes a refresh invisible, and makes
    ``open_session`` with no arguments do the obvious thing after the browser
    has gone.

    ``session_id`` is empty when no browser is attached, which is an ordinary
    state rather than a broken one: the Grid reaped it, or an admin ended it.
    """

    session_id: str = ""
    opened_at: float = 0.0
    # What the workspace was opened with, so a reopen uses the same browser
    # rather than a default one.
    settings: dict = field(default_factory=dict)
    # Where the workspace has been: one {"origin", "url", "at"} per origin,
    # newest first. Written by `visited`; the admin History tab reads it.
    history: list = field(default_factory=list)
    # Cookies and storage the workspace saved (site_data/). Kept with the
    # record so it expires with it; never on this server's disk (with Redis,
    # as durable as Redis).
    site_data: dict = field(default_factory=dict)
    # A silent reopen's site data report, held until the first result from
    # that browser carries it: {"browser": id, "report": {...}}, or {}.
    reopened: dict = field(default_factory=dict)
    # Who opened the browser this workspace holds — {"kind", "username"} — for
    # the admin list. Shown, never consulted (spec 2026-10-09-admin-oidc).
    opened_by: dict | None = None

    @property
    def url(self) -> str:
        """The page the workspace is on: the top of its history, or ""."""
        return self.history[0]["url"] if self.history else ""

    @property
    def attached(self) -> bool:
        """Whether a browser is currently held."""
        return bool(self.session_id)

    @property
    def window(self) -> str | None:
        """The window size this workspace is set to, as ``WxH``.

        Kept current by ``resize``, so it is both the size the browser is now
        and the size it would come back as if the Grid reaped it.

        None when the workspace never named one, which is a real answer rather
        than a missing value: the window is whatever the Grid node's default
        happens to be, and printing a number here would claim we knew which.
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
                # Valid JSON that is not a record — a pointer's `[x, y]` — is
                # a miss too. `.get` on it raised AttributeError, which the
                # clause below does not catch.
                return None
            settings = data.get("settings")
            site_data = data.get("site_data")
            reopened = data.get("reopened")
            return cls(
                session_id=str(data.get("session_id") or ""),
                opened_at=float(data.get("opened_at", 0.0)),
                settings=settings if isinstance(settings, dict) else {},
                history=_visits(data.get("history")),
                site_data=site_data if isinstance(site_data, dict) else {},
                reopened=reopened if isinstance(reopened, dict) else {},
                opened_by=_opener(data.get("opened_by")),
            )
        except (ValueError, TypeError):
            # A malformed entry is a cache miss, not an outage.
            return None

    def visited(
        self,
        *urls: str | None,
        now: float | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> Workspace:
        """The same record, having landed on ``urls`` in order, then pruned.

        Each one with an origin moves that origin to the top with its URL and
        the time. One without — None for a URL withheld after a secret write
        (§F1.24), ``about:blank``, ``data:`` — records nothing.
        """
        now = time.time() if now is None else now
        history = list(self.history)
        for url in urls:
            origin = origin_of(url or "")
            if origin:
                history = [
                    {"origin": origin, "url": url, "at": now},
                    *(v for v in history if v["origin"] != origin),
                ]
        return replace(self, history=history).pruned(now, ttl)

    def pruned(self, now: float, ttl: float) -> Workspace:
        """The same record without the history entries older than ``ttl``,
        except the top one: it is where a reopen goes back to.

        Only the top entry keeps its whole URL. Below it a URL keeps its origin
        and path: a query string or fragment carries OAuth codes and reset
        tokens, and nothing but a reopen needs them (Copilot, #51).
        """
        history = self.history
        kept = history[:1] + [
            {**v, "url": page_of(v["url"])} for v in history[1:] if v["at"] >= now - ttl
        ]
        return replace(self, history=kept[:HISTORY_CAP])

    def reshaped(self, settings: dict) -> Workspace:
        """The same record, with some of its settings replaced.

        A merge rather than a swap: the caller names only the settings it just
        changed, and the ones it does not mention — the browser, the timeouts —
        have to survive or a reopen would replay a browser nobody asked for.
        """
        return replace(self, settings={**self.settings, **settings})

    def with_site_data(self, site_data: dict) -> Workspace:
        """The same record holding this site data."""
        return replace(self, site_data=dict(site_data or {}))

    def history_cleared(self) -> Workspace:
        """The same record with only its current page left in the history."""
        return replace(self, history=self.history[:1])

    def delivered(self) -> Workspace:
        """The same record, its reopen report handed to a caller."""
        return replace(self, reopened={})

    def detached(self) -> Workspace:
        """The same record with no browser, keeping the context it had.

        Not a delete: the browser choice and the last page are what the next
        open is meant to inherit, so ending a browser must not take them.
        """
        return replace(self, session_id="")


def _opener(value) -> dict | None:
    """A stored `opened_by`, or None for anything that is not one."""
    if not isinstance(value, dict) or value.get("kind") not in ("admin", "oidc"):
        return None
    name = value.get("username")
    return {"kind": value["kind"], "username": name if isinstance(name, str) else None}


def _visits(raw) -> list[dict]:
    """The well-formed entries of a stored history. A record written before
    there was one has none: it reads as a workspace that has been nowhere. A URL
    that does not parse is no entry, or every later write would trip on it
    (Copilot, #51)."""
    if not isinstance(raw, list):
        return []
    return [
        {"origin": v["origin"], "url": v["url"], "at": float(v["at"])}
        for v in raw
        if isinstance(v, dict)
        and isinstance(v.get("origin"), str) and v["origin"]
        and isinstance(v.get("url"), str) and origin_of(v["url"])
        and isinstance(v.get("at"), (int, float)) and not isinstance(v["at"], bool)
    ]


class WorkspaceStore(Protocol):
    """Maps a caller key to its workspace: the record of what it was doing, and
    the session (the live browser) it holds, if any."""

    kind: str
    # How long an entry is kept. Part of the contract because things derived
    # from a store have to keep its retention - `pointer.matching` is the one
    # that does - and a store that answered a different question about how long
    # anything lives would be two retention policies wearing one name.
    ttl: int

    def get(self, key: str) -> Workspace | None: ...

    def set(self, key: str, record: Workspace) -> None: ...

    # A read-change-write that cannot revert a concurrent write. `set` stores
    # a whole record, so a caller that read one, waited on the Grid, and set it
    # back undid whatever opened, ended or saved in between. `fn` gets the
    # record as it is at write time and returns the one to store, or None to
    # store nothing; it may run more than once, so it must only compute. It is
    # never called for an absent key. Returns what is stored afterwards.
    def update(self, key: str, fn: Change) -> Workspace | None: ...

    # `update`, and an absent key too: `fn` gets None, so a first write can
    # never land between a read and a fallback `set` (Copilot, #50).
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

    # Optional, and only the admin view needs it: the MCP surface never lists
    # workspaces, because a client may only ever see its own. A store that cannot
    # enumerate cheaply may leave these out, and the admin view shows an empty
    # list rather than failing.
    def records(self) -> dict[str, Workspace]: ...

    def owners(self) -> dict[str, str]: ...


class _Writes:
    """`update` and `change`, built on the `upsert` each store implements.

    Layered rather than side by side, so every write runs through the one a
    store implements, retries included: `update` is `upsert` that never
    creates, and `change` is either of them with a note handed back.
    """

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

    def __init__(self, ttl: int = DEFAULT_TTL_SECONDS, clock=time.time):
        self._data: dict[str, tuple[float, Workspace]] = {}
        self._ttl = ttl
        self._clock = clock
        # One lock per key, held only while someone is using it, so a workspace
        # named once does not keep a lock for the life of the process.
        self._guard = threading.Lock()
        self._locks: dict[str, list] = {}

    @property
    def ttl(self) -> int:
        """How long an entry is kept, so anything derived from this store keeps
        the same retention. `RedisStore` exposes it for the same reason: without
        it, `pointer.matching` fell back to its own default and held a pointer
        for a day on a server configured for minutes (Copilot, #31)."""
        return self._ttl

    def get(self, key: str) -> Workspace | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, record = entry
        if self._clock() >= expires_at:
            del self._data[key]
            return None
        return record

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

    def set(self, key: str, record: Workspace) -> None:
        # Under the key's lock, so a plain write cannot land inside an update.
        with self._locked(key):
            self._data[key] = (self._clock() + self._ttl, record)

    def upsert(self, key: str, fn: Create) -> Workspace | None:
        with self._locked(key):
            current = self.get(key)
            changed = fn(current)
            if changed is None:
                return current
            self._data[key] = (self._clock() + self._ttl, changed)
            return changed

    def delete(self, key: str) -> None:
        with self._locked(key):
            self._data.pop(key, None)

    def records(self) -> dict[str, Workspace]:
        """Every live workspace, keyed the way it is stored.

        Purges as it goes, which is the only thing that ever collects an entry
        nobody asks for again. ``get`` expires the one key it was handed, so a
        caller that names a workspace once and never returns — a script run from a
        shell, a workflow that builds a name per invocation — left a record here
        until the process restarted. Redis has never had this problem: it
        expires entries itself, which is why the leak was invisible in the
        deployment that matters and real in the default one.
        """
        now = self._clock()
        live = {}
        for key, (expires_at, record) in list(self._data.items()):
            if now < expires_at:
                live[key] = record
            else:
                del self._data[key]
        return live

    def owners(self) -> dict[str, str]:
        """session id -> the caller key holding it, for the sessions attached."""
        return {r.session_id: k for k, r in self.records().items() if r.attached}


class RedisStore(_Writes):
    """Shared mapping, so any replica resolves the same key.

    Entries expire natively: a mapping that outlives the browser it names is
    worse than no mapping, and Redis is better at that bookkeeping than we are.
    """

    kind = "redis"

    def __init__(
        self, client, prefix: str = DEFAULT_PREFIX, ttl: int = DEFAULT_TTL_SECONDS
    ):
        # Readable, so anything that must share this backend can be built FROM
        # this object rather than from a second reading of the environment.
        # `pointer.matching` is the one that needs it: deriving it from the env
        # a second time meant an injected store and the pointers could disagree
        # about whether they were shared at all (Copilot, #31).
        self._redis = client
        self._prefix = prefix
        self._ttl = ttl

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

    def set(self, key: str, record: Workspace) -> None:
        self._redis.set(self._k(key), record.to_json(), ex=self._ttl)

    def upsert(self, key: str, fn: Create) -> Workspace | None:
        """WATCH, read, MULTI, write: EXEC refuses if another replica wrote the
        key in between, and the change is re-applied to what it wrote. An
        absent key is watched the same way, so two first writes cannot both
        land."""
        from redis.exceptions import WatchError  # optional dependency, as above

        k = self._k(key)
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
                    pipe.set(k, changed.to_json(), ex=self._ttl)
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

    def records(self) -> dict[str, Workspace]:
        """Every live workspace, keyed the way it is stored.

        SCAN rather than KEYS: this runs on a database shared with other
        services, and KEYS would block the server while it walked all of it.
        """
        keys = []
        for raw in self._redis.scan_iter(match=f"{self._prefix}*", count=100):
            key = raw.decode() if isinstance(raw, bytes) else raw
            if not key.startswith(self._prefix + POINTER_NAMESPACE):
                keys.append(key)
        if not keys:
            return {}
        # One MGET, not a GET per key: this runs on every poll of every open
        # admin page, and each GET was its own round trip (§F4.19). A key that
        # expired since the SCAN comes back None and is skipped.
        found: dict[str, Workspace] = {}
        for key, value in zip(keys, self._redis.mget(keys), strict=True):
            record = Workspace.from_json(value or b"")
            if record:
                found[key[len(self._prefix) :]] = record
        return found

    def owners(self) -> dict[str, str]:
        """session id -> the caller key holding it, for the sessions attached."""
        return {r.session_id: k for k, r in self.records().items() if r.attached}


def from_settings(workspace: WorkspaceSettings, conn: RedisSettings) -> WorkspaceStore:
    """Build the workspace store the config asks for.

    Redis configured and unreachable, or missing its package, is a startup
    error rather than a silent step down to memory (§F4.12). An unknown
    backend no longer reaches here: the config refuses it at load.
    """
    if workspace.store == "memory":
        log.info("workspace store: memory, ttl %ss", workspace.ttl)
        return MemoryStore(ttl=workspace.ttl)
    client = redis_client(conn)  # raises StoreUnavailable rather than returning None
    log.info(
        "workspace store: redis db %s, prefix %s, ttl %ss",
        conn.db, conn.prefix, workspace.ttl,
    )
    return RedisStore(client, prefix=conn.prefix, ttl=workspace.ttl)


def redis_client(conn: RedisSettings):
    """A connected Redis client, or raises ``StoreUnavailable``. See §F4.12.

    Separate from `from_settings` because the workspace record is no longer the
    only thing worth sharing between replicas — `pointer.py` keeps the
    pointer's position the same way. Two copies of this connection cascade is
    two places to forget the database index, and the second one would be the
    one nobody tests.

    Raises rather than logging and returning None (§F4.12): a caller configured
    for Redis that gets nothing back must not quietly keep going on a mapping
    that resolves locally. ``where`` never carries the connection's password —
    neither in this message nor in the underlying exception's, which is routed
    through ``faults.message`` for the same scrubbing HTTP errors get. Both
    raises are ``from None``: chaining the raw driver exception would put its
    unscrubbed ``str()`` — URL, userinfo included — back into any traceback
    printed for this one.
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
