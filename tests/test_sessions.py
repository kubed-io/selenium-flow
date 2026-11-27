"""Session keys, and the store: a workspace's record holding its list and its
history, each session's record beside it, and each key's lifetime by its place
in the list (WS-7, WS-81, WS-87, WS-88)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from kubed.selenium_flow.principal import ADMIN, Principal
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import (
    MemoryStore,
    RedisStore,
    Session,
    Workspace,
)

from .fakes import Clock, FakeRedis, record_of, seated, shared_of

PREFIX = "selenium-flow:workspace:"


def test_every_session_key_holds_both_names():
    assert sessions.key_of("shop", "default") == "shop/default"
    assert sessions.key_of("shop", "tester") == "shop/tester"
    assert sessions.split("shop/default") == ("shop", "default")
    assert sessions.split("shop/tester") == ("shop", "tester")
    assert sessions.split("shop") == ("shop", "")


def test_a_log_line_never_names_a_session():
    assert sessions.logged("shop/x") == "shop (a session)"
    assert sessions.logged("shop/default") == "shop (a session)"
    assert sessions.logged("shop") == "shop"


@pytest.mark.parametrize("name", ["current", "downloads", "console", "network", "files", "site-data", "a/b", "", ".x"])
def test_reserved_names_are_refused(name):
    with pytest.raises(ValueError):
        sessions.valid_session_name(name)


def test_a_usable_name_is_returned():
    assert sessions.valid_session_name("tester") == "tester"
    assert sessions.valid_session_name("default") == "default"
    assert sessions.valid_session_name("admin") == "admin"


@dataclass
class Backend:
    """One store under test, at ``ttl=100`` and ``sub_ttl=10``: memory on a
    clock a test moves, or Redis, whose expiries a test reads."""

    store: MemoryStore | RedisStore
    clock: Clock
    fake: FakeRedis | None

    def at(self, seconds: float) -> Backend:
        self.clock.now = seconds
        return self

    def ttl(self, key: str):
        return self.fake.expiries[PREFIX + key]


@pytest.fixture(params=["memory", "redis"])
def backend(request) -> Backend:
    clock = Clock(0.0)
    if request.param == "memory":
        return Backend(MemoryStore(ttl=100, sub_ttl=10, clock=clock), clock, None)
    fake = FakeRedis()
    return Backend(RedisStore(fake, ttl=100, sub_ttl=10), clock, fake)


def _listed(b: Backend, *names: str) -> None:
    """``shop`` listing ``names``, each with a record, written at 0 s."""
    b.at(0)
    b.store.set("shop", Workspace(sessions=list(names)))
    for name in names:
        b.store.upsert(f"shop/{name}", lambda r: Session())


def test_the_head_lives_as_the_workspace_does(backend):
    b = backend
    _listed(b, "a", "b")
    if b.fake is not None:
        assert (b.ttl("shop"), b.ttl("shop/a"), b.ttl("shop/b")) == (100, 100, 10)
        return
    assert b.at(9).store.get("shop/b") is not None
    assert b.at(11).store.get("shop/b") is None
    assert b.store.get("shop/a") is not None
    assert b.at(50).store.get("shop/a") is not None
    assert b.at(100).store.get("shop/a") is None


def test_a_kept_workspaces_head_never_expires_and_the_others_do(backend):
    b = backend
    asked = []
    b.store.kept = lambda workspace: asked.append(workspace) or True
    _listed(b, "a", "b")
    assert set(asked) == {"shop"}, "kept is asked with the workspace's name"
    if b.fake is not None:
        assert (b.ttl("shop"), b.ttl("shop/a"), b.ttl("shop/b")) == (None, None, 10)
        return
    assert b.at(11).store.get("shop/b") is None
    assert b.at(10**9).store.get("shop/a") is not None
    assert b.store.get("shop") is not None


def test_retain_applies_to_the_workspace_and_its_head(backend):
    b = backend
    _listed(b, "a", "b")
    b.store.kept = lambda workspace: True
    b.store.retain("shop")
    if b.fake is not None:
        assert (b.ttl("shop"), b.ttl("shop/a"), b.ttl("shop/b")) == (None, None, 10)
    else:
        assert b.at(1000).store.get("shop") is not None
        assert b.store.get("shop/a") is not None
        assert b.store.get("shop/b") is None
    b.store.kept = lambda workspace: False
    b.store.retain("shop")
    if b.fake is not None:
        assert (b.ttl("shop"), b.ttl("shop/a")) == (100, 100)
        return
    assert b.at(1099).store.get("shop/a") is not None
    assert b.store.get("shop") is not None
    assert b.at(1100).store.get("shop/a") is None
    assert b.store.get("shop") is None


def test_a_session_that_becomes_head_takes_its_lifetime_at_its_next_write(backend):
    b = backend
    _listed(b, "a", "b")
    if b.fake is not None:
        assert b.ttl("shop/b") == 10
    b.at(5).store.update("shop", lambda r: r.without(["a"]))
    b.store.update("shop/b", lambda r: Session(session_id="g-b"))
    if b.fake is not None:
        assert b.ttl("shop/b") == 100
        return
    assert b.at(50).store.get("shop/b").session_id == "g-b"


def test_a_workspace_write_slides_its_heads_key(backend):
    b = backend
    b.at(0).store.set("shop", Workspace(sessions=["a", "b"]))
    b.store.set("shop/a", Session(session_id="g-a"))
    if b.fake is not None:
        # Redis counts down on its own: what is left of `shop/a`'s, nearly gone.
        b.fake.expiries[PREFIX + "shop/a"] = 1
    for now in range(5, 155, 5):
        if now % 2:
            b.at(now).store.update("shop", lambda r: r.visited())
        else:
            b.at(now).store.set("shop", Workspace(sessions=["a", "b"]))
    assert b.at(150).store.get("shop/a").session_id == "g-a"
    assert b.store.get("shop/b") is None
    if b.fake is not None:
        assert b.ttl("shop/a") == 100
    # A head with no record is not made.
    b.store.set("other", Workspace(sessions=["x"]))
    b.store.update("other", lambda r: r.visited())
    assert b.store.get("other/x") is None
    if b.fake is not None:
        assert PREFIX + "other/x" not in b.fake.data


def test_a_pinged_head_keeps_its_workspace_record(backend, monkeypatch):
    """A ping the Grid answered is use (WS-88): with no call at all, the head
    pinged past `ttl` keeps its workspace's record, and so its place."""
    from types import SimpleNamespace

    from kubed.selenium_flow.workspace import workspaces as workspaces_module
    from kubed.selenium_flow.workspace.workspaces import Caller

    from .conftest import manager

    b = backend
    monkeypatch.setattr(workspaces_module, "time", SimpleNamespace(time=b.clock))
    workspaces = manager(store=b.store)
    workspaces.open_browser(Caller("shop"))
    browser = record_of(b.store, "shop").session_id
    for now in range(40, 400, 40):
        if b.fake is not None:
            # Redis counts down on its own: what is left of each, nearly gone.
            b.fake.expiries[PREFIX + "shop"] = b.fake.expiries[PREFIX + "shop/default"] = 1
        b.at(now)
        workspaces.pinged("shop/default", browser)
    if b.fake is not None:
        assert (b.ttl("shop"), b.ttl("shop/default")) == (100, 100)
    assert shared_of(b.store, "shop").sessions == ["default"]
    assert record_of(b.store, "shop").pinged_at == 360
    assert workspaces.head_of("shop") == "default"


def test_records_lists_workspaces_only(backend):
    store = backend.store
    store.set("shop", Workspace(sessions=["a"]))
    store.set("shop/a", Session(session_id="g-a"))
    store.set("shopper/b", Session())
    assert list(store.records()) == ["shop"]
    assert isinstance(store.records()["shop"], Workspace)
    assert list(store.sessions_of("shop")) == ["a"]
    assert isinstance(store.sessions_of("shop")["a"], Session)
    assert store.sessions_of("other") == {}


def test_owners_maps_every_sessions_browser_to_its_key(backend):
    store = backend.store
    store.set("shop", Workspace(sessions=["default", "a", "b"]))
    store.set("shop/default", Session(session_id="g-1"))
    store.set("shop/a", Session(session_id="g-2"))
    store.set("shop/b", Session())
    assert store.owners() == {"g-1": "shop/default", "g-2": "shop/a"}


def test_forget_workspace_forgets_every_session(backend):
    store = backend.store
    store.set("shop", Workspace(sessions=["default", "a"]))
    store.set("shop/default", Session())
    store.set("shop/a", Session())
    store.set("shopper", Workspace(sessions=["a"]))
    store.set("shopper/a", Session())
    store.forget_workspace("shop")
    assert store.get("shop") is None
    assert store.get("shop/default") is None
    assert store.get("shop/a") is None
    assert store.get("shopper") is not None
    assert store.get("shopper/a") is not None


def test_get_many_reads_each_key_in_order(backend):
    store = backend.store
    store.set("shop/a", Session(holder="a"))
    store.set("shop/c", Session(holder="c"))
    found = store.get_many(["shop/c", "shop/b", "shop/a"])
    assert [r.holder if r else None for r in found] == ["c", None, "a"]
    assert store.get_many([]) == []


def test_each_key_reads_as_its_own_type(backend):
    store = backend.store
    store.set("shop", Workspace(sessions=["a"]))
    store.set("shop/a", Session(holder="sub-a", session_id="g-a"))
    assert store.get("shop") == Workspace(sessions=["a"])
    assert store.get("shop/a") == Session(holder="sub-a", session_id="g-a")


OLD_JSON = json.dumps({
    "session_id": "b-1",
    "opened_at": 5.0,
    "settings": {"browser": "firefox"},
    "set_by": {"browser": "caller"},
    "owner": "sub-a",
    "subs": ["tester"],
    "history": [
        {"origin": "https://example.com", "url": "https://example.com/a",
         "at": 4.0, "session": "default"},
    ],
})


def test_a_workspace_record_without_sessions_is_the_old_shape():
    for record in (
        Workspace.from_json(OLD_JSON),
        _redis_read("shop", OLD_JSON),
    ):
        assert record.sessions == []
        assert record.head == "default"
        assert record.old.session.session_id == "b-1"
        assert record.old.session.settings == {"browser": "firefox"}
        assert record.old.owner == "sub-a"
        assert record.old.subs == ["tester"]
        assert [v["url"] for v in record.history] == ["https://example.com/a"]
        assert set(json.loads(record.to_json())) == {"sessions", "history"}


def test_an_old_sub_sessions_label_is_read_and_never_written():
    old = Session.from_json(json.dumps({"session_id": "", "label": "tester", "holder": "sub-b"}))
    assert (old.label_was, old.holder) == ("tester", "sub-b")
    assert "label_was" not in json.loads(old.to_json())
    assert "label" not in json.loads(old.to_json())


def _redis_read(key, raw):
    fake = FakeRedis()
    fake.data[PREFIX + key] = raw.encode()
    return RedisStore(fake).get(key)


def test_a_record_round_trips():
    session = Session(holder="sub-a", session_id="g-1", called_at=3.0)
    assert Session.from_json(session.to_json()) == session
    shared = Workspace(sessions=["a", "b"]).visited("https://example.com/x", session="a")
    assert Workspace.from_json(shared.to_json()) == shared
    assert Workspace.from_json('{"sessions": []}').head is None
    assert Workspace.from_json('{"sessions": ["a", 3, "b", "a"]}').sessions == ["a", "b"]


def test_last_used_is_the_latest_of_open_call_and_ping():
    assert Session().last_used == 0.0
    assert Session(opened_at=5.0, called_at=3.0, pinged_at=4.0).last_used == 5.0
    assert Session(opened_at=1.0, called_at=7.0, pinged_at=4.0).last_used == 7.0
    assert Session(opened_at=1.0, called_at=2.0, pinged_at=9.0).last_used == 9.0
    assert "last_used" not in json.loads(Session(opened_at=5.0).to_json())


def test_a_list_appends_a_name_once_and_drops_names():
    shared = Workspace().listing("a").listing("b").listing("a")
    assert shared.sessions == ["a", "b"]
    assert shared.head == "a"
    assert shared.without(["a"]).sessions == ["b"]
    assert shared.without(["a", "b"]).head is None


def test_the_heads_top_entry_is_the_one_kept():
    shared = (
        Workspace(sessions=["a", "b"])
        .visited("https://a.test/1", now=0.0, session="a")
        .visited("https://b.test/1", now=0.0, session="b")
    )
    later = shared.pruned(now=10**6, ttl=100, sub_ttl=10)
    assert [v["session"] for v in later.history] == ["a"]
    assert later.history_cleared().history == later.history
    assert [v["session"] for v in shared.history_cleared(["b"]).history] == ["b", "a"]


@pytest.mark.parametrize("name", ["console", "network", "files", "site-data"])
def test_a_name_a_session_uri_once_started_with_cannot_name_a_session(name):
    from kubed.selenium_flow.workspace.workspaces import Caller

    from .conftest import manager

    with pytest.raises(ValueError):
        manager().session_of(Caller("shop"), name)
    pinned = Caller.from_request({"workspace": ["shop"], "session": [name]}, {})
    with pytest.raises(ValueError):
        _ = pinned.pinned


def test_seated_is_read_back_by_record_of():
    store = MemoryStore(clock=Clock())
    visit = {"origin": "https://example.com", "url": "https://example.com/a", "at": 1.0, "session": "default"}
    seated(store, "shop", "default", session_id="b-1", history=[visit])
    seated(store, "shop", "tester", session_id="b-2")
    assert record_of(store, "shop").session_id == "b-1"
    assert record_of(store, "shop", "tester").session_id == "b-2"
    assert store.get("shop/default").session_id == "b-1"
    assert shared_of(store, "shop").page("default") == "https://example.com/a"
    assert shared_of(store, "shop").sessions == ["default", "tester"]


def test_own_name_is_the_username_when_valid():
    who = Principal("oidc", subject="3f2a-77", username="sam")
    assert sessions.own_name(who) == "sam"


@pytest.mark.parametrize("username", ["current", "a b", None])
def test_own_name_falls_back_to_the_subject(username):
    who = Principal("oidc", subject="3f2a-77", username=username)
    assert sessions.own_name(who) == "3f2a-77"


def test_an_own_name_nothing_valid_names_is_refused():
    with pytest.raises(ValueError) as refused:
        sessions.own_name(Principal("oidc", subject="auth0|1"))
    assert str(refused.value) == sessions.OWN_UNNAMED


def test_the_token_and_no_principal_are_admin_and_default():
    assert sessions.own_name(ADMIN) == "admin"
    assert sessions.own_name(None) == "default"
