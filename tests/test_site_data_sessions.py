"""The session's side of site data: merge a save, restore on open, announce."""

import json

import pytest

from kubed.selenium_flow.core import site_data
from tests.conftest import NAMED, RecordingActions, manager

URL = "https://app.example.com/x"


class SiteActions(RecordingActions):
    def __init__(self):
        super().__init__()
        self.site_data_seen = []
        self.retired = []
        self.kept = []
        self.landing_only = False
        self.waiting = ["https://w.test"]
        self.arrived = None
        self.cannot_add = False

    def open_session(self, url=None, site_data=None, **kw):
        self.site_data_seen.append(site_data)
        opened = super().open_session(url=url, **kw)
        if site_data:
            opened["site_data"] = {
                "restored": ["x"], "waiting": ["https://w.test"], "skipped": [],
            }
            opened["_site_data_pending"] = {
                "origins": [] if self.landing_only else list(self.waiting),
                "script": "p1",
                **({"arrived": self.arrived} if self.arrived else {}),
            }
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

    def retire_site_data(self, session_id, script, keep=None):
        self.retired.append(script)
        self.kept.append(sorted(keep) if keep else None)
        return "" if self.cannot_add or not keep else f"{script}+"


def opened_with_save():
    m = manager(SiteActions())
    m.open_browser(NAMED)
    m.act(NAMED, lambda s: m.actions.save_site_data(s))
    return m


def reopened(m, **kw):
    m.end_browser(NAMED)
    return m.open_browser(NAMED, **kw)


def test_a_save_is_merged_into_the_record_and_never_returned_raw(named_caller):
    m = manager(SiteActions())
    m.open_browser(NAMED)
    result = m.act(NAMED, lambda s: m.actions.save_site_data(s))
    assert site_data.CAPTURED not in result
    assert result["saved"] and result["uri"] == "session://site-data"
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]


def test_open_restores_what_was_saved_and_says_so(named_caller):
    m = opened_with_save()
    told = reopened(m)
    assert m.actions.site_data_seen[-1]
    assert told["site_data"]["waiting"] == ["https://w.test"]
    assert "_site_data_pending" not in told
    record = m.store.get(NAMED)
    assert record.site_data["pending"]["browser"] == record.session_id


def test_restore_off_deletes_and_says_how_many(named_caller):
    m = opened_with_save()
    told = reopened(m, restore_site_data=False)
    assert m.actions.site_data_seen[-1] is None
    assert told["site_data"] == {"forgotten": 1}
    assert m.store.get(NAMED).site_data == {}


def test_a_failed_open_does_not_erase_what_was_saved(named_caller):
    """Declining a restore deletes only once the clean browser is open: a
    transient Grid failure must not cost the saved sign-in (Copilot, #49)."""
    m = opened_with_save()
    m.end_browser(NAMED)

    def refuse(**_):
        raise RuntimeError("the grid is full")

    m.actions.open_session = refuse
    with pytest.raises(RuntimeError):
        m.open_browser(NAMED, restore_site_data=False)
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]


def test_nothing_saved_means_no_hint(named_caller):
    m = manager(SiteActions())
    assert "site_data" not in m.open_browser(NAMED)


def test_arriving_on_a_waiting_origin_is_announced_once_and_retires_the_script(
    named_caller,
):
    m = opened_with_save()
    reopened(m)
    arrive = {"url": "https://w.test/page"}
    first = m.act(NAMED, lambda s: dict(arrive))
    assert first["site_data"] == {
        "restored": ["https://w.test"], "uri": "session://site-data/w.test",
    }
    assert m.actions.retired == ["p1"]
    assert "site_data" not in m.act(NAMED, lambda s: dict(arrive))
    assert m.actions.retired == ["p1"]


def test_a_quiet_call_carries_no_hint(named_caller):
    m = opened_with_save()
    reopened(m)
    quiet = m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})
    assert "site_data" not in quiet


def test_a_silent_reopen_restores_and_the_next_result_says_so(named_caller):
    m = opened_with_save()
    m.actions.grid.alive.clear()
    first = m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})
    assert m.actions.site_data_seen[-1]
    assert first["site_data"]["waiting"] == ["https://w.test"]
    assert "site_data" not in m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})


def test_remember_keeps_site_data(named_caller):
    m = opened_with_save()
    m.open_browser(NAMED)
    m.open_browser(NAMED)
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]


def test_describe_summarises_site_data(named_caller):
    m = opened_with_save()
    assert m.describe(NAMED)["site_data"] == {
        "sites": 1, "uri": "session://site-data",
    }


def test_restore_site_data_accepts_a_string_false(named_caller):
    m = opened_with_save()
    reopened(m, restore_site_data="false")
    assert m.store.get(NAMED).site_data == {}


def test_a_landing_only_restore_retires_the_script_at_open(named_caller):
    m = opened_with_save()
    m.actions.landing_only = True
    reopened(m)
    assert m.actions.retired == ["p1"]
    assert "pending" not in m.store.get(NAMED).site_data


def test_a_landing_only_silent_reopen_retires_and_still_announces(named_caller):
    m = opened_with_save()
    m.actions.landing_only = True
    m.actions.grid.alive.clear()
    first = m.act(NAMED, lambda s: {"url": "https://elsewhere.test"})
    assert m.actions.retired == ["p1"]
    assert first["site_data"]["restored"] == ["x"]
    assert "pending" not in m.store.get(NAMED).site_data
    assert "site_data" not in m.act(NAMED, lambda s: {"url": "https://e.test"})


def test_the_pending_note_never_reaches_the_browser(named_caller):
    m = opened_with_save()
    reopened(m)
    reopened(m)
    assert m.actions.site_data_seen[-1]
    assert "pending" not in m.actions.site_data_seen[-1]


# ---- a flow step is settled the same way a single call is ---------------------


class FlowActions(SiteActions):
    def navigate(self, session_id, url=None, **kw):
        return {"url": "https://w.test/page", "title": "w"}


def flow_world(tmp_path, steps):
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, flow={"data_dir": str(tmp_path)},
    ))
    server.flows.save(NAMED, "login", {"steps": steps})
    return server.flows, manager(FlowActions())


def run_flow(store, m, **kw):
    from kubed.selenium_flow.flows import api as flowapi

    m.open_browser(NAMED)
    return m, flowapi.run_for(store, m.actions, m, NAMED, "login", **kw)


def test_a_flow_that_saves_keeps_the_data_and_never_reports_the_capture(
    named_caller, tmp_path
):
    store, m = flow_world(tmp_path, [{"tool": "save_site_data", "args": {}, "return": True}])
    m, report = run_flow(store, m, verbose=True)
    assert list(m.store.get(NAMED).site_data["origins"]) == ["https://app.example.com"]
    assert site_data.CAPTURED not in json.dumps(report)
    assert report["steps"][0]["result"]["saved"]["cookies"] == 1


def test_a_flow_step_arriving_on_a_waiting_origin_carries_the_hint(
    named_caller, tmp_path
):
    store, m = flow_world(tmp_path, [{"tool": "navigate", "args": {"url": "https://w.test/page"}}])
    m.open_browser(NAMED)
    m.act(NAMED, lambda s: m.actions.save_site_data(s))
    reopened(m)
    report = run_flow(store, m)[1]
    assert report["steps"][0]["site_data"]["restored"] == ["https://w.test"]


# ---- one tab's arrival never refills another's --------------------------------


def with_storage_for(m, *origins):
    record = m.store.get(NAMED)
    data = dict(record.site_data)
    data["origins"] = {
        **data.get("origins", {}),
        **{o: {"local": {"k": o}, "session": {}, "saved_at": 1.0} for o in origins},
    }
    m.store.set(NAMED, record.with_site_data(data))


def test_an_arrival_swaps_the_script_for_one_carrying_only_what_waits(named_caller):
    m = opened_with_save()
    with_storage_for(m, "https://v.test", "https://w.test")
    m.actions.waiting = ["https://v.test", "https://w.test"]
    reopened(m)
    m.act(NAMED, lambda s: {"url": "https://w.test/page"})
    assert m.actions.retired == ["p1"]
    assert m.actions.kept == [["https://v.test"]], "w.test no longer rides along"
    pending = m.store.get(NAMED).site_data["pending"]
    assert pending["script"] == "p1+" and pending["origins"] == ["https://v.test"]
    m.act(NAMED, lambda s: {"url": "https://v.test/"})
    assert m.actions.retired == ["p1", "p1+"] and m.actions.kept[-1] is None
    assert "pending" not in m.store.get(NAMED).site_data


def test_a_landing_that_arrived_at_open_is_swapped_out_at_once(named_caller):
    m = opened_with_save()
    with_storage_for(m, "https://w.test")
    m.actions.arrived = ["https://app.example.com"]
    reopened(m)
    assert m.actions.retired == ["p1"] and m.actions.kept == [["https://w.test"]]
    pending = m.store.get(NAMED).site_data["pending"]
    assert pending["script"] == "p1+" and "arrived" not in pending


def test_origins_that_cannot_fill_are_not_announced_later(named_caller):
    m = opened_with_save()
    with_storage_for(m, "https://v.test", "https://w.test")
    m.actions.waiting = ["https://v.test", "https://w.test"]
    m.actions.cannot_add = True
    reopened(m)
    m.act(NAMED, lambda s: {"url": "https://w.test/page"})
    assert "pending" not in m.store.get(NAMED).site_data
    told = m.act(NAMED, lambda s: {"url": "https://v.test/"})
    assert "site_data" not in told


def test_a_landing_swap_that_cannot_add_stops_waiting(named_caller):
    m = opened_with_save()
    with_storage_for(m, "https://w.test")
    m.actions.arrived = ["https://app.example.com"]
    m.actions.cannot_add = True
    reopened(m)
    assert "pending" not in m.store.get(NAMED).site_data


def test_the_capture_is_stripped_even_when_the_store_fails(named_caller):
    m = opened_with_save()
    result = m.actions.save_site_data("x")

    def down(name):
        raise ConnectionError("redis is down")

    m.store.get = down
    with pytest.raises(ConnectionError):
        m.settle(NAMED, result, touch=False)
    assert site_data.CAPTURED not in result
