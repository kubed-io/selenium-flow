"""The admin surface over a session's three file sections.

Downloads, Screenshots and Files never merge into one list (§F4.6): each has
its own address, its own listing, and — for Screenshots and Downloads — its
own clear. This module is the HTTP-level proof of the routes `admin.py`
builds on top of `files.sections`, `files.keep`, `files.delete_one` and
`files.clear_screenshots`: what a GET returns, which name clears which
section, that keeping moves a screenshot and reports itself in `files_rev`,
and that every route still needs the token.

The real Grid is never dialled; `browser.Grid`'s HTTP methods are patched per
test, the same way `test_kept_files.py` and `test_files_and_admin.py` do it.
"""

from unittest.mock import patch

import pytest
import requests
from starlette.testclient import TestClient

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.core import browser
from kubed.selenium_flow.flows import store as flows
from kubed.selenium_flow.http import links
from kubed.selenium_flow.names import RECORDINGS_DIR, SCREENSHOTS_DIR
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.session.store import SessionRecord

from .conftest import TOKEN

pytestmark = pytest.mark.unit

KEY = "desktop"
SESSION = "desktop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def kept_server(tmp_path):
    """A server with somewhere to keep files."""
    return SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path)},
    ))


@pytest.fixture
def client(kept_server):
    """Every request names its session, the way this surface says to (§F2.13)."""
    return TestClient(
        kept_server.mcp.http_app(), headers={"X-Session-Key": SESSION}
    )


@pytest.fixture
def live(kept_server):
    """A flow session holding a browser, which is what the admin API addresses."""
    kept_server.sessions.store.set(
        KEY, SessionRecord(session_id="abc").visited("https://x/")
    )
    return kept_server


# ---- the listing --------------------------------------------------------


def test_the_listing_is_three_sections(client, live, tmp_path):
    live.flows.write_file(SESSION, "shot.png", b"\x89PNG", SCREENSHOTS_DIR)
    live.flows.write_file(SESSION, "report.pdf", b"PDF")
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(
            browser.Grid,
            "files",
            return_value=[{"name": "movie.mp4", "size": 10, "creationTime": 1}],
        ),
    ):
        body = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()
    assert [f["name"] for f in body["downloads"]] == ["movie.mp4"]
    assert [f["name"] for f in body["screenshots"]] == ["shot.png"]
    assert [f["name"] for f in body["files"]] == ["report.pdf"]
    assert body["browser"] is True


def test_clearing_screenshots_leaves_files(client, live):
    live.flows.write_file(SESSION, "shot.png", b"x", SCREENSHOTS_DIR)
    live.flows.write_file(SESSION, "report.pdf", b"x")
    response = client.delete(f"/admin/sessions/{KEY}/files/screenshots", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()["cleared"] == 1
    assert live.flows.files(SESSION, SCREENSHOTS_DIR) == []
    assert [f["name"] for f in live.flows.files(SESSION)] == ["report.pdf"]


def test_clearing_downloads_empties_the_grid_store(client, live):
    live.flows.write_file(SESSION, "shot.png", b"x", SCREENSHOTS_DIR)
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "clear_files") as clear,
    ):
        response = client.delete(f"/admin/sessions/{KEY}/files/downloads", headers=AUTH)
    assert response.status_code == 200, response.text
    clear.assert_called_once_with("abc")
    # Screenshots are ours and elsewhere, so clearing the Grid's store leaves
    # them exactly where they were (§F1.10).
    assert [f["name"] for f in live.flows.files(SESSION, SCREENSHOTS_DIR)] == [
        "shot.png"
    ]


def test_a_reaped_browser_has_no_downloads_to_clear(client, kept_server):
    """The record still names a browser; the Grid no longer has it. Dialling
    `clear_files` for an id the Grid has reaped would be a call for nothing,
    so this is success without ever making it."""
    kept_server.sessions.store.set(KEY, SessionRecord(session_id="abc"))
    with (
        patch.object(browser.Grid, "is_alive", return_value=False),
        patch.object(browser.Grid, "clear_files") as clear,
    ):
        response = client.delete(f"/admin/sessions/{KEY}/files/downloads", headers=AUTH)
    assert response.status_code == 200, response.text
    clear.assert_not_called()


class _FakeGridResponse:
    """A `requests` response standing in for the Grid's own answer, exercising
    `Grid.clear_files` itself rather than a mock of the whole method — the
    other two tests above patch `clear_files` out entirely and so would never
    have caught it skipping the status check (Copilot, PR #41)."""

    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Server Error", response=self
            )


def test_a_grid_500_on_clear_is_not_reported_as_success(client, live):
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch(
            "kubed.selenium_flow.core.browser.requests.Session.delete",
            return_value=_FakeGridResponse(500),
        ),
    ):
        response = client.delete(f"/admin/sessions/{KEY}/files/downloads", headers=AUTH)
    assert response.status_code >= 500, response.text
    # The Grid's own error text never reaches the caller verbatim; only the
    # errors.py-decided message does.
    assert "grid.invalid" not in response.text


def test_a_grid_404_on_clear_is_success(client, live):
    """A raced reap: the Grid's file store is already gone by the time this
    call lands. Idempotent, same as `quit`'s own 404."""
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch(
            "kubed.selenium_flow.core.browser.requests.Session.delete",
            return_value=_FakeGridResponse(404),
        ),
    ):
        response = client.delete(f"/admin/sessions/{KEY}/files/downloads", headers=AUTH)
    assert response.status_code == 200, response.text


def test_deleting_one_file_in_files(client, live):
    live.flows.write_file(SESSION, "report.pdf", b"x")
    response = client.delete(f"/admin/sessions/{KEY}/files/report.pdf", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert live.flows.files(SESSION) == []


def test_there_is_no_delete_for_one_screenshot(client, live):
    live.flows.write_file(SESSION, "shot.png", b"x", SCREENSHOTS_DIR)
    response = client.delete(
        f"/admin/sessions/{KEY}/files/screenshots/shot.png", headers=AUTH
    )
    assert response.status_code in (404, 405)
    assert [f["name"] for f in live.flows.files(SESSION, SCREENSHOTS_DIR)] == [
        "shot.png"
    ]


def test_keeping_from_the_admin_moves_a_screenshot(client, live):
    live.flows.write_file(SESSION, "shot.png", b"x", SCREENSHOTS_DIR)
    response = client.post(
        f"/admin/sessions/{KEY}/files/screenshots/shot.png/keep", headers=AUTH
    )
    assert response.status_code == 200, response.text
    assert live.flows.files(SESSION, SCREENSHOTS_DIR) == []
    assert [f["name"] for f in live.flows.files(SESSION)] == ["shot.png"]


@pytest.mark.parametrize("folder", ["files", "flows"])
def test_the_admin_keeps_only_from_the_two_folders(client, live, folder):
    response = client.post(
        f"/admin/sessions/{KEY}/files/{folder}/whatever.pdf/keep", headers=AUTH
    )
    assert response.status_code == 400


def test_the_admin_keep_refuses_a_key_that_owns_no_library(client, kept_server, tmp_path):
    """The keep route resolves the key through `library(key)`, as the delete and
    clear handlers do, so a stored key that cannot be a directory is refused
    with that reason rather than reaching the store as a raw name."""
    stale = "named:desktop"
    kept_server.sessions.store.set(stale, SessionRecord(session_id="").visited("https://x/"))
    response = client.post(
        f"/admin/sessions/{stale}/files/screenshots/shot.png/keep", headers=AUTH
    )
    assert response.status_code == 400
    assert "cannot keep files" in response.json()["error"]
    assert not any(tmp_path.rglob("shot.png"))


def test_the_screenshot_route_serves_a_signed_file(client, live):
    live.flows.write_file(
        SESSION, "shot.png", b"\x89PNG\r\n\x1a\n", SCREENSHOTS_DIR
    )
    with (
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "files", return_value=[]),
    ):
        listing = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()
    url = listing["screenshots"][0]["url"]
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n"
    unsigned = url.split("?", 1)[0]
    assert client.get(unsigned).status_code == 403


def test_a_screenshot_that_is_not_there_is_a_404(client, live):
    response = client.get(links.screenshot_url(SESSION, "missing.png", TOKEN))
    assert response.status_code == 404


def test_a_broken_screenshot_store_is_a_5xx_not_a_404(client, live):
    """A read that fails is not the same fact as a read that found nothing:
    an NFS permission fault or a mount gone read-only must not tell a client
    to stop retrying something that could work on the next attempt, and its
    message must not quote DATA_DIR's own layout back at whoever asked
    (Copilot, PR #41)."""
    with patch.object(
        flows.LocalFlowStore,
        "file_path",
        side_effect=PermissionError(
            13, "Permission denied", "/data/flows/desktop/screenshots/shot.png"
        ),
    ):
        response = client.get(links.screenshot_url(SESSION, "shot.png", TOKEN))
    assert response.status_code >= 500
    assert response.status_code < 600
    assert "/data/flows" not in response.text
    # Signature-only route: no exception text at all, not even a scrubbed one.
    assert response.json() == {"error": "the file could not be read right now"}


# ---- the counts -----------------------------------------------------------


def test_the_session_row_counts_each_section(client, live):
    live.flows.write_file(SESSION, "shot.png", b"x", SCREENSHOTS_DIR)
    live.flows.write_file(SESSION, "report.pdf", b"x")
    with (
        patch.object(browser.Grid, "sessions", return_value=[{"session_id": "abc"}]),
        patch.object(
            browser.Grid,
            "files",
            return_value=[{"name": "movie.mp4", "size": 1, "creationTime": 1}],
        ),
    ):
        row = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]
    assert row["counts"] == {"downloads": 1, "screenshots": 1, "recordings": 0, "files": 1}
    assert row["files_count"] == 3


def test_the_file_stamp_changes_when_a_screenshot_is_kept(client, live):
    live.flows.write_file(SESSION, "shot.png", b"x", SCREENSHOTS_DIR)
    with patch.object(browser.Grid, "sessions", return_value=[]):
        before = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]
    client.post(
        f"/admin/sessions/{KEY}/files/screenshots/shot.png/keep", headers=AUTH
    )
    with patch.object(browser.Grid, "sessions", return_value=[]):
        after = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]
    # The name moved from Screenshots to Files, so the stamp changes even
    # though the total count of files this session holds did not.
    assert before["files_count"] == after["files_count"] == 1
    assert before["files_rev"] != after["files_rev"]


# ---- every endpoint needs the token ----------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/admin/sessions/{key}/files"),
        ("delete", "/admin/sessions/{key}/files/downloads"),
        ("delete", "/admin/sessions/{key}/files/screenshots"),
        ("delete", "/admin/sessions/{key}/files/report.pdf"),
        ("post", "/admin/sessions/{key}/files/screenshots/shot.png/keep"),
        ("delete", "/admin/sessions/{key}/files/recordings"),
        ("post", "/admin/sessions/{key}/files/recordings/a.mp4/keep"),
    ],
)
def test_every_files_endpoint_needs_the_token(client, method, path):
    route = path.format(key=KEY)
    assert getattr(client, method)(route).status_code == 401


# ---- recordings ---------------------------------------------------------------


def test_recordings_are_listed_counted_cleared_and_kept(client, live):
    live.sessions.store.set(
        KEY,
        SessionRecord(session_id="abc", settings={"record": True}).visited("https://x/"),
    )
    live.flows.write_file(SESSION, "rec-1.mp4", b"v", RECORDINGS_DIR)
    live.flows.write_file(SESSION, "rec-2.mp4", b"w", RECORDINGS_DIR)
    live.flows.write_file(SESSION, "report.pdf", b"p")
    live.flows.write_note(SESSION, "8f3d6dc2a1b04e6f9c1d2e3f4a5b6c7d", {"opened": 1})
    with (
        patch.object(browser.Grid, "sessions", return_value=[{"session_id": "abc"}]),
        patch.object(browser.Grid, "is_alive", return_value=True),
        patch.object(browser.Grid, "files", return_value=[]),
    ):
        row = client.get("/admin/sessions", headers=AUTH).json()["sessions"][0]
        body = client.get(f"/admin/sessions/{KEY}/files", headers=AUTH).json()
    assert row["counts"]["recordings"] == 2 and row["recording"] is True
    assert sorted(f["name"] for f in body["recordings"]) == ["rec-1.mp4", "rec-2.mp4"]

    kept = client.post(
        f"/admin/sessions/{KEY}/files/recordings/rec-1.mp4/keep", headers=AUTH
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["uri"] == "session://files/rec-1.mp4"
    assert [f["name"] for f in live.flows.files(SESSION, RECORDINGS_DIR)] == ["rec-2.mp4"]

    cleared = client.delete(f"/admin/sessions/{KEY}/files/recordings", headers=AUTH)
    assert cleared.status_code == 200 and cleared.json()["cleared"] == 1
    assert live.flows.files(SESSION, RECORDINGS_DIR) == []
    assert sorted(f["name"] for f in live.flows.files(SESSION)) == [
        "rec-1.mp4", "report.pdf",
    ]
    assert len(live.flows.notes()) == 1
