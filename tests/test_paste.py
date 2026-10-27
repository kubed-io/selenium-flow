"""Paste through WebDriver, at the element a person focused (spec 2026-10-27, ruling 34).

The page's own answers are scripted: `FOCUSED` answers in turn, one per
browsing context, and `window !== window.top` answers whether the agent has a
frame selected.
"""

import threading
from dataclasses import replace

import pytest
from selenium.common.exceptions import WebDriverException

from kubed.selenium_flow import faults
from kubed.selenium_flow.core import actions as actions_module
from kubed.selenium_flow.core.actions import (
    CANNOT_TYPE,
    IN_A_FRAME,
    NOTHING_FOCUSED,
    PASTE_CAP,
)
from kubed.selenium_flow.workspace import locks
from kubed.selenium_flow.workspace.store import MemoryStore, Workspace
from kubed.selenium_flow.workspace.workspaces import Workspaces

from .conftest import manager
from .fakes import ScriptedDriver, ScriptedElement

pytestmark = pytest.mark.unit


class FocusedDriver(ScriptedDriver):
    """A driver whose focused element is answered in turn, one per context."""

    def __init__(self, in_frame=False, **kw):
        super().__init__(**kw)
        self.__dict__["answers"] = []
        self.__dict__["in_frame"] = in_frame

    def execute_script(self, script, *args):
        if script == actions_module.FOCUSED:
            self.log.append(("focused",))
            return self.answers.pop(0) if self.answers else None
        if "window.top" in script:
            self.log.append(("in_frame",))
            return self.in_frame
        return super().execute_script(script, *args)


def element(log, tag="textarea"):
    found = ScriptedElement(log)
    found.tag = tag
    return found


@pytest.fixture
def paste(actions, monkeypatch):
    """``paste(driver, text)``: the action over a driver, as the Grid would give it."""

    def use(driver, text):
        monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: driver)
        return actions.paste("abc", text)

    return use


def calls(driver, *kinds):
    return [entry for entry in driver.log if entry[:1] == kinds or entry[:2] in kinds]


def test_it_types_at_the_focused_element_without_clearing(paste):
    driver = FocusedDriver()
    driver.answers[:] = [element(driver.log)]
    paste(driver, "héllo \u2013 日本 ✓")
    assert ("element", "send_keys", "héllo \u2013 日本 ✓") in driver.log
    assert ("element", "clear") not in driver.log
    assert not [e for e in driver.log if e[:1] == ("switch_to",)]


def test_it_descends_into_a_focused_iframe_and_returns_to_the_top(paste):
    driver = FocusedDriver()
    frame, field = element(driver.log, "iframe"), element(driver.log, "textarea")
    driver.answers[:] = [frame, field]
    paste(driver, "hello")
    switches = [e for e in driver.log if e[:1] == ("switch_to",)]
    assert switches == [
        ("switch_to", "frame", frame),
        ("switch_to", "default_content"),
    ]
    order = [e for e in driver.log if e[0] in ("switch_to", "element")]
    assert order.index(("element", "send_keys", "hello")) < order.index(
        ("switch_to", "default_content")
    )


def test_it_returns_to_the_top_when_typing_fails_inside_a_frame(paste):
    driver = FocusedDriver()
    frame, field = element(driver.log, "iframe"), element(driver.log, "textarea")
    field.fail_on = {"send_keys": WebDriverException("boom")}
    driver.answers[:] = [frame, field]
    with pytest.raises(WebDriverException):
        paste(driver, "hello")
    assert driver.log.count(("switch_to", "default_content")) == 1


def test_it_gives_up_on_frames_nested_too_deep(paste):
    driver = FocusedDriver()
    driver.answers[:] = [element(driver.log, "iframe") for _ in range(50)]
    with pytest.raises(ValueError, match="frames"):
        paste(driver, "x")
    assert len([e for e in driver.log if e[:2] == ("switch_to", "frame")]) <= 10
    assert ("switch_to", "default_content") in driver.log


def test_it_refuses_while_the_agent_is_in_a_frame_and_switches_nothing(paste):
    driver = FocusedDriver(in_frame=True)
    driver.answers[:] = [element(driver.log)]
    with pytest.raises(ValueError) as refused:
        paste(driver, "hello")
    assert str(refused.value) == IN_A_FRAME
    assert not [e for e in driver.log if e[:1] == ("switch_to",)]
    assert ("element", "send_keys", "hello") not in driver.log


def test_nothing_focused_is_refused(paste):
    driver = FocusedDriver()
    with pytest.raises(ValueError) as refused:
        paste(driver, "hello")
    assert str(refused.value) == NOTHING_FOCUSED


def test_a_driver_that_cannot_type_a_character_says_so(paste):
    driver = FocusedDriver()
    field = element(driver.log)
    field.fail_on = {
        "send_keys": WebDriverException(
            "unknown error: ChromeDriver only supports characters in the BMP"
        )
    }
    driver.answers[:] = [field]
    with pytest.raises(ValueError) as refused:
        paste(driver, "😀")
    assert str(refused.value) == CANNOT_TYPE


def test_another_driver_failure_is_not_taken_for_a_character_problem(paste):
    driver = FocusedDriver()
    field = element(driver.log)
    field.fail_on = {"send_keys": WebDriverException("session deleted")}
    driver.answers[:] = [field]
    with pytest.raises(WebDriverException):
        paste(driver, "hello")


def test_it_is_capped_at_ten_thousand_characters(paste):
    driver = FocusedDriver()
    driver.answers[:] = [element(driver.log)]
    answer = paste(driver, "a" * (PASTE_CAP + 500))
    assert ("element", "send_keys", "a" * PASTE_CAP) in driver.log
    assert answer["typed"] == PASTE_CAP


@pytest.mark.parametrize(
    "text, typed",
    [
        ("hello\n", "hello"),
        ("hello\r\n", "hello"),
        ("hello\n\n", "hello\n"),
        ("one\ntwo\r\nthree\n", "one\ntwo\r\nthree"),
        ("hello\r", "hello\r"),
        # Dropped before the cap: what was sent ends in the newline.
        ("a" * (PASTE_CAP - 1) + "\n", "a" * (PASTE_CAP - 1)),
        ("a" * (PASTE_CAP - 1) + "\n\n", "a" * (PASTE_CAP - 1) + "\n"),
    ],
    ids=["lf", "crlf", "only-one", "inside", "lone-cr", "at-the-cap", "over-the-cap"],
)
def test_one_trailing_newline_is_dropped_and_the_rest_types(paste, text, typed):
    driver = FocusedDriver()
    driver.answers[:] = [element(driver.log)]
    answer = paste(driver, text)
    assert calls(driver, ("element", "send_keys")) == [("element", "send_keys", typed)]
    assert answer["typed"] == len(typed)


def test_a_lone_newline_types_nothing(paste):
    driver = FocusedDriver()
    driver.answers[:] = [element(driver.log)]
    assert paste(driver, "\n")["typed"] == 0
    assert not calls(driver, ("element", "send_keys"))


def test_the_answer_is_a_count_and_never_the_text(paste):
    driver = FocusedDriver()
    driver.answers[:] = [element(driver.log)]
    answer = paste(driver, "hunter2")
    assert answer["typed"] == 7
    assert "hunter2" not in repr(answer) and "value" not in answer


# ---- the workspace half ------------------------------------------------------


@pytest.fixture
def held(actions, monkeypatch):
    """A `Workspaces` over the real action, with ``w1`` holding a live browser."""
    driver = FocusedDriver()
    driver.answers[:] = [element(driver.log)]
    monkeypatch.setattr(actions.grid, "is_alive", lambda session_id: True)
    monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: driver)
    workspaces = Workspaces(actions, MemoryStore())
    workspaces.store.set("w1", Workspace(session_id="abc"))
    return workspaces, driver


def test_the_workspace_answers_a_count_only(held):
    workspaces, driver = held
    assert workspaces.paste("w1", "hunter2") == {"typed": 7}
    assert ("element", "send_keys", "hunter2") in driver.log


def test_a_paste_is_not_recorded_in_the_workspace(held):
    workspaces, _ = held
    workspaces.paste("w1", "hunter2")
    record = workspaces.store.get("w1")
    assert "hunter2" not in repr(record)
    assert not record.history


def test_no_browser_is_404_and_nothing_is_reopened():
    workspaces = manager()
    workspaces.store.set("w1", Workspace(session_id="gone"))
    with pytest.raises(faults.NotFound) as refused:
        workspaces.paste("w1", "hello")
    assert str(refused.value) == "this workspace has no browser open"
    assert workspaces.actions.opened == 0


def test_a_paste_waits_for_a_call_in_flight(held):
    workspaces, driver = held
    entered, release, done = threading.Event(), threading.Event(), threading.Event()

    def agent():
        with locks.driving("abc"):
            entered.set()
            release.wait(5)

    def person():
        workspaces.paste("w1", "hello")
        done.set()

    holder = threading.Thread(target=agent)
    holder.start()
    assert entered.wait(5)
    pasting = threading.Thread(target=person)
    pasting.start()
    assert not done.wait(0.3), "the paste did not wait for the agent's call"
    assert ("element", "send_keys", "hello") not in driver.log
    release.set()
    assert done.wait(5)
    holder.join()
    pasting.join()
    assert ("element", "send_keys", "hello") in driver.log


def test_a_paste_leaves_a_reopen_report_for_the_agent(held):
    workspaces, _ = held
    report = {"reopened": True}
    workspaces.store.update(
        "w1",
        lambda r: replace(r, reopened={"browser": "abc", "report": report}),
    )
    workspaces.paste("w1", "hello")
    assert workspaces.store.get("w1").reopened == {"browser": "abc", "report": report}
    assert workspaces.touch("w1", browser="abc") == report


def test_a_paste_takes_the_sessions_turn_before_the_call_begins(held, monkeypatch):
    workspaces, _ = held
    order = []
    # Every call takes its turn at the host, capture or not (live control, ruling 33).
    monkeypatch.setattr(
        workspaces.calls, "begin", lambda *a, **k: order.append("begin") or object()
    )
    monkeypatch.setattr(
        workspaces.calls, "end", lambda *a, **k: order.append("end") or None
    )
    entered, release = threading.Event(), threading.Event()

    def agent():
        with locks.driving("abc"):
            entered.set()
            release.wait(5)

    holder = threading.Thread(target=agent)
    holder.start()
    assert entered.wait(5)
    pasting = threading.Thread(target=workspaces.paste, args=("w1", "hello"))
    pasting.start()
    pasting.join(0.3)
    assert order == [], "the call began while the agent's call was in flight"
    release.set()
    pasting.join(5)
    holder.join()
    assert order == ["begin", "end"]
