"""Session files: the signed links, the admin surface, and the three renderings.

The Grid owns the file store, so nothing here dials a browser. What is asserted
is the part this server actually decides: who may fetch a file, which shape a
given client is offered, and that the two surfaces show the same components.
"""

import time
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow import admin, apps, browser, files, links
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.store import SessionRecord

from .conftest import TOKEN

pytestmark = pytest.mark.unit

ENTRIES = [
    {"name": "shot.png", "size": 1024, "creationTime": 1700000000000},
    {"name": "report.pdf", "size": 2048, "creationTime": 1700000001000},
]


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


KEY = "named:desktop"


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
    described = files.describe("abc", ENTRIES[0], TOKEN)
    assert described["content_type"] == "image/png"
    assert described["image"] is True
    assert described["url"].startswith("/files/abc/shot.png?exp=")
    assert described["kept"] is False, "a download belongs to the browser"
    assert files.describe("abc", ENTRIES[1], TOKEN)["image"] is False


# --- the admin surface ----------------------------------------------------


def test_the_admin_page_needs_no_token(client):
    """It is the sign-in form; everything it displays is fetched separately."""
    page = client.get("/admin")
    assert page.status_code == 200
    assert "MCP token" in page.text


def test_the_admin_page_carries_the_shared_components(client):
    """The dashboard and the app must render from one library, not two."""
    page = client.get("/admin").text
    assert "const SF" in page and "SF.fileGrid" in page
    assert "--accent" in page, "the shared stylesheet is missing"


def test_the_two_toolbar_actions_are_wired_the_same_way(client):
    """A button wired to nothing fails silently, which is the worst kind — and
    two buttons wired separately is how one of them ends up without the confirm
    or the error alert the other has, which is what happened here.

    Both now go through one `destructive` helper that owns the confirmation, the
    DELETE and the failure message, and both wear the same class, so they read
    as one control with two verbs rather than two unrelated ones.
    """
    page = client.get("/admin").text
    assert "function destructive(id," in page
    assert "'/admin/sessions/' + encodeURIComponent(key) + path, 'DELETE'" in page
    for button in ("clearFiles", "endBrowser"):
        assert f'id="{button}" class="danger"' in page, button
        assert f"destructive('{button}'" in page, button


def test_neither_toolbar_action_is_offered_without_a_browser(client):
    """A control that does nothing is worse than one that is visibly off.

    Both act on the browser — and a detached session has no files either, since
    the Grid deletes the file store with the browser. Asserted on `showDetail`,
    which is the one place the header is drawn, from a fetch and from a pushed
    update alike, so neither can be left enabled on a session that went idle
    while someone was looking at it.
    """
    page = client.get("/admin").text
    assert "function showDetail(row)" in page
    assert "$('endBrowser').disabled = $('clearFiles').disabled = !row.attached;" in page


def test_the_detail_view_is_updated_by_the_event_stream(client):
    """It used to drop every event while the detail view was open, to avoid
    clobbering a file grid. The effect was a header that never changed: a
    browser attaching to the session you were looking at only showed if you
    navigated out and back."""
    page = client.get("/admin").text
    assert "if ($('detailView').hidden) paint(data); else refreshDetail(data);" in page


def test_a_changed_browser_clears_the_file_grid(client):
    """The Grid keeps a file store per browser and deletes it with the browser,
    so after a switch the files on screen do not merely look stale — they are
    gone. Leaving them up for the length of a fetch offers files that 404."""
    page = client.get("/admin").text
    assert "(row.session_id || null) !== shownBrowser" in page
    assert "SF.fileGrid($('files'), {files: []})" in page


def test_the_file_grid_is_not_redrawn_on_every_heartbeat(client):
    """Redrawing it would close a lightbox and lose a scroll position, for a
    payload that says nothing new about the files."""
    page = client.get("/admin").text
    assert "if (filesStamp(row) !== shownFiles) loadFiles(current);" in page


def test_a_dead_event_stream_is_reopened_with_a_fresh_url(client):
    """The stream URL is signed and expires. EventSource reconnects on its own,
    but only ever to the URL it was given — so after the expiry it retries a URL
    that can never work again, for the life of the tab."""
    page = client.get("/admin").text
    assert "if (events) { events.close(); events = null; }" in page
    assert "watch(data.events_url);" in page


def test_the_components_render_no_action_buttons(client):
    """Acting on a session belongs in the detail toolbar, where the page already
    has one. A button inside a card sits next to the status pill and makes that
    pill look clickable — and these same components render inside an MCP app
    that holds no credential, where any action would be dead."""
    page = client.get("/admin").text
    # Just the component library: the page's own toolbar lives outside it and
    # is exactly where the destructive button is supposed to be.
    components = page.split("const SF")[1].split("})();")[0]
    assert "opts.onend" not in components
    assert "createElement('button')" not in components, (
        "an action button leaked into the shared component library"
    )


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
    server.sessions.store.set("named:idle", SessionRecord(session_id=""))
    with patch.object(browser.Grid, "quit") as quit_:
        response = client.delete(
            "/admin/sessions/named:idle", headers={"Authorization": f"Bearer {TOKEN}"}
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


def test_a_partial_download_is_never_served(client):
    """It is half-written and about to be renamed, so it is not a file yet."""
    url = links.file_url("abc", "shot.png.crdownload", TOKEN)
    assert client.get(url).status_code == 404


def test_the_admin_api_lists_files_with_signed_urls(client, flow_session):
    with patch.object(browser.Grid, "files", return_value=ENTRIES):
        body = client.get(
            f"/admin/sessions/{KEY}/files",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()
    # Newest first. The listing merges the browser's downloads with the session's
    # kept files, and a merged list needs a total order of its own rather than
    # inheriting either source's — so it sorts on creation time, and report.pdf
    # is the later of the two fixtures.
    assert [f["name"] for f in body["files"]] == ["report.pdf", "shot.png"]
    assert all("sig=" in f["url"] for f in body["files"])


def test_the_listing_shows_flow_sessions_not_grid_sessions(client, server):
    """The Grid is the superset — it runs browsers put there by anything at all.
    Listing those would be showing somebody else's work as though it were ours,
    and handing whoever holds the admin token a browser id they never opened."""
    server.sessions.store.set(
        "named:mine",
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
    assert keys == ["named:mine"]
    assert "somebody-else" not in str(body)


def test_a_detached_session_is_listed_as_idle_with_its_context(client, server):
    """The point of the split: no browser, but still a session worth seeing."""
    server.sessions.store.set(
        "named:idle",
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


def test_a_stateless_session_is_listed_and_labelled(client, server):
    server.sessions.store.set("session:abc", SessionRecord(session_id="abc"))
    with patch.object(browser.Grid, "sessions", return_value=[]):
        body = client.get(
            "/admin/sessions", headers={"Authorization": f"Bearer {TOKEN}"}
        ).json()
    assert body["sessions"][0]["owner"] == "stateless"


def test_a_detached_session_has_no_files_rather_than_an_error(client, server):
    """It had them; the Grid deleted them with the browser. That is not a fault."""
    server.sessions.store.set("named:idle", SessionRecord(session_id=""))
    body = client.get(
        "/admin/sessions/named:idle/files",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert body.status_code == 200
    assert body.json()["files"] == []


# --- the three renderings -------------------------------------------------


async def test_files_are_a_resource_and_a_template(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert "session://files" in uris
    templates = {t.uri_template for t in await server.mcp.list_resource_templates()}
    assert "session://files/{name}" in templates


async def test_the_mcp_surface_never_lists_other_sessions(server):
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


async def test_the_app_shell_is_a_ui_resource(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert apps.RESOURCE_URI in uris


async def test_the_file_tools_are_hidden_from_a_resource_client(server):
    """A mirror is noise for a client that can read the resource itself."""
    names = {t.name for t in await server.mcp.list_tools()}
    assert "session_files" not in names


async def test_the_file_tools_return_for_a_client_that_renders_apps(server):
    """For that client the tool is the only route to a rendered component."""
    with patch.object(apps, "supported", return_value=True):
        names = {t.name for t in await server.mcp.list_tools()}
    assert "session_files" in names


async def test_apps_can_be_turned_off():
    off = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token=TOKEN, apps_enabled=False
    )
    uris = {str(r.uri) for r in await off.mcp.list_resources()}
    assert apps.RESOURCE_URI not in uris
    # The listing survives as a resource: it never depended on apps.
    assert "session://files" in uris


def test_the_app_csp_admits_our_own_origin_and_the_sdk():
    """An app gets no network by default, so both have to be declared."""
    csp = apps.config_for("https://selenium.example.com/flow").csp
    # Compared element-wise rather than with `in`. It is already exact — these
    # are lists, so `in` is membership, not a substring test — but the reader
    # that flags this cannot tell the two apart, and neither can a person
    # skimming. Being explicit costs nothing and the substring version of this
    # check is a real bug elsewhere (see the origin matching in secrets.py).
    assert any(d == "https://selenium.example.com" for d in csp.resource_domains)
    assert any(d == apps.SDK_ORIGIN for d in csp.resource_domains)


def test_the_app_csp_omits_an_origin_it_does_not_have():
    csp = apps.config_for("").csp
    assert csp.resource_domains == [apps.SDK_ORIGIN]


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
