"""Every caller acts in its own session, and a session is held by the subject
that opened it (WS-82, WS-83, WS-84)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.principal import ADMIN, Principal
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import MemoryStore, RedisStore
from kubed.selenium_flow.workspace.workspaces import Caller

from .conftest import TOKEN, manager
from .fakes import FakeRedis, RecordingActions, record_of, seated, shared_of

pytestmark = pytest.mark.unit

FIRST = Principal("oidc", subject="sub-a", username="first")
SECOND = Principal("oidc", subject="sub-b", username="second")
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def as_(principal=None, workspace="shop"):
    return Caller(workspace, "query", principal=principal)


def pinned(session, principal=None, workspace="shop"):
    caller = Caller.from_request({"workspace": [workspace], "session": [session]}, {})
    return replace(caller, principal=principal)


def built(store=None, actions=None):
    store = store if store is not None else MemoryStore()
    actions = actions or RecordingActions()
    return manager(actions, store), store, actions


def test_an_unnamed_call_acts_in_the_callers_own_session():
    workspaces, _, _ = built()
    for principal, own in ((FIRST, "first"), (ADMIN, "admin"), (None, "default")):
        key = sessions.key_of("shop", own)
        assert workspaces.session_of(as_(principal)) == key
        assert workspaces.reading(as_(principal)) == key


def test_two_subjects_get_two_sessions():
    workspaces, store, actions = built()
    first = workspaces.open_browser(as_(FIRST))
    second = workspaces.open_browser(as_(SECOND))
    assert (first["session"], second["session"]) == ("first", "second")
    assert record_of(store, "shop", "first").holder == "sub-a"
    assert record_of(store, "shop", "second").holder == "sub-b"
    assert shared_of(store, "shop").subs == ["first", "second"]
    assert actions.opened == 2 and actions.closed == []


def test_an_unnamed_call_with_no_session_opens_nothing():
    workspaces, store, actions = built()
    ran = []
    with pytest.raises(faults.NoSession, match="open_session"):
        workspaces.act(as_(FIRST), ran.append, tool="navigate")
    assert ran == [] and actions.opened == 0
    assert record_of(store, "shop", "first") is None
    assert shared_of(store, "shop") is None


def test_an_unnamed_read_with_no_session_answers_empty_and_makes_nothing():
    workspaces, store, _ = built()
    seated(store, "shop", session_id="g-default")
    seated(store, "shop", "tester", holder="sub-b")
    before = {**store.records(), **store.sub_records("shop")}
    context = workspaces.describe(as_(FIRST))
    assert (context["session"], context["default"], context["live"]) == (
        "first", False, False,
    )
    assert context["url"] is None
    assert {**store.records(), **store.sub_records("shop")} == before
    assert shared_of(store, "shop").subs == ["tester"]
    assert record_of(store, "shop", "first") is None


@pytest.mark.parametrize(
    "call",
    [
        lambda w: w.session_of(as_(SECOND), "first"),
        lambda w: w.session_of(pinned("first", SECOND)),
        lambda w: w.reading(as_(SECOND), "first"),
    ],
    ids=["argument", "pin", "reading"],
)
def test_another_subjects_session_is_refused(call):
    workspaces, _, _ = built()
    workspaces.open_browser(as_(FIRST))
    with pytest.raises(faults.Held) as refused:
        call(workspaces)
    assert str(refused.value) == "that session is held by another caller"
    assert errors.status_for(refused.value) == 403


def test_a_refused_open_makes_nothing():
    workspaces, store, actions = built()
    workspaces.open_browser(as_(FIRST))
    before = record_of(store, "shop", "first")
    with pytest.raises(faults.Held):
        workspaces.open_browser(as_(SECOND), session="first")
    assert record_of(store, "shop", "first") == before
    assert record_of(store, "shop", "first").holder == "sub-a"
    assert actions.opened == 1 and actions.closed == []


def test_the_same_subject_shares_its_session():
    workspaces, store, _ = built()
    one = as_(FIRST)
    other = replace(
        Caller.from_request({}, {"sf-workspace": ["shop"]}),
        principal=Principal("oidc", subject="sub-a", username="first"),
    )
    key = sessions.key_of("shop", "first")
    assert workspaces.open_browser(one)["session"] == "first"
    assert workspaces.session_of(other) == key
    assert workspaces.act(other, lambda sid: {"acted_on": sid})["acted_on"] == (
        record_of(store, "shop", "first").session_id
    )
    assert workspaces.open_browser(other, session="first")["session"] == "first"
    assert record_of(store, "shop", "first").holder == "sub-a"


def test_an_admin_route_is_not_held():
    from .test_admin_live import build

    server = build()
    store = server.workspaces.store
    seated(
        store, "w1", "first", holder="sub-a", session_id="g-first",
        ran={"live_view": True},
        settings={"capture": True}, set_by={"capture": "caller"},
    )
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    base = "/admin/workspaces/w1"
    picked = {"session": "first"}
    # The admin's reads of it: its row, its capture and its live view.
    row = client.get(f"{base}/session", params=picked)
    assert row.status_code == 200, row.text
    assert row.json()["session_id"] == "g-first"
    assert client.get(f"{base}/console", params=picked).status_code == 200
    live = client.get(f"{base}/live", params=picked)
    assert live.status_code == 200, live.text
    # Its End.
    ended = client.delete(f"{base}/session", params=picked)
    assert ended.status_code == 200, ended.text
    assert ended.json()["session_id"] == "g-first"
    assert not record_of(store, "w1", "first").attached
    assert record_of(store, "w1", "first").holder == "sub-a"
    # And End all.
    store.update("w1/first", lambda r: replace(r, session_id="g-again"))
    every = client.post(f"{base}/end-all")
    assert every.status_code == 200, every.text
    assert every.json()["ended"] == 2
    assert not record_of(store, "w1", "first").attached


def test_an_invalid_own_name_is_refused_with_a_400():
    workspaces, store, _ = built()
    caller = as_(Principal("oidc", subject="auth0|1"))
    with pytest.raises(ValueError) as refused:
        workspaces.session_of(caller)
    assert str(refused.value) == sessions.OWN_UNNAMED
    assert errors.status_for(refused.value) == 400
    assert shared_of(store, "shop") is None
    assert workspaces.session_of(caller, "x", opening=True) == "shop/x"
    assert record_of(store, "shop", "x").holder == "auth0|1"


def test_a_username_another_holds_is_refused():
    workspaces, store, actions = built()
    assert workspaces.open_browser(as_(ADMIN))["session"] == "admin"
    impostor = as_(Principal("oidc", subject="sub-c", username="admin"))
    with pytest.raises(faults.Held):
        workspaces.session_of(impostor)
    with pytest.raises(faults.Held):
        workspaces.open_browser(impostor)
    assert record_of(store, "shop", "admin").holder == "admin"
    assert actions.opened == 1


@pytest.mark.parametrize("backend", ["memory", "redis"])
def test_two_subjects_racing_one_name_leave_one_holder(backend, monkeypatch):
    store = MemoryStore() if backend == "memory" else RedisStore(FakeRedis())
    workspaces, _, _ = built(store)
    # FIRST's record lands after SECOND's check and before SECOND's write.
    workspaces.session_of(as_(FIRST), opening=True)
    real, missed = store.get, []

    def get(key):
        if not missed:
            missed.append(key)
            return None
        return real(key)

    monkeypatch.setattr(store, "get", get)
    with pytest.raises(faults.Held):
        workspaces.session_of(as_(SECOND), "first", opening=True)
    assert missed == ["shop/first"], "SECOND's check saw no record"
    monkeypatch.undo()
    assert record_of(store, "shop", "first").holder == "sub-a"
    assert shared_of(store, "shop").subs == ["first"]


def test_the_token_reaches_admin_over_http(server):
    server.workspaces.actions = RecordingActions()
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    opened = client.post("/browser?workspace=shop", json={})
    assert opened.status_code == 200, opened.text
    assert opened.json()["session"] == "admin"
    assert record_of(server.workspaces.store, "shop", "admin").holder == "admin"
