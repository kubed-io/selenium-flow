"""What a browser and a Grid default to: constants, no Selenium.

Config and session settings validate against these, so they live where importing
them does not load a driver.
"""

from __future__ import annotations

DEFAULT_GRID_URL = "http://localhost:4444"

# The browsers this server can open. Every action speaks plain W3C WebDriver,
# so only session creation differs.
CHROME = "chrome"
FIREFOX = "firefox"
BROWSERS = (CHROME, FIREFOX)
DEFAULT_BROWSER = CHROME


def normalize_browser(value=None) -> str:
    """A supported browser name, or the default when nothing was asked for.

    An unrecognised name raises: falling back would hand the caller a browser
    it did not name.
    """
    if value is None or not str(value).strip():
        return DEFAULT_BROWSER
    name = str(value).strip().lower()
    if name not in BROWSERS:
        raise ValueError(
            f"unknown browser {value!r}; known browsers: {', '.join(BROWSERS)}"
        )
    return name
