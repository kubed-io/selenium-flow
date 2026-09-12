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


def test_chrome_is_never_offered_the_chance_to_save_a_password():
    """The worst-shaped failure this server has had (§F1.37).

    Submitting a password makes Chrome offer to remember it, and that offer is
    browser furniture rather than anything in the page: it takes the input focus
    and keeps it. Every later click and keystroke is then delivered to the
    bubble, while WebDriver still finds elements and still reports success — so
    a flow that logs in leaves a browser that looks fine and does nothing.
    Nobody is here to answer the offer, so it must never be made.
    """
    prefs = (
        Grid("http://grid.invalid:4444")
        ._options("chrome")
        .to_capabilities()["goog:chromeOptions"]["prefs"]
    )
    assert prefs["credentials_enable_service"] is False
    assert prefs["profile.password_manager_enabled"] is False
    # Same popup, reached by a different route.
    assert prefs["profile.password_manager_leak_detection"] is False


def test_firefox_is_never_offered_the_chance_to_save_a_password():
    prefs = (
        Grid("http://grid.invalid:4444")
        ._options("firefox")
        .to_capabilities()["moz:firefoxOptions"]["prefs"]
    )
    assert prefs["signon.rememberSignons"] is False


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


class TestASessionInheritsItsOwnLastValues:
    """A caller that names nothing after its browser went means "carry on".

    That is a stronger signal than any default and a weaker one than an argument
    it just typed, which is where `previous` sits in the cascade.
    """

    def test_the_previous_browser_beats_the_server_default(self):
        resolved = settings_module.resolve(
            {}, env={"DEFAULT_BROWSER": "chrome"}, previous={"browser": "firefox"}
        )
        assert resolved["browser"] == "firefox"

    def test_an_explicit_argument_still_beats_the_previous_one(self):
        resolved = settings_module.resolve(
            {"browser": "chrome"}, env={}, previous={"browser": "firefox"}
        )
        assert resolved["browser"] == "chrome"

    def test_the_window_size_is_inherited_too(self):
        resolved = settings_module.resolve(
            {}, env={}, previous={"width": 1400, "height": 900}
        )
        assert resolved["width"] == 1400
        assert resolved["height"] == 900

    def test_nothing_previous_changes_nothing(self):
        assert settings_module.resolve({}, env={}, previous=None) == {}
        assert settings_module.resolve({}, env={}, previous={}) == {}

    def test_junk_in_a_stored_record_cannot_smuggle_in_a_setting(self):
        """The record is written by this server, but it round-trips through the
        store as JSON — so an unknown key is dropped rather than passed to
        open_session, where it would be a TypeError."""
        resolved = settings_module.resolve({}, env={}, previous={"nonsense": 1})
        assert resolved == {}


def test_ending_a_browser_the_grid_no_longer_has_is_success():
    """A 404 from the Grid means the session is gone, which is the state `quit`
    was asked to produce.

    It matters because the moment a caller is most likely to end an already-gone
    browser is a cleanup after a failure — exactly when a second error helps
    least. A Grid that cannot be reached still raises: it has not told us the
    browser is gone, so reporting success would be a guess.
    """
    from unittest.mock import Mock, patch

    gone = Mock(status_code=404)
    with patch("kubed.selenium_flow.browser.requests.delete", return_value=gone):
        Grid("http://grid.invalid:4444").quit("already-gone")
    gone.raise_for_status.assert_not_called()


async def test_a_rejected_browser_does_not_cost_you_the_one_you_have(
    server, monkeypatch
):
    """`open_session` ends the browser you hold before opening the next, which
    is what makes switching a single call. The validation has to come first.

    It used to come second, so a typo in `browser=` quit a working browser and
    then failed — the caller lost its page, its cookies and the form it had
    filled in, for a misspelling. A rejected argument must cost nothing.
    """
    from .conftest import NAMED

    open_session = (await server.mcp.get_tool("open_session")).fn
    ended = []
    monkeypatch.setattr(server.sessions, "key", lambda: NAMED)
    monkeypatch.setattr(server.sessions, "end_browser", lambda *a: ended.append(a))

    with pytest.raises(ValueError, match="safari"):
        open_session(browser="safari")
    assert ended == [], "the browser was ended before the argument was checked"
