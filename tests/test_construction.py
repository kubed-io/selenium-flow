"""How the server is built and run: `create_app`, `QuickMCP`, `run()` and the
container behind `Inject[Actions]`."""

import pytest
from fastmcp import Client
from kubed.mcp import Inject, QuickMCP

from kubed.selenium_flow import main as main_module
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.http.access_log import RouteTemplates
from kubed.selenium_flow.http.slashes import RelativeSlashRedirects
from kubed.selenium_flow.http.tracing import RouteSpans
from kubed.selenium_flow.mcp import tools as tools_module
from kubed.selenium_flow.mcp.tools import _require_injected_actions
from kubed.selenium_flow.server import create_app, http_middleware

from .conftest import NAMED, calling_as
from .fakes import recorded, untabbed

pytestmark = pytest.mark.unit

GRID = {"url": "http://grid.invalid:4444"}


async def test_create_app_builds_a_quickmcp_with_the_settings_as_its_config():
    settings = Settings(grid=GRID)
    server = create_app(settings)
    assert isinstance(server.mcp, QuickMCP)

    @server.mcp.tool
    def configured(config: Inject[Settings]) -> bool:
        return config is settings

    async with Client(server.mcp.server) as client:
        called = await client.call_tool("configured", {})
    assert called.structured_content == {"result": True}


@pytest.fixture
def ran(monkeypatch):
    """Boot through `main` without serving: each `QuickMCP.run` call's
    ``(serving, http_options)``."""
    calls = []
    monkeypatch.setattr(
        QuickMCP, "run",
        lambda self, serving=None, **options: calls.append((serving, options)),
    )
    for name in ("AUTH_TOKEN", "CONFIG_FILE", "PORT", "HOST", "LOG_LEVEL", "TRANSPORT"):
        monkeypatch.delenv(name, raising=False)
    return calls


def _config(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return ["--config-file", str(path)]


def test_main_serves_http_through_run_with_the_config_s_settings(tmp_path, ran):
    main_module.main(_config(tmp_path, "route_prefix: /flow\n"))
    [(serving, options)] = ran
    # No Serving of its own: `run()` reads transport, host and port off the config.
    assert serving is None
    assert options["path"] == "/flow/mcp"
    assert [m.cls for m in options["middleware"]] == [
        m.cls for m in http_middleware()
    ]


def test_main_serves_the_route_spans_when_otel_is_on(tmp_path, ran):
    main_module.main(_config(tmp_path, "telemetry:\n  otel: true\n"))
    [(_serving, options)] = ran
    assert [m.cls for m in options["middleware"]] == [RouteSpans, RelativeSlashRedirects]


def test_main_serves_stdio_through_run_too(tmp_path, ran):
    main_module.main(_config(tmp_path, "transport: stdio\n"))
    [(serving, _options)] = ran
    assert serving is None


@pytest.fixture
def acting(monkeypatch):
    """An open server whose every act runs straight on browser ``b1``."""
    server = untabbed(create_app(Settings(grid=GRID)))
    calling_as(monkeypatch, NAMED)
    monkeypatch.setattr(
        server.workspaces, "act", lambda caller, call, **kw: call("b1")
    )
    return server


class Navigating:
    """An `Actions` stand-in that records where it was sent."""

    def __init__(self):
        self.sent = []

    def navigate(self, session_id, **arguments):
        self.sent.append((session_id, arguments["url"]))
        return {"url": arguments["url"], "title": "Sent"}


async def test_an_acting_tool_hides_its_injected_actions_from_the_schema(acting):
    tool = await acting.mcp.get_tool("navigate")
    assert "actions" not in tool.parameters["properties"]


async def test_an_acting_tool_is_handed_the_actions_singleton(acting):
    navigating = Navigating()
    acting.mcp.override(Actions, navigating)
    async with Client(acting.mcp.server) as client:
        await client.call_tool("navigate", {"url": "https://example.test/"})
    assert navigating.sent == [("b1", "https://example.test/")]


async def test_the_actions_singleton_is_the_server_s_own(acting):
    @acting.mcp.tool
    def same(actions: Inject[Actions]) -> bool:
        return actions is acting.actions

    async with Client(acting.mcp.server) as client:
        called = await client.call_tool("same", {})
    assert called.structured_content == {"result": True}


async def test_an_acting_tool_called_directly_uses_the_actions_it_is_given(acting):
    navigating = Navigating()
    tool = await acting.mcp.get_tool("navigate")
    tool.fn(url="https://example.test/direct", actions=navigating)
    assert navigating.sent == [("b1", "https://example.test/direct")]


def test_an_action_without_actions_stops_registration():
    def navigate(url: str):
        """Navigate."""

    with pytest.raises(TypeError, match="navigate: an action takes actions"):
        _require_injected_actions("navigate", navigate)


def test_an_action_with_bare_actions_stops_registration():
    # A bare `Actions` is a parameter FastMCP would put in the tool's schema.
    def navigate(url: str, *, actions: Actions):
        """Navigate."""

    with pytest.raises(TypeError, match="navigate: an action takes actions"):
        _require_injected_actions("navigate", navigate)


def test_building_the_server_refuses_an_action_whose_actions_is_not_injected(
    monkeypatch,
):
    # Every action's `actions: "Inject[Actions]"` is read in the tools module,
    # so an `Inject` there that gives a bare `Actions` breaks each declaration:
    # what an action declared with a plain `actions: Actions` would be.
    class Bare:
        def __class_getitem__(cls, item):
            return item

    monkeypatch.setattr(tools_module, "Inject", Bare)
    with pytest.raises(TypeError, match="an action takes actions: Inject"):
        create_app(Settings(grid=GRID))


def test_an_action_with_injected_actions_registers_written_either_way():
    def typed(url: str, *, actions: Inject[Actions]):
        """Navigate."""

    # What a module with `from __future__ import annotations` declares.
    def written(url: str, *, actions: "Inject[Actions]"):
        """Navigate."""

    _require_injected_actions("navigate", typed)
    _require_injected_actions("navigate", written)


def test_with_telemetry_the_route_spans_are_outermost():
    rec = recorded()
    built = http_middleware(rec.telemetry, templates=RouteTemplates(()), prefix="")
    assert [m.cls for m in built] == [RouteSpans, RelativeSlashRedirects]
    assert [m.cls for m in http_middleware()] == [RelativeSlashRedirects]
    with pytest.raises(ValueError):
        http_middleware(rec.telemetry)
