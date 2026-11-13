"""Which match an action uses when a selector finds several (spec 2026-11-13)."""

import pytest
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By

from kubed.selenium_flow.core import browser, probe

from .fakes import ScriptedElement
from .test_actions_contract import pointer_lands  # noqa: F401

pytestmark = pytest.mark.unit

T = (By.CSS_SELECTOR, "#x")


class _El:
    def __init__(self, log, name, displayed=True, enabled=True, stale=False):
        self.log, self.name = log, name
        self.displayed, self.enabled, self.stale = displayed, enabled, stale

    def _ask(self, what, flag):
        self.log.append((self.name, what))
        if self.stale:
            raise StaleElementReferenceException("gone")
        return flag

    def is_displayed(self):
        return self._ask("is_displayed", self.displayed)

    def is_enabled(self):
        return self._ask("is_enabled", self.enabled)


class _Driver:
    current_url = "https://example.test/"
    title = "Example"

    def __init__(self, elements):
        self.elements = elements

    def find_elements(self, *_):
        return self.elements


def test_the_first_usable_match_is_used():  # ruling 1
    log = []
    a, b = _El(log, "a", displayed=False), _El(log, "b")
    assert browser.wait_for_clickable(_Driver([a, b]), T, timeout=0) == browser.Found(b, 1, 2)


def test_one_match_is_asked_what_it_is_asked_today():  # ruling 5
    log = []
    a = _El(log, "a")
    assert browser.wait_for_clickable(_Driver([a]), T, timeout=0) == browser.Found(a, 0, 1)
    assert log == [("a", "is_displayed"), ("a", "is_enabled")]


def test_a_disabled_first_match_is_passed_over():
    log = []
    a, b = _El(log, "a", enabled=False), _El(log, "b")
    assert browser.wait_for_clickable(_Driver([a, b]), T, timeout=0).index == 1


def test_a_stale_match_is_skipped_not_raised():  # Review Focus 1
    log = []
    a, b = _El(log, "a", stale=True), _El(log, "b")
    assert browser.wait_for_clickable(_Driver([a, b]), T, timeout=0).index == 1


def test_both_visible_takes_the_first_and_asks_nothing_of_the_second():  # Review Focus 3
    log = []
    a, b = _El(log, "a"), _El(log, "b")
    assert browser.wait_for_clickable(_Driver([a, b]), T, timeout=0) == browser.Found(a, 0, 2)
    assert not [e for e in log if e[0] == "b"]


def test_only_the_first_candidates_are_tried():  # ruling 6, Review Focus 2
    log = []
    hidden = [_El(log, str(i), displayed=False) for i in range(probe.CANDIDATES + 10)]
    with pytest.raises(TimeoutException):
        browser.wait_for_clickable(_Driver([*hidden, _El(log, "last")]), T, timeout=0)
    assert {e[0] for e in log} == {str(i) for i in range(probe.CANDIDATES)}


def test_shown_prefers_the_first_displayed():  # ruling 3
    log = []
    a, b = _El(log, "a", displayed=False), _El(log, "b")
    assert browser.wait_for_shown(_Driver([a, b]), T, timeout=0) == browser.Found(b, 1, 2)


def test_shown_falls_back_to_the_first():
    log = []
    a, b = _El(log, "a", displayed=False), _El(log, "b", displayed=False)
    assert browser.wait_for_shown(_Driver([a, b]), T, timeout=0) == browser.Found(a, 0, 2)


def test_shown_with_one_match_asks_nothing_more():  # ruling 5
    log = []
    a = _El(log, "a", displayed=False)
    assert browser.wait_for_shown(_Driver([a]), T, timeout=0) == browser.Found(a, 0, 1)
    assert log == []


def test_found_reports_only_a_choice():  # ruling 7
    assert browser.Found("e", 1, 2).fields() == {"matched": 2, "index": 1}
    assert browser.Found("e", 0, 1).fields() == {}


def two_matches(driver):
    class Hidden(ScriptedElement):
        def is_displayed(self):
            self._do("is_displayed")
            return False

    hidden = Hidden(driver.log)
    driver.__dict__["find_elements"] = lambda *a, **k: [hidden, driver.element]
    return driver


@pytest.mark.parametrize("call", [
    lambda a: a.interact("abc", "click", selector={"css": "#x"}),
    lambda a: a.interact("abc", "hover", selector={"css": "#x"}),
    lambda a: a.write("abc", "hi", selector={"css": "#x"}),
    lambda a: a.press_key("abc", "Tab", selector={"css": "#x"}),
])
def test_an_action_says_which_match_it_used(actions, scripted, pointer_lands, call):  # noqa: F811
    two_matches(scripted())
    result = call(actions)
    assert (result["matched"], result["index"]) == (2, 1)


def test_one_match_adds_nothing_to_the_result(actions, scripted, pointer_lands):  # noqa: F811
    scripted()
    assert "matched" not in actions.interact("abc", "click", selector={"css": "#x"})
