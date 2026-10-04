"""What the browser layer promises that nothing else pinned.

Every other test stubs `act` or `_at`, so the behaviour *around* the driver —
what each action reports, what it refuses before touching the browser, what it
restores in a `finally` — could change without a test noticing. These go through
the real `Actions` against a `ScriptedDriver` that remembers every call, so a
refactor of the recipe every action shares (reconnect, maybe navigate, wait, act,
report) is provable rather than hoped for.
"""

import tempfile
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import wait as selenium_wait

from kubed.selenium_flow.core import browser, pointer
from kubed.selenium_flow.core.browser import NAVIGATION_SETTLE
from kubed.selenium_flow.errors import status_for

from .fakes import CountingDriver, ScriptedElement

pytestmark = pytest.mark.unit

PAGE = "https://example.test/"
BOTH = {"css": "a", "xpath": "//a"}


@pytest.fixture
def pointer_lands(monkeypatch):
    """`pointer.move` answers as if the pointer arrived, so a gesture can run
    against the scripted driver without a browser's geometry."""
    monkeypatch.setattr(
        pointer,
        "move",
        lambda *a, **k: {
            "at": (10.0, 10.0), "glided": False, "nudged": False,
            "unknown_start": False,
            # A move reports the window's size it read on landing (Task 19).
            "viewport": (1000, 800),
        },
    )


# ---- X1: a dialog is state, never an error ----------------------------------

DIALOG = "Are you sure?"

EVERY_ACTION = {
    "navigate": lambda a: a.navigate("abc", PAGE),
    "interact": lambda a: a.interact("abc", "click", selector={"css": "#go"}),
    "write": lambda a: a.write("abc", "hi", selector={"css": "#q"}),
    "press_key": lambda a: a.press_key("abc", "Tab"),
    "extract": lambda a: a.extract("abc", selector={"css": "#x"}),
    "execute_script": lambda a: a.execute_script("abc", "return 1"),
    "assert": lambda a: a.assert_("abc", "return true", wait_timeout=0),
    "resize": lambda a: a.resize("abc", width=500),
    "frame": lambda a: a.frame("abc", "default"),
    "dialog": lambda a: a.dialog("abc", "read"),
    "screenshot": lambda a: a.screenshot("abc", save=False),
}


@pytest.mark.parametrize("name", sorted(EVERY_ACTION))
def test_an_open_dialog_is_reported_as_page_state_and_never_raised(
    actions, scripted, pointer_lands, name
):
    """The click landed and opened a prompt: failing the action for that would
    report a success as a failure, and the caller learns from `dialog` what to do."""
    scripted(dialog=DIALOG, scripts={"readyState": "complete", "return true": True})
    result = EVERY_ACTION[name](actions)
    assert result["url"] is None and result["title"] is None
    assert result["dialog"] == DIALOG
    assert "answer it with dialog" in result["hint"]


def test_a_page_with_no_dialog_has_no_dialog_keys(actions, scripted):
    scripted()
    result = actions.navigate("abc", PAGE)
    assert result == {"url": PAGE, "title": "Example"}


# ---- K6: settling is allowed to expire ---------------------------------------


@pytest.fixture
def clock(monkeypatch):
    """Selenium's wait loop on a clock that only moves when it sleeps."""
    now = [0.0]
    monkeypatch.setattr(
        selenium_wait,
        "time",
        SimpleNamespace(
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        ),
    )
    return now


def test_settling_on_a_page_that_never_navigates_expires_quietly(scripted, clock):
    """A form handled in JavaScript never goes stale, and that is the common
    case for Enter: expiring after NAVIGATION_SETTLE is an answer, not a fault."""
    driver = scripted()
    assert browser.settled(driver, driver.element) is None
    assert NAVIGATION_SETTLE <= clock[0] < NAVIGATION_SETTLE + 0.1
    assert not any("readyState" in str(e) for e in driver.log)


def test_settling_waits_for_the_new_document_once_the_old_one_is_gone(scripted, clock):
    driver = scripted(scripts={"readyState": "complete"})
    driver.element.fail_on["is_enabled"] = StaleElementReferenceException()
    browser.settled(driver, driver.element)
    assert any("readyState" in str(e) for e in driver.log)
    assert clock[0] < NAVIGATION_SETTLE


def test_settling_survives_the_session_going_away_mid_wait(scripted, clock):
    driver = scripted()
    driver.element.fail_on["is_enabled"] = RuntimeError("session gone")
    assert browser.settled(driver, driver.element) is None


# ---- X3, N1-N3: what navigation and reads return -----------------------------


def test_navigate_always_goes_even_when_it_is_already_there(actions, scripted):
    driver = scripted(url=PAGE)
    result = actions.navigate("abc", PAGE)
    assert ("get", PAGE) in driver.log
    assert result == {"url": PAGE, "title": "Example"}


def test_a_named_url_the_browser_is_already_on_is_not_navigated_to(actions, scripted):
    """Fragment and trailing slash do not make a different page."""
    driver = scripted(url="https://example.test/docs")
    actions.extract("abc", selector={"css": "#x"}, url="https://example.test/docs/#top")
    assert not [e for e in driver.log if e[0] == "get"]


def test_a_named_url_somewhere_else_is_navigated_to_before_the_act(actions, scripted):
    driver = scripted(url=PAGE)
    actions.extract("abc", selector={"css": "#x"}, url="https://other.test/")
    names = driver.names()
    assert ("get", "https://other.test/") in driver.log
    assert names.index("get") < names.index("find_element")


# ---- Ruling 4: a caller opens the web, and nothing else ---------------------

REFUSED_URLS = {
    "file:///etc/passwd": "file",
    "chrome://settings": "chrome",
    "view-source:https://example.test/": "view-source",
    "javascript:alert(1)": "javascript",
    "data:text/html,<b>hi</b>": "data",
}


@pytest.fixture
def untouched(actions, monkeypatch):
    """A browser that fails the test if it is reached: a refused URL is a 400
    about the input, and costs no reattach."""

    def reached(*_):
        raise AssertionError("the browser was reached")

    monkeypatch.setattr(actions.grid, "reconnect", reached)


@pytest.mark.parametrize("url", sorted(REFUSED_URLS))
def test_navigate_refuses_every_scheme_but_the_webs(actions, untouched, url):
    with pytest.raises(ValueError) as refused:
        actions.navigate("abc", url)
    assert str(refused.value) == (
        f"only http(s) URLs can be opened here; {REFUSED_URLS[url]}: cannot"
    )
    assert status_for(refused.value) == 400


@pytest.mark.parametrize("url", ["about:blank", "http://example.test/", PAGE])
def test_navigate_opens_the_web_and_a_blank_page(actions, scripted, url):
    driver = scripted()
    actions.navigate("abc", url)
    assert ("get", url) in driver.log


URL_TAKERS = {
    "extract": lambda a, url: a.extract("abc", selector={"css": "#x"}, url=url),
    "interact": lambda a, url: a.interact(
        "abc", "click", selector={"css": "#go"}, url=url
    ),
    "upload_file": lambda a, url: a.upload_file(
        "abc", selector={"css": "input"}, text="hi", url=url
    ),
    "execute_script": lambda a, url: a.execute_script("abc", "return 1", url=url),
}


@pytest.mark.parametrize("url", sorted(REFUSED_URLS))
@pytest.mark.parametrize("name", sorted(URL_TAKERS))
def test_every_url_argument_refuses_the_same_schemes(actions, untouched, name, url):
    with pytest.raises(ValueError) as refused:
        URL_TAKERS[name](actions, url)
    assert str(refused.value).startswith("only http(s) URLs can be opened here")
    assert status_for(refused.value) == 400


def test_extract_returns_html_text_and_page_state_and_nothing_else(actions, scripted):
    driver = scripted()
    driver.element.text, driver.element.html = "hello", "<i>hello</i>"
    result = actions.extract("abc", selector={"css": "#x"})
    assert result == {
        "html": "<i>hello</i>", "text": "hello", "url": PAGE, "title": "Example",
    }


def test_extract_waits_for_presence_not_for_clickability(actions, scripted):
    driver = scripted()
    actions.extract("abc", selector={"css": "#x"})
    assert ("element", "is_displayed") not in driver.log


def test_execute_script_returns_whatever_the_page_answered_unshaped(actions, scripted):
    answer = {"a": [1, None, "two"], "b": float("nan") != 0}
    scripted(scripts={"return": answer})
    result = actions.execute_script("abc", "return x")
    assert result == {"result": answer, "url": PAGE, "title": "Example"}


def test_execute_script_with_no_answer_reports_none_not_a_missing_key(actions, scripted):
    scripted()
    assert actions.execute_script("abc", "x()")["result"] is None


# ---- I2: a refused argument costs the browser nothing ------------------------

REFUSED = {
    "interact: unknown gesture": lambda a: a.interact(
        "abc", "bogus", selector={"css": "a"}, url=PAGE
    ),
    "interact: selector names both": lambda a: a.interact(
        "abc", "click", selector=BOTH, url=PAGE
    ),
    "interact: no selector": lambda a: a.interact("abc", "click", url=PAGE),
    "drag: no destination": lambda a: a.drag(
        "abc", selector={"css": "a"}, url=PAGE
    ),
    "write: selector names both": lambda a: a.write(
        "abc", "x", selector=BOTH, url=PAGE
    ),
    "press_key: unknown key": lambda a: a.press_key("abc", "Hyperspace", url=PAGE),
    "press_key: selector names both": lambda a: a.press_key(
        "abc", "Tab", selector=BOTH, url=PAGE
    ),
    "assert: hold leaves no room": lambda a: a.assert_(
        "abc", "return true", wait_timeout=5, stable_for=50, url=PAGE
    ),
    "dialog: send_text without text": lambda a: a.dialog("abc", "send_text"),
    "dialog: unknown action": lambda a: a.dialog("abc", "bogus"),
    "print: unknown format": lambda a: a.print_("abc", format="docx", url=PAGE),
    "frame: unknown action": lambda a: a.frame("abc", "bogus"),
    "frame: switch names no frame": lambda a: a.frame("abc", "switch"),
    "extract: selector names both": lambda a: a.extract(
        "abc", selector=BOTH, url=PAGE
    ),
    "frame: selector names both": lambda a: a.frame(
        "abc", "switch", selector=BOTH
    ),
    "screenshot: selector names both": lambda a: a.screenshot(
        "abc", selector=BOTH, url=PAGE
    ),
    "outline: selector names both": lambda a: a.outline(
        "abc", selector=BOTH, url=PAGE
    ),
    "upload_file: selector names both": lambda a: a.upload_file(
        "abc", selector=BOTH, text="x", url=PAGE
    ),
}


def _cases():
    cases = []
    for name, case in REFUSED.items():
        if hasattr(case, "values"):  # already a pytest.param
            cases.append(pytest.param(*case.values, marks=case.marks, id=name))
        else:
            cases.append(pytest.param(case, id=name))
    return cases


@pytest.mark.parametrize("refuse", _cases())
def test_a_refused_argument_is_refused_before_the_browser_is_touched(
    actions, scripted, monkeypatch, refuse
):
    """`_at` reconnects and may navigate, so an impossible request that is
    refused after it has already moved the caller's browser."""
    driver = scripted()
    touched = []
    monkeypatch.setattr(
        actions.grid,
        "reconnect",
        lambda session_id: touched.append(session_id) or driver,
    )
    with pytest.raises(ValueError):
        refuse(actions)
    assert touched == []


# ---- R2: every action takes the same road, in the same order ----------------

OTHER = "https://other.test/"
AT = {"css": "#x"}

# Each action's whole sequence on its success path, reattach to page state:
# what `core/recipe.py` must leave exactly as it was. A navigation is asked for
# wherever an action takes `url`, so the step that may navigate is on the record.
ROADS = {
    "navigate": (
        {}, lambda a: a.navigate("abc", OTHER),
        ["reconnect", "get", "current_url", "title"],
    ),
    "interact click": (
        {}, lambda a: a.interact("abc", "click", selector=AT, url=OTHER),
        ["reconnect", "current_url", "get", "find_element", "element.is_displayed",
         "element.is_enabled", "element.click", "current_url", "title"],
    ),
    "interact hover": (
        {}, lambda a: a.interact("abc", "hover", selector=AT),
        ["reconnect", "find_element", "current_url", "title"],
    ),
    "drag": (
        {"scripts": {"innerWidth": [1000, 800]}},
        lambda a: a.drag("abc", selector=AT, by_x=5, url=OTHER),
        # Task 19: no `execute_script` between the wait and the drop. The
        # window's size comes back with the approach's landing (center.js),
        # so the separate viewport read is gone.
        ["reconnect", "current_url", "get", "find_element", "element.is_displayed",
         "element.is_enabled", "execute", "current_url", "title"],
    ),
    "frame switch": (
        {}, lambda a: a.frame("abc", "switch", selector=AT),
        ["reconnect", "find_element", "switch_to.frame", "execute_script",
         "current_url", "title"],
    ),
    "frame default": (
        {}, lambda a: a.frame("abc", "default"),
        ["reconnect", "switch_to.default_content", "execute_script", "current_url",
         "title"],
    ),
    "resize": (
        {}, lambda a: a.resize("abc", width=500),
        ["reconnect", "get_window_size", "set_window_size", "get_window_size",
         "current_url", "title"],
    ),
    "dialog": (
        {"dialog": DIALOG}, lambda a: a.dialog("abc", "accept"),
        ["reconnect", "switch_to.alert", "alert.text", "alert.accept", "current_url",
         "title"],
    ),
    "upload_file": (
        {}, lambda a: a.upload_file("abc", selector=AT, text="hi", url=OTHER),
        ["reconnect", "current_url", "get", "set.file_detector", "find_element",
         "element.send_keys", "current_url", "title"],
    ),
    "write": (
        {}, lambda a: a.write("abc", "hi", selector=AT, url=OTHER),
        ["reconnect", "current_url", "get", "find_element", "element.is_displayed",
         "element.is_enabled", "element.clear", "element.send_keys",
         "element.get_attribute", "current_url", "title"],
    ),
    "press_key at an element": (
        {}, lambda a: a.press_key("abc", "Tab", selector=AT, url=OTHER),
        ["reconnect", "current_url", "get", "find_element", "element.is_displayed",
         "element.is_enabled", "element.send_keys", "current_url", "title"],
    ),
    "press_key wherever focus is": (
        {}, lambda a: a.press_key("abc", "Tab"),
        ["reconnect", "find_element", "element.send_keys", "current_url", "title"],
    ),
    "outline": (
        {"scripts": {"": {"elements": [], "total": 0}}},
        lambda a: a.outline("abc", selector=AT, url=OTHER),
        ["reconnect", "current_url", "get", "find_element", "execute_script",
         "current_url", "title"],
    ),
    "execute_script": (
        {}, lambda a: a.execute_script("abc", "return 1", url=OTHER),
        ["reconnect", "current_url", "get", "execute_script", "current_url", "title"],
    ),
    "assert": (
        {"scripts": {"return true": True}},
        lambda a: a.assert_("abc", "return true", wait_timeout=0, url=OTHER),
        ["reconnect", "current_url", "get", "execute_script", "current_url", "title"],
    ),
    "extract": (
        {}, lambda a: a.extract("abc", selector=AT, url=OTHER),
        ["reconnect", "current_url", "get", "find_element", "element.get_attribute",
         "current_url", "title"],
    ),
    "screenshot": (
        {}, lambda a: a.screenshot("abc", selector=AT, width=400, url=OTHER,
                                   save=False),
        ["reconnect", "current_url", "get", "get_window_size", "set_window_size",
         "find_element", "current_url", "title"],
    ),
}

_NAMED_BY_PART = ("element", "switch_to", "alert", "set")


def _road(log):
    return [
        f"{entry[0]}.{entry[1]}" if entry[0] in _NAMED_BY_PART else entry[0]
        for entry in log
    ]


@pytest.mark.parametrize("name", sorted(ROADS))
def test_each_action_takes_its_road_in_the_same_order(
    actions, monkeypatch, pointer_lands, name
):
    """One reattach, then the navigation if one is due, the wait, the act and the
    page state, in exactly the order each action has always sent them."""
    settings, call, road = ROADS[name]
    driver = CountingDriver(**settings)
    monkeypatch.setattr(actions.grid, "reconnect", driver.reconnect)
    call(actions)
    assert _road(driver.log) == road
    assert driver.reconnects == 1


def test_print_reads_the_page_once_it_is_kept(actions, monkeypatch):
    driver = CountingDriver()
    monkeypatch.setattr(actions.grid, "reconnect", driver.reconnect)
    monkeypatch.setattr(
        CountingDriver, "page_source", property(lambda d: d.log.append(("page_source",))
                                                or "<html/>"),
        raising=False,
    )
    kept = []
    actions.keep = lambda name, data, folder: kept.append(driver.names()) or {}
    actions.print_("abc", format="html", url=OTHER)
    assert kept == [["reconnect", "current_url", "get", "page_source"]]
    assert _road(driver.log) == kept[0] + ["current_url", "title"]


# ---- Task 19: a pointer move asks the page once per question -----------------

# The same roads with the pointer code running for real, so the scripts a move
# sends are on the record. Each move reads where it landed with center.js, which
# also answers the window's size: there is no separate viewport read.
CENTER = {"at": [10, 10], "scrolled": False, "outside": False, "viewport": [1000, 800]}
MOVES = {
    "interact click": (
        lambda a: a.interact("abc", "click", selector=AT),
        ["reconnect", "find_element", "element.is_displayed", "element.is_enabled",
         "execute_script", "execute", "execute_script", "element.click",
         "current_url", "title"],
    ),
    "interact hover glide": (
        lambda a: a.interact("abc", "hover", selector=AT, glide=True),
        ["reconnect", "find_element", "execute_script", "execute_script", "execute",
         "execute_script", "current_url", "title"],
    ),
    "drag by an offset": (
        lambda a: a.drag("abc", selector=AT, by_x=5),
        ["reconnect", "find_element", "element.is_displayed", "element.is_enabled",
         "execute_script", "execute", "execute_script", "execute", "current_url",
         "title"],
    ),
    "drag to an element": (
        lambda a: a.drag("abc", selector=AT, to={"css": "#y"}),
        ["reconnect", "find_element", "element.is_displayed", "element.is_enabled",
         "execute_script", "execute", "execute_script", "find_element",
         "execute_script", "execute", "current_url", "title"],
    ),
}


@pytest.mark.parametrize("name", sorted(MOVES))
def test_a_pointer_move_reads_the_window_with_where_it_landed(
    actions, monkeypatch, name
):
    call, road = MOVES[name]
    driver = CountingDriver(scripts={"atX": {"inside": False}, "bringIntoView": CENTER})

    class _Pointable(ScriptedElement):
        """`ActionBuilder.move_to` refuses anything that is not a WebElement."""

        id = "element-1"

    WebElement.register(_Pointable)
    driver.__dict__["element"] = _Pointable(driver.log)
    monkeypatch.setattr(actions.grid, "reconnect", driver.reconnect)
    actions.pointers.set("abc", 1, 1)
    call(actions)
    assert _road(driver.log) == road


def test_a_wait_asks_again_every_fifth_of_a_second_and_names_what_it_waited_for(
    scripted, clock
):
    """Selenium's own default is half a second. The timeout, and the sentence a
    wait that expires is re-raised with, are what they were."""
    from selenium.common.exceptions import TimeoutException

    driver = scripted()
    asked = []
    with pytest.raises(TimeoutException) as expired:
        browser._waited(driver, lambda d: asked.append(clock[0]), 2, "no element x")
    gaps = {round(b - a, 6) for a, b in pairwise(asked)}
    assert gaps == {browser.WAIT_POLL} == {0.2}
    assert 2 <= clock[0] < 2 + browser.WAIT_POLL + 1e-9
    assert str(expired.value.msg).startswith(
        f"no element x within 2s. The browser is at {PAGE!r}"
    )


# ---- I8, I9 live in test_pointer.py -----------------------------------------


# ---- S3, S4: what open_session applies and says ------------------------------


def test_a_width_alone_keeps_the_browsers_own_height(actions, scripted):
    driver = scripted(window=(1000, 700))
    result = actions.open_session(width=640)
    assert ("set_window_size", 640, 700) in driver.log
    assert (result["width"], result["height"]) == (640, 700)


def test_a_height_alone_keeps_the_browsers_own_width(actions, scripted):
    driver = scripted(window=(1000, 700))
    actions.open_session(height="480")
    assert ("set_window_size", 1000, 480) in driver.log


def test_no_size_given_leaves_the_window_alone(actions, scripted):
    driver = scripted()
    actions.open_session()
    assert "set_window_size" not in driver.names()


def test_timeouts_are_applied_and_echoed_in_settings(actions, scripted):
    driver = scripted()
    result = actions.open_session(page_load_timeout="45", script_timeout=12)
    assert ("set_page_load_timeout", 45) in driver.log
    assert ("set_script_timeout", 12) in driver.log
    assert result["settings"]["page_load_timeout"] == 45
    assert result["settings"]["script_timeout"] == 12


def test_timeouts_not_given_are_neither_applied_nor_echoed(actions, scripted):
    driver = scripted()
    result = actions.open_session()
    assert "set_page_load_timeout" not in driver.names()
    assert "set_script_timeout" not in driver.names()
    assert set(result["settings"]) == {"browser", "width", "height"}


def test_a_timeout_that_cannot_be_read_falls_back_to_the_default_wait(actions, scripted):
    """Applied at the fallback, and echoed as zero: what a refresh replays is
    what was asked for, and an unreadable value was not a request."""
    driver = scripted()
    result = actions.open_session(page_load_timeout="soon", script_timeout="later")
    assert ("set_page_load_timeout", 300) in driver.log
    assert ("set_script_timeout", 30) in driver.log
    assert result["settings"]["page_load_timeout"] == 0
    assert result["settings"]["script_timeout"] == 0


def test_the_session_a_browser_opens_with_starts_with_no_pointer(actions, scripted):
    scripted(session_id="fresh")
    actions.pointers.set("fresh", 5, 5)
    actions.open_session()
    assert actions.pointers.get("fresh") is None


# ---- S10: ending a browser ---------------------------------------------------


class _Reply:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_ending_a_browser_forgets_where_its_pointer_was(actions, monkeypatch):
    sent = []
    monkeypatch.setattr(
        actions.grid.http,
        "delete",
        lambda url, timeout=None: sent.append(url) or _Reply(200),
    )
    actions.pointers.set("abc", 40, 50)
    result = actions.end_browser("abc")
    assert result == {"success": True, "session_id": "abc"}
    assert sent == ["http://grid.invalid:4444/session/abc"]
    assert actions.pointers.get("abc") is None


def test_a_grid_that_refuses_to_end_a_browser_is_an_error_and_keeps_the_pointer(
    actions, monkeypatch
):
    monkeypatch.setattr(actions.grid.http, "delete", lambda *a, **k: _Reply(500))
    actions.pointers.set("abc", 40, 50)
    with pytest.raises(requests.HTTPError):
        actions.end_browser("abc")
    assert actions.pointers.get("abc") == (40, 50)


# ---- F2: frames --------------------------------------------------------------


def test_switching_to_a_frame_by_index_defaults_to_the_first(actions, scripted):
    driver = scripted(scripts={"window.top": True})
    result = actions.frame("abc", "switch", index="")
    assert ("switch_to", "frame", 0) in driver.log
    assert result["action"] == "switch"
    assert result["in_frame"] is True


def test_switching_to_a_frame_by_index_uses_the_number_given(actions, scripted):
    driver = scripted()
    actions.frame("abc", index="2")
    assert ("switch_to", "frame", 2) in driver.log


def test_switching_to_a_frame_by_selector_goes_into_the_element_found(actions, scripted):
    driver = scripted()
    actions.frame("abc", selector={"css": "iframe"})
    assert ("switch_to", "frame", driver.element) in driver.log
    assert ("element", "is_displayed") not in driver.log


def test_leaving_a_frame_reports_it_is_out(actions, scripted):
    driver = scripted(scripts={"window.top": False})
    result = actions.frame("abc", "default")
    assert ("switch_to", "default_content") in driver.log
    assert result["in_frame"] is False


def test_a_page_that_cannot_say_whether_it_is_framed_is_reported_out(actions, scripted):
    driver = scripted()

    def refuse(script, *args):
        raise RuntimeError("session gone")

    driver.__dict__["execute_script"] = refuse
    assert actions.frame("abc", "parent")["in_frame"] is False


# ---- G1: dialogs -------------------------------------------------------------


def test_reading_a_dialog_returns_its_text_and_leaves_it_open(actions, scripted):
    driver = scripted(dialog=DIALOG)
    result = actions.dialog("abc", "read")
    assert result["action"] == "read"
    assert result["message"] == DIALOG
    assert not [e for e in driver.log if e[0] == "alert" and e[1] != "text"]


@pytest.mark.parametrize("action", ["accept", "dismiss"])
def test_answering_a_dialog_reads_its_text_before_closing_it(actions, scripted, action):
    driver = scripted(dialog=DIALOG)
    result = actions.dialog("abc", action)
    alerts = [e[1] for e in driver.log if e[0] == "alert"]
    assert result["message"] == DIALOG
    assert alerts == ["text", action]


def test_sending_text_to_a_prompt_types_then_accepts(actions, scripted):
    driver = scripted(dialog=DIALOG)
    actions.dialog("abc", "SEND_TEXT", text=7)
    alerts = [e[1:] for e in driver.log if e[0] == "alert"]
    assert alerts == [("text",), ("send_keys", "7"), ("accept",)]


def test_a_dialog_that_is_not_there_is_a_timeout_naming_what_was_waited_for(
    actions, scripted, clock
):
    from selenium.common.exceptions import NoAlertPresentException, TimeoutException

    driver = scripted()

    class _NoAlert:
        @property
        def alert(self):
            raise NoAlertPresentException()

    driver.__dict__["switch_to"] = _NoAlert()
    with pytest.raises(TimeoutException, match="no dialog"):
        actions.dialog("abc", "read", wait_timeout=1)


# ---- C2: the three ways to capture -------------------------------------------


def test_a_viewport_screenshot_resizes_nothing(actions, scripted):
    driver = scripted()
    result = actions.screenshot("abc", save=False)
    assert "set_window_size" not in driver.names()
    assert (result["width"], result["height"]) == (3, 4)
    assert result["image"] == driver.PNG
    assert "file" not in result


def test_a_sized_screenshot_leaves_the_window_at_the_size_asked_for(actions, scripted):
    driver = scripted(window=(1000, 700))
    actions.screenshot("abc", width=400, save=False)
    assert ("set_window_size", 400, 700) in driver.log
    assert driver.window == {"width": 400, "height": 700}


def test_a_selector_screenshot_captures_the_element_alone(actions, scripted):
    driver = scripted()
    driver.element.screenshot_as_base64 = driver.PNG
    result = actions.screenshot("abc", selector={"css": "#chart"}, save=False)
    assert result["image"] == driver.PNG
    assert "get_screenshot_as_base64" not in driver.names()
    assert ("element", "is_displayed") not in driver.log


def test_a_full_page_screenshot_grows_the_window_and_then_restores_it(
    actions, scripted
):
    driver = scripted(window=(1000, 700), scripts={"scrollWidth": [800, 3000]})
    actions.screenshot("abc", full_page=True, save=False)
    resizes = [e[1:] for e in driver.log if e[0] == "set_window_size"]
    assert resizes == [(1000, 3000), (1000, 700)]
    assert driver.window == {"width": 1000, "height": 700}


def test_a_full_page_screenshot_that_fails_still_restores_the_window(actions, scripted):
    driver = scripted(window=(1000, 700), scripts={"scrollWidth": [800, 3000]})

    def fail():
        raise RuntimeError("renderer crashed")

    driver.__dict__["get_screenshot_as_base64"] = fail
    with pytest.raises(RuntimeError, match="renderer crashed"):
        actions.screenshot("abc", full_page=True, save=False)
    assert driver.window == {"width": 1000, "height": 700}


# ---- U5, U6: staging an upload --------------------------------------------


@pytest.fixture
def staging(tmp_path, monkeypatch):
    """Where `mkdtemp` stages an upload, and nowhere else."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _staged(folder: Path):
    return list(folder.glob("selenium-flow-*"))


def test_an_upload_takes_no_file_from_the_servers_disk(actions, untouched):
    """Ruling 3: `path` read any file the server user could, its config and
    mounted secrets included. A file comes as content or from the session's
    store; a server path is not an argument at all."""
    with pytest.raises(TypeError, match="path"):
        actions.upload_file("abc", selector={"css": "input"}, path="/etc/passwd")


def test_the_staging_directory_is_removed_once_the_upload_is_sent(
    actions, scripted, staging
):
    driver = scripted()
    seen = []
    send = driver.element.send_keys
    driver.element.send_keys = lambda p: seen.append(Path(p).read_text()) or send(p)
    result = actions.upload_file("abc", selector={"css": "input"}, text="hello")
    assert seen == ["hello"]
    assert result["bytes"] == 5
    assert _staged(staging) == []


def test_the_staging_directory_is_removed_when_the_upload_fails(
    actions, scripted, staging
):
    driver = scripted()
    driver.element.fail_on["send_keys"] = RuntimeError("node refused")
    with pytest.raises(RuntimeError, match="node refused"):
        actions.upload_file("abc", selector={"css": "input"}, text="hello")
    assert _staged(staging) == []


def test_an_unwritable_temp_directory_is_a_server_fault_naming_it(
    actions, scripted, tmp_path, monkeypatch
):
    gone = tmp_path / "no-such-mount"
    monkeypatch.setattr(tempfile, "tempdir", str(gone))
    scripted()
    with pytest.raises(RuntimeError) as caught:
        actions.upload_file("abc", selector={"css": "input"}, text="hello")
    assert str(gone) in str(caught.value)
    assert "TMPDIR" in str(caught.value)
    assert status_for(caught.value) == 500
