"""The probe's JavaScript, checked rather than mocked.

Every other test of `outline` hands the action a driver double that returns a
canned list, so the script itself — selector uniqueness, the interactive filter,
the limit, what counts as visible — could regress with the suite green. Copilot
said so twice on #29 and was right both times.

Two guards, because neither is enough alone:

- **A syntax check**, which runs anywhere node does. A script that does not
  parse fails in the browser as a WebDriverException whose message is not about
  the mistake, and the substring assertions elsewhere would not notice.
- **An integration test against a real browser**, which is the only thing that
  can prove what the script *answers*. It needs a Grid, so it is marked
  `integration` and skips without `GRID_URL` — `pytest -m integration` with that
  set is the run that covers it.
"""

import os
import shutil
import subprocess
from urllib.parse import quote

import pytest

from kubed.selenium_flow import probe

GRID_URL = os.environ.get("GRID_URL", "")

PAGE = (
    "<style>#menu ul{display:none} #acctmenu{display:none}</style>"
    "<nav id=menu><button class=toggle>Account</button>"
    '<ul class=menu-content><li><a href="/settings">Settings</a></li></ul></nav>'
    # The other kind of menu: a trigger that says aria-expanded, so it opens on
    # a CLICK. Hovering it does nothing, which is what the advice used to say
    # (saga §F2.10). The trigger is also an <a> with no href, which is why half
    # a real site's nav was invisible to the map.
    "<div id=acct><a id=acctbtn aria-expanded=false aria-controls=acctmenu>"
    "Account menu</a>"
    '<ul id=acctmenu><li><a href="/profile">Profile</a></li></ul></div>'
    # Two dropdowns under one wrapper, where the SECOND menu has no id of its
    # own. Its nearest ancestor holding an [aria-expanded] is the wrapper, which
    # holds the FIRST toggle too - and that one says aria-controls="menu-a",
    # so it opens somebody else's menu (Copilot, #31).
    "<style>.twin-hidden{display:none}</style>"
    "<nav id=twin>"
    "<a id=tog-a aria-expanded=false aria-controls=menu-a>A</a>"
    '<ul id=menu-a class=twin-hidden><li><a href="/a-item">A item</a></li></ul>'
    '<span><ul class=twin-hidden><li><a href="/b-item">B item</a></li></ul></span>'
    "</nav>"
    # A container whose text belongs to its children, not to itself - and
    # beside it a button whose single wrapper still makes the text its own.
    '<li id=navrow><a href="/a">Alpha</a><a href="/b">Beta</a></li>'
    "<button class=pub><span>Publish</span></button>"
    "<form><input type=hidden name=csrf value=1>"
    '<span id=lbl>Email</span><span id=req>(required)</span>'
    '<input name=email aria-labelledby="lbl req">'
    "<button disabled>Save</button></form>"
    '<a href="/one">Open</a><a href="/two">Open</a>'
    '<div role=heading>Not a control</div>'
)


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to parse JS")
@pytest.mark.parametrize(
    "script", [probe.USABLE_JS, probe.OUTLINE_JS], ids=["usable", "outline"]
)
def test_the_script_parses(tmp_path, script):
    """Wrapped in a function because that is how WebDriver runs it: the bare
    text has a top-level `return`, which is only legal inside one."""
    path = tmp_path / "probe.js"
    path.write_text("(function () {\n" + script + "\n});", encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def browser_page():
    """A real browser on a page with one of everything the probe classifies."""
    from kubed.selenium_flow.actions import Actions
    from kubed.selenium_flow.browser import Grid

    grid = Grid(GRID_URL)
    actions = Actions(grid)
    session = actions.open_session(
        url="data:text/html," + quote(PAGE), width=900, height=700
    )["session_id"]
    try:
        yield actions, session
    finally:
        actions.end_browser(session)


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_outline_answers_about_a_real_page(browser_page):
    actions, session = browser_page
    listed = actions.outline(session)["elements"]
    by_name = {entry["name"]: entry for entry in listed}

    # The hidden menu item: found, with a selector, and reported unusable.
    settings = by_name["Settings"]
    assert settings["visible"] is False
    assert settings["reason"] == "hidden"
    assert settings["blocked_by"] == "ul.menu-content"

    # An IDREF *list* is joined, not looked up as one id.
    assert "Email (required)" in by_name

    # A disabled control says so; a hidden input is not listed at all; a
    # structural role is not a control.
    assert by_name["Save"]["reason"] == "disabled"
    assert not any("csrf" in (name or "") for name in by_name)
    assert "Not a control" not in by_name

    # An <a> with no href is a control somebody wired up, and skipping it is
    # what gated half a real nav (saga §F2.10).
    assert "Account menu" in by_name


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_a_hidden_entry_names_what_opens_it_and_which_gesture(browser_page):
    """`blocked_by` says what is in the way, which was never the question. On
    selenium.dev the advice said hover, the menu opened on click, and the page
    had been reporting `aria-expanded` the whole time (saga §F2.10)."""
    actions, session = browser_page
    by_name = {e["name"]: e for e in actions.outline(session)["elements"]}

    # The click-toggled one: the trigger declares aria-expanded, so say click.
    profile = by_name["Profile"]
    assert profile["reason"] == "hidden"
    assert profile["revealed_by"] == "#acctbtn"
    assert profile["open_with"] == "click"

    # And the :hover one, which declares nothing, still says hover.
    settings = by_name["Settings"]
    assert settings["open_with"] == "hover"
    assert settings["revealed_by"]


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_a_trigger_that_opens_a_different_menu_is_not_offered(browser_page):
    """`aria-controls` is a statement about WHICH menu, and it disqualifies as
    well as qualifies: a wrapper holding two dropdowns would otherwise hand out
    whichever toggle came first in the document (Copilot, #31)."""
    actions, session = browser_page
    by_name = {e["name"]: e for e in actions.outline(session)["elements"]}

    # The one it does name: click that.
    assert by_name["A item"]["revealed_by"] == "#tog-a"
    assert by_name["A item"]["open_with"] == "click"

    # The one it does not: anything but that, and never "click #tog-a".
    other = by_name["B item"]
    assert other["reason"] == "hidden"
    assert other.get("revealed_by") != "#tog-a"


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_the_failure_says_click_when_the_trigger_says_click(browser_page):
    """The same fact, in the sentence a timeout carries."""
    from selenium.common.exceptions import TimeoutException

    actions, session = browser_page
    with pytest.raises(TimeoutException) as timed_out:
        actions.interact(session, "click", css='a[href="/profile"]', wait_timeout=1)
    message = str(timed_out.value)
    assert "#acctbtn" in message
    assert 'interact(action="click")' in message


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_no_text_xpath_for_an_element_whose_text_is_its_children(browser_page):
    """A nav container was offered
    //li[normalize-space()="HelpDeskHelpDeskAdd TicketHistory..."] — every
    descendant's text run together, brittle and not its own label."""
    actions, session = browser_page
    listed = actions.outline(session, interactive=False, limit=300)["elements"]

    rows = [e for e in listed if e.get("name") == "AlphaBeta"]
    assert rows, "the container should be listed"
    for entry in rows:
        assert "xpath" not in entry, entry
        assert entry["css"] == "#navrow"

    # And the rule is not too wide: a label wrapped in one span is still the
    # element's own text, which is what <button><span>Save</span></button> is.
    wrapped = next(e for e in listed if e.get("name") == "Publish" and e["role"] == "button")
    assert wrapped["xpath"] == '//button[normalize-space()="Publish"]'


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_every_selector_it_hands_out_resolves_to_that_element(browser_page):
    """The promise the whole map rests on: one element, and the right one."""
    from selenium.webdriver.common.by import By

    actions, session = browser_page
    driver = actions.grid.reconnect(session)
    for entry in actions.outline(session, interactive=False, limit=200)["elements"]:
        how, selector = (
            (By.CSS_SELECTOR, entry["css"]) if entry.get("css")
            else (By.XPATH, entry["xpath"])
        )
        found = driver.find_elements(how, selector)
        assert len(found) == 1, f"{selector} matched {len(found)}"


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_the_filter_and_the_limit_are_applied_in_the_page(browser_page):
    actions, session = browser_page
    assert actions.outline(session, limit=0)["count"] == 0
    assert actions.outline(session, limit=2)["count"] == 2
    filtered = actions.outline(session, text="settings")["elements"]
    assert [entry["name"] for entry in filtered] == ["Settings"]


@pytest.mark.integration
@pytest.mark.skipif(not GRID_URL, reason="needs a Selenium Grid; set GRID_URL")
def test_a_covered_click_says_what_is_on_top(browser_page):
    """`element_to_be_clickable` passes for a covered element, so this failure
    arrives from `element.click()` rather than from the wait (Copilot, #29)."""
    from selenium.common.exceptions import ElementClickInterceptedException

    actions, session = browser_page
    covered = (
        "<button id=under style='position:absolute;left:20px;top:40px'>Under</button>"
        "<div id=cover style='position:fixed;left:0;top:0;width:100%;height:200px;"
        "background:#eee'>cookies</div>"
    )
    with pytest.raises(ElementClickInterceptedException) as refused:
        actions.interact(
            session, "click", css="#under", url="data:text/html," + quote(covered)
        )
    assert "div#cover is on top of it" in str(refused.value)
