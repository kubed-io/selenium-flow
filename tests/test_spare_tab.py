"""The spare tab: a tab whose every request is answered with a marked blank
page, so it can stand on any origin without the site loading (spec round 2,
*The spike*). BiDi is faked module by module; what `spare_tab` sends, and in
which order, is the contract the live spike proved."""

import json
import re

import pytest

from kubed.selenium_flow.core.browser import (
    BIDI_INTERVAL,
    SPARE_MARKER,
    SPARE_PAGE,
    Grid,
    ServiceWorkerAnswered,
    spare_tab,
)

pytestmark = pytest.mark.unit


class FakeTabs:
    def __init__(self, log):
        self.log, self.at = log, {}

    def create(self, type=None, background=None, **_):
        self.log.append(("create", type, background))
        return "spare-1"

    def navigate(self, context=None, url=None, wait=None):
        self.log.append(("navigate", context, url, wait))
        self.at[context] = url.rstrip("/")

    def close(self, context=None):
        self.log.append(("close", context))


class FakeNetwork:
    def __init__(self, log):
        self.log, self.handlers, self.responses = log, {}, []

    def add_intercept(self, phases=None, contexts=None, **_):
        self.log.append(("intercept", phases, contexts))
        return {"intercept": "i-1"}

    def remove_intercept(self, intercept=None):
        self.log.append(("unintercept", intercept))

    def add_event_handler(self, event, callback, contexts=None):
        self.log.append(("handler", event, contexts))
        self.handlers[7] = callback
        return 7

    def remove_event_handler(self, event, callback_id):
        self.log.append(("unhandler", event, callback_id))

    def provide_response(self, **kw):
        self.responses.append(kw)


class FakeScript:
    """Answers as the page would: ours, with the marker, or a service
    worker's. ``stuck`` leaves the tab on the page it was on before."""

    def __init__(self, log, tabs, value=None, workers=(), stuck=False, fail=None):
        self.log, self.tabs = log, tabs
        self.value, self.workers, self.stuck, self.fail = value, set(workers), stuck, fail
        self.expression = None

    def evaluate(self, expression=None, target=None, await_promise=None):
        self.log.append(("evaluate", target, await_promise))
        self.expression = expression
        if self.fail:
            return {"type": "exception", "exceptionDetails": {"text": self.fail}}
        origin = "https://before.test" if self.stuck else self.tabs.at[target["context"]]
        page = {"spare": origin not in self.workers, "origin": origin}
        if page["spare"]:
            page["value"] = self.value
        return {"type": "success", "result": {"type": "string", "value": json.dumps(page)}}


class FakeBidi:
    def __init__(self, **kw):
        self.log = []
        self.browsing_context = FakeTabs(self.log)
        self.network = FakeNetwork(self.log)
        self.script = FakeScript(self.log, self.browsing_context, **kw)


def test_a_spare_tab_stands_on_an_origin_runs_there_and_closes():
    bidi = FakeBidi(value={"theme": "dark"})
    with spare_tab(bidi) as run:
        assert run("https://app.test", "localStorage.length") == {"theme": "dark"}
    assert bidi.log == [
        ("create", "tab", True),
        ("intercept", ["beforeRequestSent"], ["spare-1"]),
        ("handler", "before_request", ["spare-1"]),
        ("navigate", "spare-1", "https://app.test/", "complete"),
        ("evaluate", {"context": "spare-1"}, True),
        ("unhandler", "before_request", 7),
        ("unintercept", "i-1"),
        ("close", "spare-1"),
    ]
    assert SPARE_MARKER in bidi.script.expression
    assert "localStorage.length" in bidi.script.expression


def test_every_blocked_request_is_answered_with_the_marked_page():
    bidi = FakeBidi()
    with spare_tab(bidi):
        answer = bidi.network.handlers[7]
        answer({"isBlocked": True, "request": {"request": "r-1", "url": "https://app.test/"}})
        answer({"isBlocked": False, "request": {"request": "r-2", "url": "https://app.test/x"}})
        answer("not an event")
    assert bidi.network.responses == [{
        "request": "r-1",
        "status_code": 200,
        "headers": [{"name": "content-type", "value": {"type": "string", "value": "text/html"}}],
        "body": {"type": "string", "value": SPARE_PAGE},
    }]
    assert SPARE_MARKER in SPARE_PAGE


def test_a_refused_response_never_raises_on_the_event_thread():
    bidi = FakeBidi()

    def refuse(**kw):
        raise RuntimeError("no such request")

    bidi.network.provide_response = refuse
    with spare_tab(bidi):
        bidi.network.handlers[7]({"isBlocked": True, "request": {"request": "r-1"}})


def test_a_service_worker_page_is_refused_before_anything_runs_there():
    bidi = FakeBidi(value="read", workers={"https://sw.test"})
    with pytest.raises(ServiceWorkerAnswered, match=re.escape("https://sw.test")), spare_tab(bidi) as run:
        run("https://sw.test", "localStorage.length")
    assert bidi.log[-1] == ("close", "spare-1"), "the tab still closes"


def test_a_tab_that_did_not_move_is_never_read_as_the_new_origin():
    bidi = FakeBidi(value="stale", stuck=True)
    with pytest.raises(RuntimeError, match=re.escape("not https://app.test")), spare_tab(bidi) as run:
        run("https://app.test", "1")


def test_a_script_that_throws_raises_its_text_and_the_tab_still_closes():
    bidi = FakeBidi(fail="boom")
    with pytest.raises(RuntimeError, match="boom"), spare_tab(bidi) as run:
        run("https://app.test", "1")
    assert bidi.log[-1] == ("close", "spare-1")


def test_an_existing_tab_is_intercepted_and_left_open():
    bidi = FakeBidi(value=1)
    with spare_tab(bidi, context="main-1") as run:
        run("https://app.test", "1")
    assert bidi.log == [
        ("intercept", ["beforeRequestSent"], ["main-1"]),
        ("handler", "before_request", ["main-1"]),
        ("navigate", "main-1", "https://app.test/", "complete"),
        ("evaluate", {"context": "main-1"}, True),
        ("unhandler", "before_request", 7),
        ("unintercept", "i-1"),
    ], "no create, no close; the intercept is gone from the user's own tab"


def test_the_bidi_socket_is_polled_every_few_milliseconds():
    """Selenium's 100 ms default put a 100 ms floor under every BiDi call
    (spike, 2026-10-01)."""
    with Grid("http://grid.example:4444").bidi("abc") as driver:
        assert driver.command_executor.client_config.websocket_interval == BIDI_INTERVAL
    assert BIDI_INTERVAL <= 0.01
