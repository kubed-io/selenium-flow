"""Every URI this server serves has a view in show's table (programme R17).

A new resource or template without a row in `mcp/show.py` fails here, the way a
capability without both surfaces fails `test_surfaces.py`. If it fails, add the
row (and its view in the app) rather than editing the expectation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastmcp import Client

from kubed.selenium_flow.config import Settings
from kubed.selenium_flow.mcp import show
from kubed.selenium_flow.server import SeleniumMCP

from .conftest import TOKEN

pytestmark = pytest.mark.unit

# The app shell is the drawing, not a thing to draw (spec
# 2026-10-09-show-everything, ruling 8).
EXEMPT = ("ui://",)
SKILL = "skill://selenium-flow/"

# Research's table in the spec, with the skill's files folded into one.
FORMS = {
    "workspace://current",
    "workspace://site-data",
    "workspace://site-data/{site}",
    "workspace://files",
    "workspace://files/screenshots",
    "workspace://files/recordings",
    "workspace://files/downloads",
    "workspace://files/{name}",
    "workspace://files/screenshots/{name}",
    "workspace://files/recordings/{name}",
    "workspace://files/downloads/{name}",
    "flow://flows",
    "flow://flows/{name}",
    "flow://schema",
    "secret://secrets",
    SKILL + "*",
}


@pytest.fixture
def everything(tmp_path, built_ui):
    """Every section that registers a resource, turned on, and the UI built.

    Today a section that is off still lists its resource; a future one may list
    only when on, and the guard has to see it either way."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    secret = tmp_path / "secrets" / "demo"
    secret.mkdir(parents=True)
    (secret / "username").write_text("u")
    return SeleniumMCP(Settings(
        grid={"url": "http://grid.invalid:4444"},
        auth={"token": TOKEN},
        data={"dir": str(tmp_path / "data")},
        secrets={"dirs": str(tmp_path / "secrets")},
        recording={"enabled": True, "dir": str(inbox)},
    ))


async def served(server) -> list[str]:
    """Every listed resource's URI and every template, as a client sees them."""
    async with Client(server.mcp) as c:
        resources = [str(r.uri) for r in await c.list_resources()]
        # MCP SDK v2 renamed the field; the older one warns on the new SDK.
        templates = [
            getattr(t, "uri_template", None) or t.uriTemplate
            for t in await c.list_resource_templates()
        ]
    return resources + templates


def concrete(uri: str) -> str:
    """A template made into one URI it would answer."""
    return re.sub(r"\{[^}]+\}", "sample", uri)


async def test_every_uri_the_server_serves_has_a_view(everything):
    missing = []
    for uri in await served(everything):
        if uri.startswith(EXEMPT):
            continue
        try:
            show.view_for(concrete(uri))
        except ValueError:
            missing.append(uri)
    assert missing == [], f"no row in mcp/show.py VIEWS for: {missing}"


async def test_the_shell_itself_is_served_and_exempt(everything):
    assert "ui://selenium-flow/component" in await served(everything)


async def test_the_inventory_is_the_one_the_spec_lists(everything):
    """A new resource fails here too: add it to the spec's table and to FORMS."""
    forms = {
        SKILL + "*" if uri.startswith(SKILL) else uri
        for uri in await served(everything)
        if not uri.startswith(EXEMPT)
    }
    assert forms == FORMS


APP = Path(__file__).parents[1] / "ui" / "src" / "App.svelte"


def test_every_component_show_names_the_shell_draws():
    """The other end of the table: a component with no Svelte view would draw
    'Nothing to show'."""
    keys = set(re.findall(
        r"^\s+'?([\w-]+)'?: \w+ as Component<Props>,", APP.read_text(), re.M
    ))
    assert {view.component for view in show.VIEWS} == keys
