"""The Settings tab's one endpoint."""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    # auth.token from env, redis.password from the file, serving.port from args.
    path = tmp_path / "config.yaml"
    path.write_text("redis:\n  password: pw!\n", encoding="utf-8")
    server = SeleniumMCP(
        argv=["--config-file", str(path), "--port", "8000"],
        environ={"GRID_URL": "http://grid.invalid:4444", "AUTH_TOKEN": TOKEN},
    )
    return TestClient(server.mcp.server.http_app())


def test_it_needs_the_token(client):
    assert client.get("/admin/settings").status_code == 401


def test_every_section_and_nothing_sensitive(client):
    body = client.get("/admin/settings", headers=AUTH).json()
    assert next(s["name"] for s in body["sections"]) == "server"
    rows = {r["key"]: r for s in body["sections"] for r in s["settings"]}
    assert rows["auth.token"]["value"] is None and rows["auth.token"]["set"] is True
    assert rows["redis.password"]["source"] == "config"
    assert TOKEN not in str(body) and "pw!" not in str(body)
    assert "secrets.entries" not in rows
