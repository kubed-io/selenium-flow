"""Fakes for the site data tests: a cookie jar, the driver `Grid.bidi`
yields, and the spare tab. Shared by the restore and the save tests, so the
two fake one browser."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

from kubed.selenium_flow.core import site_data as sd
from kubed.selenium_flow.core.browser import ServiceWorkerAnswered

NOW = 1_790_800_000.0
MAIN = "main-1"


def cookie(name, domain, value="v", http_only=False, expiry=None, secure=True):
    """A cookie as the snapshot stores it."""
    return {
        "name": name, "value": value, "value_type": "string", "domain": domain,
        "path": "/", "http_only": http_only, "secure": secure,
        "same_site": "lax", "expiry": expiry,
    }


def snapshot(cookies=(), origins=None, session=None, saved_at=NOW):
    """A saved snapshot in round 2's shape; ``origins`` maps an origin to its
    localStorage."""
    return {
        "cookies": list(cookies),
        "origins": {o: {"local": dict(v)} for o, v in (origins or {}).items()},
        "session": dict(session or {}),
        "saved_at": saved_at,
    }


def bidi_cookie(name, domain, value="v", http_only=False):
    """A cookie as BiDi's storage.getCookies returns it."""
    return SimpleNamespace(
        name=name, domain=domain, path="/", http_only=http_only, secure=True,
        same_site="lax", expiry=None, value=SimpleNamespace(type="string", value=value),
    )


class FakeStorage:
    """A jar: ``cookies`` are there from the start; what is set reads back,
    except a ``drop``ped name — accepted without an error and never kept, as
    Chrome does to SameSite=None without Secure."""

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
    def __init__(self):
        self.navigated = []

    def navigate(self, context=None, url=None, wait=None):
        self.navigated.append((context, url))


class FakeBidi:
    """What `Grid.bidi` yields: the jar, the main tab's id, navigation."""

    current_window_handle = MAIN

    def __init__(self, order=None, **kw):
        self.storage = FakeStorage(order=order, **kw)
        self.browsing_context = FakeTabs()


def bidi_cm(fake):
    """A stand-in for `Grid.bidi` that yields ``fake``."""

    @contextmanager
    def cm(session_id):
        yield fake

    return cm


class FakeSpare:
    """`browser.spare_tab`, faked over two dicts of storage by origin.

    An origin in ``refuse`` fails to load, one in ``workers`` is answered by a
    service worker, and ``broken`` makes the tab itself fail to open. Each run
    is logged as (tab, origin); ``order``, when given, collects the tab too.
    """

    def __init__(self, local=None, refuse=(), workers=(), broken=None, order=None):
        self.local = {o: dict(v) for o, v in (local or {}).items()}
        self.session: dict = {}
        self.refuse, self.workers = set(refuse), set(workers)
        self.broken, self.order = broken, order
        self.runs: list = []
        self.tabs: list = []

    @contextmanager
    def __call__(self, bidi, context=None):
        if self.broken:
            raise self.broken
        tab = context or "spare"
        self.tabs.append(tab)

        def run(origin, expression):
            self.runs.append((tab, origin))
            if self.order is not None:
                self.order.append(tab)
            if origin in self.workers:
                raise ServiceWorkerAnswered(origin)
            if origin in self.refuse:
                raise RuntimeError("the navigation failed")
            if expression == sd.READ_LOCAL:
                return dict(self.local.get(origin, {}))
            store, items = filled(expression)
            target = self.session if store == "sessionStorage" else self.local
            target.setdefault(origin, {}).update(items)
            return len(items)

        yield run


def filled(expression):
    """What a `site_data.fill` expression sets, and in which store: it carries
    its items as JSON."""
    store = "sessionStorage" if "sessionStorage.setItem" in expression else "localStorage"
    items = json.loads(expression.split("const items = ", 1)[1].split("; for ", 1)[0])
    return store, items
