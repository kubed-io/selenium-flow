"""Where a workspace has been: the history on its record.

One entry per origin, newest first, and the current page is the top one. Only
a page with an origin enters it, and a URL withheld after a secret write
records nothing (§F1.24). Entries expire with the record's TTL, the top one
excepted."""

import json
import time

import pytest

from kubed.selenium_flow.workspace.store import (
    HISTORY_CAP,
    MemoryStore,
    RedisStore,
    Workspace,
)
from kubed.selenium_flow.workspace.workspaces import Caller

from .conftest import NAMED, RecordingActions, manager
from .fakes import FakeRedis

pytestmark = pytest.mark.unit

DAY = 86400.0


def origins(record):
    return [v["origin"] for v in record.history]


def test_a_page_bumps_its_origin_to_the_top_with_its_url_and_time():
    record = Workspace().visited("https://a.test/1", now=10.0).visited("https://b.test/", now=20.0)
    record = record.visited("https://a.test/2", now=30.0)
    assert record.history == [
        {"origin": "https://a.test", "url": "https://a.test/2", "at": 30.0},
        {"origin": "https://b.test", "url": "https://b.test/", "at": 20.0},
    ]


def test_the_current_url_is_the_top_of_the_history():
    assert Workspace().url == ""
    assert Workspace().visited("https://a.test/x", now=1.0).url == "https://a.test/x"


def test_a_page_with_no_origin_or_a_withheld_url_records_nothing():
    record = Workspace().visited("https://a.test/x", now=1.0)
    for nowhere in (None, "", "about:blank", "data:text/html,hi"):
        assert record.visited(nowhere, now=2.0).history == record.history


def test_only_the_current_page_keeps_its_query_and_fragment():
    # An OAuth callback's code, a reset link's token: values that only a reopen
    # of the current page could need.
    record = Workspace().visited("https://u:p@a.test/cb?code=XYZ#state=1", now=1.0)
    assert record.url == "https://u:p@a.test/cb?code=XYZ#state=1"
    record = record.visited("https://b.test/reset?token=T", now=2.0)
    assert [v["url"] for v in record.history] == [
        "https://b.test/reset?token=T",
        "https://a.test/cb",
    ]


def test_several_pages_are_recorded_in_order():
    record = Workspace().visited("https://a.test/", "https://b.test/", "https://a.test/z", now=5.0)
    assert [v["url"] for v in record.history] == ["https://a.test/z", "https://b.test/"]


def test_an_entry_older_than_the_ttl_goes_but_the_top_one_stays():
    record = Workspace().visited("https://old.test/", now=0.0).visited("https://mid.test/", now=10.0)
    later = record.visited(None, now=DAY + 20.0, ttl=DAY)
    assert origins(later) == ["https://mid.test"], "both expired; the top is where a reopen goes"


def test_at_most_a_hundred_entries_and_the_oldest_goes_first():
    record = Workspace()
    for i in range(HISTORY_CAP + 5):
        record = record.visited(f"https://h{i}.test/", now=float(i))
    assert len(record.history) == HISTORY_CAP
    assert record.history[0]["origin"] == f"https://h{HISTORY_CAP + 4}.test"
    assert record.history[-1]["origin"] == "https://h5.test"


@pytest.mark.parametrize(
    "store", [MemoryStore(), RedisStore(FakeRedis(), prefix="p:")], ids=lambda s: s.kind
)
def test_both_stores_round_trip_the_history(store):
    record = Workspace(session_id="s").visited("https://a.test/1", now=1.0).visited("https://b.test/2", now=2.0)
    store.set("k", record)
    assert store.get("k").history == record.history
    assert store.get("k").url == "https://b.test/2"


def test_a_record_from_before_the_history_reads_as_having_been_nowhere():
    old = Workspace.from_json('{"session_id": "s", "url": "https://a.test/x"}')
    assert (old.session_id, old.history, old.url) == ("s", [], "")


def test_a_malformed_entry_is_dropped_not_fatal():
    raw = (
        '{"session_id": "s", "history": ['
        '{"origin": "https://a.test", "url": "https://a.test/", "at": 1.0}, '
        '{"origin": 3}, "x", '
        '{"origin": "https://b.test", "url": "https://b.test/", "at": "soon"}]}'
    )
    assert Workspace.from_json(raw).history == [
        {"origin": "https://a.test", "url": "https://a.test/", "at": 1.0}
    ]
    assert Workspace.from_json('{"session_id": "s", "history": {"a": 1}}').history == []


def test_a_stored_url_that_does_not_parse_is_dropped_not_tripped_on():
    raw = json.dumps({"history": [
        {"origin": "https://a.test", "url": "https://a.test/", "at": 2.0},
        {"origin": "https://b.test", "url": "https://[broken/path", "at": 1.0},
    ]})
    record = Workspace.from_json(raw)
    assert origins(record) == ["https://a.test"]
    assert origins(record.visited("https://c.test/", now=3.0)) == ["https://c.test", "https://a.test"]


def test_touch_bumps_the_history_and_a_withheld_url_still_slides_the_ttl():
    clock = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: clock[0])
    workspaces = manager(store=store)
    workspaces.remember(NAMED, "abc", "https://a.test/")
    workspaces.touch(NAMED, "https://b.test/")
    clock[0] += 50
    workspaces.touch(NAMED, None)
    clock[0] += 50
    record = store.get(NAMED)
    assert record is not None, "the workspace expired while it was being used"
    assert origins(record) == ["https://b.test", "https://a.test"]


def test_touch_from_a_browser_the_record_no_longer_names_records_nothing():
    workspaces = manager()
    workspaces.remember(NAMED, "new", "https://a.test/")
    workspaces.touch(NAMED, "https://elsewhere.test/", browser="old")
    assert workspaces.store.get(NAMED).url == "https://a.test/"


def test_a_save_never_reads_an_origin_that_aged_out_since_the_last_call():
    # The save reads `visited` before its own touch prunes.
    m = manager(RecordingActions())
    now = time.time()
    m.store.set(NAMED, Workspace(history=[
        {"origin": "https://new.test", "url": "https://new.test/", "at": now},
        {"origin": "https://old.test", "url": "https://old.test/", "at": now - DAY - 10},
    ]))
    assert m.visited(NAMED) == ["https://new.test"]


def test_opening_another_browser_keeps_where_the_workspace_has_been():
    workspaces = manager(RecordingActions())
    workspaces.remember(NAMED, "one", "https://a.test/")
    workspaces.remember(NAMED, "two", "https://b.test/", replacing="one")
    assert origins(workspaces.store.get(NAMED)) == ["https://b.test", "https://a.test"]


def test_ending_a_browser_keeps_the_history():
    workspaces = manager(RecordingActions())
    workspaces.remember(NAMED, "one", "https://a.test/")
    workspaces.end_browser(Caller(NAMED))
    assert workspaces.store.get(NAMED).url == "https://a.test/"


# ---- a flow run writes every page it reached, in one update ------------------


def run_reporting(monkeypatch, report):
    """`run_for` around a canned run report: what reaches the history is
    decided from the report alone, in one store write."""
    from kubed.selenium_flow.flows import api as flowapi

    monkeypatch.setattr(flowapi, "run_one", lambda *a, **kw: report)
    workspaces = manager(RecordingActions())
    workspaces.open_browser(Caller(NAMED), url="https://start.test/")
    writes = []
    real = workspaces.store.update

    def counting(key, fn):
        writes.append(key)
        return real(key, fn)

    monkeypatch.setattr(workspaces.store, "update", counting)
    flowapi.run_for(None, workspaces.actions, workspaces, NAMED, "login")
    return workspaces, writes


def test_a_flow_run_records_each_steps_page_in_order_in_one_write(monkeypatch):
    workspaces, writes = run_reporting(monkeypatch, {
        "status": "ok",
        "steps": [
            {"n": 1, "ok": True, "url": "https://app.test/login"},
            {"n": 2, "ok": True, "url": "https://sso.test/auth"},
            {"n": 3, "ok": True},
            {"n": 4, "ok": True, "url": "https://app.test/home"},
        ],
        "url": "https://app.test/home",
    })
    assert [v["url"] for v in workspaces.store.get(NAMED).history] == [
        "https://app.test/home", "https://sso.test/auth", "https://start.test/",
    ]
    assert writes == [NAMED], "one update for the whole run"


def test_a_failed_step_and_a_redacted_end_record_nothing(monkeypatch):
    workspaces, _ = run_reporting(monkeypatch, {
        "status": "failed",
        "steps": [
            {"n": 1, "ok": True, "url": "https://app.test/login"},
            {"n": 2, "ok": False, "url": "https://other.test/?q=[hidden]"},
        ],
        "url": "https://other.test/?q=[hidden]",
        "url_redacted": True,
    })
    assert [v["url"] for v in workspaces.store.get(NAMED).history] == [
        "https://app.test/login", "https://start.test/",
    ]


def test_a_run_that_went_nowhere_still_slides_the_ttl(monkeypatch):
    workspaces, writes = run_reporting(monkeypatch, {"status": "ok", "steps": [{"n": 1, "ok": True}]})
    assert writes == [NAMED]
    assert workspaces.store.get(NAMED).url == "https://start.test/"
