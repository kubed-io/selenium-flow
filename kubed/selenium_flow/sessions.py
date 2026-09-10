"""Deciding which browser a caller means.

An agent works one conversation at a time and gains nothing from threading a
session id through every call, so when this feature is on it may omit
``session_id`` and the browser it opened earlier is found again.

The whole problem is *keying* that lookup, and getting it wrong is expensive:
key on something that changes per request and every call opens a fresh browser,
leaking a Grid slot each time. So a key is only ever taken from something the
client actually controls, in this order:

1. **A name the client chose** — an ``X-Session-Key`` header, or
   ``?session=<name>`` on the MCP URL. Deterministic, and the only option that
   does not depend on the client behaving well: any client that can set a URL or
   a header can hold a session, including ones that cannot hold an MCP session at
   all. The header wins when both are given: it is set inside the credential,
   which an admin controls, so pinning a name there deliberately ties one
   session to one credential and a caller must not be able to override it from
   the URL. The parameter is the ergonomic path for everyone else — one shared
   bearer credential, each caller naming itself in its own URL.
2. **The MCP transport session** — the ``Mcp-Session-Id`` header, read off the
   request directly. This is the id the server negotiated and enforces.
3. **stdio**, where one process serves exactly one client, so a constant is
   correct.

What is deliberately *not* used is ``Context.session_id``. It looks like the
obvious choice and is a trap: when it cannot find a real session it falls back
to ``str(uuid4())``, so it never fails — it just returns a brand new key on
every call, which silently leaks one browser per tool call. Reading the header
ourselves means a missing session is visible as a missing session.

With every accepted key stable by construction, opening a browser on first use
is safe again: a caller can start with ``navigate`` and it works, and a caller
whose key we cannot determine is told to pass ``session_id`` instead of being
quietly handed a fresh browser.

**The HTTP endpoints never use any of this.** They take a session id in and give
one back, always, so an n8n workflow owns its session outright and can pass it
between nodes. The capability set stays identical across both surfaces; only the
ergonomics differ.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .actions import Actions
from .browser import DEFAULT_BROWSER
from .store import MemoryStore, SessionRecord, SessionStore

log = logging.getLogger(__name__)

# A client names its session with either of these. The query parameter is the
# one most clients can set, since an MCP server is usually configured by URL.
NAME_PARAM = "session"
NAME_HEADER = "x-session-key"
# The header the transport negotiates. Read directly rather than through
# Context.session_id, which invents a value when there is none.
MCP_SESSION_HEADER = "mcp-session-id"
# A stateless caller has no key, and inventing one to *resolve* it is the bug
# this module exists to prevent. Recording one under its own browser id is a
# different thing: it is never looked up to answer "whose browser is this?",
# only written so the session shows up in the admin history like any other.
STATELESS_PREFIX = "session:"


def stateless_key(session_id: str) -> str:
    """The store key a caller-less session is recorded under."""
    return f"{STATELESS_PREFIX}{session_id}"


@dataclass(frozen=True)
class CallerKey:
    """Who is calling, and how we worked that out.

    ``source`` exists for the log line: when sessions misbehave, the first
    question is always which of the three mechanisms was actually used.
    """

    value: str
    source: str


def http_request() -> tuple[dict, dict] | None:
    """(query params, headers) for the current request, or None off HTTP.

    Both are looked up through FastMCP's dependency helpers, which read the
    request from a context variable set by the ASGI stack. That is a different
    path from the one ``Context.session_id`` uses, and it keeps working where
    that one gives up.
    """
    try:
        from fastmcp.server.dependencies import get_http_request
    except ImportError:  # pragma: no cover - fastmcp is a hard dependency
        return None
    try:
        request = get_http_request()
    except Exception:  # noqa: BLE001 - outside an HTTP request this raises
        request = None
    if request is None:
        # Headers may still be reachable even when the request object is not.
        try:
            from fastmcp.server.dependencies import get_http_headers

            headers = get_http_headers()
        except Exception:  # noqa: BLE001
            return None
        if not headers:
            return None
        return {}, {k.lower(): v for k, v in headers.items()}
    return (
        dict(request.query_params),
        {k.lower(): v for k, v in request.headers.items()},
    )


def caller_key() -> CallerKey | None:
    """The stable identity of the current caller, or None if there isn't one.

    Returning None is a real answer, not a failure: it means this request
    carries nothing stable to key on, and the caller must pass ``session_id``.
    """
    http = http_request()
    if http is None:
        # stdio: one process serves one client, so there is nothing to tell
        # apart and a constant is exactly right.
        return CallerKey("stdio", "stdio")

    params, headers = http

    # Header first, and this ordering is a permission boundary, not a taste:
    # the header is set inside the credential, which an admin controls, while
    # the query parameter is written by whoever wires up the call. An admin
    # pinning a name that way is choosing one session per credential, so a
    # caller must not be able to override it from the URL. Leaving the header
    # out is the same decision made the other way: it delegates the choice to
    # whoever implements the call.
    named = headers.get(NAME_HEADER) or params.get(NAME_PARAM)
    if named:
        return CallerKey(f"named:{named}", "named")

    transport = headers.get(MCP_SESSION_HEADER)
    if transport:
        return CallerKey(f"mcp:{transport}", "transport")

    return None


class SessionManager:
    """Resolves a caller's browser, keeping the mapping in a ``SessionStore``.

    Disabled is a real mode, not a degraded one: every tool still works, the
    caller just has to pass ``session_id`` the way the HTTP surface does.
    """

    def __init__(
        self,
        actions: Actions,
        store: SessionStore | None = None,
        enabled: bool = True,
    ):
        self.actions = actions
        self.enabled = enabled
        self.store = store if store is not None else MemoryStore()

    @property
    def kind(self) -> str:
        """Which backend is in play, for /health and the config log line."""
        if not self.enabled:
            return "disabled"
        return getattr(self.store, "kind", "memory")

    SAVED = "saved"
    STATELESS = "stateless"

    def mode(self, key: CallerKey | None = None) -> str:
        """Which contract this request is under.

        Per request, not per server: the same process serves a client that names
        a session and one that cannot, and they follow different rules.
        """
        if key is None:
            key = self.key()
        return self.SAVED if key is not None else self.STATELESS

    def key(self) -> CallerKey | None:
        """The current caller's key, or None when sessions are off."""
        if not self.enabled:
            return None
        return caller_key()

    def describe(self) -> dict:
        """What this caller's session is, and which contract it is under.

        Deliberately side-effect free: reading a status resource must never open
        a browser, so this peeks at the store rather than going through
        ``resolve``. It reports the *mode* first because that decides the shape
        of every later call, and names the reference that explains it — an agent
        that reads this should not have to guess what to read next.
        """
        key = self.key()
        mode = self.mode(key)
        status = {
            "mode": mode,
            "session_id": None,
            "browser": None,
            "url": None,
            "live": None,
            "in_frame": None,
            "key": key.value if key else None,
            "key_source": key.source if key else None,
            "store": self.kind,
            "settings": {},
            "guidance": (
                "skill://selenium-flow/references/SAVED_SESSIONS.md"
                if mode == self.SAVED
                else "skill://selenium-flow/references/STATELESS.md"
            ),
            "pass_session_id": mode == self.STATELESS,
        }
        if key is None:
            return status

        record = self.store.get(key.value)
        if record is None:
            return status

        status["session_id"] = record.session_id or None
        status["url"] = record.url or None
        status["settings"] = dict(record.settings or {})
        # Reported at the top level as well as inside settings, because "which
        # browser am I driving" is the question this resource exists to answer
        # and a caller should not have to know it is stored as a setting. A
        # record written before browsers were selectable has none, and that
        # session really is the default one.
        status["browser"] = status["settings"].get("browser") or DEFAULT_BROWSER
        # A record with no browser is an ordinary state, not a broken one: the
        # Grid reaped it or an admin ended it, and the context it left behind is
        # what the next open_session inherits.
        status["live"] = (
            self.actions.grid.is_alive(record.session_id) if record.attached else False
        )
        if status["live"]:
            # Only worth a round trip when there is a live browser to ask.
            try:
                from . import browser as browser_module

                status["in_frame"] = browser_module.in_frame(
                    self.actions.grid.reconnect(record.session_id)
                )
            except Exception:  # noqa: BLE001 - status must never fail
                status["in_frame"] = None
        return status

    def resolve(self, key: CallerKey | None, session_id: str | None) -> str:
        """The session id to act on, or an error explaining which mode you are in.

        The two modes are deliberately exclusive, because a caller that is
        confused about which one it is in produces the most expensive class of
        mistake: acting on the wrong browser, or opening one per call.

        - **Stateless** (no key): ``session_id`` is required on every call.
        - **Saved** (a key): ``session_id`` must NOT be passed. The server knows
          which browser is yours, and an id from somewhere else is either a
          mistake or a browser someone else owns.

        Nothing here opens a browser. ``open_session`` is the one place that
        happens, and hiding it behind a first use hid the only place a session's
        settings could be chosen.
        """
        if key is None:
            if session_id:
                return session_id
            raise ValueError(
                "session_id is required: this request carries no stable session "
                "to key on, so you own the session. Call open_session, keep the "
                "session_id it returns, and pass it on every call. See the "
                "skill's references/STATELESS.md."
            )

        if session_id:
            raise ValueError(
                "do not pass session_id: this server is holding a browser for "
                f"you (key {key.source}). Omit session_id and it is resolved for "
                "you. Passing one is only correct when the server cannot "
                "identify you. See the skill's references/SAVED_SESSIONS.md."
            )

        record = self.store.get(key.value)
        if record is None or not record.attached:
            # Detached reads the same as absent on purpose. An agent is told it
            # has no browser and calls open_session, which is the one path that
            # opens one — and which now inherits this record's browser and page.
            raise ValueError(
                "no browser is open for you yet: call open_session first. It "
                "takes the window size and timeouts this session should use, "
                "which is why it is not done implicitly."
            )

        if self.actions.grid.is_alive(record.session_id):
            log.debug(
                "session %s recalled for key %s (%s)",
                record.session_id,
                key.value,
                key.source,
            )
            return record.session_id

        # The Grid reaped it. Reopen where the caller left off, with the same
        # settings, so a refresh does not silently change the browser's shape.
        log.info(
            "session %s is gone from the grid, reopening for key %s",
            record.session_id,
            key.value,
        )
        return self._open(key, url=record.url or None, settings=record.settings)

    def _open(
        self, key: CallerKey, url: str | None = None, settings: dict | None = None
    ) -> str:
        """Open a browser for this key. Only ``open_session`` and a refresh."""
        opened = self.actions.open_session(url=url, **(settings or {}))
        self.remember(key, opened["session_id"], opened.get("url", url or ""), settings)
        log.info(
            "opened session %s for key %s (%s)",
            opened["session_id"],
            key.value,
            key.source,
        )
        return opened["session_id"]

    def store_key(self, key: CallerKey | None, session_id: str = "") -> str | None:
        """Where this caller's record lives, or None if it cannot have one.

        A keyed caller is stored under its key. A caller with no key is stored
        under the browser it holds, which is not the same thing as giving it a
        key: nothing ever resolves a caller *from* that entry, so the leak this
        module is built to prevent stays prevented. It exists so a stateless
        session is visible in the admin history and expires like any other.
        """
        if not self.enabled:
            return None
        if key is not None:
            return key.value
        return stateless_key(session_id) if session_id else None

    def remember(
        self,
        key: CallerKey | None,
        session_id: str,
        url: str = "",
        settings: dict | None = None,
    ) -> None:
        """Bind a browser to this caller's flow session.

        The settings are stored because a reopen has to use the *same* browser,
        not a default one — swapping Firefox for Chrome, or a 1400x900 window
        for the node default, midway through a task would be a silent change of
        shape. They are also what ``open_session`` with no arguments inherits.
        """
        where = self.store_key(key, session_id)
        if where is None:
            return
        self.store.set(
            where,
            SessionRecord(
                session_id=session_id,
                url=url,
                opened_at=time.time(),
                settings=dict(settings or {}),
            ),
        )

    def touch(
        self, key: CallerKey | None, url: str | None, session_id: str = ""
    ) -> None:
        """Record where the browser ended up, and slide the record's TTL.

        Called after an action so a later reopen restores the right page, and so
        a session in active use does not expire out of the store underneath the
        caller. Stateless callers are touched too, which is what keeps their
        entry in the history alive for as long as they are working.
        """
        if not url:
            return
        where = self.store_key(key, session_id)
        if where is None:
            return
        record = self.store.get(where)
        if record is not None:
            self.store.set(where, record.at(url))

    def context(self, key: CallerKey | None) -> dict:
        """The browser and page this caller's session last had, for a reopen.

        Empty when there is no record, which is the same answer as "nothing to
        inherit" and lets ``open_session`` treat both alike.
        """
        where = self.store_key(key)
        if where is None:
            return {}
        record = self.store.get(where)
        if record is None:
            return {}
        return {"settings": dict(record.settings or {}), "url": record.url or ""}

    def detach(self, store_key: str) -> SessionRecord | None:
        """Drop the browser from a flow session, keeping the session itself.

        The admin surface's half of ending a browser. Deliberately not a delete:
        the browser choice and the last page are the context the next open
        inherits, and taking those as well would make ending a stale browser
        cost the caller its place.
        """
        record = self.store.get(store_key)
        if record is None:
            return None
        detached = record.detached()
        self.store.set(store_key, detached)
        return record

    def forget(self, key: CallerKey | None, session_id: str | None = None) -> None:
        """Drop the binding, so the next call opens a new browser.

        ``session_id`` guards the common mistake: a caller closing a session it
        named explicitly must not evict a *different* browser this key happens
        to be holding.
        """
        if not (self.enabled and key is not None):
            return
        if session_id is not None:
            record = self.store.get(key.value)
            if record is not None and record.session_id != session_id:
                return
        self.store.delete(key.value)
