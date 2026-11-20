"""How the server is built and run: `create_app`, `QuickMCP`, `run()` and the
container behind `Inject[Actions]`."""

import pytest
from fastmcp import Client
from kubed.mcp import Inject, QuickMCP

from kubed.selenium_flow import main as main_module
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.server import create_app, http_middleware

from .conftest import NAMED, calling_as
from .fakes import untabbed

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
    tool = await acting.mcp.server.get_tool("navigate")
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
    tool = await acting.mcp.server.get_tool("navigate")
    tool.fn(url="https://example.test/direct", actions=navigating)
    assert navigating.sent == [("b1", "https://example.test/direct")]
