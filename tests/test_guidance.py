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
    from kubed.selenium_flow.mcp.tools import INSTRUCTIONS

    assert "skill://selenium-flow/SKILL.md" in INSTRUCTIONS


def test_the_emitted_shapes_are_exactly_what_they_were():
    """The contract does not move: a run's hint is an object with `read`, the
    session status's guidance is a bare string. Sharing the construction must
    not change either, and this is the test that says so."""
    from kubed.selenium_flow.flows.run import hint_for

    hint = hint_for({"tool": "assert", "error": "", "n": 1}, "demo")
    assert hint["read"] == "skill://selenium-flow/references/FLOWS.md"
    assert hint["section"] == "say-what-must-be-true"
    assert hint["prompt"] == "repair_flow"
