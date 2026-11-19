"""The admin API over sub-sessions: the list holds workspaces with their
sessions, every session-scoped route takes ``?session=``, and End all ends them
every one (WS-74, WS-77, UI-64, UI-65)."""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlsplit

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace import sessions
from kubed.selenium_flow.workspace.store import Workspace

from .conftest import TOKEN

pytestmark = pytest.mark.unit

AUTH = {"Authorization": f"Bearer {TOKEN}"}
WS = "desk"
TESTER = sessions.key_of(WS, "tester")
A_SITE = "https://a.example.test/"
B_SITE = "https://b.example.test/"
NOW = 1_790_800_000.0


def visit(url, session, at):
    return {"origin": url.rstrip("/"), "url": url, "at": at, "session": session}


class Grid:
    """Every browser lives; nothing else is asked of it."""

    def sessions(self):
        return [{"session_id": "g1"}, {"session_id": "g2"}]

    def is_alive(self, session_id):
        return True

    def files(self, session_id):
        return []


@pytest.fixture
def server():
    s = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN},
    ))
    s.actions.grid = s.workspaces.actions.grid = Grid()
    ended = []
    s.actions.end_browser = ended.append
    s.ended = ended
    store = s.workspaces.store
    store.set(WS, Workspace(
        session_id="g1", opened_at=NOW, subs=["tester"],
        settings={"capture": True, "browser": "firefox"},
        history=[visit(B_SITE, "tester", NOW - 5), visit(A_SITE, "default", NOW - 10)],
        site_data={
            "cookies": [
                {"name": "a", "value": "1", "domain": "a.example.test", "path": "/"},
                {"name": "b", "value": "1", "domain": "b.example.test", "path": "/"},
            ],
            "origins": {}, "session": {}, "saved_at": NOW,
        },
    ))
    store.set(TESTER, Workspace(
        session_id="g2", opened_at=NOW, label="tester",
        settings={"capture": True, "browser": "chrome"},
    ))
    # A workspace that exists only because a sub-session was opened in it.
    store.set("fresh", Workspace(subs=["only"]))
    store.set(sessions.key_of("fresh", "only"), Workspace(label="only"))
    return s


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app(), headers=AUTH)


def rows(client):
    return {r["key"]: r for r in client.get("/admin/workspaces").json()["workspaces"]}


def test_admin_lists_sub_sessions_under_their_workspace(client):
    listed = rows(client)
    assert set(listed) == {WS, "fresh"}, "no sub-session is a row of its own"
    assert listed[WS]["sessions"] == [
        {"name": "default", "label": "default", "default": True, "live": True,
         "browser": "firefox"},
        {"name": "tester", "label": "tester", "default": False, "live": True,
         "browser": "chrome"},
    ]
    # Only because a sub was opened: no browser, no owner, still listed cleanly.
    assert listed["fresh"]["sessions"] == [
        {"name": "default", "label": "default", "default": True, "live": False,
         "browser": "chrome"},
        {"name": "only", "label": "only", "default": False, "live": False,
         "browser": "chrome"},
    ]


def test_admin_session_routes_take_session(server, client):
    base = f"/admin/workspaces/{WS}"
    # The Session row of the picked session.
    default = client.get(f"{base}/session").json()
    assert (default["session"], default["session_id"], default["browser"]) == (
        "default", "g1", "firefox")
    there = client.get(f"{base}/session", params={"session": "tester"}).json()
    assert (there["session"], there["session_id"], there["browser"], there["label"]) == (
        "tester", "g2", "chrome", "tester")
    # Capture is the picked session's.
    for route in ("console", "network"):
        assert client.get(f"{base}/{route}", params={"session": "tester"}).status_code == 200
    seen = []
    real = server.workspaces.captured
    server.workspaces.captured = lambda key, kind, **q: seen.append(key) or real(key, kind, **q)
    client.get(f"{base}/console")
    client.get(f"{base}/console", params={"session": "tester"})
    assert seen == [WS, TESTER]
    # An unknown name is a 404, a reserved or malformed one a 400, on every route.
    for method, route in (
        ("get", "session"), ("get", "console"), ("get", "network"),
        ("get", "site-data"), ("delete", "session"),
    ):
        call = getattr(client, method)
        assert call(f"{base}/{route}", params={"session": "typo"}).status_code == 404, route
        assert call(f"{base}/{route}", params={"session": "files"}).status_code == 400, route
        assert call(f"{base}/{route}", params={"session": "a b!"}).status_code == 400, route


def test_ending_one_session_leaves_the_others(server, client):
    done = client.delete(f"/admin/workspaces/{WS}/session", params={"session": "tester"})
    assert done.json() == {"success": True, "key": WS, "session_id": "g2"}
    assert server.ended == ["g2"]
    store = server.workspaces.store
    assert store.get(TESTER).attached is False and store.get(WS).attached is True


def test_end_all_ends_every_sessions_browser(server, client):
    done = client.post(f"/admin/workspaces/{WS}/end-all")
    assert done.status_code == 200 and done.json()["ended"] == 2
    assert sorted(server.ended) == ["g1", "g2"]
    store = server.workspaces.store
    # Every record is kept, detached.
    assert store.get(WS).attached is False and store.get(TESTER).attached is False
    assert store.get(WS).subs == ["tester"]
    # Nothing left to end answers 0, and a second press ends nothing.
    assert client.post(f"/admin/workspaces/{WS}/end-all").json()["ended"] == 0
    assert client.post("/admin/workspaces/fresh/end-all").json()["ended"] == 0
    assert client.post("/admin/workspaces/nobody/end-all").json()["ended"] == 0


def test_an_admin_link_to_a_sub_sessions_live_view_is_scoped_to_it(client):
    """The tail link is signed for one session: it opens no other."""
    body = client.get(f"/admin/workspaces/{WS}/console", params={"session": "tester"}).json()
    parts = urlsplit(body["tail_url"])
    query = dict(parse_qsl(parts.query))
    assert parts.path == f"/admin/workspaces/{WS}/tail"
    assert query["session"] == "tester"
    stranger = f"{parts.path}?exp={query['exp']}&sig={query['sig']}&kind=console"
    anon = TestClient(client.app)
    assert anon.get(stranger, params={"session": "other"}).status_code in (401, 403, 404)
    assert anon.get(stranger).status_code in (401, 403)


def test_the_history_rows_carry_their_session(client):
    sites = client.get(f"/admin/workspaces/{WS}/history").json()["sites"]
    assert {s["site"]: s["session"] for s in sites} == {
        "b.example.test": "tester", "a.example.test": "default"}


def test_site_data_orders_and_counts_by_the_picked_sessions_history(client):
    base = f"/admin/workspaces/{WS}/site-data"
    default = client.get(base).json()
    tester = client.get(base, params={"session": "tester"}).json()
    assert [s["site"] for s in default["sites"]] == ["a.example.test", "b.example.test"]
    assert [s["site"] for s in tester["sites"]] == ["b.example.test", "a.example.test"]
    listed = rows(client)[WS]
    assert listed["history_count"] == 2
    assert listed["site_data_count"] == 2


def test_forgetting_a_workspace_forgets_its_sub_sessions(server):
    server.workspaces.forget(WS)
    store = server.workspaces.store
    assert store.get(WS) is None and store.get(TESTER) is None
    assert store.sub_records(WS) == {}
    assert store.get("fresh") is not None


def test_admin_logs_never_name_a_sub_session(server, client, caplog):
    caplog.set_level(logging.DEBUG)
    client.delete(f"/admin/workspaces/{WS}/session", params={"session": "tester"})
    client.get(f"/admin/workspaces/{WS}/console", params={"session": "typo"})
    client.get(f"/admin/workspaces/{WS}/site-data", params={"session": "typo"})
    ours = " ".join(
        r.getMessage() for r in caplog.records if r.name.startswith("kubed.")
    )
    assert "ended the browser" in ours and "refused" in ours
    assert "tester" not in ours and "typo" not in ours
