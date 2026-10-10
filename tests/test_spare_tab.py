"""The spare tab: a tab whose every request is answered with a marked blank
page, so it can stand on any origin without the site loading (spec round 2,
*The spike*). The channel is faked; what `spare_tab` sends, and in which
order, is the contract the live spike proved."""

import re
from contextlib import contextmanager

import pytest

from kubed.selenium_flow.site_data.spare import (
    SPARE_MARKER,
    SPARE_PAGE,
    SPARE_PATH,
    ServiceWorkerAnswered,
    spare_tab,
)

from .fakes import FakeChannel

pytestmark = pytest.mark.unit

EVENT = "network.beforeRequestSent"


def test_a_spare_tab_stands_on_an_origin_runs_there_and_closes():
    ch = FakeChannel(value={"theme": "dark"})
    with spare_tab(ch) as run:
        assert run("https://app.test", "localStorage.length") == {"theme": "dark"}
    assert [m for m, _ in ch.log] == [
        "browsingContext.create", "network.addIntercept", "session.subscribe",
        "browsingContext.navigate", "script.evaluate",
        "network.removeIntercept", "session.unsubscribe", "browsingContext.close",
    ]
    assert ch.params("browsingContext.create") == {"type": "tab", "background": True}
    assert ch.params("network.addIntercept") == {
        "phases": ["beforeRequestSent"], "contexts": ["spare-1"],
        "urlPatterns": [{"type": "pattern", "pathname": SPARE_PATH}],
    }
    assert ch.params("session.subscribe") == {"events": [EVENT], "contexts": ["spare-1"]}
    assert ch.params("browsingContext.navigate") == {
        "context": "spare-1", "url": "https://app.test" + SPARE_PATH, "wait": "complete",
    }
    assert ch.params("script.evaluate")["target"] == {"context": "spare-1"}
    assert ch.params("script.evaluate")["awaitPromise"] is True
    assert SPARE_MARKER in ch.expression
    assert "localStorage.length" in ch.expression


def test_every_blocked_request_is_answered_with_the_marked_page():
    ch = FakeChannel()
    with spare_tab(ch):
        answer = ch.handlers[EVENT]
        answer(EVENT, {"isBlocked": True, "request": {"request": "r-1"}})
        answer(EVENT, {"isBlocked": False, "request": {"request": "r-2"}})
    assert ch.posts == [("network.provideResponse", {
        "request": "r-1", "statusCode": 200,
        "headers": [{"name": "content-type", "value": {"type": "string", "value": "text/html"}}],
        "body": {"type": "string", "value": SPARE_PAGE},
    })]
    assert SPARE_MARKER in SPARE_PAGE


def test_the_spare_tab_is_hidden_from_capture_for_its_whole_life():
    ch, seen = FakeChannel(), []

    @contextmanager
    def hidden(tab):
        seen.append(("in", tab))
        yield
        seen.append(("out", tab))

    with spare_tab(ch, hidden=hidden) as run:
        run("https://app.test", "1")
        assert seen == [("in", "spare-1")]
    assert seen == [("in", "spare-1"), ("out", "spare-1")]
    assert ch.log[-1][0] == "browsingContext.close", "closed while still hidden"


def test_an_existing_tab_is_never_hidden():
    ch, seen = FakeChannel(), []

    @contextmanager
    def hidden(tab):
        seen.append(tab)
        yield

    with spare_tab(ch, context="main-1", hidden=hidden):
        pass
    assert seen == []


def test_a_service_worker_page_is_refused_before_anything_runs_there():
    ch = FakeChannel(value="read", workers={"https://sw.test"})
    with pytest.raises(ServiceWorkerAnswered, match=re.escape("https://sw.test")), spare_tab(ch) as run:
        run("https://sw.test", "localStorage.length")
    assert ch.log[-1][0] == "browsingContext.close", "the tab still closes"


def test_a_tab_that_did_not_move_is_never_read_as_the_new_origin():
    ch = FakeChannel(value="stale", stuck=True)
    with pytest.raises(RuntimeError, match=re.escape("not https://app.test")), spare_tab(ch) as run:
        run("https://app.test", "localStorage.setItem('k', 'v')")
    assert not ch.ran, "nothing was written on the wrong origin"


def test_the_expression_runs_on_the_requested_origin():
    ch = FakeChannel(value=1)
    with spare_tab(ch) as run:
        run("https://app.test", "1")
    assert ch.ran


def test_a_script_that_throws_raises_its_text_and_the_tab_still_closes():
    ch = FakeChannel(fail="boom")
    with pytest.raises(RuntimeError, match="boom"), spare_tab(ch) as run:
        run("https://app.test", "1")
    assert ch.log[-1][0] == "browsingContext.close"


def test_an_existing_tab_is_intercepted_and_left_open():
    ch = FakeChannel(value=1)
    with spare_tab(ch, context="main-1") as run:
        run("https://app.test", "1")
    assert [m for m, _ in ch.log] == [
        "network.addIntercept", "session.subscribe", "browsingContext.navigate",
        "script.evaluate", "network.removeIntercept", "session.unsubscribe",
    ], "no create, no close; the intercept is gone from the user's own tab"
    assert ch.params("network.addIntercept")["contexts"] == ["main-1"]


def test_a_failed_teardown_step_still_runs_the_rest():
    ch = FakeChannel(value=1, refuse={"network.removeIntercept"})
    with spare_tab(ch) as run:
        run("https://app.test", "1")
    assert [m for m, _ in ch.log][-3:] == [
        "network.removeIntercept", "session.unsubscribe", "browsingContext.close",
    ]


def test_an_intercept_is_removed_even_when_the_subscribe_fails():
    ch = FakeChannel(down={"session.subscribe"})

    @contextmanager
    def failing(events, handler, *, contexts=None, name="channel"):
        raise RuntimeError("could not subscribe")
        yield

    ch.listen = failing
    with pytest.raises(RuntimeError, match="could not subscribe"), spare_tab(ch):
        pass
    assert [m for m, _ in ch.log] == [
        "browsingContext.create", "network.addIntercept",
        "network.removeIntercept", "browsingContext.close",
    ]
