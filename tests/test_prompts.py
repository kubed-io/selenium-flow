"""Prompts, and the hint that points a person at one.

A skill is read by the model; a prompt is picked by a person. An agent cannot
invoke one — so when a run fails, the report names the prompt that repairs it and
the reference that explains the failure, and somebody can act on either.
"""

import pytest
import yaml
from fastmcp import Client
from fastmcp.exceptions import ToolError
from kubed.mcp.prompts import PLACEHOLDER, load_prompt, split_frontmatter
from kubed.mcp.protocol import MCPError

from kubed.selenium_flow.flows.report import hint_for
from kubed.selenium_flow.mcp import skill

from .fakes import plugin_server

pytestmark = pytest.mark.unit

COMMANDS = skill.plugin_path() / "commands"
SHIPPED = sorted(COMMANDS.glob("*.md"))


# ---- the files that ship ----------------------------------------------------


def test_there_are_prompts_to_serve():
    """Guards the glob as much as the files: finding none would pass every
    check below vacuously."""
    assert {path.stem for path in SHIPPED} >= {"repair_flow", "build_flow"}


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_shipped_prompt_loads_strictly(path):
    """`add_plugin` logs and skips a broken file so one cannot take the server
    down. That makes this the only place a typo is ever caught."""
    prompt = load_prompt(path)
    assert prompt.name == path.stem
    assert prompt.description, "the picker shows this; an empty one is invisible"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_command_has_a_hint_and_the_model_may_run_it(path):
    """PLG-3: a local install offers each file as a Claude Code command, with an
    `argument-hint` for the `/` menu, and nothing stops the model running it."""
    header = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
    assert isinstance(header.get("argument-hint"), str) and header["argument-hint"].strip()
    assert "disable-model-invocation" not in header


async def test_every_shipped_prompt_renders_with_its_defaults(server):
    """Every argument either has a default or is required, and a rendered prompt
    never contains an unfilled placeholder: through the server, as served."""
    async with Client(server.mcp.server) as client:
        listed = await client.list_prompts()
        assert {p.name for p in listed} == {path.stem for path in SHIPPED}
        for prompt in listed:
            for argument in prompt.arguments or []:
                assert argument.description, f"{argument.name} has nothing to show a person"
            given = {a.name: f"<{a.name}>" for a in prompt.arguments or [] if a.required}
            result = await client.get_prompt(prompt.name, given)
            assert "{{" not in result.messages[0].content.text


def test_the_format_matches_the_one_kubed_mcp_serves():
    """The thing that must not drift is the file format: frontmatter,
    `arguments`, `{{ placeholders }}`."""
    for path in SHIPPED:
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
        assert isinstance(yaml.safe_load(yaml.safe_dump(meta)), dict)
        assert "description" in meta
        assert PLACEHOLDER.search(body), "a template with no slots"


def test_the_prompts_are_where_claude_code_reads_commands():
    assert {p.stem for p in COMMANDS.glob("*.md")} == {"build_flow", "repair_flow"}


# ---- the loader's rules, through add_plugin ---------------------------------
# kubed-mcp reads the commands. These are the rules its own tests leave out
# (PLUG-23, PLUG-25, PLUG-26), on a plugin of the case's own.


async def _served(tmp_path, files: dict) -> list[str]:
    mcp = plugin_server(tmp_path / "p", {"commands/fine.md": "---\ndescription: fine\n---\nBody.\n", **files})
    async with Client(mcp.server) as client:
        return sorted(p.name for p in await client.list_prompts())


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "---\ndescription: x\narguments:\n- name: flow\n  required: true\n  default: y\n---\n{{ flow }}\n",
            id="a-required-argument-cannot-have-a-default",
        ),
        # `arguments: 1` once raised out of the loop and took every prompt with it
        # (Copilot, #30).
        pytest.param("---\ndescription: x\narguments: 1\n---\nBody.\n", id="arguments-must-be-a-list"),
        pytest.param("Just a body.\n", id="no-frontmatter"),
        pytest.param("---x\ndescription: a\n---\nBody\n", id="a-first-line-not-exactly-three-dashes"),
    ],
)
async def test_a_broken_command_is_skipped_rather_than_fatal(tmp_path, caplog, text):
    assert await _served(tmp_path, {"commands/broken.md": text}) == ["fine"]
    assert "broken.md is skipped" in caplog.text


async def test_rendering_fills_defaults_and_demands_the_required(tmp_path):
    mcp = plugin_server(tmp_path / "p", {
        "commands/fix.md": "---\ndescription: x\narguments:\n- name: flow\n  required: true\n"
        "- name: step\n  default: the run will say\n---\nFix {{ flow }} at {{ step }}.\n",
    })
    async with Client(mcp.server) as client:
        text = (await client.get_prompt("fix", {"flow": "login"})).messages[0].content.text
        assert text == "Fix login at the run will say.\n"
        # A picker sends "" for a field somebody left alone, which is not an answer.
        blank = await client.get_prompt("fix", {"flow": "login", "step": ""})
        assert blank.messages[0].content.text == "Fix login at the run will say.\n"
        with pytest.raises((MCPError, ToolError), match="flow"):
            await client.get_prompt("fix", {"flow": ""})


async def test_a_crlf_prompt_has_no_carriage_return_before_its_body(tmp_path):
    mcp = plugin_server(tmp_path / "p", {"commands/crlf.md": b"---\r\ndescription: a\r\n---\r\nBody\r\n"})
    async with Client(mcp.server) as client:
        text = (await client.get_prompt("crlf", {})).messages[0].content.text
    assert text.startswith("Body")


# ---- served ------------------------------------------------------------------


async def test_the_server_publishes_them(server):
    async with Client(server.mcp.server) as client:
        listed = {prompt.name for prompt in await client.list_prompts()}
    assert {"repair_flow", "build_flow"} <= listed


async def test_a_client_can_render_one(server):
    async with Client(server.mcp.server) as client:
        result = await client.get_prompt("repair_flow", {"flow": "sign-in"})
    rendered = result.messages[0].content.text
    assert "sign-in" in rendered
    assert "{{" not in rendered


# ---- the hint on a failed run ------------------------------------------------


def test_a_failed_step_points_at_the_prompt_that_repairs_it():
    hint = hint_for({"n": 3, "tool": "interact", "error": "no clickable element matched"}, "login")
    assert hint["prompt"] == "repair_flow"
    assert hint["arguments"] == {"flow": "login", "step": "3"}


@pytest.mark.parametrize(
    "step,page,section",
    [
        ({"tool": "assert", "error": "Already signed in."}, "FLOWS.md", "say-what-must-be-true"),
        (
            {"tool": "interact", "error": "no clickable element matched 'a.x' within 30s"},
            "FLOWS.md",
            "when-a-flow-fails",
        ),
        ({"tool": "navigate", "error": "the Grid is full"}, "TROUBLESHOOTING.md", None),
    ],
)
def test_where_it_sends_you_depends_on_what_failed(step, page, section):
    hint = hint_for(step, "login")
    assert hint["read"].endswith(page)
    assert hint.get("section") == section


def test_the_uri_is_a_resource_uri_and_nothing_else():
    """Everything after `skill://selenium-flow/selenium-flow/` is the file path, so an anchor
    glued on the end names a file that does not exist. The section rides
    beside it (Copilot, #30)."""
    hint = hint_for({"tool": "assert", "error": "x"}, "login")
    assert "#" not in hint["read"]
    assert hint["read"] == "skill://selenium-flow/selenium-flow/references/FLOWS.md"
    assert hint["section"] == "say-what-must-be-true"


def test_every_reference_it_can_name_exists():
    """A hint that points at a page nobody wrote is worse than no hint."""
    from kubed.selenium_flow.mcp import skill

    references = skill.skill_path() / "references"
    for step in (
        {"tool": "assert", "error": "x"},
        {"tool": "interact", "error": "no clickable element matched"},
        {"tool": "navigate", "error": "something else"},
    ):
        page = hint_for(step, "f")["read"].rsplit("/", 1)[-1].split("#")[0]
        assert (references / page).is_file(), page


def test_an_anchor_it_names_is_a_heading_that_exists():
    """A `#section` that no heading matches drops a reader at the top of the
    page, which is the same as not saying anything."""
    from kubed.selenium_flow.mcp import skill

    references = skill.skill_path() / "references"
    for step in (
        {"tool": "assert", "error": "x"},
        {"tool": "interact", "error": "no clickable element matched"},
    ):
        hint = hint_for(step, "f")
        page = hint["read"].rsplit("/", 1)[-1]
        headings = {
            line.lstrip("# ").strip().lower().replace(" ", "-").replace(",", "")
            for line in (references / page).read_text().splitlines()
            if line.startswith("#")
        }
        assert hint["section"] in headings, f"{page} has no heading {hint['section']}"


def test_a_real_failed_run_carries_the_hint():
    """Through `run()`, not the helper: the helper staying right while nothing
    calls it is the failure this kind of test exists to catch."""
    from kubed.selenium_flow.flows.run import run

    class _Broken:
        def navigate(self, session_id, **kwargs):
            return {"url": kwargs.get("url"), "title": "Login"}

        def interact(self, session_id, **kwargs):
            raise RuntimeError("no clickable element matched 'button.go' within 30s")

        def page(self, session_id):
            return {"url": "https://example.test/", "title": "t"}

    document = {
        "steps": [
            {"tool": "navigate", "args": {"url": "https://example.test/"}},
            {"tool": "interact", "args": {"action": "click", "selector": {"css": "button.go"}}},
        ]
    }
    report = run(_Broken(), document, "b", params={})

    assert report["status"] == "failed"
    assert report["hint"]["prompt"] == "repair_flow"
    assert report["hint"]["arguments"] == {"flow": "flow", "step": "2"}
    assert report["hint"]["read"].endswith("FLOWS.md")
    assert report["hint"]["section"] == "when-a-flow-fails"


@pytest.mark.parametrize(
    "document,because",
    [
        (
            {
                "parameters": {"properties": {"token": {"writeOnly": True}}},
                "steps": [{"tool": "navigate", "args": {"url": "https://x/"}}],
            },
            "writeOnly",
        ),
        (
            {"steps": [{"tool": "navigate", "params": {"url": "https://x/"}}]},
            "older format",
        ),
    ],
)
def test_a_run_refused_before_step_one_still_says_where_to_look(document, because):
    """Two of the three ways a run can fail returned before the hint existed
    (Copilot, #30) — a refused document is exactly when somebody needs it."""
    from kubed.selenium_flow.flows.run import run

    report = run(object(), document, "b")
    assert report["status"] == "failed"
    assert because in report["steps"][0]["error"]
    assert report["hint"]["prompt"] == "repair_flow"


def test_a_run_that_worked_carries_no_hint():
    """Nothing to repair, nothing to say."""
    from kubed.selenium_flow.flows.run import run

    class _Fine:
        def navigate(self, session_id, **kwargs):
            return {"url": kwargs.get("url"), "title": "Login"}

    document = {"steps": [{"tool": "navigate", "args": {"url": "https://example.test/"}}]}
    assert "hint" not in run(_Fine(), document, "b")
