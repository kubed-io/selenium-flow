"""Fixtures for the site data tests: snapshots, cookies and the spare tab. The
driver and the jar are in `fakes`; this is what is built on them, shared by the
restore and the save tests, so the two fake one browser."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

from kubed.selenium_flow.site_data import transfer
from kubed.selenium_flow.site_data.spare import ServiceWorkerAnswered

NOW = 1_790_800_000.0


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
    return {
        "name": name, "domain": domain, "path": "/", "httpOnly": http_only,
        "secure": True, "sameSite": "lax", "size": len(name) + len(value),
        "value": {"type": "string", "value": value},
    }


def channels_for(fake, broken=None):
    """A stand-in for `Channels` whose every channel is ``fake``. With
    ``broken``, opening one raises it, as a Grid whose /se/bidi is down does."""

    opened = []  # whether each channel asked for was private

    @contextmanager
    def open_channel(session_id, *, private=False):
        opened.append(private)
        if broken is not None:
            raise broken
        yield fake

    return SimpleNamespace(open_channel=open_channel, opened=opened)


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
        self.hidden: list = []  # the `hidden` each tab was opened with

    @contextmanager
    def __call__(self, channel, context=None, *, hidden=None):
        if self.broken:
            raise self.broken
        tab = context or "spare"
        self.tabs.append(tab)
        self.hidden.append(hidden)

        def run(origin, expression):
            self.runs.append((tab, origin))
            if self.order is not None:
                self.order.append(tab)
            if origin in self.workers:
                raise ServiceWorkerAnswered(origin)
            if origin in self.refuse:
                raise RuntimeError("the navigation failed")
            if expression == transfer.READ_LOCAL:
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
