"""Session files: the signed links, the admin surface, and the three renderings.

The Grid owns the file store, so nothing here dials a browser. What is asserted
is the part this server actually decides: who may fetch a file, which shape a
given client is offered.
"""

import json
import time
from unittest.mock import patch

import pytest
import requests
from starlette.testclient import TestClient

from kubed.selenium_flow.core import browser
from kubed.selenium_flow.http import files, links
from kubed.selenium_flow.mcp import apps
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.store import SessionRecord

from .conftest import TOKEN

pytestmark = pytest.mark.unit

ENTRIES = [
    {"name": "shot.png", "size": 1024, "creationTime": 1700000000000},
    {"name": "report.pdf", "size": 2048, "creationTime": 1700000001000},
]


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


KEY = "desktop"


@pytest.fixture
def flow_session(server):
    """A flow session holding a browser, which is what the admin API addresses.

    The admin surface lists *our* sessions, not the Grid's, so a test that does
    not put one in the store is asking about an empty server.
    """
    server.sessions.store.set(KEY, SessionRecord(session_id="abc", url="https://x/"))
    return server


# --- signed links ---------------------------------------------------------


def test_a_signed_link_validates():
    path = links.file_path("abc", "shot.png")
    signed = links.sign(path, TOKEN)
    exp = signed.split("exp=")[1].split("&")[0]
    sig = signed.split("sig=")[1]
    assert links.valid(path, exp, sig, TOKEN)


def test_a_link_is_the_same_url_for_a_while():
    """Found by the self-test on the live deploy: `exp` to the second made
    every listing's URLs new, so the browser cache never hit and each repaint
    of the admin page re-downloaded every screenshot on it."""
    path = links.file_path("abc", "shot.png")
    start = links.EXPIRY_STEP * 1000 + 1
    first = links.sign(path, TOKEN, now=start)
    assert links.sign(path, TOKEN, now=start + links.EXPIRY_STEP - 2) == first
    assert links.sign(path, TOKEN, now=start + links.EXPIRY_STEP) != first


def test_rounding_never_shortens_a_link():
    path = links.file_path("abc", "shot.png")
    for now in (0, 1, links.EXPIRY_STEP - 1, links.EXPIRY_STEP, 12345.6):
        exp = int(links.sign(path, TOKEN, now=now).split("exp=")[1].split("&")[0])
        assert now + links.DEFAULT_TTL <= exp < now + links.DEFAULT_TTL + links.EXPIRY_STEP


def test_a_signature_is_bound_to_its_path():
    """The whole point: a link to one file is not a link to another."""
    exp = int(time.time()) + 60
    sig = links.signature(links.file_path("abc", "shot.png"), exp, TOKEN)
    assert not links.valid(links.file_path("abc", "secret.pdf"), exp, sig, TOKEN)
    assert not links.valid(links.file_path("other", "shot.png"), exp, sig, TOKEN)


def test_a_signature_is_bound_to_its_expiry():
    """So a recipient cannot extend their own link by editing the query."""
    path = links.file_path("abc", "shot.png")
    sig = links.signature(path, int(time.time()) + 60, TOKEN)
    assert not links.valid(path, int(time.time()) + 99999, sig, TOKEN)


def test_an_expired_link_is_refused():
    path = links.file_path("abc", "shot.png")
    past = int(time.time()) - 1
    assert not links.valid(path, past, links.signature(path, past, TOKEN), TOKEN)


def test_a_different_token_cannot_sign():
    """Rotating the token is what revokes every link already handed out."""
    path = links.file_path("abc", "shot.png")
    exp = int(time.time()) + 60
    assert not links.valid(path, exp, links.signature(path, exp, "other"), TOKEN)


def test_junk_is_refused_rather_than_raising():
    path = links.file_path("abc", "shot.png")
    assert not links.valid(path, "not-a-number", "x", TOKEN)
    assert not links.valid(path, None, None, TOKEN)


def test_without_a_token_the_url_is_unsigned():
    """Auth off means there is nothing to protect and nothing to sign with."""
    assert links.file_url("abc", "shot.png", None) == "/files/abc/shot.png"
    assert "sig=" in links.file_url("abc", "shot.png", TOKEN)


def test_names_are_escaped_into_the_path():
    assert "%2F" in links.file_path("abc", "../../etc/passwd")


# --- what the Grid's store hands back -------------------------------------


def test_downloads_in_flight_are_not_files():
    assert browser.is_partial("report.pdf.crdownload")
    assert browser.is_partial(".com.google.Chrome.MUJEUy")
    assert not browser.is_partial("report.pdf")


def test_describe_marks_images_and_types():
    url = links.file_url("abc", "shot.png", TOKEN)
    described = files.describe(files.DOWNLOADS, ENTRIES[0], url)
    assert described["content_type"] == "image/png"
    assert described["image"] is True
    assert described["url"] == url
    assert "kept" not in described, "the folder says that now, not a flag"
    assert described["keep_with"] == f'{files.KEEP_TOOL}({json.dumps(described["uri"])})', (
        "a download is not kept until keep_file is called"
    )
    assert files.describe(files.DOWNLOADS, ENTRIES[1], url)["image"] is False


# --- the admin surface ----------------------------------------------------


def test_the_admin_api_requires_the_token(client):
    assert client.get("/admin/sessions").status_code == 401
    assert client.get("/admin/sessions/x/files").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/admin/sessions", headers=bad).status_code == 401


# --- ending a session -----------------------------------------------------


def test_ending_a_session_requires_the_token(client):
    """It quits somebody's browser, so it is the last route to leave open."""
    assert client.delete("/admin/sessions/abc").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.delete("/admin/sessions/abc", headers=bad).status_code == 401


def test_ending_a_session_quits_the_browser(client, flow_session):
    with patch.object(browser.Grid, "quit") as quit_:
        response = client.delete(
            f"/admin/sessions/{KEY}", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert response.status_code == 200
    assert response.json() == {"success": True, "key": KEY, "session_id": "abc"}
    quit_.assert_called_once_with("abc")


def test_a_grid_that_refuses_to_quit_is_still_a_200(client, flow_session):
    """The button's job is "make sure this holds no browser", and an unreachable
    Grid does not stop that being true — the record is detached either way. So
    the operator sees success rather than a 500 for something already handled.

    That the detach happens is SessionManager's contract, asserted directly in
    test_sessions.py; the route's contract is the status code."""
    with patch.object(browser.Grid, "quit", side_effect=RuntimeError("gone")):
        response = client.delete(
            f"/admin/sessions/{KEY}", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert response.status_code == 200


def test_ending_a_session_with_no_browser_is_a_no_op(client, server):
    """The button's job is "make sure this holds no browser", which is already
    true — so it succeeds rather than erroring."""
    server.sessions.store.set("idle", SessionRecord(session_id=""))
    with patch.object(browser.Grid, "quit") as quit_:
        response = client.delete(
            "/admin/sessions/idle", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert response.status_code == 200
    assert response.json()["session_id"] is None
    quit_.assert_not_called()


def test_ending_a_session_does_not_disturb_the_files_route(client, flow_session):
    """`/admin/sessions/<key>` and `/admin/sessions/<key>/files` are different
    routes, and a DELETE to one must not be routed to the other."""
    with (
        patch.object(browser.Grid, "clear_files") as clear,
        patch.object(browser.Grid, "quit") as quit_,
    ):
        client.delete(
            f"/admin/sessions/{KEY}", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    quit_.assert_called_once()
    clear.assert_not_called()


def test_a_file_needs_a_valid_signature_not_a_token(client):
    """The route exists to be put in an <img>, which cannot send a header."""
    assert client.get("/files/abc/shot.png").status_code == 403
    assert client.get("/files/abc/shot.png?exp=1&sig=x").status_code == 403


def test_a_signed_file_is_served_without_any_header(client):
    with patch.object(browser.Grid, "read_file", return_value=b"\x89PNG\r\n\x1a\n"):
        response = client.get(links.file_url("abc", "shot.png", TOKEN))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert "inline" in response.headers["content-disposition"]


def test_a_grid_404_on_download_is_a_404(client):
    """The Grid's own status decides what its refusal means (`errors.py`): a
    gone browser or a file the Grid never had answers the same 404 a caller
    already gets for one it deleted itself, not a 500 that tells a retrying
    client the request itself is fine."""
    gone = requests.Response()
    gone.status_code = 404
    with patch.object(
        browser.Grid, "read_file", side_effect=requests.HTTPError(response=gone)
    ):
        response = client.get(links.file_url("abc", "shot.png", TOKEN))
    assert response.status_code == 404
    assert response.json() == {"error": "not found"}


def test_an_unreachable_grid_on_download_is_a_503_not_a_404(client):
    """Before this fix every exception from the Grid was flattened into "not
    found" — a downed Grid told a client to stop retrying something that would
    have worked a moment later (Copilot, PR #41). And this route is authorised
    by signature alone, not the admin token, so the body must not carry the
    Grid's own address either: `requests.ConnectionError`'s message names the
    host and port `urllib3` tried, e.g. `HTTPConnectionPool(host=...)`, and
    that is `GRID_URL`'s own internals leaking to whoever holds the link."""
    with patch.object(
        browser.Grid,
        "read_file",
        side_effect=requests.ConnectionError(
            "HTTPConnectionPool(host='selenium-grid.selenium.svc.cluster.local', "
            "port=4444): Max retries exceeded"
        ),
    ):
        response = client.get(links.file_url("abc", "shot.png", TOKEN))
    assert response.status_code == 503
    body = response.text
    assert "HTTPConnectionPool" not in body
    assert "selenium-grid" not in body
    assert "4444" not in body
    assert response.json() == {"error": "the file could not be read right now"}


def test_a_partial_download_is_never_served(client):
    """It is half-written and about to be renamed, so it is not a file yet."""
    url = links.file_url("abc", "shot.png.crdownload", TOKEN)
    assert client.get(url).status_code == 404


def test_the_admin_api_lists_files_with_signed_urls(client, flow_session):
    """`server` keeps no flows (no `FLOW_DATA_DIR`), so Downloads is the only
    section with anything in it — and it still has to list, signed, with flows
    off entirely."""
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "files", return_value=ENTRIES),
    ):
        body = client.get(
            f"/admin/sessions/{KEY}/files",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()
    # Newest first — the Grid's own listing has no order of its own to inherit,
    # and report.pdf is the later of the two fixtures.
    assert [f["name"] for f in body["downloads"]] == ["report.pdf", "shot.png"]
    assert all("sig=" in f["url"] for f in body["downloads"])
    assert body["files"] == [], "no store, nothing to keep into"


def test_the_listing_shows_flow_sessions_not_grid_sessions(client, server):
    """The Grid is the superset — it runs browsers put there by anything at all.
    Listing those would be showing somebody else's work as though it were ours,
    and handing whoever holds the admin token a browser id they never opened."""
    server.sessions.store.set(
        "mine",
        SessionRecord(session_id="mine", url="https://x/", settings={"browser": "firefox"}),
    )
    grid_rows = [
        {"session_id": "mine", "browser": "firefox", "version": "155", "node": "n1"},
        {"session_id": "somebody-else", "browser": "chrome", "version": "1", "node": "n1"},
    ]
    with (
        patch.object(browser.Grid, "sessions", return_value=grid_rows),
        patch.object(browser.Grid, "files", return_value=[]),
    ):
        body = client.get(
            "/admin/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
    keys = [row["key"] for row in body["sessions"]]
    assert keys == ["mine"]
    assert "somebody-else" not in str(body)


def test_a_detached_session_is_listed_as_idle_with_its_context(client, server):
    """The point of the split: no browser, but still a session worth seeing."""
    server.sessions.store.set(
        "idle",
        SessionRecord(session_id="", url="https://x/", settings={"browser": "firefox"}),
    )
    with patch.object(browser.Grid, "sessions", return_value=[]):
        body = client.get(
            "/admin/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
    row = body["sessions"][0]
    assert row["attached"] is False
    assert row["live"] is False
    assert row["session_id"] is None
    assert row["url"] == "https://x/"
    assert row["browser"] == "firefox", "the context outlives the browser"


def test_the_stdio_session_is_listed_and_labelled(client, server):
    """One key shape survives — the name a caller chose — and `stdio` is the one
    nobody typed, so it is still worth saying where it came from (§F2.12)."""
    server.sessions.store.set("stdio", SessionRecord(session_id="abc"))
    with patch.object(browser.Grid, "sessions", return_value=[]):
        body = client.get(
            "/admin/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
    assert body["sessions"][0]["owner"] == "stdio"


def test_a_detached_session_has_no_files_rather_than_an_error(client, server):
    """It had them; the Grid deleted them with the browser. That is not a
    fault — and neither is having no store at all, which `server` also has
    none of."""
    server.sessions.store.set("idle", SessionRecord(session_id=""))
    body = client.get(
        "/admin/sessions/idle/files",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert body.status_code == 200
    payload = body.json()
    assert payload["downloads"] == payload["screenshots"] == payload["files"] == []


# --- the three renderings -------------------------------------------------


async def test_files_are_a_resource_and_a_template(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert "session://files" in uris
    templates = {t.uri_template for t in await server.mcp.list_resource_templates()}
    assert "session://files/{name}" in templates


async def test_the_mcp_surface_never_lists_other_sessions(built_ui, server):
    """A client owns one session and may only ever see that one.

    The session list is an admin view over HTTP, deliberately not a tool and not
    a resource: a tool that enumerated every session would hand any MCP client
    somebody else's browser id, which is the whole credential for driving it.
    """
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert "grid://sessions" not in uris
    with patch.object(apps, "supported", return_value=True):
        names = {t.name for t in await server.mcp.list_tools()}
    assert "browser_sessions" not in names
    # What a client does get is its own, and only its own.
    assert "session://current" in uris


async def test_the_app_shell_is_a_ui_resource(built_ui, server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert apps.RESOURCE_URI in uris


async def test_the_file_tools_are_hidden_from_a_resource_client(server):
    """A mirror is noise for a client that can read the resource itself."""
    names = {t.name for t in await server.mcp.list_tools()}
    assert "session_files" not in names


async def test_the_file_tools_return_for_a_client_that_renders_apps(built_ui, server):
    """For that client the tool is the only route to a rendered component."""
    with patch.object(apps, "supported", return_value=True):
        names = {t.name for t in await server.mcp.list_tools()}
    assert "session_files" in names


async def test_apps_can_be_turned_off(built_ui):
    off = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, apps_enabled=False
    )
    uris = {str(r.uri) for r in await off.mcp.list_resources()}
    assert apps.RESOURCE_URI not in uris
    # The listing survives as a resource: it never depended on apps.
    assert "session://files" in uris


def test_the_app_csp_omits_an_origin_it_does_not_have():
    csp = apps.config_for("").csp
    assert csp.resource_domains == csp.connect_domains == []


# --- the live event stream ------------------------------------------------


def test_the_sessions_call_hands_back_a_signed_stream_url(client):
    """EventSource cannot send a header, so it is given a URL it can just open."""
    with patch.object(browser.Grid, "sessions", return_value=[]):
        body = client.get(
            "/admin/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
    assert body["events_url"].startswith("/admin/events?exp=")
    assert "sig=" in body["events_url"]


def test_the_event_stream_refuses_an_unsigned_open(client):
    assert client.get("/admin/events").status_code == 401
    assert client.get("/admin/events?exp=1&sig=x").status_code == 401


def test_the_event_stream_signature_is_bound_to_its_own_path(client):
    """A file link must not open the stream, or the reverse.

    The stream is deliberately NOT read here. It is an endless generator, and
    TestClient runs the app on a portal thread, so reading one event and leaving
    blocks forever waiting for a body that never ends — which is a property of
    the test harness, not of the route. The framing is exercised against a real
    server instead.
    """
    with patch.object(browser.Grid, "sessions", return_value=[]):
        url = client.get(
            "/admin/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()["events_url"]
    query = url.split("?", 1)[1]
    assert client.get(f"/files/abc/shot.png?{query}").status_code == 403

