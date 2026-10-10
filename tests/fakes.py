"""The test doubles, one copy of each.

Each class answers a named production seam and carries the union of what the
modules that used to keep their own copy needed. Before this module two files
could each hold a ``FakeRedis`` and the unit double could drift from the one the
bench times; a module split now has one file to repoint.
"""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from selenium.common.exceptions import UnexpectedAlertPresentException

from kubed.selenium_flow.bidi import CHANNEL_DOWN, BidiError
from kubed.selenium_flow.faults import BidiUnavailable
from kubed.selenium_flow.urls import origin_of

MAIN = "main-1"


class FakeGrid:
    """Answers `Grid`: ``is_alive`` for liveness, ``files`` and ``read_file`` for
    the download listing.

    ``alive`` is the set of live session ids and ``checked`` every liveness
    question asked; ``entries`` and ``data`` are the listing and the bytes, and
    ``reads`` every file read.
    """

    def __init__(self, entries=(), data=b"bytes"):
        self.alive = set()
        self.checked = []
        self.entries = list(entries)
        self.data = data
        self.reads = []

    def is_alive(self, session_id):
        self.checked.append(session_id)
        return session_id in self.alive

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


class FakeRedis:
    """Enough of redis-py for the store: get, mget, set with ex, delete, ping,
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

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return [key.encode() for key in list(self.data) if key.startswith(prefix)]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    """redis-py's optimistic transaction: WATCH reads at once, MULTI buffers,
    EXEC refuses with WatchError if a watched key was written since.

    ``fake.interfere`` runs just before EXEC — another replica's write landing
    between this one's read and its write.
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
        return {"contexts": [{"context": MAIN, "children": []}]}

    def _browsingContext_create(self, params):
        self.tabs += 1
        return {"context": f"spare-{self.tabs}"}

    def _browsingContext_navigate(self, params):
        self.navigated.append((params["context"], params["url"]))
        self.at[params["context"]] = origin_of(params["url"])
        return {}

    def _browsingContext_close(self, params):
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

    def default_content(self):
        self._driver.log.append(("switch_to", "default_content"))

    def parent_frame(self):
        self._driver.log.append(("switch_to", "parent_frame"))


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
    is what every locator finds.
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
        d["_url"], d["_title"], d["dialog"] = url, title, dialog
        d["scripts"] = dict(scripts or {})
        d["window"] = {"width": window[0], "height": window[1]}
        d["session_id"] = session_id
        d["element"] = ScriptedElement(d["log"])
        d["switch_to"] = _ScriptedSwitch(self)
        d["screenshot_data"] = self.PNG

    def __setattr__(self, name, value):
        self.log.append(("set", name))
        self.__dict__[name] = value

    @property
    def current_url(self):
        self.log.append(("current_url",))
        if self.dialog is not None:
            raise UnexpectedAlertPresentException("dialog open")
        return self._url

    @property
    def title(self):
        self.log.append(("title",))
        if self.dialog is not None:
            raise UnexpectedAlertPresentException("dialog open")
        return self._title

    def get(self, url):
        self.log.append(("get", url))
        self.__dict__["_url"] = url

    def execute_script(self, script, *args):
        self.log.append(("execute_script", script))
        for needle, value in self.scripts.items():
            if needle in script:
                return value
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
