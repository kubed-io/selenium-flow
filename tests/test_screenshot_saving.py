"""Every screenshot is kept with the session, because nobody can see the rest.

`save` made the agent decide, per screenshot, whether a person would ever want
to look at it — and the answer was usually no, so the admin UI showed nothing
and a human asking "what did it see?" had nothing to open. A session's files are
ephemeral and cheap; deciding was the expensive part. See saga §F2.9.
"""

from unittest.mock import patch

import pytest

from kubed.selenium_flow.core import actions as actions_module

pytestmark = pytest.mark.unit

# A real 1x1 PNG, because the action reads the image's dimensions out of it.
PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)
ENTRY = {"name": "screenshot.png", "size": 3, "creationTime": 1}


class _Driver:
    current_url = "https://example.test/orders"
    title = "Orders"

    def get_screenshot_as_base64(self):
        return PIXEL

    def get_window_size(self):
        return {"width": 800, "height": 600}

    def set_window_size(self, *_):
        pass

    def execute_script(self, *_):
        return None


@pytest.fixture
def shooting(actions, monkeypatch):
    """Actions pointed at a driver that always hands back one pixel."""
    monkeypatch.setattr(actions, "_at", lambda *a, **k: _Driver())
    return actions


def test_a_screenshot_is_saved_without_being_asked(shooting):
    with patch.object(
        actions_module.browser, "save_to_downloads", return_value=ENTRY
    ) as saving:
        result = shooting.screenshot("abc")
    assert result["file"] == ENTRY
    assert saving.call_count == 1


def test_save_false_still_means_do_not_keep_it(shooting):
    """The escape hatch stays, for a flow taking thirty frames it will never
    look at again."""
    with patch.object(actions_module.browser, "save_to_downloads") as saving:
        result = shooting.screenshot("abc", save=False)
    assert "file" not in result
    assert saving.call_count == 0


def test_a_save_that_fails_still_returns_the_image(shooting):
    """Saving is now on every screenshot, so it must never be able to take one
    away. A page whose policy blocks the download, or a Grid that never lists
    the file, costs you the file and not the picture."""
    with patch.object(
        actions_module.browser,
        "save_to_downloads",
        side_effect=TimeoutError("screenshot.png did not appear in the downloads"),
    ):
        result = shooting.screenshot("abc")
    assert result["image"] == PIXEL
    assert "file" not in result
    assert "did not appear" in result["file_error"]


# ---- the link is the point: a person cannot see a tool result ---------------


def test_a_saved_file_carries_its_link_when_the_server_can_sign_one(shooting):
    """The agent should hand a person a URL, not a base64 image it cannot show.

    The signer is injected rather than built here: the behaviour layer never
    holds the token, it holds a function the server closed over (§F2.9).
    """
    described = {"name": "screenshot.png", "url": "/files/abc/screenshot.png?sig=x"}
    shooting.describe_file = lambda session_id, entry: {**described, "for": session_id}
    with patch.object(actions_module.browser, "save_to_downloads", return_value=ENTRY):
        result = shooting.screenshot("abc")
    assert result["file"]["url"] == described["url"]
    assert result["file"]["for"] == "abc"


def test_without_a_signer_the_raw_entry_is_still_returned(shooting):
    """An open server with no public base still saves; it just has no link."""
    with patch.object(actions_module.browser, "save_to_downloads", return_value=ENTRY):
        result = shooting.screenshot("abc")
    assert result["file"] == ENTRY


def test_a_pdf_carries_its_link_too(shooting, monkeypatch):
    """Same rule, same helper — two places that describe a stored file is how
    they start disagreeing."""
    monkeypatch.setattr(
        actions_module.browser, "save_to_downloads", lambda *a, **k: ENTRY
    )
    shooting.describe_file = lambda session_id, entry: {**entry, "absolute_url": "https://x/f"}
    driver = _Driver()
    driver.print_page = lambda: PIXEL
    monkeypatch.setattr(shooting, "_at", lambda *a, **k: driver)
    result = shooting.save_pdf("abc")
    assert result["file"]["absolute_url"] == "https://x/f"


async def test_the_server_wires_the_signer_up(monkeypatch):
    """Through the real server: the seam is easy to leave unconnected, and a
    missing link looks exactly like a server that has no public base."""
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://selenium.example.com/flow")
    server = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token="tok")
    described = server.actions.describe_file("abc", ENTRY)
    assert described["absolute_url"].startswith("https://selenium.example.com/flow")
    assert "sig=" in described["absolute_url"], "the link must be signed"


@pytest.mark.parametrize(
    ("public", "absolute"),
    [
        ("", None),  # a path is not an absolute URL, however it looks
        ("https://sf.example", "https://sf.example/flow/files/"),
        ("https://sf.example/flow", "https://sf.example/flow/files/"),
    ],
)
def test_a_mounted_server_hands_out_links_it_serves(monkeypatch, public, absolute):
    """The route is at `/flow/files`, so the link must be too — whether or not a
    public base is set, and once only when that base already names the mount."""
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.setenv("PUBLIC_BASE_URL", public)
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token="tok", route_prefix="/flow"
    )
    described = server.actions.describe_file("abc", ENTRY)
    assert described["url"].startswith("/flow/files/abc/")
    if absolute is None:
        assert "absolute_url" not in described
    else:
        assert described["absolute_url"].startswith(absolute + "abc/")


async def test_the_tools_tell_the_agent_to_hand_over_the_link(server):
    """A hint the model reads while deciding, not after."""
    for name in ("screenshot", "save_pdf"):
        description = (await server.mcp.get_tool(name)).description or ""
        assert "absolute_url" in description, f"{name} does not mention the link"


# ---- what a failed save is allowed to say (Copilot, #28) --------------------


def test_the_reason_a_save_failed_never_quotes_the_grid(shooting):
    """Storing goes through the Grid's HTTP API, and requests puts the whole URL
    in its message — a URL this deployment may put credentials in."""
    import requests

    leaky = requests.HTTPError(
        "500 Server Error for url: http://user:hunter2@grid.internal:4444/session"
    )
    with patch.object(
        actions_module.browser, "save_to_downloads", side_effect=leaky
    ):
        result = shooting.screenshot("abc")
    assert "hunter2" not in result["file_error"]
    assert "grid.internal" not in result["file_error"]
    assert "HTTPError" in result["file_error"], "it should still say what kind"


def test_our_own_timeout_still_says_the_useful_thing(shooting):
    """It names the file and says it never arrived, and contains no address."""
    with patch.object(
        actions_module.browser,
        "save_to_downloads",
        side_effect=TimeoutError("shot.png did not appear in the session's downloads"),
    ):
        result = shooting.screenshot("abc")
    assert result["file_error"] == "shot.png did not appear in the session's downloads"


def test_the_published_file_shape_matches_what_describe_returns():
    """A generated client hides or rejects fields the document does not declare,
    and the two drifted the moment this was written by hand (Copilot, #28)."""
    from kubed.selenium_flow.http import files
    from kubed.selenium_flow.spec import FILE_SCHEMAS

    entry = FILE_SCHEMAS["FileEntry"]["properties"]
    described = files.describe(
        "sess", {"name": "shot.png", "size": 3, "creationTime": 1}, "tok", "https://h"
    )
    assert set(described) <= set(entry), (
        "the OpenAPI file schema is missing keys that are actually returned: "
        f"{sorted(set(described) - set(entry))}"
    )
    # The Grid can omit creationTime, so the descriptor's `created` can be null
    # and a generated client must accept that (Copilot, #28).
    assert "null" in entry["created"]["type"]
    assert files.describe("sess", {"name": "x.png"}, "tok")["created"] is None


# ---- saving by default must not cost the caller time or lie about the type ---
# From the suppressed half of Copilot's review on #28.


def test_a_page_that_cannot_download_is_not_waited_out(shooting):
    """Chrome refuses downloads from a data: URL, and the poll would spend its
    whole timeout finding that out — on every frame of a thirty-frame flow, now
    that saving is the default."""
    driver = _Driver()
    driver.current_url = "data:text/html,<h1>test</h1>"
    shooting._at = lambda *a, **k: driver
    with patch.object(actions_module.browser, "save_to_downloads") as saving:
        result = shooting.screenshot("abc")
    assert saving.call_count == 0, "it tried anyway, and would wait out the poll"
    assert "data:" in result["file_error"]
    assert result["image"] == PIXEL


def test_the_default_save_does_not_wait_the_full_download_timeout(shooting):
    """15s is the right budget for a file the caller asked for. It is the wrong
    one for a best-effort save on every screenshot."""
    with patch.object(
        actions_module.browser, "save_to_downloads", return_value=ENTRY
    ) as saving:
        shooting.screenshot("abc")
    assert saving.call_args.kwargs["timeout"] <= 5


@pytest.mark.parametrize(
    "given,expected",
    [("chart", "chart.png"), ("chart.png", "chart.png"), ("chart.pdf", "chart.pdf.png")],
)
def test_a_screenshot_is_named_as_the_png_it_is(shooting, given, expected):
    """The file store guesses the served type from the name, so a PNG called
    `chart.pdf` is handed to a browser as a PDF and will not open."""
    with patch.object(
        actions_module.browser, "save_to_downloads", return_value=ENTRY
    ) as saving:
        shooting.screenshot("abc", filename=given)
    assert saving.call_args.args[2] == expected


def test_a_pdf_is_named_as_the_pdf_it_is(shooting, monkeypatch):
    driver = _Driver()
    driver.print_page = lambda: PIXEL
    monkeypatch.setattr(shooting, "_at", lambda *a, **k: driver)
    with patch.object(
        actions_module.browser, "save_to_downloads", return_value=ENTRY
    ) as saving:
        shooting.save_pdf("abc", filename="page.png")
    assert saving.call_args.args[2] == "page.png.pdf"


async def test_the_prompt_does_not_promise_a_file_that_may_not_exist(server):
    """`save=false` and a blocked download both mean there is no file, and a
    model told otherwise will hand a person a name that does not exist."""
    description = (await server.mcp.get_tool("screenshot")).description or ""
    assert "By default" in description
    assert "file_error" in description


async def test_the_prompt_does_not_promise_a_signature_auth_off_cannot_give(server):
    """With MCP_AUTH_TOKEN unset there is nothing to sign with, and links.py
    emits the plain path deliberately."""
    for name in ("screenshot", "save_pdf"):
        description = (await server.mcp.get_tool(name)).description or ""
        assert "signed" not in description or "when this server has a token" in description


def test_insecure_content_is_allowed_only_when_the_server_is_told_to():
    """It lets Chrome keep a PDF saved from an http page — and lets an https page
    load http scripts, which could read a secret typed into it. So it is an
    opt-in (Copilot, #40), measured on Chrome 152."""
    from kubed.selenium_flow.core.browser import Grid

    key = "profile.default_content_setting_values.mixed_script"
    default = Grid("http://grid.invalid:4444")._options("chrome").experimental_options
    allowed = Grid("http://grid.invalid:4444", allow_insecure_content=True)._options(
        "chrome"
    ).experimental_options
    assert key not in default["prefs"]
    assert allowed["prefs"][key] == 1
    assert default["prefs"]["profile.default_content_setting_values.automatic_downloads"] == 1


def test_the_setting_reaches_the_grid_from_the_environment(monkeypatch):
    from kubed.selenium_flow.main import build_parser
    from kubed.selenium_flow.server import SeleniumMCP

    monkeypatch.setenv("ALLOW_INSECURE_CONTENT", "true")
    args = build_parser().parse_args([])
    server = SeleniumMCP(
        grid_url="http://grid.invalid:4444",
        allow_insecure_content=args.allow_insecure_content,
    )
    assert server.grid.allow_insecure_content is True


class _Page:
    session_id = "abc"

    def __init__(self, opaque=False, protocol="https:", agent="Chrome/152"):
        self.answer = [opaque, protocol, agent]

    def execute_script(self, script, *args):
        if "window.origin" in script:
            return self.answer
        return None


class _EmptyStore:
    allow_insecure_content = False

    def files(self, session_id):
        return []


def _refused(page, store=None):
    from kubed.selenium_flow.core import browser

    with pytest.raises(TimeoutError) as refused:
        browser.save_to_downloads(store or _EmptyStore(), page, "s.pdf", b"x", "application/pdf", timeout=0)
    return str(refused.value)


def test_a_save_refused_on_a_page_with_no_origin_says_why():
    """Chrome allows about:blank or a data: page one download. A 15-second
    'did not appear' said nothing about that, or about what to do."""
    assert "Navigate to a real page" in _refused(_Page(opaque=True))


def test_a_save_refused_on_an_http_page_names_the_setting():
    said = _refused(_Page(protocol="http:"))
    assert "ALLOW_INSECURE_CONTENT" in said and "https page" in said


def test_with_the_setting_on_an_http_refusal_does_not_blame_it():
    store = _EmptyStore()
    store.allow_insecure_content = True
    assert "did not appear" in _refused(_Page(protocol="http:"), store)
    assert "did not appear" in _refused(_Page(protocol="http:", agent="Firefox/150"))
