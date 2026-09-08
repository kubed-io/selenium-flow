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
    response = open_client.post("/browser/interact", json={"session_id": "x"})
    assert response.status_code == 400
    assert "xpath" in response.json()["error"]


def test_unknown_keys_are_dropped_rather_than_rejected(open_client):
    """A caller on a newer client should not hard-fail on an extra field."""
    response = open_client.post(
        "/browser/interact",
        json={
            "session_id": "x",
            "action": "click",
            "xpath": "//a",
            "not_a_real_field": 1,
        },
    )
    assert response.status_code == 500  # reached the Grid, not a 400


def test_interact_rejects_an_unknown_action(open_client):
    """The error names the real list, so a model can correct itself."""
    response = open_client.post(
        "/browser/interact", json={"session_id": "x", "action": "karate", "xpath": "//a"}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "karate" in error
    for known in ("click", "hover", "scroll_to"):
        assert known in error


def test_dialog_rejects_an_unknown_action(open_client):
    response = open_client.post(
        "/browser/dialog", json={"session_id": "x", "action": "shout"}
    )
    assert response.status_code == 400
    assert "shout" in response.json()["error"]


def test_dialog_send_text_requires_text(open_client):
    response = open_client.post(
        "/browser/dialog", json={"session_id": "x", "action": "send_text"}
    )
    assert response.status_code == 400
    assert "text is required" in response.json()["error"]


def test_upload_refuses_more_than_one_source(open_client):
    """Two sources for one file is a caller mistake worth naming precisely."""
    response = open_client.post(
        "/browser/upload",
        json={"session_id": "x", "xpath": "//input", "content": "eA==", "path": "/tmp/x"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "only one" in error
    assert "content" in error and "path" in error


def test_upload_takes_plain_text_as_the_file(open_client):
    """The ergonomic path: an agent uploading something it just wrote.

    A 500 means the text was accepted and only the unroutable Grid stopped it;
    a 400 would mean the input was rejected.
    """
    response = open_client.post(
        "/browser/upload",
        json={
            "session_id": "x",
            "xpath": "//input",
            "text": '{"generated": true}',
            "filename": "data.json",
        },
    )
    assert response.status_code == 500, response.json()


def test_upload_needs_some_kind_of_file(open_client):
    response = open_client.post(
        "/browser/upload", json={"session_id": "x", "xpath": "//input"}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "text" in error and "content" in error and "path" in error


def test_upload_rejects_content_that_is_not_base64(open_client):
    """And points at the multipart form, which is the easier way over HTTP."""
    response = open_client.post(
        "/browser/upload",
        json={"session_id": "x", "xpath": "//input", "content": "definitely not base64!"},
    )
    assert response.status_code == 400
    assert "base64" in response.json()["error"]
    assert "multipart" in response.json()["error"]


def test_upload_accepts_a_multipart_file(open_client):
    """Sending a file over HTTP should be a file, not base64 inside JSON.

    A 500 here is the *right* answer: the body parsed, the action ran, and only
    the unroutable Grid stopped it. A 400 would mean the file never arrived.
    """
    response = open_client.post(
        "/browser/upload",
        data={"session_id": "x", "xpath": "//input"},
        files={"content": ("report.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == 500, response.json()


def test_a_multipart_filename_can_be_overridden(open_client):
    response = open_client.post(
        "/browser/upload",
        data={"session_id": "x", "xpath": "//input", "filename": "renamed.csv"},
        files={"content": ("original.csv", b"x", "text/csv")},
    )
    assert response.status_code == 500, response.json()


def test_frame_rejects_an_unknown_action(open_client):
    response = open_client.post(
        "/browser/frame", json={"session_id": "x", "action": "sideways"}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "sideways" in error
    for known in ("switch", "parent", "default"):
        assert known in error


def test_frame_switch_needs_a_target(open_client):
    """Switching without saying which frame is a caller mistake, not a default."""
    response = open_client.post(
        "/browser/frame", json={"session_id": "x", "action": "switch"}
    )
    assert response.status_code == 400
    assert "xpath" in response.json()["error"]


def test_frame_default_needs_no_target(open_client):
    """Going back to the main page is unambiguous, so it takes no arguments.

    A 500 means it got past validation to the unroutable Grid.
    """
    response = open_client.post(
        "/browser/frame", json={"session_id": "x", "action": "default"}
    )
    assert response.status_code == 500, response.json()


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
