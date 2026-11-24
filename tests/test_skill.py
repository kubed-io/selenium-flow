"""The embedded skills: their authoring constraints, and how they are served.

Three different things are pinned here.

The authoring rules (frontmatter shape, body length) are Anthropic's published
limits; a skill that breaks them is not loadable by the clients meant to read it.
Every skill is held to them (PLG-4).

The URI is FastMCP's convention under kubed-mcp's namespace: its client helpers
find skills by scanning for `skill://<path>/SKILL.md`, and the path is the
server's name and the skill's. The strongest test here does that discovery for
real rather than asserting on a string.

How a plugin's skill is served is kubed-mcp's (PLUG-19, PLUG-20 and SKL-*), with
its own tests; these check selenium-flow's skills, as its server serves them.
"""

import json
import pathlib
import re

import pytest
import yaml
from fastmcp import Client
from fastmcp.skills import Skill, SkillsClientExtension
from kubed.mcp.protocol import MCPError

from kubed.selenium_flow.mcp import skill as skill_module

# The URIs served, spelled out here as the convention being checked rather than
# read back from the server's own code: the server's name, then the skill's.
NAMESPACE = "skill://selenium-flow"
ENTRY = "SKILL.md"
MANIFEST = "_manifest"
# The entry point first: every other skill assumes it has been read.
SKILLS = (
    "driving-a-browser",
    "building-flows",
    "signing-in",
    "using-the-admin-ui",
    "deploying-the-server",
)
ENTRY_SKILL = "driving-a-browser"
# Lines of SKILL.md, frontmatter included (#195): the references hold the
# substance. The entry point carries the rules and the tool table, so it may
# run longer.
BUDGET = dict.fromkeys(SKILLS, (10, 40)) | {ENTRY_SKILL: (10, 100)}

pytestmark = pytest.mark.unit

SKILLS_DIR = skill_module.skills_path()
REPO = pathlib.Path(__file__).parent.parent
PYPROJECT = REPO / "pyproject.toml"
# A skill:// address any text names: up to the closing backtick, quote or space.
ADDRESS = re.compile(r"skill://selenium-flow/[A-Za-z0-9_./-]*[A-Za-z0-9_]")


def root(name: str) -> str:
    return f"{NAMESPACE}/{name}"


def frontmatter(name: str) -> dict:
    return yaml.safe_load((SKILLS_DIR / name / ENTRY).read_text().split("---", 2)[1])


def body(name: str) -> str:
    return (SKILLS_DIR / name / ENTRY).read_text()


# ---- the set of skills -----------------------------------------------------


def test_the_plugin_ships_exactly_the_five_skills():
    """No folder beyond the five (#195), and none of them missing."""
    on_disk = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    assert on_disk == set(SKILLS)


def test_a_reference_file_name_is_unique_across_the_skills():
    """`guidance.pointer(page)` names a page alone, so the page has to name
    its skill."""
    seen: dict[str, str] = {}
    for name in SKILLS:
        for page in (SKILLS_DIR / name / "references").glob("*"):
            assert page.name not in seen, f"{page.name} is in {seen.get(page.name)} and {name}"
            seen[page.name] = name


def test_guidance_names_the_skill_that_ships_each_page():
    from kubed.selenium_flow.core import guidance

    assert set(guidance.SKILLS) == set(SKILLS)
    for page, name in guidance.PAGES.items():
        assert (SKILLS_DIR / name / "references" / page).is_file(), page


# ---- authoring constraints -------------------------------------------------


@pytest.mark.parametrize("name", SKILLS)
def test_the_skill_is_present(name):
    assert (SKILLS_DIR / name / ENTRY).is_file()


@pytest.mark.parametrize("name", SKILLS)
def test_the_name_is_a_valid_skill_name(name):
    """Max 64 chars, lowercase letters, digits and hyphens only."""
    assert re.fullmatch(r"[a-z0-9-]{1,64}", frontmatter(name)["name"])


@pytest.mark.parametrize("name", SKILLS)
def test_the_description_is_present_and_within_budget(name):
    """Max 1024 characters and non-empty: it is what decides whether a client
    loads the skill at all, so an empty one makes the skill invisible."""
    description = frontmatter(name)["description"]
    assert description.strip()
    assert len(description) <= 1024


@pytest.mark.parametrize("name", SKILLS)
def test_the_description_says_when_to_use_it_not_just_what_it_is(name):
    """A description that omits the trigger cannot be matched against a task;
    it opens with it (superpowers writing-skills)."""
    assert frontmatter(name)["description"].startswith("Use when")


@pytest.mark.parametrize("name", SKILLS)
def test_the_body_stays_within_its_budget(name):
    """Short and tabular: what the skill covers, then when to read which page."""
    low, high = BUDGET[name]
    lines = body(name).splitlines()
    assert low <= len(lines) <= high, f"{name}/SKILL.md is {len(lines)} lines"


@pytest.mark.parametrize("name", SKILLS)
def test_the_directory_name_is_the_skill_name(name):
    """FastMCP refuses a skill whose folder is named otherwise (PLG-8)."""
    assert frontmatter(name)["name"] == name


@pytest.mark.parametrize("name", SKILLS)
def test_the_packaged_skill_is_a_valid_agent_skill(name):
    """The check that serves it, so an edit that breaks it fails here (PLG-18)."""
    assert Skill.from_directory(SKILLS_DIR / name).name == name


# ---- the index and its references ------------------------------------------


def linked(name: str) -> set[tuple[str, str]]:
    """Every (skill, path) a SKILL.md points at by address."""
    found = set()
    for address in ADDRESS.findall(body(name)):
        skill, _, path = address.removeprefix(NAMESPACE + "/").partition("/")
        found.add((skill, path))
    return found


@pytest.mark.parametrize("name", SKILLS)
def test_the_index_points_at_references_by_their_exact_address(name):
    """The agent knows the exact link for whatever it needs right away."""
    assert any(skill == name and path.startswith("references/") for skill, path in linked(name))


@pytest.mark.parametrize("name", SKILLS)
def test_every_address_the_index_names_exists(name):
    """A dead link costs a wasted round trip and teaches nothing."""
    for skill, path in sorted(linked(name)):
        assert (SKILLS_DIR / skill / path).is_file(), f"{name}/SKILL.md links to missing {skill}/{path}"


@pytest.mark.parametrize("name", SKILLS)
def test_every_reference_is_reachable_from_its_own_index(name):
    """A file nobody links to will never be lazily loaded, so it may as well
    not ship."""
    own = {path for skill, path in linked(name) if skill == name}
    on_disk = {
        p.relative_to(SKILLS_DIR / name).as_posix()
        for p in (SKILLS_DIR / name / "references").glob("*.md")
    }
    assert on_disk <= own, f"unreferenced files: {sorted(on_disk - own)}"


def test_the_entry_point_names_every_other_skill():
    """The instructions name one entry; the rest are found from it."""
    named = {skill for skill, _ in linked(ENTRY_SKILL)}
    assert named >= set(SKILLS)


def test_the_one_session_contract_has_a_reference():
    """There is one contract now — name yourself — where there were two modes,
    so there is one reference rather than a branch to take first."""
    references = SKILLS_DIR / ENTRY_SKILL / "references"
    assert (references / "WORKSPACES.md").is_file()
    for gone in ("STATELESS.md", "SAVED_SESSIONS.md"):
        assert not (references / gone).is_file(), gone


def test_flows_says_to_reuse_a_tag_in_use():
    """An agent sets tags while writing a flow, so the page it reads then sends
    it to the tags already in use before it makes one up."""
    text = (SKILLS_DIR / "building-flows/references/FLOWS.md").read_text()
    section = text.split("## Tags", 1)[1].split("\n## ", 1)[0]
    assert "workspace://current/tags" in section
    assert "only when none fits" in section


def test_the_admin_skill_states_its_rules():
    """An agent doing admin work asks first and never persists the token
    (#195, PLG-62)."""
    text = re.sub(
        r"\s+", " ", (SKILLS_DIR / "using-the-admin-ui/references/AGENT_AS_ADMIN.md").read_text()
    )
    for rule in ("Ask the person first", "save_site_data", "execute_script", "take control", "confirm"):
        assert rule in text, rule


# ---- every address a reader is given reads ---------------------------------


def addresses() -> list[tuple[str, str]]:
    """Every `skill://selenium-flow/…` address in the server, the plugin, the
    docs an agent reads and the golden files, with where it was found."""
    files = [
        *(REPO / "kubed").rglob("*.py"),
        *(REPO / "plugin").rglob("*.md"),
        REPO / "README.md",
        REPO / "AGENTS.md",
        REPO / "CONTRIBUTING.md",
        *(REPO / "tests" / "golden").glob("*.json"),
    ]
    found = set()
    for path in files:
        for address in ADDRESS.findall(path.read_text()):
            # A template (`<skill>`, `{skill}`) or a bare prefix names no file.
            if address.endswith((".md", MANIFEST)) and "<" not in address and "{" not in address:
                found.add((path.relative_to(REPO).as_posix(), address))
    return sorted(found)


def test_there_are_addresses_to_check():
    assert len(addresses()) > 10


async def test_every_address_named_anywhere_reads(server):
    """A hint, a prompt or a page that names an address the server does not
    serve teaches the agent the manual is broken. None of the old
    single-skill addresses is left (no redirects)."""
    unread = []
    async with Client(server.mcp.server) as client:
        for where, address in addresses():
            try:
                contents = await client.read_resource(address)
            except MCPError:
                unread.append((where, address))
                continue
            assert contents[0].text, address
    assert not unread, unread


# ---- served ----------------------------------------------------------------


async def test_the_listing_names_each_skill_once(server):
    """One row per skill, so a client scanning resources/list sees five skills,
    not their files."""
    async with Client(server.mcp.server) as client:
        listed = await client.list_resources()
    rows = {str(r.uri): r for r in listed if str(r.uri).startswith("skill://")}
    assert set(rows) == {f"{root(name)}/{ENTRY}" for name in SKILLS}
    for name in SKILLS:
        assert rows[f"{root(name)}/{ENTRY}"].description == frontmatter(name)["description"]


async def test_no_skill_template_is_listed(server):
    async with Client(server.mcp.server) as client:
        templates = await client.list_resource_templates()
    assert not [t for t in templates if t.uri_template.startswith("skill://")]


def every_file() -> list[tuple[str, str]]:
    return [
        (name, path)
        for name in SKILLS
        for path in [MANIFEST, *sorted(p for s, p in linked(name) if s == name)]
    ]


@pytest.mark.parametrize("name, path", every_file())
async def test_every_file_reads_by_its_uri(server, name, path):
    async with Client(server.mcp.server) as client:
        contents = await client.read_resource(f"{root(name)}/{path}")
    assert contents[0].text
    if path.endswith(".md"):
        assert contents[0].mimeType == "text/markdown"
    if path == MANIFEST:
        assert json.loads(contents[0].text)["skill"] == name


async def test_an_unknown_skill_file_is_not_found(server):
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError):
            await client.read_resource(f"{root(ENTRY_SKILL)}/NOPE.md")


async def test_the_old_single_skill_address_is_gone(server):
    """No redirect (Dr K's no-backcompat rule)."""
    async with Client(server.mcp.server) as client:
        with pytest.raises(MCPError):
            await client.read_resource(f"{root('selenium-flow')}/{ENTRY}")


@pytest.mark.parametrize("extensions", [[], [SkillsClientExtension()]], ids=["resources", "skills"])
@pytest.mark.parametrize("name", SKILLS)
async def test_a_fastmcp_client_reads_the_whole_skill(server, extensions, name):
    """Every file the manifest names, verified against its digest, with or
    without the Skills extension."""
    async with Client(server.mcp.server, extensions=extensions) as client:
        manifest = json.loads((await client.read_resource(f"{root(name)}/{MANIFEST}"))[0].text)
        read = await client.read_skill(f"{root(name)}/{ENTRY}")
    assert read.name == name
    assert set(read.files) == {f["path"] for f in manifest["files"]}


# ---- the FastMCP skill convention ------------------------------------------


async def test_a_fastmcp_client_discovers_the_skills(server):
    """The real thing: FastMCP's own client, against this server."""
    async with Client(server.mcp.server, extensions=[SkillsClientExtension()]) as client:
        found = await client.list_skills()
        listed = await client.list_skill_resources()
    assert sorted(s.frontmatter.name for s in found) == sorted(SKILLS)
    for skill in found:
        assert skill.frontmatter.description == frontmatter(skill.frontmatter.name)["description"]
    assert sorted(str(r.uri) for r in listed) == sorted(f"{root(n)}/{ENTRY}" for n in SKILLS)


@pytest.mark.parametrize("name", SKILLS)
async def test_a_fastmcp_client_can_read_the_manifest(server, name):
    """What `read_skill` relies on without the extension."""
    async with Client(server.mcp.server) as client:
        manifest = json.loads((await client.read_resource(f"{root(name)}/{MANIFEST}"))[0].text)
    assert manifest["skill"] == name
    assert ENTRY in [f["path"] for f in manifest["files"]]


# ---- reading ---------------------------------------------------------------


def test_the_plugin_serves_the_packaged_skills(server):
    assert sorted(server.plugin.skills) == sorted(f"{root(n)}/{ENTRY}" for n in SKILLS)
    assert server.plugin.namespace == "selenium-flow"


@pytest.mark.parametrize("name", SKILLS)
async def test_the_served_text_keeps_its_frontmatter(server, name):
    """The metadata is part of what a reader uses to judge relevance."""
    async with Client(server.mcp.server) as client:
        contents = await client.read_resource(f"{root(name)}/{ENTRY}")
    assert contents[0].text.startswith("---")


async def test_every_tool_and_resource_has_a_row(built_ui, server):
    """An agent that misreads its own task skips a router but scans a table.

    The first agent to fly a real app read FLOWS.md and never opened
    INTERACTION.md, because it thought it had a "this menu won't open" problem
    rather than an interaction question — so it never learned `hover` existed.
    One row per tool on the entry point it always reads, and one per resource
    on RESOURCES.md, which the entry point links.

    Taken from the live server, with the two reading tools left out: they are
    how a client reads the resource rows, and the page says so in prose.
    """
    from kubed.selenium_flow.mcp import mirror

    entry = body(ENTRY_SKILL)
    tools = {t.name for t in await server.mcp.server.list_tools()} - set(mirror.MIRROR_TOOLS)
    missing = sorted(t for t in tools if not re.search(rf"^\| `{t}` \|", entry, re.MULTILINE))
    assert not missing, f"{ENTRY_SKILL}/SKILL.md's tool table has no row for {missing}"

    table = (SKILLS_DIR / ENTRY_SKILL / "references/RESOURCES.md").read_text()
    reads = {str(r.uri) for r in await server.mcp.server.list_resources()} | {
        t.uri_template for t in await server.mcp.server.list_resource_templates()
    }
    reads = {u for u in reads if not u.startswith(("ui://", "skill://"))}
    missing = sorted(u for u in reads if not re.search(rf"^\| `{re.escape(u)}` \|", table, re.MULTILINE))
    assert not missing, f"RESOURCES.md's table has no row for {missing}"


# ---- the examples have to work ---------------------------------------------


def documented_selectors() -> list[tuple[str, str]]:
    """Every `selector=` / `to=` literal the docs teach, with where it came from.

    The braces are matched by counting rather than by a regex, and that is the
    point: a pattern that stops at the first `}` cannot see a NESTED literal,
    which is precisely the mistake this exists to catch. The first version of
    this helper used one, skipped every broken example, and passed.
    """
    found = []
    pages = [*SKILLS_DIR.rglob("*.md"), REPO / "README.md", REPO / "AGENTS.md"]
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


CONTROL_PAGE = SKILLS_DIR / ENTRY_SKILL / "references/CONTROL.md"


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

    page = CONTROL_PAGE.read_text()
    said = getattr(control, sent).format(who="drk", at="14:02")
    assert phrase in said
    assert phrase in re.sub(r"\s+", " ", page)


@pytest.mark.parametrize("sent", ["HELD", "STILL"])
def test_the_control_sentences_the_skill_quotes_whole_are_the_servers(sent):
    """A fragment survives a clause dropped from the code's sentence; the
    example quoted whole does not."""
    from kubed.selenium_flow.live import control

    page = re.sub(r"\s+", " ", CONTROL_PAGE.read_text())
    assert getattr(control, sent).format(who="alex", at="14:02") in page
