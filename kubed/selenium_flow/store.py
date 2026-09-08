"""Where a saved session is kept: a key to browser-session-id map.

Backs the saved-sessions feature in ``sessions.py``, and nothing else. It stores
a short string per MCP conversation — never a browser, which lives on the Grid.

Two backends. In-pod memory is the default and is correct for one replica.
Redis is optional and only earns its place when more than one replica must
resolve the same key, or when the mapping should outlive a restart.

Redis is configured entirely from ``REDIS_*`` environment variables and is off
unless one of them is set, so nothing here runs for a caller who never asked.

``REDIS_DB`` matters more than it looks. The cluster runs one shared Redis whose
logical databases are handed out by index in a registry, and landing on someone
else's index pollutes their keyspace and eats their memory budget. This app's
index is 6. It is applied whether the connection came from ``REDIS_URL`` or from
the host/port settings, and a warning is logged if Redis is on and no index was
named.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

log = logging.getLogger(__name__)

DEFAULT_PREFIX = "selenium-flow:session:"
# The index claimed by this app in the cluster's Redis registry. Not merely a
# default: db 0 belongs to n8n, and every index has an owner.
DEFAULT_DB = 6
# A mapping outliving the browser it names is worse than no mapping, because the
# caller acts on a session that has already been reaped. The Grid's own idle
# timeout is 300s by default, so a day is generous but bounded.
DEFAULT_TTL_SECONDS = 86400


class SessionStore(Protocol):
    """Maps a caller-chosen key to a Grid session id."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, session_id: str) -> None: ...

    def delete(self, key: str) -> None: ...


class MemoryStore:
    """Process-local mapping. Correct for a single replica, lost on restart."""

    kind = "memory"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, session_id: str) -> None:
        self._data[key] = session_id

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class RedisStore:
    """Shared mapping, so any replica resolves the same key.

    Entries expire: a key whose browser has already been reaped by the Grid
    should stop resolving rather than hand out a dead session id.
    """

    kind = "redis"

    def __init__(self, client, prefix: str = DEFAULT_PREFIX, ttl: int = DEFAULT_TTL_SECONDS):
        self._redis = client
        self._prefix = prefix
        self._ttl = ttl

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> str | None:
        value = self._redis.get(self._k(key))
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    def set(self, key: str, session_id: str) -> None:
        self._redis.set(self._k(key), session_id, ex=self._ttl)

    def delete(self, key: str) -> None:
        self._redis.delete(self._k(key))


def redis_configured(env: dict | None = None) -> bool:
    """Whether any REDIS_* setting was supplied.

    Presence is the switch. There is no REDIS_ENABLED flag because a URL or host
    that is set but ignored is a worse failure than one that is missing.
    """
    env = os.environ if env is None else env
    return bool(env.get("REDIS_URL") or env.get("REDIS_HOST"))


def from_env(env: dict | None = None) -> SessionStore:
    """Build the session store the environment asks for.

    Falls back to memory, loudly, if Redis is asked for but unusable — a session
    key that resolves locally is better than a server that will not start, and
    the log line says which one is in play.
    """
    env = os.environ if env is None else env
    if not redis_configured(env):
        return MemoryStore()

    prefix = env.get("REDIS_PREFIX", DEFAULT_PREFIX)
    ttl = int(env.get("REDIS_TTL", DEFAULT_TTL_SECONDS))
    if env.get("REDIS_DB") is None:
        log.warning(
            "REDIS_DB is not set; using this app's registered index %s. The "
            "cluster's Redis is shared and every index has an owner.",
            DEFAULT_DB,
        )
    db = int(env.get("REDIS_DB", DEFAULT_DB))

    try:
        import redis  # imported here: an optional dependency must not be a hard import
    except ImportError:
        log.warning(
            "REDIS_* is set but the redis package is missing — "
            "install kubed-selenium-flow[redis]. Falling back to in-memory sessions."
        )
        return MemoryStore()

    try:
        if env.get("REDIS_URL"):
            # db is passed explicitly rather than left to the URL: a URL without
            # a /<index> path silently means db 0, which belongs to n8n.
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
        return MemoryStore()

    log.info("session store: redis db %s, prefix %s, ttl %ss", db, prefix, ttl)
    return RedisStore(client, prefix=prefix, ttl=ttl)
