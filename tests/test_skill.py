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
PLUGIN = skill_module.plugin_path()
# A skill's own file, by its path from the root, in backticks.
OWN = re.compile(r"`(references/[A-Za-z0-9_.-]+\.md)`")
# A backticked name; read only for the names in SKILLS.
NAMED = re.compile(r"`([a-z][a-z-]*)`")
# Any file name a text gives, with whatever path is in front of it.
FILE_NAME = re.compile(r"(?<![\w/.])((?:[\w.:-]+/)*[A-Z][A-Z0-9_]*\.md)")
# A file in another skill, as the explorer and the prompts name it, with an optional section.
IN_SKILL = re.compile(r"(?:\*([^*]+)\* in )?`([A-Z][A-Z0-9_]*\.md)` in the `([a-z-]+)` skill")
# The three cross-skill forms (design §1), on whitespace-collapsed text.
CROSS = re.compile(r"[Tt]he `([a-z-]+)` skill(?:'s)?(?:, row \"([^\"]+)\"|, \*([^*]+)\*)?")


def root(name: str) -> str:
    return f"{NAMESPACE}/{name}"


def frontmatter(name: str) -> dict:
    return yaml.safe_load((SKILLS_DIR / name / ENTRY).read_text().split("---", 2)[1])


def body(name: str) -> str:
    return (SKILLS_DIR / name / ENTRY).read_text()


def unfenced(text: str) -> str:
    """The text without its fenced code blocks, fence lines included."""
    kept, fenced = [], False
    for line in text.splitlines(keepends=True):
        if line.strip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            kept.append(line)
    assert not fenced, "an unclosed code fence"
    return "".join(kept)


def skill_files() -> list[tuple[str, pathlib.Path]]:
    """(skill, path) for every markdown file under each skill."""
    return [(name, path) for name in SKILLS for path in sorted((SKILLS_DIR / name).rglob("*.md"))]


def rows(name: str) -> list[str]:
    """The first cell of each body row of the tables in a skill's SKILL.md."""
    lines = body(name).splitlines()
    cells = []
    for at, line in enumerate(lines):
        if not line.startswith("|") or re.fullmatch(r"\|[\s|:-]+\|?", line):
            continue
        if at + 1 < len(lines) and re.fullmatch(r"\|[\s|:-]+\|?", lines[at + 1]):
            continue  # a header row
        cells.append(" ".join(line.strip().strip("|").split("|")[0].split()))
    return cells


def ruled_files() -> list[pathlib.Path]:
    """The files the address rule reads: the skill files, the agents and the prompts."""
    return [
        *(path for _, path in skill_files()),
        *sorted((PLUGIN / "agents").glob("*.md")),
        *sorted((PLUGIN / "commands").glob("*.md")),
    ]


def whole_phrase(row: str, cell: str) -> bool:
    """A row quoted from a cell is a whole phrase of it, not a fragment."""
    return re.search(rf"(?:^|[,;] ){re.escape(row)}(?=$|[,;]| \()", cell) is not None


def names_italic(text: str, section: str) -> bool:
    """A section is named in italics, not in bold."""
    return re.search(rf"(?<!\*)\*{re.escape(section)}\*(?!\*)", text) is not None


def has_heading(text: str, section: str) -> bool:
    """A section is a whole `## ` heading, not a deeper one that ends the same way."""
    return re.search(rf"^## {re.escape(section)}$", text, re.M) is not None


def bare_files(text: str) -> list[str]:
    """The file names a text gives with no cross-skill form around them."""
    named = [match.span() for match in IN_SKILL.finditer(text)]
    return [
        match.group(1)
        for match in FILE_NAME.finditer(text)
        if not any(start <= match.start() and match.end() <= end for start, end in named)
    ]


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

    assert set(guidance.PAGES.values()) <= set(SKILLS)
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
    """Every (skill, path) a SKILL.md points at: its own files by their path,
    and the other skills by name."""
    text = body(name)
    return {(name, path) for path in OWN.findall(text)} | {
        (named, ENTRY) for named in NAMED.findall(text) if named in SKILLS
    }


@pytest.mark.parametrize("name", SKILLS)
def test_the_index_points_at_references_by_their_path(name):
    """The agent knows the exact path for whatever it needs right away."""
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
    rules = text.split("## The rules", 1)[1].split("## ", 1)[0]
    for rule in (
        "1. Ask the person first, and show them the change",
        "2. Never `save_site_data` on the admin page.",
        "3. Never `execute_script` there,",
        "4. Never take control in Live.",
        "5. Take a destructive action (End, End all, Delete, Clear, Forget, Move) only when the person has named its target, and click its confirm yourself.",
        "6. Stay out of other workspaces' Site data and Network detail.",
    ):
        assert rule in rules, rule


def test_an_unclosed_fence_is_refused():
    assert unfenced("a\n```json\n{}\n```\nb\n") == "a\nb\n"
    with pytest.raises(AssertionError):
        unfenced("a\n```\nb\n")


def test_a_quoted_row_is_whole_phrases_of_a_cell():
    cell = "A workspace's config (`workspace.yaml`, `flows.global`, `config_url`), an invalid config"
    for quote in ("an invalid config", "A workspace's config", cell, "`flows.global`"):
        assert whole_phrase(quote, cell), quote
    for fragment in ("invalid config", "A workspace", "an invalid con", "config (`workspace.yaml`"):
        assert not whole_phrase(fragment, cell), fragment


def test_a_section_is_an_italic_not_a_bold():
    assert names_italic("| An OIDC issuer | *OIDC* |", "OIDC")
    assert not names_italic("**OIDC**", "OIDC")


def test_a_section_is_a_whole_heading_of_its_level():
    text = "## When a flow fails\n### Tags\n## Say what must be true here\n"
    assert has_heading(text, "When a flow fails")
    assert not has_heading(text, "Tags")
    assert not has_heading(text, "Say what must be true")


def test_a_file_named_without_its_skill_is_bare():
    assert bare_files("Read `SECRETS.md` first.") == ["SECRETS.md"]
    assert bare_files("Read SECRETS.md first.") == ["SECRETS.md"]
    assert bare_files("Read `references/SECRETS.md` first.") == ["references/SECRETS.md"]
    assert bare_files("Read `SECRETS.md` in the `signing-in` skill.") == []
    assert bare_files("*When a flow fails* in `FLOWS.md` in the `building-flows` skill") == []


@pytest.mark.parametrize("path", ruled_files(), ids=lambda p: p.relative_to(PLUGIN).as_posix())
def test_no_skill_address_outside_fenced_code(path):
    """Where a skill lives is the client's to know (PLG-76)."""
    assert "skill://" not in unfenced(path.read_text())


@pytest.mark.parametrize("name, path", skill_files(), ids=lambda v: getattr(v, "name", v))
def test_a_skill_names_its_own_files_from_its_root(name, path):
    """SEP-2640 resolves a path against the skill's root, from any file."""
    text = unfenced(path.read_text())
    own = set(OWN.findall(text))
    for named in FILE_NAME.findall(text):
        assert named == "SKILL.md" or named in own, f"{path.name} names {named}"
    for rel in own:
        assert (SKILLS_DIR / name / rel).is_file(), f"{path.name} names missing {rel}"


def test_every_cross_reference_names_what_is_there():
    found = 0
    for path in ruled_files():
        text = " ".join(unfenced(path.read_text()).split())
        for skill, row, section in CROSS.findall(text):
            found += 1
            assert skill in SKILLS, f"{path.name} names no skill `{skill}`"
            if row:
                assert any(whole_phrase(row, cell) for cell in rows(skill)), f"{path.name}: {skill} has no row {row!r}"
            if section:
                assert names_italic(body(skill), section), f"{path.name}: {skill} names no *{section}*"
    assert found >= 25


@pytest.mark.parametrize("path", sorted((PLUGIN / "agents").glob("*.md")), ids=lambda p: p.name)
def test_every_skill_an_agent_names_is_in_its_skills(path):
    """`_meta` skills are where a subagent finds an address (PLG-40, PLG-76)."""
    _, front, text = path.read_text().split("---", 2)
    listed = yaml.safe_load(front)["skills"]
    named = {n for n in NAMED.findall(text) if n in SKILLS}
    assert named and named <= set(listed), sorted(named - set(listed))
    assert set(listed) <= set(SKILLS)


def test_a_file_named_in_another_skill_is_there():
    found = 0
    for path in [*(PLUGIN / "agents").glob("*.md"), *(PLUGIN / "commands").glob("*.md")]:
        text = " ".join(unfenced(path.read_text()).split())
        assert not bare_files(text), f"{path.name} names {bare_files(text)} without its skill"
        for section, page, skill in IN_SKILL.findall(text):
            found += 1
            file = SKILLS_DIR / skill / "references" / page
            assert file.is_file(), f"{path.name}: no {page} in {skill}"
            if section:
                assert has_heading(file.read_text(), section), f"{path.name}: no *{section}* in {page}"
    assert found >= 5


# ---- every address a reader is given reads ---------------------------------


def addresses() -> list[tuple[str, str]]:
    """Every `skill://selenium-flow/…` address the server computes (the entry
    point and every page a pointer can name), every one named literally in
    the plugin, the docs an agent reads and the golden files, and each skill's
    own path (`references/…`) resolved against its root, with where it was
    found."""
    from kubed.selenium_flow.core import guidance

    computed = {("guidance.ENTRY", guidance.ENTRY)} | {
        (f"guidance.pointer({page!r})", guidance.pointer(page)) for page in guidance.PAGES
    }
    files = [
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
    for name, path in skill_files():
        for rel in OWN.findall(unfenced(path.read_text())):
            found.add((path.relative_to(REPO).as_posix(), f"{root(name)}/{rel}"))
    return sorted(found | computed)


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

    Taken from the live server, with kubed-mcp's two reading tools left out: they
    are how a client reads the resource rows, and the page says so in prose.
    """
    from .conftest import RESOURCE_TOOLS

    entry = body(ENTRY_SKILL)
    tools = {t.name for t in await server.mcp.server.list_tools()} - RESOURCE_TOOLS
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


# ---- sessions are a list (#248) --------------------------------------------

REFERENCES = SKILLS_DIR / ENTRY_SKILL / "references"


def collapsed(text: str) -> str:
    return " ".join(text.split())


def test_rule_one_says_a_join_ends_nothing():
    """Calling `open_session` again joins; only a change or `fresh=true` ends the browser."""
    rules = body(ENTRY_SKILL).split("## Rules", 1)[1]
    rule = collapsed(rules.split("1. ", 1)[1].split("\n2. ", 1)[0])
    assert "Calling it again joins your live browser and ends nothing" in rule
    assert "`fresh=true` is also how you get a clean browser or your saved site data back" in rule


def test_the_held_refusal_the_skill_quotes_is_the_servers():
    from kubed.selenium_flow import faults

    page = (REFERENCES / "TROUBLESHOOTING.md").read_text()
    assert has_heading(page, f'"{faults.HELD}"')


def test_control_says_a_join_is_not_refused():
    page = collapsed(CONTROL_PAGE.read_text())
    assert "An `open_session` that joins your live browser is not refused" in page


def test_workspaces_lists_the_context_keys_b_built():
    from kubed.selenium_flow.core import capabilities

    page = (REFERENCES / "WORKSPACES.md").read_text()
    section = page.split("## What `workspace://current` says", 1)[1].split("\n## ", 1)[0]
    for key in ("session", "main", "sessions", "yours"):
        assert f"`{key}`" in section, key
    assert has_heading(page, "A result says `main_moved`")
    # The section's first fenced json block is the example.
    example = json.loads(section.split("```json", 1)[1].split("```", 1)[0])
    assert {"session", "main", "sessions"} <= example.keys()
    for entry in example["sessions"]:
        assert set(entry) == set(capabilities.SESSION_ENTRY["required"])


def test_the_admin_skill_opens_admin_ui():
    page = (SKILLS_DIR / "using-the-admin-ui/references/AGENT_AS_ADMIN.md").read_text()
    assert 'session="admin-ui"' in page
    assert 'session="admin"' not in page


def test_site_data_says_fresh_on_a_live_session():
    page = collapsed((SKILLS_DIR / "signing-in/references/SITE_DATA.md").read_text())
    assert "a new session starts with none" in page
    assert "`fresh=true` puts saved site data back" in page
