"""The origin-changed event and the scrubber (spec 2026-11-14-secret-scope).

Each browser has a current origin; the scrubber reads the secrets allowed there
once, off the loop, when it changes, and scrubs a result with them. Nothing is
read and nothing is scanned when secrets are off, and an opaque page reads
nothing at all.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from concurrent.futures import Future
from dataclasses import replace

import pytest
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException

from kubed.selenium_flow import errors, faults, secrets, urls
from kubed.selenium_flow.core.actions import BODY_CAP
from kubed.selenium_flow.redaction import Forms, scrub
from kubed.selenium_flow.workspace import scrub as scrub_module
from kubed.selenium_flow.workspace.origins import OriginChanged, Origins
from kubed.selenium_flow.workspace.scrub import Scrubber
from kubed.selenium_flow.workspace.store import MemoryStore
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

from .conftest import RecordingActions

pytestmark = pytest.mark.unit

A = "https://a.test"
B = "https://b.test"
WAIT = 5  # seconds a test waits on a future that is already on its way


class FakeCatalogue:
    """Answers `Forms.of` from ``{origin: {(name, key): value}}``, and counts.

    ``calls`` holds the reads that can touch a value; ``revisions`` counts the
    cheap `revision()` calls separately.
    """

    def __init__(self, scopes: dict, *, gate: threading.Event | None = None):
        self.scopes = scopes
        self.calls: dict = {"forms_for": [], "leashed": 0}
        self.revisions = 0
        self._revision = 1
        self.gate = gate
        self.fail_forms_for = False
        self.fail_leashed = False

    def rotate(self, scopes: dict) -> None:
        self.scopes = scopes
        self._revision += 1

    def revision(self) -> int:
        self.revisions += 1
        return self._revision

    def _forms(self, secrets_: dict) -> Forms:
        forms = Forms()
        for ident, value in secrets_.items():
            forms = forms | Forms.of(value, ident)
        return forms

    def forms_for(self, origin: str) -> Forms:
        self.calls["forms_for"].append(origin)
        if self.gate is not None:
            self.gate.wait(WAIT)
        if self.fail_forms_for:
            raise OSError("cannot read alpha-11")
        return self._forms(self.scopes.get(origin, {}))

    def leashed(self) -> Forms:
        self.calls["leashed"] += 1
        if self.fail_leashed:
            raise OSError("cannot read alpha-11 either")
        every: dict = {}
        for scoped in self.scopes.values():
            every.update(scoped)
        return self._forms(every)


class FakeCapture:
    def __init__(self):
        self.secrets: list = []

    def secret(self, workspace, forms):
        self.secrets.append((workspace, forms))


@pytest.fixture
def make():
    made: list[Scrubber] = []

    def factory(*args, **kwargs) -> Scrubber:
        kwargs.setdefault("ttl", 60)
        scrubber = Scrubber(*args, **kwargs)
        made.append(scrubber)
        return scrubber

    yield factory
    for scrubber in made:
        scrubber.close()


# --- the origin-changed event -----------------------------------------------


def test_the_event_fires_once_per_origin_change():  # ruling 6
    seen = []
    o = Origins()
    o.listen(seen.append)
    for url in [f"{A}/x", f"{A}/y?q=1", f"{A}/y", f"{B}/", f"{B}/z"]:
        o.arrived("w", "g1", url)
    assert [(e.old, e.new) for e in seen] == [(None, A), (A, B)]
    assert seen[0] == OriginChanged("w", "g1", None, A, False)


def test_a_missing_url_emits_nothing_and_answers_none():
    seen = []
    o = Origins()
    o.listen(seen.append)
    assert o.arrived("w", "g1", None) is None and seen == []


def test_an_arrival_answers_the_origin():
    o = Origins()
    assert o.arrived("w", "g1", f"{A}/path?q=1#x") == A
    assert o.arrived("w", "g1", "about:blank") == ""


def test_an_opaque_page_is_an_origin_of_its_own():  # ruling 13
    seen = []
    o = Origins()
    o.listen(seen.append)
    for url in [f"{A}/", "about:blank", "data:text/html,hi", f"{A}/"]:
        o.arrived("w", "g1", url)
    assert [(e.old, e.new) for e in seen] == [(None, A), (A, ""), ("", A)]


def test_a_new_browser_starts_with_no_origin():
    o = Origins()
    o.arrived("w", "g1", f"{A}/")
    assert o.current("w", "g2") is None
    assert o.current("w", "g1") == A


def test_a_new_browser_fires_again_for_the_same_origin():
    seen = []
    o = Origins()
    o.listen(seen.append)
    o.arrived("w", "g1", f"{A}/")
    o.forget("w", "g1")
    o.arrived("w", "g2", f"{A}/")
    assert [(e.browser, e.old, e.new) for e in seen] == [
        ("g1", None, A),
        ("g2", None, A),
    ]


def test_a_frame_origin_fires_once_per_browser():
    seen = []
    o = Origins()
    o.listen(seen.append)
    o.arrived("w", "g1", "https://f.test/a", frame=True)
    o.arrived("w", "g1", "https://f.test/b", frame=True)
    assert [(e.new, e.frame) for e in seen] == [("https://f.test", True)]
    assert o.current("w", "g1") is None  # a frame is never the top's origin


def test_a_listener_that_raises_never_fails_the_arrival(caplog):
    heard = []

    def broken(event):
        raise RuntimeError("boom alpha-11")

    o = Origins()
    o.listen(broken)
    o.listen(heard.append)
    with caplog.at_level(logging.WARNING):
        assert o.arrived("w", "g1", f"{A}/") == A
    assert len(heard) == 1  # the next listener still hears it
    assert "RuntimeError" in caplog.text
    assert "alpha-11" not in caplog.text and "boom" not in caplog.text


def test_framed_is_set_answered_and_cleared_by_a_new_browser():  # ruling 15
    o = Origins()
    assert o.framed("w", "g1") is True  # not known yet: unknown (round 3)
    assert o.framed("w", "g1", True) is True
    assert o.framed("w", "g1") is True
    assert o.framed("w", "g1", False) is False
    o.framed("w", "g1", True)
    o.forget("w", "g1")
    o.opened("w", "g2")
    assert o.framed("w", "g2") is False


def test_a_stale_arrival_leaves_the_current_browser_alone():  # fix round 1
    """A call still on g1 when g2 opened must not wipe g2's state."""
    seen = []
    o = Origins()
    o.listen(seen.append)
    o.arrived("w", "g1", f"{A}/")
    o.forget("w", "g1")
    o.arrived("w", "g2", f"{B}/")
    o.framed("w", "g2", True)
    assert o.arrived("w", "g1", f"{A}/x") == A  # answered, nothing more
    o.framed("w", "g1", False)
    o.arrived("w", "g1", "https://f.test/", frame=True)
    assert o.current("w", "g2") == B and o.framed("w", "g2") is True
    assert [(e.browser, e.new) for e in seen] == [("g1", A), ("g2", B)]


def test_a_browser_takes_the_slot_only_once_the_last_one_is_forgotten():
    seen = []
    o = Origins()
    o.listen(seen.append)
    o.arrived("w", "g1", f"{A}/")
    o.framed("w", "g1", True)
    o.arrived("w", "g2", f"{B}/")  # g1 not forgotten: g2 is not current yet
    assert o.current("w", "g1") == A and o.framed("w", "g1") is True
    assert o.current("w", "g2") is None
    assert o.framed("w", "g2") is True  # not the current browser: unknown
    assert [e.browser for e in seen] == ["g1"]


def test_forget_drops_the_browser():
    o = Origins()
    o.arrived("w", "g1", f"{A}/")
    o.framed("w", "g1", True)
    o.forget("w", "g1")
    assert o.current("w", "g1") is None
    assert o.framed("w", "g1") is True  # gone: unknown, never "no frame"
    o.arrived("w", "g1", f"{A}/")  # and it never comes back
    assert o.current("w", "g1") is None


def test_forget_leaves_another_browser_alone():
    o = Origins()
    o.arrived("w", "g2", f"{A}/")
    o.forget("w", "g1")
    assert o.current("w", "g2") == A


@pytest.mark.parametrize(
    "url",
    [
        f"{A}/x",
        "HTTPS://A.Test/",
        "https://a.test:443/",
        "https://a.test:8443/x",
        "https://user:pw@a.test/",
        "http://[::1]:8080/",
        "about:blank",
        "data:text/html,hi",
        "javascript:alert(1)",
        "chrome-error://chromewebdata/",
        "file:///etc/hosts",
        "",
    ],
)
def test_the_origin_is_the_one_the_leash_compares(url):
    assert urls.scope_origin(url) == secrets.origin(url)


@pytest.mark.parametrize("url", ["http://[bad/", "https://a.test:99999/", None, 7])
def test_an_unreadable_url_has_no_origin_and_never_raises(url):
    assert urls.scope_origin(url) == ""


def test_the_leash_and_the_scope_are_one_rule():  # fix round 1
    """`secrets.origin` delegates, so the two cannot drift apart."""
    assert secrets.origin("http://[bad/") == ""


# --- the scrubber --------------------------------------------------------------


def test_a_navigation_and_a_result_for_one_origin_read_once(make):  # plan input 4
    gate = threading.Event()
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}}, gate=gate)
    s = make(cat)
    first = s.build("w", "g1", A)
    second = s.build("w", "g1", A)
    gate.set()
    assert first.result(WAIT) is second.result(WAIT)
    s.build("w", "g1", A).result(WAIT)  # built already: no read
    assert cat.calls["forms_for"] == [A]


def test_an_opaque_origin_reads_nothing(make):  # Review Focus 2, ruling 13
    cat = FakeCatalogue({})
    s = make(cat)
    assert not s.forms("w", "g1", ["", ""])
    assert not s.build("w", "g1", "").result(WAIT)
    assert cat.calls == {"forms_for": [], "leashed": 0}


def test_no_catalogue_scrubs_nothing(make):  # Review Focus 1
    s = make(None)
    value = {"html": "alpha-11"}
    shown, changed = s.result("w", "g1", value, [A, A])
    assert shown is value and changed is False
    assert s.resource("w", "alpha-11") == "alpha-11"
    assert not s.forms("w", "g1", [None, None])
    exc = ValueError("alpha-11")
    s.error("w", "g1", exc, [None, None])
    assert exc.args == ("alpha-11",)


def test_no_secret_allowed_here_returns_the_value_itself(make):  # Review Focus 1
    cat = FakeCatalogue({A: {}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    value = {"html": "alpha-11"}
    shown, changed = s.result("w", "g1", value, [A, A])
    assert shown is value and changed is False
    assert cat.calls == {"forms_for": [A], "leashed": 0}


def test_an_unknown_origin_reads_every_leashed_secret(make):  # ruling 11
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    shown, _ = s.result("w", "g1", {"text": "alpha-11"}, [A, None])
    assert shown == {"text": "[secret:a/password]"} and cat.calls["leashed"] == 1


def test_an_origin_not_built_in_this_browser_is_unknown(make):  # ruling 14
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}, B: {}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    s.forms("w", "g2", [A, A])  # g2 never built A
    assert cat.calls["leashed"] == 1


def test_a_pending_build_is_waited_on(make, monkeypatch):
    gate = threading.Event()
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}}, gate=gate)
    s = make(cat)
    s.build("w", "g1", A)
    waiting = threading.Event()
    real = scrub_module.wait

    def watched(futures, timeout):
        waiting.set()
        return real(futures, timeout=timeout)

    monkeypatch.setattr(scrub_module, "wait", watched)
    answered: Future = Future()
    threading.Thread(
        target=lambda: answered.set_result(s.forms("w", "g1", [A, A]))
    ).start()
    assert waiting.wait(WAIT)  # the scrub is waiting on the build
    gate.set()
    assert ("a", "password") in answered.result(WAIT).ids
    assert cat.calls == {"forms_for": [A], "leashed": 0}


def test_a_secret_allowed_elsewhere_is_not_searched_for(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}, B: {}})
    s = make(cat)
    s.build("w", "g1", B).result(WAIT)
    assert s.result("w", "g1", {"t": "alpha-11"}, [B] * 2)[0] == {"t": "alpha-11"}


def test_the_sets_of_origins_the_browser_left_still_apply(make):  # ruling 14
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}, B: {}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    s.build("w", "g1", B).result(WAIT)
    shown, _ = s.result("w", "g1", {"t": "alpha-11"}, [B, B])
    assert shown == {"t": "[secret:a/password]"}


def test_a_changed_url_is_reported(make):  # ruling 21
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    shown, changed = s.result(
        "w", "g1", {"url": f"{A}/?q=alpha-11", "title": "t"}, [A, A]
    )
    assert shown["url"] == f"{A}/?q=[secret:a/password]" and changed is True
    shown, changed = s.result(
        "w", "g1", {"url": f"{A}/", "title": "alpha-11"}, [A, A]
    )
    assert shown == {"url": f"{A}/", "title": "[secret:a/password]"}
    assert changed is False


def test_a_url_that_is_not_text_is_not_changed(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    assert s.result("w", "g1", {"url": None}, [A, A]) == ({"url": None}, False)
    assert s.result("w", "g1", "alpha-11", [A, A]) == ("[secret:a/password]", False)


def test_a_rotation_rebuilds_and_keeps_the_old_forms(make):  # ruling 10
    cat = FakeCatalogue({A: {("a", "password"): "old-value-1"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {("a", "password"): "new-value-2"}})  # revision moves
    forms = s.forms("w", "g1", [A, A])
    assert scrub("old-value-1 new-value-2", forms) == (
        "[secret:a/password] [secret:a/password]"
    )


def test_a_rotation_rebuilds_the_current_origin(make):  # ruling 10
    cat = FakeCatalogue({A: {("a", "password"): "old-value-1"}})
    o = Origins()
    s = make(cat, origins=o)
    o.listen(s.changed)
    o.opened("w", "g1")  # known unframed (round 3)
    o.arrived("w", "g1", f"{A}/")
    s.forms("w", "g1", [A, A])  # waits for the build `changed` started
    cat.rotate({A: {("a", "password"): "new-value-2"}})
    forms = s.forms("w", "g1", [A, A])
    assert scrub("old-value-1 new-value-2", forms) == (
        "[secret:a/password] [secret:a/password]"
    )
    assert cat.calls == {"forms_for": [A, A], "leashed": 0}


def test_ended_drops_the_browser_and_carry_keeps_it(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    s.forms("w", "g1", [A, A])
    assert cat.calls == {"forms_for": [A], "leashed": 0}

    s.carry("w", "g1", "g2")
    assert ("a", "password") in s.forms("w", "g2", [A, A]).ids
    assert cat.calls == {"forms_for": [A], "leashed": 0}  # g2 needs no read

    s.ended("w", "g1")  # late, for the browser carried from: kept
    s.forms("w", "g2", [A, A])
    assert cat.calls == {"forms_for": [A], "leashed": 0}

    s.ended("w", "g2")
    assert ("a", "password") in s.forms("w", "g2", [A, A]).ids
    assert cat.calls["leashed"] == 1  # read again


def test_a_new_browser_starts_afresh_once_the_last_one_ended(make):
    cat = FakeCatalogue({A: {("a", "password"): "old-value-1"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {("a", "password"): "new-value-2"}})
    s.forms("w", "g1", [A, A])  # the old forms are kept for g1
    s.ended("w", "g1")
    cat.rotate({})
    assert not s.forms("w", "g3", [None, None])  # g1's kept forms are not g3's


def _rotated(make):
    """g2 current, built on A, rotated: it masks the old value and the new."""
    cat = FakeCatalogue({A: {("a", "password"): "old-value-1"}})
    s = make(cat)
    s.build("w", "g2", A).result(WAIT)
    cat.rotate({A: {("a", "password"): "new-value-2"}})
    both = "old-value-1 new-value-2"
    masked = "[secret:a/password] [secret:a/password]"
    assert scrub(both, s.forms("w", "g2", [A, A])) == masked
    return cat, s, both, masked


def test_a_stale_error_leaves_the_current_browser_alone(make):  # fix round 1
    _, s, both, masked = _rotated(make)
    exc = ValueError(both)
    s.error("w", "g1", exc, [A, A])  # a call still on g1 when g2 opened
    assert exc.args == (masked,)  # scrubbed with g2's forms and every secret
    assert scrub(both, s.forms("w", "g2", [A, A])) == masked


def test_a_stale_result_leaves_the_current_browser_alone(make):  # fix round 1
    _, s, both, masked = _rotated(make)
    assert s.result("w", "g1", {"t": both}, [A, A])[0] == {"t": masked}
    assert scrub(both, s.forms("w", "g2", [A, A])) == masked


def test_a_stale_build_reads_nothing_and_installs_nothing(make):  # fix round 1
    cat, s, both, masked = _rotated(make)
    read = list(cat.calls["forms_for"])
    got = []
    future = s.build("w", "g1", B, done=got.append)
    s.changed(OriginChanged("w", "g1", A, B, False))
    with pytest.raises(faults.ScrubFailed):
        future.result(WAIT)
    assert got == [None] and cat.calls["forms_for"] == read
    assert scrub(both, s.forms("w", "g2", [A, A])) == masked


def test_an_ended_browser_never_comes_back(make):  # fix round 1
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    s.ended("w", "g1")
    s.forms("w", "g1", [A, A])  # a late call: every secret, nothing kept
    s.forms("w", "g1", [A, A])
    assert cat.calls["leashed"] == 2 and cat.calls["forms_for"] == [A]
    s.build("w", "g2", A).result(WAIT)  # the slot is free for a new one
    s.forms("w", "g2", [A, A])
    assert cat.calls["leashed"] == 2


def test_a_browser_takes_the_slot_only_once_the_last_one_ended(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    s.forms("w", "g3", [A, A])  # g1 has not ended: g3 is not current yet
    assert cat.calls["leashed"] == 1
    s.forms("w", "g1", [A, A])  # g1 lost nothing
    assert cat.calls == {"forms_for": [A], "leashed": 1}


def test_a_build_that_fails_falls_back_to_every_leashed_secret(make, caplog):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    cat.fail_forms_for = True
    s = make(cat)
    got = []
    with caplog.at_level(logging.WARNING):
        forms = s.build("w", "g1", A, done=got.append).result(WAIT)
    assert ("a", "password") in forms.ids and got == [forms]
    assert cat.calls == {"forms_for": [A], "leashed": 1}
    assert "OSError" in caplog.text and "alpha-11" not in caplog.text


def test_a_build_where_both_reads_fail_is_done_with_none(make, caplog):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    cat.fail_forms_for = cat.fail_leashed = True
    s = make(cat)
    got = []
    with caplog.at_level(logging.WARNING):
        future = s.build("w", "g1", A, done=got.append)
        with pytest.raises(faults.ScrubFailed):
            future.result(WAIT)
    assert got == [None]
    with pytest.raises(faults.ScrubFailed):
        s.result("w", "g1", {"t": "alpha-11"}, [A, A])
    assert "alpha-11" not in caplog.text


def test_a_build_past_the_timeout_fails_closed(make, monkeypatch, caplog):
    monkeypatch.setattr(scrub_module, "BUILD_TIMEOUT", 0.01)
    gate = threading.Event()
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}}, gate=gate)
    s = make(cat)
    s.build("w", "g1", A)
    try:
        with (
            caplog.at_level(logging.WARNING),
            pytest.raises(faults.ScrubFailed) as failed,
        ):
            s.result("w", "g1", {"t": "alpha-11"}, [A, A])
    finally:
        gate.set()
    assert str(failed.value) == faults.SCRUB_FAILED
    assert failed.value.__cause__ is None and failed.value.__suppress_context__
    assert "TimeoutError" in caplog.text and "alpha-11" not in caplog.text


def test_a_leashed_read_past_the_timeout_fails_closed(make, monkeypatch):
    # Long enough for the revision read, which comes first, to be quick.
    monkeypatch.setattr(scrub_module, "BUILD_TIMEOUT", 0.5)
    gate = threading.Event()
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    real = cat.leashed

    def stuck():
        gate.wait(WAIT)
        return real()

    cat.leashed = stuck
    s = make(cat)
    try:
        with pytest.raises(faults.ScrubFailed):
            s.forms("w", "g1", [None, None])
    finally:
        gate.set()
    assert cat.revisions == 1  # the revision was read: the leashed read timed out


def test_a_read_that_timed_out_is_cancelled(make, monkeypatch):  # fix round 1
    monkeypatch.setattr(scrub_module, "BUILD_TIMEOUT", 0.01)
    gate = threading.Event()
    cat = FakeCatalogue({A: {}, B: {}}, gate=gate)
    s = make(cat, workers=1)
    s.build("w", "g1", A)  # holds the one worker
    try:
        with pytest.raises(faults.ScrubFailed):
            s.forms("w", "g1", [A, A])  # its revision read queues, times out
    finally:
        gate.set()
    s.build("w", "g1", B).result(WAIT)  # runs after anything queued before it
    assert cat.revisions == 2  # the two builds; the timed-out read never ran


def test_a_failed_revision_read_fails_closed(make, caplog):
    cat = FakeCatalogue({})

    def broken():
        raise KeyError("alpha-11")

    cat.revision = broken
    s = make(cat)
    with caplog.at_level(logging.WARNING), pytest.raises(faults.ScrubFailed):
        s.result("w", "g1", {"t": "x"}, [A, A])
    assert "KeyError" in caplog.text and "alpha-11" not in caplog.text


def test_forms_reach_capture_when_built(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    capture = FakeCapture()
    s = make(cat, capture)
    forms = s.build("w", "g1", A).result(WAIT)
    s.build("w", "g1", A).result(WAIT)  # built already: not handed again
    assert capture.secrets == [("w", forms)]
    s.build("w", "g1", "").result(WAIT)  # nothing read, nothing handed
    assert len(capture.secrets) == 1


def test_a_capture_that_raises_never_fails_the_build(make, caplog):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})

    class Broken:
        def secret(self, workspace, forms):
            raise RuntimeError("alpha-11")

    s = make(cat, Broken())
    with caplog.at_level(logging.WARNING):
        assert ("a", "password") in s.build("w", "g1", A).result(WAIT).ids
    assert "RuntimeError" in caplog.text and "alpha-11" not in caplog.text


def test_a_selected_frame_is_unknown(make):  # ruling 15
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    o = Origins()
    o.opened("w", "g1")  # known unframed (round 3)
    s = make(cat, origins=o)
    s.build("w", "g1", A).result(WAIT)
    s.forms("w", "g1", [A, A])
    assert cat.calls["leashed"] == 0
    o.framed("w", "g1", True)
    s.forms("w", "g1", [A, A])
    assert cat.calls["leashed"] == 1
    o.framed("w", "g1", False)
    s.forms("w", "g1", [A, A])
    assert cat.calls["leashed"] == 1


def test_a_browser_capture_heard_first_is_unframed_once_opened(make):
    """Capture attaches before the first page, so its navigation takes the slot
    implicitly before `open_session` gives it: opened, it is known unframed."""
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    o = Origins()
    s = make(cat, origins=o)
    o.arrived("w", "g1", f"{A}/")  # capture's navigationStarted
    assert o.framed("w", "g1") is True  # taken implicitly: unknown
    o.opened("w", "g1")
    assert o.framed("w", "g1") is False and o.current("w", "g1") == A
    s.opened("w", "g1")
    s.build("w", "g1", A).result(WAIT)
    s.forms("w", "g1", [A, A])
    s.forms("w", "g1", [A, A])
    assert cat.calls == {"forms_for": [A], "leashed": 0}


def test_opened_keeps_a_frame_a_call_said_since():
    o = Origins()
    o.arrived("w", "g1", f"{A}/")
    o.framed("w", "g1", True)  # a racing `frame` call, after the bind
    o.opened("w", "g1")
    assert o.framed("w", "g1") is True


def test_changed_starts_a_build_and_returns_at_once(make):
    gate = threading.Event()
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}}, gate=gate)
    s = make(cat)
    s.changed(OriginChanged("w", "g1", None, A, False))  # does not wait on gate
    gate.set()
    s.build("w", "g1", A).result(WAIT)
    assert cat.calls["forms_for"] == [A]


def test_changed_with_no_catalogue_does_nothing(make):
    s = make(None)
    s.changed(OriginChanged("w", "g1", None, A, False))
    assert s.build("w", "g1", A).result(WAIT) == Forms()


def test_an_error_is_scrubbed_in_place(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    exc = ValueError("no element after typing alpha-11")
    assert s.error("w", "g1", exc, [A, A]) is None
    assert exc.args == ("no element after typing [secret:a/password]",)
    assert type(exc) is ValueError


def test_an_error_that_cannot_be_scrubbed_fails_closed(make, caplog):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    exc = OSError(2, "missing", "/tmp/alpha-11")  # filename is out of reach
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(faults.ScrubFailed) as failed,
    ):
        s.error("w", "g1", exc, [A, A])
    assert failed.value.__cause__ is None and failed.value.__suppress_context__
    assert "Unscrubbed" in caplog.text and "alpha-11" not in caplog.text


def test_a_resource_is_scrubbed_with_every_leashed_secret(make):  # ruling 16
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    assert s.resource("w", "site data: alpha-11") == (
        "site data: [secret:a/password]"
    )
    assert cat.calls == {"forms_for": [], "leashed": 1}


def test_a_resource_keeps_what_the_browser_kept(make):  # ruling 10
    cat = FakeCatalogue({A: {("a", "password"): "old-value-1"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {("a", "password"): "new-value-2"}})
    s.forms("w", "g1", [A, A])
    assert s.resource("w", "old-value-1 new-value-2") == (
        "[secret:a/password] [secret:a/password]"
    )


def test_close_fails_a_build_still_owed(make):
    gate = threading.Event()
    cat = FakeCatalogue({A: {}}, gate=gate)
    s = Scrubber(cat, ttl=60, workers=1)
    s.build("w", "g1", A)  # holds the one worker
    queued = s.build("w", "g1", B)
    s.close()
    gate.set()
    with pytest.raises(faults.ScrubFailed):
        queued.result(WAIT)


def test_scrub_failed_says_the_fixed_sentence():
    assert str(faults.ScrubFailed()) == faults.SCRUB_FAILED
    assert faults.SCRUB_FAILED == (
        "this call's result could not be checked for secrets, so it is not "
        "returned. The action itself may have run: read the page before you "
        "repeat it."
    )


# --- fix round 2: taking the slot explicitly, and a slot that heals ------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_opened_takes_the_slot_from_a_browser_never_forgotten():
    """A workspace that lost its record cannot name g1 to forget it."""
    seen = []
    o = Origins()
    o.listen(seen.append)
    o.arrived("w", "g1", f"{A}/")
    o.framed("w", "g1", True)
    o.opened("w", "g2")
    o.arrived("w", "g2", f"{B}/")
    assert [(e.browser, e.old, e.new) for e in seen] == [
        ("g1", None, A),
        ("g2", None, B),
    ]
    assert o.framed("w", "g2") is False and o.current("w", "g2") == B
    o.arrived("w", "g1", f"{A}/x")  # g1 is finished with: stale
    assert o.current("w", "g2") == B and len(seen) == 2


def test_opened_twice_keeps_the_browser_it_gave_the_slot_to():
    o = Origins()
    o.opened("w", "g1")
    o.arrived("w", "g1", f"{A}/")
    o.opened("w", "g1")
    assert o.current("w", "g1") == A


def test_opened_starts_the_scrubber_afresh(make):
    cat = FakeCatalogue({A: {("a", "password"): "old-value-1"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {("a", "password"): "new-value-2"}})
    s.forms("w", "g1", [A, A])  # g1 keeps old-value-1
    leashed = cat.calls["leashed"]
    s.opened("w", "g2")
    s.build("w", "g2", A).result(WAIT)  # installed: a read of A, not of everything
    forms = s.forms("w", "g2", [A, A])
    assert cat.calls["leashed"] == leashed and cat.calls["forms_for"][-1] == A
    assert scrub("old-value-1 new-value-2", forms) == "old-value-1 [secret:a/password]"
    s.forms("w", "g1", [A, A])  # g1 is finished with: stale, every secret
    assert cat.calls["leashed"] == leashed + 1


def test_opened_twice_keeps_the_scrubbers_state(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.opened("w", "g1")
    s.build("w", "g1", A).result(WAIT)
    s.opened("w", "g1")
    s.forms("w", "g1", [A, A])
    assert cat.calls == {"forms_for": [A], "leashed": 0}


def test_a_slot_left_held_heals_after_the_ttl():
    clock = Clock()
    seen = []
    o = Origins(ttl=10, clock=clock)
    o.listen(seen.append)
    o.arrived("w", "g1", f"{A}/")
    o.arrived("w", "g2", f"{B}/")  # g1 still holds it
    assert [e.browser for e in seen] == ["g1"]
    clock.now += 11
    o.arrived("w", "g2", f"{B}/")
    assert [(e.browser, e.new) for e in seen] == [("g1", A), ("g2", B)]
    assert o.current("w", "g2") == B
    assert o.framed("w", "g2") is True  # taken implicitly: unknown (round 3)


def test_a_scrubber_slot_left_held_heals_after_the_ttl(make):
    clock = Clock()
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat, ttl=10, clock=clock)
    s.build("w", "g1", A).result(WAIT)
    clock.now += 11
    s.build("w", "g2", A).result(WAIT)
    s.forms("w", "g2", [A, A])
    assert cat.calls == {"forms_for": [A, A], "leashed": 0}


def test_a_stale_streak_is_logged_once_without_the_browser(caplog):
    o = Origins()
    o.arrived("w", "g1", f"{A}/")
    with caplog.at_level(logging.DEBUG, logger="kubed.selenium_flow.workspace"):
        o.arrived("w", "g2", f"{B}/?q=alpha-11")
        o.framed("w", "g2", True)
        stale = [r for r in caplog.records if "slot" in r.getMessage()]
        assert len(stale) == 1
        o.arrived("w", "g1", f"{A}/")  # the holder: the streak ends
        o.arrived("w", "g2", f"{B}/")
        stale = [r for r in caplog.records if "slot" in r.getMessage()]
    assert len(stale) == 2
    for record in stale:
        said = record.getMessage()
        assert "w" in said and "g1" not in said and "g2" not in said
        assert "b.test" not in said and "alpha-11" not in said


def test_a_scrubbers_stale_streak_is_logged_once_without_the_browser(make, caplog):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    with caplog.at_level(logging.DEBUG, logger="kubed.selenium_flow.workspace"):
        s.forms("w", "g2", [A, A])
        s.result("w", "g2", {"t": "alpha-11"}, [A, A])
        stale = [r for r in caplog.records if "slot" in r.getMessage()]
        assert len(stale) == 1
        s.forms("w", "g1", [A, A])  # the holder: the streak ends
        s.forms("w", "g2", [A, A])
        stale = [r for r in caplog.records if "slot" in r.getMessage()]
    assert len(stale) == 2
    for record in stale:
        said = record.getMessage()
        assert "g1" not in said and "g2" not in said and "alpha-11" not in said


# --- fix round 3: a slot taken implicitly has an unknown frame ------------------


def test_a_slot_taken_implicitly_has_an_unknown_frame():  # ruling 15, fails closed
    """An evicted slot taken again must not forget a selected frame as "none"."""
    o = Origins()
    o.arrived("w", "g", f"{A}/")
    assert o.framed("w", "g") is True
    assert o.framed("w", "g", False) is False
    assert o.framed("w", "g") is False
    o2 = Origins()
    o2.opened("w", "g")
    assert o2.framed("w", "g") is False
    o2.arrived("w", "g", f"{A}/")
    assert o2.framed("w", "g") is False


def test_a_slot_evicted_and_taken_again_is_framed():
    clock = Clock()
    o = Origins(ttl=10, clock=clock)
    o.opened("w", "g")
    o.framed("w", "g", True)
    clock.now += 11  # the slot goes, and the browser takes it again
    o.arrived("w", "g", f"{A}/")
    assert o.framed("w", "g") is True


# --- fix round 4: carry gives the slot; a browser carried from is stale ---------


def test_carry_gives_the_slot_whoever_held_it(make):
    """`carry` from a browser that did not hold the slot still gives ``new`` it."""
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "X", A).result(WAIT)
    s.carry("w", "old", "new")
    s.forms("w", "new", [A, A])  # A not yet built for new: unknown, once
    assert cat.calls["leashed"] == 1
    s.build("w", "new", A).result(WAIT)  # the build that call started
    s.forms("w", "new", [A, A])
    s.forms("w", "new", [A, A])
    assert cat.calls["leashed"] == 1  # new holds the slot: no every-secret read
    s.forms("w", "X", [A, A])  # X is finished with: stale
    assert cat.calls["leashed"] == 2


def test_carry_from_another_holder_unmasks_nothing(make):  # ruling 10, fails safe
    cat = FakeCatalogue({A: {("a", "password"): "old-value-1"}})
    s = make(cat)
    s.build("w", "X", A).result(WAIT)
    cat.rotate({A: {("a", "password"): "new-value-2"}})
    s.forms("w", "X", [A, A])  # X keeps old-value-1, and builds new-value-2
    s.build("w", "X", A).result(WAIT)
    both = "old-value-1 new-value-2"
    masked = "[secret:a/password] [secret:a/password]"
    s.carry("w", "old", "new")
    cat.rotate({})  # nothing in scope any more: only what was held masks
    assert scrub(both, s.forms("w", "new", [A, A])) == masked
    s.build("w", "new", A).result(WAIT)
    assert scrub(both, s.forms("w", "new", [A, A])) == masked


def test_carry_on_a_free_slot_gives_it(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.carry("w", "old", "new")
    s.forms("w", "other", [None, None])  # new holds it: other is stale
    s.build("w", "new", A).result(WAIT)
    s.forms("w", "new", [A, A])
    assert cat.calls == {"forms_for": [A], "leashed": 1}


def test_a_browser_carried_from_is_stale(make):
    clock = Clock()
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat, ttl=10, clock=clock)
    s.build("w", "g1", A).result(WAIT)
    s.carry("w", "g1", "g2")
    s.forms("w", "g1", [A, A])  # a late call on g1: every secret
    assert cat.calls == {"forms_for": [A], "leashed": 1}
    got = []
    future = s.build("w", "g1", B, done=got.append)  # reads nothing
    with pytest.raises(faults.ScrubFailed):
        future.result(WAIT)
    assert got == [None] and cat.calls["forms_for"] == [A]
    clock.now += 6
    s.forms("w", "g1", [A, A])  # does not slide g2's entry
    clock.now += 6
    s.forms("w", "g2", [A, A])  # g2's entry expired: A unknown again
    assert cat.calls["leashed"] == 3


def test_a_browser_carried_from_is_framed():  # ruling 15: Origins on a carry
    o = Origins()
    o.opened("w", "g1")
    o.arrived("w", "g1", f"{A}/")
    o.opened("w", "g2")  # what a reap's reopen calls beside `Scrubber.carry`
    assert o.framed("w", "g1") is True
    assert o.framed("w", "g2") is False


# --- the choke point: every call's result and error (Task 6) ------------------


class ScopeActions(RecordingActions):
    """`RecordingActions` whose one action answers ``result`` or raises ``raises``.

    ``page`` and ``write`` are what a bound write asks of it."""

    def __init__(self):
        super().__init__()
        self.result: dict = {"url": f"{A}/", "title": "t"}
        self.raises: BaseException | None = None
        self.written: dict = {"url": f"{A}/login", "title": "t", "value": None}

    def do(self, session_id):
        if self.raises is not None:
            raise self.raises
        return dict(self.result)

    def page(self, session_id):
        return {"url": f"{A}/login", "title": "Log in"}

    def write(self, session_id, text, **kwargs):
        if self.raises is not None:
            raise self.raises
        return dict(self.written)


class BindingCatalogue(FakeCatalogue):
    """A `FakeCatalogue` a bound write can type from: ``a/password`` on ``A``."""

    def entry(self, name):
        return {"name": name, "keys": ["password"], "allowed_urls": [A]}

    def allows(self, name, url):
        return urls.scope_origin(url) == A

    def value(self, name, key):
        return self.scopes[A][(name, key)]


CALLER = Caller("w", "query")
SECRET_A = {A: {("a", "password"): "alpha-11"}}


def scoped(catalogue=None, *, url=f"{A}/"):
    """A `Workspaces` over doubles, its browser open on ``url``."""
    ws = Workspaces(ScopeActions(), MemoryStore(), catalogue=catalogue)
    ws.open_browser(CALLER, url=url)
    return ws


@pytest.fixture
def ws():
    made = scoped(BindingCatalogue(SECRET_A))
    yield made
    made.scrubber.close()


@pytest.fixture
def ws_without_secrets():
    made = scoped(None)
    yield made
    made.scrubber.close()


def act(ws, tool="extract"):
    return ws.act(CALLER, ws.actions.do, tool=tool)


def sid(ws):
    return ws.store.get("w").session_id


def test_a_value_never_typed_is_marked_on_its_site(ws):  # ruling 1, the issue's repro
    ws.actions.result = {"url": f"{A}/login", "title": "t", "html": '<input value="alpha-11">'}
    assert act(ws)["html"] == '<input value="[secret:a/password]">'


def test_the_same_value_on_another_site_is_left_alone():
    cat = BindingCatalogue({A: {("a", "password"): "alpha-11"}, B: {}})
    ws = scoped(cat, url=f"{B}/")
    try:
        ws.actions.result = {"url": f"{B}/", "title": "alpha-11"}
        assert act(ws)["title"] == "alpha-11"
        assert cat.calls == {"forms_for": [B], "leashed": 0}
    finally:
        ws.scrubber.close()


def test_an_error_is_scrubbed_and_keeps_its_status(ws):
    ws.actions.raises = TimeoutException(msg=f"waited on {A}/?q=alpha-11")
    with pytest.raises(TimeoutException) as raised:
        act(ws)
    assert "alpha-11" not in faults.message(raised.value)
    assert "[secret:a/password]" in faults.message(raised.value)
    assert errors.status_for(raised.value) == 400


def test_a_url_carrying_a_value_is_marked_and_never_recorded(ws):  # Review Focus 3
    ws.actions.result = {"url": f"{A}/search?q=alpha-11", "title": "t"}
    assert "[secret:a/password]" in act(ws)["url"]
    record = ws.store.get("w")
    assert "alpha-11" not in (record.url or "")
    assert "alpha-11" not in json.dumps(record.history)
    # A later reopen goes back to the page before it, never to the value.
    ws.actions.grid.alive.discard(sid(ws))
    ws.actions.result = {"url": f"{A}/", "title": "t"}
    act(ws)
    assert "alpha-11" not in json.dumps(ws.actions.opened_urls)


def test_a_dialog_result_is_scrubbed_with_every_leashed_secret(ws):  # Review Focus 5
    ws.actions.result = {"url": None, "title": None, "dialog": "alpha-11"}
    assert act(ws)["dialog"] == "[secret:a/password]"


def test_no_catalogue_leaves_every_result_as_it_was(ws_without_secrets, monkeypatch):  # Review Focus 1
    result = {"url": f"{A}/", "title": "alpha-11"}
    ws_without_secrets.actions.result = dict(result)
    monkeypatch.setattr(scrub_module, "scrub_values", lambda *a: pytest.fail("scanned"))
    assert act(ws_without_secrets) == result


def test_no_secret_allowed_here_returns_the_very_object(monkeypatch):  # Review Focus 1
    ws = scoped(BindingCatalogue({A: {}}))
    try:
        returned = {"url": f"{A}/", "title": "alpha-11"}
        ws.actions.do = lambda session_id: returned
        monkeypatch.setattr(scrub_module, "scrub_values", lambda *a: pytest.fail("scanned"))
        assert act(ws) is returned
    finally:
        ws.scrubber.close()


def test_a_scrub_that_fails_answers_the_fixed_sentence(ws, monkeypatch):  # ruling 2
    def boom():
        raise OSError("cannot stat alpha-11")

    monkeypatch.setattr(ws.scrubber._catalogue, "revision", boom)
    ws.actions.result = {"url": f"{A}/", "title": "alpha-11"}
    with pytest.raises(faults.ScrubFailed, match="could not be checked for secrets") as raised:
        act(ws)
    assert errors.status_for(raised.value) == 500
    assert faults.message(raised.value) == faults.SCRUB_FAILED


def test_an_error_whose_scrub_fails_is_never_chained_to_it(ws, monkeypatch):  # ruling 2
    def boom():
        raise OSError("cannot stat alpha-11")

    monkeypatch.setattr(ws.scrubber._catalogue, "revision", boom)
    ws.actions.raises = ValueError("typed alpha-11")
    with pytest.raises(faults.ScrubFailed) as raised:
        act(ws)
    assert raised.value.__context__ is None and raised.value.__cause__ is None


def test_a_frame_result_sets_framed(ws):  # ruling 15
    ws.actions.result = {"action": "switched", "in_frame": True}
    act(ws, tool="frame")
    assert ws.origins.framed("w", sid(ws)) is True
    ws.actions.result = {"action": "parent", "in_frame": False}
    act(ws, tool="frame")
    assert ws.origins.framed("w", sid(ws)) is False


def test_the_event_fires_for_open_reopen_and_a_withheld_bound_write(ws):
    seen: list[OriginChanged] = []
    ws.on_origin_changed(seen.append)
    first = sid(ws)
    ws.open_browser(CALLER, url=f"{A}/")
    second = sid(ws)
    assert seen[-1] == OriginChanged("w", second, None, A, False)
    # A bound write that lands on B with the value in its URL: withheld, and heard.
    ws.actions.written = {"url": f"{B}/?q=alpha-11", "title": "t", "value": None}
    shown = secrets.perform_write(
        ws.scrubber._catalogue, ws.actions, ws, "w",
        {"selector": {"css": "#p"}, "secret": {"name": "a", "key": "password"}},
    )
    assert "alpha-11" not in json.dumps(shown)
    assert seen[-1] == OriginChanged("w", second, A, B, False)
    assert ws.store.get("w").url == f"{A}/"  # withheld
    # The Grid reaps it: the reopen is heard on the page the record holds.
    ws.actions.grid.alive.discard(second)
    act(ws)
    third = sid(ws)
    assert third not in (first, second)
    assert OriginChanged("w", third, None, A, False) in seen


def test_open_session_scrubs_what_it_tells(ws):
    told = ws.open_browser(CALLER, url=f"{A}/search?q=alpha-11")
    assert told["url"] == f"{A}/search?q=[secret:a/password]"
    record = ws.store.get("w")
    assert "alpha-11" not in json.dumps(record.history)


def test_an_open_that_lost_its_race_is_scrubbed_and_gives_no_slot(ws, monkeypatch):
    """The browser that lost is quit, and the one that won is given its slot by
    its own open: this one describes the record, scrubbed, and takes nothing."""
    monkeypatch.setattr(ws, "remember", lambda *a, **k: "won-elsewhere")
    ws.store.update("w", lambda r: replace(r, history=[
        {"origin": A, "url": f"{A}/?q=alpha-11", "at": time.time()}
    ]))
    told = ws.open_browser(CALLER, url=f"{A}/")
    assert told["url"] == f"{A}/?q=[secret:a/password]"
    assert "w" not in ws.scrubber._entries
    assert ws.origins.current("w", "won-elsewhere") is None


def test_a_bound_writes_url_carrying_another_value_in_scope_is_withheld():
    cat = BindingCatalogue({A: {("a", "password"): "alpha-11", ("a", "token"): "tok-123456"}})
    ws = scoped(cat)
    try:
        ws.actions.written = {"url": f"{A}/?t=tok-123456", "title": "t", "value": None}
        shown = secrets.perform_write(
            cat, ws.actions, ws, "w",
            {"selector": {"css": "#p"}, "secret": {"name": "a", "key": "password"}},
        )
        assert shown["url"] == f"{A}/?t=[secret:a/token]"
        assert "tok-123456" not in json.dumps(ws.store.get("w").history)
    finally:
        ws.scrubber.close()


def test_a_bound_writes_error_is_scrubbed_by_scope_too(ws):
    ws.scrubber._catalogue.scopes[A][("a", "token")] = "tok-123456"
    ws.scrubber._catalogue.rotate(ws.scrubber._catalogue.scopes)
    ws.actions.raises = InvalidSessionIdException("gone on ?t=tok-123456 typing alpha-11")
    with pytest.raises(InvalidSessionIdException) as raised:
        secrets.perform_write(
            ws.scrubber._catalogue, ws.actions, ws, "w",
            {"selector": {"css": "#p"}, "secret": {"name": "a", "key": "password"}},
        )
    said = faults.message(raised.value)
    assert "tok-123456" not in said and "alpha-11" not in said
    assert errors.status_for(raised.value) == 404


def test_end_browser_forgets_the_browser(ws):
    gone = sid(ws)
    act(ws)
    assert ws.origins.current("w", gone) == A
    ws.end_browser(CALLER)
    assert ws.origins.current("w", gone) is None
    assert "w" not in ws.scrubber._entries


def test_a_session_ended_elsewhere_forgets_the_browser(ws):
    gone = sid(ws)
    act(ws)
    ws.browser_ended("w", gone)
    assert ws.origins.current("w", gone) is None
    assert "w" not in ws.scrubber._entries


def test_a_reap_reopen_carries_the_sets(ws):
    old = sid(ws)
    act(ws)
    ws.actions.grid.alive.discard(old)
    ws.actions.result = {"url": f"{A}/", "title": "alpha-11"}
    assert act(ws)["title"] == "[secret:a/password]"
    new = sid(ws)
    assert new != old
    assert ws.scrubber._entries["w"].browser == new
    assert A in ws.scrubber._entries["w"].sets  # carried, not read again
    assert ws.scrubber._catalogue.calls["forms_for"] == [A]
    assert ws.origins.framed("w", new) is False


def test_the_event_is_never_emitted_inside_a_store_fn(ws, monkeypatch):
    inside = []
    change = ws.store.change

    def watched(*args, **kwargs):
        inside.append(True)
        try:
            return change(*args, **kwargs)
        finally:
            inside.pop()

    monkeypatch.setattr(ws.store, "change", watched)
    heard = []
    ws.on_origin_changed(lambda event: heard.append(bool(inside)))
    ws.open_browser(CALLER, url=f"{B}/")
    ws.actions.result = {"url": f"{A}/", "title": "t"}
    act(ws)
    ws.actions.grid.alive.discard(sid(ws))
    act(ws)
    assert heard and not any(heard)


def test_describe_and_captured_are_scrubbed(ws):
    ws.store.update("w", lambda r: replace(r, history=[
        {"origin": A, "url": f"{A}/?q=alpha-11", "at": time.time()}
    ]))
    assert "alpha-11" not in json.dumps(ws.describe(CALLER))
    assert "[secret:a/password]" in json.dumps(ws.describe(CALLER))


def test_captured_is_scrubbed_and_its_off_answer_is_not():
    from kubed.selenium_flow.capture import OFF

    ws = scoped(BindingCatalogue(SECRET_A))
    try:
        assert ws.captured("w", "console") == OFF  # the constant, unread
        assert ws.scrubber._catalogue.calls["leashed"] == 0
        ws.capture = FakeCaptureViews({"entries": [{"text": "pw alpha-11"}]})
        ws.store.update("w", lambda r: replace(r, settings={**r.settings, "capture": True},
                                               set_by={**r.set_by, "capture": "caller"}))
        view = ws.captured("w", "console")
        assert view["entries"] == [{"text": "pw [secret:a/password]"}]
    finally:
        ws.scrubber.close()


class FakeCaptureViews:
    def __init__(self, view):
        self.view = view

    def console(self, name, **query):
        return dict(self.view)


def test_a_screenshots_pixels_pass_unscrubbed(ws):  # ruling 12
    pixels = "iVBORw0KGgoalpha-11AAAA"
    ws.actions.result = {"url": f"{A}/", "image": pixels, "title": "alpha-11"}
    shot = act(ws, tool="screenshot")
    assert shot["image"] == pixels and shot["title"] == "[secret:a/password]"


def test_a_captured_site_data_save_is_stored_raw(ws, monkeypatch):
    from kubed.selenium_flow.site_data import snapshot

    stored = []
    monkeypatch.setattr(ws, "_save_site_data",
                        lambda name, result, captured, producer=None: stored.append(captured))
    ws.actions.result = {"url": f"{A}/", snapshot.CAPTURED: {"cookies": ["alpha-11"]}}
    result = act(ws, tool="save_site_data")
    assert stored == [{"cookies": ["alpha-11"]}]
    assert snapshot.CAPTURED not in result


def test_capture_heard_builds_and_hands_the_forms(ws):
    got: list = []
    done = threading.Event()
    ws.capture_heard("w", sid(ws), f"{A}/x", False, lambda forms: (got.append(forms), done.set()))
    assert done.wait(WAIT)
    assert "alpha-11" in got[0]


def test_capture_heard_with_no_url_hands_every_leashed_secret(ws):
    got: list = []
    done = threading.Event()
    ws.capture_heard("w", sid(ws), None, False, lambda forms: (got.append(forms), done.set()))
    assert done.wait(WAIT)
    assert "alpha-11" in got[0]


def test_a_closed_scrubber_reads_again_when_asked(make):
    """The server's lifespan closes it, and FastMCP runs that lifespan again
    for a later session: a read asked for after starts a thread again."""
    cat = FakeCatalogue(SECRET_A)
    s = make(cat)
    s.close()
    assert "alpha-11" in s.build("w", "g1", A).result(WAIT)


def test_the_server_forgets_a_browser_the_bus_says_ended(tmp_path, monkeypatch):
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.monitor.events import SessionEnded
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    server = SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}))
    try:
        ws = server.workspaces
        ws.origins.opened("w", "g1")
        ws.origins.arrived("w", "g1", f"{A}/")
        ws.scrubber.opened("w", "g1")
        for cause in ("ended", "gone", "lost"):
            server.bus.publish(SessionEnded("w", "g1", cause, 1.0))
        assert ws.origins.current("w", "g1") is None
        assert "w" not in ws.scrubber._entries
    finally:
        server.workspaces.scrubber.close()


def test_the_server_hands_its_catalogue_to_the_workspaces(tmp_path, monkeypatch):
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.delenv("SECRETS_DIRS", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "password").write_text("alpha-11")
    (tmp_path / "a" / "_allowed_urls").write_text(f"{A}\n")
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, secrets={"dirs": str(tmp_path)},
    ))
    try:
        assert server.workspaces.scrubber._catalogue is server.secrets
        assert server.workspaces.scrubber.resource("w", "alpha-11") == "[secret:a/password]"
    finally:
        server.workspaces.scrubber.close()


# --- fix round 1 (Task 6) -------------------------------------------------------


class EndingMonitor:
    """A monitor whose `ended` does what the server's `session.ended`
    subscriber does, at once: the reap is heard before the reopen."""

    def __init__(self):
        self.workspaces = None

    def ended(self, name, session_id, cause):
        self.workspaces.browser_ended(name, session_id)

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_a_reap_heard_first_still_carries_what_the_browser_kept():  # fix 1
    cat = BindingCatalogue({A: {("a", "password"): "alpha-11"}})
    monitor = EndingMonitor()
    ws = Workspaces(ScopeActions(), MemoryStore(), catalogue=cat, monitor=monitor)
    monitor.workspaces = ws
    try:
        ws.open_browser(CALLER, url=f"{A}/")
        act(ws)
        cat.rotate({A: {("a", "password"): "beta-22"}})
        act(ws)  # the rotation is seen: alpha-11 is kept
        ws.actions.grid.alive.discard(sid(ws))
        ws.actions.result = {"url": f"{A}/", "title": "old alpha-11"}
        assert act(ws)["title"] == "old [secret:a/password]"
    finally:
        ws.scrubber.close()


def test_ended_parks_and_carry_claims(make):  # fix 1
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {}})
    s.ended("w", "g1")
    s.carry("w", "g1", "g2")
    assert scrub("alpha-11", s.forms("w", "g2", [B, B])) == "[secret:a/password]"


def test_opened_discards_what_was_parked(make):  # fix 1
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {}})
    s.ended("w", "g1")
    s.opened("w", "g2")
    s.carry("w", "g1", "g3")  # a late carry from the parked browser: nothing left
    assert scrub("alpha-11", s.forms("w", "g3", [""])) == "alpha-11"


def test_a_call_from_a_parked_browser_is_still_stale(make):  # fix 1
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    s.ended("w", "g1")
    before = cat.calls["leashed"]
    assert scrub("alpha-11", s.forms("w", "g1", [A, A])) == "[secret:a/password]"
    assert cat.calls["leashed"] == before + 1
    assert "w" not in s._entries


class FailingOpen(ScopeActions):
    """An open whose first page times out, quoting where it landed."""

    def __init__(self):
        super().__init__()
        self.fail_opens = False

    def open_session(self, url=None, **kwargs):
        if self.fail_opens:
            raise TimeoutException(msg=f"the first page never loaded: {A}/?q=alpha-11")
        return super().open_session(url=url, **kwargs)


def test_an_open_that_fails_is_scrubbed(ws):  # fix 2
    actions = FailingOpen()
    ws.actions = actions
    actions.fail_opens = True
    with pytest.raises(TimeoutException) as raised:
        ws.open_browser(CALLER, url=f"{A}/")
    assert "alpha-11" not in faults.message(raised.value)
    assert errors.status_for(raised.value) == 400
    assert "w" not in ws.scrubber._entries  # no browser took the slot


def test_a_reopen_that_fails_is_scrubbed(ws):  # fix 2
    actions = FailingOpen()
    actions.grid = ws.actions.grid
    ws.actions = actions
    ws.actions.grid.alive.discard(sid(ws))
    actions.fail_opens = True
    with pytest.raises(TimeoutException) as raised:
        act(ws)
    assert "alpha-11" not in faults.message(raised.value)


def test_a_reopen_landing_on_a_value_never_records_it(ws, monkeypatch):  # fix 2
    opened = ws.actions.open_session

    def redirected(url=None, **kwargs):
        made = opened(url=url, **kwargs)
        return {**made, "url": f"{A}/?q=alpha-11"}

    monkeypatch.setattr(ws.actions, "open_session", redirected)
    ws.actions.grid.alive.discard(sid(ws))
    ws.actions.result = {"title": "t"}  # records nothing: the reopen's page stays
    act(ws)
    assert "alpha-11" not in json.dumps(ws.store.get("w").history)


def test_the_reopen_report_is_scrubbed_when_handed_over(ws, monkeypatch):  # fix 3
    report = {"cookies": 0, "sites": [], "skipped": [{"reason": "page said alpha-11"}]}
    monkeypatch.setattr(ws, "_touch", lambda *a, **k: (report, None))
    result = act(ws)
    assert result["site_data"]["skipped"][0]["reason"] == "page said [secret:a/password]"


def test_an_open_whose_scrub_fails_records_no_url(ws, monkeypatch):  # fix 4
    def boom():
        raise OSError("cannot stat")

    monkeypatch.setattr(ws.scrubber._catalogue, "revision", boom)
    monkeypatch.setattr(ws.scrubber._catalogue, "forms_for", lambda o: boom())
    monkeypatch.setattr(ws.scrubber._catalogue, "leashed", boom)
    with pytest.raises(faults.ScrubFailed):
        ws.open_browser(CALLER, url=f"{B}/somewhere")
    assert f"{B}/somewhere" not in json.dumps(ws.store.get("w").history)


def test_a_withheld_open_keeps_the_earlier_entry_of_its_origin(ws):  # fix 5
    first = ws.store.get("w").history[0]
    assert first["url"] == f"{A}/"
    ws.open_browser(CALLER, url=f"{A}/?q=alpha-11")
    history = ws.store.get("w").history
    assert history and history[0]["url"] == f"{A}/"


def test_with_secrets_off_a_screenshot_is_the_very_object(ws_without_secrets):  # fix 6
    shot = {"image": "iVBOR", "url": f"{A}/", "width": 1, "height": 1}
    ws_without_secrets.actions.do = lambda session_id: shot
    returned = act(ws_without_secrets, tool="screenshot")
    assert returned is shot
    assert list(returned)[:4] == ["image", "url", "width", "height"]


# --- fix round 2 (Task 6) -------------------------------------------------------


def test_carry_claims_the_park_when_the_new_browser_took_the_slot_first(make):
    """Capture hears the reopened browser's first page before `carry` runs, so
    the new browser takes the free slot implicitly: the park is still claimed."""
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {}})
    s.ended("w", "g1")
    s.forms("w", "g2", [B, B])  # g2 takes the free slot implicitly
    s.carry("w", "g1", "g2")
    assert scrub("alpha-11", s.forms("w", "g2", [B, B])) == "[secret:a/password]"


def test_a_second_park_keeps_the_first(make):
    cat = FakeCatalogue({A: {("a", "password"): "alpha-11"}})
    s = make(cat)
    s.build("w", "g1", A).result(WAIT)
    cat.rotate({A: {}})
    s.ended("w", "g1")
    s.forms("w", "g2", [B, B])  # g2 takes the free slot implicitly
    s.ended("w", "g2")  # parks g2's, over g1's
    s.carry("w", "g2", "g3")
    assert scrub("alpha-11", s.forms("w", "g3", [B, B])) == "[secret:a/password]"


# --- network_body: the request's origin; scrubbed whole, then cut ----------
# (final review 3 and 11; the prefix oracle, review 12)

LONG = "alpha-11-0123456789abcdef"
LONG_MARK = "[secret:a/password]"


@pytest.fixture
def on_b():
    """A browser on B, where nothing is allowed; A allows ``LONG``."""
    made = scoped(BindingCatalogue({A: {("a", "password"): LONG}, B: {}}), url=f"{B}/")
    yield made
    made.scrubber.close()


@pytest.fixture
def on_a():
    """A browser on A, which allows ``LONG``."""
    made = scoped(BindingCatalogue({A: {("a", "password"): LONG}}))
    yield made
    made.scrubber.close()


def body(text, **more):
    """`network_body`'s answer: the whole masked body, never cut by the action."""
    return {"id": "3.7", "uri": "workspace://network/3.7", "mime": "text/plain",
            "size": len(text.encode()), "binary": False, "text": text,
            "truncated": False, **more}


def test_a_body_is_scrubbed_with_every_leashed_secret(on_b):
    """The body came from the request's origin, never visited, maybe: A's value
    is masked on B's page, whatever the answer says about where it is."""
    on_b.actions.result = body(f"x {LONG} y", url=f"{B}/")
    assert act(on_b, "network_body")["text"] == f"x {LONG_MARK} y"


def prefixes(value):
    """Every guess at ``value`` short of it whole, as a script would spell one."""
    guesses = []
    for n in range(4, len(value)):
        part = value[:n]
        for guess in (part, part.upper(), part.encode().hex(),
                      base64.b64encode(part.encode()).decode()):
            guesses += [guess, guess + "…"]
    return guesses


def test_a_script_returning_prefixes_gets_every_one_back_unchanged(on_a):
    """Masking the start of a value would answer each guess, one character at a
    time: nothing is matched by prefix, an ellipsis or no."""
    guesses = prefixes(LONG)
    on_a.actions.result = {"url": f"{A}/", "title": "t", "result": list(guesses)}
    assert act(on_a, "execute_script")["result"] == guesses


def test_a_value_the_cap_cuts_through_comes_back_as_its_marker(on_b):
    """Scrubbed whole, then cut: the value straddles the byte cap, its marker
    (shorter than it) ends before it."""
    at = BODY_CAP - 20
    on_b.actions.result = body("x" * at + LONG + "y" * 100)
    got = act(on_b, "network_body")
    assert got["text"] == "x" * at + LONG_MARK + "y"
    assert got["truncated"] is True and LONG[:4] not in got["text"]


def test_a_marker_the_cap_would_cut_is_cut_before(on_b):
    at = BODY_CAP - 10
    on_b.actions.result = body("x" * at + LONG + "y" * 100)
    got = act(on_b, "network_body")
    assert got["text"] == "x" * at and got["truncated"] is True


def test_a_body_under_the_cap_is_whole(on_b):
    on_b.actions.result = body("token=" + LONG[:12] + " and " + LONG)
    got = act(on_b, "network_body")
    assert got["text"] == "token=" + LONG[:12] + " and " + LONG_MARK
    assert got["truncated"] is False


def test_a_body_is_cut_at_its_cap_with_secrets_off(ws_without_secrets):
    ws_without_secrets.actions.result = body("é" * BODY_CAP)
    got = act(ws_without_secrets, "network_body")
    assert got["text"] == "é" * (BODY_CAP // 2) and got["truncated"] is True


def test_another_action_is_never_cut(ws_without_secrets):
    long = "x" * (BODY_CAP + 10)
    ws_without_secrets.actions.result = {"url": f"{A}/", "title": "t", "text": long,
                                         "elements": [{"name": long}]}
    got = act(ws_without_secrets, "extract")
    assert got["text"] == long and got["elements"][0]["name"] == long


def outlined(*names):
    return {"url": f"{A}/", "title": "t", "count": len(names), "total": len(names),
            "elements": [{"role": "button", "name": name} for name in names]}


def test_an_outline_name_the_cap_cuts_through_keeps_its_marker(on_a):
    """The value straddles character 80; its marker ends before it."""
    on_a.actions.result = outlined("n" * 60 + LONG + "z" * 10)
    name = act(on_a, "outline")["elements"][0]["name"]
    assert name == "n" * 60 + LONG_MARK + "z" + "…"


def test_an_outline_name_whose_marker_the_cap_would_cut_is_cut_before(on_a):
    on_a.actions.result = outlined("n" * 70 + LONG + "z" * 10)
    name = act(on_a, "outline")["elements"][0]["name"]
    assert name == "n" * 70 + "…" and LONG[:4] not in name


def test_an_outline_name_of_80_characters_is_left_as_it_is(ws):
    ws.actions.result = outlined("n" * 80, "short")
    names = [e["name"] for e in act(ws, "outline")["elements"]]
    assert names == ["n" * 80, "short"]


def test_an_outline_name_is_cut_at_80_with_secrets_off(ws_without_secrets):
    ws_without_secrets.actions.result = outlined("n" * 100)
    name = act(ws_without_secrets, "outline")["elements"][0]["name"]
    assert name == "n" * 80 + "…"


def test_the_hand_backs_outline_is_cut_too(ws_without_secrets):
    ws_without_secrets.actions.outline = ws_without_secrets.actions.do
    ws_without_secrets.actions.result = outlined("n" * 100)
    summary = ws_without_secrets.handed_back("w", None)
    assert summary["outline"]["elements"][0]["name"] == "n" * 80 + "…"


def test_the_hand_backs_outline_is_scrubbed_before_it_is_cut(on_a):
    """Cut after `_said`'s scrub: the value straddling character 80 is its
    marker, never a prefix."""
    on_a.actions.outline = on_a.actions.do
    on_a.actions.result = outlined("n" * 60 + LONG + "z" * 10)
    summary = on_a.handed_back("w", None)
    assert summary["outline"]["elements"][0]["name"] == "n" * 60 + LONG_MARK + "z…"


# --- flows_here: a flow's own words are scrubbed (final review 10) ----------

HINTED = [{"name": "login", "uri": "flow://flows/login",
           "description": "signs in as alpha-11"}]


@pytest.fixture
def hinted():
    made = Workspaces(ScopeActions(), MemoryStore(),
                      catalogue=BindingCatalogue(SECRET_A),
                      hints=lambda name, url: [dict(h) for h in HINTED])
    yield made
    made.scrubber.close()


def test_a_hint_on_a_result_is_scrubbed_as_flow_flows_is(hinted):
    hinted.open_browser(CALLER, url=f"{A}/")
    hinted.actions.result = {"url": f"{A}/next", "title": "t"}
    told = act(hinted)
    assert told["flows_here"][0]["description"] == "signs in as [secret:a/password]"


def test_a_hint_on_an_open_is_scrubbed_too(hinted):
    told = hinted.open_browser(CALLER, url=f"{A}/")
    assert told["flows_here"][0]["description"] == "signs in as [secret:a/password]"


def test_a_hint_that_cannot_be_scrubbed_is_dropped_never_the_call(hinted, monkeypatch):
    hinted.open_browser(CALLER, url=f"{A}/")
    hinted.actions.result = {"url": f"{A}/next", "title": "t"}

    def failed(name, value):
        raise faults.ScrubFailed()

    monkeypatch.setattr(hinted.scrubber, "resource", failed)
    told = act(hinted)
    assert "flows_here" not in told and "alpha-11" not in json.dumps(told)
