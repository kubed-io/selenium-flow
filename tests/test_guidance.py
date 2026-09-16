"""Pointing a caller at the manual, in one shape and at the right moment.

Two emitters build a `skill://` URI: a failed flow run says which reference
explains that kind of failure, and the session status says which explains
sessions. They built the same URI two different ways — one from a `REFERENCES`
constant, one as a hardcoded literal — which is two places for the same string
to be wrong.

What each emitter *returns* is untouched: a run's `hint` is an object, the
status's `guidance` is a string, and both are published that way. Only the
construction is shared.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_every_page_guidance_can_name_is_one_the_skill_ships():
    """A `skill://` URI that cannot be read is worse than no URI at all: it
    teaches an agent that the manual is broken, and it costs a round trip to
    find that out. The pages are a closed set for exactly this reason."""
    from kubed.selenium_flow.mcp import guidance, skill

    references = skill.skill_path() / "references"
    for page in guidance.PAGES:
        assert (references / page).is_file(), f"{page} is named but not shipped"


def test_a_pointer_is_a_resource_uri_and_nothing_else():
    """Everything after `skill://selenium-flow/` is a file path, so an anchor
    glued on the end names a file that does not exist. The section travels
    beside the URI, never inside it."""
    from kubed.selenium_flow.mcp.guidance import pointer

    assert pointer("FLOWS.md") == "skill://selenium-flow/references/FLOWS.md"
    assert "#" not in pointer("FLOWS.md")


def test_a_page_it_does_not_ship_is_refused_at_the_source():
    """Caught where the typo is, not where the agent reads it."""
    import re

    from kubed.selenium_flow.mcp.guidance import pointer

    # Escaped: `match` is a regex, and an unescaped dot matches any character —
    # so the pattern would pass against a message that never said "INVENTED.md".
    with pytest.raises(ValueError, match=re.escape("INVENTED.md")):
        pointer("INVENTED.md")


def test_the_instructions_name_the_skill():
    """The one blurb every MCP client receives at connect time.

    Before this, only *failures* pointed at the skill — so an agent found the
    manual after it had already gone wrong. The skill is published correctly and
    a client listing resources can see it; nothing told it to read it.
    """
    from kubed.selenium_flow.mcp.tools import instructions

    assert "skill://selenium-flow/SKILL.md" in instructions(True)


def test_the_instructions_do_not_name_a_skill_that_is_not_served():
    """`--no-skill` is a supported mode, and it registers no skill:// resource.

    Telling that client to read the manual points it at a URI nothing answers,
    which is the same failure as naming a reference the skill does not ship —
    it teaches an agent the manual is broken (Copilot, #36).
    """
    from kubed.selenium_flow.mcp.tools import instructions

    assert "skill://" not in instructions(False)


def test_the_session_status_omits_guidance_when_no_skill_is_served():
    """Same rule, the other emitter."""
    from kubed.selenium_flow.core.actions import Actions
    from kubed.selenium_flow.core.browser import Grid
    from kubed.selenium_flow.session.sessions import SessionManager

    actions = Actions(Grid("http://grid.invalid:4444"))
    served = SessionManager(actions, skill_available=True)
    silent = SessionManager(actions, skill_available=False)
    assert "SESSIONS.md" in served.describe("someone")["guidance"]
    assert "guidance" not in silent.describe("someone")


def test_the_emitted_shapes_are_exactly_what_they_were():
    """The contract does not move: a run's hint is an object with `read`, the
    session status's guidance is a bare string. Sharing the construction must
    not change either, and this is the test that says so."""
    from kubed.selenium_flow.flows.run import hint_for

    hint = hint_for({"tool": "assert", "error": "", "n": 1}, "demo")
    assert hint["read"] == "skill://selenium-flow/references/FLOWS.md"
    assert hint["section"] == "say-what-must-be-true"
    assert hint["prompt"] == "repair_flow"


def test_every_section_a_hint_names_is_a_heading_on_its_page():
    """A pointer to a section that is not there costs the reader a scroll through
    the page for nothing, and nothing else checks the anchors."""
    import re

    from kubed.selenium_flow.flows.run import hint_for
    from kubed.selenium_flow.mcp import skill

    failures = [
        {"tool": "assert", "error": "", "n": 1},
        {"tool": "interact", "error": "no element matched '//x'", "n": 1},
        {"tool": "navigate", "error": "the run passed its 120s budget before this step", "n": 2},
        {"tool": "navigate", "error": "boom", "n": 1},
    ]
    references = skill.skill_path() / "references"
    for step in failures:
        hint = hint_for(step, "demo")
        if "section" not in hint:
            continue
        page = hint["read"].rsplit("/", 1)[-1]
        slugs = {
            re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
            for line in (references / page).read_text().splitlines()
            if line.startswith("#")
        }
        assert hint["section"] in slugs, f"{page} has no heading for {hint['section']}"


@pytest.mark.parametrize("tool", ["navigate", "assert"])
def test_a_run_out_of_time_points_at_the_budget_whatever_step_was_next(tool):
    """An `assert` that never ran did not fail, so it must not be pointed at the
    advice for writing assertions (Copilot, #38). Through a real run."""
    from kubed.selenium_flow.flows import run as flowrun

    class Clock:
        now = 0.0

        def monotonic(self):
            Clock.now += 1.0
            return Clock.now

    class Navigates:
        def navigate(self, session_id, **kwargs):
            return {"url": kwargs.get("url")}

    steps = [
        {"tool": "navigate", "args": {"url": "a"}},
        {"tool": "navigate", "args": {"url": "b"}},
        {"tool": tool, "args": {"url": "c"} if tool == "navigate" else {"script": "return true"}},
    ]
    original = flowrun.time
    flowrun.time = Clock()
    try:
        report = flowrun.run(Navigates(), {"name": "f", "timeout": 3, "steps": steps}, "b")
    finally:
        flowrun.time = original
    assert report["steps"][-1]["tool"] == tool
    assert report["hint"]["section"] == "how-long-a-run-may-take"
