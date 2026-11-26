"""The test doubles, one copy of each.

Each class answers a named production seam and carries the union of what the
test modules using it need, so a module split has one file to repoint.
"""

import asyncio
import inspect
import json
import threading
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import ClassVar, NamedTuple

import yaml
from mcp_types import Request, RequestParams
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ConfigDict, TypeAdapter
from selenium.common.exceptions import (
    NoSuchWindowException,
    UnexpectedAlertPresentException,
)

from kubed.selenium_flow.bidi import CHANNEL_DOWN, BidiError
from kubed.selenium_flow.core import actions as actions_module
from kubed.selenium_flow.core import browser as browser_module
from kubed.selenium_flow.faults import BidiUnavailable, SocketClosed
from kubed.selenium_flow.live.tunnel import Tunnel
from kubed.selenium_flow.redaction import Forms
from kubed.selenium_flow.telemetry import Telemetry
from kubed.selenium_flow.urls import origin_of, scope_origin
from kubed.selenium_flow.workspace.workspaces import Caller, Workspaces

MAIN = "main-1"


class FakeGrid:
    """Answers `Grid`: ``is_alive`` for liveness, ``sessions`` for its listing,
    ``files`` and ``read_file`` for the download listing.

    ``alive`` is the set of live session ids and ``checked`` every liveness
    question asked, each a command sent to that browser; ``listed`` counts the
    listings, and ``list_fails`` makes one fail as an unreachable Grid does.
    ``entries`` and ``data`` are the downloads and the bytes, and ``reads``
    every file read.
    """

    def __init__(self, entries=(), data=b"bytes"):
        self.alive = set()
        self.checked = []
        self.listed = 0
        self.list_fails = False
        self.entries = list(entries)
        self.data = data
        self.reads = []

    def is_alive(self, session_id):
        self.checked.append(session_id)
        return session_id in self.alive

    def sessions(self):
        self.listed += 1
        if self.list_fails:
            raise ConnectionError("grid unavailable")
        return [{"session_id": sid} for sid in sorted(self.alive)]

    def files(self, session_id):
        return list(self.entries)

    def read_file(self, session_id, name):
        self.reads.append((session_id, name))
        return self.data


class CountingGridServer:
    """A Grid on a real localhost socket that counts the TCP connections it
    accepts, so a test can prove two calls shared one.

    Answers what `Grid` and a reattached driver ask: a session's URL, its file
    listing, ``/status``; anything else is ``{"value": null}``. ``drop`` is how
    many of the next requests are read and then answered by closing the
    connection - a pooled connection meeting a Grid that restarted under it.
    ``requests`` is every (method, path) received, answered or dropped.
    ``cookie`` is a ``Set-Cookie`` every answer carries, and ``cookies`` the
    ``Cookie`` header each request arrived with.
    """

    def __init__(self):
        self.connections = 0
        self.requests = []
        self.drop = 0
        self.cookie = None
        self.cookies = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                fake.requests.append((self.command, self.path))
                fake.cookies.append(self.headers.get("Cookie"))
                if fake.drop:
                    fake.drop -= 1
                    self.close_connection = True
                    return
                body = json.dumps(fake.reply(self.path)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if fake.cookie:
                    self.send_header("Set-Cookie", fake.cookie)
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = _answer

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                accepted = super().get_request()
                fake.connections += 1
                return accepted

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    @staticmethod
    def reply(path):
        if path.endswith("/url"):
            return {"value": "https://example.test/"}
        if path.endswith("/se/files"):
            return {"value": {"files": []}}
        if path == "/status":
            return {"value": {"ready": True}}
        return {"value": None}

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


class FakeActions:
    """Answers `Actions`, two ways.

    Given a ``grid`` it is the holder `files` reads ``actions.grid`` from. Used
    as a flow's actions it records every tool call as (tool, session, kwargs) and
    fails on a named tool, or on the nth call (``fail_on``) with ``error``.
    """

    def __init__(self, grid=None, *, fail_on=None, error="boom"):
        self.grid = grid
        self.calls = []
        self.fail_on = fail_on or set()
        self.error = error

    def _record(self, tool, session_id, **kwargs):
        self.calls.append((tool, session_id, kwargs))
        if tool in self.fail_on or len(self.calls) in self.fail_on:
            raise RuntimeError(self.error)
        return {"url": f"https://example.test/{tool}", "title": tool.title()}

    def navigate(self, session_id, **kwargs):
        return self._record("navigate", session_id, **kwargs)

    def write(self, session_id, **kwargs):
        # The real one reads the field back and returns it, which is the leak
        # a guarded step has to close.
        return {
            **self._record("write", session_id, **kwargs),
            "value": kwargs.get("text"),
        }

    def interact(self, session_id, **kwargs):
        return self._record("interact", session_id, **kwargs)

    def extract(self, session_id, **kwargs):
        return {**self._record("extract", session_id, **kwargs), "text": "the heading"}

    def screenshot(self, session_id, **kwargs):
        return {
            **self._record("screenshot", session_id, **kwargs),
            "image": "A" * 5000,
        }

    def open_session(self, session_id, **kwargs):  # pragma: no cover - refused
        return self._record("open_session", session_id, **kwargs)

    def check_tabs(self, session_id):
        return {}


class RecordingActions:
    """Counts what was opened and ended, without opening anything.

    Records the *settings* each browser was opened with as well as the count,
    because "a refresh reopens the same browser it had" is a behaviour several
    modules depend on and a bare counter cannot express it.
    """

    def __init__(self):
        self.opened = 0
        self.opened_urls = []
        self.opened_settings = []
        # Where each open's version came from (`session_caps.build`): beside
        # the settings, never one of them.
        self.opened_version_from = []
        self.closed = []
        self.grid = FakeGrid()
        # What `tabs_listed` answers, or raises when it is an exception.
        self.listing: list[dict] | BaseException = []

    def tabs_listed(self, session_id):
        if isinstance(self.listing, BaseException):
            raise self.listing
        return list(self.listing)

    def open_session(self, url=None, **kwargs):
        self.opened += 1
        session_id = f"generated-{self.opened}"
        self.opened_urls.append(url)
        self.opened_version_from.append(kwargs.pop("version_from", "call"))
        self.opened_settings.append(dict(kwargs))
        self.grid.alive.add(session_id)
        # What a real open reports for the workspace only: what was sent and
        # what ran (`actions.OPEN_PRIVATE`). `open_browser` keeps them off
        # what a caller is told.
        sent = [
            {
                "key": "browserName",
                "value": kwargs.get("browser") or "chrome",
                "sources": ["option"],
            }
        ]
        ran = {"version": "100.0", "node": f"node-{self.opened}", "live_view": True}
        return {
            "session_id": session_id,
            "url": url or "about:blank",
            "sent": sent,
            "ran": ran,
        }

    def end_browser(self, session_id):
        self.closed.append(session_id)
        self.grid.alive.discard(session_id)
        return {"success": True, "session_id": session_id}

    def check_tabs(self, session_id):
        return {}


class TabbedActions(RecordingActions):
    """`RecordingActions` for the tab checks wrapped around every call.

    ``checks`` is what each `check_tabs` answers next, in order: a dict, or an
    exception that is raised; ``{}`` once it is empty. ``calls`` names every
    method asked, in order, so a test reads where the checks fell. ``at`` is
    the page the session is on: where it opened, then where a check moved it;
    ``pages`` is the same by Grid id, for several browsers at once.
    ``do`` answers ``result``, or raises ``raises``; ``page`` answers the
    browser's page, ``write`` that page and the value read back, and ``tab``
    ``{"tabs": listing, "tab": ...}`` as a ``list`` does, with the current page.
    """

    def __init__(self):
        super().__init__()
        self.checks: list = []
        self.calls: list[str] = []
        self.result: dict = {}
        self.raises: BaseException | None = None
        self.at: dict = {"url": "https://example.test/", "title": "Example"}
        self.pages: dict[str, dict] = {}

    def open_session(self, url=None, **kwargs):
        opened = super().open_session(url, **kwargs)
        self.at = self.pages[opened["session_id"]] = {"url": opened["url"], "title": ""}
        return opened

    def _on(self, session_id):
        return dict(self.pages.get(session_id, self.at))

    def check_tabs(self, session_id):
        self.calls.append("check_tabs")
        found = self.checks.pop(0) if self.checks else {}
        if isinstance(found, BaseException):
            raise found
        if "tab" in found:
            self.at = {"url": found.get("url"), "title": found.get("title")}
            self.pages[session_id] = self.at
        return dict(found)

    def do(self, session_id, **kwargs):
        self.calls.append("do")
        if self.raises is not None:
            raise self.raises
        return dict(self.result)

    def page(self, session_id):
        self.calls.append("page")
        return self._on(session_id)

    def write(self, session_id, text=None, **kwargs):
        self.calls.append("write")
        return {**self._on(session_id), "value": text}

    # The tools a flow step names, answered as `do` is.
    navigate = interact = extract = do

    def tab(self, session_id, action="list", id=None, url=None):
        self.calls.append("tab")
        current = next((t["tab"] for t in self.listing if t["current"]), "t1")
        return {"tabs": list(self.listing), "tab": current, **self.at}


def untabbed(server):
    """``server``, whose calls find no tab to follow: for a server whose Grid
    is never dialled, so the check around every call (`Workspaces.followed`)
    would be the only thing that dials it. Returns ``server``."""
    server.actions.check_tabs = lambda session_id: {}
    return server


def patch_os(monkeypatch, name, replacement):
    """Replace ``os.<name>`` everywhere `pathlib` reaches it, for a storage fault.

    A listing is two names until 3.13: `Path.iterdir` uses ``listdir`` before it
    and ``scandir`` from it.
    """
    import os
    monkeypatch.setattr(os, name, replacement)


class FakeClock:
    """Stands in for the `time` module `flows.run` reads: ``monotonic`` advances
    a second every time it is read."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 1.0
        return self.now


class Clock:
    """A ``clock`` argument a test moves by hand: calling it returns ``now``."""

    def __init__(self, now=1_791_500_000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeRedis:
    """Enough of redis-py for the store: get, mget, set with ex, delete, persist,
    expire, ping,
    scan_iter (bytes keys, as redis-py returns them) and pipeline.

    ``data`` holds the bytes by key, ``expiries`` the ``ex`` each was set with,
    and ``calls`` the reads, so a test can count round trips. An unreachable
    server (``reachable=False``) fails ``ping``.
    """

    def __init__(self, reachable=True):
        self.data = {}
        self.expiries = {}
        self.reachable = reachable
        self.calls = []
        self.watching = set()

    def ping(self):
        if not self.reachable:
            raise ConnectionError("nope")
        return True

    def get(self, key):
        self.calls.append("get")
        return self.data.get(key)

    def mget(self, keys):
        self.calls.append("mget")
        return [self.data.get(key) for key in keys]

    def set(self, key, value, ex=None):
        self.data[key] = value.encode() if isinstance(value, str) else value
        self.expiries[key] = ex

    def delete(self, key):
        self.data.pop(key, None)

    def persist(self, key):
        if key in self.data:
            self.expiries[key] = None

    def expire(self, key, seconds):
        if key in self.data:
            self.expiries[key] = seconds

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return [key.encode() for key in list(self.data) if key.startswith(prefix)]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    """redis-py's optimistic transaction: WATCH reads at once, MULTI buffers,
    EXEC refuses with WatchError if a watched key was written since.

    ``fake.interfere`` runs just before EXEC — another replica's write landing
    between this one's read and its write. ``fake.watching`` holds the keys
    that EXEC is about to write, so it can pick its moment.
    """

    def __init__(self, fake):
        self.fake = fake
        self.watched = {}
        self.queued = []
        self.buffering = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.reset()

    def reset(self):
        self.watched, self.queued, self.buffering = {}, [], False

    def watch(self, key):
        self.watched[key] = self.fake.data.get(key)

    def unwatch(self):
        self.watched = {}

    def get(self, key):
        return self.fake.get(key)

    def multi(self):
        self.buffering = True

    def set(self, key, value, ex=None):
        assert self.buffering, "a write outside MULTI is not a transaction"
        self.queued.append((key, value, ex))

    def execute(self):
        from redis.exceptions import WatchError

        interfere = getattr(self.fake, "interfere", None)
        self.fake.watching = set(self.watched)
        if interfere:
            interfere()
        changed = any(self.fake.data.get(k) != v for k, v in self.watched.items())
        queued = self.queued
        self.reset()
        if changed:
            raise WatchError("watched key changed")
        for key, value, ex in queued:
            self.fake.set(key, value, ex=ex)
        return [True] * len(queued)


class FakeChannel:
    """What `Channels.open_channel` yields: a `bidi.Channel` answering the site
    data commands from its state, every one logged as ``(method, params)``.

    ``cookies`` are in the jar from the start (BiDi's shape); what is set reads
    back, except a ``drop``ped name — accepted without an error and never kept,
    as Chrome does to SameSite=None without Secure — and a ``refuse_cookie``
    name, which answers BidiError. ``refuse`` and ``down`` are methods that
    answer BidiError and raise BidiUnavailable. ``value``, ``workers``,
    ``stuck`` and ``fail`` shape the page script; ``order`` collects "cookie"
    for each cookie set. ``at`` maps a tab to the origin it stands on.
    ``tree`` is what ``getTree`` answers, a list of ``{"context", "url"}``;
    when None, the windows of ``windows`` (a driver's ``pages``) in their
    order, or the one main context without them. ``titles`` maps a context to
    the title a ``document.title`` evaluation reads there.
    """

    def __init__(
        self,
        order=None,
        cookies=(),
        refuse=(),
        down=(),
        refuse_cookie=(),
        drop=(),
        value=None,
        workers=(),
        stuck=False,
        fail=None,
        tree=None,
        titles=None,
    ):
        self.log, self.posts, self.handlers = [], [], {}
        self.cookies, self.set = list(cookies), []
        self.refuse, self.down = set(refuse), set(down)
        self.refuse_cookie, self.drop, self.order = set(refuse_cookie), set(drop), order
        self.value, self.workers, self.stuck, self.fail = (
            value, set(workers), stuck, fail,
        )
        self.navigated, self.at, self.tabs = [], {}, 0
        self.bodies = {}  # request id -> network.getData's answer
        self.tree = tree
        self.windows = None  # a driver's ``pages``: spare tabs appear there too
        self.titles = dict(titles or {})
        self.expression = None
        self.ran = False  # whether the page would have evaluated the expression

    def params(self, method):
        """The params of the last call to ``method``."""
        return [p for m, p in self.log if m == method][-1]

    def call(self, method, params=None, *, timeout=None):
        params = params or {}
        self.log.append((method, params))
        if method in self.down:
            raise BidiUnavailable(CHANNEL_DOWN)
        if method in self.refuse:
            raise BidiError("unknown error", "refused")
        return getattr(self, "_" + method.replace(".", "_"))(params)

    def post(self, method, params=None):
        self.posts.append((method, params))

    @contextmanager
    def listen(self, events, handler, *, contexts=None, name="channel"):
        events = list(events)
        self.log.append(("session.subscribe", {"events": events, "contexts": contexts}))
        for event in events:
            self.handlers[event] = handler
        try:
            yield None
        finally:
            self.log.append(
                ("session.unsubscribe", {"events": events, "contexts": contexts})
            )

    def _storage_getCookies(self, params):
        held = [
            {"name": c["name"], "domain": c["domain"], "path": c.get("path", "/")}
            for c in self.set
            if c["name"] not in self.drop
            and not (c.get("sameSite") == "none" and not c.get("secure"))
        ]
        return {"cookies": self.cookies + held}

    def _storage_setCookie(self, params):
        cookie = params["cookie"]
        if cookie["name"] in self.refuse_cookie:
            raise BidiError("unable to set cookie")
        self.set.append(cookie)
        if self.order is not None:
            self.order.append("cookie")
        return {}

    def _browsingContext_getTree(self, params):
        if self.tree is not None:
            return {"contexts": [{**c, "children": []} for c in self.tree]}
        if self.windows is not None:
            return {
                "contexts": [
                    {"context": handle, "url": page[0], "children": []}
                    for handle, page in self.windows.items()
                ]
            }
        return {"contexts": [{"context": MAIN, "children": []}]}

    def _browsingContext_activate(self, params):
        return {}

    def _browsingContext_create(self, params):
        self.tabs += 1
        if self.windows is not None:
            self.windows[f"spare-{self.tabs}"] = ("about:blank", "")
        return {"context": f"spare-{self.tabs}"}

    def _browsingContext_navigate(self, params):
        self.navigated.append((params["context"], params["url"]))
        self.at[params["context"]] = origin_of(params["url"])
        return {}

    def _browsingContext_close(self, params):
        if self.windows is not None:
            self.windows.pop(params["context"], None)
        return {}

    def _network_addIntercept(self, params):
        return {"intercept": "i-1"}

    def _network_removeIntercept(self, params):
        return {}

    def _network_getData(self, params):
        try:
            body = self.bodies[params["request"]]
        except KeyError:
            raise BidiError("no such network data", "gone") from None
        if isinstance(body, str):  # a BiDi error code the browser answers with
            raise BidiError(body, "text from the browser, with a path /tmp/x")
        return body

    def _script_evaluate(self, params):
        if params["expression"] == "document.title":
            title = self.titles.get(params["target"]["context"], "")
            return {"type": "success", "result": {"type": "string", "value": title}}
        expression = self.expression = params["expression"]
        if self.fail:
            return {"type": "exception", "exceptionDetails": {"text": self.fail}}
        origin = (
            "https://before.test" if self.stuck else self.at[params["target"]["context"]]
        )
        page = {"spare": origin not in self.workers, "origin": origin}
        # No JS runs here (tests/test_js_behaviour.py runs the real script):
        # the expression runs when the page is ours and the script guards it
        # with the origin the tab is really on.
        self.ran = page["spare"] and (
            f"location.origin === {json.dumps(origin)}" in expression
        )
        if self.ran:
            page["value"] = self.value
        return {
            "type": "success",
            "result": {"type": "string", "value": json.dumps(page)},
        }


class ScriptedElement:
    """What a locator finds: it records what it is asked to do on the driver's
    log, so the order of a gesture is assertable alongside the driver's own
    calls. ``text`` and ``html`` are what a read returns; ``tag`` is what
    ``tag_name`` answers."""

    def __init__(self, log, text="the text", html="<b>the text</b>", value=""):
        self.log, self.text, self.html, self.value = log, text, html, value
        self.tag = "input"
        self.screenshot_as_base64 = ""
        self.fail_on = {}
        # Runs after a click: a click that opens or closes a tab.
        self.on_click = None

    @property
    def tag_name(self):
        self._do("tag_name")
        return self.tag

    def _do(self, name, *args):
        self.log.append(("element", name, *args))
        if name in self.fail_on:
            raise self.fail_on[name]

    def is_displayed(self):
        self._do("is_displayed")
        return True

    def is_enabled(self):
        self._do("is_enabled")
        return True

    def click(self):
        self._do("click")
        if self.on_click is not None:
            self.on_click()

    def clear(self):
        self._do("clear")

    def send_keys(self, *keys):
        self._do("send_keys", *keys)

    def get_attribute(self, name):
        self._do("get_attribute", name)
        return self.html if name == "innerHTML" else self.value


class _ScriptedAlert:
    def __init__(self, driver):
        self._driver = driver

    @property
    def text(self):
        self._driver.log.append(("alert", "text"))
        return self._driver.dialog

    def accept(self):
        self._driver.log.append(("alert", "accept"))
        self._driver.__dict__["dialog"] = None

    def dismiss(self):
        self._driver.log.append(("alert", "dismiss"))
        self._driver.__dict__["dialog"] = None

    def send_keys(self, keys):
        self._driver.log.append(("alert", "send_keys", keys))


class _ScriptedSwitch:
    def __init__(self, driver):
        self._driver = driver

    @property
    def alert(self):
        self._driver.log.append(("switch_to", "alert"))
        return _ScriptedAlert(self._driver)

    def frame(self, target):
        self._driver.log.append(("switch_to", "frame", target))
        self._driver.__dict__["frames"] += 1

    def window(self, handle):
        """As a real browser does, even to the window it is on: the session
        lands at the top page, out of any frame (measured on Chrome and
        Firefox)."""
        driver = self._driver
        driver.log.append(("switch_to", "window", handle))
        if handle not in driver.pages:
            raise NoSuchWindowException("no such window")
        with suppress(NoSuchWindowException):
            driver._page()  # keep what the window being left showed
        driver.__dict__["current"], driver.__dict__["_seen"] = handle, None
        driver.__dict__["frames"] = 0

    def new_window(self, kind="tab"):
        driver = self._driver
        number = max(
            [driver.opened, *(int(h[1:]) for h in driver.pages if h[1:].isdigit())]
        )
        handle = f"w{number + 1}"
        driver.__dict__["opened"] = number + 1
        driver.log.append(("switch_to", "new_window", kind))
        with suppress(NoSuchWindowException):
            driver._page()
        driver.pages[handle] = ("about:blank", "")
        driver.__dict__["current"], driver.__dict__["_seen"] = handle, None
        driver.__dict__["frames"] = 0

    def default_content(self):
        self._driver.log.append(("switch_to", "default_content"))
        self._driver.__dict__["frames"] = 0

    def parent_frame(self):
        self._driver.log.append(("switch_to", "parent_frame"))
        self._driver.__dict__["frames"] = max(0, self._driver.frames - 1)

    @property
    def active_element(self):
        self._driver.log.append(("switch_to", "active_element"))
        return self._driver.focused


class ScriptedDriver:
    """A driver that does nothing and remembers everything.

    ``log`` holds every call and every attribute read or write, in order, as
    tuples: ``("get", url)``, ``("current_url",)``, ``("set_window_size", w, h)``,
    ``("element", "click")`` for what a found element was asked. A test asserts
    on the log's order, or on whether an entry is there at all.

    ``dialog`` is the text of an open native dialog, or None: while it is open,
    reading ``current_url`` or ``title`` raises ``UnexpectedAlertPresentException``
    as a real driver does. ``scripts`` maps a substring of a script to what
    ``execute_script`` returns for it, first match winning, else None. ``element``
    is what every locator finds, and ``focused`` what ``switch_to.active_element``
    answers: the same element unless a test sets another.

    ``pages`` maps each open window handle to its ``(url, title)`` and
    ``current`` is the handle the driver is on: a test opens a popup by adding
    a handle to ``pages`` and closes one with ``close()``. Reading the url or
    title of a closed current window raises ``NoSuchWindowException``, as
    does switching to a window that is not open.

    ``frames`` is how deep in frames the session is: ``switch_to.frame`` goes
    one deeper, ``switch_to.window`` (to any window, its own too) back to the
    top, and the page's frame check (`browser.in_frame`) answers it unless a
    ``scripts`` entry answers first.
    """

    PNG = (
        "iVBORw0KGgoAAAANSUhEUgAAAAMAAAAEAQMAAACTPww9AAAAA1BMVEUAAACnej3aAAAAAXRSTlMA"
        "QObYZgAAAApJREFUCNdjYAAAAAIAAeIhvDMAAAAASUVORK5CYII="
    )

    def __init__(
        self, url="https://example.test/", title="Example", scripts=None,
        dialog=None, window=(1280, 800), session_id="abc",
    ):
        d = self.__dict__
        d["log"] = []
        d["pages"] = {"w1": (url, title)}
        d["_url"], d["_title"], d["_seen"] = url, title, (url, title)
        d["current"], d["opened"] = "w1", 1
        d["frames"] = 0
        d["dialog"] = dialog
        d["scripts"] = dict(scripts or {})
        d["window"] = {"width": window[0], "height": window[1]}
        d["session_id"] = session_id
        d["element"] = ScriptedElement(d["log"])
        d["focused"] = d["element"]
        d["switch_to"] = _ScriptedSwitch(self)
        d["screenshot_data"] = self.PNG

    def __setattr__(self, name, value):
        self.log.append(("set", name))
        self.__dict__[name] = value

    def _page(self):
        """The current window's ``(url, title)``.

        ``_url`` and ``_title`` are the current window's page, which subclasses
        move by writing ``__dict__``; ``pages`` is what a test edits. Whichever
        changed since the last read wins, ``pages`` when both did.
        """
        d = self.__dict__
        if d["current"] not in d["pages"]:
            raise NoSuchWindowException("window closed")
        theirs = d["pages"][d["current"]]
        mine = theirs if theirs != d["_seen"] else (d["_url"], d["_title"])
        d["_url"], d["_title"] = mine
        d["pages"][d["current"]] = d["_seen"] = mine
        return mine

    @property
    def current_url(self):
        self.log.append(("current_url",))
        if self.dialog is not None:
            raise UnexpectedAlertPresentException("dialog open")
        return self._page()[0]

    @property
    def title(self):
        self.log.append(("title",))
        if self.dialog is not None:
            raise UnexpectedAlertPresentException("dialog open")
        return self._page()[1]

    @property
    def window_handles(self):
        self.log.append(("window_handles",))
        return list(self.pages)

    @property
    def current_window_handle(self):
        self.log.append(("current_window_handle",))
        if self.current not in self.pages:
            raise NoSuchWindowException("window closed")
        return self.current

    def close(self):
        self.log.append(("close",))
        self.pages.pop(self.current, None)

    def get(self, url):
        self.log.append(("get", url))
        self._page()
        self.__dict__["_url"] = url

    def execute_script(self, script, *args):
        self.log.append(("execute_script", script))
        for needle, value in self.scripts.items():
            if needle in script:
                return value
        if script == browser_module.IN_FRAME:
            return self.frames > 0
        return None

    def execute(self, *args, **kwargs):
        self.log.append(("execute", *args))
        return {"value": None}

    def find_element(self, by=None, value=None):
        self.log.append(("find_element", by, value))
        return self.element

    def find_elements(self, by=None, value=None):
        self.log.append(("find_elements", by, value))
        return [self.element]

    def get_window_size(self):
        self.log.append(("get_window_size",))
        return dict(self.window)

    def set_window_size(self, width, height):
        self.log.append(("set_window_size", width, height))
        self.window.update(width=width, height=height)

    def set_page_load_timeout(self, seconds):
        self.log.append(("set_page_load_timeout", seconds))

    def set_script_timeout(self, seconds):
        self.log.append(("set_script_timeout", seconds))

    def get_screenshot_as_base64(self):
        self.log.append(("get_screenshot_as_base64",))
        return self.screenshot_data

    def names(self):
        """Just the names in the log, for an order assertion."""
        return [entry[0] for entry in self.log]


class CountingDriver(ScriptedDriver):
    """A `ScriptedDriver` that also counts how often it was reattached to.

    Installed as the Grid's ``reconnect`` (``grid.reconnect = driver.reconnect``),
    it puts a ``("reconnect", session_id)`` entry on the same log as every driver
    call, so an action's whole road - reattach, maybe navigate, wait, act, read
    the page - is one sequence a test can compare against what it was before.
    """

    def __init__(self, **settings):
        super().__init__(**settings)
        self.__dict__["reconnects"] = 0

    def reconnect(self, session_id):
        self.__dict__["reconnects"] += 1
        self.log.append(("reconnect", session_id))
        return self


class FakePeer:
    """Answers `live.tunnel.Peer`: one end of a WebSocket, played by a test.

    ``feed`` is what ``receive`` returns next and ``hang_up`` makes it raise
    `SocketClosed`; ``sent`` is everything ``send`` was given and ``closed`` the
    code and reason ``close`` was called with. ``next_sent`` waits for the next
    send, so a test reads the tunnel's side of a conversation in order.
    """

    def __init__(self):
        self.sent = []
        self.closed = None
        self._incoming = asyncio.Queue()
        self._outgoing = asyncio.Queue()

    def feed(self, data):
        self._incoming.put_nowait(data)

    def hang_up(self, code=1000, reason=""):
        self._incoming.put_nowait(SocketClosed(code, reason))

    async def receive(self):
        item = await self._incoming.get()
        if isinstance(item, BaseException):
            self._incoming.put_nowait(item)
            raise item
        return item

    async def send(self, data):
        if self.closed is not None:
            raise SocketClosed(*self.closed)
        self.sent.append(data)
        self._outgoing.put_nowait(data)

    async def close(self, code=1000, reason=""):
        if self.closed is None:
            self.closed = (code, reason)

    async def next_sent(self):
        return await asyncio.wait_for(self._outgoing.get(), 1)


# ---- what `Workspaces` is built over -----------------------------------------


def caller(name="bot"):
    """A query-string caller naming ``name``."""
    return Caller(name, "query")


class SessionGrid:
    """Answers the `Grid` questions `Workspaces` asks: liveness and idle timeout.

    ``alive`` is the set of live ids and ``timeouts`` each one's idle timeout;
    ``timeout_fails`` makes the timeout unreadable. Reattaching is refused.
    """

    def __init__(self):
        self.alive = set()
        self.timeouts = {}
        self.timeout_fails = False

    def is_alive(self, sid):
        return sid in self.alive

    def sessions(self):
        return [{"session_id": sid} for sid in sorted(self.alive)]

    def session_timeout(self, sid):
        if self.timeout_fails:
            raise ConnectionError("status unavailable")
        return self.timeouts.get(sid)

    def reconnect(self, sid):
        raise ConnectionError("not in this test")


class WorkspaceActions:
    """Answers the open and end `Workspaces` asks of `Actions`: every browser is
    ``grid0001``, ``grid0002``... with a 300 s idle timeout. ``quit_fails`` makes
    ending one fail as an unreachable Grid does."""

    def __init__(self):
        self.grid = SessionGrid()
        self.n = 0
        self.quit_fails = False

    def open_session(self, *, on_created=None, **kwargs):
        self.n += 1
        sid = f"grid{self.n:04d}"
        self.grid.alive.add(sid)
        self.grid.timeouts.setdefault(sid, 300)
        return {
            "session_id": sid,
            "browser": kwargs.get("browser") or "chrome",
            "url": "about:blank",
            "title": "",
            "width": 1280,
            "height": 900,
        }

    def end_browser(self, sid):
        if self.quit_fails:
            raise ConnectionError("grid unreachable")
        self.grid.alive.discard(sid)

    def check_tabs(self, session_id):
        return {}


class PageActions(WorkspaceActions):
    """`WorkspaceActions` whose browser stands on one page."""

    def page(self, session_id):
        return {"url": "https://a.test/", "title": "A"}


class Bus:
    def __init__(self, published):
        self.published = published

    def publish(self, event):
        self.published.append(event)


class FakeMonitor:
    """Records `calling` / `called` and what the bus was handed; holds no connection unless
    a test gives it one."""

    def __init__(self):
        self.touched, self.published = [], []
        self.bus = Bus(self.published)
        self.held = None
        self.loop = None
        self.owed: set[str] = set()  # sessions that owe capture
        self.attached: list = []

    def keep(self, *a, **k):
        pass

    def calling(self, session_id):
        self.touched.append(("calling", session_id))

    def called(self, session_id):
        self.touched.append(("called", session_id))

    def backing_off(self, session_id):
        return False

    def connection(self, session_id):
        return self.held

    def owes(self, session_id, reason):
        return reason == "capture" and session_id in self.owed

    def capturing(self, session_id):
        return self.held is not None

    def attach(self, workspace, session_id, reason, **kwargs):
        self.attached.append((workspace, session_id, reason, kwargs.get("browser")))

    def opened(self, *args, **kwargs):
        pass

    def ended(self, *args, **kwargs):
        return True


class Anywhere:
    """A catalogue holding ``demo/password``, usable on any page."""

    def entry(self, name):
        return {"name": name, "keys": ["password"], "allowed_urls": []}

    def allows(self, name, url):
        return True

    def value(self, name, key):
        return "hunter2"


def build(capture, monitor, actions=None):
    """Workspaces over ``monitor`` and ``capture`` with a browser open in ``ws``
    (attached to ``capture`` when there is one), and the monitor's record of
    the open cleared."""
    workspaces = Workspaces(actions or PageActions(), monitor=monitor, capture=capture)
    workspaces.open_browser(Caller("ws", "query"))
    if capture is not None:
        sid = workspaces.store.get("ws").session_id
        capture.attached("ws", sid, "chrome", "top-1", again=False)
    monitor.touched.clear()
    monitor.published.clear()
    return workspaces


class FakeWorkspaces:
    """Answers what the file listings ask of `Workspaces`: ``browser`` is the
    Grid id of the workspace's live browser, or "" when it has none."""

    def __init__(self, browser="abc"):
        self._browser = browser

    def name(self):
        return "desktop"

    def browser(self, name):
        return self._browser

    def resolve(self, name):
        return self._browser


# ---- MCP extension methods -----------------------------------------------------


class ExtensionParams(RequestParams):
    model_config = ConfigDict(extra="allow")


async def request(client, method, **params):
    """Send ``method``, a method no MCP client knows, with ``params``."""
    return await client.session.send_request(
        Request(method=method, params=ExtensionParams(**params)), TypeAdapter(dict)
    )


def plugin_server(root, files: dict, name: str = "t"):
    """A `QuickMCP` named ``name`` serving a plugin of ``files`` written under
    ``root``, as `add_plugin` serves selenium-flow's own: for the rules a
    plugin is read by, on content the shipped plugin does not have."""
    from kubed.mcp import QuickMCP

    for path, body in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8")
    mcp = QuickMCP(name)
    mcp.add_plugin(root)
    return mcp


def header(text: str) -> object:
    """The YAML front matter of a Markdown document."""
    lines = text.splitlines()
    end = next(i for i, line in enumerate(lines[1:], start=1) if line.rstrip() == "---")
    return yaml.safe_load("\n".join(lines[1:end]))


# ---- secrets ----------------------------------------------------------------


class FakeCatalogue:
    """Answers `Forms.of` from ``{origin: {(name, key): value}}``, and counts.

    ``calls`` holds the reads that can touch a value; ``revisions`` counts the
    cheap `revision()` calls separately.
    """

    def __init__(self, scopes: dict, *, gate: threading.Event | None = None):
        self.scopes = scopes
        self.calls: dict = {"forms_for": [], "leashed": 0}
        self.revisions = 0
        self._revision = 1
        self.gate = gate
        self.fail_forms_for = False
        self.fail_leashed = False

    def rotate(self, scopes: dict) -> None:
        self.scopes = scopes
        self._revision += 1

    def revision(self) -> int:
        self.revisions += 1
        return self._revision

    def _forms(self, secrets_: dict) -> Forms:
        forms = Forms()
        for ident, value in secrets_.items():
            forms = forms | Forms.of(value, ident)
        return forms

    def forms_for(self, origin: str) -> Forms:
        self.calls["forms_for"].append(origin)
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail_forms_for:
            raise OSError("cannot read alpha-11")
        return self._forms(self.scopes.get(origin, {}))

    def leashed(self) -> Forms:
        self.calls["leashed"] += 1
        if self.fail_leashed:
            raise OSError("cannot read alpha-11 either")
        every: dict = {}
        for scoped in self.scopes.values():
            every.update(scoped)
        return self._forms(every)


class BindingCatalogue(FakeCatalogue):
    """A `FakeCatalogue` a bound write can type from: the secrets of the origin
    ``on``, each allowed there and nowhere else."""

    def __init__(self, scopes: dict, *, on: str, **kwargs):
        super().__init__(scopes, **kwargs)
        self.on = on

    def entry(self, name):
        return {"name": name, "keys": ["password"], "allowed_urls": [self.on]}

    def allows(self, name, url):
        return scope_origin(url) == self.on

    def value(self, name, key):
        return self.scopes[self.on][(name, key)]


class Vault:
    """Just enough catalogue to hand `secrets.bind` one restricted value."""

    def __init__(self, value="hunter2"):
        self.secret = value

    def entry(self, name):
        return {"name": name, "keys": ["password"], "allowed_urls": [], "restricted": True}

    def allows(self, name, url):
        return True

    def value(self, name, key):
        return self.secret


# ---- the monitor's connections ------------------------------------------------


class FakeSubscriber:
    """What `FakeConnection.listen` returns: a handler for some events."""

    def __init__(self, events, handler, on_drop=None):
        self.events, self.handler, self.on_drop = frozenset(events), handler, on_drop
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeConnection:
    """Stands in for `bidi.Connection`: records what the monitor asked of it.

    ``refuse`` makes every open raise ConnectionError, ``hang_on_subscribe``
    makes every subscribe and ``hang_on_open`` every open wait until ``gate``
    is set (forever without one); conftest's `fresh_connections` resets them
    around every test."""

    made: ClassVar[list] = []
    refuse: ClassVar[bool] = False
    hang_on_subscribe: ClassVar[bool] = False
    hang_on_open: ClassVar[bool] = False
    gate: ClassVar[asyncio.Event | None] = None

    def __init__(self, url, *, on_close=None):
        self.url, self.on_close = url, on_close
        self.is_open = False
        self.dropped = False
        self.subscribed = []
        self.collectors = []
        self.listeners = []
        self.commands = []
        self.flushed = 0
        self.closed = 0
        self.closed_clean = False
        self._subscriptions = set()
        self._collectors = set()
        FakeConnection.made.append(self)

    @classmethod
    def reset(cls):
        cls.made, cls.refuse, cls.hang_on_subscribe = [], False, False
        cls.hang_on_open, cls.gate = False, None

    async def open(self):
        if FakeConnection.hang_on_open:
            await (FakeConnection.gate or asyncio.Event()).wait()
        if FakeConnection.refuse:
            raise ConnectionError("refused")
        self.is_open = True

    async def command(self, method, params=None, *, timeout=5.0):
        self.commands.append((method, params))
        if method == "browsingContext.getTree":
            return {"contexts": [{"context": "top-1"}]}
        return {}

    def listen(self, events, handler, *, name, limit=1000, on_drop=None):
        subscriber = FakeSubscriber(events, handler, on_drop)
        self.listeners.append(subscriber)
        return subscriber

    async def subscribe(self, events, contexts=None):
        if FakeConnection.hang_on_subscribe:
            await (FakeConnection.gate or asyncio.Event()).wait()
        self.subscribed.append(list(events))
        subscription = f"sub-{len(self.subscribed)}"
        self._subscriptions.add(subscription)
        return subscription

    async def add_collector(self, params):
        self.collectors.append(params)
        self._collectors.add("col-1")
        return "col-1"

    async def flush(self):
        self.flushed += 1

    async def close(self):
        if self.is_open and (self._subscriptions or self._collectors):
            self._subscriptions.clear()  # what `Connection.close` unsubscribes
            self._collectors.clear()  # and the collectors it removes
            self.closed_clean = True
        self.is_open = False
        self.closed += 1

    def drop(self):
        self.is_open, self.dropped = False, True
        self.on_close()

    async def emit(self, method, params):
        for subscriber in self.listeners:
            if method in subscriber.events and not subscriber.cancelled:
                result = subscriber.handler(method, params)
                if inspect.isawaitable(result):
                    await result


class FakeSink:
    """Stands in for `Capture`: records every call the monitor makes."""

    def __init__(self):
        self.calls = []

    def attached(self, workspace, session_id, browser, top, *, again):
        self.calls.append(("attached", workspace, session_id, browser, top, again))

    def ingest(self, workspace, session_id, method, params):
        self.calls.append(("ingest", workspace, session_id, method, params))

    def replayed(self, workspace, session_id):
        self.calls.append(("replayed", workspace, session_id))

    def detached(self, workspace, session_id):
        self.calls.append(("detached", workspace, session_id))

    def lost(self, workspace, session_id):
        self.calls.append(("lost", workspace, session_id))


# ---- live view ----------------------------------------------------------------


def held(tunnels, workspace, who, mode, grid_id="g1"):
    """A viewer of ``workspace`` in ``mode``, asked through the registry as the
    tunnel asks once it pipes. With no call running, Control is requested and
    the grant lands once the pages are followed: run here to its
    end on a loop of its own, since the callers are plain tests."""
    tunnel = Tunnel(FakePeer(), who=who, workspace=workspace, session="s", grace=1)
    tunnels.add(grid_id, tunnel)
    if mode == "control":

        async def granted():
            tunnel.mode = tunnel.on_ask()
            episode = tunnels.controls._episodes.get(workspace)
            if episode is not None and episode.pending is not None:
                await asyncio.wait({episode.pending})

        asyncio.run(granted())
    return tunnel


# ---- drivers ------------------------------------------------------------------


class FocusedDriver(ScriptedDriver):
    """A driver whose focused element is answered in turn, one per context."""

    def __init__(self, in_frame=False, **kw):
        super().__init__(**kw)
        self.__dict__["answers"] = []
        self.__dict__["in_frame"] = in_frame

    def execute_script(self, script, *args):
        if script == actions_module.FOCUSED:
            self.log.append(("focused",))
            return self.answers.pop(0) if self.answers else None
        if "window.top" in script:
            self.log.append(("in_frame",))
            return self.in_frame
        return super().execute_script(script, *args)


def element(log, tag="textarea"):
    found = ScriptedElement(log)
    found.tag = tag
    return found


class KeyDriver:
    """A driver to send keys at: what has focus is the driver itself, and
    ``sent`` is every combination it was sent."""

    current_url = "https://example.test/"
    title = "t"

    def __init__(self):
        self.sent = []

    @property
    def switch_to(self):
        # press_key with no selector sends to what has focus.
        return SimpleNamespace(active_element=self)

    def send_keys(self, *keys):
        self.sent.append("".join(keys))

    def execute_script(self, *_):
        return False


class Recorded(NamedTuple):
    """A `Telemetry` on private providers, and what they recorded."""

    telemetry: Telemetry
    spans: InMemorySpanExporter
    metrics: InMemoryMetricReader


def recorded() -> Recorded:
    """A `Telemetry` on in-memory providers; no global is set."""
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    metrics = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metrics])
    return Recorded(
        Telemetry(tracer_provider=tracer_provider, meter_provider=meter_provider),
        spans,
        metrics,
    )


def points(reader, name):
    """Each data point of the metric `name`, as `(attributes, point)`, from one
    collection."""
    data = reader.get_metrics_data()
    found = []
    for resource in data.resource_metrics if data else ():
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    found += [(dict(p.attributes), p) for p in metric.data.data_points]
    return found
