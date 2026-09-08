"""The HTTP surface, driven through the real ASGI app."""

import pytest
from starlette.testclient import TestClient

from .conftest import TOKEN

pytestmark = pytest.mark.unit


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


@pytest.fixture
def open_client(open_server):
    return TestClient(open_server.mcp.http_app())


def test_health_needs_no_credentials(client):
    """A kubelet has no token, so the probe must not require one."""
    response = client.get("/health")
    # The Grid address is unroutable in tests, so degraded is the honest answer
    # — what matters is that the request was not rejected for auth.
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert "grid" in body


def test_endpoints_reject_a_missing_token(client):
    response = client.post("/browser/open", json={})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_endpoints_reject_a_wrong_token(client):
    response = client.post(
        "/browser/open", json={}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_a_good_token_gets_past_auth(client):
    """It fails on the unreachable Grid, not on the credential."""
    response = client.post(
        "/browser/open", json={}, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 500
    assert "error" in response.json()


def test_missing_required_argument_is_a_400_not_a_500(open_client):
    response = open_client.post("/browser/click", json={"session_id": "x"})
    assert response.status_code == 400
    assert "xpath" in response.json()["error"]


def test_unknown_keys_are_dropped_rather_than_rejected(open_client):
    """A caller on a newer client should not hard-fail on an extra field."""
    response = open_client.post(
        "/browser/click",
        json={"session_id": "x", "xpath": "//a", "not_a_real_field": 1},
    )
    assert response.status_code == 500  # reached the Grid, not a 400


def test_a_non_object_body_is_rejected(open_client):
    response = open_client.post("/browser/open", json=[1, 2, 3])
    assert response.status_code == 400


def test_press_key_rejects_an_unknown_key(open_client):
    response = open_client.post(
        "/browser/press-key", json={"session_id": "x", "key": "banana"}
    )
    assert response.status_code == 400
    assert "unknown key" in response.json()["error"]


def test_every_endpoint_is_mounted(open_client):
    """A 404 here means the route table and the app disagree."""
    from kubed.selenium_flow.routes import ENDPOINTS

    for path in ENDPOINTS:
        response = open_client.post(f"/browser/{path}", json={})
        assert response.status_code != 404, f"/browser/{path} is not mounted"
