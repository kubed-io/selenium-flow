"""The session's side of site data: a save stored and never returned, a
restore on open and on a silent reopen, and the one report each makes."""

import json
from dataclasses import replace

import pytest

from kubed.selenium_flow.site_data import snapshot as site_data
from kubed.selenium_flow.workspace.store import MemoryStore
from kubed.selenium_flow.workspace.workspaces import Caller
from tests.conftest import NAMED, RecordingActions, manager

URL = "https://app.example.com/x"
REPORT = {"restored": ["app.example.com"], "skipped": [], "uri": "workspace://site-data"}


class SiteActions(RecordingActions):
    def __init__(self):
        super().__init__()
        self.site_data_seen = []

    def open_session(self, url=None, site_data=None, **kw):
        self.site_data_seen.append(site_data)
        opened = super().open_session(url=url, **kw)
        if site_data:
            opened["site_data"] = dict(REPORT)
        return opened

    def save_site_data(self, session_id, url=None):
        return {
            "url": URL,
            "title": "t",
            site_data.CAPTURED: {
                "cookies": [{"name": "a", "value": "1", "domain": "app.example.com"}],
                "origin": "https://app.example.com",
                "local": {"k": "v"},
                "session": {},
            },
        }


class RetryingStore(MemoryStore):
    """Redis refusing the first EXEC: `fn` runs on the record `first`, that
    run is thrown away, `between` happens, and `fn` runs again on what is
    stored. The store contract allows it, so nothing `fn` leaves outside
    itself may outlive its last run."""

    first = None
    between = staticmethod(lambda: None)

    def upsert(self, key, fn):
        if self.first is not None:
            fn(self.first)
            self.first = None
            self.between()
        return super().upsert(key, fn)


def opened_with_save():
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    m.act(Caller(NAMED), lambda s: m.actions.save_site_data(s))
    return m


def reopened(m, **kw):
    m.end_browser(Caller(NAMED))
    return m.open_browser(Caller(NAMED), **kw)


def reaped(m):
    """The Grid took the browser: the next call reopens it on its own."""
    m.actions.grid.alive.clear()


def test_a_save_is_stored_and_never_returned_raw(named_caller):
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    result = m.act(Caller(NAMED), lambda s: m.actions.save_site_data(s))
    assert site_data.CAPTURED not in result
    assert result["saved"]["cookies"] == 1 and result["uri"] == "workspace://site-data"
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]


def test_open_restores_what_was_saved_and_says_so(named_caller):
    m = opened_with_save()
    told = reopened(m)
    assert m.actions.site_data_seen[-1]["cookies"]
    assert told["site_data"] == REPORT


def test_restore_off_deletes_the_snapshot_and_keeps_the_history(named_caller):
    m = opened_with_save()
    told = reopened(m, restore_site_data=False)
    assert m.actions.site_data_seen[-1] is None
    assert told["site_data"] == {"forgotten": 1}
    record = m.store.get(NAMED)
    assert record.site_data == {}
    assert any(v["origin"] == "https://app.example.com" for v in record.history)


def test_restore_site_data_accepts_a_string_false(named_caller):
    m = opened_with_save()
    reopened(m, restore_site_data="false")
    assert m.store.get(NAMED).site_data == {}


def test_a_failed_open_does_not_erase_what_was_saved(named_caller):
    """Declining a restore deletes only once the clean browser is open: a
    transient Grid failure must not cost the saved sign-in (Copilot, #49)."""
    m = opened_with_save()
    m.end_browser(Caller(NAMED))

    def refuse(**_):
        raise RuntimeError("the grid is full")

    m.actions.open_session = refuse
    with pytest.raises(RuntimeError):
        m.open_browser(Caller(NAMED), restore_site_data=False)
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]


def test_nothing_saved_means_no_hint(named_caller):
    m = manager(SiteActions())
    assert "site_data" not in m.open_browser(Caller(NAMED))


def test_an_insecure_open_keeps_what_was_saved(named_caller):
    """No restore into an insecure browser, but nothing is deleted either:
    the next secure browser gets it all back."""
    m = opened_with_save()
    reopened(m, insecure=True)
    assert m.store.get(NAMED).site_data["origins"]


def test_remember_keeps_site_data(named_caller):
    m = opened_with_save()
    m.open_browser(Caller(NAMED))
    m.open_browser(Caller(NAMED))
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]


def test_describe_summarises_site_data(named_caller):
    m = opened_with_save()
    assert m.describe(Caller(NAMED))["site_data"] == {"sites": 1, "uri": "workspace://site-data"}


# ---- a silent reopen says what came back, on the first result after it --------


def test_the_call_that_reopens_a_reaped_browser_says_what_came_back(named_caller):
    m = opened_with_save()
    reaped(m)
    first = m.act(Caller(NAMED), lambda s: {"url": "https://elsewhere.example.com/"})
    assert m.actions.site_data_seen[-1]["cookies"], "the reopen restored"
    assert first["site_data"] == REPORT
    assert "site_data" not in m.act(Caller(NAMED), lambda s: {"url": "https://elsewhere.example.com/"})
    assert m.store.get(NAMED).reopened == {}


def test_a_report_waits_for_a_call_that_finishes(named_caller):
    m = opened_with_save()
    reaped(m)

    def fails(_):
        raise RuntimeError("the page broke")

    with pytest.raises(RuntimeError):
        m.act(Caller(NAMED), fails)
    assert m.act(Caller(NAMED), lambda s: {"url": URL})["site_data"] == REPORT


def test_a_report_is_never_handed_to_a_result_from_another_browser(named_caller):
    m = opened_with_save()
    reaped(m)
    m.resolve(NAMED)
    told = {"url": URL}
    m.settle(NAMED, told, browser="an-older-browser")
    assert "site_data" not in told
    assert m.act(Caller(NAMED), lambda s: {"url": URL})["site_data"] == REPORT


def test_a_retried_touch_hands_over_only_what_its_last_run_saw(named_caller):
    """The first run met this browser holding the report; by the retry another
    browser is bound, nothing of this one's is committed, and nothing is told."""
    store = RetryingStore()
    m = manager(SiteActions(), store)
    m.open_browser(Caller(NAMED))
    m.act(Caller(NAMED), lambda s: m.actions.save_site_data(s))
    reaped(m)
    browser = m.resolve(NAMED)
    store.first = store.get(NAMED)
    store.between = lambda: store.update(NAMED, lambda r: replace(r, session_id="newer"))
    assert m.touch(NAMED, URL, browser=browser) is None
    assert store.get(NAMED).reopened["report"] == REPORT


def test_an_open_drops_a_report_nobody_collected(named_caller):
    m = opened_with_save()
    reaped(m)
    m.resolve(NAMED)
    assert m.open_browser(Caller(NAMED))["site_data"] == REPORT, "the open's own report"
    assert "site_data" not in m.act(Caller(NAMED), lambda s: {"url": URL})


# ---- a flow is settled the way a single call is ---------------------------------


class FlowActions(SiteActions):
    def navigate(self, session_id, url=None, **kw):
        return {"url": url, "title": "w"}


def flow_world(tmp_path, steps):
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, data={"dir": str(tmp_path)},
    ))
    server.flows.save(NAMED, "login", {"steps": steps})
    m = manager(FlowActions())
    m.open_browser(Caller(NAMED))
    return server.flows, m


def run(store, m):
    from kubed.selenium_flow.flows import api as flowapi

    return flowapi.run_for(store, m.actions, m, NAMED, "login", verbose=True)


def test_a_flow_that_saves_keeps_the_data_and_never_reports_the_capture(
    named_caller, tmp_path
):
    store, m = flow_world(tmp_path, [{"tool": "save_site_data", "args": {}, "return": True}])
    report = run(store, m)
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]
    assert site_data.CAPTURED not in json.dumps(report)
    assert report["steps"][0]["result"]["saved"]["cookies"] == 1


def test_a_flow_run_that_reopened_the_browser_says_so_once(named_caller, tmp_path):
    store, m = flow_world(tmp_path, [{"tool": "navigate", "args": {"url": "https://w.example.com/"}}])
    m.act(Caller(NAMED), lambda s: m.actions.save_site_data(s))
    reaped(m)
    report = run(store, m)
    assert report["site_data"] == REPORT
    assert "site_data" not in report["steps"][0]
    assert "site_data" not in run(store, m)


# ---- a save never reverts what landed while it worked -------------------------


def test_the_capture_is_stripped_even_when_the_store_fails(named_caller):
    m = opened_with_save()
    result = m.actions.save_site_data("x")

    def down(name):
        raise ConnectionError("redis is down")

    m.store.get = down
    with pytest.raises(ConnectionError):
        m.settle(NAMED, result, touch=False)
    assert site_data.CAPTURED not in result


def test_the_capture_is_stripped_even_when_the_touch_fails(named_caller):
    """`settle` asks the store for the touch before it stores the save: the
    capture is gone before either, or a failing touch leaves every value in
    what the caller gets."""
    m = opened_with_save()
    result = m.actions.save_site_data("x")

    def down(name, fn):
        raise ConnectionError("redis is down")

    m.store.update = down
    with pytest.raises(ConnectionError):
        m.settle(NAMED, result)
    assert site_data.CAPTURED not in result


def test_a_retried_save_into_an_expired_record_claims_nothing(named_caller):
    """The first run merged; the record expired before the retry, so nothing
    was stored and the result must not say it was."""
    store = RetryingStore()
    m = manager(SiteActions(), store)
    m.open_browser(Caller(NAMED))
    store.first = store.get(NAMED)
    store.between = lambda: store.delete(NAMED)
    result = m.actions.save_site_data("x")
    m.settle(NAMED, result, touch=False)
    assert site_data.CAPTURED not in result
    assert "saved" not in result and "uri" not in result
    assert store.get(NAMED) is None


def save_elsewhere(m, origin):
    def save(r):
        data = dict(r.site_data)
        data["origins"] = {**data.get("origins", {}), origin: {"local": {"k": "1"}}}
        return r.with_site_data(data)

    return lambda: m.store.update(NAMED, save)


def test_ending_a_browser_keeps_a_save_that_landed_while_it_quit(named_caller):
    m = opened_with_save()
    real = m.actions.end_browser

    def end(session_id):
        save_elsewhere(m, "https://late.example.com")()
        return real(session_id)

    m.actions.end_browser = end
    m.end_browser(Caller(NAMED))
    record = m.store.get(NAMED)
    assert not record.attached
    assert record.site_data["origins"].get("https://late.example.com") is not None


def test_a_save_from_a_replaced_browser_keeps_nothing(named_caller):
    """open_session(restore_site_data=false) on another request deletes the
    data; a save still finishing on the old browser must not bring it back."""
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))

    def save_while_replaced(resolved):
        m.store.update(NAMED, lambda r: replace(r, session_id="newer", site_data={}))
        return m.actions.save_site_data(resolved)

    told = m.act(Caller(NAMED), save_while_replaced)
    assert site_data.CAPTURED not in told
    assert told["saved"]["sites"] == [] and told["saved"]["cookies"] == 0
    assert told["saved"]["skipped"][0]["reason"].startswith("another session took over")
    assert m.store.get(NAMED).site_data == {}


def test_a_stale_result_does_not_move_the_newer_browsers_page_or_window(named_caller):
    """A result from a browser the record no longer names must not write its
    page or size over the newer browser's: a reap would reopen B at A's page
    (Copilot, #50). The TTL still slides."""
    m = opened_with_save()
    m.store.update(NAMED, lambda r: r.visited("https://b.test/"))

    def meanwhile(resolved):
        m.store.update(NAMED, lambda r: replace(r, session_id="newer"))
        return {"url": "https://a.test/page", "width": 640, "height": 480}

    m.act(Caller(NAMED), meanwhile, reshapes=True)
    record = m.store.get(NAMED)
    assert record.session_id == "newer"
    assert record.url == "https://b.test/"
    assert record.settings.get("width") != 640


def test_a_result_from_the_browser_held_still_moves_the_page(named_caller):
    m = opened_with_save()
    m.act(Caller(NAMED), lambda s: {"url": "https://a.test/page", "width": 640, "height": 480},
          reshapes=True)
    record = m.store.get(NAMED)
    assert record.url == "https://a.test/page"
    assert record.settings["width"] == 640


def test_a_report_survives_a_result_that_fails_after_it_was_handed_over(named_caller, monkeypatch):
    # The touch hands the report to the result; a save that then fails turns
    # that result into an error, and the report must wait for the next one.
    m = opened_with_save()
    reaped(m)
    real = m._save_site_data

    def broken(*args, **kwargs):
        raise ValueError("the cookie jar is over the cap")

    monkeypatch.setattr(m, "_save_site_data", broken)
    with pytest.raises(ValueError):
        m.act(Caller(NAMED), lambda s: {"url": URL})
    monkeypatch.setattr(m, "_save_site_data", real)
    assert m.act(Caller(NAMED), lambda s: {"url": URL})["site_data"] == REPORT
    assert "site_data" not in m.act(Caller(NAMED), lambda s: {"url": URL})


@pytest.mark.parametrize("corrupt", [
    {"cookies": 5},
    {"cookies": ["x"], "origins": {"https://app.example.com": "y"}},
    {"origins": [1], "session": {"origin": "https://app.example.com", "items": "z"}},
])
def test_a_corrupt_record_never_fails_the_clean_open_that_throws_it_away(named_caller, corrupt):
    # restore_site_data=false is the way out of a broken record, so counting
    # what it forgets must not be the thing that breaks.
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    m.store.update(NAMED, lambda r: r.with_site_data(corrupt))
    m.end_browser(Caller(NAMED))
    told = m.open_browser(Caller(NAMED), restore_site_data=False)
    assert told["workspace"] == NAMED
    assert m.store.get(NAMED).site_data == {}
