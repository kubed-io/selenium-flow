"""Where a saved session is kept: a caller key to browser-session record map.

Backs the session manager in ``sessions.py``, and nothing else. It stores a
small record per caller — never a browser, which lives on the Grid.

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
from dataclasses import asdict, dataclass, replace
from typing import Protocol

log = logging.getLogger(__name__)

# Every key is written under this prefix, which is what makes sharing a database
# with other applications safe.
DEFAULT_PREFIX = "selenium-flow:session:"
# Redis's own default. Deliberately not a guess about the deployment: an install
# with an index convention passes REDIS_DB, and the prefix keeps it safe if not.
DEFAULT_DB = 0
# How long a mapping is kept. Only a cache lifetime — the browser it names is
# reaped on the Grid's schedule, not this one, and a record that outlives its
# browser is detected and refreshed rather than trusted.
DEFAULT_TTL_SECONDS = 3600


@dataclass(frozen=True)
class SessionRecord:
    """What is remembered for one caller.

    ``url`` is the point of storing a record rather than a bare id: when the
    Grid has reaped the browser, reopening and navigating back to the last known
    page makes the refresh invisible to the caller.
    """

    session_id: str
    url: str = ""
    opened_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | bytes) -> SessionRecord | None:
        try:
            data = json.loads(raw)
            return cls(
                session_id=str(data["session_id"]),
                url=str(data.get("url", "")),
                opened_at=float(data.get("opened_at", 0.0)),
            )
        except (ValueError, KeyError, TypeError):
            # A malformed entry is a cache miss, not an outage.
            return None

    def at(self, url: str) -> SessionRecord:
        """The same record, remembering a newer page."""
        return replace(self, url=url or self.url)


class SessionStore(Protocol):
    """Maps a caller key to the browser session it is using."""

    kind: str

    def get(self, key: str) -> SessionRecord | None: ...

    def set(self, key: str, record: SessionRecord) -> None: ...

    def delete(self, key: str) -> None: ...


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
