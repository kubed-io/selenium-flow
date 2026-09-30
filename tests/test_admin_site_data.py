"""The admin API over a session's site data: the Site data tab's two routes
and the two fields each session row carries.

A secret-only site is listed though nothing is saved for it, an httpOnly value
is never shown, and Forget leaves the parent-domain cookies other sites use.
"""

import json

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.store import SessionRecord

from .conftest import TOKEN

pytestmark = pytest.mark.unit

KEY = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DATA = {
    "cookies": [
        {"name": "sid", "value": "RAW-SECRET", "domain": "app.example.com",
         "path": "/", "http_only": True},
        {"name": "theme", "value": "dark", "domain": "app.example.com",
         "path": "/"},
        {"name": "shared", "value": "s", "domain": ".example.com", "path": "/"},
    ],
    "origins": {
        "https://app.example.com": {
            "local": {"a": "1"}, "session": {}, "saved_at": 10.0,
        },
    },
    "saved_at": 10.0,
    # The shape SessionManager writes: a list of origins, not a mapping.
    "pending": {"browser": "b-1", "origins": ["https://app.example.com", "https://keep.dev"],
                "script": "preload-1", "announce": False},
}


@pytest.fixture
def server():
    s = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        secrets={"entries": {"mail": {
            "description": "the mailbox",
            "allowed_urls": ["https://mail.example.org"],
            "keys": {"user": {"value": "me"}},
        }}},
    ))
    s.sessions.store.set(KEY, SessionRecord(session_id="", site_data=DATA))
    return s


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app(), headers=AUTH)


def url(suffix=""):
    return f"/admin/sessions/{KEY}/site-data{suffix}"


def test_the_list_has_saved_sites_and_a_secret_only_row(client):
    body = client.get(url()).json()
    assert body["key"] == KEY
    by = {s["site"]: s for s in body["sites"]}
    assert by["app.example.com"]["saved"] is True
    assert by["mail.example.org"]["saved"] is False
    assert by["mail.example.org"]["secrets"][0]["name"] == "mail"
    assert body["saved_sites"] == 1
    assert set(body["details"]) == set(by)
    assert "pending" not in json.dumps(body)


def test_details_mask_http_only_values(client):
    cookies = client.get(url()).json()["details"]["app.example.com"]["cookies"]
    shown = {c["name"]: c["value"] for c in cookies}
    assert shown["sid"] == "•••"
    assert shown["theme"] == "dark"
    assert "RAW-SECRET" not in client.get(url()).text


def test_a_key_with_no_record_still_lists_secrets(client):
    body = client.get("/admin/sessions/nobody/site-data").json()
    assert body["saved_sites"] == 0
    assert [s["site"] for s in body["sites"]] == ["mail.example.org"]


def test_forget_keeps_shared_cookies_and_the_secret_row(client, server):
    gone = client.delete(url("/app.example.com")).json()["forgotten"]
    assert gone["site"] == "app.example.com"
    assert gone["cookies"] == ["sid", "theme"]
    assert gone["kept_shared"] == ["shared"]
    left = server.sessions.store.get(KEY).site_data
    assert [c["name"] for c in left["cookies"]] == ["shared"]
    assert left["origins"] == {}
    assert left["pending"] == {"browser": "b-1", "origins": ["https://keep.dev"],
                               "script": "preload-1", "announce": False}
    sites = {s["site"] for s in client.get(url()).json()["sites"]}
    assert "mail.example.org" in sites
    second = client.delete(url("/app.example.com"))
    assert second.status_code == 404
    assert second.json() == {"error": "no saved site data for app.example.com"}


def test_the_routes_need_the_token(server):
    bare = TestClient(server.mcp.http_app())
    assert bare.get(url()).status_code == 401
    assert bare.delete(url("/app.example.com")).status_code == 401


def test_the_row_carries_a_count_and_a_rev_that_move_on_forget(client):
    def row():
        rows = client.get("/admin/sessions").json()["sessions"]
        return next(r for r in rows if r["key"] == KEY)

    before = row()
    assert before["site_data_count"] == 1
    saved = json.loads(before["site_data_rev"])[1]
    assert saved[0][0] == "app.example.com"
    client.delete(url("/app.example.com"))
    after = row()
    # The shared cookie still stands as a site of its own.
    assert [r[0] for r in json.loads(after["site_data_rev"])[1]] == ["example.com"]
    assert after["site_data_rev"] != before["site_data_rev"]


def test_a_cookie_only_resave_moves_the_rev(client, server):
    def rev():
        rows = client.get("/admin/sessions").json()["sessions"]
        return next(r for r in rows if r["key"] == KEY)["site_data_rev"]

    before = rev()
    record = server.sessions.store.get(KEY)
    # Same hosts, same counts, the origin's own time untouched: only the
    # top-level time says the jar was saved again.
    resaved = {**record.site_data, "saved_at": 20.0}
    server.sessions.store.set(KEY, record.with_site_data(resaved))
    assert rev() != before


def test_details_say_what_forget_takes_and_what_it_leaves(client):
    d = client.get(url()).json()["details"]["app.example.com"]
    assert d["own_cookies"] == ["sid", "theme"]
    assert d["kept_shared"] == ["shared"]


def test_a_row_under_only_a_parent_cookie_has_nothing_to_forget(server):
    record = SessionRecord(session_id="", site_data={
        "cookies": [{"name": "shared", "value": "s", "domain": ".example.org", "path": "/"}],
        "origins": {}, "saved_at": 10.0,
    })
    server.sessions.store.set(KEY, record)
    client = TestClient(server.mcp.http_app(), headers=AUTH)
    body = client.get(url()).json()
    by = {s["site"]: s for s in body["sites"]}
    # mail.example.org is listed for its secret and sits under .example.org.
    assert by["mail.example.org"]["saved"] is False
    assert by["example.org"]["saved"] is True
    assert body["saved_sites"] == 1
    assert body["details"]["example.org"]["own_cookies"] == ["shared"]
    assert client.delete(url("/mail.example.org")).status_code == 404


def test_a_parent_only_row_can_be_forgotten(client):
    client.delete(url("/app.example.com"))
    assert client.delete(url("/example.com")).status_code == 200
    rows = client.get("/admin/sessions").json()["sessions"]
    assert next(r for r in rows if r["key"] == KEY)["site_data_count"] == 0
