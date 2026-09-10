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

Selection is explicit via ``SESSION_STORE``. Left unset it infers redis from the
presence of ``REDIS_*``, so an existing deployment keeps working.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Protocol

log = logging.getLogger(__name__)

# Every key is written under this prefix, which is what makes sharing a database
# with other applications safe.
DEFAULT_PREFIX = "selenium-flow:session:"
# Redis's own default. Deliberately not a guess about the deployment: an install
# with an index convention passes REDIS_DB, and the prefix keeps it safe if not.
DEFAULT_DB = 0
# How long a flow session is kept after it was last used. A day, because these
# are now the history the admin view shows rather than a short-lived cache: a
# named session in daily use never expires, one abandoned yesterday is gone.
# The browser it names is still reaped on the Grid's schedule, not this one.
DEFAULT_TTL_SECONDS = 86400


@dataclass(frozen=True)
class SessionRecord:
    """One flow session: its context, and the browser it currently holds.

    ``url`` and ``settings`` are the point of storing a record rather than a
    bare id. They are what makes a browser replaceable: reopening and navigating
    back to the last known page, in the browser it was using, makes a refresh
    invisible — and makes ``open_session`` with no arguments do the obvious
    thing after the browser has gone.

    ``session_id`` is empty when no browser is attached, which is an ordinary
    state rather than a broken one: the Grid reaped it, or an admin ended it.
    """

    session_id: str = ""
    url: str = ""
    opened_at: float = 0.0
    # What the session was opened with, so a reopen uses the same browser
    # rather than a default one.
    settings: dict = field(default_factory=dict)

    @property
    def attached(self) -> bool:
        """Whether a browser is currently held."""
        return bool(self.session_id)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | bytes) -> SessionRecord | None:
        try:
            data = json.loads(raw)
            settings = data.get("settings")
            return cls(
                session_id=str(data.get("session_id") or ""),
                url=str(data.get("url", "")),
                opened_at=float(data.get("opened_at", 0.0)),
                settings=settings if isinstance(settings, dict) else {},
            )
        except (ValueError, TypeError):
            # A malformed entry is a cache miss, not an outage.
            return None

    def at(self, url: str) -> SessionRecord:
        """The same record, remembering a newer page."""
        return replace(self, url=url or self.url)

    def detached(self) -> SessionRecord:
        """The same record with no browser, keeping the context it had.

        Not a delete: the browser choice and the last page are what the next
        open is meant to inherit, so ending a browser must not take them.
        """
        return replace(self, session_id="")


class SessionStore(Protocol):
    """Maps a caller key to the browser session it is using."""

    kind: str

    def get(self, key: str) -> SessionRecord | None: ...

    def set(self, key: str, record: SessionRecord) -> None: ...

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

    def get(self, key: str) -> SessionRecord | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, record = entry
        if self._clock() >= expires_at:
            del self._data[key]
            return None
        return record

    def set(self, key: str, record: SessionRecord) -> None:
        self._data[key] = (self._clock() + self._ttl, record)

    def delete(self, key: str) -> None:
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
        self._redis = client
        self._prefix = prefix
        self._ttl = ttl

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> SessionRecord | None:
        value = self._redis.get(self._k(key))
        if value is None:
            return None
        return SessionRecord.from_json(value)

    def set(self, key: str, record: SessionRecord) -> None:
        self._redis.set(self._k(key), record.to_json(), ex=self._ttl)

    def delete(self, key: str) -> None:
        self._redis.delete(self._k(key))

    def records(self) -> dict[str, SessionRecord]:
        """Every live flow session, keyed the way it is stored.

        SCAN rather than KEYS: this runs on a database shared with other
        services, and KEYS would block the server while it walked all of it.
        """
        found: dict[str, SessionRecord] = {}
        for raw in self._redis.scan_iter(match=f"{self._prefix}*", count=100):
            key = raw.decode() if isinstance(raw, bytes) else raw
            record = SessionRecord.from_json(self._redis.get(key) or b"")
            if record:
                found[key[len(self._prefix) :]] = record
        return found

    def owners(self) -> dict[str, str]:
        """session id -> the caller key holding it, for the sessions attached."""
        return {r.session_id: k for k, r in self.records().items() if r.attached}


def redis_configured(env: dict | None = None) -> bool:
    """Whether any REDIS_* connection setting was supplied."""
    env = os.environ if env is None else env
    return bool(env.get("REDIS_URL") or env.get("REDIS_HOST"))


def chosen_backend(env: dict | None = None) -> str:
    """Which backend the environment asks for.

    ``SESSION_STORE`` is the explicit switch. Without it the presence of a
    ``REDIS_*`` connection setting implies redis, so a deployment configured
    before this variable existed behaves the same.
    """
    env = os.environ if env is None else env
    explicit = str(env.get("SESSION_STORE", "")).strip().lower()
    if explicit:
        return explicit
    return "redis" if redis_configured(env) else "memory"


def from_env(env: dict | None = None) -> SessionStore:
    """Build the session store the environment asks for.

    Falls back to memory, loudly, if redis is asked for but unusable — a mapping
    that resolves locally beats a server that will not start, and the log line
    says which one is in play.
    """
    env = os.environ if env is None else env
    ttl = int(env.get("SESSION_TTL", DEFAULT_TTL_SECONDS))
    backend = chosen_backend(env)

    if backend == "memory":
        log.info("session store: memory, ttl %ss", ttl)
        return MemoryStore(ttl=ttl)

    if backend != "redis":
        log.warning(
            "SESSION_STORE=%s is not a known backend (memory, redis). "
            "Falling back to in-memory sessions.",
            backend,
        )
        return MemoryStore(ttl=ttl)

    prefix = env.get("REDIS_PREFIX", DEFAULT_PREFIX)
    db = int(env.get("REDIS_DB", DEFAULT_DB))

    try:
        import redis  # imported here: an optional dependency must not be a hard import
    except ImportError:
        log.warning(
            "SESSION_STORE=redis but the redis package is missing — "
            "install kubed-selenium-flow[redis]. Falling back to in-memory sessions."
        )
        return MemoryStore(ttl=ttl)

    try:
        if env.get("REDIS_URL"):
            # Passed explicitly rather than left to the URL, so REDIS_DB is
            # honoured even when the URL carries no /<index> path.
            client = redis.Redis.from_url(env["REDIS_URL"], db=db)
        else:
            client = redis.Redis(
                host=env.get("REDIS_HOST", "localhost"),
                port=int(env.get("REDIS_PORT", 6379)),
                db=db,
                password=env.get("REDIS_PASSWORD") or None,
                username=env.get("REDIS_USERNAME") or None,
                ssl=str(env.get("REDIS_SSL", "")).strip().lower()
                in ("1", "true", "yes", "on"),
            )
        client.ping()
    except Exception as exc:  # noqa: BLE001 - a bad address must not stop the boot
        log.warning(
            "Redis is configured but unreachable (%s). "
            "Falling back to in-memory sessions.",
            exc,
        )
        return MemoryStore(ttl=ttl)

    log.info("session store: redis db %s, prefix %s, ttl %ss", db, prefix, ttl)
    return RedisStore(client, prefix=prefix, ttl=ttl)
