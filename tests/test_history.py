"""Where a session has been: the history on its record.

One entry per origin, newest first, and the current page is the top one. Only
a page with an origin enters it, and a URL withheld after a secret write
records nothing (§F1.24). Entries expire with the record's TTL, the top one
excepted."""

import pytest

from kubed.selenium_flow.session.store import (
    HISTORY_CAP,
    MemoryStore,
    RedisStore,
    SessionRecord,
)

from .conftest import NAMED, RecordingActions, manager
from .test_sessions import FakeRedis

pytestmark = pytest.mark.unit

DAY = 86400.0


def origins(record):
    return [v["origin"] for v in record.history]


def test_a_page_bumps_its_origin_to_the_top_with_its_url_and_time():
    record = SessionRecord().at("https://a.test/1", now=10.0).at("https://b.test/", now=20.0)
    record = record.at("https://a.test/2", now=30.0)
    assert record.history == [
        {"origin": "https://a.test", "url": "https://a.test/2", "at": 30.0},
        {"origin": "https://b.test", "url": "https://b.test/", "at": 20.0},
    ]


def test_the_current_url_is_the_top_of_the_history():
    assert SessionRecord().url == ""
    assert SessionRecord().at("https://a.test/x", now=1.0).url == "https://a.test/x"


def test_a_page_with_no_origin_or_a_withheld_url_records_nothing():
    record = SessionRecord().at("https://a.test/x", now=1.0)
    for nowhere in (None, "", "about:blank", "data:text/html,hi"):
        assert record.at(nowhere, now=2.0).history == record.history


def test_several_pages_are_recorded_in_order():
    record = SessionRecord().at("https://a.test/", "https://b.test/", "https://a.test/z", now=5.0)
    assert [v["url"] for v in record.history] == ["https://a.test/z", "https://b.test/"]


def test_an_entry_older_than_the_ttl_goes_but_the_top_one_stays():
    record = SessionRecord().at("https://old.test/", now=0.0).at("https://mid.test/", now=10.0)
    later = record.at(None, now=DAY + 20.0, ttl=DAY)
    assert origins(later) == ["https://mid.test"], "both expired; the top is where a reopen goes"


def test_at_most_a_hundred_entries_and_the_oldest_goes_first():
    record = SessionRecord()
    for i in range(HISTORY_CAP + 5):
        record = record.at(f"https://h{i}.test/", now=float(i))
    assert len(record.history) == HISTORY_CAP
    assert record.history[0]["origin"] == f"https://h{HISTORY_CAP + 4}.test"
    assert record.history[-1]["origin"] == "https://h5.test"


@pytest.mark.parametrize(
    "store", [MemoryStore(), RedisStore(FakeRedis(), prefix="p:")], ids=lambda s: s.kind
)
def test_both_stores_round_trip_the_history(store):
    record = SessionRecord(session_id="s").at("https://a.test/1", now=1.0).at("https://b.test/2", now=2.0)
    store.set("k", record)
    assert store.get("k").history == record.history
    assert store.get("k").url == "https://b.test/2"


def test_a_record_from_before_the_history_reads_as_having_been_nowhere():
    old = SessionRecord.from_json('{"session_id": "s", "url": "https://a.test/x"}')
    assert (old.session_id, old.history, old.url) == ("s", [], "")


def test_a_malformed_entry_is_dropped_not_fatal():
    raw = (
        '{"session_id": "s", "history": ['
        '{"origin": "https://a.test", "url": "https://a.test/", "at": 1.0}, '
        '{"origin": 3}, "x", '
        '{"origin": "https://b.test", "url": "https://b.test/", "at": "soon"}]}'
    )
    assert SessionRecord.from_json(raw).history == [
        {"origin": "https://a.test", "url": "https://a.test/", "at": 1.0}
    ]
    assert SessionRecord.from_json('{"session_id": "s", "history": {"a": 1}}').history == []


def test_touch_bumps_the_history_and_a_withheld_url_still_slides_the_ttl():
    clock = [1000.0]
    store = MemoryStore(ttl=60, clock=lambda: clock[0])
    sessions = manager(store=store)
    sessions.remember(NAMED, "abc", "https://a.test/")
    sessions.touch(NAMED, "https://b.test/")
    clock[0] += 50
    sessions.touch(NAMED, None)
    clock[0] += 50
    record = store.get(NAMED)
    assert record is not None, "the session expired while it was being used"
    assert origins(record) == ["https://b.test", "https://a.test"]


def test_touch_from_a_browser_the_record_no_longer_names_records_nothing():
    sessions = manager()
    sessions.remember(NAMED, "new", "https://a.test/")
    sessions.touch(NAMED, "https://elsewhere.test/", browser="old")
    assert sessions.store.get(NAMED).url == "https://a.test/"


def test_opening_another_browser_keeps_where_the_session_has_been():
    sessions = manager(RecordingActions())
    sessions.remember(NAMED, "one", "https://a.test/")
    sessions.remember(NAMED, "two", "https://b.test/", replacing="one")
    assert origins(sessions.store.get(NAMED)) == ["https://b.test", "https://a.test"]


def test_ending_a_browser_keeps_the_history():
    sessions = manager(RecordingActions())
    sessions.remember(NAMED, "one", "https://a.test/")
    sessions.end_browser(NAMED)
    assert sessions.store.get(NAMED).url == "https://a.test/"
