"""Serving the built UI, and serving without one (§F4.15, §F4.17)."""

import logging
import pathlib
import re
import shutil

import pytest
from starlette.testclient import TestClient

from kubed.selenium_flow.http import admin
from kubed.selenium_flow.mcp import apps
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import SHELL, TOKEN

pytestmark = pytest.mark.unit

UI = pathlib.Path(__file__).resolve().parent.parent / "ui"
FLOW_TS = UI / "src" / "admin" / "flow.ts"
SHELLS = UI / "public"


def _server():
    return SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token=TOKEN)


def test_without_a_build_the_page_is_the_placeholder():
    res = TestClient(_server().mcp.http_app()).get("/")
    assert res.status_code == 200
    assert res.text == admin.PLACEHOLDER
    assert "<title>Selenium Flow</title>" in res.text and "<h1>Selenium Flow</h1>" in res.text


def test_without_a_build_no_app_is_offered_and_the_files_resource_stays():
    import asyncio

    uris = {str(r.uri) for r in asyncio.run(_server().mcp.list_resources())}
    assert apps.RESOURCE_URI not in uris
    assert "session://files" in uris


def test_without_a_build_startup_says_how_to_build(caplog):
    with caplog.at_level(logging.INFO):
        _server()
    assert any("npm --prefix ui run build" in r.getMessage() for r in caplog.records)


def test_with_a_build_the_page_inlines_its_bundle_and_fills_the_server_values(built_ui):
    res = TestClient(_server().mcp.http_app()).get("/")
    assert "/* admin css */" in res.text and "/* admin js */" in res.text
    assert 'data-console="/"' in res.text and 'data-mount=""' in res.text
    assert "__" not in res.text.replace("__init__", "")


def test_the_bundle_goes_in_after_the_placeholders(built_ui):
    """A placeholder-shaped string inside the bundle is never substituted."""
    (built_ui / "admin.js").write_text("const s = '__MOUNT__'")
    assert "const s = '__MOUNT__'" in admin.page("admin", MOUNT="/flow", CONSOLE="/")



def test_one_bundle_is_never_searched_for_the_other(built_ui):
    """The stylesheet goes in first; a `__JS__` inside it is text, not a slot."""
    (built_ui / "admin.css").write_text("/* __JS__ */")
    out = admin.page("admin", MOUNT="", CONSOLE="/")
    assert "<style>/* __JS__ */</style>" in out
    assert '<script type="module">/* admin js */</script>' in out

def test_a_script_close_in_the_bundle_cannot_end_the_inline_script(built_ui):
    (built_ui / "admin.js").write_text("const s = '</script><b>'; const t = '</SCRIPT>'")
    out = admin.page("admin", MOUNT="", CONSOLE="/")
    assert out.count("</script>") == 1 and "</SCRIPT>" not in out


def test_a_placeholder_named_in_a_shell_comment_does_not_take_the_bundle(built_ui):
    """The bundle goes in once, so it has to go in where it runs — not into a
    comment that mentions the placeholder, leaving the real one unfilled."""
    shell = "<!-- __CSS__ and __JS__ are the bundle -->\n" + SHELL.format(name="admin")
    (built_ui / "admin.html").write_text(shell)
    out = admin.page("admin", MOUNT="", CONSOLE="/")
    assert "<style>/* admin css */</style>" in out
    assert '<script type="module">/* admin js */</script>' in out
    assert "__CSS__" not in out and "__JS__" not in out


@pytest.mark.skipif(not SHELLS.is_dir(), reason="ui/ is not in this tree (an sdist without it)")
@pytest.mark.parametrize("name", ["admin", "app"])
def test_the_real_shells_are_filled_completely(built_ui, name):
    """Against the shells the build actually copies, not a stand-in."""
    shutil.copy(SHELLS / f"{name}.html", built_ui / f"{name}.html")
    out = admin.page(name, MOUNT="/flow", CONSOLE="/")
    assert f"<style>/* {name} css */</style>" in out
    assert f">/* {name} js */</script>" in out
    assert not re.search(r"__[A-Z]+__", out)


def test_ui_built_needs_all_three_files(ui_dir):
    (ui_dir / "admin.html").write_text("x")
    (ui_dir / "admin.js").write_text("x")
    assert not admin.ui_built("admin")
    (ui_dir / "admin.css").write_text("x")
    assert admin.ui_built("admin")


async def test_with_a_build_the_app_shell_is_a_ui_resource(built_ui):
    uris = {str(r.uri) for r in await _server().mcp.list_resources()}
    assert apps.RESOURCE_URI in uris


def test_the_app_csp_admits_our_own_origin_and_nothing_else():
    """The SDK is bundled now, so no CDN is declared (spec, difference 1)."""
    csp = apps.config_for("https://selenium.example.com/flow").csp
    assert csp.resource_domains == ["https://selenium.example.com"]
    assert csp.connect_domains == ["https://selenium.example.com"]


@pytest.mark.skipif(not FLOW_TS.is_file(), reason="ui/ is not in this tree (an sdist without it)")
def test_every_runnable_action_has_a_glyph_and_no_glyph_is_stale():
    """An outline row drops the tool's name, so a tool with no icon would be a
    step with no identity; the unknown glyph is reserved for a flow naming an
    action that does not exist. And a glyph for an action the runner no longer
    knows is a rename that only half happened."""
    from kubed.selenium_flow.flows.run import RUNNABLE

    body = re.search(r"export const TOOL_ICON[^=]*=\s*\{(.*?)\n\}", FLOW_TS.read_text(), re.S)
    assert body, "TOOL_ICON is no longer an object literal in flow.ts"
    keys = set(re.findall(r"(?:^|[\s,{])([A-Za-z_]\w*)\s*:", body.group(1)))
    assert keys, "no keys parsed out of TOOL_ICON"
    assert sorted(set(RUNNABLE) - keys) == [], "runnable actions with no glyph"
    assert sorted(keys - set(RUNNABLE)) == [], "glyphs for actions the runner does not know"
