"""Every screenshot and print is kept with the session, straight to its files.

`save` made the agent decide, per screenshot, whether a person would ever want
to look at it — and the answer was usually no, so the admin UI showed nothing
and a human asking "what did it see?" had nothing to open (§F2.9).

They used to reach the session's files by being handed back to the page as a
download, which put them at the mercy of every download Chrome refuses: a
plain-http page, a page with no origin, a second save from `about:blank`. The
bytes are this server's, so they are written where they are kept (§F3.8).
"""

import base64

import pytest

from kubed.selenium_flow import errors, faults
from kubed.selenium_flow.core.actions import Actions
from kubed.selenium_flow.core.browser import Grid

from .conftest import calling_as

pytestmark = pytest.mark.unit

# A real 1x1 PNG, because the action reads the image's dimensions out of it.
PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)
PDF = base64.b64encode(b"%PDF-1.7 pretend").decode()


class _Driver:
    current_url = "https://example.test/orders"
    title = "Orders"
    page_source = "<html><body><h1>Orders</h1></body></html>"

    def __init__(self):
        self.printed = []

    def get_screenshot_as_base64(self):
        return PIXEL

    def print_page(self, options=None):
        self.printed.append(options)
        return PDF

    def get_window_size(self):
        return {"width": 800, "height": 600}

    def set_window_size(self, *_):
        pass

    def execute_script(self, *_):
        return None


class _Keeper:
    """Records what an action asked to keep, and answers like the store."""

    def __init__(self, fail=None):
        self.kept = []
        self.fail = fail

    def __call__(self, name, data, folder):
        if self.fail:
            raise self.fail
        self.kept.append((name, data, folder))
        return {"name": name, "size": len(data), "kept": True}


@pytest.fixture
def driver():
    return _Driver()


@pytest.fixture
def keeper():
    return _Keeper()


@pytest.fixture
def acting(driver, keeper, monkeypatch):
    actions = Actions(Grid("http://grid.invalid:4444"), keep=keeper)
    monkeypatch.setattr(actions, "_at", lambda *a, **k: driver)
    return actions


# ---- screenshots ------------------------------------------------------------


def test_a_screenshot_is_kept_without_being_asked(acting, keeper):
    result = acting.screenshot("abc")
    assert result["file"] == {"name": "screenshot.png", "size": 70, "kept": True}
    assert keeper.kept == [("screenshot.png", base64.b64decode(PIXEL), "screenshots")]


def test_save_false_still_means_do_not_keep_it(acting, keeper):
    """The escape hatch stays, for a flow taking thirty frames it will never
    look at again."""
    result = acting.screenshot("abc", save=False)
    assert "file" not in result
    assert keeper.kept == []


@pytest.mark.parametrize(
    "page", ["data:text/html,<h1>test</h1>", "about:blank", "http://intranet.lan/"]
)
def test_a_screenshot_is_kept_from_pages_the_browser_would_not_download_from(
    acting, driver, keeper, page
):
    """The three pages Chrome refused a download on, measured on Chrome 152, and
    the reason the browser is no longer asked (§F3.8)."""
    driver.current_url = page
    result = acting.screenshot("abc")
    assert result["file"]["name"] == "screenshot.png"
    assert "file_error" not in result


def test_a_save_that_fails_still_returns_the_image_without_quoting_the_disk(
    acting, driver, monkeypatch
):
    """Saving is on every screenshot, so it must never take one away. And an
    OSError names a path under DATA_DIR, which is nobody's business."""
    acting.keep = _Keeper(OSError(28, "No space left", "/data/flows/x/files/s.png"))
    result = acting.screenshot("abc")
    assert result["image"] == PIXEL
    assert "file" not in result
    assert result["file_error"] == "the capture could not be kept (OSError)"


def test_a_server_that_keeps_nothing_says_so_and_still_shows_the_image(acting):
    from kubed.selenium_flow.http import files

    acting.keep = _Keeper(ValueError(files.OFF))
    result = acting.screenshot("abc")
    assert result["image"] == PIXEL
    assert "DATA_DIR" in result["file_error"]


@pytest.mark.parametrize(
    "given,expected",
    [("chart", "chart.png"), ("chart.png", "chart.png"), ("chart.pdf", "chart.pdf.png")],
)
def test_a_screenshot_is_named_as_the_png_it_is(acting, keeper, given, expected):
    """The file store guesses the served type from the name, so a PNG called
    `chart.pdf` is handed to a browser as a PDF and will not open."""
    acting.screenshot("abc", filename=given)
    assert keeper.kept[0][0] == expected


def test_a_screenshot_is_kept_in_the_screenshots_folder(acting, keeper):
    acting.screenshot("abc")
    assert keeper.kept[0][2] == "screenshots"


def test_a_print_is_kept_in_files(acting, keeper):
    acting.print_("abc")
    assert keeper.kept[0][2] == "files"


# ---- print -------------------------------------------------------------------


def test_a_pdf_is_the_browser_s_own_print_kept(acting, driver, keeper):
    result = acting.print_("abc")
    assert keeper.kept == [("page.pdf", b"%PDF-1.7 pretend", "files")]
    assert result["format"] == "pdf"
    assert result["bytes"] == len(b"%PDF-1.7 pretend")
    assert result["file"]["name"] == "page.pdf"
    printed = driver.printed[0]
    assert printed.orientation != "landscape"
    assert printed.background is False


def test_landscape_and_background_reach_the_print(acting, driver):
    acting.print_("abc", landscape=True, background=True)
    printed = driver.printed[0]
    assert printed.orientation == "landscape"
    assert printed.background is True


def test_html_is_the_page_as_it_stands(acting, driver, keeper):
    driver.page_source = "<html><body><p>rendered by script</p></body></html>"
    result = acting.print_("abc", format="HTML", filename="orders")
    assert keeper.kept == [("orders.html", driver.page_source.encode(), "files")]
    assert result["format"] == "html"
    assert driver.printed == [], "html must not go through the print command"


@pytest.mark.parametrize(
    "fmt,given,expected",
    [("pdf", "page.png", "page.png.pdf"), ("html", "page.pdf", "page.pdf.html")],
)
def test_a_print_is_named_as_what_it_is(acting, keeper, fmt, given, expected):
    acting.print_("abc", format=fmt, filename=given)
    assert keeper.kept[0][0] == expected


@pytest.mark.parametrize("fmt", ["mhtml", None, "", False])
def test_an_unknown_format_is_a_bad_request_that_names_the_choices(acting, keeper, fmt):
    """`null` and `""` over HTTP included: the tool's enum refuses them, and
    quietly printing a PDF instead would make the two surfaces disagree."""
    with pytest.raises(ValueError) as refused:
        acting.print_("abc", format=fmt)
    assert "pdf, html" in str(refused.value)
    assert errors.status_for(refused.value) == 400
    assert keeper.kept == []


def test_a_print_that_cannot_be_kept_does_not_quote_the_disk(acting):
    acting.keep = _Keeper(OSError(13, "Permission denied", "/data/flows/x/files/p.pdf"))
    with pytest.raises(RuntimeError) as failed:
        acting.print_("abc")
    assert str(failed.value) == "the print could not be kept (PermissionError)"
    assert "/data" not in faults.message(failed.value)
    assert errors.status_for(failed.value) == 500


# ---- through the real server: the store, the name, the link -------------------


@pytest.fixture
def keeping_server(tmp_path):
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    return SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": "tok"},
        public_base_url="https://selenium.example.com",
        data={"dir": str(tmp_path)},
    ))


def test_the_server_keeps_it_on_disk_with_a_signed_link(keeping_server, tmp_path):
    """Through the real wiring: the seam is easy to leave unconnected, and a
    missing link looks exactly like a server that has no public base."""
    entry = keeping_server.actions.keep("shot.png", b"png", "files")
    assert entry["name"] == "shot.png"
    assert entry["url"].startswith("https://selenium.example.com/")
    assert "sig=" in entry["url"], "the link must be signed"
    assert "absolute_url" not in entry, "one link, not two"
    assert [p.read_bytes() for p in tmp_path.rglob("shot.png")] == [b"png"]


def test_a_second_file_of_the_same_name_does_not_replace_the_first(keeping_server):
    """Somebody may already have been handed a link to the first one."""
    first = keeping_server.actions.keep("shot.png", b"one", "files")
    second = keeping_server.actions.keep("shot.png", b"two", "files")
    third = keeping_server.actions.keep("shot.png", b"three", "files")
    assert [first["name"], second["name"], third["name"]] == [
        "shot.png",
        "shot (1).png",
        "shot (2).png",
    ]


def test_two_saves_racing_for_one_name_do_not_overwrite_each_other(
    keeping_server, monkeypatch
):
    """Another save can take the name between looking and writing, so nothing
    looks: the create is what claims it (Copilot, #40)."""
    first = keeping_server.actions.keep("shot.png", b"one", "files")

    def unlisted(session):
        raise AssertionError("a name is claimed by creating it, not by listing")

    monkeypatch.setattr(keeping_server.flows, "files", unlisted)
    second = keeping_server.actions.keep("shot.png", b"two", "files")
    assert second["name"] == "shot (1).png"
    assert keeping_server.flows.read_file("stdio", first["name"]) == b"one"


def test_a_server_with_no_data_dir_refuses_to_keep_with_the_reason():
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "tok"})
    )
    with pytest.raises(ValueError, match="DATA_DIR"):
        server.actions.keep("shot.png", b"png", "files")


@pytest.mark.parametrize(
    ("public", "expected"),
    [
        ("", "/flow/kept/"),  # a path, since nobody said where the server is
        ("https://sf.example", "https://sf.example/flow/kept/"),
        ("https://sf.example/flow", "https://sf.example/flow/kept/"),
    ],
)
def test_a_mounted_server_hands_out_links_it_serves(tmp_path, public, expected):
    """The route is at `/flow/kept`, so the link must be too — whether or not a
    public base is set, and once only when that base already names the mount."""
    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.server import SeleniumMCP

    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": "tok"},
        public_base_url=public,
        route_prefix="/flow",
        data={"dir": str(tmp_path)},
    ))
    described = server.actions.keep("shot.png", b"png", "files")
    assert described["url"].startswith(expected)


def test_print_over_http_keeps_the_file_for_the_workspace_that_asked(
    keeping_server, monkeypatch, tmp_path
):
    """The other surface, end to end: the alias, the body, the caller's name
    and the response shape (Copilot, #40)."""
    from starlette.testclient import TestClient

    driver = _Driver()
    monkeypatch.setattr(keeping_server.actions, "_at", lambda *a, **k: driver)
    monkeypatch.setattr(keeping_server.workspaces, "resolve", lambda name: "abc")
    client = TestClient(
        keeping_server.mcp.http_app(),
        headers={"Authorization": "Bearer tok", "X-Session-Key": "desk"},
    )
    response = client.post(
        "/browser/print", json={"format": "pdf", "landscape": True, "filename": "q3"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "pdf"
    assert body["file"]["name"] == "q3.pdf"
    assert "/kept/desk/q3.pdf" in body["file"]["url"]
    assert driver.printed[0].orientation == "landscape"
    assert [p.read_bytes() for p in tmp_path.rglob("q3.pdf")] == [b"%PDF-1.7 pretend"]
    refused = client.post("/browser/print", json={"format": None})
    assert refused.status_code == 400


def test_a_kept_page_opens_without_its_scripts(keeping_server):
    """A printed page is a site's HTML. Served inline on this origin, its
    scripts would run beside the admin UI and its token (Copilot, #40).

    A raster image cannot script, and is not sandboxed: the opaque origin broke
    an extension reading localStorage, which threw until the tab hung.
    """
    from starlette.testclient import TestClient

    from kubed.selenium_flow.http import links

    inert = "default-src 'none'; style-src 'unsafe-inline'"
    media = "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'"
    client = TestClient(keeping_server.mcp.http_app())
    for name, csp in (
        ("page.html", "sandbox"),
        ("shot.svg", "sandbox"),
        ("page.xht", "sandbox"),
        ("mystery", "sandbox"),
        ("page.pdf", None),
        ("shot.png", inert),
        ("shot.jpg", inert),
        ("shot.gif", inert),
        ("shot.webp", inert),
        ("shot.avif", inert),
        ("clip.mp4", media),
        ("clip.webm", media),
    ):
        keeping_server.flows.write_file("stdio", name, b"<script>alert(1)</script>")
        response = client.get(links.kept_url("stdio", name, "tok"))
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers.get("content-security-policy") == csp, name


async def test_link_ttl_is_how_long_a_link_opens(tmp_path):
    """Through the real wiring, on every surface that signs: a result's link,
    the MCP listing, and the admin page's listing."""
    import json
    import time

    from fastmcp import Client
    from starlette.testclient import TestClient

    from kubed.selenium_flow.config import Settings
    from kubed.selenium_flow.http import links
    from kubed.selenium_flow.server import SeleniumMCP

    day = 86400
    server = SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": "tok"},
        link_ttl=day,
        data={"dir": str(tmp_path)},
    ))

    def lasts(url: str) -> float:
        return int(url.split("exp=")[1].split("&")[0]) - time.time()

    kept = server.actions.keep("shot.png", b"png", "screenshots")
    assert day - 5 <= lasts(kept["url"]) < day + links.EXPIRY_STEP

    async with Client(server.mcp) as client:
        listed = await client.read_resource("session://files/screenshots")
    entry = json.loads(listed[0].text)["files"][0]
    assert day - 5 <= lasts(entry["url"]) < day + links.EXPIRY_STEP

    body = TestClient(server.mcp.http_app()).get(
        "/admin/sessions/stdio/files", headers={"Authorization": "Bearer tok"}
    ).json()
    assert [f["name"] for f in body["screenshots"]] == ["shot.png"]
    assert day - 5 <= lasts(body["screenshots"][0]["url"]) < day + links.EXPIRY_STEP


def test_a_link_opens_for_at_least_a_minute():
    from kubed.selenium_flow.config import Settings

    with pytest.raises(ValueError, match="link_ttl"):
        Settings(link_ttl=30)


async def test_print_is_a_tool_that_keeps_the_file(keeping_server, monkeypatch):
    from fastmcp import Client

    driver = _Driver()
    monkeypatch.setattr(keeping_server.actions, "_at", lambda *a, **k: driver)
    monkeypatch.setattr(keeping_server.workspaces, "resolve", lambda name: "abc")
    async with Client(keeping_server.mcp) as client:
        result = await client.call_tool("print", {"format": "html"})
    assert result.structured_content["file"]["name"] == "page.html"
    assert result.structured_content["file"]["uri"] == "session://files/page.html"


@pytest.mark.parametrize("kept", [{}, {"file": {"name": "shot.png"}},
                                  {"file_error": "not kept"}])
async def test_a_screenshot_carries_the_site_data_hint(keeping_server, monkeypatch, kept):
    """The call can land on a waiting origin like any other; the image must not
    swallow the hint that says it was restored."""
    from fastmcp import Client

    hint = {"restored": ["https://w.test"], "uri": "session://site-data/w.test"}
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    monkeypatch.setattr(keeping_server.workspaces, "act", lambda name, call, **kw: {
        "image": png, "site_data": hint, **kept})
    calling_as(monkeypatch, "s")
    async with Client(keeping_server.mcp) as client:
        result = await client.call_tool("screenshot", {})
    assert result.content[0].type == "image"
    assert result.structured_content == {"site_data": hint, **kept}


# ---- what the tools promise ----------------------------------------------------


async def test_the_tools_tell_the_agent_to_hand_over_the_link(server):
    """A hint the model reads while deciding, not after."""
    for name in ("screenshot", "print"):
        description = (await server.mcp.get_tool(name)).description or ""
        assert "the file's url" in description, f"{name} does not mention the link"


async def test_the_prompt_does_not_promise_a_file_that_may_not_exist(server):
    """`save=false` and a failed save both mean there is no file, and a model
    told otherwise will hand a person a name that does not exist."""
    description = (await server.mcp.get_tool("screenshot")).description or ""
    assert "By default" in description
    assert "file_error" in description


async def test_the_prompt_does_not_promise_a_signature_auth_off_cannot_give(server):
    """With AUTH_TOKEN unset there is nothing to sign with, and links.py
    emits the plain path deliberately."""
    for name in ("screenshot", "print"):
        description = (await server.mcp.get_tool(name)).description or ""
        assert "signed" not in description or "when this server has a token" in description


def test_the_published_file_shape_matches_what_describe_returns():
    """A generated client hides or rejects fields the document does not declare,
    and the two drifted the moment this was written by hand (Copilot, #28)."""
    from kubed.selenium_flow.http import files
    from kubed.selenium_flow.spec import FILE_SCHEMAS

    entry = FILE_SCHEMAS["FileEntry"]["properties"]
    for described in (
        # A file already in Files: no keep_with, nothing left to keep.
        files.describe(files.FILES, {"name": "s.png", "size": 3, "creationTime": 1}, "https://h/s.png"),
        # A screenshot or a download: carries keep_with, since neither is kept yet.
        files.describe(
            files.SCREENSHOTS, {"name": "s.png", "size": 3, "creationTime": 1}, "https://h/s.png"
        ),
    ):
        assert set(described) <= set(entry), (
            "the OpenAPI file schema is missing keys that are actually returned: "
            f"{sorted(set(described) - set(entry))}"
        )
    # The Grid can omit creationTime, so the descriptor's `created` can be null
    # and a generated client must accept that (Copilot, #28).
    assert "null" in entry["created"]["type"]
    assert files.describe(files.FILES, {"name": "x.png"}, "https://h/x.png")["created"] is None


# ---- an insecure browser -----------------------------------------------------


def test_an_insecure_browser_accepts_a_self_signed_certificate_and_nothing_else():
    """Asked for per browser, never the default (Copilot and Dr K, #40). The
    insecure-content setting it once also carried let an https page load http
    scripts; with nothing downloaded through the page, nothing needs it."""
    grid = Grid("http://grid.invalid:4444")
    default = grid._options("chrome")
    insecure = grid._options("chrome", insecure=True)
    assert not default.accept_insecure_certs
    assert insecure.accept_insecure_certs
    assert grid._options("firefox", insecure=True).accept_insecure_certs
    prefs = insecure.experimental_options["prefs"]
    assert "profile.default_content_setting_values.mixed_script" not in prefs


def test_open_session_opens_insecure_only_when_asked_and_remembers_it(server, monkeypatch):
    """Through the tool: no env var, parameter or header turns it on, and a
    reopen after the browser went keeps it, like every other setting."""
    import asyncio

    from fastmcp import Client

    from kubed.selenium_flow.config import Settings, WorkspaceSettings, load
    from kubed.selenium_flow.workspace import settings

    # §F3.8, pinned at both ends: the config schema has no field for it at
    # all, so nothing in `session.*` can ever set a floor for it...
    assert "insecure" not in WorkspaceSettings.model_fields
    # ...and naming it in the environment is naming nothing: SESSION_INSECURE
    # is not a leaf `config.py` knows, so `load` ignores it rather than
    # inventing somewhere to put it.
    assert load([], {"SESSION_INSECURE": "true"}).settings == Settings()
    assert "insecure" not in settings.from_client({"insecure": "true"}, {"x-insecure": "true"})

    asked = []

    class _Driver:
        session_id = "abc"
        current_url = "about:blank"
        title = ""

        def get_window_size(self):
            return {"width": 800, "height": 600}

        def get(self, url):
            self.current_url = url

    def opening(browser=None, insecure=False, record=False, video_name=None):
        asked.append(insecure)
        return _Driver()

    monkeypatch.setattr(server.grid, "open", opening)
    monkeypatch.setattr(server.grid, "quit", lambda *_: None)

    async def go():
        async with Client(server.mcp) as client:
            await client.call_tool("open_session", {})
            await client.call_tool("open_session", {"insecure": True})
            opened = await client.call_tool("open_session", {})
            # And an explicit false beats the remembered true (Copilot, #40).
            await client.call_tool("open_session", {"insecure": False})
            await client.call_tool("open_session", {})
            return opened.structured_content

    last = asyncio.run(go())
    assert asked == [False, True, True, False, False]
    assert last["settings"]["insecure"] is True
