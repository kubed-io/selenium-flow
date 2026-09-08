"""Saved sessions: an optional convenience for MCP callers only.

An agent works one conversation at a time and gains nothing from threading a
session id through every call, so when this feature is on it may simply omit
``session_id`` and the browser it opened earlier is found again — keyed on the
MCP session it is already talking over.

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
        # First call of a conversation that never opened anything explicitly.
        # Opening here is what lets an agent start with `navigate` and just work.
        opened = self.actions.open_session()
        log.info("opened session %s for key %s", opened["session_id"], key)
        self.store.set(key, opened["session_id"])
        return opened["session_id"]

    def remember(self, key: str | None, session_id: str) -> None:
        """Bind a freshly opened session to this caller."""
        if self.enabled and key:
            self.store.set(key, session_id)

    def forget(self, key: str | None) -> None:
        """Drop the binding, so the next call opens a new browser."""
        if self.enabled and key:
            self.store.delete(key)
