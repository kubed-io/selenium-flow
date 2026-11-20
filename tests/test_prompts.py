"""Prompts, and the hint that points a person at one.

A skill is read by the model; a prompt is picked by a person. An agent cannot
invoke one — so when a run fails, the report names the prompt that repairs it and
the reference that explains the failure, and somebody can act on either.
"""

import pytest
import yaml
from fastmcp import Client
from fastmcp.exceptions import PromptError

from kubed.selenium_flow.flows.report import hint_for
from kubed.selenium_flow.mcp import prompts as prompts_module

pytestmark = pytest.mark.unit

SHIPPED = sorted(prompts_module.prompts_path().glob("*.md"))


# ---- the files that ship ----------------------------------------------------


def test_there_are_prompts_to_serve():
    """Guards the glob as much as the files: finding none would pass every
    check below vacuously."""
    assert {path.stem for path in SHIPPED} >= {"repair_flow", "build_flow"}


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_shipped_prompt_loads_strictly(path):
    """`load_prompts` logs and skips a broken file so one cannot take the server
    down. That makes this the only place a typo is ever caught."""
    prompt = prompts_module.load_prompt(path)
    assert prompt.name == path.stem
    assert prompt.description, "the picker shows this; an empty one is invisible"


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_command_has_a_hint_and_the_model_may_run_it(path):
    """PLG-3: a local install offers each file as a Claude Code command, with an
    `argument-hint` for the `/` menu, and nothing stops the model running it."""
    header = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
    assert isinstance(header.get("argument-hint"), str) and header["argument-hint"].strip()
    assert "disable-model-invocation" not in header


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
async def test_every_shipped_prompt_renders_with_its_defaults(path):
    """Every argument either has a default or is required, and a rendered prompt
    never contains an unfilled placeholder."""
    prompt = prompts_module.load_prompt(path)
    given = {a.name: f"<{a.name}>" for a in prompt.arguments or [] if a.required}
    rendered = await prompt.render(given)
    assert "{{" not in rendered
    for argument in prompt.arguments or []:
        assert argument.description, f"{argument.name} has nothing to show a person"


# ---- the loader's rules -----------------------------------------------------


def _write(tmp_path, body: str, name: str = "example.md"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        # Rendered blank it reads perfectly well and asks for the wrong thing.
        pytest.param(
            "---\ndescription: x\narguments:\n- name: flow\n---\nRepair {{ flow }} at {{ stp }}.\n",
            "stp",
            id="a-placeholder-with-no-argument-is-refused",
        ),
        pytest.param(
            "---\ndescription: x\narguments:\n- name: flow\n  required: true\n  default: y\n---\n{{ flow }}\n",
            "cannot have a default",
            id="a-required-argument-cannot-have-a-default",
        ),
    ],
)
def test_a_prompt_whose_arguments_do_not_fit_is_refused(tmp_path, text, refusal):
    path = _write(tmp_path, text)
    with pytest.raises(ValueError, match=refusal):
        prompts_module.load_prompt(path)


def test_a_malformed_arguments_block_is_refused_rather_than_fatal(tmp_path):
    """`arguments: 1` used to raise TypeError out of the loop, which
    `load_prompts` does not catch — so one bad file took every prompt with it
    (Copilot, #30)."""
    path = _write(tmp_path, "---\ndescription: x\narguments: 1\n---\nBody.\n", "bad.md")
    with pytest.raises(ValueError, match="must be a list"):
        prompts_module.load_prompt(path)
    _write(tmp_path, "---\ndescription: fine\n---\nBody.\n", "fine.md")
    assert [p.name for p in prompts_module.load_prompts(tmp_path)] == ["fine"]


def test_a_file_without_frontmatter_is_refused(tmp_path):
    with pytest.raises(ValueError, match="frontmatter"):
        prompts_module.load_prompt(_write(tmp_path, "Just a body.\n"))


def test_a_broken_file_is_skipped_rather_than_fatal(tmp_path, caplog):
    _write(tmp_path, "no frontmatter here\n", "broken.md")
    _write(tmp_path, "---\ndescription: fine\n---\nBody.\n", "fine.md")
    loaded = prompts_module.load_prompts(tmp_path)
    assert [p.name for p in loaded] == ["fine"]


async def test_rendering_fills_defaults_and_demands_the_required(tmp_path):
    path = _write(
        tmp_path,
        "---\ndescription: x\narguments:\n- name: flow\n  required: true\n"
        "- name: step\n  default: the run will say\n---\nFix {{ flow }} at {{ step }}.\n",
    )
    prompt = prompts_module.load_prompt(path)
    assert await prompt.render({"flow": "login"}) == "Fix login at the run will say.\n"
    # A picker sends "" for a field somebody left alone, which is not an answer.
    assert await prompt.render({"flow": "login", "step": ""}) == (
        "Fix login at the run will say.\n"
    )
    with pytest.raises(PromptError, match="flow"):
        await prompt.render({})


def test_the_format_matches_the_one_skills_mcp_serves():
    """The loader is copied rather than imported, so the thing that must not
    drift is the file format: frontmatter, `arguments`, `{{ placeholders }}`."""
    for path in SHIPPED:
        meta, body = prompts_module._split(path.read_text(encoding="utf-8"))
        assert isinstance(yaml.safe_load(yaml.safe_dump(meta)), dict)
        assert "description" in meta
        assert prompts_module.PLACEHOLDER.search(body), "a template with no slots"


def test_the_prompts_are_where_claude_code_reads_commands():
    path = prompts_module.prompts_path()
    assert path.name == "commands"
    assert {p.stem for p in path.glob("*.md")} == {"build_flow", "repair_flow"}


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
    """Everything after `skill://selenium-flow/` is the file path, so an anchor
    glued on the end names a file that does not exist. The section rides
    beside it (Copilot, #30)."""
    hint = hint_for({"tool": "assert", "error": "x"}, "login")
    assert "#" not in hint["read"]
    assert hint["read"] == "skill://selenium-flow/references/FLOWS.md"
    assert hint["section"] == "say-what-must-be-true"


def test_with_no_skill_served_there_is_nothing_to_read():
    """`--mcp-skill false` registers no skill resources, and a URI that cannot be
    loaded is worse than no URI (Copilot, #30)."""
    hint = hint_for({"tool": "assert", "error": "x"}, "login", skill_available=False)
    assert "read" not in hint
    assert "section" not in hint
    assert hint["prompt"] == "repair_flow", "the prompt is still worth naming"


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


def test_a_crlf_prompt_has_no_carriage_return_before_its_body():
    from kubed.selenium_flow.mcp.prompts import _split

    _, body = _split("---\r\ndescription: a\r\n---\r\nBody\r\n")
    assert body.startswith("Body")


def test_a_first_line_that_is_not_exactly_three_dashes_opens_no_header():
    from kubed.selenium_flow.mcp import frontmatter
    from kubed.selenium_flow.mcp.prompts import _split

    text = "---x\ndescription: a\n---\nBody\n"
    assert frontmatter.split(text) is None
    assert _split(text) is None
