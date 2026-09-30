"""Site data: what a session saves for the sites it signed in to, and how
it is shown. Pure logic only; BiDi is Task 2's."""

import json

import pytest

from kubed.selenium_flow.core import site_data as sd
from kubed.selenium_flow.session.store import SessionRecord

pytestmark = pytest.mark.unit

NOW = 1_790_800_000.0


def cookie(name, domain, value="v", http_only=False, expiry=None, secure=True):
    return {
        "name": name, "value": value, "value_type": "string", "domain": domain,
        "path": "/", "http_only": http_only, "secure": secure,
        "same_site": "lax", "expiry": expiry,
    }


def captured(origin="https://app.example.com", local=None, session=None, cookies=None):
    return {
        "cookies": cookies if cookies is not None else [cookie("sid", "app.example.com")],
        "origin": origin, "local": local or {}, "session": session or {},
    }


def test_origin_of_matches_location_origin():
    assert sd.origin_of("https://App.Example.com:443/x?y#z") == "https://app.example.com"
    assert sd.origin_of("http://localhost:3000/a") == "http://localhost:3000"
    assert sd.origin_of("http://x.test:80/") == "http://x.test"
    assert sd.origin_of("about:blank") == ""
    assert sd.origin_of("") == ""


def test_a_save_replaces_cookies_and_adds_its_origin():
    first, _ = sd.merge({}, captured(local={"theme": "dark"}), NOW)
    second, saved = sd.merge(
        first,
        captured(origin="https://sso.example.com", cookies=[cookie("kc", "sso.example.com")]),
        NOW + 5,
    )
    assert [c["name"] for c in second["cookies"]] == ["kc"], "cookies are the whole jar, replaced"
    assert set(second["origins"]) == {"https://app.example.com", "https://sso.example.com"}
    assert second["origins"]["https://app.example.com"]["local"] == {"theme": "dark"}
    assert saved == {"cookies": 1, "sites": ["https://sso.example.com"], "skipped": []}


def test_a_page_with_no_origin_saves_cookies_only():
    data, saved = sd.merge({}, captured(origin=""), NOW)
    assert data["origins"] == {}
    assert saved["sites"] == []


def test_the_restore_marker_is_never_saved():
    data, _ = sd.merge(
        {}, captured(session={"selenium-flow:restored:https://app.example.com": "1", "k": "v"}), NOW
    )
    assert data["origins"]["https://app.example.com"]["session"] == {"k": "v"}


def test_a_save_over_the_cap_keeps_cookies_and_skips_that_storage():
    big = {"blob": "x" * (sd.MAX_BYTES + 1)}
    data, saved = sd.merge({}, captured(local=big), NOW)
    assert data["cookies"] and data["origins"] == {}
    assert saved["skipped"] == [
        {"site": "https://app.example.com", "reason": "storage over 1000000 bytes"}
    ]


def test_merge_keeps_pending():
    data, _ = sd.merge({"pending": {"browser": "b", "origins": [], "script": ""}}, captured(), NOW)
    assert data["pending"]["browser"] == "b"


def test_expired_cookies_are_dropped_and_session_cookies_kept():
    kept = sd.live_cookies(
        [cookie("old", "a.test", expiry=int(NOW) - 1), cookie("new", "a.test", expiry=int(NOW) + 60),
         cookie("session", "a.test", expiry=None)],
        NOW,
    )
    assert [c["name"] for c in kept] == ["new", "session"]


def test_the_preload_script_fills_only_its_own_origin_once_per_tab():
    src = sd.preload_source({"https://app.example.com": {"local": {"a": "1"}, "session": {"b": "2"}}})
    assert src.startswith("() =>")
    assert "location.origin" in src
    assert "selenium-flow:restored:" in src
    assert json.dumps({"a": "1"}) in src


def test_the_view_groups_by_host_and_shares_parent_cookies():
    data, _ = sd.merge(
        {},
        captured(
            local={"theme": "dark"},
            cookies=[
                cookie("sid", "app.example.com", value="secret", http_only=True),
                cookie("ab", ".example.com", value="b1"),
                cookie("kc", "sso.example.com"),
            ],
        ),
        NOW,
    )
    listing = sd.view(data)
    sites = {s["site"]: s for s in listing["sites"]}
    assert set(sites) == {"app.example.com", "sso.example.com"}
    assert sites["app.example.com"]["origin"] == "https://app.example.com"
    assert sites["app.example.com"]["cookies"] == 2, "its own and the shared one"
    assert sites["sso.example.com"]["cookies"] == 2
    assert sites["app.example.com"]["uri"] == "session://site-data/app.example.com"
    one = sd.site_view(data, "app.example.com")
    by_name = {c["name"]: c for c in one["cookies"]}
    assert by_name["sid"]["value"] == sd.MASK, "httpOnly is masked"
    assert by_name["ab"]["value"] == "b1" and by_name["ab"]["shared"] is True
    assert one["local_storage"] == {"theme": "dark"}
    assert sd.site_view(data, "nope.test") is None


def test_a_parent_cookie_with_no_host_gets_its_own_site():
    data, _ = sd.merge({}, captured(origin="", cookies=[cookie("ab", ".example.com")]), NOW)
    assert [s["site"] for s in sd.view(data)["sites"]] == ["example.com"]


def test_secrets_are_matched_by_host_and_unleashed_ones_counted():
    secrets = [
        {"name": "app", "description": "d", "keys": ["username", "password"],
         "allowed_urls": ["https://app.example.com"], "restricted": True},
        {"name": "anywhere", "description": "", "keys": ["token"], "allowed_urls": [],
         "restricted": False},
        {"name": "admin", "description": "a", "keys": ["token"],
         "allowed_urls": ["https://admin.example.com"], "restricted": True},
    ]
    data, _ = sd.merge({}, captured(), NOW)
    listing = sd.view(data, secrets)
    sites = {s["site"]: s for s in listing["sites"]}
    assert sites["app.example.com"]["secrets"] == [
        {"name": "app", "description": "d", "keys": ["username", "password"]}
    ]
    assert sites["admin.example.com"]["saved"] is False, "a secret keeps a row with nothing saved"
    assert listing["unleashed_secrets"] == 1
    assert listing["saved_sites"] == 1


def test_forget_keeps_shared_cookies_and_other_sites():
    data, _ = sd.merge(
        {},
        captured(cookies=[cookie("sid", "app.example.com"), cookie("ab", ".example.com"),
                          cookie("kc", "sso.example.com")]),
        NOW,
    )
    left, removed = sd.forget(data, "app.example.com")
    assert [c["name"] for c in left["cookies"]] == ["ab", "kc"]
    assert left["origins"] == {}
    assert removed == {"site": "app.example.com", "cookies": ["sid"], "origins": ["https://app.example.com"],
                       "kept_shared": ["ab"]}


def test_summary_is_none_when_empty():
    assert sd.summary({}) is None
    data, _ = sd.merge({}, captured(), NOW)
    assert sd.summary(data) == {"sites": 1, "uri": "session://site-data"}


def test_the_record_round_trips_its_site_data():
    data, _ = sd.merge({}, captured(), NOW)
    record = SessionRecord(session_id="s", url="u").with_site_data(data)
    again = SessionRecord.from_json(record.to_json())
    assert again.site_data == data
    assert again.detached().site_data == data, "ending a browser keeps site data"
    assert again.at("https://x").site_data == data


def test_a_record_written_before_site_data_reads_as_empty():
    assert SessionRecord.from_json('{"session_id": "s", "url": "u"}').site_data == {}
    assert SessionRecord.from_json('{"session_id": "s", "site_data": [1]}').site_data == {}


# ---- BiDi ------------------------------------------------------------------


class FakeStorage:
    def __init__(self, cookies=(), refuse=()):
        self.cookies, self.set, self.refuse = list(cookies), [], set(refuse)

    def get_cookies(self, filter=None, partition=None):
        from types import SimpleNamespace
        return SimpleNamespace(cookies=self.cookies)

    def set_cookie(self, cookie=None, partition=None):
        if cookie.name in self.refuse:
            raise RuntimeError("unable to set cookie")
        self.set.append(cookie)


class FakeScript:
    def __init__(self, order=None):
        self.added, self.removed, self.order = [], [], order

    def add_preload_script(self, function_declaration=None, **_):
        self.added.append(function_declaration)
        if self.order is not None:
            self.order.append("preload")
        return {"script": "preload-1"}

    def remove_preload_script(self, script=None):
        self.removed.append(script)


class FakeBidi:
    def __init__(self, order=None, **kw):
        self.storage, self.script = FakeStorage(**kw), FakeScript(order)


def bidi_cookie(name, domain, value="v", http_only=False):
    from types import SimpleNamespace
    return SimpleNamespace(name=name, domain=domain, path="/", http_only=http_only,
                           secure=True, same_site="lax", expiry=None,
                           value=SimpleNamespace(type="string", value=value))


class PageDriver:
    def __init__(self, dump):
        self.dump = dump

    def execute_script(self, script, *args):
        return self.dump


def test_capture_reads_every_cookie_and_this_page_storage():
    bidi = FakeBidi(cookies=[bidi_cookie("sid", "app.example.com", http_only=True),
                             bidi_cookie("kc", "sso.example.com")])
    got = sd.capture(bidi, PageDriver({"origin": "https://app.example.com",
                                       "local": {"a": "1"}, "session": {}}))
    assert [c["name"] for c in got["cookies"]] == ["sid", "kc"]
    assert got["cookies"][0] == {
        "name": "sid", "value": "v", "value_type": "string",
        "domain": "app.example.com", "path": "/", "http_only": True,
        "secure": True, "same_site": "lax", "expiry": None}
    assert got["origin"] == "https://app.example.com" and got["local"] == {"a": "1"}


def test_restore_sets_live_cookies_and_one_preload_script():
    data, _ = sd.merge({}, captured(
        local={"a": "1"},
        cookies=[cookie("sid", "app.example.com"),
                 cookie("kc", "sso.example.com"),
                 cookie("old", "app.example.com", expiry=int(NOW) - 1)]), NOW)
    data, _ = sd.merge(data, captured(origin="https://other.example.com",
                                      local={"b": "2"}, cookies=data["cookies"]), NOW)
    bidi = FakeBidi()
    report, pending = sd.restore(bidi, data, "https://app.example.com/home", NOW)
    assert [c.name for c in bidi.storage.set] == ["sid", "kc"], "expired not replayed"
    assert len(bidi.script.added) == 1
    assert report["restored"] == ["https://app.example.com", "sso.example.com"]
    assert report["waiting"] == ["https://other.example.com"]
    assert pending == {"origins": ["https://other.example.com"], "script": "preload-1"}


def test_a_refused_cookie_is_skipped_not_fatal():
    data, _ = sd.merge({}, captured(cookies=[cookie("bad", "app.example.com"),
                                             cookie("ok", "app.example.com")]), NOW)
    bidi = FakeBidi(refuse={"bad"})
    report, _ = sd.restore(bidi, data, None, NOW)
    assert [c.name for c in bidi.storage.set] == ["ok"]
    assert report["skipped"] == [{"cookie": "bad", "domain": "app.example.com",
                                  "reason": "unable to set cookie"}]


def test_restore_with_no_storage_adds_no_preload_script():
    data, _ = sd.merge({}, captured(origin=""), NOW)
    bidi = FakeBidi()
    _, pending = sd.restore(bidi, data, None, NOW)
    assert bidi.script.added == [] and pending == {"origins": [], "script": ""}


def test_a_bidi_failure_as_a_whole_is_reported_not_raised():
    data, _ = sd.merge({}, captured(local={"a": "1"}), NOW)

    class Broken:
        storage = FakeStorage()

        @property
        def script(self):
            raise RuntimeError("no socket")

    report, pending = sd.restore(Broken(), data, None, NOW)
    assert report == {"restored": [], "waiting": [],
                      "skipped": [{"reason": "no socket"}]}
    assert pending == {"origins": [], "script": ""}


def test_retire_removes_the_script_and_never_raises():
    bidi = FakeBidi()
    sd.retire(bidi, "preload-1")
    assert bidi.script.removed == ["preload-1"]

    class Broken:
        @property
        def script(self):
            raise RuntimeError("gone")

    sd.retire(Broken(), "x")


def test_grid_bidi_derives_the_socket_from_the_grid_url():
    from kubed.selenium_flow.core.browser import Grid
    with Grid("https://grid.example:4444/").bidi("abc") as driver:
        assert driver.caps["webSocketUrl"] == (
            "wss://grid.example:4444/session/abc/se/bidi")
        assert driver.session_id == "abc"


def test_every_browser_is_opened_with_bidi():
    from kubed.selenium_flow.core.browser import Grid
    for name in ("chrome", "firefox"):
        assert Grid()._options(name).to_capabilities().get("webSocketUrl") is True


def bidi_cm(fake):
    from contextlib import contextmanager

    @contextmanager
    def cm(session_id):
        yield fake

    return cm


def test_save_site_data_returns_the_capture_privately(actions, monkeypatch):
    from types import SimpleNamespace
    page = SimpleNamespace(
        execute_script=lambda *a: {"origin": "https://app.example.com",
                                   "local": {}, "session": {}},
        current_url="https://app.example.com/", title="App")
    monkeypatch.setattr(actions.grid, "reconnect", lambda sid: page)
    monkeypatch.setattr(actions.grid, "bidi",
                        bidi_cm(FakeBidi(cookies=[bidi_cookie("sid", "x.test")])))
    got = actions.save_site_data("sid")
    assert got[sd.CAPTURED]["origin"] == "https://app.example.com"
    assert got["url"] == "https://app.example.com/" and got["title"] == "App"


class OpenedDriver:
    session_id = "new-1"
    current_url = "https://app.example.com/"
    title = "App"

    def __init__(self, order):
        self.order = order

    def get_window_size(self):
        return {"width": 1, "height": 2}

    def get(self, url):
        self.order.append("get")


def _open(actions, monkeypatch, site_data):
    order = []
    monkeypatch.setattr(actions.grid, "open",
                        lambda name, insecure=False: OpenedDriver(order))
    monkeypatch.setattr(actions.grid, "bidi", bidi_cm(FakeBidi(order=order)))
    result = actions.open_session(url="https://app.example.com/",
                                  site_data=site_data)
    return result, order


def test_open_session_restores_before_the_first_page_loads(actions, monkeypatch):
    data, _ = sd.merge({}, captured(local={"a": "1"}), NOW)
    result, order = _open(actions, monkeypatch, data)
    assert order == ["preload", "get"]
    assert result["site_data"]["restored"] == ["https://app.example.com"]
    assert result["_site_data_pending"] == {"origins": [], "script": "preload-1"}


def test_open_session_says_nothing_without_site_data(actions, monkeypatch):
    for empty in (None, {}):
        result, order = _open(actions, monkeypatch, empty)
        assert order == ["get"]
        assert "site_data" not in result and "_site_data_pending" not in result


def test_forgetting_a_parent_only_row_removes_its_dotted_cookie():
    data = {"cookies": [cookie("shared", ".example.com")], "origins": {}}
    assert [r["site"] for r in sd.view(data)["sites"]] == ["example.com"]
    left, removed = sd.forget(data, "example.com")
    assert removed["cookies"] == ["shared"]
    assert removed["kept_shared"] == []
    assert left["cookies"] == []
    assert sd.view(left)["sites"] == []
