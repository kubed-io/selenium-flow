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


# Every request names its session the way the surface says to (§F2.13). It is a
# default header on the client rather than a keyword on each call, because it is
# a property of the caller, not of the request.
SESSION = "desktop"


@pytest.fixture
def client(server, monkeypatch):
    monkeypatch.setattr(server.sessions, "resolve", lambda name: "browser-1")
    return TestClient(server.mcp.http_app(), headers={"X-Session-Key": SESSION})


@pytest.fixture
def open_client(open_server, monkeypatch):
    """A client with auth off, whose session already holds a browser.

    These tests are about this surface's own validation, so the browser is
    resolved out of the way: the Grid address is unroutable, and reaching it is
    the sentinel for "the input was accepted".
    """
    monkeypatch.setattr(open_server.sessions, "resolve", lambda name: "browser-1")
    return TestClient(open_server.mcp.http_app(), headers={"X-Session-Key": SESSION})


@pytest.mark.parametrize(
    ("probe", "status"),
    [("/health", 200), ("/started", 200), ("/ready", GRID_DOWN), ("/info", 200)],
)
def test_the_ops_endpoints_need_no_credentials(client, probe, status):
    """A kubelet has no token, so none of these may require one. `/ready` is the
    only one that asks the Grid, and the Grid is unroutable here."""
    assert client.get(probe).status_code == status


def test_liveness_does_not_depend_on_the_grid(client):
    """The split Dr K asked for. A liveness probe that failed on a Grid outage
    would restart every replica for a fault in another service, which is why
    this cluster was probing `/openapi.json` instead."""
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/started").json() == {"status": "started"}


def test_readiness_is_the_one_that_asks_the_grid(client):
    """A server that cannot reach a Grid can accept a call and do nothing with
    it, so this is where a 503 belongs: out of the Service, still running."""
    response = client.get("/ready")
    assert response.status_code == GRID_DOWN
    assert response.json()["status"] == "degraded"


def test_no_ops_endpoint_hands_out_the_grids_credentials(monkeypatch):
    """`GRID_URL` may carry userinfo, and these answer to anyone."""
    import requests

    from kubed.selenium_flow.server import SeleniumMCP

    grid = "http://user:hunter2@[fd00::1]:4444"
    server = SeleniumMCP(grid_url=grid)

    def quotes_the_url():  # a parse or proxy error, not the HTTPError already cut
        raise requests.exceptions.InvalidURL(f"Failed to parse: {grid}/status")

    monkeypatch.setattr(server.actions.grid, "status", quotes_the_url)
    client = TestClient(server.mcp.http_app())
    for probe in ("/ready", "/info"):
        body = client.get(probe).text
        assert "hunter2" not in body and "user:" not in body, probe
    # Still a usable address: an IPv6 host keeps its brackets (Copilot, #35).
    assert client.get("/info").json()["grid"] == "http://[fd00::1]:4444"


def test_endpoints_reject_a_missing_token(client):
    response = client.post("/browser", json={})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_endpoints_reject_a_wrong_token(client):
    response = client.post(
        "/browser", json={}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_a_good_token_gets_past_auth(client):
    """It fails on the unreachable Grid, not on the credential."""
    response = client.post(
        "/browser", json={}, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == GRID_DOWN
    assert "error" in response.json()


def test_the_mouse_action_is_the_path_not_a_field(open_client):
    """`/browser/interact/click` reads as the thing it does, and the action can
    no longer be omitted: without one there is no route (§F2.13)."""
    assert open_client.post("/browser/interact", json={}).status_code in (404, 405)


def test_naming_no_element_is_a_400_that_says_how_to_address_one(open_client):
    """`xpath` stopped being required when `css` was added, so the schema can no
    longer catch this — the boundary check has to, and it has to say both."""
    response = open_client.post("/browser/interact/click", json={})
    assert response.status_code == 400
    error = response.json()["error"]
    assert "xpath" in error and "css" in error


def test_naming_both_elements_is_a_400_rather_than_a_silent_choice(open_client):
    response = open_client.post(
        "/browser/interact/click",
        json={"selector": {"xpath": "//a", "css": "a"}},
    )
    assert response.status_code == 400
    assert "not both" in response.json()["error"]


def test_unknown_keys_are_dropped_rather_than_rejected(open_client):
    """A caller on a newer client should not hard-fail on an extra field."""
    response = open_client.post(
        "/browser/interact/click",
        json={"selector": {"xpath": "//a"}, "not_a_real_field": 1},
    )
    assert response.status_code == GRID_DOWN  # reached the Grid, not a 400


def test_interact_rejects_an_unknown_action(open_client):
    """The error names the real list, so a model can correct itself."""
    response = open_client.post(
        "/browser/interact/karate", json={"selector": {"xpath": "//a"}}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "karate" in error
    for known in ("click", "hover", "scroll_to"):
        assert known in error


def test_dialog_rejects_an_unknown_action(open_client):
    response = open_client.post(
        "/browser/dialog", json={"action": "shout"}
    )
    assert response.status_code == 400
    assert "shout" in response.json()["error"]


def test_dialog_send_text_requires_text(open_client):
    response = open_client.post(
        "/browser/dialog", json={"action": "send_text"}
    )
    assert response.status_code == 400
    assert "text is required" in response.json()["error"]


def test_upload_refuses_more_than_one_source(open_client):
    """Two sources for one file is a caller mistake worth naming precisely."""
    response = open_client.post(
        "/browser/upload",
        json={"selector": {"xpath": "//input"}, "content": "eA==", "path": "/tmp/x"},
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
            "selector": {"xpath": "//input"},
            "text": '{"generated": true}',
            "filename": "data.json",
        },
    )
    assert response.status_code == GRID_DOWN, response.json()


def test_upload_needs_some_kind_of_file(open_client):
    response = open_client.post(
        "/browser/upload", json={"selector": {"xpath": "//input"}}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "text" in error and "content" in error and "path" in error


def test_upload_rejects_content_that_is_not_base64(open_client):
    """And points at the multipart form, which is the easier way over HTTP."""
    response = open_client.post(
        "/browser/upload",
        json={"selector": {"xpath": "//input"}, "content": "definitely not base64!"},
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
        data={"selector": '{"xpath": "//input"}'},
        files={"content": ("report.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert response.status_code == GRID_DOWN, response.json()


def test_a_multipart_filename_can_be_overridden(open_client):
    response = open_client.post(
        "/browser/upload",
        data={"selector": '{"xpath": "//input"}', "filename": "renamed.csv"},
        files={"content": ("original.csv", b"x", "text/csv")},
    )
    assert response.status_code == GRID_DOWN, response.json()


def test_frame_rejects_an_unknown_action(open_client):
    response = open_client.post(
        "/browser/frame", json={"action": "sideways"}
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "sideways" in error
    for known in ("switch", "parent", "default"):
        assert known in error


def test_frame_switch_needs_a_target(open_client):
    """Switching without saying which frame is a caller mistake, not a default."""
    response = open_client.post("/browser/frame", json={"action": "switch"})
    assert response.status_code == 400
    assert "selector" in response.json()["error"]


def test_frame_default_needs_no_target(open_client):
    """Going back to the main page is unambiguous, so it takes no arguments.

    Reaching the Grid means it got past validation.
    """
    response = open_client.post(
        "/browser/frame", json={"action": "default"}
    )
    assert response.status_code == GRID_DOWN, response.json()


def test_a_non_object_body_is_rejected(open_client):
    response = open_client.post("/browser", json=[1, 2, 3])
    assert response.status_code == 400


def test_press_key_rejects_an_unknown_key(open_client):
    response = open_client.post(
        "/browser/press-key", json={"key": "banana"}
    )
    assert response.status_code == 400
    assert "unknown key" in response.json()["error"]


def test_an_unsupported_browser_is_a_400_with_the_real_list(open_client):
    """The settings cascade validates as well as merges, and it used to run
    outside the handler's try — so this ValueError escaped as a bare 500 with no
    body, while the MCP surface answered with the message below. The surfaces
    may differ in return shape, never in whether an error is usable.
    """
    response = open_client.post("/browser", json={"browser": "safari"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert "safari" in error
    for name in ("chrome", "firefox"):
        assert name in error


def test_every_endpoint_is_mounted(open_client):
    """A 404 here means the route table and the app disagree."""
    from kubed.selenium_flow.routes import ACTION_IN_PATH, ENDPOINTS

    for path, action in ENDPOINTS.items():
        route = f"/browser/{path}" + ("/click" if action == ACTION_IN_PATH else "")
        response = open_client.post(route, json={})
        assert response.status_code != 404, f"{route} is not mounted"


def test_the_browser_is_one_resource_addressed_by_naming_yourself(open_client):
    """Opening, reading and ending are methods on it rather than paths of their
    own, because which browser is a question about who is asking (§F2.13)."""
    assert open_client.get("/browser").status_code == 200
    assert open_client.delete("/browser").status_code == 200


def test_a_request_that_names_no_session_is_refused(open_server):
    """The one contract, on this surface too: there is no browser to act on
    until a caller says who it is."""
    bare = TestClient(open_server.mcp.http_app())
    response = bare.post("/browser/navigate", json={"url": "https://example.test"})
    assert response.status_code == 400
    assert "name your session" in response.json()["error"]


def test_naming_the_session_twice_is_refused(open_client):
    response = open_client.post(
        "/browser/navigate",
        json={"url": "https://example.test"},
        params={"session": "from-url"},
    )
    assert response.status_code == 400
    assert "once" in response.json()["error"]


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


# ---- where the whole server hangs ------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [("/", ""), ("", ""), (None, ""), ("/flow", "/flow"), ("flow/", "/flow")],
)
def test_the_prefix_is_a_mount_point_and_slash_means_root(given, expected):
    """`/` and `""` both mean root: `/` is what an operator types when they mean
    no prefix, and taking it literally would make every path start `//`."""
    from kubed.selenium_flow.routes import mount

    assert mount(given) == expected


def test_every_tree_moves_with_the_prefix():
    """The inversion §F1.11 asked for: `ROUTE_PREFIX` used to rename `/browser`
    while `/mcp`, `/admin` and `/files` stayed fixed. Nobody wants the browser
    endpoints called something else; everybody eventually wants the server
    mounted under a path."""
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, route_prefix="/flow"
    )
    app = server.mcp.http_app(path=server.mcp_path)  # what `run` serves
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    for tree in (
        "/flow/browser", "/flow/flows", "/flow/files", "/flow/admin/sessions",
        "/flow/mcp", "/flow/openapi.yaml",
    ):
        assert tree in paths, tree
    assert not any(
        p.startswith(("/browser", "/flows", "/files", "/admin", "/mcp", "/openapi"))
        for p in paths
    ), "something stayed behind at the root"


def test_the_ui_is_at_the_mount_root_and_the_old_path_redirects():
    """The UI is what a person gets for visiting the server; `/admin/*` is the
    API that page calls. The root used to 404."""
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, route_prefix="/flow"
    )
    client = TestClient(server.mcp.http_app())
    assert client.get("/flow/").status_code == 200
    moved = client.get("/flow/admin", follow_redirects=False)
    assert moved.status_code == 301
    assert moved.headers["location"] == "/flow/"


def test_the_ops_endpoints_answer_at_the_root_whatever_the_prefix():
    """The one path whose reader did not choose the mount. A readiness probe
    that 404s after a config change is the failure this avoids."""
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, route_prefix="/flow"
    )
    client = TestClient(server.mcp.http_app())
    for probe in ("/health", "/started", "/ready", "/info"):
        assert client.get(probe).status_code in (200, GRID_DOWN), probe
        assert client.get(f"/flow{probe}").status_code in (200, GRID_DOWN), probe


async def test_the_published_spec_describes_the_paths_actually_served():
    """A document that names a path nothing serves is worse than no document,
    and a prefix is exactly where the two drift apart."""
    from kubed.selenium_flow.openapi import build_spec
    from kubed.selenium_flow.routes import ENDPOINTS
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, route_prefix="/flow"
    )
    spec = await build_spec(server.mcp, ENDPOINTS, "/flow", authenticated=True)
    served = {r.path for r in server.mcp.http_app().routes if hasattr(r, "path")}
    assert set(spec["paths"]) <= served, set(spec["paths"]) - served
