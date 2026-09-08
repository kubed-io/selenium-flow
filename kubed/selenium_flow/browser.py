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
from selenium.common.exceptions import (
    TimeoutException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.file_detector import LocalFileDetector
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
        # Never let the browser answer a dialog on the caller's behalf. Chrome's
        # default, "dismiss and notify", silently clicks Cancel on a confirm and
        # only reports it as an error on whatever command happened to notice —
        # so a destructive prompt gets answered by accident and the dialog is
        # gone before anyone can decide. "ignore" leaves it open for `dialog`.
        options.set_capability("unhandledPromptBehavior", "ignore")
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


# Selenium raises TimeoutException with an EMPTY message, which surfaces to a
# caller as the useless string "Message:". Every wait here re-raises with what
# was actually being waited for, because "no element matched //x" is the whole
# diagnosis and the bare timeout is none of it.
def _waited(driver, condition, timeout: int, description: str):
    try:
        return WebDriverWait(driver, timeout).until(condition)
    except TimeoutException as exc:
        raise TimeoutException(
            f"{description} within {timeout}s. The browser is at "
            f"{driver.current_url!r}; if that is not the page you expected, the "
            f"wait is not the problem."
        ) from exc


def wait_for_element(driver, xpath: str, timeout: int = 30):
    """Wait for an element to exist in the DOM."""
    return _waited(
        driver,
        EC.presence_of_element_located((By.XPATH, xpath)),
        timeout,
        f"no element matched {xpath!r}",
    )


def wait_for_clickable(driver, xpath: str, timeout: int = 30):
    """Wait for an element to exist *and* be interactable."""
    return _waited(
        driver,
        EC.element_to_be_clickable((By.XPATH, xpath)),
        timeout,
        f"no clickable element matched {xpath!r}",
    )


def in_frame(driver) -> bool:
    """Whether the session is currently switched into an iframe.

    There is no WebDriver command for "which frame am I in", so this asks the
    page: a document whose window is not the top window is a frame. Worth
    reporting, because a forgotten frame switch makes every later locator fail
    for a reason that looks nothing like the cause.
    """
    try:
        return bool(driver.execute_script("return window.self !== window.top"))
    except Exception:  # noqa: BLE001 - a dialog or a dead session must not raise here
        return False


def page_state(driver) -> dict:
    """Where the browser ended up, tolerating an open dialog.

    Every action reports the resulting url and title, and reading either is
    refused while a dialog is open. Letting that raise would make an action fail
    when it had in fact succeeded — the click landed, it just opened a prompt.
    So an open dialog is reported as page state, which is what it is, and tells
    the caller exactly what to do next.
    """
    try:
        return {"url": driver.current_url, "title": driver.title}
    except UnexpectedAlertPresentException:
        try:
            message = driver.switch_to.alert.text
        except Exception:  # noqa: BLE001 - it may close between the two calls
            message = ""
        return {
            "url": None,
            "title": None,
            "dialog": message,
            "hint": "a dialog is open and blocks other actions; answer it with dialog",
        }


def wait_for_alert(driver, timeout: int = 30):
    """Wait for a JS dialog and return it.

    An open alert blocks every other WebDriver command with
    ``UnexpectedAlertPresentException``, so this is the only way out of a page
    that has raised one.
    """
    return _waited(
        driver,
        EC.alert_is_present(),
        timeout,
        "no dialog (alert, confirm or prompt) was open",
    )


def accept_local_files(driver) -> None:
    """Let ``send_keys`` on a file input upload a file from *this* process.

    The browser runs on a Grid node in another container, so a path from here
    means nothing there. The local file detector makes Selenium ship the bytes
    to the node first and hand the input the remote path it landed at.
    """
    driver.file_detector = LocalFileDetector()


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
