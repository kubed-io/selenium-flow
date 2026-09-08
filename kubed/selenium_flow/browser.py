"""Talking to Selenium Grid.

Everything that knows about WebDriver lives here: connecting, reattaching to a
session someone else opened, waiting for elements, and the small coercions that
keep a caller's loose JSON from crashing a handler.

The browser is stateful, this process is not. A session lives on the Grid and
the caller carries its id, which is what lets this server scale to zero, restart
mid-workflow, or run behind more than one replica without losing a browser.
"""

from __future__ import annotations

import base64
from urllib.parse import urlsplit, urlunsplit

import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver as RemoteWebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

DEFAULT_GRID_URL = "http://selenium-grid-selenium-hub.flow.svc.cluster.local:4444"


class ReattachDriver(RemoteWebDriver):
    """A driver that binds to an existing session instead of creating one.

    ``start_session`` is what normally negotiates capabilities and opens a
    browser. Skipping it is the whole trick: the object talks to a session that
    is already running on the Grid.
    """

    def start_session(self, capabilities):
        self._web_element_identifier = None


def as_bool(value, default: bool = False) -> bool:
    """Coerce a JSON or form value to bool.

    Callers that send everything as strings would otherwise make ``"false"``
    true, because every non-empty string is truthy in Python.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value, default: int) -> int:
    """Coerce to int, falling back on anything unusable.

    An omitted optional parameter often arrives as an empty string, and
    ``int("")`` raises.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def normalize_url(url: str) -> str:
    """Drop the fragment and any trailing slash so equivalent URLs compare equal."""
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, "")
    )


def png_size(b64: str) -> tuple[int, int]:
    """Width and height straight from the PNG IHDR header — no image library."""
    raw = base64.b64decode(b64[:64])
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


class Grid:
    """A Selenium Grid endpoint, and the operations this server needs from it."""

    def __init__(self, url: str = DEFAULT_GRID_URL, timeout: int = 30):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _options(self) -> webdriver.ChromeOptions:
        options = webdriver.ChromeOptions()
        options.add_argument("--no-sandbox")
        # Chrome's default /dev/shm is 64MB and it crashes under it.
        options.add_argument("--disable-dev-shm-usage")
        return options

    def open(self) -> RemoteWebDriver:
        """Create a session and return its driver."""
        return webdriver.Remote(command_executor=self.url, options=self._options())

    def reconnect(self, session_id: str) -> RemoteWebDriver:
        """Bind to a session that is already running on the Grid."""
        driver = ReattachDriver(command_executor=self.url, options=self._options())
        driver.session_id = session_id
        return driver

    def is_alive(self, session_id: str) -> bool:
        """Whether a session still exists on the Grid.

        The Grid reaps a session after its idle timeout, so a stored id can name
        a browser that is already gone. Asking for the session's current URL is
        the cheapest W3C call that distinguishes the two: a live session answers
        200, a reaped one answers 404.
        """
        try:
            response = requests.get(
                f"{self.url}/session/{session_id}/url", timeout=self.timeout
            )
        except requests.RequestException:
            return False
        return response.status_code == 200

    def quit(self, session_id: str) -> None:
        """End a session, freeing its Grid slot.

        Done over plain HTTP rather than ``driver.quit()`` so that a session
        whose driver cannot be reattached can still be cleaned up.
        """
        response = requests.delete(
            f"{self.url}/session/{session_id}", timeout=self.timeout
        )
        response.raise_for_status()

    def status(self) -> dict:
        """The Grid's own readiness payload."""
        response = requests.get(f"{self.url}/status", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def session_count(self) -> int:
        """How many sessions are currently held across the Grid."""
        response = requests.post(
            f"{self.url}/graphql",
            json={"query": "{ grid { sessionCount } }"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["data"]["grid"]["sessionCount"]


def wait_for_element(driver, xpath: str, timeout: int = 30):
    """Wait for an element to exist in the DOM."""
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.XPATH, xpath))
    )


def wait_for_clickable(driver, xpath: str, timeout: int = 30):
    """Wait for an element to exist *and* be interactable."""
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )


def ensure_url(driver, url: str) -> bool:
    """Navigate to ``url`` unless the browser is already there.

    This is deliberately not an assertion. A caller naming a page means "act on
    this page", and failing because the browser happens to be elsewhere makes
    every workflow carry its own navigation step.
    """
    if normalize_url(driver.current_url) == normalize_url(url):
        return False
    driver.get(url)
    return True


def full_page_size(driver) -> tuple[int, int]:
    """Full scrollable document size.

    ``body`` and ``documentElement`` disagree depending on the page's CSS, so
    take the larger of the two.
    """
    return driver.execute_script(
        "return [Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),"
        " Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)]"
    )
