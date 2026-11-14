"""`Capture`: what is kept, counted, merged, aligned, expired, indexed and tailed."""

import asyncio

import pytest

from kubed.selenium_flow.capture import (
    CONSOLE_LIMIT,
    DEFAULT_TTL,
    HEADER_BYTES,
    MASK,
    REPLAY_DEPTH,
    REQUEST_LIMIT,
    TAIL_QUEUE,
    TEXT_CAP,
    WORKSPACES,
)
from kubed.selenium_flow.capture.store import CONTEXTS, Capture, Located, Mark

from .capture_events import (
    chrome_timings,
    context_created,
    context_destroyed,
    done,
    fail,
    firefox_timings,
    log,
    nav_event,
    nav_started,
    req,
)

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self, now=1_791_500_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def capture(clock):
    return Capture(clock=clock)


def attach(capture, browser="chrome", top="top-1", *, workspace="ws", session_id="s1"):
    capture.attached(workspace, session_id, browser, top, again=False)


def feed(capture, *events, workspace="ws", session_id="s1"):
    for method, params in events:
        capture.ingest(workspace, session_id, method, params)


def texts(capture, **query):
    return [e["text"] for e in capture.console("ws", **query)["entries"]]


# ---- the brief's -------------------------------------------------------------


def test_dropped_events_are_carried_only_once_there_are_some(capture):
    """Spotless 7: the capture listener's queue overflowing was a log line and
    nothing a reader looks at. Every view now says how many were lost."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("kept"))
    assert "dropped" not in capture.console("ws") and "dropped" not in capture.network("ws")
    assert "dropped" not in capture.counts("ws")
    for _ in range(12):
        capture.lost("ws", "s1")
    assert capture.console("ws")["dropped"] == 12
    assert capture.network("ws", filter="failed")["dropped"] == 12
    assert capture.counts("ws")["dropped"] == 12


def test_a_drop_from_a_browser_capture_does_not_hear_is_not_counted(capture):
    attach(capture)
    feed(capture, log("kept"))
    capture.lost("ws", "nobody")
    capture.lost("other", "s1")
    assert "dropped" not in capture.console("ws")


def test_the_console_buffer_keeps_the_newest_thousand(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), *[log(f"m{i}", ts=1000.0 + i) for i in range(1500)])
    view = capture.console("ws", pages=5)
    assert view["total"] == CONSOLE_LIMIT and view["entries"][0]["text"] == "m1499"


def test_debug_is_counted_not_kept(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("d", level="debug"), log("i"))
    view = capture.console("ws")
    assert [e["text"] for e in view["entries"]] == ["i"] and view["not_kept"] == {"debug": 1}


def test_successful_static_is_counted_not_kept_and_a_failed_image_is_kept(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/a.css", dest="style"), done("r1", 200, mime="text/css"),
         req("r2", "https://a.test/x.png", dest="image"), done("r2", 404, mime="image/png"),
         req("r3", "https://a.test/api", initiator="fetch"), done("r3", 500))
    view = capture.network("ws")
    assert [r["url"] for r in view["requests"]] == ["https://a.test/api", "https://a.test/x.png"]
    assert view["not_kept"] == {"static": 1}
    assert capture.counts("ws") == {"console": 0, "network": 3, "errors": 0, "failed_requests": 1}


def test_requests_merge_by_id_and_redirect(capture):
    """As Chrome sends them: each event stamped with its request's start, and
    the duration only in ``timings``, relative to ``timeOrigin``."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/login", dest="document", nav="n1", ts=2000.0),
         done("r1", 302, mime="text/html", ts=2000.0, timings=chrome_timings(2000.0, 10.2)),
         req("r1", "https://a.test/home", dest="document", nav="n1", redirect=1, ts=2011.0),
         done("r1", 200, mime="text/html", redirect=1, ts=2011.0,
              timings=chrome_timings(2011.0, 39.4)))
    rows = capture.network("ws")["requests"]
    assert [(r["id"], r["status"], r["took_ms"]) for r in rows] == [("1.2", 200, 39), ("1.1", 302, 10)]


def test_a_rows_headers_are_bounded_a_side_and_what_was_dropped_is_counted(capture):
    attach(capture)
    big = [(f"x-{i}", "v" * 5000) for i in range(40)]
    feed(capture, nav_started("https://a.test/"), req("r1", "https://a.test/api", headers=big))
    assert capture.request("ws", "1.1")["headers_dropped"] == 37
    feed(capture, done("r1", 500, headers=big))
    detail = capture.request("ws", "1.1")
    assert detail["headers_dropped"] == 74
    for side in ("request_headers", "response_headers"):
        assert sum(len(h["name"].encode()) + len(h["value"].encode()) for h in detail[side]) <= HEADER_BYTES
    assert "headers_dropped" not in capture.network("ws")["requests"][0]


def test_firefox_takes_its_duration_from_its_epoch_timings(capture):
    """Network time, as DevTools shows it: not the events' stamps, which on
    Firefox include delivering the body."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api", ts=1_791_663_814_846.0),
         done("r1", 200, ts=1_791_663_814_976.0,
              timings=firefox_timings(1_791_663_814_842.131, 1_791_663_814_890.473)))
    assert capture.network("ws")["requests"][0]["took_ms"] == 48


def test_without_a_response_end_the_events_stamps_are_the_duration(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api", ts=1000.0),
         done("r1", 500, ts=1120.0, timings={"timeOrigin": 0, "requestTime": 0, "responseEnd": 0}),
         req("r2", "https://a.test/api2", ts=1000.0),
         done("r2", 500, ts=1030.0))
    rows = capture.network("ws")["requests"]
    assert [(r["id"], r["took_ms"]) for r in rows] == [("1.2", 30), ("1.1", 120)]


def test_a_duration_nothing_measured_is_unknown_never_zero(capture):
    """Chrome's fallback: equal stamps say nothing, so the row says it does not
    know rather than that the request took no time."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api", ts=1000.0),
         fail("r1", ts=1000.0, timings=chrome_timings(1000.0, 0)),
         req("r2", "https://a.test/api2", ts=1000.0),
         done("r2", 200, ts=1000.0, timings={"timeOrigin": True, "requestTime": 0, "responseEnd": True}))
    rows = capture.network("ws")["requests"]
    assert [r["took_ms"] for r in rows] == [None, None]


def test_size_is_body_size_never_content_size(capture):
    attach(capture)
    _, completed = done("r1", 200, body_size=None)
    completed["response"].update({"bytesReceived": 777, "content": {"size": 99999}})
    feed(capture, nav_started("https://a.test/"), req("r1", "https://a.test/api"),
         ("network.responseCompleted", completed))
    assert capture.network("ws")["requests"][0]["size"] == 777


def test_only_the_newest_navigation_keeps_its_query(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/cb?code=abc"),
         req("r1", "https://a.test/api?token=t1"), done("r1", 200),
         nav_started("https://a.test/home?tab=2"),
         req("r2", "https://a.test/api?page=2"), done("r2", 200))
    rows = capture.network("ws", pages=2)["requests"]
    assert [r["url"] for r in rows] == ["https://a.test/api?page=2", "https://a.test/api"]
    assert [p["url"] for p in capture.console("ws", pages=2)["pages"]] == [
        "https://a.test/home?tab=2", "https://a.test/cb"]


def test_the_spare_tab_is_never_kept_counted_or_tailed(capture):
    attach(capture)
    with capture.hidden("s1", "spare-1"):
        feed(capture, req("r9", "https://b.test/__selenium-flow-spare__", ctx="spare-1"), done("r9", 200),
             log("from spare", ctx="spare-1"))
    assert capture.counts("ws") == {"console": 0, "network": 0, "errors": 0, "failed_requests": 0}


def test_another_tab_marks_no_navigation(capture):
    attach(capture, top="top-1")
    feed(capture, nav_started("https://a.test/"), nav_started("https://popup.test/", ctx="tab-2"))
    assert [p["url"] for p in capture.console("ws", pages=5)["pages"]] == ["https://a.test/"]


def test_chrome_replay_lands_only_the_unmatched_suffix(capture):
    attach(capture, browser="chrome")
    first = [log(f"m{i}", ts=1000.0 + i) for i in range(3)]
    feed(capture, nav_started("https://a.test/"), *first)
    capture.attached("ws", "s1", "chrome", "top-1", again=True)
    feed(capture, *first[1:], log("m3", ts=1003.0))      # Chrome's burst: m1, m2, then the new m3
    capture.replayed("ws", "s1")
    assert [e["text"] for e in capture.console("ws")["entries"]] == ["m3", "m2", "m1", "m0"]


def test_firefox_holds_nothing_on_reconnect(capture):
    attach(capture, browser="firefox")
    tick = log("tick", ts=1000.0)
    feed(capture, nav_started("https://a.test/"), tick)
    capture.attached("ws", "s1", "firefox", "top-1", again=True)
    feed(capture, tick)                                   # a real repeat, not a replay
    capture.replayed("ws", "s1")
    assert [e["text"] for e in capture.console("ws")["entries"]] == ["tick", "tick"]


def test_the_ttl_slides_on_every_entry_and_expires_after_the_last(capture, clock):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("a"))
    clock.now += DEFAULT_TTL - 1
    feed(capture, log("b"))
    clock.now += DEFAULT_TTL - 1
    assert capture.console("ws") is not None
    clock.now += 2
    assert capture.console("ws") is None


def test_mark_and_caused_count_this_window_only(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("old", level="error"))
    since = capture.mark("ws")
    feed(capture, log("boom", type="javascript", level="error"), log("w", level="warn"),
         req("r1", "https://a.test/api"), done("r1", 503),
         req("r2", "https://a.test/i.png", dest="image"), fail("r2"))
    assert capture.caused("ws", since) == {"errors": 1, "failed_requests": 1}


def test_the_index_finds_a_request_by_session_and_id(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), req("r1", "https://a.test/api"), done("r1", 200))
    assert capture.request_for("s1", "1.1") == Located("s1", "r1", "application/json", 10)
    assert capture.request_for("s1", "9.9") is None and capture.request_for("other", "1.1") is None


async def test_a_tail_gets_every_row_masked_and_reports_its_drops(capture):
    attach(capture)
    capture.secret("ws", ["hunter2"])
    tail = capture.tail("ws", "console")
    feed(capture, log("pw hunter2"), *[log(f"m{i}") for i in range(TAIL_QUEUE + 5)])
    assert await tail.next() == {"dropped": 6}
    first = await tail.next()
    assert first["text"] == f"pw {MASK}" and first["kept"] is True
    tail.close()


async def test_a_catalogued_secret_reaches_a_row_as_its_marker(capture):
    from kubed.selenium_flow.redaction import Forms

    attach(capture)
    capture.secret("ws", Forms.of("hunter22", ("ldap", "password")))
    tail = capture.tail("ws", "console")
    feed(capture, log("pw hunter22"))
    assert (await tail.next())["text"] == "pw [secret:ldap/password]"
    tail.close()


def test_discard_forgets_the_workspace(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("a"))
    capture.discard("ws")
    assert capture.console("ws") is None and capture.mark("ws") is None


# ---- the top-level context and lifecycle events ------------------------------


def test_a_later_top_level_context_created_does_not_replace_top(capture):
    """The spare tab's `contextCreated` can arrive before it is hidden."""
    attach(capture, top="top-1")
    feed(capture, context_created("spare-1"), nav_started("https://spare.test/", ctx="spare-1"),
         nav_started("https://a.test/"))
    assert [p["url"] for p in capture.console("ws", pages=5)["pages"]] == ["https://a.test/"]


def test_a_top_level_context_created_sets_top_only_when_none_was_known(capture):
    attach(capture, top=None)
    feed(capture, context_created("frame-1", parent="top-1"), context_created("top-1"),
         context_created("tab-2"), nav_started("https://a.test/"),
         nav_started("https://b.test/", ctx="tab-2"))
    assert [p["url"] for p in capture.console("ws", pages=5)["pages"]] == ["https://a.test/"]


def test_lifecycle_events_make_no_rows_counts_marks_or_tail_rows(capture, clock):
    attach(capture)
    tail = capture.tail("ws", "console")
    feed(capture, context_created("tab-2"), context_destroyed("tab-2"), context_destroyed("spare-1"))
    assert capture.counts("ws") == {"console": 0, "network": 0, "errors": 0, "failed_requests": 0}
    assert capture.console("ws")["pages"] == [] and tail._queue.empty()
    tail.close()


def test_committed_history_and_fragment_update_the_current_mark_only(capture):
    attach(capture)
    feed(capture, nav_event("navigationCommitted", "https://a.test/"),  # before any start: nothing
         nav_started("https://a.test/"),
         nav_event("navigationCommitted", "https://a.test/landed"),
         nav_event("historyUpdated", "https://a.test/route?x=1"),
         nav_event("fragmentNavigated", "https://a.test/route?x=1#top"),
         nav_event("historyUpdated", "https://other.test/", ctx="tab-2"))
    pages = capture.console("ws", pages=5)["pages"]
    assert [(p["nav"], p["url"]) for p in pages] == [(1, "https://a.test/route?x=1#top")]


def test_before_the_first_navigation_the_window_is_nav_zero(capture):
    attach(capture)
    feed(capture, log("early"), req("r1", "https://a.test/api"), done("r1", 500))
    assert texts(capture) == ["early"]
    assert [r["id"] for r in capture.network("ws")["requests"]] == ["0.1"]
    assert capture.counts("ws") == {"console": 1, "network": 1, "errors": 0, "failed_requests": 1}


def test_counts_are_since_the_last_navigation(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("e", level="error"), log("i"),
         req("r1", "https://a.test/api"), done("r1", 500),
         nav_started("https://a.test/next"), log("w", level="warn"),
         req("r2", "https://a.test/i.png", dest="image"), done("r2", 200, mime="image/png"))
    assert capture.counts("ws") == {"console": 1, "network": 1, "errors": 0, "failed_requests": 0}


# ---- requests ------------------------------------------------------------------


def test_an_in_flight_request_is_visible_and_a_static_one_is_not(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api"), req("r2", "https://a.test/i.png", dest="image"))
    [row] = capture.network("ws")["requests"]
    assert (row["url"], row["status"], row["took_ms"]) == ("https://a.test/api", None, None)
    assert capture.counts("ws")["network"] == 2


def test_a_type_learned_from_the_mime_can_make_a_row_static(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/logo", initiator="other"), done("r1", 200, mime="image/png"))
    view = capture.network("ws")
    assert view["requests"] == [] and view["not_kept"] == {"static": 1}
    assert capture.request_for("s1", "1.1") is None


def test_a_failed_request_is_kept_with_its_error(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/f.woff", dest="font", ts=1000.0),
         fail("r1", error="net::ERR_ABORTED", ts=1250.0))
    [row] = capture.network("ws")["requests"]
    assert (row["type"], row["failed"], row["error"], row["took_ms"]) == (
        "font", True, "net::ERR_ABORTED", 250)
    assert capture.counts("ws")["failed_requests"] == 0          # a font, not hinted


def test_an_end_for_a_request_never_seen_starting_is_ignored(capture):
    """Its start was evicted, came before the hold, or was the spare tab's —
    whose end can arrive after the tab stops being hidden."""
    attach(capture)
    with capture.hidden("s1", "spare-1"):
        feed(capture, req("r9", "https://b.test/spare", ctx="spare-1"))
    feed(capture, nav_started("https://a.test/"), done("r9", 404, ctx="spare-1"),
         fail("r7", url="https://a.test/api?k=1"))
    assert capture.network("ws")["requests"] == []
    assert capture.counts("ws") == {"console": 0, "network": 0, "errors": 0, "failed_requests": 0}


def test_credentials_cookies_and_post_data_never_reach_a_row(capture):
    attach(capture)
    capture.secret("ws", ["s3cr3t"])
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api?pw=s3cr3t",
             headers=[("Authorization", "Bearer abc"), ("X-Echo", "it is s3cr3t")]),
         done("r1", 200, headers=[("Set-Cookie", "sid=1"), ("X-Ok", "fine")]))
    detail = capture.request("ws", "1.1")
    assert detail["url"] == f"https://a.test/api?pw={MASK}"
    assert detail["request_headers"] == [
        {"name": "Authorization", "value": MASK}, {"name": "X-Echo", "value": f"it is {MASK}"}]
    assert detail["response_headers"] == [
        {"name": "Set-Cookie", "value": MASK}, {"name": "X-Ok", "value": "fine"}]
    flat = repr(detail)
    assert "COOKIE-VALUE" not in flat and "POST-DATA" not in flat and "s3cr3t" not in flat


def test_a_secret_is_masked_in_every_text_field_of_a_row(capture):
    """A reason phrase is free text from the server, a method the page's own."""
    attach(capture)
    capture.secret("ws", ["hunter2"])
    tail = capture.tail("ws", "network")
    _, completed = done("r1", 400, mime="text/hunter2")
    completed["response"]["statusText"] = "bad password hunter2"
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api", method="hunter2"), ("network.responseCompleted", completed),
         req("r2", "https://a.test/b"), fail("r2", error="net::hunter2"))
    rows = [capture.request("ws", "1.1"), capture.request("ws", "1.2"),
            *capture.network("ws")["requests"], tail._queue.get_nowait(), tail._queue.get_nowait()]
    assert "hunter2" not in repr(rows)
    assert rows[0]["status_text"] == f"bad password {MASK}" and rows[0]["method"] == MASK
    tail.close()


def test_older_rows_lose_their_query_in_url_bearing_headers_too(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/go", headers=[("Referer", "https://a.test/?code=abc")]),
         done("r1", 302, headers=[("Location", "https://a.test/cb?code=abc"), ("X-Q", "a?b")]),
         req("r2", "https://a.test/slow?t=1", headers=[("Referer", "https://a.test/?code=abc")]),
         nav_started("https://a.test/cb?code=abc"),
         done("r2", 302, headers=[("Content-Location", "https://a.test/x?code=abc")]))
    r1, r2 = capture.request("ws", "1.1"), capture.request("ws", "1.2")
    assert r1["request_headers"] == [{"name": "Referer", "value": "https://a.test/"}]
    assert r1["response_headers"] == [
        {"name": "Location", "value": "https://a.test/cb"}, {"name": "X-Q", "value": "a?b"}]
    assert r2["url"] == "https://a.test/slow"
    assert r2["response_headers"] == [{"name": "Content-Location", "value": "https://a.test/x"}]


def test_an_old_console_source_loses_its_query_too(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         log("x", frame=("https://a.test/a.js?k=1", 0, 0)), nav_started("https://a.test/b"))
    [entry] = capture.console("ws", pages=2)["entries"]
    assert entry["source"] == {"url": "https://a.test/a.js", "line": 1}


def test_a_huge_url_is_cut(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), req("r1", "data:text/plain," + "x" * 100_000))
    assert len(capture.network("ws")["requests"][0]["url"]) == TEXT_CAP


def test_an_evicted_row_takes_its_index_entry_with_it(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         *[req(f"r{i}", f"https://a.test/{i}") for i in range(REQUEST_LIMIT + 1)])
    assert capture.request_for("s1", "1.1") is None
    assert capture.request_for("s1", f"1.{REQUEST_LIMIT + 1}") is not None
    buffer = capture._cache["ws"]
    assert len(buffer.index) == REQUEST_LIMIT


def test_requests_in_flight_are_capped(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         *[req(f"r{i}", f"https://a.test/{i}.png", dest="image") for i in range(REQUEST_LIMIT + 5)])
    assert len(capture._cache["ws"].in_flight) == REQUEST_LIMIT
    feed(capture, done("r0", 404, mime="image/png"))           # its start was evicted: ignored
    assert capture.request("ws", "1.1") is None


def test_a_new_session_keeps_the_buffer_and_the_old_index(capture):
    """Task 8 reads a Located for the old browser as gone, by its session id."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"), req("r1", "https://a.test/api"), done("r1", 200))
    capture.attached("ws", "s2", "chrome", "top-9", again=False)
    assert capture.request_for("s2", "1.1") == Located("s1", "r1", "application/json", 10)
    assert capture.request_for("s1", "1.1") is None
    feed(capture, log("old browser"), session_id="s1")          # the ended browser is not heard
    assert texts(capture) == []


def test_a_new_session_in_the_workspace_keeps_its_secrets(capture):
    """A secret typed before a reap or a reopen can be echoed by the next
    browser (a restored login shows it): the workspace's secrets carry over,
    and no other workspace gets them (PR review M7)."""
    attach(capture)
    attach(capture, workspace="other", session_id="o1")
    capture.secret("ws", ["hunter2"])
    capture.attached("ws", "s2", "chrome", "top-1", again=False)
    feed(capture, log("hunter2"), session_id="s2")
    feed(capture, log("hunter2"), workspace="other", session_id="o1")
    assert texts(capture) == [MASK]
    assert [e["text"] for e in capture.console("other")["entries"]] == ["hunter2"]


def test_a_credential_a_header_carried_is_masked_by_the_session_that_replaces_it(capture):
    """A reopen restores the login, so the next browser can echo the token the
    last one sent before it sends it itself."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api", headers=[("Authorization", "Bearer abcdefgh12345")]))
    capture.attached("ws", "s2", "chrome", "top-1", again=False)
    feed(capture, log("token abcdefgh12345"), session_id="s2")
    assert texts(capture) == [f"token {MASK}"]


def test_an_expired_buffer_comes_back_empty_with_the_sessions_secrets(capture, clock):
    attach(capture)
    capture.secret("ws", ["hunter2"])
    feed(capture, nav_started("https://a.test/"), log("before"))
    clock.now += DEFAULT_TTL + 1
    assert capture.console("ws") is None
    feed(capture, log("pw hunter2"))
    assert texts(capture) == [f"pw {MASK}"]


def test_a_session_never_attached_is_not_heard(capture):
    feed(capture, log("stray"), session_id="nobody")
    assert capture.console("ws") is None


def test_a_hold_again_of_a_replaced_session_is_not_heard(capture):
    attach(capture)
    capture.attached("ws", "s2", "chrome", "top-2", again=False)
    capture.attached("ws", "s1", "chrome", "top-1", again=True)   # the old watch, late
    feed(capture, log("old"), session_id="s1")
    feed(capture, log("new", ctx="top-2"), session_id="s2")
    assert texts(capture) == ["new"]


def test_a_hold_again_after_a_discard_is_not_heard(capture):
    attach(capture)
    capture.discard("ws")
    capture.attached("ws", "s1", "chrome", "top-1", again=True)
    feed(capture, log("after"))
    assert capture.console("ws") is None


def test_detached_without_attached_is_a_no_op(capture):
    capture.detached("ws", "s1")
    capture.replayed("ws", "s1")
    assert capture.console("ws") is None


# ---- Chrome's replay, held and bounded -----------------------------------------


def test_detached_drops_what_was_held(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("m0", ts=1.0), log("m1", ts=2.0))
    capture.attached("ws", "s1", "chrome", "top-1", again=True)
    feed(capture, log("m0", ts=1.0))
    capture.detached("ws", "s1")
    feed(capture, log("m2", ts=3.0))
    assert texts(capture) == ["m2", "m1", "m0"]


def test_a_context_never_seen_is_not_held(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("m0", ts=1.0))
    capture.attached("ws", "s1", "chrome", "top-1", again=True)
    feed(capture, log("frame says", ctx="frame-1"))
    assert texts(capture) == ["frame says", "m0"]


def test_a_held_context_lands_once_its_replay_is_decided(capture):
    """Once a context has held as many as were seen, the overlap cannot change,
    so it lands then: what is held never exceeds REPLAY_DEPTH per context."""
    attach(capture)
    seen = [log(f"m{i}", ts=float(i)) for i in range(REPLAY_DEPTH + 20)]
    feed(capture, nav_started("https://a.test/"), *seen)
    capture.attached("ws", "s1", "chrome", "top-1", again=True)
    feed(capture, *seen[-REPLAY_DEPTH:])
    assert capture._sessions["s1"].holding == {}
    feed(capture, *[log(f"n{i}", ts=1000.0 + i) for i in range(3)])
    assert texts(capture)[:4] == ["n2", "n1", "n0", f"m{REPLAY_DEPTH + 19}"]
    capture.replayed("ws", "s1")
    assert len(texts(capture, pages=5)) == REPLAY_DEPTH + 23


def test_the_contexts_remembered_for_a_replay_are_bounded_and_pruned(capture):
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         *[log("x", ctx=f"frame-{i}") for i in range(CONTEXTS + 10)])
    recent = capture._sessions["s1"].recent
    assert len(recent) == CONTEXTS and "frame-0" not in recent
    feed(capture, context_destroyed(f"frame-{CONTEXTS + 9}"))
    assert f"frame-{CONTEXTS + 9}" not in recent


def test_the_hidden_set_is_gone_once_the_block_ends(capture):
    attach(capture)
    with capture.hidden("s1", "spare-1"):
        assert capture._hidden == {"s1": {"spare-1"}}
    assert capture._hidden == {}
    feed(capture, log("now heard", ctx="spare-1"))
    assert texts(capture) == ["now heard"]


def test_the_sessions_remembered_are_bounded(capture):
    for i in range(WORKSPACES + 3):
        attach(capture, workspace=f"w{i}", session_id=f"s{i}")
        capture.detached(f"w{i}", f"s{i}")
    assert len(capture._sessions) == WORKSPACES and "s0" not in capture._sessions


def test_a_session_still_held_is_never_evicted(capture):
    """Evicting a held session's `Held` would stop its capture silently: its
    events are only heard for a session capture knows (PR review M9)."""
    attach(capture)
    for i in range(WORKSPACES + 3):
        attach(capture, workspace=f"w{i}", session_id=f"s{i}x")
        capture.detached(f"w{i}", f"s{i}x")
    feed(capture, log("still heard"))
    assert texts(capture) == ["still heard"]
    assert len(capture._sessions) == WORKSPACES


def evict_idle(capture):
    """Pause ``ws``'s session and push it out with WORKSPACES idle others."""
    capture.detached("ws", "s1")
    for i in range(WORKSPACES + 1):
        attach(capture, workspace=f"w{i}", session_id=f"s{i}x")
        capture.detached(f"w{i}", f"s{i}x")
    assert "s1" not in capture._sessions


def test_a_session_evicted_while_paused_is_heard_again_with_its_secrets(capture):
    """PR review, round 2: a hold again after the agent's pause must not stop
    capture silently because the session's `Held` was evicted meanwhile, and
    what it was told to mask is still masked."""
    attach(capture)
    capture.secret("ws", ["hunter2"])
    evict_idle(capture)
    capture.attached("ws", "s1", "firefox", "top-1", again=True)
    feed(capture, log("pw hunter2"))
    assert texts(capture) == [f"pw {MASK}"]


def test_a_secret_bound_while_evicted_is_masked_once_heard_again(capture):
    attach(capture)
    evict_idle(capture)
    capture.secret("ws", ["hunter2"])
    capture.attached("ws", "s1", "firefox", "top-1", again=True)
    feed(capture, log("pw hunter2"))
    assert texts(capture) == [f"pw {MASK}"]


def test_an_evicted_sessions_secrets_carry_to_the_session_that_replaces_it(capture):
    attach(capture)
    capture.secret("ws", ["hunter2"])
    evict_idle(capture)
    capture.attached("ws", "s2", "chrome", "top-1", again=False)
    feed(capture, log("pw hunter2"), session_id="s2")
    assert texts(capture) == [f"pw {MASK}"]
    # and the browser it replaced is not heard again
    capture.attached("ws", "s1", "chrome", "top-1", again=True)
    feed(capture, log("old"))
    assert texts(capture) == [f"pw {MASK}"]


def test_a_discarded_workspace_is_not_heard_again_after_eviction(capture):
    attach(capture)
    evict_idle(capture)
    capture.discard("ws")
    capture.attached("ws", "s1", "chrome", "top-1", again=True)
    feed(capture, log("after"))
    assert capture.console("ws") is None


# ---- mark, caused ----------------------------------------------------------------


def test_caused_without_a_buffer_is_nothing(capture):
    assert capture.mark("ws") is None
    assert capture.caused("ws", Mark(3, 4)) == {"errors": 0, "failed_requests": 0}


# ---- tails ---------------------------------------------------------------------


async def test_a_network_tail_gets_static_rows_unkept_and_no_headers(capture):
    attach(capture)
    tail = capture.tail("ws", "network")
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/a.css", dest="style", headers=[("Referer", "x")]),
         done("r1", 200, mime="text/css"),
         req("r2", "https://a.test/api"), fail("r2"))
    first, second = await tail.next(), await tail.next()
    assert (first["url"], first["kept"], second["url"], second["kept"]) == (
        "https://a.test/a.css", False, "https://a.test/api", True)
    assert "request_headers" not in first and "response_headers" not in second
    assert tail._queue.empty()                       # nothing at request start
    tail.close()


async def test_a_debug_entry_is_tailed_unkept(capture):
    attach(capture)
    tail = capture.tail("ws", "console")
    feed(capture, log("d", level="debug"))
    row = await tail.next()
    assert (row["text"], row["kept"]) == ("d", False)
    tail.close()


async def test_a_closed_tail_hears_nothing_more(capture):
    attach(capture)
    tail = capture.tail("ws", "console")
    tail.close()
    assert capture._tails == {}
    feed(capture, log("after"))
    assert tail._queue.empty()


async def test_a_tail_waits_for_a_row(capture):
    attach(capture)
    tail = capture.tail("ws", "console")
    waiting = asyncio.ensure_future(tail.next())
    await asyncio.sleep(0)
    assert not waiting.done()
    feed(capture, log("late"))
    assert (await waiting)["text"] == "late"
    tail.close()


def test_a_tail_of_an_unknown_kind_is_refused(capture):
    with pytest.raises(ValueError, match="console or network"):
        capture.tail("ws", "all")


def test_a_third_partys_cookie_headers_cannot_evict_the_first_partys_session(capture):
    """R5-3: a cookie with no BiDi domain counts against its request's host,
    so a tracker's 48 session-named cookies are its own share, not the app's."""
    import random

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    hostile = "; ".join(f"token{i}=" + "".join(random.Random(i).sample(alphabet, 64)) * 2
                        for i in range(48))
    sid = "Q7vX_2mK9pL4wR8tZ1nB6yH3cJ5fD0gS-aEuWiOqT"
    attach(capture)
    feed(capture, nav_started("https://app.example/"),
         req("r1", "https://app.example/api", headers=[("Cookie", f"sid={sid}")]))
    for i in range(3):
        feed(capture,
             req(f"t{i}", "https://tracker.example/p", headers=[("Cookie", hostile)]),
             done(f"t{i}", 200, url="https://tracker.example/p",
                  headers=[("Set-Cookie", hostile.split("; ")[i])]))
    feed(capture, log(f"echo {sid}"))
    assert texts(capture)[-1] == f"echo {MASK}"


def test_a_credential_too_long_to_mask_is_counted_where_dropped_is(capture):
    """F-3: an operator learns that a credential went unmasked; ``unmasked``
    is present only when there was one, and counts each value once."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"),
         req("r1", "https://a.test/api", headers=[("Cookie", "sid=ok-value-0123456789")]))
    assert "unmasked" not in capture.counts("ws") and "unmasked" not in capture.console("ws")
    huge = "sid=" + "Zz9-" * 1100                            # past a cookie's 4 096
    for i in range(3):
        feed(capture, req(f"r{i + 2}", "https://a.test/api", headers=[("Cookie", huge)]))
    assert capture.counts("ws")["unmasked"] == 1
    assert capture.console("ws")["unmasked"] == 1 and capture.network("ws")["unmasked"] == 1


# ---- the secrets in scope: capture hears the origin, and holds until it is known --
# Spec 2026-11-14-secret-scope, "Capture as source 2", rulings 8, 9, 13 and 22.

A = "https://a.test"
B = "https://b.test"
TOP = "top-1"


class Asks(dict):
    """What capture asked for: origin ("*" for every leashed secret) -> the
    ``done`` it handed over, kept so a test decides when the forms land."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple] = []

    def __call__(self, workspace, session_id, url, frame, done):
        from kubed.selenium_flow.urls import scope_origin

        self.calls.append((workspace, session_id, url, frame))
        self["*" if url is None else scope_origin(url)] = done

    def asked(self, origin):
        from kubed.selenium_flow.urls import scope_origin

        return [c for c in self.calls if ("*" if c[2] is None else scope_origin(c[2])) == origin]


def capture_with_recorder(clock=None, browser="chrome"):
    cap = Capture(clock=clock or Clock())
    asks = Asks()
    cap.heard = asks
    cap.attached("w", "g1", browser, TOP, again=False)
    return cap, asks


def nav(ctx, url, **kw):
    return nav_started(url, ctx=ctx, **kw)


def console(ctx, text, **kw):
    return log(text, ctx=ctx, **kw)


def ingest(cap, *events):
    for method, params in events:
        cap.ingest("w", "g1", method, params)


def shown(cap, pages=1):
    return [e["text"] for e in cap.console("w", pages=pages)["entries"]]


def password(value, name="a"):
    from kubed.selenium_flow.redaction import Forms

    return Forms.of(value, (name, "password"))


def test_a_new_origin_holds_its_events_until_its_forms_land():          # ruling 9
    cap, asks = capture_with_recorder()
    cap.ingest("w", "g1", "browsingContext.navigationStarted", nav(TOP, f"{A}/")[1])
    cap.ingest("w", "g1", "log.entryAdded", console(TOP, "pw alpha-11")[1])
    assert cap.console("w")["entries"] == []            # held, never stored raw
    asks[A](password("alpha-11"))
    assert cap.console("w")["entries"][0]["text"] == "pw [secret:a/password]"
    assert [p["url"] for p in cap.console("w")["pages"]] == [f"{A}/"]   # _started ran


def test_a_frame_navigation_is_asked_for_too():                          # ruling 8
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"), nav("frame-1", f"{B}/inner"), console("frame-1", "x alpha-11"))
    assert [c[3] for c in asks.asked(A)] == [False]
    assert [c[3] for c in asks.asked(B)] == [True]
    asks[A](password("other-value"))
    assert shown(cap) == []                            # the frame waits on its own origin
    asks[B](password("alpha-11"))
    assert shown(cap) == ["x [secret:a/password]"]


def test_a_navigation_request_before_navigation_started_holds_too():     # either BiDi order
    cap, asks = capture_with_recorder()
    ingest(cap, req("r1", f"{B}/login", dest="document", nav="n1"), console(TOP, "early alpha-11"),
           nav(TOP, f"{B}/login"), console(TOP, "late alpha-11"))
    assert len(asks.asked(B)) == 1                     # asked once, whichever came first
    assert shown(cap) == [] and cap.network("w")["requests"] == []
    asks[B](password("alpha-11"))
    assert shown(cap) == ["late [secret:a/password]"]  # the start's mark split the page
    assert [(e["text"], e["nav"]) for e in cap._cache["w"].console] == [
        ("early [secret:a/password]", 0), ("late [secret:a/password]", 1)]
    assert [r["url"] for r in cap._cache["w"].requests] == [f"{B}/login"]


def test_a_redirect_to_another_origin_is_asked_for_and_waited_on():
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/go"), req("r1", f"{B}/landed", dest="document", nav="n1", redirect=1),
           console(TOP, "pw alpha-11"))
    asks[A](password("nothing-here"))
    assert shown(cap) == []                            # still waiting on the redirect's origin
    asks[B](password("alpha-11"))
    assert shown(cap) == ["pw [secret:a/password]"]


def test_a_failed_read_drops_and_counts_and_asks_again():                # ruling 22
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"), console(TOP, "pw alpha-11"))
    asks[A](None)
    assert cap.console("w")["entries"] == []
    assert cap.counts("w")["dropped"] == 2             # the start and the entry, never raw
    ingest(cap, console(TOP, "again alpha-11"))
    assert len(asks.asked(A)) == 2                     # the next event asks again
    assert shown(cap) == []
    asks[A](password("alpha-11"))
    assert shown(cap) == ["again [secret:a/password]"]
    assert cap.counts("w")["dropped"] == 2


def test_an_overflowing_hold_drops_the_oldest_and_counts():
    from kubed.selenium_flow.capture import PENDING_EVENTS

    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"), *[console(TOP, f"m{i}") for i in range(PENDING_EVENTS + 4)])
    assert cap.console("w")["entries"] == []
    assert cap.counts("w")["dropped"] == 5             # the start and m0..m3: the oldest
    asks[A](password("alpha-11"))
    view = cap.console("w")
    assert view["total"] == PENDING_EVENTS and view["entries"][-1]["text"] == "m4"
    assert view["entries"][0]["text"] == f"m{PENDING_EVENTS + 3}"


def test_an_old_held_event_is_dropped_when_the_next_arrives(clock):
    from kubed.selenium_flow.capture import PENDING_SECONDS

    cap, asks = capture_with_recorder(clock)
    ingest(cap, nav(TOP, f"{A}/"), console(TOP, "old"))
    clock.now += PENDING_SECONDS                       # not older than the bound yet
    ingest(cap, console(TOP, "kept"))
    assert "dropped" not in cap.counts("w")
    clock.now += 1
    ingest(cap, console(TOP, "new"))                   # the start and "old" are past it now
    assert cap.counts("w")["dropped"] == 2
    asks[A](password("alpha-11"))
    assert shown(cap) == ["new", "kept"]


def test_a_hold_again_waits_for_every_leashed_secret():
    cap, asks = capture_with_recorder(browser="firefox")
    ingest(cap, nav(TOP, f"{A}/"))
    asks[A](password("nothing-here"))
    cap.attached("w", "g1", "firefox", TOP, again=True)
    assert asks.calls[-1] == ("w", "g1", None, False)  # every leashed secret
    ingest(cap, console(TOP, "pw alpha-11"))           # its origin is unknown now
    assert shown(cap) == []
    asks["*"](password("alpha-11", name="b"))
    assert shown(cap) == ["pw [secret:b/password]"]
    ingest(cap, console("frame-9", "lands at once"))   # every context is known to be "*"
    assert shown(cap)[0] == "lands at once"


def test_the_ask_happens_outside_the_lock():
    import threading

    cap = Capture(clock=Clock())
    cap.heard = lambda w, s, url, frame, done: done(password("alpha-11"))   # already built
    cap.attached("w", "g1", "chrome", TOP, again=False)
    worker = threading.Thread(
        target=ingest, args=(cap, nav(TOP, f"{A}/"), console(TOP, "pw alpha-11")), daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()                       # a done under the lock would deadlock
    assert shown(cap) == ["pw [secret:a/password]"]


def test_an_origin_already_ready_is_not_held():
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"))
    asks[A](password("alpha-11"))
    ingest(cap, nav(TOP, f"{A}/next"), console(TOP, "pw alpha-11"))
    assert shown(cap) == ["pw [secret:a/password]"]
    assert len(asks.calls) == 1


def test_an_opaque_page_is_never_asked_for_or_held():                    # ruling 13
    cap, asks = capture_with_recorder()
    ingest(cap, console(TOP, "before any page"), nav(TOP, "about:blank"), console(TOP, "blank"))
    assert shown(cap) == ["blank"] and asks.calls == []


def test_without_heard_nothing_is_held_or_asked(capture):
    """Secrets off, or no server: capture is exactly what it was."""
    attach(capture)
    feed(capture, nav_started("https://a.test/"), log("lands"))
    assert capture.heard is None and texts(capture) == ["lands"]
    assert capture._sessions["s1"].waiting == {}


def test_another_context_lands_while_one_waits():
    cap, asks = capture_with_recorder()
    ingest(cap, nav("frame-1", f"{B}/"), console("frame-1", "held"), console(TOP, "top lands"))
    assert shown(cap) == ["top lands"]
    asks[B](password("alpha-11"))
    assert shown(cap) == ["held", "top lands"]


def test_a_lost_event_makes_every_context_unknown():
    """A dropped event may have been a navigation: no context's origin can be
    trusted after it, so they wait for every leashed secret (ruling 9)."""
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"))
    asks[A](password("nothing-here"))
    cap.lost("w", "g1")
    ingest(cap, console(TOP, "pw alpha-11"))
    assert shown(cap) == [] and len(asks.asked("*")) == 1
    asks["*"](password("alpha-11"))
    assert shown(cap) == ["pw [secret:a/password]"]


def test_chrome_replay_is_aligned_when_its_forms_land_after_replayed():
    cap, asks = capture_with_recorder()
    first = [console(TOP, f"m{i}", ts=1000.0 + i) for i in range(3)]
    ingest(cap, nav(TOP, f"{A}/"))
    asks[A](password("nothing-here"))
    ingest(cap, *first)
    cap.attached("w", "g1", "chrome", TOP, again=True)
    ingest(cap, *first[1:], console(TOP, "m3", ts=1003.0))   # Chrome's burst, held for "*"
    cap.replayed("w", "g1")                                   # before the read lands
    assert shown(cap) == ["m2", "m1", "m0"]
    asks["*"](password("alpha-11"))
    assert shown(cap) == ["m3", "m2", "m1", "m0"]


def test_events_held_across_a_hold_again_land_before_the_replay_is_aligned():
    cap, asks = capture_with_recorder()
    held = [console(TOP, f"m{i}", ts=1000.0 + i) for i in range(2)]
    ingest(cap, nav(TOP, f"{A}/"), *held)                    # waiting on A
    cap.attached("w", "g1", "chrome", TOP, again=True)
    ingest(cap, *held, console(TOP, "m2", ts=1002.0))        # Chrome replays what it sent
    cap.replayed("w", "g1")
    asks[A](password("alpha-11"))
    assert shown(cap) == []                                   # and the new ones wait on "*"
    asks["*"](password("alpha-11"))
    assert shown(cap) == ["m2", "m1", "m0"]
    assert cap._sessions["g1"].holding is None


async def test_on_a_loop_the_forms_land_on_the_loop():
    """`done` runs on a scrubber thread; the tails are asyncio queues, so the
    landing is handed to the loop."""
    import threading

    cap, asks = capture_with_recorder()
    tail = cap.tail("w", "console")
    ingest(cap, nav(TOP, f"{A}/"), console(TOP, "pw alpha-11"))
    worker = threading.Thread(target=asks[A], args=(password("alpha-11"),))
    worker.start()
    worker.join()
    assert shown(cap) == [] and tail._queue.empty()           # not yet: the loop has not run
    row = await asyncio.wait_for(tail.next(), 5)
    assert row["text"] == "pw [secret:a/password]"
    assert shown(cap) == ["pw [secret:a/password]"]
    tail.close()


def test_past_the_contexts_bound_a_forgotten_origin_makes_every_context_unknown():
    """A context forgotten before its origin was read must not land as opaque,
    and the contexts waiting are bounded like the ones remembered."""
    cap, asks = capture_with_recorder()
    ingest(cap, *[nav(f"f{i}", f"https://h{i}.test/") for i in range(CONTEXTS + 1)])
    assert cap.counts("w")["dropped"] == 1            # f0's start: its wait let go
    assert len(cap._sessions["g1"].waiting) == CONTEXTS
    ingest(cap, console("never-navigated", "pw alpha-11"))
    assert shown(cap) == [] and len(asks.asked("*")) == 1
    asks["*"](password("alpha-11"))
    assert shown(cap) == ["pw [secret:a/password]"]


# ---- fix round 1 ---------------------------------------------------------------


def _until(predicate, wait=5.0):
    """Poll ``predicate`` until it holds: a real `Workspaces` reads on its own
    threads, and with no loop `done` lands there."""
    import time

    deadline = time.monotonic() + wait
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


@pytest.mark.parametrize(
    "site, scope",
    [("https://a.test", "https://a.test"), ("https://[::1]:8443", "https://::1:8443")],
    ids=["a-host", "ipv6-literal"],
)
def test_capture_through_the_real_workspaces_lands_the_marker(site, scope):
    """Review, round 1: the ask carries the navigation's URL, so the origin is
    the one `Workspaces` reads, an IPv6 literal included (its unbracketed
    origin is no URL of its own)."""
    from .test_secret_scope import FakeCatalogue, scoped, sid

    ws = scoped(FakeCatalogue({scope: {("a", "password"): "alpha-11"}}), url=f"{site}/")
    try:
        cap = Capture(clock=Clock())
        cap.heard = ws.capture_heard
        cap.attached("w", sid(ws), "chrome", TOP, again=False)
        for method, params in (nav(TOP, f"{site}/login?next=1"), console(TOP, "pw alpha-11")):
            cap.ingest("w", sid(ws), method, params)
        assert _until(lambda: cap.console("w")["entries"])
        assert shown(cap) == ["pw [secret:a/password]"]
    finally:
        ws.scrubber.close()


def child(ctx, parent=TOP, opener=None):
    _, params = context_created(ctx, parent=parent)
    if opener is not None:
        params["originalOpener"] = opener
    return "browsingContext.contextCreated", params


def test_a_srcdoc_child_of_a_waiting_top_waits_with_it():
    """Controller ruling (round 1): a child's "" inherits its parent's origin,
    so it waits on what its parent waits on, and lands after it."""
    cap, asks = capture_with_recorder()
    ingest(cap, child("frame-1"), child("frame-2"), nav(TOP, f"{A}/"),
           nav("frame-1", "about:srcdoc"), console("frame-1", "srcdoc alpha-11"),
           console("frame-2", "never navigated alpha-11"))
    assert shown(cap) == [] and set(asks) == {A}
    asks[A](password("alpha-11"))
    rows = [(e["text"], e["nav"]) for e in cap._cache["w"].console]
    assert rows == [("srcdoc [secret:a/password]", 1),
                    ("never navigated [secret:a/password]", 1)]


def test_a_child_of_a_ready_parent_is_ready():
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"))
    asks[A](password("alpha-11"))
    ingest(cap, child("frame-1"), nav("frame-1", "about:blank"), console("frame-1", "lands"))
    assert shown(cap) == ["lands"] and len(asks.calls) == 1


def test_a_grandchild_inherits_through_its_parent():
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"))
    asks[A](password("nothing-here"))
    ingest(cap, child("frame-1"), nav("frame-1", f"{B}/"), child("frame-2", parent="frame-1"),
           console("frame-2", "pw alpha-11"))
    assert shown(cap) == []
    asks[B](password("alpha-11"))
    assert shown(cap) == ["pw [secret:a/password]"]


def test_a_popup_inherits_its_opener_and_without_one_waits_for_every_secret():
    """A tab with no parent: its opener's origin when the event names one;
    with none, fail-safe, every leashed secret (one read for the browser)."""
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"), child("popup-1", parent=None, opener=TOP),
           console("popup-1", "opened alpha-11"))
    assert shown(cap) == [] and "*" not in asks
    asks[A](password("alpha-11"))
    assert shown(cap) == ["opened [secret:a/password]"]
    ingest(cap, child("popup-2", parent=None), console("popup-2", "orphan beta-22"))
    assert shown(cap)[0] == "opened [secret:a/password]" and len(asks.asked("*")) == 1
    asks["*"](password("beta-22", name="b"))
    assert shown(cap)[0] == "orphan [secret:b/password]"


def test_the_top_pages_blank_is_still_known_empty():                     # ruling 13
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, "about:blank"), child("frame-1"), console("frame-1", "lands"))
    assert shown(cap) == ["lands"] and asks.calls == []


def test_the_held_events_are_bounded_in_bytes_too(monkeypatch):
    from kubed.selenium_flow.capture import store

    monkeypatch.setattr(store, "QUEUE_BYTES", 20_000)   # each entry is ~8 kB: text and arg
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"), *[console(TOP, f"m{i} " + "x" * 4000) for i in range(3)])
    assert cap.counts("w")["dropped"] == 2              # the start and m0: the oldest
    asks[A](password("alpha-11"))
    assert [t[:2] for t in shown(cap)] == ["m2", "m1"]
    assert cap._sessions["g1"].weight == 0


def test_detached_drops_a_replay_still_waiting_as_it_drops_one_held():
    cap, asks = capture_with_recorder()
    first = [console(TOP, f"m{i}", ts=1000.0 + i) for i in range(2)]
    ingest(cap, nav(TOP, f"{A}/"))
    asks[A](password("nothing-here"))
    ingest(cap, *first)
    cap.attached("w", "g1", "chrome", TOP, again=True)
    ingest(cap, *first, console(TOP, "m2", ts=1002.0))   # the replay, waiting for "*"
    cap.detached("w", "g1")
    assert cap.counts("w")["dropped"] == 3
    asks["*"](password("alpha-11"))
    assert shown(cap) == ["m1", "m0"]


def test_detached_keeps_what_waited_from_before_the_hold_again():
    cap, asks = capture_with_recorder()
    ingest(cap, nav(TOP, f"{A}/"), console(TOP, "m0", ts=1000.0))   # waiting on A
    cap.attached("w", "g1", "chrome", TOP, again=True)
    ingest(cap, console(TOP, "m0", ts=1000.0))                      # its replay
    cap.detached("w", "g1")
    assert cap.counts("w")["dropped"] == 1
    asks[A](password("alpha-11"))
    asks["*"](password("alpha-11"))
    assert shown(cap) == ["m0"]
