"""What a browser and a Grid default to: constants, no Selenium.

Config and session settings validate against these, so they live where importing
them does not load a driver.
"""

from __future__ import annotations

DEFAULT_GRID_URL = "http://selenium-grid-selenium-hub.flow.svc.cluster.local:4444"

# The browsers this server can open. Both are plain W3C WebDriver, which is the
# whole reason a second one costs so little: every action already speaks the
# standard protocol, so only session creation differs. Edge would be a third
# entry plus a stereotype on the Grid, not a new code path.
CHROME = "chrome"
FIREFOX = "firefox"
BROWSERS = (CHROME, FIREFOX)
DEFAULT_BROWSER = CHROME


def normalize_browser(value=None) -> str:
    """A supported browser name, or the default when nothing was asked for.

    Unlike the numeric settings, an unrecognised value is fatal rather than
    ignored. Falling back would hand the caller a *different browser* than the
    one it named and let it keep going — and the whole reason to name one is
    that the choice matters.
    """
    if value is None or not str(value).strip():
        return DEFAULT_BROWSER
    name = str(value).strip().lower()
    if name not in BROWSERS:
        raise ValueError(
            f"unknown browser {value!r}; known browsers: {', '.join(BROWSERS)}"
        )
    return name
