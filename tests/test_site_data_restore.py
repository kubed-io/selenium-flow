"""A restore, straight into the browser before its first page: cookies, then
each origin's localStorage in a spare tab, then the saved sessionStorage in
the main tab (spec round 2, *open_session*). The spare tab is faked at
`site_data.spare_tab`; `browser.spare_tab` has its own tests."""

import json
import shutil
import subprocess

import pytest

from kubed.selenium_flow.core import site_data as sd

from .site_data_fakes import (
    MAIN,
    NOW,
    FakeBidi,
    FakeSpare,
    FakeStorage,
    bidi_cm,
    cookie,
    snapshot,
)

pytestmark = pytest.mark.unit

LIST = "session://site-data"
APP = "https://app.example.com"


@pytest.fixture
def spare(monkeypatch):
    fake = FakeSpare()
    monkeypatch.setattr(sd, "spare_tab", fake)
    return fake


def test_cookies_then_each_origins_storage_then_session_storage(spare):
    order = []
    spare.order = order
    bidi = FakeBidi(order=order)
    data = snapshot(
        cookies=[cookie("sid", "app.example.com"), cookie("kc", "sso.example.com"),
                 cookie("old", "app.example.com", expiry=int(NOW) - 1)],
        origins={APP: {"theme": "dark"}, "https://sso.example.com": {"kc": "1"}},
        session={"origin": APP, "items": {"token": "t"}},
    )
    report = sd.restore(bidi, data, NOW)
    assert [c.name for c in bidi.storage.set] == ["sid", "kc"], "the expired one stays out"
    assert spare.local == {APP: {"theme": "dark"}, "https://sso.example.com": {"kc": "1"}}
    assert spare.session == {APP: {"token": "t"}}
    assert order == ["cookie", "cookie", "spare", "spare", MAIN]
    assert spare.tabs == ["spare", MAIN], "one spare tab for every origin, then the main tab"
    assert bidi.browsing_context.navigated == [(MAIN, "about:blank")], "never left on the stand-in"
    assert report == {"restored": ["app.example.com", "sso.example.com"], "skipped": [], "uri": LIST}


def test_a_cookie_only_snapshot_opens_no_tab(spare):
    report = sd.restore(FakeBidi(), snapshot(cookies=[cookie("sid", "app.example.com")]), NOW)
    assert spare.tabs == []
    assert report == {"restored": ["app.example.com"], "skipped": [], "uri": LIST}


def test_an_origin_that_fails_is_skipped_and_the_rest_go_on(spare):
    spare.refuse = {"https://bad.example.com"}
    data = snapshot(origins={"https://bad.example.com": {"a": "1"}, "https://ok.example.com": {"b": "2"}})
    report = sd.restore(FakeBidi(), data, NOW)
    assert spare.local == {"https://ok.example.com": {"b": "2"}}
    assert report["restored"] == ["ok.example.com"]
    assert report["skipped"] == [{"site": "https://bad.example.com", "reason": "the navigation failed"}]


def test_session_storage_that_fails_is_skipped_not_fatal(spare):
    spare.refuse = {APP}
    data = snapshot(session={"origin": APP, "items": {"t": "1"}})
    bidi = FakeBidi()
    assert sd.restore(bidi, data, NOW) == {
        "restored": [], "uri": LIST,
        "skipped": [{"site": APP, "reason": "the navigation failed"}],
    }
    assert bidi.browsing_context.navigated == [(MAIN, "about:blank")], "never left on the stand-in"


def test_a_tab_that_cannot_open_skips_its_origins_and_the_cookies_stand(spare):
    spare.broken = RuntimeError("socket is already closed")
    data = snapshot(
        cookies=[cookie("sid", "app.example.com")],
        origins={APP: {"k": "v"}},
        session={"origin": "https://sso.example.com", "items": {"t": "1"}},
    )
    report = sd.restore(FakeBidi(), data, NOW)
    assert report["restored"] == ["app.example.com"], "the cookie came back"
    assert report["skipped"] == [
        {"site": APP, "reason": "socket is already closed"},
        {"site": "https://sso.example.com", "reason": "socket is already closed"},
    ]


def test_a_malformed_snapshot_is_a_skip_not_a_raise(spare):
    report = sd.restore(FakeBidi(), {"cookies": 5}, NOW)
    assert report["restored"] == [] and [set(s) for s in report["skipped"]] == [{"reason"}]


def test_a_dead_channel_is_reported_never_raised(spare):
    spare.broken = RuntimeError("socket is already closed")

    class Dead:
        current_window_handle = MAIN

        @property
        def storage(self):
            raise RuntimeError("socket is already closed")

    data = snapshot(cookies=[cookie("sid", "a.example.com")], origins={"https://a.example.com": {"k": "v"}})
    report = sd.restore(Dead(), data, NOW)
    assert report["restored"] == []
    assert {s["reason"] for s in report["skipped"]} == {"socket is already closed"}


def test_a_none_same_site_without_secure_is_restored_as_lax(spare):
    """Chrome reports an unspecified SameSite as "none" and silently refuses
    None without Secure: the reopened session came back signed out."""
    loose = {**cookie("sid", "app.example.com", secure=False), "same_site": "none"}
    tight = {**cookie("tok", "app.example.com", secure=True), "same_site": "none"}
    fox = {**cookie("fx", "app.example.com", secure=False), "same_site": "default"}
    bidi = FakeBidi()
    report = sd.restore(bidi, snapshot(cookies=[loose, tight, fox]), NOW)
    assert {c.name: c.same_site for c in bidi.storage.set} == {"sid": "lax", "tok": "none", "fx": "default"}
    assert report["skipped"] == [] and report["restored"] == ["app.example.com"]


def test_a_cookie_refused_or_not_kept_is_skipped(spare):
    data = snapshot(cookies=[cookie("gone", "only.example.com"), cookie("ok", "app.example.com"),
                             cookie("bad", "refused.example.com")])
    report = sd.restore(FakeBidi(drop={"gone"}, refuse={"bad"}), data, NOW)
    assert report["skipped"] == [
        {"cookie": "bad", "domain": "refused.example.com", "reason": "unable to set cookie"},
        {"cookie": "gone", "domain": "only.example.com", "reason": "the browser did not keep it"},
    ]
    assert report["restored"] == ["app.example.com"], "a host with nothing kept is not restored"


def test_an_unreadable_jar_trusts_the_set(spare):
    class Blind(FakeStorage):
        def get_cookies(self, filter=None, partition=None):
            raise RuntimeError("no read")

    bidi = FakeBidi()
    bidi.storage = Blind()
    report = sd.restore(bidi, snapshot(cookies=[cookie("ok", "app.example.com")]), NOW)
    assert report["restored"] == ["app.example.com"] and report["skipped"] == []


def test_one_malformed_cookie_is_skipped_not_the_whole_restore(spare):
    good = cookie("ok", "app.example.com")
    nameless = {k: v for k, v in cookie("x", "app.example.com").items() if k != "name"}
    domainless = {**cookie("nodomain", "app.example.com"), "domain": None}
    stringy = cookie("odd", "app.example.com", expiry="tomorrow")
    bidi = FakeBidi()
    report = sd.restore(bidi, snapshot(cookies=[nameless, domainless, stringy, good]), NOW)
    assert [c.name for c in bidi.storage.set] == ["ok"]
    # Only string fields ride along: the published shape declares them strings.
    assert report["skipped"] == [
        {"reason": "a stored cookie with no name or domain", "domain": "app.example.com"},
        {"reason": "a stored cookie with no name or domain", "cookie": "nodomain"},
        {"reason": "a stored cookie whose expiry is not a time", "cookie": "odd",
         "domain": "app.example.com"},
    ]


def test_a_failure_never_leaks_the_grid_credential(spare):
    leak = "http://user:pass@grid:4444"
    spare.broken = RuntimeError(f"connect to {leak}/session failed")
    bidi = FakeBidi()

    def refuse(cookie=None, partition=None):
        raise RuntimeError(f"refused by {leak}")

    bidi.storage.set_cookie = refuse
    data = snapshot(cookies=[cookie("sid", "a.example.com")], origins={"https://a.example.com": {"k": "v"}})
    report = sd.restore(bidi, data, NOW)
    assert len(report["skipped"]) == 2
    assert "user:pass" not in json.dumps(report)


def test_every_skipped_item_restore_emits_is_declared(spare):
    from kubed.selenium_flow.spec.schemas import SITE_DATA_HINT

    declared = SITE_DATA_HINT["properties"]["skipped"]["items"]
    spare.refuse = {"https://bad.example.com"}
    data = snapshot(
        cookies=[cookie("gone", "a.example.com"), cookie("bad", "b.example.com"), {"name": "x"}],
        origins={"https://bad.example.com": {"k": "v"}},
    )
    report = sd.restore(FakeBidi(drop={"gone"}, refuse={"bad"}), data, NOW)
    assert len(report["skipped"]) == 4
    for item in report["skipped"]:
        assert set(item) <= set(declared["properties"]), item
        assert set(declared["required"]) <= set(item)
    assert "waiting" not in SITE_DATA_HINT["properties"], "nothing waits: every origin is in place"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_storage_scripts_fill_and_read_for_real(tmp_path):
    harness = tmp_path / "storage.js"
    harness.write_text(
        "const store = () => { const s = new Map(); return {\n"
        "  getItem: (k) => (s.has(k) ? s.get(k) : null), setItem: (k, v) => { s.set(k, String(v)) },\n"
        "  get length() { return s.size }, key: (i) => [...s.keys()][i] } };\n"
        "Object.defineProperty(globalThis, 'localStorage', { value: store(), configurable: true });\n"
        "Object.defineProperty(globalThis, 'sessionStorage', { value: store(), configurable: true });\n"
        "const [local, session, read] = JSON.parse(process.argv[2]);\n"
        "const counts = [eval(local), eval(session)];\n"
        "console.log(JSON.stringify({counts, local: eval(read), session: sessionStorage.getItem('t')}));\n",
        encoding="utf-8",
    )
    items = {"theme": "dark", "quote": "a'b\"c</script>"}
    scripts = [sd.fill("localStorage", items), sd.fill("sessionStorage", {"t": "1"}), sd.READ_LOCAL]
    out = subprocess.run(
        ["node", str(harness), json.dumps(scripts)], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"counts": [2, 1], "local": items, "session": "1"}


# ---- open_session restores before the first page ------------------------------


class OpenedDriver:
    session_id = "new-1"
    current_url = APP + "/"
    title = "App"

    def __init__(self, order):
        self.order = order

    def get_window_size(self):
        return {"width": 1, "height": 2}

    def get(self, url):
        self.order.append("get")


def opened(actions, monkeypatch, spare, data, **kw):
    order = []
    spare.order = order
    monkeypatch.setattr(actions.grid, "open", lambda name, insecure=False: OpenedDriver(order))
    monkeypatch.setattr(actions.grid, "bidi", bidi_cm(FakeBidi(order=order)))
    return actions.open_session(url=APP + "/", site_data=data, **kw), order


FULL = snapshot(
    cookies=[cookie("sid", "app.example.com")],
    origins={APP: {"a": "1"}},
    session={"origin": APP, "items": {"t": "1"}},
)


def test_open_session_restores_everything_before_the_first_page(actions, monkeypatch, spare):
    result, order = opened(actions, monkeypatch, spare, FULL)
    assert order == ["cookie", "spare", MAIN, "get"]
    assert result["site_data"] == {"restored": ["app.example.com"], "skipped": [], "uri": LIST}
    assert "_site_data_pending" not in result


def test_an_insecure_browser_gets_no_saved_site_data(actions, monkeypatch, spare):
    """A browser that accepts any certificate would hand saved cookies for
    every site to whoever sits in the middle (security review, #49)."""
    result, order = opened(actions, monkeypatch, spare, FULL, insecure=True)
    assert order == ["get"], "nothing set, no tab"
    assert result["site_data"] == {
        "restored": [], "skipped": [{"reason": "an insecure browser gets no saved site data"}],
        "uri": LIST,
    }


def test_open_session_says_nothing_without_site_data(actions, monkeypatch, spare):
    for empty in (None, {}, snapshot()):
        result, order = opened(actions, monkeypatch, spare, empty)
        assert order == ["get"]
        assert "site_data" not in result


def test_a_corrupt_record_never_fails_an_open(actions, monkeypatch, spare):
    """Restore is best effort: nothing in it can fail an open."""
    for corrupt in ({"session": "x"}, {"session": ["a"]}, ["a"], "x"):
        result, order = opened(actions, monkeypatch, spare, corrupt)
        assert order == ["get"] and "site_data" not in result and result["session_id"] == "new-1"


def test_a_restore_with_nothing_to_say_carries_no_hint(actions, monkeypatch, spare):
    data = snapshot(cookies=[cookie("old", "app.example.com", expiry=int(NOW) - 1)])
    result, order = opened(actions, monkeypatch, spare, data)
    assert order == ["get"] and "site_data" not in result
