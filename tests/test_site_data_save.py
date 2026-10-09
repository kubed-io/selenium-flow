"""A save: the whole jar, the page's two storages, and the localStorage of
every other origin the session has been to, read in a spare tab — one
snapshot that replaces the last (spec round 2, *save_site_data*)."""

import json
import time
from types import SimpleNamespace

import pytest

from kubed.selenium_flow import urls
from kubed.selenium_flow.site_data import snapshot as sd
from kubed.selenium_flow.site_data import transfer
from kubed.selenium_flow.workspace.store import Workspace

from .fakes import FakeBidi
from .site_data_fakes import (
    NOW,
    FakeSpare,
    bidi_cm,
    bidi_cookie,
    cookie,
    snapshot,
)

pytestmark = pytest.mark.unit

APP, SSO, OLD = "https://app.example.com", "https://sso.example.com", "https://old.example.com"
PAGE = {"origin": APP, "local": {"a": "1"}, "session": {"t": "1"}}


def visit(origin, at=NOW):
    return {"origin": origin, "url": origin + "/", "at": at}


def captured(origin=APP, local=None, session=None, cookies=None, others=None, failed=None):
    return {
        "cookies": cookies if cookies is not None else [cookie("sid", "app.example.com")],
        "origin": origin, "local": local or {}, "session": session or {},
        "others": others or {}, "failed": failed or [],
    }


# ---- snapshot: one save replaces the last ------------------------------------


def test_a_save_replaces_the_whole_snapshot():
    before = snapshot(cookies=[cookie("old", "old.example.com")], origins={OLD: {"x": "1"}},
                      session={"origin": OLD, "items": {"s": "1"}}, saved_at=NOW - 60)
    data, saved = sd.snapshot(before, captured(local={"theme": "dark"}), [visit(APP)], NOW)
    assert data == {
        "cookies": [cookie("sid", "app.example.com")],
        "origins": {APP: {"local": {"theme": "dark"}}},
        "session": {"origin": APP, "items": {}},
        "saved_at": NOW,
    }
    assert saved == {"cookies": 1, "sites": [APP], "skipped": []}


def test_every_origin_read_is_kept_newest_first_and_an_empty_one_left_out():
    history = [visit(APP, NOW), visit(SSO, NOW - 10), visit(OLD, NOW - 20)]
    others = {OLD: {"o": "1"}, SSO: {"kc": "1"}, "https://empty.example.com": {}}
    data, saved = sd.snapshot({}, captured(local={"a": "1"}, others=others), history, NOW)
    assert list(data["origins"]) == [APP, SSO, OLD]
    assert saved["sites"] == [APP, SSO, OLD]


def test_the_page_session_storage_is_kept_for_its_origin():
    data, saved = sd.snapshot({}, captured(session={"token": "t"}), [visit(APP)], NOW)
    assert data["session"] == {"origin": APP, "items": {"token": "t"}}
    assert data["origins"] == {} and saved["sites"] == [APP], "sessionStorage alone is a site saved"


def test_a_page_with_no_origin_saves_its_cookies_and_the_others():
    data, saved = sd.snapshot({}, captured(origin="", others={SSO: {"kc": "1"}}), [visit(SSO)], NOW)
    assert data["session"] == {} and list(data["origins"]) == [SSO]
    assert saved == {"cookies": 1, "sites": [SSO], "skipped": []}


def test_an_origin_that_could_not_be_read_keeps_its_last_storage():
    before = snapshot(origins={SSO: {"kc": "old"}})
    failed = [{"site": SSO, "reason": sd.SW_REASON}, {"site": OLD, "reason": "the navigation failed"}]
    history = [visit(APP), visit(SSO), visit(OLD)]
    data, saved = sd.snapshot(before, captured(local={"a": "1"}, failed=failed), history, NOW)
    assert data["origins"] == {APP: {"local": {"a": "1"}}, SSO: {"local": {"kc": "old"}}}
    assert saved["sites"] == [APP]
    assert saved["skipped"] == failed


def test_over_the_cap_the_origins_visited_longest_ago_go_first():
    third = {"blob": "x" * (sd.MAX_BYTES // 3)}
    history = [visit(APP, NOW), visit(SSO, NOW - 10), visit(OLD, NOW - 20)]
    data, saved = sd.snapshot({}, captured(local=third, others={SSO: third, OLD: third}), history, NOW)
    assert len(json.dumps(data)) <= sd.MAX_BYTES
    assert list(data["origins"]) == [APP, SSO]
    assert saved["skipped"] == [{"site": OLD, "reason": sd.LEFT_OUT}]


def test_the_pages_own_storage_is_the_last_to_go():
    big = {"blob": "x" * (sd.MAX_BYTES - 1000)}
    history = [visit(APP), visit(SSO, NOW - 10)]
    data, saved = sd.snapshot({}, captured(local=big, session=big, others={SSO: {"kc": "1"}}),
                              history, NOW)
    assert data["origins"] == {} and data["session"]["items"] == big
    assert [s["site"] for s in saved["skipped"]] == [SSO, APP]
    assert saved["sites"] == [APP]


def test_a_cookie_jar_over_the_cap_raises_and_saves_nothing():
    big = [cookie("blob", "app.example.com", "x" * (sd.MAX_BYTES + 1))]
    with pytest.raises(ValueError, match="the cookie jar is over 1000000 bytes; nothing was saved"):
        sd.snapshot({}, captured(cookies=big), [visit(APP)], NOW)
    from kubed.selenium_flow import errors

    assert errors.status_for(ValueError("x")) == 400


def _old_cap(data):
    """The cap as it was: re-serialise the whole payload after every eviction.
    Kept as the reference the O(n) cap must agree with."""
    data = {**data, "origins": dict(data["origins"])}
    gone_sites = []
    while len(json.dumps(data)) > sd.MAX_BYTES:
        if data["origins"]:
            gone = list(data["origins"])[-1]
            data["origins"] = {o: e for o, e in data["origins"].items() if o != gone}
        else:
            gone, data["session"] = data["session"]["origin"], {}
        gone_sites.append(gone)
    return data, gone_sites


def test_the_cap_evicts_what_the_re_serialising_loop_did(monkeypatch):
    import random

    rng = random.Random(20)
    monkeypatch.setattr(sd, "MAX_BYTES", 6_000)
    for _ in range(300):
        n = rng.randint(0, 12)
        origins = [f"https://s{i}.example{rng.choice(['.com', '.é'])}" for i in range(n)]
        pick = lambda: "".join(rng.choice('ab"\\é\n') * rng.randint(0, 900) for _ in range(2))  # noqa: E731
        others = {o: {"k": pick()} for o in origins[1:]}
        here = origins[0] if origins else ""
        cap = captured(origin=here, local={"k": pick()} if here else {},
                       session={"s": pick()} if here and rng.random() < 0.7 else {},
                       others=others, cookies=[cookie("sid", "a.com", pick()[:300])])
        history = [visit(o) for o in origins]
        try:
            data, receipt = sd.snapshot({}, cap, history, NOW)
        except ValueError:
            continue
        # The same payload, uncapped, through the old loop.
        monkeypatch.setattr(sd, "MAX_BYTES", 10**9)
        full, _ = sd.snapshot({}, cap, history, NOW)
        monkeypatch.setattr(sd, "MAX_BYTES", 6_000)
        want, gone = _old_cap(full)
        assert data == want
        assert [s["site"] for s in receipt["skipped"]] == gone
        assert len(json.dumps(data)) <= sd.MAX_BYTES


def test_the_record_round_trips_its_snapshot():
    data, _ = sd.snapshot({}, captured(local={"a": "1"}, session={"t": "1"}), [visit(APP)], NOW)
    record = Workspace(session_id="s").visited(APP + "/x").with_site_data(data)
    again = Workspace.from_json(record.to_json())
    assert again.site_data == data
    assert again.detached().site_data == data, "ending a browser keeps site data"
    assert again.visited("https://x.example.com/").site_data == data


# ---- capture: what the browser holds -----------------------------------------


class PageDriver:
    def __init__(self, dump):
        self.dump = dump

    def execute_script(self, script, *args):
        return self.dump


@pytest.fixture
def spare(monkeypatch):
    fake = FakeSpare(local={SSO: {"kc": "1"}, OLD: {}})
    monkeypatch.setattr(transfer, "spare_tab", fake)
    return fake


def test_capture_reads_the_jar_the_page_and_every_other_origin_in_one_tab(spare):
    bidi = FakeBidi()
    bidi.storage.cookies = [bidi_cookie("sid", "app.example.com", http_only=True),
                            bidi_cookie("kc", "sso.example.com")]
    got = transfer.capture(bidi, PageDriver(PAGE), [APP, SSO, OLD])
    assert got["cookies"][0] == {
        "name": "sid", "value": "v", "value_type": "string", "domain": "app.example.com",
        "path": "/", "http_only": True, "secure": True, "same_site": "lax", "expiry": None,
    }
    assert (got["origin"], got["local"], got["session"]) == (APP, {"a": "1"}, {"t": "1"})
    assert got["others"] == {SSO: {"kc": "1"}, OLD: {}}
    assert got["failed"] == []
    assert spare.tabs == ["spare"], "one tab for every other origin"
    assert spare.runs == [("spare", SSO), ("spare", OLD)], "the page's own is read in place"


def test_only_web_origins_are_visited(spare):
    transfer.capture(FakeBidi(), PageDriver(PAGE), ["chrome://settings", SSO, "file:///tmp"])
    assert spare.runs == [("spare", SSO)]


def test_a_service_worker_origin_is_reported_and_never_read(spare):
    spare.workers = {SSO}
    got = transfer.capture(FakeBidi(), PageDriver(PAGE), [APP, SSO, OLD])
    assert got["others"] == {OLD: {}}
    assert got["failed"] == [{"site": SSO, "reason": "a service worker answered: save while on this site"}]


def test_a_tab_that_cannot_open_fails_every_origin_it_did_not_read(spare):
    spare.broken = RuntimeError("socket is already closed")
    got = transfer.capture(FakeBidi(), PageDriver(PAGE), [APP, SSO, OLD])
    assert got["others"] == {}
    assert got["failed"] == [
        {"site": SSO, "reason": "socket is already closed"},
        {"site": OLD, "reason": "socket is already closed"},
    ]


def test_no_other_origin_means_no_tab(spare):
    got = transfer.capture(FakeBidi(), PageDriver(PAGE), [APP])
    assert spare.tabs == [] and got["others"] == {}


def test_a_save_on_a_page_with_no_storage_keeps_the_cookies(spare):
    bidi = FakeBidi()
    bidi.storage.cookies = [bidi_cookie("sid", "app.example.com")]
    got = transfer.capture(bidi, PageDriver({"origin": "", "local": {}, "session": {}}))
    data, saved = sd.snapshot({}, got, [], NOW)
    assert saved == {"cookies": 1, "sites": [], "skipped": []}
    assert data["origins"] == {} and data["session"] == {}


# ---- the action, and the server that hands it the history --------------------


def page():
    return SimpleNamespace(execute_script=lambda *a: dict(PAGE), current_url=APP + "/", title="App")


def test_save_site_data_reads_the_origins_the_workspace_has_been_to(actions, monkeypatch, spare):
    monkeypatch.setattr(actions.grid, "reconnect", lambda sid: page())
    monkeypatch.setattr(actions.grid, "bidi", bidi_cm(FakeBidi()))
    actions.visited = lambda: [APP, SSO]
    got = actions.save_site_data("sid")
    assert got["url"] == APP + "/" and got["title"] == "App"
    assert got[sd.CAPTURED]["others"] == {SSO: {"kc": "1"}}


def test_save_site_data_without_a_history_reads_only_the_page(actions, monkeypatch, spare):
    monkeypatch.setattr(actions.grid, "reconnect", lambda sid: page())
    monkeypatch.setattr(actions.grid, "bidi", bidi_cm(FakeBidi()))
    assert actions.visited is None
    assert actions.save_site_data("sid")[sd.CAPTURED]["others"] == {}
    assert spare.tabs == []


def test_one_save_through_the_server_keeps_every_site_the_workspace_went_to(monkeypatch, spare):
    """The server hands `Actions` the caller's history: one save on the
    second app keeps the first's storage too (spec round 2)."""
    from starlette.testclient import TestClient

    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.core.browser import Grid
    from kubed.selenium_flow.server import SeleniumMCP
    from kubed.selenium_flow.workspace.workspaces import Workspaces

    from .conftest import NAMED, TOKEN

    monkeypatch.setattr(Grid, "reconnect", lambda self, sid: page())
    monkeypatch.setattr(Grid, "bidi", lambda self, sid: bidi_cm(FakeBidi())(sid))
    monkeypatch.setattr(Workspaces, "resolve", lambda self, name: "live-id")
    server = SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}))
    # Stamped now, not at NOW: the save reads the history as the next write
    # would keep it, and a fixed stamp ages out of the store's TTL.
    now = time.time()
    server.workspaces.store.set(
        NAMED, Workspace(session_id="live-id").visited(SSO + "/", now=now - 60).visited(APP + "/x", now=now)
    )
    response = TestClient(server.mcp.http_app()).post(
        "/browser/save-site-data", headers={"Authorization": f"Bearer {TOKEN}"},
        params={"workspace": NAMED}, json={},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["saved"]["sites"] == [APP, SSO]
    stored = server.workspaces.store.get(NAMED).site_data
    assert stored["origins"] == {APP: {"local": {"a": "1"}}, SSO: {"local": {"kc": "1"}}}
    assert stored["session"] == {"origin": APP, "items": {"t": "1"}}


# ---- a save mid-flow sees the sites the run reached before it -----------------


class Tab:
    """One browser tab: where it is, and that page's own storage."""

    title = "t"

    def __init__(self):
        self.current_url = "about:blank"

    def get(self, url):
        self.current_url = url

    def execute_script(self, script, *args):
        here = urls.origin_of(self.current_url)
        return {"origin": here, "local": {"page": here}, "session": {}}


def test_a_save_mid_flow_reads_the_sites_the_run_reached_before_it(
    named_caller, tmp_path, monkeypatch, spare
):
    """The run writes its pages once, at the end; a save step in the middle
    still has to read the sites before it, so they go in first."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.core.browser import Grid
    from kubed.selenium_flow.flows import api as flowapi
    from kubed.selenium_flow.server import SeleniumMCP

    from .conftest import NAMED

    a, b, c = "https://a.example.com", "https://b.example.com", "https://c.example.com"
    spare.local = {a: {"a": "1"}}
    tab = Tab()
    monkeypatch.setattr(Grid, "reconnect", lambda self, sid: tab)
    monkeypatch.setattr(Grid, "is_alive", lambda self, sid: True)
    monkeypatch.setattr(Grid, "bidi", lambda self, sid: bidi_cm(FakeBidi())(sid))
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)},
    ))
    server.flows.save(NAMED, "trip", {"steps": [
        {"tool": "navigate", "args": {"url": a + "/"}},
        {"tool": "navigate", "args": {"url": b + "/"}},
        {"tool": "save_site_data", "args": {}, "return": True},
        {"tool": "navigate", "args": {"url": c + "/"}},
    ]})
    server.workspaces.store.set(NAMED, Workspace(session_id="live-id"))
    touched = []
    real = server.workspaces.touch

    def touch(name, *urls, browser=None):
        touched.append(urls)
        return real(name, *urls, browser=browser)

    monkeypatch.setattr(server.workspaces, "touch", touch)
    report = flowapi.run_for(server.flows, server.actions, server.workspaces, NAMED, "trip")
    assert spare.runs == [("spare", a)], "a is read in the spare tab; b is the page"
    assert report["steps"][2]["result"]["saved"]["sites"] == [b, a]
    record = server.workspaces.store.get(NAMED)
    assert record.site_data["origins"] == {b: {"local": {"page": b}}, a: {"local": {"a": "1"}}}
    assert len(touched) == 2 and touched[0] == (a + "/", b + "/"), "flushed before the save, in order"
    assert a + "/" not in touched[1] and b + "/" not in touched[1], "the end writes only what came after"
    assert [v["origin"] for v in record.history] == [c, b, a]
