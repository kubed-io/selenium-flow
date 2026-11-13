"""A failed wait says why, and what to do about it.

The first agent to fly a real app lost a stretch of its sortie to a link inside
a `display: none` menu: the wait said "no clickable element matched" and timed
out, which is true and is not the diagnosis. The page knew. See saga §F2.8.
"""

import pytest
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

from kubed.selenium_flow.core import browser, probe

pytestmark = pytest.mark.unit


class _Hidden:
    """What the page found: there, and not usable. The wait asks it."""

    def is_displayed(self):
        return False

    def is_enabled(self):
        return False


class _Page:
    """A driver that answers the probe with whatever the page would say."""

    current_url = "https://example.test/tickets"
    title = "Tickets"

    def __init__(self, answer, found=True, raises=None, count=1):
        self.answer = answer
        self.found = found
        self.raises = raises
        self.count = count
        self.scripts = []
        self.args = []

    def find_elements(self, *_):
        return [_Hidden() for _ in range(self.count)] if self.found else []

    def find_element(self, *_):
        from selenium.common.exceptions import NoSuchElementException

        raise NoSuchElementException("nope")

    def execute_script(self, script, *args):
        self.scripts.append(script)
        self.args.append(args)
        if self.raises:
            raise self.raises
        if script is probe.USABLES_JS:
            return [self.answer] * len(args[0])
        return self.answer


@pytest.mark.parametrize(
    "reason,extra,expected",
    [
        ("hidden", {"detail": "ul.menu-content"}, "ul.menu-content is hidden"),
        ("covered", {"detail": "div#modal"}, "div#modal is on top of it at its centre"),
        ("covered", {"detail": "div#cookie-banner", "overlay": True},
         "like a cookie banner or an overlay"),
        ("unpainted", {"detail": "rect.frame-background"},
         "paints nothing at its centre, so the click goes through to rect.frame-background"),
        ("invisible_cover", {"detail": "rect#hot", "selector": {"css": "#hot"}},
         'It may be the real target: try selector={"css": "#hot"}'),
        ("zero_size", {"detail": "a.link"}, "has no size"),
        ("offscreen", {"detail": "button.save"}, "outside the viewport"),
        ("disabled", {"detail": "button.submit"}, "is disabled"),
    ],
)
def test_the_page_is_asked_what_is_wrong(reason, extra, expected):
    driver = _Page({"reason": reason, **extra})
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


T = (By.CSS_SELECTOR, "a")


def test_a_plain_cover_is_not_called_a_cookie_banner():          # brief 2
    assert "cookie" not in probe.explain(_Page({"reason": "covered", "detail": "rect.bg"}), T)


def test_a_hidden_element_with_nothing_visible_above_it_says_less():   # ruling 12
    assert probe.explain(_Page({"reason": "hidden", "detail": "html"}), T) == "It exists, but html is hidden."


def test_an_unread_stylesheet_is_admitted_not_hidden():              # ruling 12, Dr K's wording
    page = _Page({"reason": "hidden", "detail": "ul.menu", "trigger": None, "unread": True})
    assert probe.explain(page, T) == (
        "It exists, but ul.menu is hidden. A stylesheet on this page couldn't be read, "
        "so if ul.menu opens on mouse-over, hover what opens it.")


def test_a_found_trigger_outranks_an_unread_sheet():
    page = _Page({"reason": "hidden", "detail": "ul", "trigger": "#dd", "gesture": "hover", "unread": True})
    assert "couldn't be read" not in probe.explain(page, T)


def test_nothing_matching_says_so():                               # ruling 8
    assert probe.explain(_Page({}, found=False), T) == "Nothing on the page matches it."


def test_several_matches_are_listed_with_why():                    # ruling 1, brief 1
    page = _Page({"reason": "hidden", "detail": "section.thumbs"}, count=2)
    assert probe.explain(page, T) == (
        "2 elements match it, and none could be used: "
        "[0] section.thumbs is hidden; [1] section.thumbs is hidden.")


def test_more_than_listed_says_how_many_more():
    page = _Page({"reason": "offscreen", "detail": "a"}, count=7)
    assert probe.explain(page, T).endswith("[4] it is outside the viewport; and 2 more.")


def test_more_than_candidates_says_to_narrow():                    # Review Focus 2
    page = _Page({"reason": "hidden", "detail": "li"}, count=probe.CANDIDATES + 10)
    assert "Only the first 50 are tried; narrow the selector." in probe.explain(page, T)


def test_the_list_carries_the_first_move():
    page = _Page({"reason": "hidden", "detail": "ul", "trigger": "#toggle", "gesture": "click"}, count=2)
    assert " [0]: #toggle opens it and says so with aria-expanded" in probe.explain(page, T)


def test_explain_asks_about_the_element_it_is_given():             # ruling 9, brief 4
    page = _Page({"reason": "unpainted", "detail": "rect.bg"})
    page.find_elements = lambda *_: pytest.fail("re-queried")
    assert "rect.bg" in probe.explain(page, T, element="<clicked>")
    assert page.args[-1] == ("<clicked>",)


def test_a_wait_with_nothing_matching_says_so():
    with pytest.raises(TimeoutException) as timed_out:
        browser.wait_for_clickable(_Page({}, found=False), T, timeout=0)
    assert "Nothing on the page matches it." in str(timed_out.value)


def test_an_offscreen_element_is_told_to_scroll_to_it():
    driver = _Page({"reason": "offscreen", "detail": "button.save"})
    assert 'interact(action="scroll_to")' in probe.explain(driver, (By.CSS_SELECTOR, "a"))


@pytest.mark.parametrize(
    "driver",
    [
        _Page({"reason": None, "detail": "a.link"}),
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
    from kubed.selenium_flow.core import js

    helpers = js.read(probe._HELPERS_FILE)
    # The TEXT, not the file name: comparing the constant would pass whatever
    # the constant happened to be, which is exactly what it started doing the
    # day the scripts moved out of Python and it became "helpers.js".
    assert helpers.strip()
    assert helpers in probe.USABLE_JS
    assert helpers in probe.OUTLINE_JS
    assert probe.USABLE_JS.count("reasonFor = ") == 1
    assert probe.OUTLINE_JS.count("reasonFor = ") == 1


def test_a_short_answer_list_still_counts_what_was_asked():
    page = _Page({"reason": "offscreen", "detail": "a"}, count=7)
    full = page.execute_script
    page.execute_script = lambda script, *args: full(script, *args)[:2] if script is probe.USABLES_JS else full(script, *args)
    text = probe.explain(page, T)
    assert "[1] it is outside the viewport; [2] the page reports nothing wrong with it" in text
    assert "[4] the page reports nothing wrong with it; and 2 more." in text
    assert text.endswith("and 2 more.")


def test_a_none_entry_does_not_lose_the_others():
    page = _Page({"reason": "offscreen", "detail": "a"}, count=3)
    page.execute_script = lambda script, *a: [None, {"reason": "disabled"}, "junk"]
    assert probe.explain(page, T) == (
        "3 elements match it, and none could be used: "
        "[0] the page reports nothing wrong with it; [1] it is disabled; "
        "[2] the page reports nothing wrong with it.")


@pytest.mark.parametrize("answer", [None, {"reason": None}, "x", {}])
def test_short_takes_any_answer(answer):
    assert probe.short(answer) == "the page reports nothing wrong with it"
    assert probe.move_for(answer) == ""
