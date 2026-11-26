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

import json
import logging
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlencode

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.testclient import TestClient

from kubed.selenium_flow import config
from kubed.selenium_flow.core import pointer
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.core.browser import Grid
from kubed.selenium_flow.flows.document import step_schemas
from kubed.selenium_flow.http.admin import page as _admin_page
from kubed.selenium_flow.mcp import clients as clients_module
from kubed.selenium_flow.mcp.failures import FASTMCP_LOGGER
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.workspace.store import MemoryStore
from kubed.selenium_flow.workspace.workspaces import Workspaces

from .bidi_fakes import (
    FakeBrowser,
    browser_in_thread,  # noqa: F401 - a fixture, offered here
)
from .fakes import (  # noqa: F401 - FakePeer, RecordingActions, recorded, points, quick, configured, loaded are offered here
    FakeConnection,
    FakeGrid,
    FakePeer,
    RecordingActions,
    ScriptedDriver,
    configured,
    loaded,
    points,
    quick,
    recorded,
    untabbed,
)

TOKEN = "test-token-abc123"

# Two workspaces that are stable and distinct, which is the whole premise: the
# same name must always resolve to the same browser, and two different names
# must never see each other's. A name is the store key, so a test can read and
# write `store[NAMED]` directly.
NAMED = "desktop"
OTHER = "laptop"

# kubed-mcp's two reading tools (DLV-6): listed only when a request turns resources off.
RESOURCE_TOOLS = frozenset({"list_resources", "read_resource"})


# ---- the real server -------------------------------------------------------


@pytest.fixture
def grid():
    """A Grid pointed at an address nothing listens on."""
    return Grid("http://grid.invalid:4444")


@pytest.fixture
def actions(grid):
    return Actions(grid)


# The servers below dial no Grid, so none of them checks for tabs (`untabbed`).


@pytest.fixture
def server():
    """An authenticated server, since that is how it is deployed."""
    return untabbed(SeleniumMCP(
        {"grid": {"url": "http://grid.invalid:4444"}, "auth": {"token": TOKEN}}
    ))


@pytest.fixture
def open_server():
    """A server with auth disabled, as `docker compose up` runs it."""
    return untabbed(SeleniumMCP({"grid": {"url": "http://grid.invalid:4444"}}))


@pytest.fixture
def data_server(tmp_path):
    """An authenticated server with a data directory in ``tmp_path``, so flows
    and kept files have somewhere to go."""
    return untabbed(SeleniumMCP({
        "grid": {"url": "http://grid.invalid:4444"},
        "auth": {"token": TOKEN},
        "data": {"dir": str(tmp_path)},
    }))


@pytest.fixture
def client(server):
    """An HTTP client of ``server`` that sends no credentials of its own."""
    return TestClient(server.mcp.server.http_app())


# ---- doubles ---------------------------------------------------------------


@pytest.fixture
async def browser():
    """A browser's BiDi end on this test's loop (`tests/bidi_fakes.py`)."""
    async with FakeBrowser() as fake:
        yield fake


def http(params=None, headers=None):
    """Stand in for the ambient HTTP request: every value a list, as
    ``clients.request_values`` reads one."""
    return (
        {k: [v] for k, v in (params or {}).items()},
        {k: [v] for k, v in (headers or {}).items()},
    )


@asynccontextmanager
async def over_http(server, headers=None, params=None, *, mode=None, client_info=None):
    """A client of ``server`` over the real ASGI app, authenticated with ``TOKEN``.

    ``headers`` and ``params`` travel as an HTTP client would send them, so the
    server reads them itself. ``mode`` and ``client_info`` go to ``Client`` when given.
    """
    app = server.mcp.server.http_app()

    def factory(headers=None, timeout=None, auth=None, **kwargs):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
            timeout=timeout if timeout is not None else httpx2.Timeout(30),
            **kwargs,
        )

    url = f"http://test/mcp?{urlencode(params)}" if params else "http://test/mcp"
    transport = StreamableHttpTransport(
        url,
        headers={"Authorization": f"Bearer {TOKEN}", **(headers or {})},
        httpx_client_factory=factory,
    )
    options = {"client_info": client_info}
    if mode is not None:
        options["mode"] = mode
    async with app.router.lifespan_context(app), Client(transport, **options) as client:
        yield client


async def delivery_meta(client) -> dict:
    """The handshake's ``_meta["io.kubed/delivery"]``, as plain JSON.

    The client keeps the handshake result it negotiated: the legacy
    ``initialize`` result, or the ``server/discover`` result in auto mode.
    """
    result = client.initialize_result or client.session.discover_result
    return json.loads(json.dumps(result.meta["io.kubed/delivery"]))


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
    call is refused with the message that says how to name yourself.
    """
    monkeypatch.setattr(clients_module, "request_values", lambda: http())
    return


# ---- what is logged --------------------------------------------------------


class LogLines(logging.Handler):
    """Every record as ``(level, message, whether it carried a traceback)``;
    the message is formatted with any traceback appended."""

    def __init__(self):
        super().__init__()
        self.records: list[tuple[int, str, bool]] = []

    def emit(self, record):
        self.records.append(
            (record.levelno, self.format(record), record.exc_info is not None)
        )


@contextmanager
def log_lines(name, level=None):
    """Listen to logger ``name`` directly (at ``level`` while listening), and
    yield its `LogLines` records."""
    handler = LogLines()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(name)
    before = logger.level
    if level is not None:
        logger.setLevel(level)
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(before)


@pytest.fixture
def mcp_log():
    """FastMCP's log lines. Its logger does not propagate, so it is listened to
    directly."""
    with log_lines(FASTMCP_LOGGER) as lines:
        yield lines


@pytest.fixture(autouse=True)
def _no_config_environment(request, monkeypatch):
    """No test reads the runner's config: every setting's env name and CONFIG_FILE are
    unset, as quick-mcp's conftest does. An integration test's GRID_URL is its input."""
    if request.node.get_closest_marker("integration"):
        return
    for name in {leaf.env for leaf in config.leaves()}:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def fresh_connections():
    """`FakeConnection` keeps the connections it made, and the switches a test
    flips, on the class: every test starts and ends with them cleared."""
    FakeConnection.reset()
    yield
    FakeConnection.reset()


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
    '<div id="root" data-mount="__MOUNT__" data-console="__CONSOLE__"'
    ' data-oidc="__OIDC__"></div>'
    '<script type="module">__JS__</script>'
)


@pytest.fixture
def built_ui(ui_dir):
    """A stand-in for the build: the seven files the server reads, recognisable by
    content. The eighth, ``live.js.LICENSE.txt``, ships beside ``live.js`` and is
    never served, so nothing here needs it."""
    for name in ("admin", "app"):
        (ui_dir / f"{name}.html").write_text(SHELL.format(name=name))
        (ui_dir / f"{name}.css").write_text(f"/* {name} css */")
        (ui_dir / f"{name}.js").write_text(f"/* {name} js */")
    (ui_dir / "live.js").write_text("/* live js */")
    return ui_dir


@pytest.fixture
def issuer():
    """A loopback OIDC issuer: its JWKS is fetched for real."""
    from .jwks import Issuer

    made = Issuer()
    yield made
    made.close()


# ---- what the tools are ------------------------------------------------------


@pytest.fixture
async def step_schema_map(server):
    """The real tool schemas, built the way production builds them.

    From ENDPOINTS rather than from a listing — a listing is shaped per request
    and, since flows gained tools of their own, would have offered `save_flow`
    as a valid step. `flowapi.RUNNABLE` refuses that at run time, so a looser
    map here would have let validation and execution disagree.
    """
    from kubed.selenium_flow.core.capabilities import ENDPOINTS

    tools = {}
    for name in sorted(set(ENDPOINTS.values())):
        full = await server.mcp.get_tool(name)
        tools[name] = full.parameters or {}
    return step_schemas(tools)


@pytest.fixture
def pointer_lands(monkeypatch):
    """`pointer.move` answers as if the pointer arrived, so a gesture can run
    against the scripted driver without a browser's geometry."""
    monkeypatch.setattr(
        pointer,
        "move",
        lambda *a, **k: {
            "at": (10.0, 10.0), "glided": False, "nudged": False,
            "unknown_start": False,
            # A move reports the window's size it read on landing.
            "viewport": (1000, 800),
        },
    )
