"""Where to send a caller that needs the manual.

Two emitters point at the skill: a failed flow run says which reference explains
that kind of failure, and the session status says which explains sessions. They
built the same URI two different ways — one from a constant, one as a hardcoded
literal — and a third place was one copy-paste away.

**Not everything that helps a caller is a pointer.** The dialog hint in
``core.browser`` tells you which tool to call, not which page to read, and it
stays prose. Folding it in here would be unification for its own sake.

What each emitter returns is untouched: a run's ``hint`` is an object, the
status's ``guidance`` is a string, and both are published that way. Only the
construction is shared, which is the part that can silently be wrong.
"""

from __future__ import annotations

SKILL = "selenium-flow"
REFERENCES = f"skill://{SKILL}/references"
ENTRY = f"skill://{SKILL}/SKILL.md"

# The references the skill ships. A closed set on purpose: a `skill://` URI that
# cannot be read is worse than no URI at all — it teaches an agent the manual is
# broken, and it costs a round trip to discover that. `test_guidance.py` holds
# this list against the directory, so a renamed page fails here rather than in
# somebody's session.
PAGES = (
    "CONFIGURATION.md",
    "FLOWS.md",
    "INTERACTION.md",
    "READING_PAGES.md",
    "SECRETS.md",
    "SESSIONS.md",
    "TROUBLESHOOTING.md",
)


def pointer(page: str) -> str:
    """The resource URI for one reference, refusing a page that is not shipped.

    A URI and nothing else: everything after ``skill://selenium-flow/`` is a
    file path, so an anchor glued on the end names a file that does not exist.
    A section travels *beside* the URI, in its own field.
    """
    if page not in PAGES:
        raise ValueError(
            f"{page!r} is not a reference this skill ships — one of: "
            + ", ".join(PAGES)
        )
    return f"{REFERENCES}/{page}"
