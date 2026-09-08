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
from .store import MemoryStore, SessionRecord, SessionStore

log = logging.getLogger(__name__)

# A client names its session with either of these. The query parameter is the
# one most clients can set, since an MCP server is usually configured by URL.
NAME_PARAM = "session"
NAME_HEADER = "x-session-key"
# The header the transport negotiates. Read directly rather than through
# Context.session_id, which invents a value when there is none.
MCP_SESSION_HEADER = "mcp-session-id"


@dataclass(frozen=True)
class CallerKey:
    """Who is calling, and how we worked that out.

    ``source`` exists for the log line: when sessions misbehave, the first
    question is always which of the three mechanisms was actually used.
    """

    value: str
    source: str


def _http() -> tuple[dict, dict] | None:
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
    http = _http()
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

    def key(self) -> CallerKey | None:
        """The current caller's key, or None when sessions are off."""
        if not self.enabled:
            return None
        return caller_key()

    def describe(self) -> dict:
        """What this caller's session currently is, without changing anything.

        Deliberately side-effect free: reading a status resource must never open
        a browser, so this peeks at the store rather than going through
        ``resolve``. ``live`` is the useful part — it says whether the Grid still
        has the browser, which is the question a caller actually has.
        """
        key = self.key()
        status = {
            "session_id": None,
            "url": None,
            "live": None,
            "key": key.value if key else None,
            "key_source": key.source if key else None,
            "store": self.kind,
        }
        if key is None:
            return status
        record = self.store.get(key.value)
        if record is None:
            return status
        status["session_id"] = record.session_id
        status["url"] = record.url or None
        status["live"] = self.actions.grid.is_alive(record.session_id)
        return status

    def resolve(self, key: CallerKey | None, session_id: str | None) -> str:
        """The session id to act on.

        An explicit id always wins — a caller naming a session means it, and
        substituting a remembered one would act on the wrong browser. It is also
        taken on trust: the caller owns that session and may well have opened it
        through the HTTP surface, so it is not ours to validate or replace.
        """
        if session_id:
            return session_id
        if not self.enabled:
            raise ValueError(
                "session_id is required: saved sessions are disabled on this server"
            )
        if key is None:
            raise ValueError(
                "session_id is required: this request carries no stable session "
                "to key on. Either pass session_id on every call, or name a "
                f"session by adding ?{NAME_PARAM}=<name> to the MCP URL (or an "
                f"{NAME_HEADER} header) so this server can remember one for you."
            )

        record = self.store.get(key.value)
        if record is not None:
            if self.actions.grid.is_alive(record.session_id):
                log.debug(
                    "session %s recalled for key %s (%s)",
                    record.session_id,
                    key.value,
                    key.source,
                )
                return record.session_id
            # The Grid reaped it. Reopen where the caller left off so the
            # refresh is invisible; this is why the record carries a url.
            log.info(
                "session %s is gone from the grid, reopening for key %s",
                record.session_id,
                key.value,
            )
            return self._open(key, url=record.url or None)

        # First use of a key we know is stable, so opening cannot leak.
        return self._open(key)

    def _open(self, key: CallerKey, url: str | None = None) -> str:
        opened = self.actions.open_session(url=url)
        self.remember(key, opened["session_id"], opened.get("url", url or ""))
        log.info(
            "opened session %s for key %s (%s)",
            opened["session_id"],
            key.value,
            key.source,
        )
        return opened["session_id"]

    def remember(self, key: CallerKey | None, session_id: str, url: str = "") -> None:
        """Bind a session to this caller."""
        if self.enabled and key is not None:
            self.store.set(
                key.value,
                SessionRecord(session_id=session_id, url=url, opened_at=time.time()),
            )

    def touch(self, key: CallerKey | None, url: str | None) -> None:
        """Record where the browser ended up, and slide the mapping's TTL.

        Called after an action so a later refresh can restore the right page,
        and so a session in active use does not expire out of the store
        underneath the caller.
        """
        if not (self.enabled and key is not None and url):
            return
        record = self.store.get(key.value)
        if record is not None:
            self.store.set(key.value, record.at(url))

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
