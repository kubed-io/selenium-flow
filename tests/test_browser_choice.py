"""Choosing between Chrome and Firefox.

Both are plain W3C WebDriver, so the risk here is not the protocol — it is the
two places a browser choice can silently become a different browser: a bad value
falling back to the default, and a refresh after the Grid reaps a session
reopening on the default instead of the one that was asked for.
"""

import pytest
from selenium import webdriver

from kubed.selenium_flow import settings as settings_module
from kubed.selenium_flow.browser import (
    BROWSERS,
    DEFAULT_BROWSER,
    Grid,
    is_partial,
    normalize_browser,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "chrome"),
        ("", "chrome"),
        ("   ", "chrome"),
        ("chrome", "chrome"),
        ("firefox", "firefox"),
        ("Firefox", "firefox"),
        ("  FIREFOX  ", "firefox"),
    ],
)
def test_a_browser_name_is_normalized(value, expected):
    assert normalize_browser(value) == expected


def test_an_unknown_browser_is_refused_rather_than_defaulted():
    """The one coercion in this codebase that raises instead of falling back.

    Handing back Chrome to a caller that asked for Safari would let the task
    keep running on a browser it did not choose, which is the whole thing the
    argument exists to control.
    """
    with pytest.raises(ValueError) as exc:
        normalize_browser("safari")
    assert "safari" in str(exc.value)
    # The message has to say what IS allowed, the way `interact` does.
    for name in BROWSERS:
        assert name in str(exc.value)


def test_the_options_are_the_right_driver_class():
    grid = Grid("http://grid.invalid:4444")
    assert isinstance(grid._options("chrome"), webdriver.ChromeOptions)
    assert isinstance(grid._options("firefox"), webdriver.FirefoxOptions)
    assert isinstance(grid._options(None), webdriver.ChromeOptions)


@pytest.mark.parametrize("name", BROWSERS)
def test_both_browsers_carry_the_two_capabilities_that_matter(name):
    """These are W3C, not vendor extensions, and neither is optional.

    Without `unhandledPromptBehavior: ignore` the browser answers dialogs on the
    caller's behalf; without `se:downloadsEnabled` the Grid keeps no per-session
    download store and `session_files` has nothing to list.
    """
    caps = Grid("http://grid.invalid:4444")._options(name).to_capabilities()
    assert caps["unhandledPromptBehavior"] == "ignore"
    assert caps["se:downloadsEnabled"] is True


def test_firefox_is_told_to_save_downloads_without_asking():
    """Firefox's analogue of Chrome's automatic-downloads prompt.

    `save_pdf` is the one that would hang without it: Firefox opens a PDF in its
    own viewer rather than saving, so the file never reaches the Grid's store.
    """
    caps = Grid("http://grid.invalid:4444")._options("firefox").to_capabilities()
    prefs = caps["moz:firefoxOptions"]["prefs"]
    assert prefs["browser.download.folderList"] == 2
    assert "application/pdf" in prefs["browser.helperApps.neverAsk.saveToDisk"]


@pytest.mark.parametrize(
    "name,partial",
    [
        ("report.pdf.crdownload", True),  # chrome, in flight
        ("report.pdf.part", True),  # firefox, in flight
        (".com.google.Chrome.AbC123", True),  # chrome's hidden scratch copy
        ("report.pdf", False),
        ("notes.part.pdf", False),  # `.part` only counts as a suffix
    ],
)
def test_a_download_in_flight_is_not_offered_to_the_caller(name, partial):
    """Each browser has its own scratch name and both get renamed on completion.

    Listing one hands the caller a half-written file that is about to be called
    something else.
    """
    assert is_partial(name) is partial


class TestTheSettingsCascade:
    """`browser` rides the same cascade as the window size, deliberately.

    What comes out of `resolve` is what gets stored against the session, and the
    stored settings are what a refresh replays — so a browser kept beside the
    cascade rather than in it would be dropped on every refresh.
    """

    def test_the_env_default_is_read(self):
        assert settings_module.from_env({"DEFAULT_BROWSER": "firefox"}) == {
            "browser": "firefox"
        }

    def test_the_env_var_is_not_called_browser(self):
        """`BROWSER` is a Unix convention for the user's preferred browser
        command, and plenty of environments — code-server among them — set it to
        a shell script. Reading it would break the server for reasons that have
        nothing to do with it."""
        assert settings_module.from_env({"BROWSER": "/usr/bin/xdg-open"}) == {}

    def test_an_unusable_default_is_ignored_rather_than_fatal(self):
        """A typo in a deployment's environment must not stop every session."""
        assert settings_module.from_env({"DEFAULT_BROWSER": "nonsense"}) == {}

    def test_a_client_default_comes_from_the_url_or_a_header(self):
        assert settings_module.from_client({"browser": "firefox"}, {}) == {
            "browser": "firefox"
        }
        assert settings_module.from_client({}, {"x-browser": "firefox"}) == {
            "browser": "firefox"
        }

    def test_the_header_beats_the_query_parameter(self):
        resolved = settings_module.from_client(
            {"browser": "chrome"}, {"x-browser": "firefox"}
        )
        assert resolved == {"browser": "firefox"}

    def test_an_explicit_argument_beats_both(self):
        resolved = settings_module.resolve(
            {"browser": "chrome"}, env={"DEFAULT_BROWSER": "firefox"}
        )
        assert resolved["browser"] == "chrome"

    def test_an_explicit_bad_browser_is_fatal_even_though_a_default_is_not(self):
        """The asymmetry is the point: a default is a preference and dropping a
        bad one costs nothing, while an explicit argument is this caller naming
        a browser for this session."""
        with pytest.raises(ValueError):
            settings_module.resolve({"browser": "safari"}, env={})

    def test_nothing_anywhere_leaves_the_browser_unset(self):
        """Unset, not "chrome" — the cascade only reports what was actually set,
        and `open_session` is where the default is applied."""
        assert "browser" not in settings_module.resolve({}, env={})
