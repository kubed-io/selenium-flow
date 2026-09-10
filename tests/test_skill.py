"""The embedded skill: its authoring constraints, and how it is served.

Three different things are pinned here.

The authoring rules (frontmatter shape, body length) are Anthropic's published
limits; a skill that breaks them is not loadable by the clients meant to read it.

The URI is FastMCP's convention, not ours - its client helpers find skills by
scanning for `skill://<name>/SKILL.md`. The strongest test here does that
discovery for real rather than asserting on a string.

The serving rules are this repo's: resources by default, a mirroring tool for
clients without resources, nothing at all when it is switched off.
"""

import fnmatch
import pathlib
import re

import pytest
import yaml
from fastmcp import Client
from fastmcp.utilities.skills import get_skill_manifest, list_skills

from kubed.selenium_flow import resources as resources_module
from kubed.selenium_flow import skill as skill_module
from kubed.selenium_flow.resources import STATUS_TOOL
from kubed.selenium_flow.server import SeleniumMCP
from kubed.selenium_flow.skill import (
    ENTRY,
    MANIFEST,
    MANIFEST_URI,
    RESOURCE_URI,
    SKILL_NAME,
    SKILL_TOOL,
)

# tomllib is 3.11+; on 3.10 the reader is tomli, which the `test` extra pulls in
# under that marker. This used to fall back to None and skip the test below —
# which meant the one check that proves every skill file reaches the wheel was
# silently absent on the oldest interpreter, the leg most likely to break.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 only
    import tomli as tomllib

pytestmark = pytest.mark.unit

SKILL_DIR = skill_module.skill_path()
PYPROJECT = pathlib.Path(__file__).parent.parent / "pyproject.toml"


def http(params=None, headers=None):
    return dict(params or {}), dict(headers or {})


def frontmatter() -> dict:
    return yaml.safe_load((SKILL_DIR / ENTRY).read_text().split("---", 2)[1])


# ---- authoring constraints -------------------------------------------------


def test_the_skill_is_present():
    assert (SKILL_DIR / ENTRY).is_file()


def test_the_name_is_a_valid_skill_name():
    """Max 64 chars, lowercase letters, digits and hyphens only."""
    assert re.fullmatch(r"[a-z0-9-]{1,64}", frontmatter()["name"])


def test_the_description_is_present_and_within_budget():
    """Max 1024 characters and non-empty: it is what decides whether a client
    loads the skill at all, so an empty one makes the skill invisible."""
    description = frontmatter()["description"]
    assert description.strip()
    assert len(description) <= 1024


def test_the_description_says_when_to_use_it_not_just_what_it_is():
    """A description that omits the trigger cannot be matched against a task."""
    assert "use when" in frontmatter()["description"].lower()


def test_the_body_stays_within_the_recommended_budget():
    """Under 500 lines; past that it should be split into supporting files."""
    lines = (SKILL_DIR / ENTRY).read_text().splitlines()
    assert len(lines) < 500, f"SKILL.md is {len(lines)} lines; split it up"


def test_the_directory_name_is_the_skill_name():
    """SkillProvider takes the name from the folder, NOT the frontmatter.

    Renaming the directory silently renames the skill and moves its URI, so the
    two must agree or the published name contradicts the file's own metadata.
    """
    assert SKILL_DIR.name == SKILL_NAME == frontmatter()["name"]


def test_every_skill_file_is_covered_by_package_data():
    """Otherwise the file is missing from the wheel, with no error anywhere.

    The skill is only useful because it ships with the code it describes, so a
    supporting file that silently does not install is the feature failing
    quietly.
    """
    config = tomllib.loads(PYPROJECT.read_text())
    setuptools = config["tool"]["setuptools"]
    assert setuptools["package-dir"]["kubed.selenium_flow.skills"] == "skills", (
        "the root skills/ directory must map into the package, or it does not ship"
    )
    patterns = setuptools["package-data"]["kubed.selenium_flow.skills"]
    assert patterns, "no package-data patterns for the skill directory"

    # Patterns are relative to the mapped root, which is skills/ itself.
    for path in SKILL_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(SKILL_DIR.parent).as_posix()
        # fnmatch rather than Path.full_match, which is 3.13+ only. Its `*`
        # crosses directory separators, which is loose but right for the
        # question being asked: is this file covered by any pattern at all.
        assert any(fnmatch.fnmatch(rel, p) for p in patterns), (
            f"{rel} matches no package-data pattern {patterns}"
        )


# ---- the index and its references ------------------------------------------


def references() -> list[str]:
    """Every references/... path the index points at."""
    return re.findall(r"references/[A-Z_]+\.md", (SKILL_DIR / ENTRY).read_text())


def test_the_index_points_at_references():
    """The top level is a guide to the rest, not the whole manual."""
    assert references(), "SKILL.md links to no references at all"


def test_every_referenced_file_exists():
    """A dead link costs a wasted round trip and teaches nothing."""
    for path in sorted(set(references())):
        assert (SKILL_DIR / path).is_file(), f"SKILL.md links to missing {path}"


def test_every_reference_is_reachable_from_the_index():
    """A file nobody links to will never be lazily loaded, so it may as well
    not ship."""
    linked = set(references())
    on_disk = {
        p.relative_to(SKILL_DIR).as_posix()
        for p in (SKILL_DIR / "references").glob("*.md")
    }
    assert on_disk <= linked, f"unreferenced files: {sorted(on_disk - linked)}"


def test_both_session_modes_have_a_reference():
    """The one branch every caller has to take before anything else works."""
    assert (SKILL_DIR / "references/STATELESS.md").is_file()
    assert (SKILL_DIR / "references/SAVED_SESSIONS.md").is_file()


async def test_each_reference_is_its_own_resource(server):
    """Listed individually rather than hidden behind the manifest, so a client
    can link straight to the one it needs."""
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    for path in sorted(set(references())):
        assert f"skill://{SKILL_NAME}/{path}" in uris


async def test_a_reference_can_be_read_through_the_tool(server):
    body = skill_module.read(server.skill, "references/STATELESS.md")
    assert body and body.startswith("#")


# ---- the FastMCP skill convention ------------------------------------------


def test_the_uri_follows_the_discovery_convention():
    """`list_skills` scans for exactly this shape; anything else is invisible."""
    assert f"skill://{SKILL_NAME}/{ENTRY}" == RESOURCE_URI
    assert RESOURCE_URI.startswith("skill://") and RESOURCE_URI.endswith("/SKILL.md")
    assert f"skill://{SKILL_NAME}/{MANIFEST}" == MANIFEST_URI


async def test_a_fastmcp_client_discovers_the_skill(server):
    """The real thing: FastMCP's own helper, against this server."""
    async with Client(server.mcp) as client:
        found = await list_skills(client)
    assert [s.name for s in found] == [SKILL_NAME]
    assert found[0].description == frontmatter()["description"]


async def test_a_fastmcp_client_can_read_the_manifest(server):
    """What `download_skill` relies on to know which files to fetch."""
    async with Client(server.mcp) as client:
        manifest = await get_skill_manifest(client, SKILL_NAME)
    assert manifest.name == SKILL_NAME
    assert ENTRY in [f.path for f in manifest.files]


async def test_the_skill_resources_are_listed(server):
    uris = {str(r.uri) for r in await server.mcp.list_resources()}
    assert {RESOURCE_URI, MANIFEST_URI} <= uris


# ---- reading ---------------------------------------------------------------


def test_load_returns_a_provider_for_the_packaged_skill():
    provider = skill_module.load()
    assert provider is not None
    assert provider.skill_info.name == SKILL_NAME


def test_the_served_text_keeps_its_frontmatter(server):
    """The metadata is part of what a reader uses to judge relevance."""
    assert skill_module.read(server.skill, ENTRY).startswith("---")


def test_the_manifest_lists_what_ships(server):
    assert ENTRY in skill_module.read(server.skill, MANIFEST)


def test_reading_a_file_that_does_not_exist_returns_none(server):
    assert skill_module.read(server.skill, "nope.md") is None


@pytest.mark.parametrize(
    "path", ["../server.py", "/etc/passwd", "a/../../server.py", "../../pyproject.toml"]
)
def test_only_files_on_the_manifest_can_be_read(server, path):
    assert skill_module.read(server.skill, path) is None


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, True),
        ({"SKILL_ENABLED": "true"}, True),
        ({"SKILL_ENABLED": "false"}, False),
        ({"SKILL_ENABLED": "0"}, False),
        ({"SKILL_ENABLED": "off"}, False),
    ],
)
def test_the_skill_is_on_unless_switched_off(env, expected):
    assert skill_module.enabled(env) is expected


# ---- how it is served ------------------------------------------------------


async def test_the_skill_tool_is_hidden_from_clients_that_read_resources(server):
    assert SKILL_TOOL not in {t.name for t in await server.mcp.list_tools()}


async def test_the_skill_tool_appears_for_a_client_that_cannot(server, monkeypatch):
    monkeypatch.setattr(resources_module, "_http", lambda: http({"resources": "off"}))
    names = {t.name for t in await server.mcp.list_tools()}
    assert SKILL_TOOL in names
    assert STATUS_TOOL in names, "both mirrors are revealed by the same switch"


async def test_the_hidden_skill_tool_is_still_callable(server):
    assert await server.mcp.get_tool(SKILL_TOOL) is not None


async def test_switching_the_skill_off_removes_both_shapes():
    off = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token="t", skill_enabled=False
    )
    assert off.skill is None
    uris = {str(r.uri) for r in await off.mcp.list_resources()}
    assert RESOURCE_URI not in uris and MANIFEST_URI not in uris
    assert SKILL_TOOL not in {t.name for t in await off.mcp.list_tools()}


async def test_the_status_mirror_still_works_with_the_skill_off():
    """Turning one feature off must not take the other's mirror with it."""
    off = SeleniumMCP(
        grid_url="http://grid.invalid:4444", auth_token="t", skill_enabled=False
    )
    assert await off.mcp.get_tool(STATUS_TOOL) is not None
