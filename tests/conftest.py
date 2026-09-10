"""Shared fixtures and doubles.

Everything here was previously duplicated across test modules — two copies of
``RecordingActions``, two of ``FakeGrid``, three of the ``http()`` helper, and
the same four-line ``monkeypatch.setattr(..., http_request, ...)`` incantation in
about twenty tests. They had already drifted: one ``RecordingActions`` counted
opened browsers, the other also recorded the settings each was opened with, so
which behaviours a test could assert depended on which file it happened to live
in.

The Grid is never actually dialled. Tests here exercise wiring and coercion;
anything that needs a live Grid is an integration test and is marked as one.
"""

import pytest

from kubed.selenium_flow import sessions as sessions_module
from kubed.selenium_flow.actions import Actions
from kubed.selenium_flow.browser import Grid
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.sessions import CallerKey, SessionManager
from kubed.selenium_flow.store import MemoryStore

TOKEN = "test-token-abc123"

# Two callers that are stable and distinct, which is the whole premise of the
# saved-session feature: the same key must always resolve to the same browser,
# and two different keys must never see each other's.
NAMED = CallerKey("named:desktop", "named")
OTHER = CallerKey("named:laptop", "named")


# ---- the real server -------------------------------------------------------


@pytest.fixture
def grid():
    """A Grid pointed at an address nothing listens on."""
    return Grid("http://grid.invalid:4444")


@pytest.fixture
def actions(grid):
    return Actions(grid)


@pytest.fixture
def server():
    """An authenticated server, since that is how it is deployed."""
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)


@pytest.fixture
def open_server():
    """A server with auth disabled, as `docker compose up` runs it."""
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=None)


# ---- doubles ---------------------------------------------------------------


class FakeGrid:
    """Tracks which sessions are still live, and every liveness question asked."""

    def __init__(self):
        self.alive = set()
        self.checked = []

    def is_alive(self, session_id):
        self.checked.append(session_id)
        return session_id in self.alive


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
        self.closed = []
        self.grid = FakeGrid()

    def open_session(self, url=None, **kwargs):
        self.opened += 1
        session_id = f"generated-{self.opened}"
        self.opened_urls.append(url)
        self.opened_settings.append(dict(kwargs))
        self.grid.alive.add(session_id)
        return {"session_id": session_id, "url": url or "about:blank"}

    def end_browser(self, session_id):
        self.closed.append(session_id)
        self.grid.alive.discard(session_id)
        return {"success": True, "session_id": session_id}


def http(params=None, headers=None):
    """Stand in for the ambient HTTP request."""
    return dict(params or {}), dict(headers or {})


def manager(actions=None, store=None, enabled=True):
    """A SessionManager over doubles, which is how nearly every test wants one."""
    return SessionManager(
        actions or RecordingActions(), store or MemoryStore(), enabled=enabled
    )


# ---- who is calling --------------------------------------------------------


@pytest.fixture
def named_caller(monkeypatch):
    """Make the ambient request look like a client that named its session.

    This is the `NAMED` key, so a test using this fixture can read and write
    `store[NAMED.value]` directly. Saved mode — `session_id` is resolved for the
    caller and passing one is an error.
    """
    monkeypatch.setattr(
        sessions_module, "http_request", lambda: http({"session": "desktop"})
    )
    return NAMED


@pytest.fixture
def stateless_caller(monkeypatch):
    """Make the ambient request carry nothing stable to key on.

    Stateless mode — every call must pass its own `session_id`.
    """
    monkeypatch.setattr(sessions_module, "http_request", lambda: http())
    return
