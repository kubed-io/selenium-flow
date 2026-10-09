"""The Secrets tab's one endpoint: the catalogue, never a value."""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def secrets_dir(tmp_path):
    d = tmp_path / "secrets" / "demo"
    d.mkdir(parents=True)
    (d / "username").write_text("u")
    (d / "password").write_text("p")
    return tmp_path / "secrets"


def test_the_endpoint_lists_the_catalogue(tmp_path, secrets_dir):
    srv = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN},
        data={"dir": str(tmp_path / "flows")}, secrets={"dirs": str(secrets_dir)},
    ))
    body = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).json()
    assert body["enabled"] is True
    demo = next(s for s in body["secrets"] if s["name"] == "demo")
    assert demo["name"] == "demo"
    assert "uses" not in demo
    assert "uses" not in body
    assert "undefined" not in body


def test_no_value_is_ever_sent(tmp_path, secrets_dir):
    srv = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN},
        secrets={"dirs": str(secrets_dir)},
    ))
    text = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).text
    assert '"u"' not in text and '"p"' not in text


def test_it_needs_the_token(tmp_path):
    srv = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )
    assert TestClient(srv.mcp.http_app()).get("/admin/secrets").status_code == 401


def test_with_no_catalogue_it_says_off(tmp_path):
    srv = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN})
    )
    body = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).json()
    assert body == {"enabled": False, "count": 0, "secrets": []}


def test_a_broken_store_answers_through_errors_not_a_bare_500(
    tmp_path, secrets_dir, monkeypatch
):
    """`catalogue.listing` used to run unguarded, so an ``OSError`` from a store
    gone read-only or unmounted fell straight through to Starlette's own handler
    instead of the scrubbed, logged answer every other route on this surface
    gives (§AGENTS.md, errors.py)."""
    from kubed.selenium_flow import secrets as secrets_module

    srv = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN},
        data={"dir": str(tmp_path / "flows")}, secrets={"dirs": str(secrets_dir)},
    ))

    def boom(self, workspace=""):
        raise OSError("secrets store is unmounted")

    monkeypatch.setattr(secrets_module.Catalogue, "listing", boom)
    resp = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH)
    assert resp.status_code == 500
    assert resp.json() == {"error": "secrets store is unmounted"}
