"""Talking to Selenium Grid.

Everything that knows about WebDriver lives here: connecting, reattaching to a
session someone else opened, waiting for elements, and the small coercions that
keep a caller's loose JSON from crashing a handler.

The browser is stateful, this process is not. A browser lives on the Grid and
the caller's session name leads back to it, which is what lets this server
scale to zero, restart mid-workflow, or run behind more than one replica without
losing a browser.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import socket
import zipfile
from http.cookiejar import DefaultCookiePolicy
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.remote.file_detector import LocalFileDetector
from selenium.webdriver.remote.webdriver import WebDriver as RemoteWebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.connection import HTTPConnection
from urllib3.exceptions import ProtocolError

from ..urls import normalize_url
from . import probe
from .defaults import (
    DEFAULT_GRID_URL,
    FIREFOX,
    normalize_browser,
)

# Seconds any one request to the Grid's own HTTP endpoints may take.
GRID_TIMEOUT = 30
# Connections each pool keeps open to the Grid: one per call that can be in
# flight at once, which is anyio's 40 worker threads. Fewer and a busy moment
# opens, uses and drops the overflow, logging a warning for each.
GRID_CONNECTIONS = 40
# How often a wait for an element or a dialog asks again. Selenium's 0.5 s
# default makes an element that renders just after a miss cost up to half a
# second; each ask is one to three round trips, so this is not a busy loop.
WAIT_POLL = 0.2


def _socket_options() -> list[tuple[int, int, int]]:
    """urllib3's defaults, plus what makes a pooled connection notice a dead hub.

    The Grid sits behind a Service: a connection kept from the last call is
    tied to one hub pod, and when that pod dies with its node nothing closes
    the socket. A request sent on it is retransmitted until the kernel gives up,
    about 924 s (tcp_retries2), where a fresh connection gave up in about
    127 s. Keepalive probes after 30 s idle, 10 s apart, three missed, and
    unacknowledged data abandoned after 60 s, bound that. A long command - a
    300 s page load, an assert - is not cut short: the hub's kernel answers
    the probes while the browser works.

    Each option only where the platform has it, so a macOS or Windows install
    still imports.
    """
    options = list(HTTPConnection.default_socket_options)
    options.append((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))
    for name, value in (
        ("TCP_KEEPIDLE", 30),
        ("TCP_KEEPINTVL", 10),
        ("TCP_KEEPCNT", 3),
        ("TCP_USER_TIMEOUT", 60_000),
    ):
        if hasattr(socket, name):
            options.append((socket.IPPROTO_TCP, getattr(socket, name), value))
    return options


SOCKET_OPTIONS = _socket_options()


class _GridAdapter(HTTPAdapter):
    """requests' adapter with `SOCKET_OPTIONS` on every connection it opens."""

    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = SOCKET_OPTIONS
        super().init_poolmanager(*args, **kwargs)

# How long to give a keystroke-triggered navigation to commit before concluding
# there was not one. See `settled`. Short on purpose: every Enter that navigates
# nowhere waits this out, so it trades a small delay on those for a correct
# answer on the ones that do navigate.
NAVIGATION_SETTLE = 2.0
# Seconds a site data BiDi socket may take to connect or answer.
BIDI_TIMEOUT = 5.0
# How often Selenium looks for a BiDi reply. Its 100 ms default is a floor
# under every BiDi command; at 5 ms a spare tab costs 30-140 ms per origin.
BIDI_INTERVAL = 0.005


class ReattachDriver(RemoteWebDriver):
    """A driver that binds to an existing session instead of creating one.

    ``start_session`` is what normally negotiates capabilities and opens a
    browser. Skipping it is the whole trick: the object talks to a session that
    is already running on the Grid.
    """

    def start_session(self, capabilities):
        self._web_element_identifier = None


def is_partial(name: str) -> bool:
    """Whether a download-directory entry is a scratch copy, not a finished file.

    Each browser has its own shape and all of them are renamed away once the
    download completes: Chrome writes ``<name>.crdownload`` and a hidden
    ``.com.google.Chrome.XXXXXX``, Firefox writes ``<name>.part``. Listing one
    would offer the caller a half-written file that is about to be called
    something else.
    """
    return name.endswith((".crdownload", ".part")) or name.startswith(".")


def png_size(b64: str) -> tuple[int, int]:
    """Width and height straight from the PNG IHDR header — no image library."""
    raw = base64.b64decode(b64[:64])
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def _dropped(exc: requests.ConnectionError) -> bool:
    """Whether a request died on a connection the Grid had already let go.

    A pooled connection outlives a Grid restart, and the first request sent on
    it is answered with a reset rather than a response: urllib3's "Connection
    aborted". A refused or unresolvable Grid is a different failure and raises
    as it always did; asking again would only ask the same dead address twice.
    """
    return bool(exc.args) and isinstance(exc.args[0], ProtocolError)


class Grid:
    """A Selenium Grid endpoint, and the operations this server needs from it.

    It holds two connection pools, one per client library, so the calls each
    action makes reuse connections instead of opening one apiece: ``http`` for
    the Grid's own endpoints, and the WebDriver connection every reattached
    driver shares (see `reconnect`).
    """

    def __init__(self, url: str = DEFAULT_GRID_URL):
        self.url = url.rstrip("/")
        self.http = requests.Session()
        adapter = _GridAdapter(pool_maxsize=GRID_CONNECTIONS)
        self.http.mount("http://", adapter)
        self.http.mount("https://", adapter)
        # Each call used to be its own session and kept no cookies; a pool
        # must not start pinning the Grid's calls to whatever an ingress set.
        self.http.cookies.set_policy(DefaultCookiePolicy(allowed_domains=()))
        self._webdriver = None

    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
        """One request to the Grid, sent again once if the pool's connection
        turns out to be one the Grid dropped (`_dropped`).

        Once: the retry runs on a fresh connection, so a second failure is the
        Grid's real answer and raises exactly as a single request would.
        """
        send = getattr(self.http, method)
        url = f"{self.url}{path}"
        try:
            return send(url, timeout=GRID_TIMEOUT, **kwargs)
        except requests.ConnectionError as exc:
            if not _dropped(exc):
                raise
        return send(url, timeout=GRID_TIMEOUT, **kwargs)

    def _options(self, browser: str | None = None, insecure: bool = False):
        """Capabilities for a new session of ``browser``.

        The two capabilities that matter are W3C standard and identical for
        every browser; only the vendor-specific noise below them differs, which
        is why supporting a second browser is a branch rather than a subclass.
        """
        name = normalize_browser(browser)
        options = (
            webdriver.FirefoxOptions() if name == FIREFOX else webdriver.ChromeOptions()
        )
        # Never let the browser answer a dialog on the caller's behalf. The
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
        # Every browser speaks BiDi so a saved site can be restored into it;
        # every other tool still uses classic WebDriver.
        options.enable_bidi = True
        # `open_session(insecure=true)`: the caller knows this site's
        # certificate is self-signed, and says so for this one browser. Chosen
        # per browser and never server-wide, because only the caller knows which
        # site it is about to drive.
        if insecure:
            options.accept_insecure_certs = True

        if name == FIREFOX:
            # 2 = the directory the Grid node set for this session. Firefox
            # otherwise saves to its own default and se:downloadsEnabled has
            # nothing to collect.
            options.set_preference("browser.download.folderList", 2)
            options.set_preference("browser.download.useDownloadDir", True)
            # Firefox's equivalent of Chrome's automatic-downloads prompt: it
            # asks what to do with a type it does not recognise, and nobody is
            # there to answer. A PDF a site serves would hang without this,
            # since Firefox opens PDFs in its own viewer by default.
            options.set_preference(
                "browser.helperApps.neverAsk.saveToDisk",
                "application/pdf,image/png,application/octet-stream",
            )
            options.set_preference("pdfjs.disabled", True)
            # See the Chrome block below: the password-save prompt is a browser
            # popup, and the browser is the only thing that can answer it.
            options.set_preference("signon.rememberSignons", False)
            return options

        options.add_argument("--no-sandbox")
        # Chrome's default /dev/shm is 64MB and it crashes under it.
        options.add_argument("--disable-dev-shm-usage")
        # Chrome asks permission before a page's *second* automatic download and
        # denies it silently when nobody can answer. The first file of a session
        # would arrive and every one after it would vanish with no error, which
        # is a miserable thing to debug. 1 = allow.
        options.add_experimental_option(
            "prefs",
            {
                "profile.default_content_setting_values.automatic_downloads": 1,
                # Submitting a password offers to save it, and that offer is a
                # *browser* popup rather than anything in the page. It takes the
                # input focus and never gives it back, so every later click and
                # keystroke is delivered to the bubble instead of the document —
                # silently: the element is found, the command succeeds, and
                # nothing happens. A flow that logs in would leave the browser
                # looking fine and unable to do anything, which is the worst
                # shape a failure can take. Nobody is here to answer the offer,
                # so do not let it be made.
                "credentials_enable_service": False,
                "profile.password_manager_enabled": False,
                # Same popup, different trigger: Chrome warns about a password it
                # believes was breached.
                "profile.password_manager_leak_detection": False,
            },
        )
        return options

    def open(
        self, browser: str | None = None, insecure: bool = False
    ) -> RemoteWebDriver:
        """Create a session on ``browser`` and return its driver."""
        return webdriver.Remote(
            command_executor=self.url, options=self._options(browser, insecure)
        )

    def reconnect(self, session_id: str) -> RemoteWebDriver:
        """Bind to a session that is already running on the Grid.

        The options are inert here and deliberately not parameterised by
        browser: ``ReattachDriver`` skips ``start_session``, which is the only
        thing that would ever send them. Reattaching to a Firefox session with
        Chrome's options works for exactly that reason, and asking the caller
        to know the browser to reconnect would be a lie about what is needed.

        A reattached driver lives for one call, its connection does not: every
        driver this Grid hands out shares one ``RemoteConnection``, so a call
        reuses the keep-alive connections the last one opened rather than
        building a new pool and a new TCP connection. The first driver builds
        it through Selenium's own constructor, which picks the connection class
        it always did; urllib3 reopens a pooled connection the Grid closed.
        """
        if self._webdriver is None:
            driver = ReattachDriver(
                command_executor=self.url,
                options=self._options(),
                client_config=ClientConfig(
                    remote_server_addr=self.url,
                    init_args_for_pool_manager={
                        "init_args_for_pool_manager": {
                            "maxsize": GRID_CONNECTIONS,
                            "socket_options": SOCKET_OPTIONS,
                        }
                    },
                    # Selenium waits 30 s for a BiDi socket that never answers;
                    # `bidi` would stall that long on a Grid whose BiDi route is
                    # down. Nothing but `bidi` opens a socket.
                    websocket_timeout=BIDI_TIMEOUT,
                    websocket_interval=BIDI_INTERVAL,
                ),
            )
            self._webdriver = driver.command_executor
        else:
            driver = ReattachDriver(
                command_executor=self._webdriver, options=self._options()
            )
        driver.session_id = session_id
        return driver

    @contextlib.contextmanager
    def bidi(self, session_id: str):
        """A reattached driver that speaks BiDi: ``.storage``, ``.network``,
        ``.browsing_context``, ``.script``.

        The socket address is derived, not discovered: the Grid proxies it at
        ``/session/<id>/se/bidi``.
        """
        parts = urlsplit(self.url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        socket = urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        driver = self.reconnect(session_id)
        # The socket's timeout and polling are on the shared connection's
        # config, set once by `reconnect`.
        driver.caps = {"webSocketUrl": f"{socket}/session/{session_id}/se/bidi"}
        try:
            yield driver
        finally:
            # Selenium opens the socket lazily on first use; closing it is the
            # only cleanup, the browser is not ours to quit.
            connection = getattr(driver, "_websocket_connection", None)
            if connection is not None:
                with contextlib.suppress(Exception):
                    connection.close()

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
            response = self._send("get", f"/session/{session_id}/url")
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

        A 404 is success. The Grid has no such session, which is the state this
        was asked to produce — ending is the one operation where "it was already
        done" and "I did it" are the same answer. It matters because the moment
        a caller is most likely to end a browser that is already gone is a
        cleanup after a failure, which is exactly when a second error helps
        least. Anything else still raises: a Grid that cannot be reached has not
        told us the browser is gone, and reporting success would be a guess.
        """
        response = self._send("delete", f"/session/{session_id}")
        if response.status_code == 404:
            return
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
        response = self._send("get", f"/session/{session_id}/se/files")
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
        response = self._send(
            "post", f"/session/{session_id}/se/files", json={"name": name}
        )
        response.raise_for_status()
        archive = base64.b64decode(response.json()["value"]["contents"])
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            member = name if name in bundle.namelist() else bundle.namelist()[0]
            return bundle.read(member)

    def clear_files(self, session_id: str) -> None:
        """Drop everything the session has downloaded, keeping the browser.

        A 404 is success, same reasoning as `quit`: the browser or its file
        store is already gone — a raced reap — so "already cleared" and "just
        cleared" are the same answer. Anything else raises, so a Grid refusal
        surfaces as a Grid refusal instead of the silent 200 an unchecked
        response used to report.
        """
        response = self._send("delete", f"/session/{session_id}/se/files")
        if response.status_code == 404:
            return
        response.raise_for_status()

    def status(self) -> dict:
        """The Grid's own readiness payload."""
        response = self._send("get", "/status")
        response.raise_for_status()
        return response.json()

    def session_count(self) -> int:
        """How many sessions are currently held across the Grid."""
        response = self._send(
            "post", "/graphql", json={"query": "{ grid { sessionCount } }"}
        )
        response.raise_for_status()
        return response.json()["data"]["grid"]["sessionCount"]


# Selenium raises TimeoutException with an EMPTY message, which surfaces to a
# caller as the useless string "Message:". Every wait here re-raises with what
# was actually being waited for, because "no element matched //x" is the whole
# diagnosis and the bare timeout is none of it.
def _waited(driver, condition, timeout: int, description: str, target=None):
    try:
        return WebDriverWait(driver, timeout, poll_frequency=WAIT_POLL).until(
            condition
        )
    except TimeoutException as exc:
        # When the element is there and simply cannot be used, the page knows
        # why and a bare timeout does not. Only the clickability waits pass a
        # target: a presence wait failed because nothing matched at all, so
        # there is nothing to ask about (saga §F2.8).
        why = probe.explain(driver, target) if target else ""
        raise TimeoutException(
            f"{description} within {timeout}s. The browser is at "
            f"{driver.current_url!r}; if that is not the page you expected, the "
            f"wait is not the problem." + (f" {why}" if why else "")
        ) from exc


# How a caller may address an element. XPath is the original and stays the
# default in every example; CSS is here because it is shorter for the common
# cases and because `#id` and `.class` make separate strategies for those
# redundant.
#
# Selenium offers six more, and they are left out for two different reasons.
# ID, NAME, TAG_NAME and CLASS_NAME are each a CSS selector spelled longhand.
# LINK_TEXT and PARTIAL_LINK_TEXT are NOT — CSS cannot match text content at
# all — but XPath can, with `//a[contains(., 'Next')]`, so between these two
# every one of the six is already reachable. Two strategies a model has to
# choose between is a schema; eight is a quiz.
SELECTORS = {"xpath": By.XPATH, "css": By.CSS_SELECTOR}


def locator(selector) -> tuple[str, str]:
    """Exactly one selector, as the (strategy, value) pair Selenium wants.

    ``selector`` is ``{"xpath": ...}`` or ``{"css": ...}`` — one object rather
    than two flat arguments, because they are one choice and every tool that
    took one took the other (§F2.14). A model with the same two fields is
    accepted too, which is what the MCP surface passes.

    Mutually exclusive keys rather than a ``by=`` enum beside a ``value=``: the
    key names itself, so a caller writes ``{"css": "button.go"}`` without having
    to be told what the strategies are, and ``xpath`` keeps meaning exactly what
    it has always meant.

    Both, or neither, is refused rather than resolved. Guessing which one was
    meant is how a typo in one of them becomes a click on the element the other
    one found, which is the most expensive kind of wrong this server can be.
    **Never a fallback from one to the other**, for that reason: a flow that
    silently used its second choice reports `ok` while it drifts (§F2.14).
    """
    if selector is None:
        selector = {}
    elif hasattr(selector, "model_dump"):
        selector = selector.model_dump(exclude_none=True)
    elif isinstance(selector, str):
        # A multipart form field can only carry a string, and some clients
        # stringify object arguments. Parsed here rather than only at the tool
        # boundary, because an upload over HTTP arrives as a form.
        try:
            selector = json.loads(selector)
        except ValueError:
            raise ValueError(
                "selector must be an object: {\"css\": \"button.go\"} or "
                "{\"xpath\": \"//button\"}"
            ) from None
    if not isinstance(selector, dict):
        raise ValueError(
            "selector must be an object: {\"css\": \"button.go\"} or "
            "{\"xpath\": \"//button\"}"
        )
    unknown = set(selector) - set(SELECTORS)
    if unknown:
        raise ValueError(
            f"a selector takes xpath or css, not {', '.join(sorted(unknown))}"
        )
    given = [(name, selector.get(name)) for name in SELECTORS if selector.get(name)]
    if not given:
        raise ValueError(
            "no element given: pass a selector, e.g. "
            "selector={\"xpath\": \"//button[@type='submit']\"} or "
            "selector={\"css\": \"button[type=submit]\"}"
        )
    if len(given) > 1:
        raise ValueError(
            "a selector takes xpath or css, not both: "
            + ", ".join(f"{name}={value!r}" for name, value in given)
        )
    name, value = given[0]
    return SELECTORS[name], str(value)


def wait_for_element(driver, target, timeout: int = 30):
    """Wait for an element to exist in the DOM. ``target`` comes from `locator`."""
    _, value = target
    return _waited(
        driver,
        EC.presence_of_element_located(target),
        timeout,
        f"no element matched {value!r}",
    )


def wait_for_clickable(driver, target, timeout: int = 30):
    """Wait for an element to exist *and* be interactable."""
    _, value = target
    return _waited(
        driver,
        EC.element_to_be_clickable(target),
        timeout,
        f"no clickable element matched {value!r}",
        target=target,
    )


def settled(driver, anchor, timeout: float = NAVIGATION_SETTLE) -> None:
    """Wait for a navigation a keystroke may just have started.

    ``element.click()`` and ``driver.get()`` are both *specified* to wait for a
    navigation they cause, which is why every action built on those reports the
    page it landed on. Sending keys carries no such promise: the command returns
    while the browser is still on the old page, so reading state straight
    afterwards describes the page that was submitted FROM — and can tear,
    pairing the old URL with the new document's not-yet-set title.

    Measured against a real Grid before this existed: ``write(submit=True)``
    reported the pre-submit page on 6 of 6 Firefox runs and 2 of 6 Chrome runs,
    while ``interact`` and ``navigate`` were right every time.

    The signal is ``anchor`` going stale, which happens when a new document
    commits. It is deliberately best-effort and deliberately short: a form
    handled in JavaScript never navigates and never goes stale, so *expiring is
    an ordinary outcome here*, not an error, and it is the cost paid by the
    keypresses that were never going anywhere.
    """
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05).until(
            EC.staleness_of(anchor)
        )
    except TimeoutException:
        # Nothing navigated. The caller is on the page it was already on, which
        # is a true answer and the common one.
        return
    except Exception:  # noqa: BLE001 - a dialog, or a session that just went
        return
    # Staleness only says the old document is gone. The new one may still be
    # parsing, and its title is empty until it is not — the other half of the
    # torn read.
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05).until(
            lambda d: d.execute_script("return document.readyState") != "loading"
        )
    except Exception:  # noqa: BLE001 - same, and a URL alone is still useful
        return


# Whether the selected browsing context is a frame. `window` and `top` are both
# unforgeable; `window.self` is not - a page can run `self = top` and a check
# reading it says "not in a frame" from inside one.
IN_FRAME = "return window !== window.top"

# The selected browsing context's origin: the frame's when the session is in
# one, the top page's otherwise. Never gated on a frame check, which a hostile
# frame could forge.
ORIGIN_HERE = "return document.location.origin"


def in_frame(driver) -> bool:
    """Whether the session is currently switched into an iframe.

    There is no WebDriver command for "which frame am I in", so this asks the
    page: a document whose window is not the top window is a frame. Worth
    reporting, because a forgotten frame switch makes every later locator fail
    for a reason that looks nothing like the cause.
    """
    try:
        return bool(driver.execute_script(IN_FRAME))
    except Exception:  # noqa: BLE001 - a dialog or a dead session must not raise here
        return False


def frame_origin(driver) -> str:
    """The origin of the document a keystroke would reach.

    A script runs in the selected browsing context, so ``document.location
    .origin`` is the frame's own when the session is in one and the top page's
    when it is not. ``page_state`` cannot say this, because WebDriver's url is
    always the top page's. Read every time, never only "when in a frame": the
    frame check is the page's to answer, and a hostile frame can forge it.

    A document with no origin of its own (``data:``, ``about:blank`` and
    ``srcdoc`` frames) answers ``"null"``; a frame with the ``sandbox`` attribute
    still reports its URL's origin. A failure or a non-string answer is ``""``;
    both match no allowed origin, so not knowing is refused rather than read as
    the top page.
    """
    try:
        found = driver.execute_script(ORIGIN_HERE)
    except Exception:  # noqa: BLE001 - not knowing is an answer the leash refuses
        return ""
    return found if isinstance(found, str) else ""


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
        "return ["
        "Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),"
        "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        "]"
    )
