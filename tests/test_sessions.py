"""Sessions: how a caller is identified, and which browser that resolves to.

Keying is the whole risk in this feature. Key on something that changes per
request and every tool call opens a browser nobody closes, so the tests that
matter most here are the ones that prove an unstable identity is *refused*
rather than quietly used.
"""

import pytest

from kubed.selenium_flow import sessions as sessions_module
from kubed.selenium_flow.sessions import CallerKey, SessionManager, caller_key
from kubed.selenium_flow.store import (
    DEFAULT_DB,
    DEFAULT_PREFIX,
    MemoryStore,
    RedisStore,
    SessionRecord,
    chosen_backend,
    from_env,
    redis_configured,
)

pytestmark = pytest.mark.unit

NAMED = CallerKey("named:desktop", "named")
OTHER = CallerKey("named:laptop", "named")


class FakeGrid:
    """Tracks which sessions are still live, and every liveness question asked."""

    def __init__(self):
        self.alive = set()
        self.checked = []

    def is_alive(self, session_id):
        self.checked.append(session_id)
        return session_id in self.alive


class RecordingActions:
    """Counts how many browsers were opened, without opening any."""

    def __init__(self):
        self.opened = 0
        self.opened_urls = []
        self.opened_settings = []
        self.closed = []
        self.grid = FakeGrid()

    def open_session(self, url=None, **kwargs):
        self.opened += 1
        session_id = f"generated-{self.opened}"
        self.opened_urls.append(url)
        self.opened_settings.append(dict(kwargs))
        self.grid.alive.add(session_id)
        return {"session_id": session_id, "url": url or "about:blank"}

    def close_session(self, session_id):
        self.closed.append(session_id)
        self.grid.alive.discard(session_id)
        return {"success": True, "session_id": session_id}


def manager(actions=None, store=None, enabled=True):
    return SessionManager(
        actions or RecordingActions(), store or MemoryStore(), enabled=enabled
    )


# ---- identifying the caller ------------------------------------------------


def http(params=None, headers=None):
    """Stand in for the ambient HTTP request."""
    return dict(params or {}), dict(headers or {})


def test_a_named_session_in_the_query_string_is_the_key(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: http({"session": "desktop"}))
    key = caller_key()
    assert key == CallerKey("named:desktop", "named")


def test_a_named_session_header_works_too(monkeypatch):
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http(headers={"x-session-key": "desktop"})
    )
    assert caller_key().value == "named:desktop"


def test_the_header_wins_over_the_query_parameter(monkeypatch):
    """A permission boundary, not a preference.

    The header is set inside the credential, which an admin controls; the query
    parameter is written by whoever wires up the call. An admin pinning a name
    in the credential means one session per credential, so a caller must not be
    able to override it from the URL.
    """
    monkeypatch.setattr(
        sessions_module,
        "http_request",
        lambda: http({"session": "from-param"}, {"x-session-key": "from-header"}),
    )
    assert caller_key().value == "named:from-header"


def test_a_name_beats_the_transport_session(monkeypatch):
    """An explicit choice by the client is more trustworthy than the transport."""
    monkeypatch.setattr(
        sessions_module,
        "http_request",
        lambda: http({"session": "desktop"}, {"mcp-session-id": "abc123"}),
    )
    assert caller_key() == CallerKey("named:desktop", "named")


def test_the_transport_session_header_is_used_when_there_is_no_name(monkeypatch):
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http(headers={"mcp-session-id": "abc123"})
    )
    assert caller_key() == CallerKey("mcp:abc123", "transport")


def test_a_request_with_nothing_stable_has_no_key(monkeypatch):
    """The regression that leaked a browser per call.

    Context.session_id answers this case with a fresh uuid4 instead of an error,
    so every call looked like a new client and opened a new browser. Refusing to
    invent a key is the entire fix.
    """
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    assert caller_key() is None


def test_stdio_is_one_client_so_a_constant_is_correct(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: None)
    assert caller_key() == CallerKey("stdio", "stdio")


def test_no_key_means_the_caller_must_be_explicit():
    actions = RecordingActions()
    with pytest.raises(ValueError, match="no stable session"):
        manager(actions).resolve(None, None)
    assert actions.opened == 0, "an unidentifiable caller must never open a browser"


def test_the_error_names_the_reference_that_explains_it():
    """An agent that hits this should not have to guess what to read."""
    with pytest.raises(ValueError, match="STATELESS.md"):
        manager().resolve(None, None)


# ---- the two modes are exclusive -------------------------------------------


def test_a_key_means_saved_mode(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: http({"session": "d"}))
    sessions = manager()
    assert sessions.mode() == sessions.SAVED


def test_no_key_means_stateless_mode(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    sessions = manager()
    assert sessions.mode() == sessions.STATELESS


def test_stateless_honours_the_id_it_is_given():
    assert manager().resolve(None, "abc") == "abc"


def test_saved_mode_refuses_a_session_id():
    """The modes are exclusive on purpose.

    A caller passing an id while the server holds one for it is either confused
    or reaching for a browser it does not own, and both produce the expensive
    kind of mistake: acting on the wrong browser.
    """
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.remember(NAMED, "mine")
    with pytest.raises(ValueError, match="do not pass session_id"):
        sessions.resolve(NAMED, "somebody-elses")
    assert actions.opened == 0


def test_the_refusal_names_the_reference():
    with pytest.raises(ValueError, match="SAVED_SESSIONS.md"):
        manager().resolve(NAMED, "abc")


# ---- resolving, without any magic ------------------------------------------


def test_resolve_never_opens_a_browser():
    """open_session is the only place a browser is created.

    Opening on first use hid the one place a session's settings could be
    chosen, which is why it was removed.
    """
    actions = RecordingActions()
    with pytest.raises(ValueError, match="call open_session first"):
        manager(actions).resolve(NAMED, None)
    assert actions.opened == 0


def test_a_live_remembered_session_is_recalled():
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("abc")
    sessions.remember(NAMED, "abc")
    assert sessions.resolve(NAMED, None) == "abc"
    assert sessions.resolve(NAMED, None) == "abc"
    assert actions.opened == 0


def test_clients_do_not_see_each_others_browsers():
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("mine")
    sessions.remember(NAMED, "mine")
    with pytest.raises(ValueError, match="call open_session first"):
        sessions.resolve(OTHER, None)


# ---- refreshing a session the Grid has reaped ------------------------------


def test_a_reaped_session_is_reopened_where_it_left_off():
    """The Grid expires idle sessions, so a stored id can name a dead browser.

    Reopening at the last known URL is what makes that invisible to the caller.
    """
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(
        NAMED.value, SessionRecord(session_id="dead", url="https://example.com/page")
    )
    assert sessions.resolve(NAMED, None) == "generated-1"
    assert actions.opened_urls == ["https://example.com/page"]


def test_a_refresh_reopens_with_the_same_settings():
    """Otherwise a refresh silently swaps the browser's shape mid-task."""
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(
        NAMED.value,
        SessionRecord(session_id="dead", url="", settings={"width": 1400, "height": 900}),
    )
    sessions.resolve(NAMED, None)
    assert actions.opened_settings == [{"width": 1400, "height": 900}]
    assert sessions.store.get(NAMED.value).settings == {"width": 1400, "height": 900}


def test_a_refresh_reopens_on_the_same_browser():
    """The reason `browser` is stored as a setting rather than beside them.

    A Firefox session that the Grid reaped and that came back as Chrome would be
    exactly the silent change of shape the stored settings exist to prevent, and
    the caller would have no way to see it happen.
    """
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(
        NAMED.value,
        SessionRecord(session_id="dead", url="", settings={"browser": "firefox"}),
    )
    sessions.resolve(NAMED, None)
    assert actions.opened_settings == [{"browser": "firefox"}]


def test_the_refreshed_session_replaces_the_stored_one():
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(NAMED.value, SessionRecord(session_id="dead", url=""))
    sessions.resolve(NAMED, None)
    assert sessions.store.get(NAMED.value).session_id == "generated-1"
    assert actions.opened == 1, "a second resolve must not open another"
    assert sessions.resolve(NAMED, None) == "generated-1"


def test_touch_records_the_page_for_a_later_refresh():
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("abc")
    sessions.remember(NAMED, "abc")
    sessions.touch(NAMED, "https://example.com/deep")
    assert sessions.store.get(NAMED.value).url == "https://example.com/deep"


def test_touch_on_an_unknown_key_is_harmless():
    sessions = manager()
    sessions.touch(NAMED, "https://example.com")
    assert sessions.store.get(NAMED.value) is None


# ---- forgetting ------------------------------------------------------------


def test_forgetting_means_open_session_again():
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("abc")
    sessions.remember(NAMED, "abc")
    sessions.forget(NAMED)
    with pytest.raises(ValueError, match="call open_session first"):
        sessions.resolve(NAMED, None)


def test_closing_a_session_you_named_does_not_evict_someone_elses():
    """Closing an explicitly passed id must not drop an unrelated binding."""
    sessions = manager()
    sessions.remember(NAMED, "mine")
    sessions.forget(NAMED, "a-different-session")
    assert sessions.store.get(NAMED.value).session_id == "mine"


def test_closing_the_remembered_session_does_evict_it():
    sessions = manager()
    sessions.remember(NAMED, "mine")
    sessions.forget(NAMED, "mine")
    assert sessions.store.get(NAMED.value) is None


# ---- the feature, off ------------------------------------------------------


def test_disabled_is_simply_stateless(monkeypatch):
    """Off is a real mode, not a degraded one: every tool still works."""
    monkeypatch.setattr(sessions_module, "http_request", lambda: http({"session": "d"}))
    sessions = manager(enabled=False)
    assert sessions.key() is None
    assert sessions.mode() == sessions.STATELESS
    assert sessions.resolve(None, "abc") == "abc"


def test_disabled_requires_an_explicit_session_id():
    actions = RecordingActions()
    with pytest.raises(ValueError, match="session_id is required"):
        manager(actions, enabled=False).resolve(None, None)
    assert actions.opened == 0


def test_the_backend_in_use_is_reported():
    assert manager(enabled=False).kind == "disabled"
    assert manager().kind == "memory"
    assert manager(store=RedisStore(FakeRedis())).kind == "redis"


# ---- what the status resource says -----------------------------------------


def test_describe_reports_the_mode_and_where_to_read_about_it(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: http({"session": "d"}))
    status = manager().describe()
    assert status["mode"] == "saved"
    assert status["pass_session_id"] is False
    assert "SAVED_SESSIONS.md" in status["guidance"]


def test_describe_reports_stateless_and_its_reference(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    status = manager().describe()
    assert status["mode"] == "stateless"
    assert status["pass_session_id"] is True
    assert "STATELESS.md" in status["guidance"]


def test_describe_never_opens_a_browser(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: http({"session": "d"}))
    actions = RecordingActions()
    assert manager(actions).describe()["session_id"] is None
    assert actions.opened == 0


def test_describe_reports_the_settings_a_session_was_opened_with(monkeypatch):
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "", {"width": 1400})
    assert sessions.describe()["settings"] == {"width": 1400}


def test_describe_reports_which_browser_is_being_driven(monkeypatch):
    """Top level, not only inside settings: "which browser am I driving" is a
    question this resource exists to answer, and a caller should not have to
    know it happens to be stored as a setting."""
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "", {"browser": "firefox"})
    assert sessions.describe()["browser"] == "firefox"


def test_a_session_stored_before_browsers_were_selectable_reads_as_chrome(monkeypatch):
    """A record with no browser really is the default one — there was nothing
    else to be — so reporting None would be less true than reporting chrome."""
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "", {"width": 1400})
    assert sessions.describe()["browser"] == "chrome"


def test_describe_reports_no_browser_when_there_is_no_session(monkeypatch):
    """Naming a browser for a session that does not exist would be a fiction."""
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    assert manager().describe()["browser"] is None


# ---- the stores ------------------------------------------------------------


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


def test_redis_store_round_trips_a_record_and_expires_it():
    fake = FakeRedis()
    store = RedisStore(fake, prefix="p:", ttl=99)
    store.set("k", SessionRecord(session_id="abc", url="https://example.com"))
    record = store.get("k")
    assert (record.session_id, record.url) == ("abc", "https://example.com")
    assert "p:k" in fake.data, "the prefix must be applied"
    assert fake.expiries["p:k"] == 99, "entries must expire, not outlive the browser"
    store.delete("k")
    assert store.get("k") is None


def test_a_corrupt_redis_entry_is_a_miss_not_a_crash():
    fake = FakeRedis()
    fake.data["p:k"] = b"not json"
    assert RedisStore(fake, prefix="p:").get("k") is None


def test_the_memory_store_expires_like_redis_does():
    """Both backends must mean the same thing by SESSION_TTL."""
    now = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: now[0])
    store.set("k", SessionRecord(session_id="abc"))
    now[0] += 59
    assert store.get("k").session_id == "abc"
    now[0] += 2
    assert store.get("k") is None


def test_touch_slides_the_expiry_of_a_session_in_use():
    now = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: now[0])
    sessions = manager(store=store)
    sessions.remember(NAMED, "abc")
    now[0] += 50
    sessions.touch(NAMED, "https://example.com")
    now[0] += 50
    assert sessions.store.get(NAMED.value) is not None, "an in-use session must not lapse"


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


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, "memory"),
        ({"REDIS_HOST": "redis.data"}, "redis"),
        ({"SESSION_STORE": "memory", "REDIS_HOST": "redis.data"}, "memory"),
        ({"SESSION_STORE": "redis"}, "redis"),
    ],
)
def test_session_store_is_the_explicit_switch(env, expected):
    """SESSION_STORE wins; without it, REDIS_* still implies redis."""
    assert chosen_backend(env) == expected


def test_an_unknown_backend_falls_back_to_memory():
    assert isinstance(from_env({"SESSION_STORE": "postgres"}), MemoryStore)


def test_session_ttl_is_honoured():
    assert from_env({"SESSION_TTL": "42"})._ttl == 42


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
            "from_url": classmethod(lambda cls, url, **kw: cls()),
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
    """REDIS_DB must win over a URL that names no index."""
    from_env({"REDIS_URL": "redis://redis.data:6379", "REDIS_DB": "2"})
    assert capturing_redis.kwargs["db"] == 2


def test_the_host_form_pins_it_too(capturing_redis):
    from_env({"REDIS_HOST": "redis.data"})
    assert capturing_redis.kwargs["db"] == DEFAULT_DB


def test_an_explicit_index_is_honoured(capturing_redis):
    from_env({"REDIS_URL": "redis://redis.data:6379", "REDIS_DB": "11"})
    assert capturing_redis.kwargs["db"] == 11


def test_the_default_index_makes_no_assumption_about_the_deployment():
    """0 is Redis's own default; the prefix is what makes sharing safe."""
    assert DEFAULT_DB == 0


# ---- liveness must not confuse "blocked" with "gone" ------------------------


class FakeResponse:
    def __init__(self, status, error=None):
        self.status_code = status
        self._error = error

    def json(self):
        if self._error is None:
            raise ValueError("no body")
        return {"value": {"error": self._error}}


@pytest.mark.parametrize(
    "response,alive,why",
    [
        (FakeResponse(200), True, "a healthy session"),
        (
            FakeResponse(500, "unexpected alert open"),
            True,
            "blocked by a dialog, but very much alive",
        ),
        (FakeResponse(404, "invalid session id"), False, "genuinely reaped"),
        (FakeResponse(500, "unknown error"), True, "some other failure"),
    ],
)
def test_only_an_invalid_session_id_counts_as_gone(monkeypatch, response, alive, why):
    """A wrong "dead" strands a browser; a wrong "alive" just errors next call.

    This shipped broken: an open dialog made the URL probe fail, resolve()
    decided the session was reaped, reopened, and abandoned the real browser
    with its dialog still up — leaking a Grid slot on every confirm().
    """
    from kubed.selenium_flow import browser as browser_module

    monkeypatch.setattr(
        browser_module.requests, "get", lambda *a, **k: response
    )
    grid = browser_module.Grid("http://grid.invalid:4444")
    assert grid.is_alive("abc") is alive, why


def test_an_unreachable_grid_does_not_strand_the_session(monkeypatch):
    from kubed.selenium_flow import browser as browser_module

    def boom(*a, **k):
        raise browser_module.requests.RequestException("down")

    monkeypatch.setattr(browser_module.requests, "get", boom)
    grid = browser_module.Grid("http://grid.invalid:4444")
    assert grid.is_alive("abc") is True


# ---- a flow session outlives its browser -----------------------------------


def test_a_detached_session_reads_as_having_no_browser(monkeypatch):
    """An agent is never told "your browser was taken". It is told it has none,
    which is the same branch as never having had one — and it calls open_session,
    the one path that opens one."""
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(NAMED.value, SessionRecord(session_id="", url="https://x/"))
    with pytest.raises(ValueError, match="no browser is open for you yet"):
        sessions.resolve(NAMED, None)
    assert actions.opened == 0, "resolve must never open one"


def test_detach_keeps_the_context_the_next_open_inherits():
    sessions = manager()
    sessions.store.set(
        NAMED.value,
        SessionRecord(
            session_id="abc", url="https://x/", settings={"browser": "firefox"}
        ),
    )
    sessions.detach(NAMED.value)
    record = sessions.store.get(NAMED.value)
    assert record.session_id == ""
    assert record.url == "https://x/"
    assert record.settings == {"browser": "firefox"}


def test_detaching_something_that_is_not_there_is_not_an_error():
    assert manager().detach("named:nobody") is None


def test_context_is_what_a_reopen_should_inherit(monkeypatch):
    sessions = manager()
    sessions.store.set(
        NAMED.value,
        SessionRecord(session_id="", url="https://x/", settings={"browser": "firefox"}),
    )
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    assert sessions.context(NAMED) == {
        "settings": {"browser": "firefox"},
        "url": "https://x/",
    }


def test_context_is_empty_when_there_is_nothing_to_inherit(monkeypatch):
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    assert manager().context(NAMED) == {}


# ---- stateless sessions are recorded, never resolved -----------------------


def test_a_stateless_session_is_recorded_under_its_own_browser():
    """So it appears in the admin history and expires like any other. This is
    NOT a caller key: nothing ever resolves a caller from it, which is what
    keeps the leak that `caller_key` exists to prevent prevented."""
    sessions = manager()
    sessions.remember(None, "sess-1", "https://x/", {"browser": "chrome"})
    record = sessions.store.get(sessions_module.stateless_key("sess-1"))
    assert record is not None
    assert record.session_id == "sess-1"
    assert record.url == "https://x/"


def test_a_stateless_caller_still_has_no_key(monkeypatch):
    """Recording one must not have quietly given stateless callers an identity."""
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    sessions = manager()
    sessions.remember(None, "sess-1", "https://x/")
    assert sessions.key() is None
    assert sessions.mode() == SessionManager.STATELESS


def test_a_stateless_session_records_where_it_got_to():
    sessions = manager()
    sessions.remember(None, "sess-1", "https://x/")
    sessions.touch(None, "https://x/deep", "sess-1")
    record = sessions.store.get(sessions_module.stateless_key("sess-1"))
    assert record.url == "https://x/deep"


def test_touch_without_a_session_id_or_key_records_nothing():
    """Belt and braces: the one path that could invent a store key must not."""
    sessions = manager()
    sessions.touch(None, "https://x/")
    assert sessions.store.records() == {}
