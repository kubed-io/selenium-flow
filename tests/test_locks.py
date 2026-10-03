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
from selenium.common.exceptions import InvalidSessionIdException

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
ENDED = "the browser was ended while this call was waiting"


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
    at once, and the assert lets go, saying its browser was ended, rather than
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

    ended, _ = in_thread(lambda: sessions.end_browser(Caller(NAMED)))
    ended.join(1)
    assert not ended.is_alive(), "end_browser waited for the assert"

    thread.join(5)
    assert not thread.is_alive()
    assert time.monotonic() - started < 5, "the assert ran on after its browser"
    (error,) = outcome
    assert isinstance(error, cancel.Ended)
    assert str(error) == ENDED
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


# ---- what an ended call says ------------------------------------------------


def polling_driver():
    """A driver whose `assert` never comes true, and the event its first poll sets."""
    polling = threading.Event()
    driver = Driver(
        scripts={"return false": False}, on_script=lambda script: polling.set()
    )
    return driver, polling


def end_once_polling(sessions, polling):
    """End ``NAMED``'s browser from another thread once its assert is polling."""

    def end():
        assert polling.wait(5), "the assert never started"
        sessions.end_browser(Caller(NAMED))

    thread = threading.Thread(target=end)
    thread.start()
    return thread


@pytest.fixture
def ended_server(monkeypatch):
    """A real server whose session `NAMED` holds a browser that never says true."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    from .conftest import TOKEN, calling_as

    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )
    driver, polling = polling_driver()
    server.actions.grid = Grid(abc=driver)
    server.sessions.store.set(NAMED, SessionRecord(session_id="abc"))
    calling_as(monkeypatch, NAMED)
    return server, polling, TOKEN


def test_an_ended_call_is_a_dead_browser_to_status_for():
    from kubed.selenium_flow import errors

    assert errors.status_for(cancel.Ended(ENDED)) == 404


def test_over_http_an_assert_whose_browser_was_ended_is_a_404(ended_server):
    """The fix is the dead browser's — open another — so is the status; an
    admin's End click is not a server failure."""
    from starlette.testclient import TestClient

    server, polling, token = ended_server
    client = TestClient(server.mcp.http_app(), headers={"X-Session-Key": NAMED})
    ender = end_once_polling(server.sessions, polling)
    response = client.post(
        "/browser/assert",
        json={"script": "return false", "wait_timeout": 10},
        headers={"Authorization": f"Bearer {token}"},
    )
    ender.join(5)
    assert response.status_code == 404, response.text
    assert response.json()["error"] == ENDED


async def test_over_mcp_an_assert_whose_browser_was_ended_says_so(ended_server):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    server, polling, _ = ended_server
    ender = end_once_polling(server.sessions, polling)
    async with Client(server.mcp) as client:
        with pytest.raises(ToolError, match=ENDED):
            await client.call_tool(
                "assert", {"script": "return false", "wait_timeout": 10}
            )
    ender.join(5)


@pytest.mark.parametrize("how", ["stop", "end_browser"])
def test_a_flow_keeps_its_own_cancellation(how):
    """The run's flags are outermost, so a flow stopped mid-assert — by its
    caller or by its browser being ended — says what a cancelled run always
    has, and the step after never starts."""
    driver, polling = polling_driver()
    actions, sessions = wired(**{NAMED: ("abc", driver)})
    stop = threading.Event()

    def cancel_it():
        assert polling.wait(5), "the assert never started"
        if how == "stop":
            stop.set()
        else:
            sessions.end_browser(Caller(NAMED))

    canceller = threading.Thread(target=cancel_it)
    canceller.start()
    steps = [
        {"tool": "assert", "args": {"script": "return false", "wait_timeout": 10}},
        {"tool": "navigate", "args": {"url": "https://after.test/"}},
    ]
    report = flowrun.run(actions, {"name": "f", "steps": steps}, "abc", stop=stop)
    canceller.join(5)

    assert report["status"] == "failed"
    assert report["steps"][-1]["error"] == cancel.Cancelled.SAYS
    assert len(report["steps"]) == 1
    assert ("get", "https://after.test/") not in [e[:2] for e in driver.log]


# ---- first come, first served -------------------------------------------------


def test_a_call_queued_during_a_flow_step_runs_before_the_next_step():
    """A flow lets go between steps and asks again at once; an unfair lock lets
    it barge back in ahead of a call that queued during the step, which then
    waits the whole run. The queued call goes next."""
    steps = [{"tool": "navigate", "args": {"url": f"https://{n}.test/"}}
             for n in range(1, 11)]
    queued = []

    def during_step_one(url):
        if url != "https://1.test/" or queued:
            return
        queued.append(
            in_thread(
                lambda: sessions.act(
                    Caller(NAMED), lambda s: actions.navigate(s, "https://queued.test/")
                )
            )
        )
        # Until the queued call holds a ticket behind this step's (internals:
        # the one deterministic sign that it is waiting on the lock).
        turns = locks._holds["abc"].lock
        deadline = time.monotonic() + 5
        while turns._next - turns._serving < 2:
            assert time.monotonic() < deadline, "the call never queued"
            time.sleep(0.001)

    driver = Driver(on_get=during_step_one)
    actions, sessions = wired(**{NAMED: ("abc", driver)})
    report = flowrun.run(actions, {"name": "ten", "steps": steps}, "abc")
    ((thread, outcome),) = queued
    thread.join(5)

    assert report["status"] == "ok", report
    assert not isinstance(outcome[0], Exception), outcome
    visited = [entry[1] for entry in driver.log if entry[0] == "get"]
    assert visited[:3] == ["https://1.test/", "https://queued.test/", "https://2.test/"]


def test_a_call_queued_when_end_browser_cuts_in_never_drives_the_browser():
    """Copilot, review 2: `end_browser` interrupts while a call waits its turn.
    The holder lets go while the Grid DELETE is still pending, and the waiter
    takes the lock — it must say its browser was ended, not reconnect and drive
    the browser being ended."""
    holding, release, deleted = threading.Event(), threading.Event(), threading.Event()

    def hold_until_released(url):
        if url == "https://holder.test/":
            holding.set()
            assert release.wait(5), "never released"

    driver = Driver(on_get=hold_until_released)
    actions, sessions = wired(**{NAMED: ("queued", driver)})
    reconnects = []
    grid = actions.grid
    reattach, quit_ = grid.reconnect, grid.quit

    def counted(session_id):
        reconnects.append(session_id)
        return reattach(session_id)

    def pending_delete(session_id):
        assert deleted.wait(5), "the DELETE was never let through"
        quit_(session_id)

    grid.reconnect, grid.quit = counted, pending_delete

    def navigate(url):
        return lambda: sessions.act(Caller(NAMED), lambda s: actions.navigate(s, url))

    holder, _ = in_thread(navigate("https://holder.test/"))
    assert holding.wait(5), "the holder never started"
    waiter, outcome = in_thread(navigate("https://waiter.test/"))
    turns = locks._holds["queued"].lock
    deadline = time.monotonic() + 5
    while turns._next - turns._serving < 2:
        assert time.monotonic() < deadline, "the call never queued"
        time.sleep(0.001)

    ender, _ = in_thread(lambda: sessions.end_browser(Caller(NAMED)))
    deadline = time.monotonic() + 5
    while not locks._holds["queued"].ending.is_set():
        assert time.monotonic() < deadline, "end_browser never interrupted"
        time.sleep(0.001)
    reconnected = len(reconnects)
    release.set()
    holder.join(5)
    waiter.join(5)
    deleted.set()
    ender.join(5)

    (error,) = outcome
    assert isinstance(error, cancel.Ended), error
    assert str(error) == ENDED
    assert ("get", "https://waiter.test/") not in [e[:2] for e in driver.log]
    assert len(reconnects) == reconnected, "the waiter reconnected to the browser"


@pytest.mark.parametrize("when", ["between looks", "during the last look", "mid-look"])
def test_an_assert_with_a_message_says_its_browser_was_ended_not_its_message(when):
    """Found on the live server: an `assert` given a `message` and then ended
    reported the message — a sentence about a page condition, on a page that is
    gone. Whenever the end lands — between two looks, during the look that
    turns out to be the last, or while a look is still on the Grid and fails
    with it — the ending wins."""
    ended = threading.Event()

    def look(script):
        if when == "between looks" or ended.is_set():
            return
        ended.set()
        sessions.end_browser(Caller(NAMED))
        if when == "mid-look":
            raise InvalidSessionIdException("session deleted as the browser closed")

    driver = Driver(scripts={"return false": False}, on_script=look)
    # A browser of its own: an ended hold another test's traceback still keeps
    # alive would end this one before its first look.
    actions, sessions = wired(**{NAMED: (f"message {when}", driver)})
    polling = threading.Event()
    if when == "between looks":
        driver.__dict__["on_script"] = lambda script: polling.set()
        ender = end_once_polling(sessions, polling)
    with pytest.raises(cancel.Ended, match=ENDED):
        sessions.act(
            Caller(NAMED),
            lambda s: actions.assert_(
                s,
                "return false",
                message="the order shipped",
                wait_timeout=0 if when == "during the last look" else 10,
            ),
        )
    if when == "between looks":
        ender.join(5)
    assert ended.is_set() or polling.is_set(), "ended before it ever looked"
