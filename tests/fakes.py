"""The test doubles, one copy of each.

Each class answers a named production seam and carries the union of what the
modules that used to keep their own copy needed. Before this module two files
could each hold a ``FakeRedis`` and the unit double could drift from the one the
bench times; a module split now has one file to repoint.
"""

import json
from types import SimpleNamespace

from kubed.selenium_flow.core.site_data import origin_of

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


class FakeStorage:
    """BiDi's ``storage`` module: a jar. ``cookies`` are there from the start;
    what is set reads back, except a ``drop``ped name — accepted without an
    error and never kept, as Chrome does to SameSite=None without Secure."""

    def __init__(self, cookies=(), refuse=(), drop=(), order=None):
        self.cookies, self.set = list(cookies), []
        self.refuse, self.drop, self.order = set(refuse), set(drop), order

    def get_cookies(self, filter=None, partition=None):
        held = [
            SimpleNamespace(name=c.name, domain=c.domain, path=c.path)
            for c in self.set
            if c.name not in self.drop and not (c.same_site == "none" and not c.secure)
        ]
        return SimpleNamespace(cookies=self.cookies + held)

    def set_cookie(self, cookie=None, partition=None):
        if cookie.name in self.refuse:
            raise RuntimeError("unable to set cookie")
        self.set.append(cookie)
        if self.order is not None:
            self.order.append("cookie")


class FakeTabs:
    """BiDi's ``browsing_context`` module. Every call lands in ``log`` (the
    wire order a spare-tab test asserts); a navigation is also kept in
    ``navigated`` as (context, url), and ``at`` maps a tab to the origin it
    stands on."""

    def __init__(self, log=None):
        self.log = [] if log is None else log
        self.navigated = []
        self.at = {}

    def create(self, type=None, background=None, **_):
        self.log.append(("create", type, background))
        return "spare-1"

    def navigate(self, context=None, url=None, wait=None):
        self.log.append(("navigate", context, url, wait))
        self.navigated.append((context, url))
        self.at[context] = origin_of(url)

    def close(self, context=None):
        self.log.append(("close", context))


class FakeNetwork:
    """BiDi's ``network`` module: intercepts and event handlers, logged."""

    def __init__(self, log):
        self.log, self.handlers, self.responses = log, {}, []

    def add_intercept(self, phases=None, contexts=None, url_patterns=None):
        self.log.append(("intercept", phases, contexts, url_patterns))
        return {"intercept": "i-1"}

    def remove_intercept(self, intercept=None):
        self.log.append(("unintercept", intercept))

    def add_event_handler(self, event, callback, contexts=None):
        self.log.append(("handler", event, contexts))
        self.handlers[7] = callback
        return 7

    def remove_event_handler(self, event, callback_id):
        self.log.append(("unhandler", event, callback_id))

    def provide_response(self, **kw):
        self.responses.append(kw)


class FakeScript:
    """BiDi's ``script`` module. Answers as the page would: ours, with the
    marker, or a service worker's. ``stuck`` leaves the tab on the page it was
    on before."""

    def __init__(self, log, tabs, value=None, workers=(), stuck=False, fail=None):
        self.log, self.tabs = log, tabs
        self.value, self.workers, self.stuck, self.fail = (
            value, set(workers), stuck, fail,
        )
        self.expression = None

    def evaluate(self, expression=None, target=None, await_promise=None):
        self.log.append(("evaluate", target, await_promise))
        self.expression = expression
        if self.fail:
            return {"type": "exception", "exceptionDetails": {"text": self.fail}}
        origin = (
            "https://before.test" if self.stuck else self.tabs.at[target["context"]]
        )
        page = {"spare": origin not in self.workers, "origin": origin}
        if page["spare"]:
            page["value"] = self.value
        return {
            "type": "success",
            "result": {"type": "string", "value": json.dumps(page)},
        }


class FakeBidi:
    """What `Grid.bidi` yields: the jar, the main tab's id, tabs, network and
    script. ``cookies``, ``refuse``, ``drop`` and ``order`` configure the jar;
    ``value``, ``workers``, ``stuck`` and ``fail`` the page script."""

    current_window_handle = MAIN

    def __init__(
        self,
        order=None,
        cookies=(),
        refuse=(),
        drop=(),
        value=None,
        workers=(),
        stuck=False,
        fail=None,
    ):
        self.log = []
        self.storage = FakeStorage(
            cookies=cookies, refuse=refuse, drop=drop, order=order
        )
        self.browsing_context = FakeTabs(self.log)
        self.network = FakeNetwork(self.log)
        self.script = FakeScript(
            self.log,
            self.browsing_context,
            value=value,
            workers=workers,
            stuck=stuck,
            fail=fail,
        )
