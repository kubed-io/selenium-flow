"""The admin API over a session's snapshot: the Site data tab's routes and
the two fields each session row carries for it.

Only what the snapshot holds is listed — no secrets, which are History's —
hosts the session went to first; an httpOnly value is never shown; Forget
and Clear change the snapshot alone, never the history or the browser.
"""

import json

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.store import SessionRecord
from kubed.selenium_flow.site_data import snapshot as site_data

from .conftest import TOKEN

pytestmark = pytest.mark.unit

KEY = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DATA = {
    "cookies": [
        {"name": "sid", "value": "RAW-SECRET", "domain": "app.example.com",
         "path": "/", "http_only": True},
        {"name": "theme", "value": "dark", "domain": "app.example.com", "path": "/"},
        {"name": "shared", "value": "s", "domain": ".example.com", "path": "/"},
        {"name": "ad", "value": "1", "domain": "ads.example.net", "path": "/"},
    ],
    "origins": {"https://app.example.com": {"local": {"a": "1"}}},
    "session": {"origin": "https://app.example.com", "items": {"s": "1"}},
    "saved_at": 10.0,
}


def visited():
    return (SessionRecord(session_id="")
            .visited("https://mail.example.org/", now=5.0)
            .visited("https://app.example.com/x", now=6.0))


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
    s.sessions.store.set(KEY, visited().with_site_data(DATA))
    return s


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app(), headers=AUTH)


def url(suffix=""):
    return f"/admin/sessions/{KEY}/site-data{suffix}"


def row(client):
    rows = client.get("/admin/sessions").json()["sessions"]
    return next(r for r in rows if r["key"] == KEY)


def test_the_list_is_the_snapshot_by_host_history_hosts_first_and_no_secrets(client):
    body = client.get(url()).json()
    assert body["key"] == KEY and body["saved_at"] == 10.0
    assert [s["site"] for s in body["sites"]] == ["app.example.com", "ads.example.net"]
    assert set(body["details"]) == {"app.example.com", "ads.example.net"}
    assert "mail.example.org" not in json.dumps(body), "a secret never makes a row here"
    assert "secrets" not in json.dumps(body)


def test_details_mask_http_only_values_and_show_both_storages(client):
    detail = client.get(url()).json()["details"]["app.example.com"]
    assert {c["name"]: c["value"] for c in detail["cookies"]} == {
        "sid": "•••", "theme": "dark", "shared": "s",
    }
    assert detail["storage"] == [{"origin": "https://app.example.com",
                                  "local_storage": {"a": "1"}, "session_storage": {"s": "1"}}]
    assert "RAW-SECRET" not in client.get(url()).text


def test_a_key_with_no_record_has_nothing_saved(client):
    assert client.get("/admin/sessions/nobody/site-data").json() == {
        "key": "nobody", "sites": [], "saved_at": None, "uri": "session://site-data",
        "details": {},
    }


def test_forget_takes_one_host_and_leaves_shared_cookies_and_the_history(client, server):
    gone = client.delete(url("/app.example.com")).json()["forgotten"]
    assert gone == {
        "site": "app.example.com", "cookies": ["sid", "theme"],
        "origins": ["https://app.example.com"],
        "kept_shared": [{"name": "shared", "domain": ".example.com", "path": "/"}],
    }
    record = server.sessions.store.get(KEY)
    assert [c["name"] for c in record.site_data["cookies"]] == ["shared", "ad"]
    assert record.site_data["origins"] == {} and record.site_data["session"] == {}
    assert record.history == visited().history, "History is not Site data's to touch"
    second = client.delete(url("/app.example.com"))
    assert second.status_code == 404
    assert second.json() == {"error": "no saved site data for app.example.com"}


def test_clear_deletes_the_snapshot_and_keeps_the_history(client, server):
    assert client.delete(url()).json() == {"cleared": ["app.example.com", "ads.example.net"]}
    record = server.sessions.store.get(KEY)
    assert record.site_data == {}
    assert record.history == visited().history
    assert client.get(url()).json()["sites"] == []
    assert client.delete(url()).json() == {"cleared": []}, "nothing left is not an error"


def test_the_routes_need_the_token(server):
    bare = TestClient(server.mcp.http_app())
    assert bare.get(url()).status_code == 401
    assert bare.delete(url("/app.example.com")).status_code == 401
    assert bare.delete(url()).status_code == 401


def test_the_row_counts_hosts_and_its_rev_moves_on_a_save_a_forget_and_a_clear(client, server):
    before = row(client)
    assert before["site_data_count"] == 2
    client.delete(url("/ads.example.net"))
    forgot = row(client)
    assert forgot["site_data_count"] == 1
    assert forgot["site_data_rev"] != before["site_data_rev"]
    record = server.sessions.store.get(KEY)
    server.sessions.store.set(KEY, record.with_site_data({**record.site_data, "saved_at": 20.0}))
    resaved = row(client)
    assert resaved["site_data_rev"] != forgot["site_data_rev"], "a re-save of the same hosts moves it"
    client.delete(url())
    assert row(client)["site_data_count"] == 0


def test_details_say_what_forget_takes_and_what_it_leaves(client):
    d = client.get(url()).json()["details"]["app.example.com"]
    assert d["own_cookies"] == ["sid", "theme"]
    assert d["kept_shared"] == [{"name": "shared", "domain": ".example.com", "path": "/"}]


def test_a_parent_only_row_can_be_forgotten_and_a_covered_host_cannot(server):
    server.sessions.store.set(KEY, visited().with_site_data({
        "cookies": [{"name": "shared", "value": "s", "domain": ".example.org", "path": "/"}],
        "origins": {}, "session": {}, "saved_at": 10.0,
    }))
    client = TestClient(server.mcp.http_app(), headers=AUTH)
    body = client.get(url()).json()
    assert [s["site"] for s in body["sites"]] == ["example.org"]
    assert body["details"]["example.org"]["own_cookies"] == ["shared"]
    assert client.delete(url("/mail.example.org")).status_code == 404, "covered, nothing of its own"
    assert client.delete(url("/example.org")).status_code == 200
    assert row(client)["site_data_count"] == 0


def test_forget_answers_404_through_the_central_policy(client):
    from kubed.selenium_flow import errors, faults

    assert errors.status_for(faults.NotFound("x")) == 404
    body = client.delete(url("/nothing.example.net"))
    assert body.status_code == 404
    assert body.json() == {"error": "no saved site data for nothing.example.net"}


def test_the_payload_comes_from_one_grouping_of_the_jar(server, monkeypatch):
    cookies = [{"name": "c", "value": "v", "domain": f"h{i}.example{i}.com", "path": "/"}
               for i in range(300)]
    server.sessions.store.set(KEY, visited().with_site_data({
        "cookies": cookies, "origins": {}, "session": {}, "saved_at": 1.0}))
    calls = {"n": 0}
    real = site_data._Jar.covering

    def counting(self, host):
        calls["n"] += 1
        return real(self, host)

    monkeypatch.setattr(site_data._Jar, "covering", counting)
    body = TestClient(server.mcp.http_app(), headers=AUTH).get(url()).json()
    assert len(body["details"]) >= 300
    assert calls["n"] <= len(body["details"]) + 5


def test_forget_keeps_a_save_that_lands_while_it_works(client, server, monkeypatch):
    """The handler used to read the record, work, and set it back whole: a save
    landing in between was reverted. The save here runs on another thread the
    moment Forget starts computing, and must survive it."""
    import threading

    store = server.sessions.store
    real = site_data.forget
    saver = []

    def save(r):
        origins = {**r.site_data["origins"], "https://late.example.net": {"local": {"b": "2"}}}
        return r.with_site_data({**r.site_data, "origins": origins})

    def forget(data, host):
        if not saver:
            saver.append(threading.Thread(target=store.update, args=(KEY, save)))
            saver[0].start()
            saver[0].join(0.3)
        return real(data, host)

    monkeypatch.setattr(site_data, "forget", forget)
    assert client.delete(url("/app.example.com")).status_code == 200
    saver[0].join(5)
    assert list(store.get(KEY).site_data["origins"]) == ["https://late.example.net"]
