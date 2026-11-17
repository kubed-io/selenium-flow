"""The workspace's side of site data: a save stored and never returned, a
restore on open and on a silent reopen, and the one report each makes."""

import contextlib
import json
import time
from dataclasses import replace

import pytest

from kubed.selenium_flow import faults
from kubed.selenium_flow.core.actions import INSECURE_SKIP
from kubed.selenium_flow.site_data import snapshot as site_data
from kubed.selenium_flow.workspace.store import MemoryStore
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces
from tests.conftest import NAMED, RecordingActions, manager
from tests.fakes import FakeMonitor

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


def idle(m, seconds, grid_timeout=300):
    """The workspace's last call and open were ``seconds`` ago."""
    then = time.time() - seconds
    m.store.update(NAMED, lambda r: replace(r, called_at=then, opened_at=then, grid_timeout=grid_timeout))


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


def test_an_insecure_browser_saves_no_site_data():
    from kubed.selenium_flow.site_data.snapshot import CAPTURED

    workspaces = Workspaces(RecordingActions())
    workspaces.open_browser(Caller(NAMED), insecure=True)
    session_id = workspaces.store.get(NAMED).session_id
    captured = {
        "cookies": [
            {
                "name": "sid",
                "value": "v",
                "domain": "bad.test",
                "path": "/",
                "http_only": True,
                "secure": True,
                "same_site": "lax",
                "expiry": None,
                "value_type": "string",
            }
        ],
        "origin": "https://bad.test",
        "local": {"k": "v"},
        "session": {},
        "others": {},
        "failed": [],
    }
    # A refusal is as good as a drop.
    with contextlib.suppress(Exception):
        workspaces.settle(
            NAMED, {"url": "https://bad.test/", CAPTURED: captured}, browser=session_id
        )
    assert not workspaces.store.get(NAMED).site_data


def test_an_insecure_save_keeps_what_a_secure_browser_saved_and_says_why(named_caller):
    m = opened_with_save()
    before = m.store.get(NAMED).site_data
    reopened(m, insecure=True)
    result = m.act(Caller(NAMED), lambda s: m.actions.save_site_data(s))
    assert site_data.CAPTURED not in result
    assert result["saved"]["skipped"] == [{"reason": INSECURE_SKIP}]
    assert m.store.get(NAMED).site_data == before


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
    assert first["reopened"] == {"cause": "lost", "message": faults.REOPENED["lost"]}
    second = m.act(Caller(NAMED), lambda s: {"url": "https://elsewhere.example.com/"})
    assert "site_data" not in second and "reopened" not in second
    assert m.store.get(NAMED).reopened == {}


def test_a_reap_with_no_site_data_is_said_once(named_caller):  # ruling 2
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    reaped(m)
    first = m.act(Caller(NAMED), lambda s: {"url": URL})
    assert first["reopened"]["cause"] == "lost" and "site_data" not in first
    assert "reopened" not in m.act(Caller(NAMED), lambda s: {"url": URL})


@pytest.mark.parametrize("grid_timeout, seconds, cause", [
    (300, 299, "lost"), (300, 300, "reaped"),
    (None, 299, "lost"), (None, 300, "reaped"),
])
def test_the_cause_is_judged_by_the_window(named_caller, grid_timeout, seconds, cause):
    m = Workspaces(SiteActions(), MemoryStore(), monitor=FakeMonitor())
    m.open_browser(Caller(NAMED))
    idle(m, seconds, grid_timeout)
    reaped(m)
    assert m.act(Caller(NAMED), lambda s: {"url": URL})["reopened"]["cause"] == cause


async def pinged_workspaces(answer):
    """A workspace whose browser the real monitor pinged twice, the Grid
    answering ``answer``; returns the manager and the browser's id."""
    from kubed.selenium_flow.monitor.events import LocalBus
    from kubed.selenium_flow.monitor.monitor import LISTING_TICK, Monitor

    from .fakes import Clock
    from .test_monitor import Listing, Pings

    clock, listing = Clock(), Listing()
    monitor = Monitor(
        LocalBus(), listing=listing, ping=Pings(answer), keep_alive=900,
        clock=clock, tick=LISTING_TICK,
    )
    workspaces = Workspaces(RecordingActions(), monitor=monitor)
    workspaces.open_browser(Caller(NAMED), url="https://a.test/")
    session_id = workspaces.store.get(NAMED).session_id
    listing.running[session_id] = 300
    for _ in range(2):
        clock.now += 240
        await monitor.look()
    return workspaces, session_id


def gone_after(workspaces, idle_for, pinged_for=None):
    """The record's last call and open were ``idle_for`` seconds ago, its last
    answered ping ``pinged_for``; the Grid then reaps the browser and a call
    reopens it. Returns the cause the reopen noted."""
    now = time.time()
    workspaces.store.update(
        NAMED,
        lambda r: replace(
            r,
            grid_timeout=300,
            called_at=now - idle_for,
            opened_at=now - idle_for - 100,
            **({} if pinged_for is None else {"pinged_at": now - pinged_for}),
        ),
    )
    workspaces.actions.grid.alive.clear()
    workspaces.resolve(NAMED)
    return workspaces.store.get(NAMED).reopened["cause"]


async def test_a_reap_after_every_keep_alive_ping_failed_is_reaped():
    """The keep-alive was owed but never reached the browser, so the node's own
    timeout is the window that applied."""
    workspaces, _ = await pinged_workspaces(ConnectionError("grid down"))
    assert workspaces.store.get(NAMED).pinged_at == 0
    assert gone_after(workspaces, 500) == "reaped"


async def test_a_browser_pinged_until_shortly_before_the_reap_is_judged_from_the_ping():
    workspaces, _ = await pinged_workspaces(True)
    assert workspaces.store.get(NAMED).pinged_at > 0
    # idle 500 s, but the Grid answered a ping 100 s ago: the node's clock had
    # not run out, so something else took it.
    assert gone_after(workspaces, 500, pinged_for=100) == "lost"


async def test_a_browser_whose_last_answered_ping_is_older_than_the_timeout_was_reaped():
    workspaces, _ = await pinged_workspaces(True)
    assert gone_after(workspaces, 500, pinged_for=310) == "reaped"


async def test_a_ping_the_grid_answered_false_is_not_recorded():
    workspaces, _ = await pinged_workspaces(False)
    assert workspaces.store.get(NAMED).pinged_at == 0


async def test_a_ping_is_recorded_on_the_record_that_holds_the_session_and_moves_no_call():
    workspaces, session_id = await pinged_workspaces(True)
    called = workspaces.store.get(NAMED).called_at
    workspaces.pinged(NAMED, "another-browser")
    before = workspaces.store.get(NAMED).pinged_at
    workspaces.pinged(NAMED, session_id)
    record = workspaces.store.get(NAMED)
    assert record.pinged_at >= before and record.called_at == called
    stale = replace(record, pinged_at=0.0)
    workspaces.store.set(NAMED, stale)
    workspaces.pinged(NAMED, "another-browser")
    assert workspaces.store.get(NAMED).pinged_at == 0.0


async def test_a_ping_for_a_session_the_record_no_longer_holds_writes_nothing():
    workspaces, _ = await pinged_workspaces(True)
    entry = workspaces.store._data[NAMED]
    workspaces.pinged(NAMED, "another-browser")
    assert workspaces.store._data[NAMED] is entry


async def test_the_servers_monitor_records_a_ping_on_the_workspace(open_server):
    """Through the wiring `SeleniumMCP` builds, not one a test makes."""
    from .fakes import Clock
    from .test_monitor import Listing, Pings

    clock = Clock()
    monitor = open_server.monitor
    monitor.ping, monitor.keep_alive, monitor.clock = Pings(True), 900, clock
    monitor.listing = Listing({"s1": 300})
    open_server.workspaces.remember(NAMED, "s1", "", {})
    monitor.opened(NAMED, "s1", "chrome", 300)
    clock.now += 240
    await monitor.look()
    assert open_server.store.get(NAMED).pinged_at > 0


def test_an_open_counts_as_activity_for_the_cause(named_caller):  # ruling 14
    """Last called yesterday, opened 10 s ago: a browser gone now was lost."""
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    now = time.time()
    m.store.update(
        NAMED,
        lambda r: replace(r, called_at=now - 1000, opened_at=now - 10, grid_timeout=300),
    )
    reaped(m)
    assert m.act(Caller(NAMED), lambda s: {"url": URL})["reopened"]["cause"] == "lost"


def test_a_call_stamps_called_at(named_caller):  # ruling 10
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    before = time.time()
    m.act(Caller(NAMED), lambda s: {"url": URL})
    assert m.store.get(NAMED).called_at >= before


def test_a_save_step_leaves_the_notice_for_the_run(named_caller, tmp_path):  # ruling 23
    store, m = flow_world(tmp_path, [{"tool": "save_site_data", "args": {}}])
    m.act(Caller(NAMED), lambda s: m.actions.save_site_data(s))
    reaped(m)
    report = run(store, m)
    assert report["reopened"]["cause"] == "lost" and report["site_data"] == REPORT


def test_a_failed_first_call_carries_the_notice_once(named_caller):  # Review Focus 1
    m = opened_with_save()
    reaped(m)

    def fails(_):
        raise RuntimeError("the page broke")

    with pytest.raises(RuntimeError) as exc:
        m.act(Caller(NAMED), fails)
    assert faults.message(exc.value) == (
        "the page broke " + faults.REOPENED["lost"] + " Saved site data was put back for 1 site(s).")
    after = m.act(Caller(NAMED), lambda s: {"url": URL})
    assert "reopened" not in after and "site_data" not in after


def test_a_failed_first_call_with_nothing_saved_says_only_the_notice(named_caller):
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    reaped(m)
    with pytest.raises(RuntimeError) as exc:
        m.act(Caller(NAMED), lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    assert faults.message(exc.value) == "x " + faults.REOPENED["lost"]


def test_a_failed_call_stamps_called_at(named_caller):  # ruling 15
    m = manager(SiteActions())
    m.open_browser(Caller(NAMED))
    m.store.update(NAMED, lambda r: replace(r, called_at=1.0))
    with pytest.raises(RuntimeError):
        m.act(Caller(NAMED), lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    assert m.store.get(NAMED).called_at > 1.0


def test_a_run_that_fails_to_start_after_a_reap_says_so(named_caller, tmp_path):
    from kubed.selenium_flow.flows import api as flowapi

    store, m = flow_world(tmp_path, [{"tool": "navigate", "args": {"url": "https://w.example.com/"}}])
    store.save(NAMED, "needs-who", {
        "parameters": {"properties": {"who": {"type": "string"}}, "required": ["who"]},
        "steps": [{"tool": "navigate", "args": {"url": "https://w.example.com/${who}"}}],
    })
    reaped(m)
    with pytest.raises(ValueError) as exc:  # the required parameter is missing
        flowapi.run_for(store, m.actions, m, NAMED, "needs-who")
    assert faults.REOPENED["lost"] in faults.message(exc.value)


def test_a_report_is_never_handed_to_a_result_from_another_browser(named_caller):
    m = opened_with_save()
    reaped(m)
    m.resolve(NAMED)
    told = {"url": URL}
    m.settle(NAMED, told, browser="an-older-browser")
    assert "site_data" not in told
    assert m.act(Caller(NAMED), lambda s: {"url": URL})["site_data"] == REPORT


def test_a_failure_from_another_browser_only_stamps_the_call(named_caller):
    m = opened_with_save()
    reaped(m)
    m.resolve(NAMED)
    before = m.store.get(NAMED)
    assert before.reopened
    error = ValueError("the old browser's call failed")
    m.failed(NAMED, error, browser="old-id")
    after = m.store.get(NAMED)
    assert not getattr(error, "__notes__", [])
    assert after.reopened == before.reopened
    assert after.called_at >= before.called_at


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
    held = store.get(NAMED).reopened
    assert held["browser"] == browser
    assert (held["cause"], held["report"]) == ("lost", REPORT)


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
    assert report["reopened"]["cause"] == "lost"
    assert "site_data" not in report["steps"][0]
    again = run(store, m)
    assert "site_data" not in again and "reopened" not in again


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


def test_a_save_keeps_the_storage_of_an_origin_visited_during_the_capture():
    from kubed.selenium_flow.site_data.snapshot import CAPTURED

    workspaces = Workspaces(RecordingActions())
    workspaces.open_browser(Caller(NAMED), url="https://b.test/")
    session_id = workspaces.store.get(NAMED).session_id
    workspaces.store.update(
        NAMED,
        lambda r: r.with_site_data(
            {
                "cookies": [],
                "origins": {"https://a.test": {"local": {"k": "v"}}},
                "session": {},
                "saved_at": 1.0,
            }
        ),
    )
    # The capture read the history as it was: only b.test. A call landing on
    # a.test before the save is written adds it to the record's history.
    captured = {
        "origin": "https://b.test",
        "local": {"x": "1"},
        "session": {},
        "cookies": [],
        "others": {},
        "failed": [],
    }
    workspaces.store.update(NAMED, lambda r: r.visited("https://a.test/"))
    workspaces.settle(
        NAMED, {CAPTURED: captured}, browser=session_id, url=None, touch=False
    )
    origins = workspaces.store.get(NAMED).site_data["origins"]
    assert origins.get("https://a.test") == {"local": {"k": "v"}}


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


def test_a_result_that_fails_after_the_touch_says_the_notice_on_its_error(named_caller, monkeypatch):
    # The touch hands the notice to the result; a save that then fails turns
    # that result into an error, and the error says it.
    m = opened_with_save()
    reaped(m)

    def broken(*args, **kwargs):
        raise ValueError("the cookie jar is over the cap")

    real = m._save_site_data
    monkeypatch.setattr(m, "_save_site_data", broken)
    with pytest.raises(ValueError) as exc:
        m.act(Caller(NAMED), lambda s: {"url": URL})
    assert faults.REOPENED["lost"] in faults.message(exc.value)
    monkeypatch.setattr(m, "_save_site_data", real)
    after = m.act(Caller(NAMED), lambda s: {"url": URL})
    assert "reopened" not in after and "site_data" not in after


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
