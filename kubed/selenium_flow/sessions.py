"""Saved sessions: an optional convenience for MCP callers only.

An agent works one conversation at a time and gains nothing from threading a
session id through every call, so when this feature is on it may simply omit
``session_id`` and the browser it opened earlier is found again — keyed on the
MCP session it is already talking over.

**Recall only. This never opens a browser.** Not every client holds a stable
``Mcp-Session-Id`` across tool calls — Claude Code does not, and FastMCP then
generates a fresh id per request. A resolver that opened a browser when it found
no mapping would hand that client a brand new browser on *every* call, silently
leaking a Grid slot each time and acting on a blank page. Recall-only degrades
honestly instead: the caller gets a clear error telling it to pass ``session_id``,
which is what the HTTP surface has always required.

**The HTTP endpoints never use this.** They take a session id in and give one
back, always, so an n8n workflow owns the session outright and can pass it
between nodes, store it, or hand it to a different workflow. Making them
implicitly stateful would take that control away and tie a browser to a
transport that has no session to begin with.

So the capability set stays identical across the two surfaces — every action is
still a tool and an endpoint. Only the ergonomics differ, and only in the
direction each caller actually benefits from.
"""

from __future__ import annotations

import logging

from .actions import Actions
from .store import MemoryStore, SessionStore

log = logging.getLogger(__name__)


class SavedSessions:
    """Resolves an MCP caller's browser, opening one on first use.

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

    def resolve(self, key: str | None, session_id: str | None) -> str:
        """The session id to act on.

        An explicit id always wins — a caller naming a session means it, and
        silently substituting a remembered one would act on the wrong browser.

        Never opens a browser. See the module docstring: a client without stable
        MCP sessions would leak one per call.
        """
        if session_id:
            return session_id
        if not self.enabled:
            raise ValueError(
                "session_id is required: saved sessions are disabled on this server"
            )
        if not key:
            raise ValueError(
                "session_id is required: this transport has no session to key on"
            )
        saved = self.store.get(key)
        if saved:
            return saved
        raise ValueError(
            "session_id is required: no browser is saved for this conversation. "
            "Call open_session first and pass the session_id it returns, or pass "
            "session_id on every call if your client does not hold a stable MCP "
            "session."
        )

    def remember(self, key: str | None, session_id: str) -> None:
        """Bind a freshly opened session to this caller."""
        if self.enabled and key:
            self.store.set(key, session_id)

    def forget(self, key: str | None) -> None:
        """Drop the binding, so the next call opens a new browser."""
        if self.enabled and key:
            self.store.delete(key)
