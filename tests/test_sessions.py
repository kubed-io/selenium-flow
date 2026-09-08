"""Saved sessions: the optional convenience, and its two backends.

The feature exists for MCP callers only. Its off state is a first-class mode,
not a degradation, so both are exercised here.
"""

import pytest

from kubed.selenium_flow.sessions import SavedSessions
from kubed.selenium_flow.store import (
    DEFAULT_DB,
    DEFAULT_PREFIX,
    MemoryStore,
    RedisStore,
    from_env,
    redis_configured,
)

pytestmark = pytest.mark.unit


class FakeRedis:
    """Enough of redis-py for the store: get, set with ex, delete, ping."""

    def __init__(self, reachable=True):
        self.data = {}
        self.expiries = {}
        self.reachable = reachable

    def ping(self):
        if not self.reachable:
            raise ConnectionError("nope")
        return True

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value.encode() if isinstance(value, str) else value
        self.expiries[key] = ex

    def delete(self, key):
        self.data.pop(key, None)


class RecordingActions:
    """Counts how many browsers were opened, without opening any."""

    def __init__(self):
        self.opened = 0
        self.closed = []

    def open_session(self, **_kwargs):
        self.opened += 1
        return {"session_id": f"generated-{self.opened}"}

    def close_session(self, session_id):
        self.closed.append(session_id)
        return {"success": True, "session_id": session_id}


# ---- the feature, on -------------------------------------------------------


def test_an_explicit_session_id_always_wins():
    """Even with a different one remembered.

    Substituting a saved session for an id the caller named would drive the
    wrong browser, silently.
    """
    actions = RecordingActions()
    saved = SavedSessions(actions, MemoryStore(), enabled=True)
    saved.remember("conversation-1", "remembered")
    assert saved.resolve("conversation-1", "explicit") == "explicit"
    assert actions.opened == 0


def test_the_first_call_opens_a_browser_and_the_second_reuses_it():
    actions = RecordingActions()
    saved = SavedSessions(actions, MemoryStore(), enabled=True)
    first = saved.resolve("conversation-1", None)
    second = saved.resolve("conversation-1", None)
    assert first == second == "generated-1"
    assert actions.opened == 1


def test_separate_conversations_get_separate_browsers():
    actions = RecordingActions()
    saved = SavedSessions(actions, MemoryStore(), enabled=True)
    assert saved.resolve("conversation-1", None) != saved.resolve("conversation-2", None)
    assert actions.opened == 2


def test_forgetting_makes_the_next_call_open_a_new_one():
    actions = RecordingActions()
    saved = SavedSessions(actions, MemoryStore(), enabled=True)
    saved.resolve("c", None)
    saved.forget("c")
    saved.resolve("c", None)
    assert actions.opened == 2


def test_a_transport_with_no_session_key_must_be_explicit():
    saved = SavedSessions(RecordingActions(), MemoryStore(), enabled=True)
    with pytest.raises(ValueError, match="no session to key on"):
        saved.resolve(None, None)


# ---- the feature, off ------------------------------------------------------


def test_disabled_requires_an_explicit_session_id():
    actions = RecordingActions()
    saved = SavedSessions(actions, MemoryStore(), enabled=False)
    with pytest.raises(ValueError, match="saved sessions are disabled"):
        saved.resolve("c", None)
    assert actions.opened == 0, "disabled must never open a browser by itself"


def test_disabled_still_honours_an_explicit_id():
    saved = SavedSessions(RecordingActions(), MemoryStore(), enabled=False)
    assert saved.resolve("c", "abc") == "abc"


def test_disabled_reports_itself():
    assert SavedSessions(RecordingActions(), enabled=False).kind == "disabled"
    assert SavedSessions(RecordingActions(), enabled=True).kind == "memory"


# ---- the redis backend -----------------------------------------------------


def test_redis_store_round_trips_and_expires():
    fake = FakeRedis()
    store = RedisStore(fake, prefix="p:", ttl=99)
    store.set("k", "session-abc")
    assert store.get("k") == "session-abc"
    assert "p:k" in fake.data, "the prefix must be applied"
    assert fake.expiries["p:k"] == 99, "entries must expire, not linger past the browser"
    store.delete("k")
    assert store.get("k") is None


def test_redis_backed_saved_sessions_behave_the_same():
    """The backend is an implementation detail; the behaviour is not."""
    actions = RecordingActions()
    saved = SavedSessions(actions, RedisStore(FakeRedis()), enabled=True)
    assert saved.resolve("c", None) == saved.resolve("c", None)
    assert actions.opened == 1
    assert saved.kind == "redis"


# ---- configuration ---------------------------------------------------------


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, False),
        ({"REDIS_URL": "redis://x:6379"}, True),
        ({"REDIS_HOST": "redis.data"}, True),
        ({"REDIS_PORT": "6379"}, False),  # a port alone configures nothing
    ],
)
def test_presence_of_a_redis_setting_is_the_switch(env, expected):
    assert redis_configured(env) is expected


def test_no_redis_env_means_memory():
    store = from_env({})
    assert isinstance(store, MemoryStore)
    assert store.kind == "memory"


def test_unreachable_redis_falls_back_to_memory_rather_than_failing_to_boot(monkeypatch):
    """A bad address should degrade, not take the server down."""
    import sys
    import types

    module = types.ModuleType("redis")
    module.Redis = type(
        "Redis",
        (),
        {
            "__init__": lambda self, **kw: None,
            "ping": lambda self: (_ for _ in ()).throw(ConnectionError("refused")),
            "from_url": classmethod(lambda cls, url: cls()),
        },
    )
    monkeypatch.setitem(sys.modules, "redis", module)
    assert isinstance(from_env({"REDIS_URL": "redis://nope:6379"}), MemoryStore)


def test_a_missing_redis_package_falls_back_to_memory(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fail_on_redis(name, *args, **kwargs):
        if name == "redis":
            raise ImportError("no redis")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_on_redis)
    assert isinstance(from_env({"REDIS_HOST": "redis.data"}), MemoryStore)


def test_the_default_prefix_is_namespaced():
    """Redis is shared with other apps in this cluster; do not collide."""
    assert DEFAULT_PREFIX.startswith("selenium-flow:")


# ---- the shared Redis instance and its database index ----------------------


class CapturingRedisModule:
    """Records how the client was constructed, so the db index can be asserted."""

    def __init__(self):
        self.kwargs = {}
        module = self

        class Redis:
            def __init__(self, **kw):
                module.kwargs = kw

            @classmethod
            def from_url(cls, url, **kw):
                module.kwargs = {"url": url, **kw}
                return cls(**kw)

            def ping(self):
                return True

        self.Redis = Redis


@pytest.fixture
def capturing_redis(monkeypatch):
    import sys

    module = CapturingRedisModule()
    monkeypatch.setitem(sys.modules, "redis", module)
    return module


def test_the_url_form_still_pins_the_database_index(capturing_redis):
    """REDIS_DB must win over a URL that names no index.

    Otherwise a deployment that sets REDIS_DB alongside a plain REDIS_URL
    silently lands on db 0 anyway.
    """
    from_env({"REDIS_URL": "redis://redis.data:6379", "REDIS_DB": "2"})
    assert capturing_redis.kwargs["db"] == 2


def test_the_host_form_pins_it_too(capturing_redis):
    from_env({"REDIS_HOST": "redis.data"})
    assert capturing_redis.kwargs["db"] == DEFAULT_DB


def test_an_explicit_index_is_honoured(capturing_redis):
    from_env({"REDIS_URL": "redis://redis.data:6379", "REDIS_DB": "11"})
    assert capturing_redis.kwargs["db"] == 11


def test_the_default_index_makes_no_assumption_about_the_deployment():
    """0 is Redis's own default.

    Guessing an index would be wrong in anyone else's cluster; the prefix is
    what makes sharing a database safe, and a deployment with a convention
    passes REDIS_DB.
    """
    assert DEFAULT_DB == 0
