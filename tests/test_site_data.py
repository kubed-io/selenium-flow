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


def test_an_ipv6_origin_keeps_its_brackets():
    assert sd.origin_of("http://[::1]:3000/") == "http://[::1]:3000"
    assert sd.origin_of("https://[2001:db8::1]/x") == "https://[2001:db8::1]"


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


def test_a_save_over_the_cap_keeps_cookies_and_skips_that_storage():
    big = {"blob": "x" * (sd.MAX_BYTES + 1)}
    data, saved = sd.merge({}, captured(local=big), NOW)
    assert data["cookies"] and data["origins"] == {}
    assert saved["skipped"] == [
        {"site": "https://app.example.com", "reason": "storage over 1000000 bytes"}
    ]


def test_expired_cookies_are_dropped_and_session_cookies_kept():
    kept = sd.live_cookies(
        [cookie("old", "a.test", expiry=int(NOW) - 1), cookie("new", "a.test", expiry=int(NOW) + 60),
         cookie("session", "a.test", expiry=None)],
        NOW,
    )
    assert [c["name"] for c in kept] == ["new", "session"]


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
    assert [e["origin"] for e in sites["app.example.com"]["storage"]] == [
        "https://app.example.com"
    ]
    assert sites["app.example.com"]["cookies"] == 2, "its own and the shared one"
    assert sites["sso.example.com"]["cookies"] == 2
    assert sites["app.example.com"]["uri"] == "session://site-data/app.example.com"
    one = sd.site_view(data, "app.example.com")
    by_name = {c["name"]: c for c in one["cookies"]}
    assert by_name["sid"]["value"] == sd.MASK, "httpOnly is masked"
    assert by_name["ab"]["value"] == "b1" and by_name["ab"]["shared"] is True
    assert one["storage"][0]["local_storage"] == {"theme": "dark"}
    assert sd.site_view(data, "nope.test") is None


def test_one_host_on_two_ports_keeps_each_origins_storage_apart():
    """Restore is per origin, so the view is too: a dev server on :3000 and
    one on :8080 used to show as one row with one value per key, under one
    arbitrary origin (live, after #49)."""
    first, _ = sd.merge(
        {}, captured(origin="http://localhost:3000", local={"k": "a"}), NOW
    )
    data, _ = sd.merge(
        first, captured(origin="http://localhost:8080", local={"k": "b"}, session={"s": "1"}), NOW
    )
    row = sd.view(data)["sites"]
    row = next(r for r in row if r["site"] == "localhost")
    assert row["storage"] == [
        {"origin": "http://localhost:3000", "local_storage": 1, "session_storage": 0},
        {"origin": "http://localhost:8080", "local_storage": 1, "session_storage": 1},
    ]
    one = sd.site_view(data, "localhost")
    assert one["storage"] == [
        {"origin": "http://localhost:3000", "local_storage": {"k": "a"}, "session_storage": {}},
        {"origin": "http://localhost:8080", "local_storage": {"k": "b"}, "session_storage": {"s": "1"}},
    ]
    assert "local_storage" not in one and "origin" not in one
    assert "local_storage" not in row and "origin" not in row


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
    broken = [*secrets, {"name": "dead", "description": "", "keys": [], "allowed_urls": [],
                         "restricted": True}]
    assert sd.view(data, broken)["unleashed_secrets"] == 1, "a restricted empty leash is usable nowhere"
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
                       "kept_shared": [{"name": "ab", "domain": ".example.com", "path": "/"}]}


def test_summary_is_none_when_empty():
    assert sd.summary({}) is None
    data, _ = sd.merge({}, captured(), NOW)
    assert sd.summary(data) == {"sites": 1, "uri": "session://site-data"}


def test_the_record_round_trips_its_site_data():
    data, _ = sd.merge({}, captured(), NOW)
    record = SessionRecord(session_id="s").at("u").with_site_data(data)
    again = SessionRecord.from_json(record.to_json())
    assert again.site_data == data
    assert again.detached().site_data == data, "ending a browser keeps site data"
    assert again.at("https://x").site_data == data


def test_a_record_written_before_site_data_reads_as_empty():
    assert SessionRecord.from_json('{"session_id": "s", "url": "u"}').site_data == {}
    assert SessionRecord.from_json('{"session_id": "s", "site_data": [1]}').site_data == {}


# ---- BiDi ------------------------------------------------------------------


class FakeStorage:
    """A jar: what is set is what reads back, except a ``drop``ped name, which
    is accepted without an error and never kept — as Chrome does to a
    SameSite=None cookie that is not Secure."""

    def __init__(self, cookies=(), refuse=(), drop=()):
        self.cookies, self.set = list(cookies), []
        self.refuse, self.drop = set(refuse), set(drop)

    def get_cookies(self, filter=None, partition=None):
        from types import SimpleNamespace
        held = [
            SimpleNamespace(name=c.name, domain=c.domain, path=c.path)
            for c in self.set
            if c.name not in self.drop
            and not (c.same_site == "none" and not c.secure)
        ]
        return SimpleNamespace(cookies=self.cookies + held)

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


def test_forgetting_a_parent_only_row_removes_its_dotted_cookie():
    data = {"cookies": [cookie("shared", ".example.com")], "origins": {}}
    assert [r["site"] for r in sd.view(data)["sites"]] == ["example.com"]
    left, removed = sd.forget(data, "example.com")
    assert removed["cookies"] == ["shared"]
    assert removed["kept_shared"] == []
    assert left["cookies"] == []
    assert sd.view(left)["sites"] == []


# ---- what the live Grid taught (2026-09-30) ---------------------------------


def test_read_storage_reaches_each_store_inside_its_try():
    assert "dump(() => localStorage)" in sd.READ_STORAGE
    assert "dump(() => sessionStorage)" in sd.READ_STORAGE


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="needs node")
def test_read_storage_on_a_page_with_no_storage_returns_the_empty_shape(tmp_path):
    """about:blank and data: throw SecurityError on merely naming localStorage."""
    import subprocess
    path = tmp_path / "read.js"
    path.write_text(
        "const location = {origin: 'null'};\n"
        "Object.defineProperty(globalThis, 'localStorage', {get() { throw new Error('SecurityError') }});\n"
        "Object.defineProperty(globalThis, 'sessionStorage', {get() { throw new Error('SecurityError') }});\n"
        "console.log(JSON.stringify((function () {" + sd.READ_STORAGE + "})()));\n",
        encoding="utf-8",
    )
    out = subprocess.run(["node", str(path)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"origin": "", "local": {}, "session": {}}


def test_a_save_on_a_page_with_no_storage_keeps_the_cookies():
    bidi = FakeBidi(cookies=[bidi_cookie("sid", "app.example.com")])
    got = sd.capture(bidi, PageDriver({"origin": "", "local": {}, "session": {}}))
    data, saved = sd.merge({}, got, NOW)
    assert saved == {"cookies": 1, "sites": [], "skipped": []}
    assert data["origins"] == {}


def test_the_bidi_socket_gives_up_in_seconds_not_thirty():
    from kubed.selenium_flow.core.browser import BIDI_TIMEOUT, Grid
    with Grid("http://grid.example:4444").bidi("abc") as driver:
        assert driver.command_executor.client_config.websocket_timeout == BIDI_TIMEOUT
    assert BIDI_TIMEOUT <= 5


def test_the_wire_loggers_never_log_at_debug(monkeypatch):
    """LOG_LEVEL=DEBUG would otherwise write every BiDi frame, cookie values
    included. Through the real entry point."""
    import logging
    from types import SimpleNamespace

    from kubed.selenium_flow import main as entry
    from kubed.selenium_flow.config import Settings

    settings = Settings(grid={"url": "http://grid.invalid:4444"}, log_level="DEBUG")
    monkeypatch.setattr(entry.config, "load",
                        lambda argv: SimpleNamespace(settings=settings, sources={}))

    class Server:
        auth_token = skill = flows = secrets = None
        sessions = SimpleNamespace(kind="memory")

        def __init__(self, *a, **kw):
            pass

        def run(self, **kw):
            pass

    monkeypatch.setattr(entry, "SeleniumMCP", Server)
    root = logging.getLogger()
    monkeypatch.setattr(logging, "basicConfig", lambda level: root.setLevel(level))
    names = ("selenium.webdriver.remote.websocket_connection",
             "selenium.webdriver.remote.remote_connection")
    before = root.level, [logging.getLogger(n).level for n in names]
    try:
        entry.main([])
        assert root.isEnabledFor(logging.DEBUG)
        for name in names:
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
    finally:
        root.setLevel(before[0])
        for name, level in zip(names, before[1], strict=True):
            logging.getLogger(name).setLevel(level)


# ---- a site's own cookies, and the ones it only sits under --------------------


def test_rows_do_not_depend_on_the_order_the_cookies_came_in():
    a = [cookie("ab", ".example.com"), cookie("sid", "app.example.com")]
    first = sd.view({"cookies": a, "origins": {}})
    second = sd.view({"cookies": list(reversed(a)), "origins": {}})
    assert [r["site"] for r in first["sites"]] == ["app.example.com", "example.com"]
    assert first == second


def test_saved_counts_only_what_forget_would_remove():
    data = {"cookies": [cookie("ab", ".example.com")], "origins": {}, "saved_at": NOW}
    secrets = [{"name": "app", "keys": [], "allowed_urls": ["https://app.example.com"]}]
    sites = {r["site"]: r for r in sd.view(data, secrets)["sites"]}
    assert sites["app.example.com"]["saved"] is False, "a parent's cookie is not its own"
    assert sites["app.example.com"]["saved_at"] is None
    assert sites["example.com"]["saved"] is True
    assert sd.view(data, secrets)["saved_sites"] == 1


def test_the_site_view_names_goes_and_stays_by_the_forget_rule():
    data = {"cookies": [cookie("own", "app.example.com"), cookie("dot", ".app.example.com"),
                        cookie("ab", ".example.com")], "origins": {}}
    one = sd.site_view(data, "app.example.com")
    assert one["own_cookies"] == ["own", "dot"]
    assert one["kept_shared"] == [
        {"name": "ab", "domain": ".example.com", "path": "/"}]
    parent = sd.site_view(data, "example.com")
    assert parent["own_cookies"] == ["ab"] and parent["kept_shared"] == []


def test_the_cap_is_on_what_is_stored_and_the_oldest_other_origins_make_room():
    half = {"blob": "x" * (sd.MAX_BYTES // 3)}
    data = {"cookies": [], "origins": {
        "https://old.test": {"local": half, "session": {}, "saved_at": 1.0},
        "https://mid.test": {"local": half, "session": {}, "saved_at": 2.0},
    }}
    merged, receipt = sd.merge(data, captured(local=half), NOW)
    assert len(json.dumps(merged)) <= sd.MAX_BYTES
    assert sorted(merged["origins"]) == ["https://app.example.com", "https://mid.test"]
    assert receipt["sites"] == ["https://app.example.com"]
    assert receipt["skipped"] == [
        {"site": "https://old.test", "reason": "evicted: over 1000000 bytes"}
    ]


def test_a_cookie_jar_over_the_cap_raises_and_saves_nothing():
    big = [cookie("blob", "app.example.com", "x" * (sd.MAX_BYTES + 1))]
    with pytest.raises(ValueError, match="the cookie jar is over 1000000 bytes; nothing was saved"):
        sd.merge({"cookies": [], "origins": {}}, captured(cookies=big), NOW)
    from kubed.selenium_flow import errors
    assert errors.status_for(ValueError("x")) == 400


def test_a_sites_own_dotted_cookie_is_not_shared_but_a_parents_is():
    data, _ = sd.merge({}, captured(cookies=[
        cookie("own", ".app.example.com"), cookie("parent", ".example.com"),
        cookie("plain", "app.example.com"),
    ]), NOW)
    shown = {c["name"]: c["shared"] for c in sd.site_view(data, "app.example.com")["cookies"]}
    assert shown == {"own": False, "parent": True, "plain": False}


def test_two_shared_cookies_of_one_name_stay_apart():
    data = {"cookies": [cookie("sid", ".example.com"), cookie("sid", ".example.org"),
                        cookie("sid", "app.example.com")], "origins": {}}
    one = sd.site_view(data, "app.example.com")
    assert one["kept_shared"] == [
        {"name": "sid", "domain": ".example.com", "path": "/"}]
    both = {"cookies": [cookie("sid", ".example.com"), cookie("sid", ".example.org")],
            "origins": {"https://a.example.com": {}, "https://a.example.org": {}}}
    assert sd.site_view(both, "a.example.com")["kept_shared"][0]["domain"] == ".example.com"
    assert sd.site_view(both, "a.example.org")["kept_shared"][0]["domain"] == ".example.org"


def test_a_jar_of_many_domains_is_not_rescanned_per_host(monkeypatch):
    """Every host used to rescan the whole jar, and every detail rebuilt the
    listing: cookies x hosts x hosts. One grouping serves all of them."""
    calls = {"n": 0}
    real = {name: getattr(sd, name) for name in ("_covers", "_own")}

    def counting(name):
        def wrapped(*a):
            calls["n"] += 1
            return real[name](*a)
        return wrapped

    for name in real:
        monkeypatch.setattr(sd, name, counting(name))
    cookies = [cookie("c", f"h{i}.example{i}.com") for i in range(500)]
    listing, details = sd.views({"cookies": cookies, "origins": {}})
    assert len(listing["sites"]) == 500 and set(details) == {r["site"] for r in listing["sites"]}
    assert calls["n"] <= 4 * len(cookies)
    assert sd.view({"cookies": cookies, "origins": {}}) == listing


# ---- Grid.bidi --------------------------------------------------------------


class FakeSocket:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.mark.parametrize("fails", [False, True])
def test_grid_bidi_closes_a_socket_it_opened(fails):
    from contextlib import nullcontext

    from kubed.selenium_flow.core.browser import Grid

    socket = FakeSocket()
    raised = pytest.raises(RuntimeError) if fails else nullcontext()
    with raised, Grid("http://grid.example:4444").bidi("abc") as driver:
        driver._websocket_connection = socket
        if fails:
            raise RuntimeError("mid-call")
    assert socket.closed == 1
