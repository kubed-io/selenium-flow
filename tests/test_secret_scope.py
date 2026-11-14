"""The origin-changed event and the scrubber (spec 2026-11-14-secret-scope).

Each browser has a current origin; the scrubber reads the secrets allowed there
once, off the loop, when it changes, and scrubs a result with them. Nothing is
read and nothing is scanned when secrets are off, and an opaque page reads
nothing at all.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future

import pytest

from kubed.selenium_flow import faults, secrets, urls
from kubed.selenium_flow.redaction import Forms, scrub
from kubed.selenium_flow.workspace import scrub as scrub_module
from kubed.selenium_flow.workspace.origins import OriginChanged, Origins
from kubed.selenium_flow.workspace.scrub import Scrubber

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
