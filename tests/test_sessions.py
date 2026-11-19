"""Session keys, and the store holding sub-sessions beside their workspace."""

from __future__ import annotations

import pytest

from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import MemoryStore, RedisStore, Workspace

from .fakes import Clock, FakeRedis


def test_a_default_session_key_is_the_workspace_name():
    assert sessions.key_of("shop", "default") == "shop"
    assert sessions.split("shop") == ("shop", "default")
    assert not sessions.is_sub("shop")


def test_a_sub_session_key_holds_both_names():
    assert sessions.key_of("shop", "tester") == "shop/tester"
    assert sessions.split("shop/tester") == ("shop", "tester")
    assert sessions.is_sub("shop/tester")


@pytest.mark.parametrize("name", ["current", "downloads", "console", "network", "files", "site-data", "a/b", "", ".x"])
def test_reserved_names_are_refused(name):
    with pytest.raises(ValueError):
        sessions.valid_session_name(name)


def test_a_usable_name_is_returned():
    assert sessions.valid_session_name("tester") == "tester"


def _memory(clock):
    return MemoryStore(ttl=86400, sub_ttl=3600, clock=clock)


def _redis():
    fake = FakeRedis()
    return RedisStore(fake, ttl=86400, sub_ttl=3600), fake


def test_a_sub_record_expires_after_sub_ttl():
    clock = Clock()
    store = _memory(clock)
    store.set("shop", Workspace())
    store.set("shop/tester", Workspace())
    clock.now += 3599
    assert store.get("shop/tester") is not None
    clock.now += 2
    assert store.get("shop/tester") is None
    assert store.get("shop") is not None


def test_a_kept_workspaces_sub_sessions_still_expire():
    clock = Clock()
    store = _memory(clock)
    store.kept = lambda key: True
    store.set("shop", Workspace())
    store.upsert("shop/tester", lambda r: Workspace())
    clock.now += 3601
    assert store.get("shop/tester") is None
    assert store.get("shop") is not None


def test_workspaces_lists_no_sub_session():
    store = _memory(Clock())
    store.set("shop", Workspace(subs=["tester"]))
    store.set("shop/tester", Workspace(session_id="grid-sub"))
    assert list(store.records()) == ["shop"]
    assert list(store.sub_records("shop")) == ["tester"]
    assert store.sub_records("other") == {}


def test_owners_maps_a_sub_sessions_browser_to_its_key():
    store = _memory(Clock())
    store.set("shop", Workspace(session_id="grid-main"))
    store.set("shop/tester", Workspace(session_id="grid-sub"))
    assert store.owners() == {"grid-main": "shop", "grid-sub": "shop/tester"}


def test_forgetting_a_workspace_forgets_its_sub_sessions():
    store = _memory(Clock())
    store.set("shop", Workspace(subs=["tester"]))
    store.set("shop/tester", Workspace())
    store.set("shopper", Workspace())
    store.set("shopper/a", Workspace())
    store.forget_workspace("shop")
    assert store.get("shop") is None
    assert store.get("shop/tester") is None
    assert store.get("shopper") is not None
    assert store.get("shopper/a") is not None


def test_a_record_round_trips_its_owner_and_subs():
    record = Workspace(owner="alice", subs=["a", "b"])
    again = Workspace.from_json(record.to_json())
    assert (again.owner, again.subs) == ("alice", ["a", "b"])
    old = Workspace.from_json('{"session_id": ""}')
    assert (old.owner, old.subs) == (None, [])


def test_a_redis_sub_record_expires_after_sub_ttl():
    store, fake = _redis()
    store.set("shop", Workspace())
    store.set("shop/tester", Workspace())
    store.upsert("shop/other", lambda r: Workspace())
    assert fake.expiries["selenium-flow:workspace:shop"] == 86400
    store.kept = lambda key: True
    store.set("shop/kept", Workspace())
    store.set("shop", Workspace())
    assert fake.expiries["selenium-flow:workspace:shop/tester"] == 3600
    assert fake.expiries["selenium-flow:workspace:shop/other"] == 3600
    assert fake.expiries["selenium-flow:workspace:shop/kept"] == 3600
    assert fake.expiries["selenium-flow:workspace:shop"] is None
    store.kept = lambda key: False
    store.retain("shop/tester")
    assert fake.expiries["selenium-flow:workspace:shop/tester"] == 3600
    store.retain("shop")
    assert fake.expiries["selenium-flow:workspace:shop"] == 86400


def test_redis_workspaces_lists_no_sub_session():
    store, _ = _redis()
    store.set("shop", Workspace(subs=["tester"]))
    store.set("shop/tester", Workspace(session_id="grid-sub"))
    assert list(store.records()) == ["shop"]
    assert list(store.sub_records("shop")) == ["tester"]


def test_redis_owners_maps_a_sub_sessions_browser_to_its_key():
    store, _ = _redis()
    store.set("shop", Workspace(session_id="grid-main"))
    store.set("shop/tester", Workspace(session_id="grid-sub"))
    assert store.owners() == {"grid-main": "shop", "grid-sub": "shop/tester"}


def test_redis_forgetting_a_workspace_forgets_its_sub_sessions():
    store, _ = _redis()
    store.set("shop", Workspace())
    store.set("shop/tester", Workspace())
    store.set("shopper/a", Workspace())
    store.forget_workspace("shop")
    assert store.get("shop") is None
    assert store.get("shop/tester") is None
    assert store.get("shopper/a") is not None


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_get_many_reads_each_key_in_order(backend):
    clock = Clock()
    store = _memory(clock) if backend == "memory" else _redis()[0]
    store.set("shop/a", Workspace(label="a"))
    store.set("shop/c", Workspace(label="c"))
    found = store.get_many(["shop/c", "shop/b", "shop/a"])
    assert [r.label if r else None for r in found] == ["c", None, "a"]
    assert store.get_many([]) == []


@pytest.mark.parametrize("name", ["console", "network", "files", "site-data"])
def test_a_name_a_session_uri_once_started_with_cannot_name_a_session(name):
    from kubed.selenium_flow.workspace.workspaces import Caller

    from .conftest import manager

    with pytest.raises(ValueError):
        manager().session_of(Caller("shop"), name)
    pinned = Caller.from_request({"workspace": ["shop"], "session": [name]}, {})
    with pytest.raises(ValueError):
        _ = pinned.pinned
