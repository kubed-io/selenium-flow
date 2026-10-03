"""One browser-driving call at a time per session, and `end_browser` still cuts in.

Ruling (2), saga Chapter 4 open question 2. Two of our action sequences
interleaved on one browser is unsafe, so a session's calls take turns — but
`end_browser` is the call that exists to stop a long `assert`, so it never
waits for one. Every test here goes through the real `Actions` on a scripted
driver: the lock lives in `Recipe.run`, and a double of an action would skip it.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from kubed.selenium_flow import secrets
from kubed.selenium_flow.core import cancel
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.flows import run as flowrun
from kubed.selenium_flow.secrets import ALLOWED_URLS, Catalogue, FilesystemSource
from kubed.selenium_flow.session import locks
from kubed.selenium_flow.session.sessions import Caller
from kubed.selenium_flow.session.store import SessionRecord

from .conftest import NAMED, OTHER, manager
from .fakes import ScriptedDriver

pytestmark = pytest.mark.unit

SITE = "https://nc.example.com"
SECRET = {"name": "nextcloud", "key": "password"}


class Driver(ScriptedDriver):
    """A `ScriptedDriver` that tells a test when a page load or a script runs."""

    def __init__(self, on_get=None, on_script=None, **settings):
        super().__init__(**settings)
        self.__dict__["on_get"] = on_get or (lambda url: None)
        self.__dict__["on_script"] = on_script or (lambda script: None)

    def get(self, url):
        self.on_get(url)
        super().get(url)

    def execute_script(self, script, *args):
        self.on_script(script)
        return super().execute_script(script, *args)


class Grid:
    """Scripted browsers by Grid id, which can be quit."""

    def __init__(self, **drivers):
        self.drivers = drivers
        self.quit_ = []

    def reconnect(self, session_id):
        return self.drivers[session_id]

    def is_alive(self, session_id):
        return session_id in self.drivers and session_id not in self.quit_

    def quit(self, session_id):
        self.quit_.append(session_id)


def wired(**browsers):
    """Real `Actions` and a `SessionManager` over ``name=(grid id, driver)``."""
    grid = Grid(**dict(browsers.values()))
    actions = Actions(grid)
    sessions = manager(actions)
    for name, (gid, _) in browsers.items():
        sessions.store.set(name, SessionRecord(session_id=gid))
    return actions, sessions


def in_thread(work):
    """Run ``work`` in a thread; the thread, and a list its outcome lands in."""
    outcome = []

    def run():
        try:
            outcome.append(work())
        except Exception as exc:  # noqa: BLE001 - asserted on by the test
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def test_two_sessions_drive_their_browsers_at_the_same_time():
    """The lock is per session: a call on one never waits for another's."""
    both = threading.Barrier(2, timeout=5)  # broken unless both are inside at once

    def meet(_url):
        both.wait()

    actions, sessions = wired(
        **{NAMED: ("b1", Driver(on_get=meet)), OTHER: ("b2", Driver(on_get=meet))}
    )
    runs = [
        in_thread(
            lambda name=name: sessions.act(
                Caller(name), lambda s: actions.navigate(s, f"https://{name}.test/")
            )
        )
        for name in (NAMED, OTHER)
    ]
    for thread, _ in runs:
        thread.join(10)

    assert [outcome[0]["url"] for _, outcome in runs] == [
        f"https://{NAMED}.test/",
        f"https://{OTHER}.test/",
    ]


def test_end_browser_does_not_wait_for_an_assert_and_the_assert_lets_go():
    """A ten-second `assert` holds the session's browser. `end_browser` returns
    at once, and the assert ends with its existing cancellation rather than
    polling a browser that is gone."""
    polling = threading.Event()
    driver = Driver(
        scripts={"return false": False}, on_script=lambda script: polling.set()
    )
    actions, sessions = wired(**{NAMED: ("abc", driver)})

    started = time.monotonic()
    thread, outcome = in_thread(
        lambda: sessions.act(
            Caller(NAMED),
            lambda s: actions.assert_(s, "return false", wait_timeout=10),
        )
    )
    assert polling.wait(5), "the assert never started"

    asked = time.monotonic()
    ended, _ = in_thread(lambda: sessions.end_browser(Caller(NAMED)))
    ended.join(1)
    assert not ended.is_alive(), "end_browser waited for the assert"
    assert time.monotonic() - asked < 1

    thread.join(5)
    assert not thread.is_alive()
    assert time.monotonic() - started < 5, "the assert ran on after its browser"
    (error,) = outcome
    assert isinstance(error, cancel.Cancelled)
    assert str(error) == "the run was cancelled: its caller stopped waiting"
    assert actions.grid.quit_ == ["abc"]
    assert not sessions.store.get(NAMED).attached


@pytest.fixture
def leashed(tmp_path):
    """A secret allowed on `SITE`, a browser on its login page, and a navigate
    that tries to land inside the bound write's page read.

    Once the write reads the frame's origin, another call navigates the same
    session away, and the read waits to see whether it lands: unlocked, it
    does, before the keystroke.
    """
    (tmp_path / "nextcloud").mkdir()
    (tmp_path / "nextcloud" / "password").write_text("hunter2")
    (tmp_path / "nextcloud" / ALLOWED_URLS).write_text(SITE)
    landed = threading.Event()
    racing = []

    def read_origin(script):
        if "location.origin" not in script or racing:
            return
        racing.append(
            in_thread(
                lambda: sessions.act(
                    Caller(NAMED), lambda s: actions.navigate(s, f"{SITE}/elsewhere")
                )
            )
        )
        landed.wait(0.5)

    driver = Driver(
        url=f"{SITE}/login",
        scripts={"location.origin": SITE},
        on_script=read_origin,
        on_get=lambda url: landed.set(),
    )
    actions, sessions = wired(**{NAMED: ("abc", driver)})
    return SimpleNamespace(
        catalogue=Catalogue([FilesystemSource(tmp_path)]),
        actions=actions,
        sessions=sessions,
        driver=driver,
        racing=racing,
    )


def moved_only_after_typing(leash):
    """The racing navigate ran, and only once the keystroke had landed."""
    ((thread, outcome),) = leash.racing
    thread.join(5)
    assert outcome and not isinstance(outcome[0], Exception), outcome
    names = [entry[:2] for entry in leash.driver.log]
    typed = names.index(("element", "send_keys"))
    moved = names.index(("get", f"{SITE}/elsewhere"))
    assert typed < moved, leash.driver.log


def test_a_navigate_cannot_land_between_a_secrets_check_and_its_keystrokes(leashed):
    """Copilot, #52: the origin read, the leash check and the typing are one
    turn on the browser, or the value is typed on a page nobody checked."""
    shown = secrets.perform_write(
        leashed.catalogue,
        leashed.actions,
        leashed.sessions,
        NAMED,
        {"selector": {"css": "#p"}, "secret": SECRET},
    )
    assert shown["text_from"] == "secret"
    moved_only_after_typing(leashed)


def test_the_same_holds_for_a_secret_typed_by_a_flow_step(leashed):
    step = {"tool": "write", "args": {"selector": {"css": "#p"}, "secret": SECRET}}
    report = flowrun.run(
        leashed.actions,
        {"name": "login", "steps": [step]},
        "abc",
        catalogue=leashed.catalogue,
    )
    assert report["status"] == "ok", report
    moved_only_after_typing(leashed)


def test_a_flow_takes_the_browser_per_step_and_end_browser_stops_it(monkeypatch):
    """Between two steps the browser is free: another call on the session gets
    its turn, and `end_browser` is seen before the next step starts — which
    then never runs."""
    driver = Driver()
    actions, sessions = wired(**{NAMED: ("abc", driver)})
    between = []

    def monotonic():
        # Read once for the deadline, then once before each step: the third
        # read is between step one and step two.
        between.append(None)
        if len(between) == 3:
            for work in (
                lambda: sessions.act(
                    Caller(NAMED), lambda s: actions.navigate(s, "https://other.test/")
                ),
                lambda: sessions.end_browser(Caller(NAMED)),
            ):
                thread, outcome = in_thread(work)
                thread.join(1)
                assert not thread.is_alive(), "waited for a flow between its steps"
                assert not isinstance(outcome[0], Exception), outcome
        return float(len(between))

    monkeypatch.setattr(flowrun, "time", SimpleNamespace(monotonic=monotonic))
    steps = [
        {"tool": "navigate", "args": {"url": "https://one.test/"}},
        {"tool": "navigate", "args": {"url": "https://two.test/"}},
    ]
    report = flowrun.run(actions, {"name": "two", "steps": steps}, "abc")

    assert report["status"] == "failed"
    assert report["steps"][-1]["error"] == "the run was cancelled before this step"
    visited = [entry[1] for entry in driver.log if entry[0] == "get"]
    assert visited == ["https://one.test/", "https://other.test/"]


def test_a_session_nobody_is_driving_holds_nothing():
    """The registry is weak: a hold lives while a call holds or waits for it."""
    actions, sessions = wired(**{NAMED: ("idle", Driver())})
    sessions.act(Caller(NAMED), lambda s: actions.navigate(s, "https://a.test/"))
    assert "idle" not in locks._holds
    locks.interrupt("idle")  # nothing to tell, and nothing is created
    assert "idle" not in locks._holds
