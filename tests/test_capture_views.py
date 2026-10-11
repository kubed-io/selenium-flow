"""What the resources read: a window of navigations, filtered, newest first."""

import pytest

from kubed.selenium_flow.capture import MASK
from kubed.selenium_flow.capture.store import Capture
from kubed.selenium_flow.capture.views import check_filter, check_level, check_pages

from .capture_events import done, fail, log, nav_started, req

pytestmark = pytest.mark.unit

PAGES = "pages is 1 to 5: how many of the last navigations to show"


@pytest.fixture
def capture():
    c = Capture(clock=lambda: 1_791_500_000.0)
    c.attached("ws", "s1", "chrome", "top-1", again=False)
    return c


def feed(capture, *events):
    for method, params in events:
        capture.ingest("ws", "s1", method, params)


@pytest.mark.parametrize("value, expected", [
    (1, 1), ("3", 3), (" 5 ", 5), (None, 1), ("", 1),
])
def test_pages_is_coerced(value, expected):
    assert check_pages(value) == expected


@pytest.mark.parametrize("value", [0, 6, "x", "1.5", -1, True])
def test_pages_out_of_range_is_refused_with_the_sentence(value):
    with pytest.raises(ValueError) as caught:
        check_pages(value)
    assert str(caught.value) == PAGES


@pytest.mark.parametrize("value, expected", [
    ("Error", "error"), ("warn", "warn"), (" ALL ", "all"), (None, "all"), ("", "all"),
])
def test_level_is_read_loosely(value, expected):
    assert check_level(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("Failed", "failed"), ("xhr", "xhr"), (None, "all"), ("", "all"),
])
def test_filter_is_read_loosely(value, expected):
    assert check_filter(value) == expected


def test_a_bad_level_or_filter_names_the_allowed_values():
    with pytest.raises(ValueError, match="all, error, warn"):
        check_level("info")
    with pytest.raises(ValueError, match="all, failed, xhr"):
        check_filter("static")


def test_the_error_level_includes_uncaught_exceptions(capture):
    feed(capture, nav_started("https://a.test/"),
         log("logged", level="error"), log("thrown", type="javascript", level="error"),
         log("w", level="warn"), log("i"))
    view = capture.console("ws", level="error")
    assert [e["text"] for e in view["entries"]] == ["thrown", "logged"]
    assert (view["count"], view["total"]) == (2, 4)
    assert [e["text"] for e in capture.console("ws", level="warn")["entries"]] == ["w"]


def test_a_console_entry_is_shaped_for_a_reader(capture):
    feed(capture, nav_started("https://a.test/", ts=1500.0),
         log("hi", ts=2500.0, frame=("https://a.test/a.js", 1, 0)))
    view = capture.console("ws")
    assert view["capture"] is True and view["pages"] == [
        {"nav": 1, "url": "https://a.test/", "at": 1.5}]
    assert view["entries"] == [{
        "level": "info", "type": "console", "text": "hi",
        "source": {"url": "https://a.test/a.js", "line": 2},
        "context": "top-1", "at": 2.5, "seq": 1, "nav": 1,
    }]
    assert view["not_kept"] == {"debug": 0}


def test_the_failed_filter_includes_a_404_and_xhr_includes_fetch(capture):
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/x.png", dest="image"), done("r1", 404, mime="image/png"),
         req("r2", "https://a.test/f", initiator="fetch"), done("r2", 200),
         req("r3", "https://a.test/x", initiator="xmlhttprequest"), fail("r3"),
         req("r4", "https://a.test/d", dest="document", nav="n2"), done("r4", 200, mime="text/html"))
    failed = capture.network("ws", filter="failed")
    assert [r["url"] for r in failed["requests"]] == ["https://a.test/x", "https://a.test/x.png"]
    assert (failed["count"], failed["total"]) == (2, 4)
    xhr = capture.network("ws", filter="xhr")
    assert [r["url"] for r in xhr["requests"]] == ["https://a.test/x", "https://a.test/f"]


def test_pages_two_spans_two_marks(capture):
    feed(capture, nav_started("https://a.test/one"), log("1"),
         nav_started("https://a.test/two"), log("2"),
         nav_started("https://a.test/three"), log("3", level="debug"), log("3"))
    view = capture.console("ws", pages=2)
    assert [p["url"] for p in view["pages"]] == ["https://a.test/three", "https://a.test/two"]
    assert [e["text"] for e in view["entries"]] == ["3", "2"]
    assert view["not_kept"] == {"debug": 1}
    assert [e["text"] for e in capture.console("ws")["entries"]] == ["3"]


def test_network_rows_carry_no_headers_and_a_request_detail_does(capture):
    capture.secret("ws", ["tok"])
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api", headers=[("X-Token", "tok")]),
         done("r1", 500, headers=[("Content-Type", "application/json")]))
    [row] = capture.network("ws")["requests"]
    assert "request_headers" not in row and "response_headers" not in row
    detail = capture.request("ws", "1.1")
    assert detail["uri"] == "workspace://network/1.1" and detail["id"] == "1.1"
    assert detail["request_headers"] == [{"name": "X-Token", "value": MASK}]
    assert detail["response_headers"] == [{"name": "Content-Type", "value": "application/json"}]
    assert capture.request("ws", "9.9") is None and capture.request("nobody", "1.1") is None


def test_a_view_is_a_copy_the_buffer_does_not_change_under(capture):
    feed(capture, nav_started("https://a.test/?q=1"), req("r1", "https://a.test/api?k=1"))
    view = capture.network("ws")
    detail = capture.request("ws", "1.1")
    feed(capture, done("r1", 500), nav_started("https://a.test/next"))
    assert view["requests"][0]["status"] is None and view["pages"][0]["url"] == "https://a.test/?q=1"
    assert detail["url"] == "https://a.test/api?k=1"


def test_no_buffer_reads_as_none(capture):
    assert capture.console("other") is None and capture.network("other") is None
    assert capture.counts("other") is None


def test_each_listing_tallies_what_it_lists_for_a_view_cut_to_its_newest(capture):
    """R1-7: show draws the newest 200, and a chip counting the rows it got
    would count only those. The listing says the totals itself."""
    feed(capture, nav_started("https://a.test/"),
         log("e", level="error"), log("x", type="javascript"), log("w", level="warn"), log("i"),
         req("r1", "https://a.test/x.png", dest="image"), done("r1", 404, mime="image/png"),
         req("r2", "https://a.test/f", initiator="fetch"), done("r2", 200),
         req("r3", "https://a.test/x", initiator="xmlhttprequest"), fail("r3"))
    assert capture.console("ws")["tally"] == {"errors": 2, "warnings": 1}
    assert capture.console("ws", level="warn")["tally"] == {"errors": 0, "warnings": 1}
    assert capture.network("ws")["tally"] == {"failed": 2, "xhr": 2}
    assert capture.network("ws", filter="failed")["tally"] == {"failed": 2, "xhr": 1}
