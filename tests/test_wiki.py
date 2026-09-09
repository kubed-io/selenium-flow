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
def test_the_sidebar_lists_every_action():
    """The sidebar is the only navigation a GitHub wiki has."""
    sidebar = (WIKI / "_Sidebar.md").read_text()
    actions = (WIKI / "Actions.md").read_text()
    for tool in re.findall(r"\[`(\w+)`\]\(\w+\)", actions):
        assert f"({tool})" in sidebar, f"{tool} is missing from _Sidebar.md"
