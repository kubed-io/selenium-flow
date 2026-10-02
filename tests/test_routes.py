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
from starlette.routing import Match
from starlette.testclient import TestClient

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.server import SeleniumMCP

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

    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    grid = "http://user:hunter2@[fd00::1]:4444"
    server = SeleniumMCP(Settings(grid={"url": grid}))

    def quotes_the_url():  # a parse or proxy error, not the HTTPError already cut
        raise requests.exceptions.InvalidURL(f"Failed to parse: {grid}/status")

    monkeypatch.setattr(server.actions.grid, "status", quotes_the_url)
    client = TestClient(server.mcp.http_app())
    for probe in ("/ready", "/info"):
        body = client.get(probe).text
        assert "hunter2" not in body and "user:" not in body, probe
    # Still a usable address: an IPv6 host keeps its brackets (Copilot, #35).
    assert client.get("/info").json()["grid"] == "http://[fd00::1]:4444"


def test_readiness_asks_the_grid_off_the_event_loop(server, monkeypatch):
    """`requests` is synchronous and a Grid that has gone away blocks until it
    times out. On the event loop that stall takes `/health` down with it — the
    outage this liveness/readiness split exists to survive (Copilot, #35)."""
    import sniffio

    on_loop = []

    def status():
        try:
            sniffio.current_async_library()
            on_loop.append(True)
        except sniffio.AsyncLibraryNotFoundError:
            on_loop.append(False)
        return {"value": {"ready": True}}

    monkeypatch.setattr(server.actions.grid, "status", status)
    monkeypatch.setattr(server.actions.grid, "session_count", lambda: 0)
    client = TestClient(server.mcp.http_app())
    assert client.get("/ready").status_code == 200
    assert on_loop == [False], "the Grid was dialled on the event loop"


def _on_the_loop() -> bool:
    import sniffio

    try:
        sniffio.current_async_library()
    except sniffio.AsyncLibraryNotFoundError:
        return False
    return True


def test_a_browser_action_runs_off_the_event_loop(server, monkeypatch):
    """A browser action is synchronous Selenium that can wait for minutes (an
    `assert` up to 900s). Run on the loop, it stalled every other request —
    MCP, the admin event stream and `/health` — until it finished."""
    seen = []

    def act(name, work, reshapes=False):
        seen.append(_on_the_loop())
        return {"success": True}

    monkeypatch.setattr(server.sessions, "act", act)
    client = TestClient(server.mcp.http_app(), headers={"X-Session-Key": SESSION})
    response = client.post(
        "/browser/navigate",
        json={"url": "https://example.com"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200, response.text
    assert seen == [False], "the browser was driven on the event loop"


def test_a_flow_run_runs_off_the_event_loop(server, monkeypatch):
    """A whole flow run is the longest synchronous call there is."""
    from kubed.selenium_flow.flows import api as flow_api

    seen = []

    def run_for(*args, **kwargs):
        seen.append(_on_the_loop())
        return {"success": True}

    monkeypatch.setattr(flow_api, "run_for", run_for)
    client = TestClient(server.mcp.http_app(), headers={"X-Session-Key": SESSION})
    response = client.post(
        "/flows/login/runs", json={}, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 200, response.text
    assert seen == [False], "the flow ran on the event loop"


def test_the_probes_answer_when_the_grid_url_is_malformed():
    """An operator's typo is not a reason for a probe to raise: a kubelet would
    read the 500 as the process being broken, which it is not."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    # No closing bracket: `urlsplit().port` raises on this.
    server = SeleniumMCP(Settings(grid={"url": "http://user:hunter2@[fd00::1:4444"}))
    client = TestClient(server.mcp.http_app())
    info = client.get("/info")
    assert info.status_code == 200 and "hunter2" not in info.text
    ready = client.get("/ready")
    assert ready.status_code == GRID_DOWN and "hunter2" not in ready.text


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
        json={"selector": {"xpath": "//input"}, "content": "eA==", "text": "x"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "only one" in error
    assert "content" in error and "text" in error


def test_upload_drops_a_server_path_like_any_unknown_field(open_client):
    """Ruling 3. Over HTTP an unknown key is dropped rather than refused, and
    `path` is one now: it reads nothing, and the upload still needs a file."""
    response = open_client.post(
        "/browser/upload",
        json={"selector": {"xpath": "//input"}, "path": "/etc/passwd"},
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error.startswith("the file is required")
    assert "path" not in error and "passwd" not in error


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
    assert "text" in error and "content" in error and "file" in error
    assert "path" not in error


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


@pytest.fixture(scope="module")
def route_table():
    """The app's routes, built once. Nothing here sends a request or changes
    state, so every test in the module may share it."""
    server = SeleniumMCP(Settings(grid={"url": "http://grid.invalid:4444"}))
    return server.mcp.http_app().routes


def test_every_endpoint_is_mounted(route_table):
    """A miss here means the route table and the app disagree. It asks the
    router rather than posting: a POST that gets past validation waits on a DNS
    failure for the unroutable Grid, which cost 0.3 s a path."""
    from kubed.selenium_flow.core.capabilities import ACTION_IN_PATH, ENDPOINTS

    for path, action in ENDPOINTS.items():
        route = f"/browser/{path}" + ("/click" if action == ACTION_IN_PATH else "")
        scope = {"type": "http", "path": route, "method": "POST"}
        assert any(
            r.matches(scope)[0] == Match.FULL for r in route_table
        ), f"{route} is not mounted"


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
    assert faults.message(raw) == "no element matched '//nope' within 3s"

    noisy = WebDriverException(
        "Message: Error: boom\nStacktrace:\nRemoteError@chrome://remote/x.mjs:8:8"
    )
    assert faults.message(noisy) == "Error: boom"


def test_an_error_with_nothing_to_say_still_says_something():
    """Empty is worse than a class name, which at least names the kind."""
    assert faults.message(TimeoutException("")) == "TimeoutException"


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
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, route_prefix="/flow",
    ))
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
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, route_prefix="/flow",
    ))
    client = TestClient(server.mcp.http_app())
    assert client.get("/flow/").status_code == 200
    moved = client.get("/flow/admin", follow_redirects=False)
    assert moved.status_code == 301
    # Relative, so a path an ingress stripped survives the redirect.
    assert moved.headers["location"] == "./"


def test_the_ops_endpoints_answer_at_the_root_whatever_the_prefix():
    """The one path whose reader did not choose the mount. A readiness probe
    that 404s after a config change is the failure this avoids."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, route_prefix="/flow",
    ))
    client = TestClient(server.mcp.http_app())
    for probe in ("/health", "/started", "/ready", "/info"):
        assert client.get(probe).status_code in (200, GRID_DOWN), probe
        assert client.get(f"/flow{probe}").status_code in (200, GRID_DOWN), probe


async def test_the_published_spec_describes_the_paths_actually_served():
    """A document that names a path nothing serves is worse than no document,
    and a prefix is exactly where the two drift apart."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.core.capabilities import ENDPOINTS
    from kubed.selenium_flow.server import SeleniumMCP
    from kubed.selenium_flow.spec import build_spec

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"}, auth={"token": TOKEN}, route_prefix="/flow",
    ))
    spec = await build_spec(server.mcp, ENDPOINTS, "/flow", authenticated=True)
    served = {r.path for r in server.mcp.http_app().routes if hasattr(r, "path")}
    assert set(spec["paths"]) <= served, set(spec["paths"]) - served


# ---- every failure class, as a caller sees it --------------------------------
#
# `errors.status_for` is unit-tested above. What a caller receives is the status
# line and the JSON body that `http.answer` builds from it, so each class
# `errors.py` classifies is raised from inside a real request and read at the other end.


def _grid_says(status: int):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError("refused", response=response)


def _raised(cls):
    return cls("it said no")


STATUS_TABLE = (
    [(_raised(faults.AssertionFailed), 400), (_raised(faults.NotFound), 404)]
    + [(_raised(faults.BidiUnavailable), 503)]
    + [(_raised(cls), 400) for cls in errors.CALLER if cls is not faults.AssertionFailed]
    + [(_raised(cls), 404) for cls in errors.GONE]
    + [(_raised(cls), 503) for cls in errors.UNAVAILABLE if cls is not faults.BidiUnavailable]
    + [
        (_grid_says(404), 404),
        (_grid_says(403), 400),
        (_grid_says(500), 503),
        (requests.HTTPError("no response at all"), 503),
        (TypeError("missing 'url'"), 400),
        (ValueError("not a thing"), 400),
        (RuntimeError("ours"), 500),
        (KeyError("also ours"), 500),
    ]
)


@pytest.mark.parametrize(
    "exc,expected", STATUS_TABLE, ids=[type(e).__name__ + str(s) for e, s in STATUS_TABLE]
)
def test_every_failure_class_is_the_status_it_means_over_http(
    open_server, open_client, monkeypatch, exc, expected
):
    def fails(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(open_server.sessions, "act", fails)
    response = open_client.post("/browser/navigate", json={"url": "https://a.test/"})
    assert response.status_code == expected
    assert response.json() == {"error": faults.message(exc)}


def test_the_status_table_names_every_class_errors_py_classifies():
    """A class defined in `faults.py`, or listed in one of `errors.py`'s
    tuples, without a row above is a status nobody looked at."""
    import inspect

    defined = {
        cls
        for _, cls in inspect.getmembers(faults, inspect.isclass)
        if cls.__module__ == faults.__name__ and issubclass(cls, Exception)
    }
    assert defined, "faults.py defines no exception classes: the enumeration broke"
    covered = {type(e) for e, _ in STATUS_TABLE}
    assert defined <= covered, f"no row for {sorted(c.__name__ for c in defined - covered)}"
    assert {*errors.CALLER, *errors.GONE, *errors.UNAVAILABLE} <= covered


def test_a_store_that_kept_losing_to_other_writers_is_a_500(open_server, open_client, monkeypatch):
    """S12: `StoreConflict` is a RuntimeError nothing classifies, so the caller
    is told it is our fault and to retry — which is true. Pinned so that moving
    the classification is a visible choice."""
    from kubed.selenium_flow.session.store import RedisStore, SessionRecord

    from .fakes import FakeRedis

    fake = FakeRedis()
    store = RedisStore(fake, prefix="p:")
    store.set(SESSION, SessionRecord(session_id="abc"))
    n = [0]

    def always():
        n[0] += 1
        # Still this browser, so the detach has work to do, but never the same
        # bytes, so the transaction never lands.
        record = SessionRecord(session_id="abc", opened_at=float(n[0]))
        fake.set(f"p:{SESSION}", record.to_json())

    fake.interfere = always
    monkeypatch.setattr(open_server.sessions, "store", store)
    monkeypatch.setattr(open_server.actions, "end_browser", lambda sid: {})
    response = open_client.delete("/browser")
    assert response.status_code == 500
    assert "kept changing" in response.json()["error"]


def test_a_record_with_a_non_numeric_opened_at_is_a_session_with_no_history(
    open_server, open_client, monkeypatch
):
    """S5: the whole record is a miss, so the caller is told it holds nothing —
    a 200, not a 500 from `float()`."""
    from kubed.selenium_flow.session.store import RedisStore

    from .fakes import FakeRedis

    fake = FakeRedis()
    fake.set(f"p:{SESSION}", '{"session_id": "abc", "opened_at": "yesterday"}')
    monkeypatch.setattr(open_server.sessions, "store", RedisStore(fake, prefix="p:"))
    response = open_client.get("/browser")
    assert response.status_code == 200
    body = response.json()
    assert (body["browser"], body["live"], body["url"]) == (None, False, None)


def test_the_browser_resource_says_the_name_came_from_the_request(open_client):
    """M36 over HTTP: MCP says `query` or `header`, this surface says `request`."""
    body = open_client.get("/browser").json()
    assert body["session"] == SESSION
    assert body["named_by"] == "request"


def test_readiness_remembers_the_grids_answer_for_two_seconds(server, monkeypatch):
    """A probe is unauthenticated and frequent; each hit dialled the Grid twice.
    Both the ok answer and the failing one are kept, then asked again."""
    from kubed.selenium_flow import routes

    now = [100.0]
    monkeypatch.setattr(routes, "clock", lambda: now[0])
    dialled = []
    grid_up = [True]

    def status():
        dialled.append("status")
        if not grid_up[0]:
            raise ConnectionError("grid went away")
        return {"value": {"ready": True}}

    monkeypatch.setattr(server.actions.grid, "status", status)
    monkeypatch.setattr(server.actions.grid, "session_count", lambda: 3)
    client = TestClient(server.mcp.http_app())
    assert client.get("/ready").status_code == 200
    grid_up[0] = False
    now[0] += 1.9
    assert client.get("/ready").json()["browsers"] == 3
    assert len(dialled) == 1, "a second hit inside the window dialled the Grid"
    now[0] += 0.2
    down = client.get("/ready")
    assert down.status_code == GRID_DOWN and len(dialled) == 2
    grid_up[0] = True
    now[0] += 1.9
    assert client.get("/ready").status_code == GRID_DOWN, "the failure is kept too"
    assert len(dialled) == 2
    now[0] += 0.2
    assert client.get("/ready").status_code == 200 and len(dialled) == 3
