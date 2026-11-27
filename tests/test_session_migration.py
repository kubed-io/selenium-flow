"""A workspace's record in the shape before WS-81 is moved once into its
`default` session, at the head of a new list (WS-90)."""

from __future__ import annotations

import json
import time

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from kubed.selenium_flow import faults
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.names import SCREENSHOTS_DIR
from kubed.selenium_flow.principal import ADMIN, Principal
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import DEFAULT_PREFIX, RedisStore
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .conftest import TOKEN, calling_as
from .fakes import FakeGrid, FakeRedis, RecordingActions, untabbed
from .test_golden import Acting, handed_to

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}
GRID = {"grid": {"url": "http://grid.invalid:4444"}}
SITE = "https://shop.example.com/cart"
SUB_SITE = "https://a.example.com/"
SITE_DATA = {
    "cookies": [{"name": "sid", "value": "v", "domain": "shop.example.com", "path": "/"}],
    "origins": {},
    "saved_at": 1.0,
}
SETTINGS = {"browser": "firefox", "width": 1400, "height": 900}
SET_BY = {"browser": "caller", "width": "config", "height": "config"}
DOWNLOAD = {"name": "report.pdf", "size": 3, "creationTime": 1}


def k(key):
    return DEFAULT_PREFIX + key


def old(fake, workspace="shop", owner="sub-a"):
    """Write ``workspace`` as a record before WS-81 kept it: the default's
    fields inline, its owner, its sub-sessions' names, and their records with
    the label each stored."""
    now = time.time()
    record = {
        "session_id": "b-1",
        "opened_at": now - 10,
        "settings": SETTINGS,
        "set_by": SET_BY,
        "site_data": SITE_DATA,
        "history": [
            {"origin": "https://shop.example.com", "url": SITE, "at": now},
            {
                "origin": "https://a.example.com", "url": SUB_SITE,
                "at": now - 1, "session": "tester",
            },
        ],
        "subs": ["tester", "sub-b"],
    }
    if owner is not None:
        record["owner"] = owner
    fake.set(k(workspace), json.dumps(record))
    fake.set(k(f"{workspace}/tester"), sub("b-2", "tester"))
    fake.set(k(f"{workspace}/sub-b"), sub("", "second"))


def sub(session_id, label):
    return json.dumps(
        {"session_id": session_id, "settings": {}, "set_by": {}, "label": label}
    )


def raw(fake, key):
    return json.loads(fake.data[k(key)])


def moved(owner="sub-a", unnamed="default"):
    fake = FakeRedis()
    store = RedisStore(fake)
    old(fake, owner=owner)
    workspaces = Workspaces(RecordingActions(), store, unnamed=unnamed)
    return workspaces, store, fake


def test_an_old_record_moves_into_default_at_the_head():
    workspaces, store, fake = moved()
    record = workspaces.migrated("shop")
    assert record.sessions == ["default", "tester", "sub-b"]
    shared = raw(fake, "shop")
    assert set(shared) == {"sessions", "history"}
    assert shared["sessions"] == ["default", "tester", "sub-b"]
    assert [(v["session"], v["url"]) for v in shared["history"]] == [
        ("default", SITE), ("tester", SUB_SITE),
    ]
    default = raw(fake, "shop/default")
    assert default["session_id"] == "b-1"
    assert (default["settings"], default["set_by"]) == (SETTINGS, SET_BY)
    assert default["site_data"] == SITE_DATA
    assert default["holder"] == "sub-a"
    # Its old label is its name: no subject's own, so held by none, and its
    # record is left as it was until the next subject to open it adopts it.
    assert store.get("shop/tester").holder == ""
    assert raw(fake, "shop/tester") == json.loads(sub("b-2", "tester"))
    assert raw(fake, "shop/sub-b")["holder"] == "sub-b"
    assert "label" not in raw(fake, "shop/sub-b")


def test_a_subjects_own_sub_session_is_held_by_its_subject():
    workspaces, _, fake = moved()
    workspaces.migrated("shop")
    assert raw(fake, "shop/sub-b")["holder"] == "sub-b"


@pytest.mark.parametrize("unnamed", ["admin", "default"])
def test_an_ownerless_record_is_held_by_the_unnamed_subject(unnamed):
    workspaces, _, fake = moved(owner=None, unnamed=unnamed)
    workspaces.migrated("shop")
    assert raw(fake, "shop/default")["holder"] == unnamed
    assert workspaces.store.get("shop/tester").holder == ""
    assert raw(fake, "shop/sub-b")["holder"] == "sub-b"


@pytest.mark.parametrize(
    "who",
    [
        Principal("oidc", subject="sub-c"),
        Principal("oidc", subject="sub-d", username="carol"),
    ],
    ids=["no-username", "username-is-the-name"],
)
def test_a_jwts_own_sub_session_labelled_by_its_name_is_reached_and_adopted(who):
    """The old code labelled a subject's own sub-session by its username,
    else its subject: when that is the session's name, the move cannot tell
    whose it was, so it holds it by none, and its JWT reaches it with no 403
    and holds it once it opens it."""
    workspaces, store, fake = moved()
    own = sessions.own_name(who)
    fake.set(k(f"shop/{own}"), sub("b-3", own))
    record = json.loads(fake.data[k("shop")])
    fake.set(k("shop"), json.dumps({**record, "subs": [*record["subs"], own]}))
    workspaces.migrated("shop")
    key = f"shop/{own}"
    assert store.get(key).holder == ""
    caller = Caller("shop", "query", principal=who)
    assert workspaces.session_of(caller) == key
    assert workspaces.reading(caller) == key
    assert workspaces.open_browser(caller)["session"] == own
    assert store.get(key).holder == who.subject
    with pytest.raises(faults.Held):
        workspaces.session_of(Caller("shop", "query", principal=ADMIN), own)


def test_the_server_picks_the_unnamed_subject():
    token = {"auth": {"token": TOKEN}}
    assert SeleniumMCP({**GRID, **token}).workspaces.unnamed == "admin"
    assert SeleniumMCP({**GRID}).workspaces.unnamed == "default"
    stdio = {**GRID, **token, "serving": {"transport": "stdio"}}
    assert SeleniumMCP(stdio).workspaces.unnamed == "default"


def test_migrating_twice_changes_nothing():
    workspaces, _, fake = moved()
    workspaces.migrated("shop")
    keys = ("shop", "shop/default", "shop/tester", "shop/sub-b")
    once = {key: fake.data[k(key)] for key in keys}
    again = workspaces.migrated("shop")
    assert {key: fake.data[k(key)] for key in keys} == once
    assert again.sessions == ["default", "tester", "sub-b"]


def test_a_move_interrupted_before_the_flip_is_finished_by_the_next_read(monkeypatch):
    workspaces, store, fake = moved()
    real, raised = store.change, []

    def change(key, fn, **kwargs):
        if key == "shop" and not raised:
            raised.append(key)
            raise ConnectionError("redis went away")
        return real(key, fn, **kwargs)

    monkeypatch.setattr(store, "change", change)
    with pytest.raises(ConnectionError):
        workspaces.migrated("shop")
    assert raised == ["shop"], "step 4 was the one cut short"
    assert "sessions" not in raw(fake, "shop"), "the shape did not change"
    written = fake.data[k("shop/default")]
    assert raw(fake, "shop/default")["holder"] == "sub-a"
    assert workspaces.migrated("shop").sessions == ["default", "tester", "sub-b"]
    assert raw(fake, "shop")["sessions"] == ["default", "tester", "sub-b"]
    assert fake.data[k("shop/default")] == written
    assert raw(fake, "shop/sub-b")["holder"] == "sub-b"


def test_two_moves_at_once_leave_one_result(monkeypatch):
    workspaces, store, fake = moved()
    real, raced = store.change, []

    def change(key, fn, **kwargs):
        # The other move runs whole just as this one's flip is about to land.
        if key == "shop" and not raced:
            raced.append(key)
            other = workspaces.migrated("shop")
            assert other.sessions == ["default", "tester", "sub-b"]
        return real(key, fn, **kwargs)

    monkeypatch.setattr(store, "change", change)
    record = workspaces.migrated("shop")
    assert raced == ["shop"], "the race was run"
    assert record.sessions == ["default", "tester", "sub-b"]
    assert raw(fake, "shop")["sessions"] == ["default", "tester", "sub-b"]
    assert raw(fake, "shop/default")["session_id"] == "b-1"
    assert raw(fake, "shop/default")["holder"] == "sub-a"
    assert raw(fake, "shop/sub-b")["holder"] == "sub-b"


def test_a_move_after_an_old_pod_rewrote_the_record_keeps_every_session():
    workspaces, _, fake = moved()
    workspaces.migrated("shop")
    # An old pod read the moved record as an empty default with no
    # sub-sessions, and wrote it back in its own shape.
    fake.set(k("shop"), json.dumps({"session_id": "", "settings": {}, "set_by": {},
                                    "history": raw(fake, "shop")["history"]}))
    listed = workspaces.migrated("shop").sessions
    assert listed[0] == "default"
    assert sorted(listed) == ["default", "sub-b", "tester"]
    assert raw(fake, "shop")["sessions"] == listed
    assert raw(fake, "shop/default")["session_id"] == "b-1", "the default is not rewritten"


def test_a_visit_to_an_old_record_moves_it_first():
    workspaces, _, fake = moved()
    workspaces._visit("shop/default", "https://example.com/next")
    shared = raw(fake, "shop")
    assert shared["sessions"] == ["default", "tester", "sub-b"]
    assert shared["history"][0]["url"] == "https://example.com/next"
    assert raw(fake, "shop/default")["session_id"] == "b-1"


def test_the_admins_history_clear_on_an_old_record_moves_it_first():
    fake = FakeRedis()
    server = SeleniumMCP({**GRID, "auth": {"token": TOKEN}}, store=RedisStore(fake))
    old(fake, "shop")
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    cleared = client.delete("/admin/workspaces/shop/history")
    assert cleared.status_code == 200, cleared.text
    assert raw(fake, "shop")["sessions"] == ["default", "tester", "sub-b"]
    assert raw(fake, "shop/default")["session_id"] == "b-1"


@pytest.mark.parametrize("kept", [False, True])
def test_a_migrated_default_has_the_heads_lifetime(kept):
    workspaces, store, fake = moved()
    store.kept = lambda workspace: kept
    workspaces.migrated("shop")
    assert fake.expiries[k("shop/default")] == (None if kept else store.ttl)
    assert fake.expiries[k("shop")] == (None if kept else store.ttl)
    # A sub-session the move holds is written with a sub-session's lifetime;
    # one it leaves held by none keeps the one its old pod gave it.
    assert fake.expiries[k("shop/sub-b")] == store.sub_ttl


async def test_kept_state_reads_through_default_after_the_move(tmp_path, monkeypatch):
    fake = FakeRedis()
    old(fake, owner=None)
    server = untabbed(
        SeleniumMCP({**GRID, "data": {"dir": str(tmp_path)}}, store=RedisStore(fake))
    )
    assert "sessions" in raw(fake, "shop"), "moved at boot"
    server.actions.grid = FakeGrid([DOWNLOAD])
    server.actions.grid.alive.add("b-1")
    server.flows.write_file("shop", "kept.txt", b"kept")
    server.flows.write_file("shop", "shot.png", b"png", SCREENSHOTS_DIR)
    calling_as(monkeypatch, "shop")
    async with Client(server.mcp.server) as client:
        downloads = json.loads((await client.read_resource("session://downloads"))[0].text)
        files = json.loads((await client.read_resource("workspace://files"))[0].text)
        current = json.loads((await client.read_resource("workspace://current"))[0].text)
    assert [f["name"] for f in downloads["files"]] == ["report.pdf"]
    assert [f["name"] for f in files["files"]] == ["kept.txt"]
    shots = next(f for f in files["folders"] if f["name"] == SCREENSHOTS_DIR)
    assert shots["count"] == 1
    assert current["session"] == "default"
    assert current["settings"] == SETTINGS
    assert current["site_data"]["sites"] == 1
    assert current["url"] == SITE


def test_boot_moves_every_old_record():
    fake = FakeRedis()
    old(fake, "shop")
    old(fake, "cart")
    SeleniumMCP({**GRID}, store=RedisStore(fake))
    for workspace in ("shop", "cart"):
        assert raw(fake, workspace)["sessions"] == ["default", "tester", "sub-b"]
        assert raw(fake, f"{workspace}/default")["session_id"] == "b-1"


def test_a_record_written_old_after_boot_moves_at_its_first_read():
    fake = FakeRedis()
    server = SeleniumMCP({**GRID, "auth": {"token": TOKEN}}, store=RedisStore(fake))
    workspaces = server.workspaces
    # An old pod writes it during the rollout: the first read moves it.
    old(fake, "shop")
    assert workspaces.reading(Caller("shop", "query", principal=ADMIN)) == "shop/admin"
    assert raw(fake, "shop")["sessions"] == ["default", "tester", "sub-b"]
    # So does the first admin row that lists it.
    old(fake, "cart")
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    rows = client.get("/admin/workspaces").json()["workspaces"]
    row = next(r for r in rows if r["key"] == "cart")
    assert row["session_id"] == "b-1"
    assert [s["name"] for s in row["sessions"]] == ["default", "tester", "sub-b"]
    assert raw(fake, "cart")["sessions"] == ["default", "tester", "sub-b"]


def test_the_token_names_default_to_reach_its_migrated_browser(monkeypatch):
    double = Acting()
    monkeypatch.setattr(Actions, "navigate", handed_to(double, "navigate"))
    fake = FakeRedis()
    old(fake, owner=None)
    server = untabbed(
        SeleniumMCP({**GRID, "auth": {"token": TOKEN}}, store=RedisStore(fake))
    )
    server.actions.grid = double.grid
    double.grid.alive.add("b-1")
    assert raw(fake, "shop/default")["holder"] == "admin"
    client = TestClient(server.mcp.server.http_app(), headers=AUTH)
    answered = client.post(
        "/browser/navigate?workspace=shop",
        json={"url": "https://example.com/a", "session": "default"},
    )
    assert answered.status_code == 200, answered.text
    assert double.calls == [("navigate", "b-1", {"url": "https://example.com/a"})]
    caller = Caller("shop", "query", principal=ADMIN)
    assert server.workspaces.session_of(caller) == "shop/admin"
