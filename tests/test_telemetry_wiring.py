"""selenium-flow's telemetry in the server: built only behind `telemetry.otel`,
heard on the bus, and asked of the Grid briefly."""

import asyncio

import pytest

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core.browser import SHOWN_TIMEOUT
from kubed.selenium_flow.monitor.events import CallFinished
from kubed.selenium_flow.server import create_app
from kubed.selenium_flow.telemetry import CALL_DURATION, Telemetry

from .fakes import points, recorded

pytestmark = pytest.mark.unit

GRID = {"url": "http://grid.invalid:4444"}


def test_with_otel_off_nothing_is_wired():
    server = create_app(Settings(grid=GRID))
    assert server.telemetry is None and server.workspaces.calls.telemetry is None
    assert "metrics" not in [s.name for s in server.bus._subs]


def test_with_otel_on_a_telemetry_is_built_on_the_global_providers():
    server = create_app(Settings(grid=GRID, telemetry={"otel": True}))  # never served
    assert isinstance(server.telemetry, Telemetry)
    assert server.workspaces.calls.telemetry is server.telemetry
    assert "metrics" in [s.name for s in server.bus._subs]


def test_an_injected_telemetry_hears_every_finished_call():
    rec = recorded()
    server = create_app(Settings(grid=GRID), telemetry=rec.telemetry)
    server.bus.publish(CallFinished("ws", "g", "chrome", "navigate", "mcp", "ok", 0.0, 1.5))
    [(attributes, point)] = points(rec.metrics, CALL_DURATION)
    assert attributes["selenium_flow.surface"] == "mcp" and point.sum == 1.5


def test_the_gauge_reads_the_grid_briefly_and_once(monkeypatch):
    rec = recorded()
    server = create_app(Settings(grid=GRID), telemetry=rec.telemetry)
    asked = []
    monkeypatch.setattr(
        server.grid, "status", lambda **kw: asked.append(kw) or {"value": {"nodes": []}}
    )
    rec.metrics.get_metrics_data()
    assert asked == [{"timeout": SHOWN_TIMEOUT, "retry": False}]


async def test_the_servers_lifespan_stops_the_telemetry_at_its_end(monkeypatch):
    rec = recorded()
    server = create_app(Settings(grid=GRID), telemetry=rec.telemetry)
    asked = []
    monkeypatch.setattr(server.grid, "status", lambda **kw: asked.append(kw) or {})
    app = server.mcp.server.http_app()
    async with app.router.lifespan_context(app):
        pass
    # The SDK's last collection runs on the reader's thread, never the loop.
    await asyncio.to_thread(rec.metrics.get_metrics_data)
    assert asked == []
