"""A failed wait says why, and what to do about it.

The first agent to fly a real app lost a stretch of its sortie to a link inside
a `display: none` menu: the wait said "no clickable element matched" and timed
out, which is true and is not the diagnosis. The page knew. See saga §F2.8.
"""

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from kubed.selenium_flow import browser, probe

pytestmark = pytest.mark.unit


class _Page:
    """A driver that answers the probe with whatever the page would say."""

    current_url = "https://example.test/tickets"
    title = "Tickets"

    def __init__(self, answer, found=True, raises=None):
        self.answer = answer
        self.found = found
        self.raises = raises
        self.scripts = []

    def find_elements(self, *_):
        return ["<element>"] if self.found else []

    def find_element(self, *_):
        from selenium.common.exceptions import NoSuchElementException

        raise NoSuchElementException("nope")

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if self.raises:
            raise self.raises
        return self.answer


@pytest.mark.parametrize(
    "reason,detail,expected",
    [
        ("hidden", "ul.menu-content", "ul.menu-content is hidden"),
        ("covered", "div#cookie-banner", "div#cookie-banner is on top of it"),
        ("zero_size", "a.link", "has no size"),
        ("offscreen", "button.save", "outside the viewport"),
        ("disabled", "button.submit", "is disabled"),
    ],
)
def test_the_page_is_asked_what_is_wrong(reason, detail, expected):
    driver = _Page({"reason": reason, "detail": detail})
    assert expected in probe.explain(driver, (By.CSS_SELECTOR, "a"))


def test_the_sentence_does_not_argue_with_its_own_advice():
    """A base sentence asserting "a menu that opens on mouse-over looks exactly
    like this" and then advising a click contradicts itself in two consecutive
    clauses. How it opens belongs to the move, which knows (Copilot, #31)."""
    clicky = probe.explain(
        _Page(
            {
                "reason": "hidden",
                "detail": "ul.menu",
                "trigger": "#toggle",
                "gesture": "click",
            }
        ),
        (By.CSS_SELECTOR, "a"),
    )
    assert 'interact(action="click")' in clicky
    assert "mouse-over" not in clicky, "it opens on click; do not muddy it"

    hovery = probe.explain(
        _Page(
            {
                "reason": "hidden",
                "detail": "ul.menu",
                "trigger": "nav#menu",
                "gesture": "hover",
            }
        ),
        (By.CSS_SELECTOR, "a"),
    )
    assert "mouse-over" in hovery, "and where it IS a hover, still say so"
    assert 'interact(action="hover")' in hovery


def test_a_hidden_element_names_what_would_reveal_it():
    """The move, not just the reason — and the move is to hover the *visible*
    thing that opens the menu. Nothing with `display: none` can receive a
    pointer, so advice to hover the hidden node is advice that cannot work
    (Copilot, #29)."""
    driver = _Page(
        {"reason": "hidden", "detail": "ul.menu-content", "trigger": "nav#menu"}
    )
    sentence = probe.explain(driver, (By.CSS_SELECTOR, "a"))
    assert 'interact(action="hover") on nav#menu' in sentence
    assert "ul.menu-content is hidden" in sentence
    assert ":hover" in sentence, "and why a script cannot do it instead"


def test_a_hidden_element_with_nothing_visible_above_it_says_less():
    """No trigger found means no instruction to give: the sentence still names
    what is hidden, and does not invent something to hover."""
    driver = _Page({"reason": "hidden", "detail": "html"})
    sentence = probe.explain(driver, (By.CSS_SELECTOR, "a"))
    assert "html is hidden" in sentence
    assert "hover" in sentence, "the class of problem is still worth naming"
    assert 'interact(action="hover") on' not in sentence


def test_an_offscreen_element_is_told_to_scroll_to_it():
    driver = _Page({"reason": "offscreen", "detail": "button.save"})
    assert 'interact(action="scroll_to")' in probe.explain(driver, (By.CSS_SELECTOR, "a"))


@pytest.mark.parametrize(
    "driver",
    [
        _Page({"reason": None, "detail": "a.link"}),
        _Page({"reason": "hidden"}, found=False),
        _Page({}, raises=RuntimeError("the page went away")),
        _Page(None),
    ],
)
def test_it_says_nothing_rather_than_guessing(driver):
    """No element, a usable one, or a probe that failed: the timeout stands on
    its own. A diagnosis that raises would hide the failure it decorates."""
    assert probe.explain(driver, (By.CSS_SELECTOR, "a")) == ""


def test_a_wait_that_times_out_carries_the_reason():
    """Through the wait, not the helper: this is the only place it shows up."""
    driver = _Page({"reason": "hidden", "detail": "ul.menu-content"})
    with pytest.raises(TimeoutException) as timed_out:
        browser.wait_for_clickable(driver, (By.CSS_SELECTOR, "a.deep"), timeout=0)
    message = str(timed_out.value)
    assert "no clickable element matched 'a.deep'" in message
    assert "https://example.test/tickets" in message, "the page it was on"
    assert "ul.menu-content is hidden" in message, "and why it could not be used"


def test_a_wait_for_presence_says_nothing_extra():
    """`wait_for_element` fails because nothing matched at all, so there is no
    element to ask about — and `hover` and `scroll_to` wait on this one."""
    driver = _Page({"reason": "hidden", "detail": "ul.menu-content"}, found=False)
    with pytest.raises(TimeoutException) as timed_out:
        browser.wait_for_element(driver, (By.CSS_SELECTOR, "a.gone"), timeout=0)
    assert "ul.menu-content" not in str(timed_out.value)


def test_the_failure_and_the_map_read_the_same_answer():
    """The error and `outline` must never disagree about one element, and the
    way to guarantee that is not to test two copies against each other — it is
    to have one copy. Both scripts are built from `_HELPERS`; if someone
    inlines a second `reasonFor`, this fails (saga §F2.8)."""
    from kubed.selenium_flow import js

    helpers = js.read(probe._HELPERS_FILE)
    # The TEXT, not the file name: comparing the constant would pass whatever
    # the constant happened to be, which is exactly what it started doing the
    # day the scripts moved out of Python and it became "helpers.js".
    assert helpers.strip()
    assert helpers in probe.USABLE_JS
    assert helpers in probe.OUTLINE_JS
    assert probe.USABLE_JS.count("reasonFor = ") == 1
    assert probe.OUTLINE_JS.count("reasonFor = ") == 1
