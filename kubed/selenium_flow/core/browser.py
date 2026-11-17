"""Talking to Selenium Grid: connecting, reattaching to a running session,
waiting for elements, and reading where the browser is.

The browser lives on the Grid and this process holds no state about it, so the
server can scale to zero, restart, or run as several replicas without losing one.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import socket
import zipfile
from collections.abc import Callable
from http.cookiejar import DefaultCookiePolicy
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from selenium import webdriver
from selenium.common.exceptions import (
    InsecureCertificateException,
    StaleElementReferenceException,
    TimeoutException,
    UnexpectedAlertPresentException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.remote.file_detector import LocalFileDetector
from selenium.webdriver.remote.webdriver import WebDriver as RemoteWebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.connection import HTTPConnection
from urllib3.exceptions import ProtocolError

from ..faults import Unresolved, Untrusted, unresolved
from ..urls import route_of, route_query, same_page
from . import probe
from .defaults import (
    DEFAULT_GRID_URL,
    FIREFOX,
)
from .session_caps import Built, build

# Seconds any one request to the Grid's own HTTP endpoints may take.
GRID_TIMEOUT = 30
# Seconds a read that is only shown may take, sent once: the idle timeout,
# asked on every open, must not hold an open that has already succeeded.
SHOWN_TIMEOUT = 5
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

    A pooled connection is tied to one hub pod behind the Service, and when that
    pod dies nothing closes the socket: a request on it is retransmitted for
    about 924 s (tcp_retries2). Keepalive probes after 30 s idle, 10 s apart,
    three missed, and unacknowledged data abandoned after 60 s, bound that. A
    300 s page load is not cut short: the hub's kernel answers the probes.

    Each option only where the platform has it, so macOS and Windows still
    import this.
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


# How long a keystroke-triggered navigation has to commit (`settled`). Every
# Enter that navigates nowhere waits this out.
NAVIGATION_SETTLE = 2.0

# How long a single-page app's router has to set a new route's title
# (`landed`). A route that keeps its title waits this out.
TITLE_SETTLE = 1.0


class ReattachDriver(RemoteWebDriver):
    """A driver bound to a running session: ``start_session``, which would open
    a browser, does nothing."""

    def start_session(self, capabilities):
        self._web_element_identifier = None


def _seconds(ms) -> int | None:
    """A node's ``sessionTimeout`` (milliseconds) in whole seconds, or None."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return int(ms) // 1000 or None


def is_partial(name: str) -> bool:
    """Whether a download-directory entry is an unfinished download.

    Chrome writes ``<name>.crdownload`` and a hidden ``.com.google.Chrome.XXXXXX``,
    Firefox writes ``<name>.part``, and each is renamed once the download is done.
    """
    return name.endswith((".crdownload", ".part")) or name.startswith(".")


def png_size(b64: str) -> tuple[int, int]:
    """Width and height from the PNG IHDR header."""
    raw = base64.b64decode(b64[:64])
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def _dropped(exc: requests.ConnectionError) -> bool:
    """Whether a request died on a pooled connection the Grid had let go
    (urllib3's "Connection aborted"), as after a Grid restart. A refused or
    unresolvable Grid is a different failure."""
    return bool(exc.args) and isinstance(exc.args[0], ProtocolError)


def _listed(status: dict) -> tuple[int, dict[str, int | None]]:
    """`Grid.listing` out of a ``GET /status`` payload."""
    nodes = status["value"].get("nodes") or []
    running: dict[str, int | None] = {}
    for node in nodes:
        timeout = _seconds(node.get("sessionTimeout"))
        for slot in node.get("slots") or []:
            session_id = (slot.get("session") or {}).get("sessionId")
            if session_id:
                running[session_id] = timeout
    return len(nodes), running


# What `reconnect` builds its inert driver with. Never mutated:
# `_Sent.to_capabilities` hands out a copy.
_INERT = build(None)


class _Sent:
    """Options whose capabilities are exactly a built set (`Grid._options`)."""

    def __init__(self, built: Built):
        super().__init__()
        self._built = built
        # What `enable_bidi` reads back, for anything that asks the object.
        self.enable_bidi = True

    def to_capabilities(self) -> dict:
        return copy.deepcopy(self._built.send)


class _ChromeSent(_Sent, webdriver.ChromeOptions):
    pass


class _FirefoxSent(_Sent, webdriver.FirefoxOptions):
    pass


class Grid:
    """A Selenium Grid endpoint, and the operations this server needs from it.

    It holds two connection pools: ``http`` for the Grid's own endpoints, and
    the WebDriver connection every reattached driver shares (see `reconnect`).
    """

    def __init__(self, url: str = DEFAULT_GRID_URL):
        self.url = url.rstrip("/")
        self.http = requests.Session()
        adapter = _GridAdapter(pool_maxsize=GRID_CONNECTIONS)
        self.http.mount("http://", adapter)
        self.http.mount("https://", adapter)
        # No cookies: an ingress's would pin the Grid's calls.
        self.http.cookies.set_policy(DefaultCookiePolicy(allowed_domains=()))
        self._webdriver = None

    def _send(
        self,
        method: str,
        path: str,
        *,
        timeout: float = GRID_TIMEOUT,
        retry: bool = True,
        **kwargs,
    ) -> requests.Response:
        """One request to the Grid, sent again once (on a fresh connection) if
        the pool's connection was one the Grid dropped (`_dropped`), unless
        ``retry`` is off."""
        send = getattr(self.http, method)
        url = f"{self.url}{path}"
        try:
            return send(url, timeout=timeout, **kwargs)
        except requests.ConnectionError as exc:
            if not retry or not _dropped(exc):
                raise
        return send(url, timeout=timeout, **kwargs)

    def _json(self, method: str, path: str, **kwargs):
        """`_send`'s answer as JSON, raising for an error status first."""
        response = self._send(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def _options(self, built: Built | None = None):
        """The Options object an open sends: exactly ``built.send``.

        Selenium's Options add keys of their own (``pageLoadStrategy``, and on
        Firefox ``acceptInsecureCerts: true`` and ``moz:debuggerAddress``) and
        rebuild a vendor block from their fields, so this one answers
        ``to_capabilities()`` with the built set alone. The class still follows
        the browser, because Selenium picks the connection from it.

        None, from `reconnect`, is the default browser's set, never sent.
        """
        built = _INERT if built is None else built
        kind = _FirefoxSent if built.browser == FIREFOX else _ChromeSent
        return kind(built)

    def open(self, built: Built) -> RemoteWebDriver:
        """Create a session sending ``built`` and return its driver."""
        return webdriver.Remote(command_executor=self.url, options=self._options(built))

    def reconnect(self, session_id: str) -> RemoteWebDriver:
        """Bind to a session that is already running on the Grid.

        The options are never sent (``ReattachDriver`` skips ``start_session``),
        so any browser's will do.

        A reattached driver lives for one call; every driver shares one
        ``RemoteConnection``, built by the first through Selenium's own
        constructor, so a call reuses the keep-alive connections the last one
        opened.
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
                ),
            )
            self._webdriver = driver.command_executor
        else:
            driver = ReattachDriver(
                command_executor=self._webdriver, options=self._options()
            )
        driver.session_id = session_id
        return driver

    def bidi_url(self, session_id: str) -> str:
        """The browser's BiDi socket, as the Grid proxies it.

        Derived from ``url``: the ``webSocketUrl`` capability carries the Grid's
        in-cluster name (``ws://hub:4444/...``).
        """
        parts = urlsplit(self.url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        root = urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        return f"{root}/session/{session_id}/se/bidi"

    def vnc_url(self, session_id: str) -> str:
        """The browser's VNC stream, as the hub proxies it.

        Derived from ``url`` like `bidi_url`: the ``se:vnc`` capability names
        the hub's in-cluster Service.
        """
        return self.bidi_url(session_id).removesuffix("/se/bidi") + "/se/vnc"

    def is_alive(self, session_id: str) -> bool:
        """Whether a session still exists on the Grid.

        Only ``invalid session id`` means gone. Asking for the URL also fails
        while a dialog is open (``unexpected alert open``), and an unreachable
        Grid says nothing: both count as alive, because a wrong "alive" surfaces
        as an error on the next call while a wrong "dead" strands a browser.
        """
        return self._alive(session_id, timeout=GRID_TIMEOUT, quiet=True)

    def ping(self, session_id: str) -> bool:
        """The keep-alive's one command: `is_alive`'s request, sent briefly,
        which the Grid counts as activity. A request that died on a dropped
        pooled connection is sent again on a fresh one, since it never reached
        the Grid; any other failure is raised for the monitor to log."""
        return self._alive(session_id, timeout=SHOWN_TIMEOUT, quiet=False)

    def _alive(self, session_id: str, *, timeout: float, quiet: bool) -> bool:
        """``quiet``: read a request that failed as alive, as `is_alive` does;
        off, let it propagate."""
        try:
            response = self._send(
                "get", f"/session/{session_id}/url", timeout=timeout, retry=True
            )
        except requests.RequestException:
            if not quiet:
                raise
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

        Plain HTTP, so a session whose driver cannot be reattached still ends.
        A 404 is success: the session is gone, which is what was asked, often
        in a cleanup after a failure. Anything else raises.
        """
        response = self._send("delete", f"/session/{session_id}")
        if response.status_code == 404:
            return
        response.raise_for_status()

    def sessions(self) -> list[dict]:
        """Every browser the Grid is running, whoever opened it."""
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

    def listing(self) -> tuple[int, dict[str, int | None]]:
        """How many nodes are registered, and every running browser mapped to
        its node's idle timeout in seconds (None when the node does not say).

        One ``GET /status``, which touches no browser: a command sent to a
        session is activity the node counts, and would keep it alive.
        """
        return _listed(self.status())

    def session_timeout(self, session_id: str) -> int | None:
        """Seconds this browser's node lets it sit idle before reaping it, or
        None when the Grid does not list it or does not say.

        Asked once and briefly (`SHOWN_TIMEOUT`): it is shown, never needed.
        """
        status = self._json("get", "/status", timeout=SHOWN_TIMEOUT, retry=False)
        return _listed(status)[1].get(session_id)

    def files(self, session_id: str) -> list[dict]:
        """What this session has finished downloading, newest first.

        The Grid's store lives on the node and ends with the session. Downloads
        in flight are omitted (`is_partial`).
        """
        listed = self._json("get", f"/session/{session_id}/se/files")
        files = [
            f
            for f in listed.get("value", {}).get("files", [])
            if not is_partial(f.get("name", ""))
        ]
        return sorted(files, key=lambda f: f.get("creationTime", 0), reverse=True)

    def read_file(self, session_id: str, name: str) -> bytes:
        """One downloaded file's bytes, unwrapped from the zip the Grid always
        answers with."""
        answer = self._json(
            "post", f"/session/{session_id}/se/files", json={"name": name}
        )
        archive = base64.b64decode(answer["value"]["contents"])
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            member = name if name in bundle.namelist() else bundle.namelist()[0]
            return bundle.read(member)

    def clear_files(self, session_id: str) -> None:
        """Drop everything the session has downloaded, keeping the browser.

        A 404 is success, as in `quit`. Anything else raises.
        """
        response = self._send("delete", f"/session/{session_id}/se/files")
        if response.status_code == 404:
            return
        response.raise_for_status()

    def status(self) -> dict:
        """The Grid's own readiness payload."""
        return self._json("get", "/status")

    def session_count(self) -> int:
        """How many sessions are currently held across the Grid."""
        query = {"query": "{ grid { sessionCount } }"}
        answer = self._json("post", "/graphql", json=query)
        return answer["data"]["grid"]["sessionCount"]


# Selenium's TimeoutException has an empty message ("Message:"), so every wait
# re-raises with what it was waiting for.
def _waited(driver, condition, timeout: int, description: str, target=None):
    try:
        return WebDriverWait(driver, timeout, poll_frequency=WAIT_POLL).until(condition)
    except TimeoutException as exc:
        # Only the clickability waits pass a target: the element exists, and
        # the page can say why it cannot be used.
        why = probe.explain(driver, target) if target else ""
        raise TimeoutException(
            f"{description} within {timeout}s. The browser is at "
            f"{driver.current_url!r}; if that is not the page you expected, the "
            f"wait is not the problem." + (f" {why}" if why else "")
        ) from exc


# Selenium's other strategies are reachable through these two: ID, NAME,
# TAG_NAME and CLASS_NAME are CSS selectors spelled longhand, and the link-text
# ones are XPath's `//a[contains(., 'Next')]`.
SELECTORS = {"xpath": By.XPATH, "css": By.CSS_SELECTOR}


def locator(selector) -> tuple[str, str]:
    """Exactly one selector, as the (strategy, value) pair Selenium wants.

    ``selector`` is ``{"xpath": ...}`` or ``{"css": ...}``, or a model with the
    same two fields (what the MCP surface passes). Both, or neither, is refused,
    never resolved: a typo in one would become a click on the element the other
    found.
    """
    if selector is None:
        selector = {}
    elif hasattr(selector, "model_dump"):
        selector = selector.model_dump(exclude_none=True)
    elif isinstance(selector, str):
        # A multipart form field (an upload over HTTP) can only carry a string,
        # and some clients stringify object arguments.
        try:
            selector = json.loads(selector)
        except ValueError:
            raise ValueError(
                'selector must be an object: {"css": "button.go"} or '
                '{"xpath": "//button"}'
            ) from None
    if not isinstance(selector, dict):
        raise ValueError(
            'selector must be an object: {"css": "button.go"} or {"xpath": "//button"}'
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
            'selector={"xpath": "//button[@type=\'submit\']"} or '
            'selector={"css": "button[type=submit]"}'
        )
    if len(given) > 1:
        raise ValueError(
            "a selector takes xpath or css, not both: "
            + ", ".join(f"{name}={value!r}" for name, value in given)
        )
    name, value = given[0]
    return SELECTORS[name], str(value)


class Found(NamedTuple):
    """The element a wait settled on, and which of the matches it was."""

    element: Any
    index: int
    matched: int

    def fields(self) -> dict:
        """What a result says about the choice: only when there was one."""
        if self.matched > 1:
            return {"matched": self.matched, "index": self.index}
        return {}


class _FirstUsable:
    """The condition behind `wait_for_clickable` and `wait_for_shown`.

    The first displayed (and, when ``clickable``, enabled) match wins, looking
    at most `probe.CANDIDATES`: ``is_displayed`` is a round trip per element,
    inside the session's lock. A stale match is skipped. Nothing usable keeps a
    clickable wait going; a shown wait falls back to the first match.
    """

    def __init__(self, target, clickable: bool):
        self.target, self.clickable = target, clickable

    def __call__(self, driver):
        found = driver.find_elements(*self.target)
        if not found:
            return False
        if not self.clickable and len(found) == 1:
            return Found(found[0], 0, 1)
        for index, element in enumerate(found[: probe.CANDIDATES]):
            try:
                if element.is_displayed() and (
                    not self.clickable or element.is_enabled()
                ):
                    return Found(element, index, len(found))
            except StaleElementReferenceException:
                continue
        return False if self.clickable else Found(found[0], 0, len(found))


def wait_for_element(driver, target, timeout: int = 30):
    """Wait for an element to exist in the DOM. ``target`` comes from `locator`."""
    _, value = target
    return _waited(
        driver,
        EC.presence_of_element_located(target),
        timeout,
        f"no element matched {value!r}",
    )


def wait_for_clickable(driver, target, timeout: int = 30) -> Found:
    """Wait for a match that exists and is interactable: the first such one."""
    _, value = target
    return _waited(
        driver,
        _FirstUsable(target, clickable=True),
        timeout,
        f"no clickable element matched {value!r}",
        target=target,
    )


def wait_for_shown(driver, target, timeout: int = 30) -> Found:
    """Wait for a match; the first displayed one, else the first."""
    _, value = target
    return _waited(
        driver,
        _FirstUsable(target, clickable=False),
        timeout,
        f"no element matched {value!r}",
    )


def settled(driver, anchor, timeout: float = NAVIGATION_SETTLE) -> None:
    """Wait for a navigation a keystroke may just have started.

    ``element.click()`` and ``driver.get()`` wait for a navigation they cause;
    sending keys returns while the browser is still on the old page, so state
    read at once can pair the old URL with the new document's empty title.

    The signal is ``anchor`` going stale when a new document commits. A form
    handled in JavaScript never navigates, so expiring is an ordinary outcome,
    never an error.
    """
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05).until(
            EC.staleness_of(anchor)
        )
    except TimeoutException:
        return  # nothing navigated
    except Exception:  # noqa: BLE001 - a dialog, or a session that just went
        return
    # The new document may still be parsing, with an empty title.
    try:
        WebDriverWait(driver, timeout, poll_frequency=0.05).until(
            lambda d: d.execute_script("return document.readyState") != "loading"
        )
    except Exception:  # noqa: BLE001 - same, and a URL alone is still useful
        return


# Whether the selected browsing context is a frame. `window` and `top` are
# unforgeable; `window.self` is not (a page can run `self = top`).
IN_FRAME = "return window !== window.top"

# The selected browsing context's origin: the frame's when the session is in
# one, the top page's otherwise. Never gated on a frame check, which a hostile
# frame could forge.
ORIGIN_HERE = "return document.location.origin"


def _asked(driver, script: str):
    """The page's answer to ``script``, or None when it cannot answer."""
    try:
        return driver.execute_script(script)
    except Exception:  # noqa: BLE001 - a dialog or a dead session means not knowing
        return None


def in_frame(driver) -> bool:
    """Whether the session is switched into a frame. WebDriver has no command
    for it, so the page is asked. It is reported because a forgotten frame
    switch makes every later locator fail for a reason that looks nothing like
    the cause."""
    return bool(_asked(driver, IN_FRAME))


def frame_origin(driver) -> str:
    """The origin of the document a keystroke would reach: the selected
    browsing context's, where WebDriver's url is always the top page's.

    ``location.origin`` is computed from the context's URL, so a ``data:``,
    ``about:blank`` or ``srcdoc`` frame answers ``"null"`` whatever origin its
    document inherited, and a frame with a ``sandbox`` attribute answers its
    URL's origin. A failure or a non-string answer is ``""``. Neither ``"null"``
    nor ``""`` matches an allowed origin, so the leash refuses both.
    """
    found = _asked(driver, ORIGIN_HERE)
    return found if isinstance(found, str) else ""


def page_state(driver) -> dict:
    """Where the browser ended up, or the dialog that is open.

    A dialog refuses reading the url and title; raising would fail an action
    that succeeded (the click landed and opened a prompt).
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


# Which document the top page is: `performance.timeOrigin` is new for every
# document and kept by a same-document navigation (a fragment, `pushState`).
# Null in a frame, whose document is not the one `page_state` reports.
DOCUMENT = (
    "if (window !== window.top) return null;"
    "return {origin: performance.timeOrigin, url: location.href,"
    " title: document.title}"
)


def document(driver) -> dict | None:
    """Which document the top page is, before a call moves it, for `landed`.

    None (no wait) when it cannot be told: in a frame, with a dialog open, a
    script that fails or an answer of the wrong shape.
    """
    found = _asked(driver, DOCUMENT)
    if not isinstance(found, dict):
        return None
    origin, url, title = found.get("origin"), found.get("url"), found.get("title")
    if not isinstance(origin, int | float) or isinstance(origin, bool):
        return None
    if not isinstance(url, str) or not isinstance(title, str):
        return None
    return {"origin": origin, "url": url, "title": title}


def screen(url: str) -> tuple[str, str]:
    """``url``'s path and hash route: what a same-document move must change for
    a new title to be expected.

    The route is `urls.route_of`'s (``#!/a`` is ``/a``), with its own query:
    Penpot names the open file there (``#/workspace?file-id=``) and titles the
    page by it. Any other fragment, the URL's query and a trailing ``/`` leave
    the screen as it was, so a search box mirroring its text into ``?q=`` does
    not pay the wait on every keystroke. The cost: an app that routes by that
    query, or by ``#home``, is not waited for, and a hash-routed search box
    (``#/search?q=``) pays the wait.
    """
    try:
        path = urlsplit(url).path.rstrip("/")
    except ValueError:
        return url, ""
    route, query = route_of(url) or "", route_query(url)
    return path, route + ("?" + query if query else "")


def landed(driver, before: dict | None, timeout: float = TITLE_SETTLE) -> dict:
    """`page_state`, once a route the call moved to has had its title set.

    A same-document navigation (a fragment, `pushState`) is over as soon as the
    URL changes, and a single-page app sets ``document.title`` afterwards from
    its router. So when the `screen` moved and ``before``'s document is still
    the page's, the title is waited for until it is not ``before``'s, for at
    most ``timeout``.

    Nothing else waits: a new document, a URL on the same `screen`, a title
    already changed, a dialog, or ``before`` None. Expiring is an ordinary
    answer, and any failure means no wait, never a failed call.
    """
    state = page_state(driver)
    if (
        before is None
        or "dialog" in state
        or screen(state["url"]) == screen(before["url"])
        or state["title"] != before["title"]
    ):
        return state
    now = document(driver)
    if (
        now is None
        or now["origin"] != before["origin"]
        or screen(now["url"]) == screen(before["url"])
    ):
        return state
    # Kept its title, a dialog opened, or the session went: no longer waited.
    with contextlib.suppress(Exception):
        WebDriverWait(driver, timeout, poll_frequency=0.05).until(
            lambda d: d.title != before["title"]
        )
    return page_state(driver)


def wait_for_alert(driver, timeout: int = 30):
    """Wait for a JS dialog and return it."""
    return _waited(
        driver,
        EC.alert_is_present(),
        timeout,
        "no dialog (alert, confirm or prompt) was open",
    )


def accept_local_files(driver) -> None:
    """Let ``send_keys`` on a file input upload a file from this process: the
    local file detector ships the bytes to the node and hands the input the
    path they landed at."""
    driver.file_detector = LocalFileDetector()


def go(driver, url: str) -> None:
    """``driver.get(url)``, with a host that does not resolve said as the caller's.

    Every page a caller names is loaded through here. The browser's own words
    would be a 500 and quote the URL on Firefox; `Unresolved` names the host
    alone, and the browser's exception is dropped so no traceback carries it.
    An untrusted certificate is `Untrusted`, the same way.
    """
    try:
        driver.get(url)
    except InsecureCertificateException:
        raise Untrusted() from None
    except WebDriverException as exc:
        if not unresolved(exc):
            raise
    else:
        return
    raise Unresolved(_host(url)) from None


def _host(url: str) -> str | None:
    """The URL's host, or None when it has none or does not parse."""
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def ensure_url(
    driver, url: str, *, before_going: Callable[[], None] = lambda: None
) -> bool:
    """Navigate to ``url`` unless the browser is already there.

    A page is host, port, path, hash route, userinfo and both queries (the
    real one and the route's own); a fragment that is not a route is ignored.

    ``before_going`` runs only when it is about to navigate, immediately before
    ``go``; it may raise to refuse.
    """
    if same_page(driver.current_url, url):
        return False
    before_going()
    go(driver, url)
    return True


def full_page_size(driver) -> tuple[int, int]:
    """Full scrollable document size: the larger of ``body``'s and
    ``documentElement``'s, which disagree depending on the page's CSS."""
    return driver.execute_script(
        "return ["
        "Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),"
        "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
        "]"
    )
