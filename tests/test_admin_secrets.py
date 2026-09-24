"""The Secrets tab's one endpoint: the catalogue, and which flows type each (§F4.10)."""

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.flows import library as flows
from kubed.selenium_flow.http import secret_uses
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _flow(store, lib, name, steps):
    store.save(lib, name, {"description": name, "steps": steps})


def _types(secret, key, css="#x"):
    return {"tool": "write", "args": {"selector": {"css": css}, "secret": {"name": secret, "key": key}}}


NAV = {"tool": "navigate", "args": {"url": "https://example.test/"}}


@pytest.fixture
def store(tmp_path):
    return flows.LocalFlowStore(tmp_path / "flows")


def test_a_flow_that_types_a_secret_is_a_use_with_its_steps(store):
    _flow(store, "desktop", "login", [NAV, _types("demo", "username"), _types("demo", "password")])
    assert secret_uses.uses(store) == {
        "demo": [{"session": "desktop", "flow": "login", "steps": [2, 3], "keys": ["username", "password"], "shared": False}]
    }


def test_a_shared_flow_is_marked_shared(store):
    _flow(store, flows.GLOBAL_SESSION, "nc", [_types("nextcloud", "password")])
    assert secret_uses.uses(store)["nextcloud"][0]["shared"] is True


def test_a_flow_with_no_secret_is_not_a_use(store):
    _flow(store, "desktop", "search", [NAV])
    assert secret_uses.uses(store) == {}


def test_a_broken_flow_does_not_take_the_listing_down(store, tmp_path):
    _flow(store, "desktop", "login", [_types("demo", "password")])
    (tmp_path / "flows" / "desktop" / "flows" / "broken.yaml").write_text("steps: [", encoding="utf-8")
    assert list(secret_uses.uses(store)) == ["demo"]


@pytest.fixture
def secrets_dir(tmp_path):
    d = tmp_path / "secrets" / "demo"
    d.mkdir(parents=True)
    (d / "username").write_text("u")
    (d / "password").write_text("p")
    return tmp_path / "secrets"


def test_the_endpoint_joins_the_catalogue_to_its_uses(tmp_path, secrets_dir):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN,
                      flow_data_dir=str(tmp_path / "flows"), secrets_dirs=str(secrets_dir))
    _flow(srv.flows, "desktop", "login", [_types("demo", "password"), _types("gone", "token")])
    body = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).json()
    assert body["enabled"] is True
    demo = next(s for s in body["secrets"] if s["name"] == "demo")
    assert demo["uses"][0]["flow"] == "login"
    assert body["undefined"] == [{"name": "gone", "uses": [
        {"session": "desktop", "flow": "login", "steps": [2], "keys": ["token"], "shared": False}]}]


def test_no_value_is_ever_sent(tmp_path, secrets_dir):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN, secrets_dirs=str(secrets_dir))
    text = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).text
    assert '"u"' not in text and '"p"' not in text


def test_it_needs_the_token(tmp_path):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)
    assert TestClient(srv.mcp.http_app()).get("/admin/secrets").status_code == 401


def test_with_no_catalogue_it_says_off(tmp_path):
    srv = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)
    body = TestClient(srv.mcp.http_app()).get("/admin/secrets", headers=AUTH).json()
    assert body == {"enabled": False, "count": 0, "secrets": [], "undefined": []}
