"""Sessions: the name a caller gives, and which browser that resolves to.

Naming is the whole contract now (§F2.12). The tests that matter most are the
ones proving a request that names nothing — or names two things — is *refused*
rather than quietly given a browser, because inventing an identity is how every
tool call opened a browser nobody closed.
"""

import pytest

from kubed.selenium_flow.session import sessions as sessions_module
from kubed.selenium_flow.session.sessions import requested
from kubed.selenium_flow.session.store import (
    DEFAULT_DB,
    DEFAULT_PREFIX,
    MemoryStore,
    RedisStore,
    SessionRecord,
    StoreUnavailable,
    chosen_backend,
    from_env,
    redis_configured,
)

from .conftest import NAMED, OTHER, RecordingActions, http, manager

pytestmark = pytest.mark.unit


# ---- naming the session ----------------------------------------------------


def test_a_name_in_the_query_string_is_the_session(monkeypatch):
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "research"})
    )
    assert requested() == sessions_module.Caller("research", "query")


def test_a_name_in_the_header_is_the_session_too(monkeypatch):
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http(headers={"x-session-key": "desk"})
    )
    assert requested() == sessions_module.Caller("desk", "header")


def test_naming_it_twice_is_refused_rather_than_resolved(monkeypatch):
    """Dr K's rule, and it replaces a precedence the old surface had. A request
    carrying both has two ideas about who is calling, and picking one hides that
    from whoever wired it up — including an admin who pinned a name in a
    credential and a caller that overrode it from the URL."""
    monkeypatch.setattr(
        sessions_module,
        "http_request",
        lambda: http({"session": "from-url"}, {"x-session-key": "from-credential"}),
    )
    with pytest.raises(ValueError, match="name your session once"):
        requested()


def test_a_request_that_names_nothing_names_nothing(monkeypatch):
    """None is a real answer, not a failure: it is what `library` turns into the
    shared library and what `name` refuses."""
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    assert requested() is None


def test_stdio_is_one_client_so_a_constant_is_correct(monkeypatch):
    monkeypatch.setattr(sessions_module, "http_request", lambda: None)
    assert requested() == sessions_module.Caller("stdio", "stdio")


def test_an_unusable_name_is_refused_where_it_arrives(monkeypatch):
    """A session name IS a directory name, so it is validated once, here. It used
    to be accepted for the browser and refused later for the library, which meant
    `?session=my bot` drove a private browser while saving its flows into the
    shared library."""
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "my bot"})
    )
    with pytest.raises(ValueError, match="not a usable session name"):
        requested()


def test_the_shared_library_cannot_be_claimed_as_a_name(monkeypatch):
    """`global` is read-only to everyone, so a caller that could name itself that
    would own every session's shared flows."""
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "global"})
    )
    with pytest.raises(ValueError, match="reserved"):
        requested()


# ---- one contract, and it is "say who you are" -----------------------------


def test_a_caller_that_named_nothing_is_told_how_to(monkeypatch):
    actions = RecordingActions()
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    with pytest.raises(ValueError, match=r"\?session=") as raised:
        manager(actions).name()
    assert "X-Session-Key" in str(raised.value)
    assert actions.opened == 0, "an unnamed caller must never open a browser"


def test_the_shared_library_is_the_one_thing_an_unnamed_caller_gets(monkeypatch):
    """The other half of requiring a name, rather than an exception to it:
    `global` is read-only, so an unnamed caller can read the shared flows and can
    write nowhere at all (§F2.13)."""
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    assert manager().library() == "global"


def test_a_named_caller_owns_its_own_library(monkeypatch):
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "research"})
    )
    assert manager().library() == "research"


# ---- resolving, without any magic ------------------------------------------


def test_resolve_never_opens_a_browser():
    """open_session is the only place a browser is created.

    Opening on first use hid the one place a session's settings could be
    chosen, which is why it was removed.
    """
    actions = RecordingActions()
    with pytest.raises(ValueError, match="call open_session first"):
        manager(actions).resolve(NAMED)
    assert actions.opened == 0


def test_a_live_remembered_session_is_recalled():
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("abc")
    sessions.remember(NAMED, "abc")
    assert sessions.resolve(NAMED) == "abc"
    assert sessions.resolve(NAMED) == "abc"
    assert actions.opened == 0


def test_clients_do_not_see_each_others_browsers():
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("mine")
    sessions.remember(NAMED, "mine")
    with pytest.raises(ValueError, match="call open_session first"):
        sessions.resolve(OTHER)


# ---- refreshing a session the Grid has reaped ------------------------------


def test_a_reaped_session_is_reopened_where_it_left_off():
    """The Grid expires idle sessions, so a stored id can name a dead browser.

    Reopening at the last known URL is what makes that invisible to the caller.
    """
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(
        NAMED, SessionRecord(session_id="dead", url="https://example.com/page")
    )
    assert sessions.resolve(NAMED) == "generated-1"
    assert actions.opened_urls == ["https://example.com/page"]


def test_a_refresh_reopens_with_the_same_settings():
    """Otherwise a refresh silently swaps the browser's shape mid-task."""
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(
        NAMED,
        SessionRecord(session_id="dead", url="", settings={"width": 1400, "height": 900}),
    )
    sessions.resolve(NAMED)
    assert actions.opened_settings == [{"width": 1400, "height": 900}]
    assert sessions.store.get(NAMED).settings == {"width": 1400, "height": 900}


def test_a_refresh_reopens_on_the_same_browser():
    """The reason `browser` is stored as a setting rather than beside them.

    A Firefox session that the Grid reaped and that came back as Chrome would be
    exactly the silent change of shape the stored settings exist to prevent, and
    the caller would have no way to see it happen.
    """
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(
        NAMED,
        SessionRecord(session_id="dead", url="", settings={"browser": "firefox"}),
    )
    sessions.resolve(NAMED)
    assert actions.opened_settings == [{"browser": "firefox"}]


def test_a_resize_is_remembered_so_a_refresh_replays_it():
    """`resize` is the only action that changes something the record *stores*.

    Left at the browser it would be undone the next time the Grid reaped that
    browser, which came back the size it was opened at — the silent shape change
    the stored settings exist to prevent, and the harder kind to notice because
    nothing errors.
    """
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.remember(NAMED, "dead", "", {"browser": "firefox", "width": 800})
    sessions.reshape(NAMED, {"width": 1024, "height": 768})

    record = sessions.store.get(NAMED)
    assert record.window == "1024x768"
    assert record.settings["browser"] == "firefox", "a merge, not a swap"

    # "dead" was never added to the fake Grid, so this takes the refresh path.
    sessions.resolve(NAMED)
    assert actions.opened_settings == [
        {"browser": "firefox", "width": 1024, "height": 768}
    ]


def test_the_refreshed_session_replaces_the_stored_one():
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(NAMED, SessionRecord(session_id="dead", url=""))
    sessions.resolve(NAMED)
    assert sessions.store.get(NAMED).session_id == "generated-1"
    assert actions.opened == 1, "a second resolve must not open another"
    assert sessions.resolve(NAMED) == "generated-1"


def test_touch_records_the_page_for_a_later_refresh():
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("abc")
    sessions.remember(NAMED, "abc")
    sessions.touch(NAMED, "https://example.com/deep")
    assert sessions.store.get(NAMED).url == "https://example.com/deep"


def test_touch_on_an_unknown_key_is_harmless():
    sessions = manager()
    sessions.touch(NAMED, "https://example.com")
    assert sessions.store.get(NAMED) is None


# ---- ending a browser ------------------------------------------------------


def test_ending_a_browser_means_open_session_again():
    """An agent is never told "your browser was taken". It is told it has none —
    the same branch as never having had one — so it calls open_session, which is
    the one path that opens one. resolve itself must never open anything."""
    actions = RecordingActions()
    sessions = manager(actions)
    actions.grid.alive.add("abc")
    sessions.remember(NAMED, "abc")
    sessions.end_browser(NAMED)
    opened_before = actions.opened
    with pytest.raises(ValueError, match="call open_session first"):
        sessions.resolve(NAMED)
    assert actions.opened == opened_before, "resolve must never open one"


def test_ending_a_browser_never_removes_the_flow_session():
    """Nothing removes one. A session expires on its TTL, and a named one comes
    straight back on the next call because the name is in the caller's URL.

    The record it leaves behind is also exactly what the next open_session
    inherits — the browser choice and the last page — so this is the same
    assertion as "ending keeps the context", made once."""
    sessions = manager()
    sessions.remember(NAMED, "mine", "https://x/", {"browser": "firefox"})
    sessions.end_browser(NAMED)
    record = sessions.store.get(NAMED)
    assert record is not None
    assert not record.attached
    assert record.url == "https://x/"
    assert record.settings == {"browser": "firefox"}


def test_a_session_can_only_end_its_own_browser():
    """There is no id to pass any more, which is the point: the only browser a
    caller can name is the one its own session holds."""
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.remember(NAMED, "mine")
    sessions.remember(OTHER, "theirs")
    assert sessions.end_browser(NAMED) == "mine"
    assert actions.closed == ["mine"]
    assert sessions.store.get(OTHER).session_id == "theirs"


def test_ending_a_browser_nobody_holds_is_harmless():
    actions = RecordingActions()
    sessions = manager(actions)
    assert sessions.end_browser(NAMED) is None
    assert actions.closed == []


def test_the_backend_in_use_is_reported():
    assert manager().kind == "memory"
    assert manager(store=RedisStore(FakeRedis())).kind == "redis"


# ---- what the status resource says -----------------------------------------
#
# describe() is SessionManager's, so it is tested here beside the rest of it.
# test_resources.py covers the other half of the question — which SHAPE a client
# gets this status in, a resource or a tool — and does not re-test the content.


def test_describe_reports_the_session_and_where_to_read_about_it(named_caller):
    status = manager().describe()
    assert status["session"] == NAMED
    assert "SESSIONS.md" in status["guidance"]


def test_describe_never_names_the_grid_id(named_caller):
    """The whole of E18 in one assertion: the browser id is how a browser is
    reached and is not part of what a caller is told."""
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "https://example.com")
    assert "abc" not in repr(sessions.describe())


def test_describe_never_opens_a_browser(named_caller):
    """Reading a status resource must never create one."""
    actions = RecordingActions()
    status = manager(actions).describe()
    assert status["live"] is False
    assert status["session"] == NAMED
    assert actions.opened == 0


def test_describe_reports_a_held_session_and_whether_it_is_still_there(named_caller):
    """Whether the browser is still there is the question this resource is for."""
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.store.set(
        NAMED, SessionRecord(session_id="abc", url="https://example.com")
    )
    status = sessions.describe()
    assert status["url"] == "https://example.com"
    assert status["live"] is True
    assert status["named_by"] == "query"


def test_describe_flags_a_session_the_grid_has_reaped(named_caller):
    sessions = manager()
    sessions.store.set(NAMED, SessionRecord(session_id="dead"))
    assert sessions.describe()["live"] is False


def test_describe_reports_the_settings_a_session_was_opened_with(named_caller):
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "", {"width": 1400})
    assert sessions.describe()["settings"] == {"width": 1400}


def test_describe_reports_which_browser_is_being_driven(named_caller):
    """Top level, not only inside settings: "which browser am I driving" is a
    question this resource exists to answer, and a caller should not have to
    know it happens to be stored as a setting."""
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "", {"browser": "firefox"})
    assert sessions.describe()["browser"] == "firefox"


def test_describe_reports_the_window_it_is_working_in(named_caller):
    """Top level, for the same reason as `browser`.

    An agent deciding whether something is off-screen, or whether a layout has
    collapsed, needs the window size — and should not have to take a screenshot
    or know it is stored as a setting to find it. None when the session never
    named one: the window is then whatever the Grid node's default happens to
    be, and printing a number would claim we knew which.
    """
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "", {"width": 1024, "height": 768})
    assert sessions.describe()["window"] == "1024x768"

    sessions.remember(NAMED, "abc", "", {"browser": "firefox"})
    assert sessions.describe()["window"] is None


def test_a_session_stored_before_browsers_were_selectable_reads_as_chrome(named_caller):
    """A record with no browser really is the default one — there was nothing
    else to be — so reporting chrome is more true than reporting None."""
    actions = RecordingActions()
    actions.grid.alive.add("abc")
    sessions = manager(actions)
    sessions.remember(NAMED, "abc", "", {"width": 1400})
    assert sessions.describe()["browser"] == "chrome"


def test_describe_reports_no_browser_when_there_is_no_session(named_caller):
    """Naming a browser for a session that does not exist would be a fiction."""
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

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return [k for k in list(self.data) if k.startswith(prefix)]


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


def test_the_pointer_beside_a_session_does_not_empty_the_history():
    """The admin list went blank in production: the pointer store writes under
    the session prefix, `records()` read its `[x, y]` as a record, raised, and
    the page rendered the failure as "No sessions yet." Built through the real
    pointer store, so a pointer namespace that moves is still covered."""
    from kubed.selenium_flow.core import pointer

    fake = FakeRedis()
    store = RedisStore(fake, prefix="p:")
    store.set("desktop", SessionRecord(session_id="abc"))
    pointer.matching(store).set("abc", 10.5, 20.0)

    assert list(store.records()) == ["desktop"]


def test_json_that_is_not_a_record_is_a_miss_not_a_crash():
    """A pointer's `[x, y]` parses as JSON and is not a record. `.get` on it
    raised AttributeError, which the parse guard did not catch - the other half
    of the blank admin list, and not reached by the namespace test (Copilot, #33)."""
    assert SessionRecord.from_json("[253.5, 226.0]") is None
    assert SessionRecord.from_json("42") is None


def test_the_memory_store_expires_like_redis_does():
    """Both backends must mean the same thing by SESSION_TTL."""
    now = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: now[0])
    store.set("k", SessionRecord(session_id="abc"))
    now[0] += 59
    assert store.get("k").session_id == "abc"
    now[0] += 2
    assert store.get("k") is None


def test_listing_collects_the_records_nobody_asks_for_again():
    """`get` only ever expires the one key it is handed, so a session named once
    and never revisited stayed in memory until the process restarted. Listing is
    the only pass over every entry, so it is where they are collected."""
    now = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: now[0])
    store.set("gone", SessionRecord(session_id="a"))
    store.set("stays", SessionRecord(session_id="b"))
    now[0] += 61
    store.set("stays", SessionRecord(session_id="b"))

    assert set(store.records()) == {"stays"}
    assert "gone" not in store._data, "the expired record was filtered, not freed"


def test_touch_slides_the_expiry_of_a_session_in_use():
    now = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: now[0])
    sessions = manager(store=store)
    sessions.remember(NAMED, "abc")
    now[0] += 50
    sessions.touch(NAMED, "https://example.com")
    now[0] += 50
    assert sessions.store.get(NAMED) is not None, "an in-use session must not lapse"


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


def test_an_unknown_backend_stops_the_boot():
    with pytest.raises(StoreUnavailable, match="postgres"):
        from_env({"SESSION_STORE": "postgres"})


def test_session_ttl_is_honoured():
    assert from_env({"SESSION_TTL": "42"})._ttl == 42


def test_no_redis_env_means_memory():
    store = from_env({})
    assert isinstance(store, MemoryStore)
    assert store.kind == "memory"


def test_memory_is_used_when_nothing_asked_for_redis():
    """Unchanged behaviour (§F4.12): memory is the answer only when nothing
    asked for Redis at all, never a step down from a Redis that failed."""
    assert isinstance(from_env({}), MemoryStore)


def test_an_unreachable_redis_stops_the_boot_with_the_reason(monkeypatch):
    """§F4.12: a server that refuses to start is restarted by Kubernetes until
    Redis answers; one that started on the wrong store is never corrected."""
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
    with pytest.raises(StoreUnavailable, match="nope:6379"):
        from_env({"REDIS_URL": "redis://nope:6379"})


def test_a_missing_redis_package_stops_the_boot(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fail_on_redis(name, *args, **kwargs):
        if name == "redis":
            raise ImportError("no redis")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_on_redis)
    with pytest.raises(StoreUnavailable, match=r"kubed-selenium-flow\[redis\]"):
        from_env({"REDIS_HOST": "redis.data"})


def test_an_unreachable_redis_never_chains_the_raw_password(monkeypatch):
    """Copilot review, PR #41: ``StoreUnavailable``'s own message is scrubbed
    through ``errors.message``, but chaining the raw driver exception with
    ``from exc`` put its unscrubbed ``str()`` back into any traceback printed
    for the boot failure — including the password `where` is built to hide.
    Both the unreachable and missing-package raises must be ``from None``."""
    import sys
    import traceback
    import types

    url = "redis://:s3cret@nowhere:6379/2"

    module = types.ModuleType("redis")
    module.Redis = type(
        "Redis",
        (),
        {
            "__init__": lambda self, **kw: None,
            "ping": lambda self: (_ for _ in ()).throw(
                ConnectionError(f"could not connect to {url}")
            ),
            "from_url": classmethod(lambda cls, url, **kw: cls()),
        },
    )
    monkeypatch.setitem(sys.modules, "redis", module)

    with pytest.raises(StoreUnavailable) as excinfo:
        from_env({"REDIS_URL": url, "REDIS_DB": "2"})

    err = excinfo.value
    assert err.__cause__ is None
    assert err.__suppress_context__ is True
    assert "s3cret" not in "".join(traceback.format_exception(err))


def test_the_reason_never_quotes_the_redis_password(monkeypatch):
    """The refusal message is read by whoever restarts the pod, and logged —
    it must never carry the credential (§F4.12)."""
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
    with pytest.raises(StoreUnavailable) as excinfo:
        from_env({"REDIS_URL": "redis://:s3cret@nowhere:6379/2"})
    assert "s3cret" not in str(excinfo.value)


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
    from kubed.selenium_flow.core import browser as browser_module

    monkeypatch.setattr(
        browser_module.requests, "get", lambda *a, **k: response
    )
    grid = browser_module.Grid("http://grid.invalid:4444")
    assert grid.is_alive("abc") is alive, why


def test_an_unreachable_grid_does_not_strand_the_session(monkeypatch):
    from kubed.selenium_flow.core import browser as browser_module

    def boom(*a, **k):
        raise browser_module.requests.RequestException("down")

    monkeypatch.setattr(browser_module.requests, "get", boom)
    grid = browser_module.Grid("http://grid.invalid:4444")
    assert grid.is_alive("abc") is True


# ---- a flow session outlives its browser -----------------------------------


def test_ending_something_that_is_not_there_is_not_an_error():
    assert manager().end_browser("nobody") is None


def test_context_is_what_a_reopen_should_inherit(monkeypatch):
    sessions = manager()
    sessions.store.set(
        NAMED,
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


# ---- one session holds one browser -----------------------------------------


def test_opening_a_replacement_ends_the_browser_it_replaces():
    """Switching browser used to abandon the old one on the Grid.

    A flow session holds at most one browser, so opening a second without
    ending the first leaves it running, referenced by nothing, holding one of a
    handful of Grid slots until the idle timeout. Found by switching Chrome to
    Firefox and watching sessionCount go to 2.
    """
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.remember(NAMED, "old-browser", "https://x/", {"browser": "chrome"})
    ended = sessions.end_browser(NAMED)
    assert ended == "old-browser"
    assert actions.closed == ["old-browser"]


def test_replacing_keeps_the_session_and_its_context():
    """The same command the admin End uses — the context is what the
    replacement inherits, so ending must not take it."""
    sessions = manager()
    sessions.remember(NAMED, "old-browser", "https://x/", {"browser": "firefox"})
    sessions.end_browser(NAMED)
    record = sessions.store.get(NAMED)
    assert not record.attached
    assert record.url == "https://x/"
    assert record.settings == {"browser": "firefox"}


def test_ending_a_session_with_no_browser_ends_nothing():
    actions = RecordingActions()
    sessions = manager(actions)
    sessions.store.set(NAMED, SessionRecord(session_id="", url="https://x/"))
    assert sessions.end_browser(NAMED) is None
    assert actions.closed == []


def test_ending_detaches_even_when_the_browser_will_not_quit():
    """It is already gone or the Grid is unreachable; either way the record
    must stop naming it, or the next call tries to use it."""

    class Refuses(RecordingActions):
        def end_browser(self, session_id):
            raise RuntimeError("gone")

    sessions = manager(Refuses())
    sessions.remember(NAMED, "old-browser", "https://x/")
    sessions.end_browser(NAMED)
    assert not sessions.store.get(NAMED).attached


# ---- opening from a known start ---------------------------------------------


async def test_open_session_comes_back_to_the_page_it_was_on(server, monkeypatch):
    """The default, and the reason a reaped browser is invisible."""
    from fastmcp import Client

    from kubed.selenium_flow.session.store import SessionRecord

    from .conftest import NAMED

    seen = {}

    def fake_open(**kwargs):
        seen.update(kwargs)
        return {"session_id": "abc", "url": kwargs.get("url") or "about:blank"}

    monkeypatch.setattr(server.sessions, "name", lambda: NAMED)
    monkeypatch.setattr(server.actions, "open_session", fake_open)
    server.sessions.store.set(
        NAMED, SessionRecord(session_id="", url="https://app.test/orders")
    )
    async with Client(server.mcp) as client:
        await client.call_tool("open_session", {})
    assert seen["url"] == "https://app.test/orders"


async def test_fresh_drops_the_remembered_page_and_keeps_the_browser(
    server, monkeypatch
):
    """For running something from a known start — a login flow you want to
    exercise signed out. Only the page is dropped: coming back as Chrome when
    the session was on Firefox is a silent change of shape, not a fresh start."""
    from fastmcp import Client

    from kubed.selenium_flow.session.store import SessionRecord

    from .conftest import NAMED

    seen = {}

    def fake_open(**kwargs):
        seen.update(kwargs)
        return {"session_id": "abc", "url": "about:blank"}

    monkeypatch.setattr(server.sessions, "name", lambda: NAMED)
    monkeypatch.setattr(server.actions, "open_session", fake_open)
    server.sessions.store.set(
        NAMED,
        SessionRecord(
            session_id="",
            url="https://app.test/orders",
            settings={"browser": "firefox", "width": 1400, "height": 900},
        ),
    )
    async with Client(server.mcp) as client:
        await client.call_tool("open_session", {"fresh": True})

    assert seen["url"] is None, "the remembered page is dropped"
    assert seen["browser"] == "firefox", "and the browser it was using is not"
    assert seen["width"] == 1400


async def test_a_url_given_alongside_fresh_still_wins(server, monkeypatch):
    """`fresh` says "not where I was", not "nowhere". A caller that named a
    start has named one."""
    from fastmcp import Client

    from kubed.selenium_flow.session.store import SessionRecord

    from .conftest import NAMED

    seen = {}
    monkeypatch.setattr(server.sessions, "name", lambda: NAMED)
    monkeypatch.setattr(
        server.actions,
        "open_session",
        lambda **kwargs: (
            seen.update(kwargs) or {"session_id": "abc", "url": "about:blank"}
        ),
    )
    server.sessions.store.set(
        NAMED, SessionRecord(session_id="", url="https://app.test/orders")
    )
    async with Client(server.mcp) as client:
        await client.call_tool(
            "open_session", {"fresh": True, "url": "https://app.test/login"}
        )
    assert seen["url"] == "https://app.test/login"
