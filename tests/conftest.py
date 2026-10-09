"""Shared fixtures and doubles.

Everything here was previously duplicated across test modules — two copies of
``RecordingActions``, two of ``FakeGrid``, three of the ``http()`` helper, and
the same four-line ``monkeypatch.setattr(..., request_values, ...)`` incantation in
about twenty tests. They had already drifted: one ``RecordingActions`` counted
opened browsers, the other also recorded the settings each was opened with, so
which behaviours a test could assert depended on which file it happened to live
in.

The Grid is never actually dialled. Tests here exercise wiring and coercion;
anything that needs a live Grid is an integration test and is marked as one.
"""

import pytest

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.core.browser import Grid
from kubed.selenium_flow.http.admin import page as _admin_page
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace.store import MemoryStore
from kubed.selenium_flow.workspace.workspaces import Workspaces

from .fakes import FakeGrid, ScriptedDriver

TOKEN = "test-token-abc123"

# Two sessions that are stable and distinct, which is the whole premise: the
# same name must always resolve to the same browser, and two different names
# must never see each other's. A name is the store key, so a test can read and
# write `store[NAMED]` directly.
NAMED = "desktop"
OTHER = "laptop"


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
    return SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )


@pytest.fixture
def open_server():
    """A server with auth disabled, as `docker compose up` runs it."""
    return SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}))


# ---- doubles ---------------------------------------------------------------


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
    """Stand in for the ambient HTTP request: every value a list, as
    ``clients.request_values`` reads one."""
    return (
        {k: [v] for k, v in (params or {}).items()},
        {k: [v] for k, v in (headers or {}).items()},
    )


def manager(actions=None, store=None):
    """A Workspaces manager over doubles, which is how nearly every test wants one."""
    return Workspaces(actions or RecordingActions(), store or MemoryStore())


@pytest.fixture
def scripted(actions, monkeypatch):
    """Make `actions` drive a `ScriptedDriver`: ``scripted(**settings)`` builds
    one, reattaches every session to it, and returns it. ``driver.log`` is the
    record of what was asked."""

    def use(**settings):
        driver = ScriptedDriver(**settings)
        monkeypatch.setattr(actions.grid, "reconnect", lambda session_id: driver)
        monkeypatch.setattr(actions.grid, "open", lambda *a, **k: driver)
        return driver

    return use


# ---- who is calling --------------------------------------------------------


def calling_as(monkeypatch, name):
    """Make the ambient request name ``name``, or name nothing when it is None.

    The one seam for who is calling: the edge reads the request through
    ``clients.request_values``, and every surface below it is handed what it
    read."""
    monkeypatch.setattr(
        clients_module,
        "request_values",
        lambda: http({"workspace": name} if name is not None else None),
    )


@pytest.fixture
def named_caller(monkeypatch):
    """Make the ambient request look like a client that named its workspace.

    This is the `NAMED` workspace, so a test using this fixture can read and write
    `store[NAMED]` directly.
    """
    monkeypatch.setattr(
        clients_module, "request_values", lambda: http({"workspace": NAMED})
    )
    return NAMED


@pytest.fixture
def unnamed_caller(monkeypatch):
    """Make the ambient request name no workspace at all.

    There is one contract now, and this is the caller that has not met it: every
    call is refused with the message that says how to name yourself (§F2.12).
    """
    monkeypatch.setattr(clients_module, "request_values", lambda: http())
    return


# ---- the built UI ----------------------------------------------------------

@pytest.fixture(autouse=True)
def ui_dir(tmp_path_factory, monkeypatch):
    """An empty UI directory for every test: the suite must not depend on
    whether this checkout happens to have run `npm --prefix ui run build`.

    Its own directory, not one inside `tmp_path`: tests that list their
    `tmp_path` (a flow store, a secrets mount) must find only what they made."""
    folder = tmp_path_factory.mktemp("ui-static")
    monkeypatch.setattr(_admin_page, "static_path", lambda: folder)
    return folder


SHELL = (
    '<title>{name}</title><style>__CSS__</style>'
    '<div id="root" data-mount="__MOUNT__" data-console="__CONSOLE__"></div>'
    '<script type="module">__JS__</script>'
)


@pytest.fixture
def built_ui(ui_dir):
    """A stand-in for the build: the six files, recognisable by content."""
    for name in ("admin", "app"):
        (ui_dir / f"{name}.html").write_text(SHELL.format(name=name))
        (ui_dir / f"{name}.css").write_text(f"/* {name} css */")
        (ui_dir / f"{name}.js").write_text(f"/* {name} js */")
    return ui_dir


@pytest.fixture
def issuer():
    """A loopback OIDC issuer: its JWKS is fetched for real."""
    from .jwks import Issuer

    made = Issuer()
    yield made
    made.close()
