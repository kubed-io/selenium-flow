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

import json
import logging
import pathlib
import re

import pytest
import yaml
from fastmcp import Client
from fastmcp.skills import Skill, SkillsClientExtension
from mcp.shared.exceptions import MCPError
from mcp_types import METHOD_NOT_FOUND

from kubed.selenium_flow.mcp import skill as skill_module
from kubed.selenium_flow.mcp.skill import ENTRY, SKILL_NAME
from kubed.selenium_flow.mcp.skill_extension import EXTENSION_ID
from kubed.selenium_flow.server import SeleniumMCP

from .fakes import request

# The URIs FastMCP's skill providers publish, spelled out here as the
# convention being checked rather than read back from the server's own code.
RESOURCE_URI = f"skill://{SKILL_NAME}/{ENTRY}"
MANIFEST = "_manifest"
MANIFEST_URI = f"skill://{SKILL_NAME}/{MANIFEST}"

pytestmark = pytest.mark.unit

SKILL_DIR = skill_module.skill_path()
PYPROJECT = pathlib.Path(__file__).parent.parent / "pyproject.toml"


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
    """FastMCP refuses a skill whose folder is named otherwise (PLG-8)."""
    assert SKILL_DIR.name == SKILL_NAME == frontmatter()["name"]


def test_the_packaged_skill_is_a_valid_agent_skill():
    """The check that serves it, so an edit that breaks it fails here (PLG-18)."""
    assert Skill.from_directory(SKILL_DIR).name == SKILL_NAME


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


def test_the_one_session_contract_has_a_reference():
    """There is one contract now — name yourself — where there were two modes,
    so there is one reference rather than a branch to take first."""
    assert (SKILL_DIR / "references/WORKSPACES.md").is_file()
    for gone in ("STATELESS.md", "SAVED_SESSIONS.md"):
        assert not (SKILL_DIR / "references" / gone).is_file(), gone


def test_flows_says_to_reuse_a_tag_in_use():
    """An agent sets tags while writing a flow, so the page it reads then sends
    it to the tags already in use before it makes one up."""
    body = (SKILL_DIR / "references/FLOWS.md").read_text()
    section = body.split("## Tags", 1)[1].split("\n## ", 1)[0]
    assert "workspace://current/tags" in section
    assert "only when none fits" in section


async def test_the_listing_names_the_skill_once(server):
    """One row, so a client scanning resources/list sees one skill, not its files."""
    async with Client(server.mcp.server) as client:
        listed = await client.list_resources()
    rows = [r for r in listed if str(r.uri).startswith("skill://")]
    assert {str(r.uri) for r in rows} == {RESOURCE_URI}
    assert rows[0].description == frontmatter()["description"]


async def test_no_skill_template_is_listed(server):
    async with Client(server.mcp.server) as client:
        templates = await client.list_resource_templates()
    assert not [t for t in templates if t.uri_template.startswith("skill://")]


@pytest.mark.parametrize("path", [MANIFEST, *sorted(set(references()))])
async def test_every_file_still_reads_by_its_uri(server, path):
    async with Client(server.mcp.server) as client:
        contents = await client.read_resource(f"skill://{SKILL_NAME}/{path}")
    assert contents[0].text
    if path.endswith(".md"):
        assert contents[0].mimeType == "text/markdown"
    if path == MANIFEST:
        assert json.loads(contents[0].text)["skill"] == SKILL_NAME


async def test_an_unknown_skill_file_is_not_found(server):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError):
            await client.read_resource(f"skill://{SKILL_NAME}/NOPE.md")


@pytest.mark.parametrize("extensions", [[], [SkillsClientExtension()]], ids=["resources", "skills"])
async def test_a_fastmcp_client_reads_the_whole_skill(server, extensions):
    """Every file the manifest names, verified against its digest, with or
    without the Skills extension."""
    async with Client(server.mcp.server, extensions=extensions) as client:
        manifest = json.loads((await client.read_resource(MANIFEST_URI))[0].text)
        read = await client.read_skill(RESOURCE_URI)
    assert read.name == SKILL_NAME
    assert set(read.files) == {f["path"] for f in manifest["files"]}


# ---- the FastMCP skill convention ------------------------------------------


async def test_a_fastmcp_client_discovers_the_skill(server):
    """The real thing: FastMCP's own client, against this server."""
    async with Client(server.mcp.server, extensions=[SkillsClientExtension()]) as client:
        found = await client.list_skills()
        listed = await client.list_skill_resources()
    assert [s.frontmatter.name for s in found] == [SKILL_NAME]
    assert found[0].frontmatter.description == frontmatter()["description"]
    assert [str(r.uri) for r in listed] == [RESOURCE_URI]


async def test_a_fastmcp_client_can_read_the_manifest(server):
    """What `read_skill` relies on without the extension."""
    async with Client(server.mcp.server) as client:
        manifest = json.loads((await client.read_resource(MANIFEST_URI))[0].text)
    assert manifest["skill"] == SKILL_NAME
    assert ENTRY in [f["path"] for f in manifest["files"]]


# ---- reading ---------------------------------------------------------------


def test_load_returns_a_provider_for_the_packaged_skill():
    provider = skill_module.load()
    assert provider is not None
    assert provider.name == SKILL_NAME
    assert provider.skill.name == SKILL_NAME


def test_a_missing_skill_md_warns(tmp_path, monkeypatch, caplog):
    folder = tmp_path / SKILL_NAME
    folder.mkdir()
    monkeypatch.setattr(skill_module, "skill_path", lambda: folder)
    with caplog.at_level(logging.WARNING):
        assert skill_module.load() is None
    assert [r.levelno for r in caplog.records if str(folder) in r.getMessage()] == [
        logging.WARNING
    ]


def write_skill(folder: pathlib.Path, front: str) -> None:
    folder.mkdir()
    (folder / ENTRY).write_text(f"---\n{front}\n---\n\n# Body\n", encoding="utf-8")


def invalid_skill(tmp_path, case: str) -> pathlib.Path:
    """A folder that breaks one Agent Skills rule."""
    good = "name: odd-skill\ndescription: Does a thing."
    fronts = {
        "bad-name": ("Odd_Skill", "name: Odd_Skill\ndescription: Does a thing."),
        "folder-mismatch": ("odd-skill", "name: some-other-name\ndescription: Does a thing."),
        "no-description": ("odd-skill", "name: odd-skill"),
        "empty-description": ("odd-skill", "name: odd-skill\ndescription: ''"),
        "long-description": ("odd-skill", "name: odd-skill\ndescription: " + "x" * 1025),
        "not-a-mapping": ("odd-skill", "- a list\n- not a mapping"),
        "date": ("odd-skill", good + "\nwhen: 2026-10-05"),
        "nan": ("odd-skill", good + "\nweight: .nan"),
        "int-key": ("odd-skill", good + "\nmetadata:\n  1: one"),
    }
    if case in fronts:
        name, front = fronts[case]
        write_skill(tmp_path / name, front)
        return tmp_path / name
    folder = tmp_path / "odd-skill"
    if case == "symlink":
        write_skill(folder, good)
        (tmp_path / "outside.md").write_text("# Outside\n", encoding="utf-8")
        (folder / "linked.md").symlink_to(tmp_path / "outside.md")
    elif case == "not-utf8":
        folder.mkdir()
        (folder / ENTRY).write_bytes(f"---\n{good}\n---\n\n# Caf\xe9\n".encode("latin-1"))
    return folder


INVALID = {
    "bad-name": "lowercase letters",
    "folder-mismatch": "must match its SKILL.md name",
    "no-description": "description must contain",
    "empty-description": "description must contain",
    "long-description": "description must contain",
    "not-a-mapping": "frontmatter must be a mapping",
    "date": "finite JSON values",
    "nan": "finite JSON values",
    "int-key": "keys must be strings",
    "symlink": "symlink",
    "not-utf8": "utf-8",
}


@pytest.mark.parametrize("case", sorted(INVALID))
async def test_an_invalid_skill_is_not_served_at_all(tmp_path, monkeypatch, caplog, case):
    """Strict: no resource, no extension, no method, and an error saying why (PLG-18)."""
    from kubed.selenium_flow.config import Settings

    folder = invalid_skill(tmp_path, case)
    monkeypatch.setattr(skill_module, "skill_path", lambda: folder)
    with caplog.at_level(logging.WARNING):
        assert skill_module.load() is None
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert str(folder) in errors[0]
    assert INVALID[case] in errors[0]

    built = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "t"})
    )
    assert built.skill is None
    for mode in ("auto", "legacy"):
        async with Client(built.mcp.server, mode=mode) as client:
            uris = [str(r.uri) for r in await client.list_resources()]
            extensions = client.session.server_capabilities.extensions or {}
            with pytest.raises(MCPError) as caught:
                await request(client, "skills/list")
            with pytest.raises(MCPError) as folder_read:
                await request(
                    client, "resources/directory/read", uri=f"skill://{folder.name}"
                )
        assert not [u for u in uris if u.startswith("skill://")]
        assert EXTENSION_ID not in extensions
        assert caught.value.error.code == METHOD_NOT_FOUND
        assert folder_read.value.error.code == METHOD_NOT_FOUND


async def test_the_served_text_keeps_its_frontmatter(server):
    """The metadata is part of what a reader uses to judge relevance."""
    async with Client(server.mcp.server) as client:
        contents = await client.read_resource(RESOURCE_URI)
    assert contents[0].text.startswith("---")


# ---- how it is served ------------------------------------------------------
#
# Whether the skill is on unless switched off is `McpSettings.skill`
# (`settings.mcp.skill`) now
# (see test_config_load.py's boolean-flag tests) — `mcp/skill.py` no longer
# reads the environment itself.


async def test_switching_the_skill_off_removes_its_resources():
    from kubed.selenium_flow.config import Settings

    off = SeleniumMCP(
        Settings(grid={"url": "http://grid.invalid:4444"}, auth={"token": "t"},
                 mcp={"skill": False})
    )
    assert off.skill is None
    uris = {str(r.uri) for r in await off.mcp.server.list_resources()}
    assert RESOURCE_URI not in uris and MANIFEST_URI not in uris


async def test_every_tool_and_resource_has_a_row_on_the_page_it_always_reads(built_ui, server):
    """An agent that misreads its own task skips a router but scans a table.

    The first agent to fly a real app read FLOWS.md and never opened
    INTERACTION.md, because it thought it had a "this menu won't open" problem
    rather than an interaction question — so it never learned `hover` existed.
    One row per tool and per resource, on the page it always reads.

    Taken from the live server, with the two reading tools left out: they are
    how a client reads the resource rows, and the page says so in prose.
    """
    from kubed.selenium_flow.mcp import mirror

    body = (SKILL_DIR / ENTRY).read_text()
    tools = {t.name for t in await server.mcp.server.list_tools()} - set(mirror.MIRROR_TOOLS)
    missing = sorted(t for t in tools if not re.search(rf"^\| `{t}` \|", body, re.MULTILINE))
    assert not missing, f"SKILL.md's tool table has no row for {missing}"

    reads = {str(r.uri) for r in await server.mcp.server.list_resources()} | {
        t.uri_template for t in await server.mcp.server.list_resource_templates()
    }
    reads = {u for u in reads if not u.startswith(("ui://", "skill://"))}
    missing = sorted(u for u in reads if not re.search(rf"^\| `{re.escape(u)}` \|", body, re.MULTILINE))
    assert not missing, f"SKILL.md's resource table has no row for {missing}"


# ---- the examples have to work ---------------------------------------------


def documented_selectors() -> list[tuple[str, str]]:
    """Every `selector=` / `to=` literal the docs teach, with where it came from.

    The braces are matched by counting rather than by a regex, and that is the
    point: a pattern that stops at the first `}` cannot see a NESTED literal,
    which is precisely the mistake this exists to catch. The first version of
    this helper used one, skipped every broken example, and passed.
    """
    import re

    found = []
    root = PYPROJECT.parent
    pages = [*SKILL_DIR.rglob("*.md"), root / "README.md", root / "AGENTS.md"]
    for page in pages:
        if not page.is_file():
            continue
        text = page.read_text()
        for match in re.finditer(r"(?:selector|to)=\{", text):
            start = match.end() - 1
            depth = 0
            for index in range(start, len(text)):
                if text[index] == "{":
                    depth += 1
                elif text[index] == "}":
                    depth -= 1
                    if depth == 0:
                        found.append((page.name, text[start : index + 1]))
                        break
            else:
                # Ran off the end of the page. An unclosed literal is a broken
                # example, and skipping it is how a guard goes quiet (Copilot).
                raise AssertionError(f"unclosed selector literal in {page.name}")
    return found


def test_there_are_documented_selectors_to_check():
    """Without this the test below passes by having no cases."""
    assert documented_selectors()


def test_every_documented_selector_is_one_the_server_would_accept():
    """These are copied straight into a call, so a broken one costs a round trip
    and some confusion. A sweep over the docs once wrote `selector={"selector":
    {...}}` into thirty of them and every test stayed green, because nothing
    read the prose (Copilot, #34).
    """
    import ast

    from kubed.selenium_flow.core.browser import locator

    for _page, literal in documented_selectors():
        # Parsed as written. Rewriting quotes to make it JSON broke every XPath
        # with a quoted literal in it, and the skip hid half the cases (Copilot, #35).
        parsed = ast.literal_eval(literal)
        locator(parsed)  # raises if it is not exactly one of xpath or css


@pytest.mark.parametrize(
    "phrase, sent",
    [
        ("has control of this browser since", "HELD"),
        ("asked for control of this browser at", "REQUESTED"),
        ("control of this browser was handed back", "HANDED_BACK"),
        ("'s control; call again.", "STILL"),
    ],
)
def test_the_control_notice_phrases_are_what_the_server_sends(phrase, sent):
    """The skill quotes what an agent will match a result against: a phrase it
    teaches must be one the server says, in the sentence it says it in."""
    from kubed.selenium_flow.live import control

    workspaces = (SKILL_DIR / "references/WORKSPACES.md").read_text()
    said = getattr(control, sent).format(who="drk", at="14:02")
    assert phrase in said
    assert phrase in re.sub(r"\s+", " ", workspaces)


@pytest.mark.parametrize("sent", ["HELD", "STILL"])
def test_the_control_sentences_the_skill_quotes_whole_are_the_servers(sent):
    """A fragment survives a clause dropped from the code's sentence; the
    example quoted whole does not."""
    from kubed.selenium_flow.live import control

    workspaces = re.sub(
        r"\s+", " ", (SKILL_DIR / "references/WORKSPACES.md").read_text()
    )
    assert getattr(control, sent).format(who="alex", at="14:02") in workspaces
