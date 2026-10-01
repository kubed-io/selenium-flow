"""The admin API over where a session has been: the History tab's routes
and the two fields each session row carries for it.

One row per host, the current one first, joined with the secrets allowed
there and what the snapshot holds for it; a secret never makes a row; Clear
keeps the current site and touches neither site data nor the browser.
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
NOW = 1_790_800_000.0
SECRETS = {"entries": {
    "app": {"description": "the app", "allowed_urls": ["https://app.example.com"],
            "keys": {"user": {"value": "u"}, "pass": {"value": "p"}}},
    "mail": {"description": "the mailbox", "allowed_urls": ["https://mail.example.org"],
             "keys": {"user": {"value": "me"}}},
    "unvisited": {"description": "a site never reached",
                  "allowed_urls": ["https://never.example.net"],
                  "keys": {"token": {"value": "t"}}},
}}
DATA = {
    "cookies": [
        {"name": "sid", "value": "1", "domain": "app.example.com", "path": "/", "http_only": True},
        {"name": "theme", "value": "dark", "domain": "app.example.com", "path": "/"},
    ],
    "origins": {"https://app.example.com": {"local": {"a": "1", "b": "2"}}},
    "session": {},
    "saved_at": NOW - 30,
}


def visited():
    return (SessionRecord(session_id="")
            .at("https://mail.example.org/inbox", now=NOW - 3600)
            .at("http://app.example.com:8080/dev", now=NOW - 120)
            .at("https://app.example.com/x", now=NOW - 60))


@pytest.fixture
def server():
    s = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, secrets=SECRETS,
    ))
    s.sessions.store.set(KEY, visited().with_site_data(DATA))
    return s


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app(), headers=AUTH)


URL = f"/admin/sessions/{KEY}/history"


def row(client):
    rows = client.get("/admin/sessions").json()["sessions"]
    return next(r for r in rows if r["key"] == KEY)


def test_one_row_per_host_current_first_with_its_latest_page(client):
    body = client.get(URL).json()
    assert body["key"] == KEY
    assert [(s["site"], s["url"], s["at"]) for s in body["sites"]] == [
        ("app.example.com", "https://app.example.com/x", NOW - 60),
        ("mail.example.org", "https://mail.example.org/inbox", NOW - 3600),
    ]


def test_each_row_carries_what_is_saved_for_its_host(client):
    sites = {s["site"]: s for s in client.get(URL).json()["sites"]}
    assert sites["app.example.com"]["saved"] == {"cookies": 2, "local": 2, "session": 0}
    assert sites["mail.example.org"]["saved"] is None


def test_secrets_join_the_hosts_they_allow_and_never_make_a_row(client):
    body = client.get(URL).json()
    sites = {s["site"]: s for s in body["sites"]}
    app = sites["app.example.com"]["secrets"]
    assert [s["name"] for s in app] == ["app"]
    assert app[0]["description"] == "the app" and sorted(app[0]["keys"]) == ["pass", "user"]
    assert [s["name"] for s in sites["mail.example.org"]["secrets"]] == ["mail"]
    assert "never.example.net" not in json.dumps(body)


def test_a_key_with_no_record_has_no_rows(client):
    assert client.get("/admin/sessions/nobody/history").json() == {"key": "nobody", "sites": []}


def test_clear_keeps_the_current_site_and_nothing_else(client, server):
    assert client.delete(URL).json() == {"cleared": ["mail.example.org"]}
    record = server.sessions.store.get(KEY)
    assert record.history == visited().history[:1]
    assert record.site_data == DATA, "site data is not History's to touch"
    assert [s["site"] for s in client.get(URL).json()["sites"]] == ["app.example.com"]


def test_the_routes_need_the_token(server):
    bare = TestClient(server.mcp.http_app())
    assert bare.get(URL).status_code == 401
    assert bare.delete(URL).status_code == 401


def test_the_row_counts_hosts_and_its_rev_follows_the_origins_and_the_top_page(client, server):
    before = row(client)
    assert before["history_count"] == 2
    assert json.loads(before["history_rev"]) == [
        ["https://app.example.com", "http://app.example.com:8080", "https://mail.example.org"],
        "https://app.example.com/x",
    ]
    server.sessions.store.update(KEY, lambda r: r.at("https://mail.example.org/sent", now=NOW))
    moved = row(client)
    assert moved["history_rev"] != before["history_rev"], "another site on top"
    client.delete(URL)
    assert row(client)["history_count"] == 1


def test_a_page_within_the_top_site_moves_the_rev_and_the_clock_alone_does_not(client, server):
    """History's top row is the session card's last page: a navigation within
    the top site must repaint it, and a call that stays put must not."""
    before = row(client)["history_rev"]
    server.sessions.store.update(KEY, lambda r: r.at("https://app.example.com/y", now=NOW))
    within = row(client)["history_rev"]
    assert within != before, "a page within the top site"
    server.sessions.store.update(KEY, lambda r: r.at("https://app.example.com/y", now=NOW + 5))
    assert row(client)["history_rev"] == within, "only the clock moved"
