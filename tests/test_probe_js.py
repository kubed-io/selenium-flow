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
    "<style>#menu ul{display:none}</style>"
    "<nav id=menu><button class=toggle>Account</button>"
    '<ul class=menu-content><li><a href="/settings">Settings</a></li></ul></nav>'
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
