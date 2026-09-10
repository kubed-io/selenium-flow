"""The HTTP surface, driven through the real ASGI app."""

import pytest
import requests
import urllib3.exceptions
from selenium.common.exceptions import (
    InvalidArgumentException,
    InvalidSelectorException,
    InvalidSessionIdException,
    JavascriptException,
    NoSuchElementException,
    SessionNotCreatedException,
    TimeoutException,
    WebDriverException,
)
from starlette.testclient import TestClient

from kubed.selenium_flow import errors

from .conftest import TOKEN

pytestmark = pytest.mark.unit

# The Grid address in these tests is unroutable, so any request that gets past
# validation dies trying to reach it. That is the sentinel for "the input was
# accepted": a 400 would mean the request itself was refused.
#
# It is 503 rather than 500 because an unreachable Grid is exactly what 503 is
# for — this server is fine, its dependency is not, and the caller should retry
# rather than change anything. Named so the distinction is stated once instead
# of being a bare number in eight assertions.
GRID_DOWN = 503


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
    assert response.status_code == GRID_DOWN
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
    assert response.status_code == GRID_DOWN  # reached the Grid, not a 400


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

    Reaching the Grid means the text was accepted; a 400 would mean the input
    was rejected.
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
    assert response.status_code == GRID_DOWN, response.json()


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

    Reaching the Grid is the *right* answer here: the body parsed and the
    action ran. A 400 would mean the file never arrived.
    """
    response = open_client.post(
        "/browser/upload",
        data={"session_id": "x", "xpath": "//input"},
        files={"content": ("report.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == GRID_DOWN, response.json()


def test_a_multipart_filename_can_be_overridden(open_client):
    response = open_client.post(
        "/browser/upload",
        data={"session_id": "x", "xpath": "//input", "filename": "renamed.csv"},
        files={"content": ("original.csv", b"x", "text/csv")},
    )
    assert response.status_code == GRID_DOWN, response.json()


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

    Reaching the Grid means it got past validation.
    """
    response = open_client.post(
        "/browser/frame", json={"session_id": "x", "action": "default"}
    )
    assert response.status_code == GRID_DOWN, response.json()


def test_a_non_object_body_is_rejected(open_client):
    response = open_client.post("/browser/open", json=[1, 2, 3])
    assert response.status_code == 400


def test_press_key_rejects_an_unknown_key(open_client):
    response = open_client.post(
        "/browser/press-key", json={"session_id": "x", "key": "banana"}
    )
    assert response.status_code == 400
    assert "unknown key" in response.json()["error"]


def test_an_unsupported_browser_is_a_400_with_the_real_list(open_client):
    """The settings cascade validates as well as merges, and it used to run
    outside the handler's try — so this ValueError escaped as a bare 500 with no
    body, while the MCP surface answered with the message below. The surfaces
    may differ in return shape, never in whether an error is usable.
    """
    response = open_client.post("/browser/open", json={"browser": "safari"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert "safari" in error
    for name in ("chrome", "firefox"):
        assert name in error


def test_every_endpoint_is_mounted(open_client):
    """A 404 here means the route table and the app disagree."""
    from kubed.selenium_flow.routes import ENDPOINTS

    for path in ENDPOINTS:
        response = open_client.post(f"/browser/{path}", json={})
        assert response.status_code != 404, f"/browser/{path} is not mounted"


def test_the_old_close_path_still_works(open_server):
    """`/browser/close` became `/browser/end` when the action was renamed.

    Its callers cannot be found and updated — an n8n workflow lives in a
    database, not in this repo — so the old path stays mapped to the same
    action. It is deliberately not in ENDPOINTS, so the spec and the wiki
    describe one name per action rather than advertising both.
    """
    from unittest.mock import patch

    from kubed.selenium_flow import browser as browser_module
    from kubed.selenium_flow.routes import ENDPOINTS, LEGACY_PATHS

    assert "close" not in ENDPOINTS, "the legacy path must not be advertised"
    assert LEGACY_PATHS["close"] == "end_browser"

    client = TestClient(open_server.mcp.http_app())
    with patch.object(browser_module.Grid, "quit") as quit_:
        response = client.post("/browser/close", json={"session_id": "abc"})
    assert response.status_code == 200
    quit_.assert_called_once_with("abc")


# ---- what a failure means --------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        # The caller's request cannot succeed as sent. Retrying it unchanged is
        # guaranteed to fail again, so a workflow should stop, not back off.
        (TimeoutException("no element matched '//nope'"), 400),
        (InvalidSelectorException("//[[["), 400),
        (NoSuchElementException("//gone"), 400),
        (JavascriptException("boom"), 400),
        (InvalidArgumentException("-5 is outside of i32"), 400),
        (ValueError("unknown key 'banana'"), 400),
        (TypeError("missing 1 required positional argument: 'xpath'"), 400),
        # The browser named is not on the Grid. Its own code because the fix is
        # specific and automatable: open a new one.
        (InvalidSessionIdException("invalid session id"), 404),
        # The Grid cannot serve this now. Worth retrying, unlike everything above.
        (SessionNotCreatedException("no free slot"), 503),
        (requests.ConnectionError("refused"), 503),
        (urllib3.exceptions.MaxRetryError(None, "http://grid.invalid"), 503),
        # Unrecognised stays ours. Claiming the caller's fault about something we
        # do not understand tells them to stop retrying a problem that may be ours.
        (RuntimeError("something new"), 500),
    ],
)
def test_a_failure_is_classified_by_what_the_caller_should_do(exc, expected):
    assert errors.status_for(exc) == expected


def test_the_message_drops_the_driver_internals():
    """Selenium's str() is the useful line, then twenty lines of chrome://.

    An agent and an n8n branch both have to read this field, and the stack trace
    is noise in it — plus the bare "Message:" prefix is the artefact AGENTS.md
    already calls out as useless.
    """
    raw = TimeoutException(
        "no element matched '//nope' within 3s"
    )
    assert errors.message(raw) == "no element matched '//nope' within 3s"

    noisy = WebDriverException(
        "Message: Error: boom\nStacktrace:\nRemoteError@chrome://remote/x.mjs:8:8"
    )
    assert errors.message(noisy) == "Error: boom"


def test_an_error_with_nothing_to_say_still_says_something():
    """Empty is worse than a class name, which at least names the kind."""
    assert errors.message(TimeoutException("")) == "TimeoutException"
