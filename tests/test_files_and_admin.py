"""Session files: the signed links, the admin surface, and the three renderings.

The Grid owns the file store, so nothing here dials a browser. What is asserted
is the part this server actually decides: who may fetch a file, which shape a
given client is offered, and that the two surfaces show the same components.
"""

import time
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow import admin, apps, browser, links
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit

ENTRIES = [
    {"name": "shot.png", "size": 1024, "creationTime": 1700000000000},
    {"name": "report.pdf", "size": 2048, "creationTime": 1700000001000},
]


@pytest.fixture
def client(server):
    return TestClient(server.mcp.http_app())


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
    described = admin.describe("abc", ENTRIES[0], TOKEN)
    assert described["content_type"] == "image/png"
    assert described["image"] is True
    assert described["url"].startswith("/files/abc/shot.png?exp=")
    assert admin.describe("abc", ENTRIES[1], TOKEN)["image"] is False


# --- the admin surface ----------------------------------------------------


def test_the_admin_page_needs_no_token(client):
    """It is the sign-in form; everything it displays is fetched separately."""
    page = client.get("/admin/ui")
    assert page.status_code == 200
    assert "MCP token" in page.text


def test_the_admin_page_carries_the_shared_components(client):
    """The dashboard and the app must render from one library, not two."""
    page = client.get("/admin/ui").text
    assert "const SF" in page and "SF.fileGrid" in page
    assert "--accent" in page, "the shared stylesheet is missing"


def test_the_admin_api_requires_the_token(client):
    assert client.get("/admin/api/sessions").status_code == 401
    assert client.get("/admin/api/sessions/x/files").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/admin/api/sessions", headers=bad).status_code == 401


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


def test_the_admin_api_lists_files_with_signed_urls(client):
    with patch.object(browser.Grid, "files", return_value=ENTRIES):
        body = client.get(
            "/admin/api/sessions/abc/files",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ).json()
    assert [f["name"] for f in body["files"]] == ["shot.png", "report.pdf"]
    assert all("sig=" in f["url"] for f in body["files"])


# --- the three renderings -------------------------------------------------


async def test_files_are_a_resource_and_a_template(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert "session://files" in uris
    assert "grid://sessions" in uris
    templates = {t.uri_template for t in await server.mcp.list_resource_templates()}
    assert "session://files/{name}" in templates


async def test_the_app_shell_is_a_ui_resource(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert apps.RESOURCE_URI in uris


async def test_the_file_tools_are_hidden_from_a_resource_client(server):
    """A mirror is noise for a client that can read the resource itself."""
    names = {t.name for t in await server.mcp.list_tools()}
    assert "session_files" not in names
    assert "browser_sessions" not in names


async def test_the_file_tools_return_for_a_client_that_renders_apps(server):
    """For that client the tool is the only route to a rendered component."""
    with patch.object(apps, "supported", return_value=True):
        names = {t.name for t in await server.mcp.list_tools()}
    assert "session_files" in names
    assert "browser_sessions" in names


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
    assert "https://selenium.example.com" in csp.resource_domains
    assert apps.SDK_ORIGIN in csp.resource_domains


def test_the_app_csp_omits_an_origin_it_does_not_have():
    csp = apps.config_for("").csp
    assert csp.resource_domains == [apps.SDK_ORIGIN]
