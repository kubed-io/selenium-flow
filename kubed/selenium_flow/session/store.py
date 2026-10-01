"""Where a flow session is kept: a caller key to session-record map.

Backs the session manager in ``sessions.py``, and nothing else. It stores a
small record per caller — never a browser, which lives on the Grid.

**A flow session is the thing, and a browser is something it may or may not
have.** The record outlives the browser deliberately: a session whose browser
was reaped, or ended from the admin UI, keeps the browser choice and the page it
was on, so the next ``open_session`` can pick up where it left off instead of
starting from the server's defaults. ``session_id`` is empty when detached.

Nothing here expires a *browser*. Selenium Grid already does that: a session
idle past ``SE_NODE_SESSION_TIMEOUT`` is reaped by the node that owns it, so an
abandoned browser cleans itself up with no scheduler on this side. What these
backends expire is the *mapping*, which is a cache and is allowed to be wrong —
``sessions.py`` validates a record against the Grid before trusting it.

Both backends honour ``ttl`` so that swapping one for the other cannot change
behaviour. Redis does it natively with ``EX``; memory keeps an expiry stamp and
treats a lapsed entry as absent.

Selection is explicit via ``session.store`` in the config. Left unset it infers
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
from typing import TYPE_CHECKING, Protocol

from .. import errors
from ..core.site_data import origin_of

if TYPE_CHECKING:
    from ..config import RedisSettings, SessionSettings

log = logging.getLogger(__name__)

# Every key is written under this prefix, which is what makes sharing a database
# with other applications safe.
DEFAULT_PREFIX = "selenium-flow:session:"
# The pointer store writes under the session prefix too, in this sub-namespace.
# No caller key has this shape, so it is never a session — but a SCAN of the
# prefix finds it, and `records()` reading one as a record emptied the admin
# list the moment any browser had been hovered.
POINTER_NAMESPACE = "pointer:"
# Redis's own default. Deliberately not a guess about the deployment: an install
# with an index convention passes REDIS_DB, and the prefix keeps it safe if not.
DEFAULT_DB = 0
# How long a flow session is kept after it was last used. A day, because these
# are now the history the admin view shows rather than a short-lived cache: a
# named session in daily use never expires, one abandoned yesterday is gone.
# The browser it names is still reaped on the Grid's schedule, not this one.
DEFAULT_TTL_SECONDS = 86400
# How many times a Redis update re-reads after another writer got there first.
# Each retry is one round trip, and a key rewritten this often in the time one
# takes is something wrong rather than something to wait out.
UPDATE_RETRIES = 10
# How many origins a session's history keeps; the oldest goes first.
HISTORY_CAP = 100

# What `update` applies: the record as it is now in, the record to store out,
# or None to store nothing.
Change = Callable[["SessionRecord"], "SessionRecord | None"]
# For `upsert`: the record as it is, or None when the key is absent.
Create = Callable[["SessionRecord | None"], "SessionRecord | None"]


class StoreUnavailable(RuntimeError):
    """Redis was asked for and cannot be used (§F4.12).

    Raised by ``redis_client`` and left to propagate out of ``from_settings``,
    rather than caught and downgraded to :class:`MemoryStore`. A pod that
    refuses to start is restarted by Kubernetes until Redis answers; a pod
    that started anyway, on the wrong store, is never corrected — the live
    server once ran three days on in-memory sessions because Redis was
    refusing connections at boot, with nothing but a log line saying so.
    Memory is the answer only when nothing asked for Redis in the first
    place.
    """


class StoreConflict(RuntimeError):
    """An update kept losing to other writers and gave up (Redis only)."""


@dataclass(frozen=True)
class SessionRecord:
    """One flow session: its context, and the browser it currently holds.

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
    # What the session was opened with, so a reopen uses the same browser
    # rather than a default one.
    settings: dict = field(default_factory=dict)
    # Where the session has been: one {"origin", "url", "at"} per origin,
    # newest first. Written by `at`; the admin History tab reads it.
    history: list = field(default_factory=list)
    # Cookies and storage the session saved (core/site_data.py). Kept with the
    # record so it expires with it; never on this server's disk (with Redis,
    # as durable as Redis).
    site_data: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        """The page the session is on: the top of its history, or ""."""
        return self.history[0]["url"] if self.history else ""

    @property
    def attached(self) -> bool:
        """Whether a browser is currently held."""
        return bool(self.session_id)

    @property
    def window(self) -> str | None:
        """The window size this session is set to, as ``WxH``.

        Kept current by ``resize``, so it is both the size the browser is now
        and the size it would come back as if the Grid reaped it.

        None when the session never named one, which is a real answer rather
        than a missing value: the window is whatever the Grid node's default
        happens to be, and printing a number here would claim we knew which.
        """
        width, height = self.settings.get("width"), self.settings.get("height")
        return f"{width}x{height}" if width and height else None

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | bytes) -> SessionRecord | None:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                # Valid JSON that is not a record — a pointer's `[x, y]` — is
                # a miss too. `.get` on it raised AttributeError, which the
                # clause below does not catch.
                return None
            settings = data.get("settings")
            site_data = data.get("site_data")
            return cls(
                session_id=str(data.get("session_id") or ""),
                opened_at=float(data.get("opened_at", 0.0)),
                settings=settings if isinstance(settings, dict) else {},
                history=_visits(data.get("history")),
                site_data=site_data if isinstance(site_data, dict) else {},
            )
        except (ValueError, TypeError):
            # A malformed entry is a cache miss, not an outage.
            return None

    def at(
        self,
        *urls: str | None,
        now: float | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> SessionRecord:
        """The same record, having landed on ``urls`` in order.

        Each one with an origin moves that origin to the top with its URL and
        the time. One without — None for a URL withheld after a secret write
        (§F1.24), ``about:blank``, ``data:`` — records nothing. Entries older
        than ``ttl`` go, except the top one: it is where a reopen goes back to.
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
        kept = history[:1] + [v for v in history[1:] if v["at"] >= now - ttl]
        return replace(self, history=kept[:HISTORY_CAP])

    def reshaped(self, settings: dict) -> SessionRecord:
        """The same record, with some of its settings replaced.

        A merge rather than a swap: the caller names only the settings it just
        changed, and the ones it does not mention — the browser, the timeouts —
        have to survive or a reopen would replay a browser nobody asked for.
        """
        return replace(self, settings={**self.settings, **settings})

    def with_site_data(self, site_data: dict) -> SessionRecord:
        """The same record holding this site data."""
        return replace(self, site_data=dict(site_data or {}))

    def detached(self) -> SessionRecord:
        """The same record with no browser, keeping the context it had.

        Not a delete: the browser choice and the last page are what the next
        open is meant to inherit, so ending a browser must not take them.
        """
        return replace(self, session_id="")


def _visits(raw) -> list[dict]:
    """The well-formed entries of a stored history. A record written before
    there was one has none: it reads as a session that has been nowhere."""
    if not isinstance(raw, list):
        return []
    return [
        {"origin": v["origin"], "url": v["url"], "at": float(v["at"])}
        for v in raw
        if isinstance(v, dict)
        and isinstance(v.get("origin"), str) and v["origin"]
        and isinstance(v.get("url"), str) and v["url"]
        and isinstance(v.get("at"), (int, float)) and not isinstance(v["at"], bool)
    ]


class SessionStore(Protocol):
    """Maps a caller key to the browser session it is using."""

    kind: str
    # How long an entry is kept. Part of the contract because things derived
    # from a store have to keep its retention - `pointer.matching` is the one
    # that does - and a store that answered a different question about how long
    # anything lives would be two retention policies wearing one name.
    ttl: int

    def get(self, key: str) -> SessionRecord | None: ...

    def set(self, key: str, record: SessionRecord) -> None: ...

    # A read-change-write that cannot revert a concurrent write. `set` stores
    # a whole record, so a caller that read one, waited on the Grid, and set it
    # back undid whatever opened, ended or saved in between. `fn` gets the
    # record as it is at write time and returns the one to store, or None to
    # store nothing; it may run more than once, so it must only compute. It is
    # never called for an absent key. Returns what is stored afterwards.
    def update(self, key: str, fn: Change) -> SessionRecord | None: ...

    # `update`, and an absent key too: `fn` gets None, so a first write can
    # never land between a read and a fallback `set` (Copilot, #50).
    def upsert(self, key: str, fn: Create) -> SessionRecord | None: ...

    def delete(self, key: str) -> None: ...

    # Optional, and only the admin view needs it: the MCP surface never lists
    # sessions, because a client may only ever see its own. A store that cannot
    # enumerate cheaply may leave these out, and the admin view shows an empty
    # list rather than failing.
    def records(self) -> dict[str, SessionRecord]: ...

    def owners(self) -> dict[str, str]: ...


class MemoryStore:
    """Process-local mapping. Correct for a single replica, lost on restart.

    Expiry is enforced here as well as in Redis so that ``SESSION_TTL`` means
    the same thing in both modes and a test can prove it without a server.
    """

    kind = "memory"

    def __init__(self, ttl: int = DEFAULT_TTL_SECONDS, clock=time.time):
        self._data: dict[str, tuple[float, SessionRecord]] = {}
        self._ttl = ttl
        self._clock = clock
        # One lock per key, held only while someone is using it, so a session
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

    def get(self, key: str) -> SessionRecord | None:
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

    def set(self, key: str, record: SessionRecord) -> None:
        # Under the key's lock, so a plain write cannot land inside an update.
        with self._locked(key):
            self._data[key] = (self._clock() + self._ttl, record)

    def update(self, key: str, fn: Change) -> SessionRecord | None:
        return self.upsert(key, lambda r: fn(r) if r is not None else None)

    def upsert(self, key: str, fn: Create) -> SessionRecord | None:
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

    def records(self) -> dict[str, SessionRecord]:
        """Every live flow session, keyed the way it is stored.

        Purges as it goes, which is the only thing that ever collects an entry
        nobody asks for again. ``get`` expires the one key it was handed, so a
        caller that names a session once and never returns — a script run from a
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


class RedisStore:
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

    def get(self, key: str) -> SessionRecord | None:
        value = self._redis.get(self._k(key))
        if value is None:
            return None
        return SessionRecord.from_json(value)

    def set(self, key: str, record: SessionRecord) -> None:
        self._redis.set(self._k(key), record.to_json(), ex=self._ttl)

    def update(self, key: str, fn: Change) -> SessionRecord | None:
        return self.upsert(key, lambda r: fn(r) if r is not None else None)

    def upsert(self, key: str, fn: Create) -> SessionRecord | None:
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
                    current = SessionRecord.from_json(raw) if raw is not None else None
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
            f"session {key!r} kept changing while it was being updated; "
            f"gave up after {UPDATE_RETRIES} tries"
        )

    def delete(self, key: str) -> None:
        self._redis.delete(self._k(key))

    def records(self) -> dict[str, SessionRecord]:
        """Every live flow session, keyed the way it is stored.

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
        found: dict[str, SessionRecord] = {}
        for key, value in zip(keys, self._redis.mget(keys), strict=True):
            record = SessionRecord.from_json(value or b"")
            if record:
                found[key[len(self._prefix) :]] = record
        return found

    def owners(self) -> dict[str, str]:
        """session id -> the caller key holding it, for the sessions attached."""
        return {r.session_id: k for k, r in self.records().items() if r.attached}


def from_settings(session: SessionSettings, conn: RedisSettings) -> SessionStore:
    """Build the session store the config asks for.

    Redis configured and unreachable, or missing its package, is a startup
    error rather than a silent step down to memory (§F4.12). An unknown
    backend no longer reaches here: the config refuses it at load.
    """
    if session.store == "memory":
        log.info("session store: memory, ttl %ss", session.ttl)
        return MemoryStore(ttl=session.ttl)
    client = redis_client(conn)  # raises StoreUnavailable rather than returning None
    log.info(
        "session store: redis db %s, prefix %s, ttl %ss",
        conn.db, conn.prefix, session.ttl,
    )
    return RedisStore(client, prefix=conn.prefix, ttl=session.ttl)


def redis_client(conn: RedisSettings):
    """A connected Redis client, or raises ``StoreUnavailable``. See §F4.12.

    Separate from `from_settings` because the session record is no longer the
    only thing worth sharing between replicas — `pointer.py` keeps the
    pointer's position the same way. Two copies of this connection cascade is
    two places to forget the database index, and the second one would be the
    one nobody tests.

    Raises rather than logging and returning None (§F4.12): a caller configured
    for Redis that gets nothing back must not quietly keep going on a mapping
    that resolves locally. ``where`` never carries the connection's password —
    neither in this message nor in the underlying exception's, which is routed
    through ``errors.message`` for the same scrubbing HTTP errors get. Both
    raises are ``from None``: chaining the raw driver exception would put its
    unscrubbed ``str()`` — URL, userinfo included — back into any traceback
    printed for this one.
    """
    url = conn.url.get_secret_value() if conn.url else None
    where = (
        errors.without_userinfo(url) if url else f"{conn.host}:{conn.port}/{conn.db}"
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
            f"({type(exc).__name__}: {errors.message(exc)}); "
            "refusing to start on in-memory sessions"
        ) from None
    return client
