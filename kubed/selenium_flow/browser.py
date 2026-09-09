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
import io
import time
import zipfile
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


def is_partial(name: str) -> bool:
    """Whether a download-directory entry is Chrome's scratch copy, not a file.

    Two shapes, both renamed away once the download completes: ``<name>.crdownload``
    and a hidden ``.com.google.Chrome.XXXXXX``.
    """
    return name.endswith(".crdownload") or name.startswith(".")


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
        # Ask the Grid to manage this session's downloads. The node then keeps a
        # per-session directory and exposes it at /session/<id>/se/files, which
        # is the whole file store — listed, read and reaped by the Grid itself.
        # Without this the browser downloads into a directory nothing can reach.
        options.set_capability("se:downloadsEnabled", True)
        # Chrome asks permission before a page's *second* automatic download and
        # denies it silently when nobody can answer. The first file of a session
        # would arrive and every one after it would vanish with no error, which
        # is a miserable thing to debug. 1 = allow.
        options.add_experimental_option(
            "prefs",
            {"profile.default_content_setting_values.automatic_downloads": 1},
        )
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

        The Grid reaps idle sessions, so a stored id can name a browser that is
        already gone, and the caller's next call should transparently reopen.

        The subtlety is what counts as gone. Asking for the session's URL is the
        cheapest probe, but it fails for *two* different reasons: the session
        does not exist (404, ``invalid session id``), or it exists and is
        blocked by an open dialog (500, ``unexpected alert open``). Treating
        both as dead abandons a perfectly good browser — with its dialog still
        open — and leaks the Grid slot it holds.

        So only an explicit "this id is not a session" means gone. Anything
        else, including a Grid we cannot reach right now, is assumed alive: a
        wrong "alive" surfaces as a real error on the next call, while a wrong
        "dead" silently strands a browser.
        """
        try:
            response = requests.get(
                f"{self.url}/session/{session_id}/url", timeout=self.timeout
            )
        except requests.RequestException:
            return True
        if response.status_code == 200:
            return True
        try:
            error = response.json().get("value", {}).get("error", "")
        except ValueError:
            error = ""
        return error != "invalid session id"

    def quit(self, session_id: str) -> None:
        """End a session, freeing its Grid slot.

        Done over plain HTTP rather than ``driver.quit()`` so that a session
        whose driver cannot be reattached can still be cleaned up.
        """
        response = requests.delete(
            f"{self.url}/session/{session_id}", timeout=self.timeout
        )
        response.raise_for_status()

    def sessions(self) -> list[dict]:
        """Every browser the Grid is currently running.

        Read from the Grid rather than from this server's own records, because
        the Grid is the one that actually has them: sessions opened over the
        HTTP surface, by another replica, or by something that is not this
        server at all still belong on the list.
        """
        found = []
        for node in self.status()["value"].get("nodes", []):
            for slot in node.get("slots", []):
                session = slot.get("session")
                if not session:
                    continue
                caps = session.get("capabilities", {})
                found.append(
                    {
                        "session_id": session.get("sessionId"),
                        "started": session.get("start"),
                        "browser": caps.get("browserName"),
                        "version": caps.get("browserVersion"),
                        "node": caps.get("se:containerName") or node.get("id"),
                        "vnc": caps.get("se:vnc"),
                    }
                )
        return sorted(found, key=lambda s: s.get("started") or "", reverse=True)

    def files(self, session_id: str) -> list[dict]:
        """What this session has finished downloading, newest first.

        The Grid owns this store, which is the point: it is created with the
        session, lives on the node beside the browser, and is deleted when the
        session ends. A store of our own would duplicate all three and get the
        lifecycle subtly wrong.

        Downloads in flight are omitted. Chrome writes them under a scratch name
        and renames on completion, so listing them would offer the caller a file
        that is half-written and about to be called something else.
        """
        response = requests.get(
            f"{self.url}/session/{session_id}/se/files", timeout=self.timeout
        )
        response.raise_for_status()
        files = [
            f
            for f in response.json().get("value", {}).get("files", [])
            if not is_partial(f.get("name", ""))
        ]
        return sorted(files, key=lambda f: f.get("creationTime", 0), reverse=True)

    def read_file(self, session_id: str, name: str) -> bytes:
        """One downloaded file's bytes.

        The Grid always answers with a zip, even for a single file, so the
        archive is unwrapped here — callers want the file, not the envelope.
        """
        response = requests.post(
            f"{self.url}/session/{session_id}/se/files",
            json={"name": name},
            timeout=self.timeout,
        )
        response.raise_for_status()
        archive = base64.b64decode(response.json()["value"]["contents"])
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            member = name if name in bundle.namelist() else bundle.namelist()[0]
            return bundle.read(member)

    def clear_files(self, session_id: str) -> None:
        """Drop everything the session has downloaded, keeping the browser."""
        requests.delete(
            f"{self.url}/session/{session_id}/se/files", timeout=self.timeout
        )

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


_SAVE_JS = """
const [name, b64, mime] = arguments;
const bin = atob(b64);
const bytes = new Uint8Array(bin.length);
for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
const url = URL.createObjectURL(new Blob([bytes], {type: mime}));
const a = document.createElement('a');
a.href = url;
a.download = name;
(document.body || document.documentElement).appendChild(a);
a.click();
setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 0);
return name;
"""


def save_to_downloads(
    grid, driver, name: str, data: bytes, mime: str, timeout: int = 15
) -> dict:
    """Put bytes into the session's download store, by having the page save them.

    There is no API for writing into the Grid's store — it only lists, reads and
    deletes. But it is fed by whatever the *browser* downloads, so a page that
    downloads a blob puts a file there through the ordinary path. That keeps one
    store for everything: a PDF the site served and a screenshot this server
    rendered land side by side, with the same lifecycle.

    Chrome deduplicates names by appending " (1)", so the filename is discovered
    by diffing the listing rather than assumed. The download is asynchronous,
    hence the poll.
    """
    before = {f["name"] for f in grid.files(driver.session_id)}
    driver.execute_script(_SAVE_JS, name, base64.b64encode(data).decode(), mime)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for entry in grid.files(driver.session_id):
            if entry["name"] not in before:
                return entry
        time.sleep(0.25)
    raise TimeoutError(f"{name} did not appear in the session's downloads")


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
