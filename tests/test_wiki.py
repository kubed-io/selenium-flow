"""The wiki's action pages are generated, so they can go stale like the spec.

Skipped entirely when the submodule is not checked out: a clone without it is a
working clone, and CI should not need the wiki to test the server.
"""

import pathlib
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).parent.parent
WIKI = REPO / "wiki"
GENERATOR = REPO / "scripts" / "generate_wiki.py"

needs_wiki = pytest.mark.skipif(
    not (WIKI / "Home.md").is_file(),
    reason="wiki submodule not checked out",
)


def _spec() -> dict:
    """The live spec, built the way the generator builds it."""
    import asyncio

    from kubed.selenium_flow.openapi import build_spec
    from kubed.selenium_flow.routes import ENDPOINTS
    from kubed.selenium_flow.server import SeleniumMCP

    async def go():
        server = SeleniumMCP(grid_url="http://grid.invalid:4444", auth_token="x")
        return await build_spec(server.mcp, ENDPOINTS, "/browser", authenticated=True)

    return asyncio.run(go())


@needs_wiki
def test_the_generated_pages_are_current():
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@needs_wiki
def test_every_internal_link_resolves():
    """A broken wiki link is invisible until somebody clicks it."""
    pages = {p.stem for p in WIKI.glob("*.md")}
    broken = []
    for page in sorted(WIKI.glob("*.md")):
        body = page.read_text()
        # Strip inline code first: `![alt](url)` in a syntax example is not a link.
        body = re.sub(r"`[^`\n]*`", "", body)
        body = re.sub(r"```.*?```", "", body, flags=re.S)
        for text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body):
            if target.startswith(("http", "#", "mailto:")):
                continue
            if target.split("#")[0] not in pages:
                broken.append(f"{page.name}: [{text}]({target})")
    assert not broken, "broken wiki links: " + "; ".join(broken)


@needs_wiki
def test_no_page_is_shadowed_by_a_file_in_a_subdirectory():
    """A wiki page is addressed by basename, whatever directory it sits in.

    `wiki/notes/screenshot.md` and `wiki/screenshot.md` therefore both answered
    to /wiki/screenshot, and GitHub served the fragment — so the page appeared
    to have lost everything but its prose while the raw file was perfect. The
    notes live in wiki/notes/ and are suffixed `.notes.md` for exactly that
    reason; this keeps a bare `<tool>.md` from coming back.
    """
    top = {p.stem for p in WIKI.glob("*.md")}
    nested = {
        p: p.stem for p in WIKI.rglob("*.md") if p.parent != WIKI and ".git" not in p.parts
    }
    clashes = [str(p.relative_to(WIKI)) for p, stem in nested.items() if stem in top]
    assert not clashes, (
        "these files shadow a top-level wiki page by basename: " + ", ".join(clashes)
    )


@needs_wiki
def test_the_sidebar_lists_every_action():
    """The sidebar is the only navigation a GitHub wiki has."""
    sidebar = (WIKI / "_Sidebar.md").read_text()
    actions = (WIKI / "Actions.md").read_text()
    for tool in re.findall(r"\[`(\w+)`\]\(\w+\)", actions):
        assert f"({tool})" in sidebar, f"{tool} is missing from _Sidebar.md"


@needs_wiki
def test_every_action_with_an_endpoint_has_a_page():
    """The gap this file existed to catch and did not.

    The generator used to select pages by `path.startswith("/browser/")`, so
    flows and kept files never got one — while the pages that *were* generated
    went on naming `keep_file` and `session_files` in their prose, because those
    come from tool docstrings. The wiki became internally inconsistent by
    working correctly.

    The rule is now the spec's own `x-mcp-tool`: an operation that is one half
    of an action a caller can also reach over MCP gets a page. Asserted against
    the live spec rather than against the generator, so adding a surface with a
    route table of its own fails here rather than going undocumented.
    """
    spec = _spec()
    expected = {
        op["x-mcp-tool"]
        for item in spec["paths"].values()
        for op in item.values()
        if "x-mcp-tool" in op
    }
    assert {"run_flow", "keep_file"} <= expected, "the spec lost a surface"
    missing = {t for t in expected if not (WIKI / f"{t}.md").is_file()}
    assert not missing, "no wiki page for: " + ", ".join(sorted(missing))


@needs_wiki
def test_the_guides_that_pages_link_to_exist():
    """`Actions` sends a reader to Flows and Files, and the env table sends them
    to Secrets. Those are hand-written, so nothing regenerates them into being —
    which is exactly how a generated link ends up pointing at nothing."""
    for guide in ("Flows", "Files", "Secrets"):
        assert (WIKI / f"{guide}.md").is_file(), guide


def test_the_env_table_documents_the_switches_for_every_feature():
    """`FLOW_DATA_DIR` and `SECRETS_DIRS` were missing from a table listing
    seventeen other variables, so the two biggest features shipped invisible to
    anyone deploying from the manual."""
    if not (WIKI / "Deployment.md").is_file():
        pytest.skip("wiki submodule not checked out")
    table = (WIKI / "Deployment.md").read_text()
    for name in ("FLOW_DATA_DIR", "SECRETS_DIRS", "GRID_URL", "MCP_AUTH_TOKEN"):
        assert f"`{name}`" in table, name
