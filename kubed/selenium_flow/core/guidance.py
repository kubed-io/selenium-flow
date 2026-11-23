"""Where to send a caller that needs the manual: the skill reference URIs a
failed flow run, a refused session and the workspace status point at."""

from __future__ import annotations

from ..names import SERVER_NAME

SKILL = "selenium-flow"
# The server's name is the namespace every skill it serves sits under.
ROOT = f"skill://{SERVER_NAME}/{SKILL}"
REFERENCES = f"{ROOT}/references"
ENTRY = f"{ROOT}/SKILL.md"

# The references the skill ships: a `skill://` URI that cannot be read teaches
# an agent the manual is broken. `test_guidance.py` holds this list against the
# directory.
PAGES = (
    "CONFIGURATION.md",
    "FLOWS.md",
    "INTERACTION.md",
    "READING_PAGES.md",
    "SECRETS.md",
    "WORKSPACES.md",
    "SITE_DATA.md",
    "TROUBLESHOOTING.md",
)


def pointer(page: str) -> str:
    """The resource URI for one reference, refusing a page that is not shipped.

    No anchor: everything after ``skill://selenium-flow/selenium-flow/`` is a file
    path. A section travels beside the URI, in its own field.
    """
    if page not in PAGES:
        raise ValueError(
            f"{page!r} is not a reference this skill ships — one of: "
            + ", ".join(PAGES)
        )
    return f"{REFERENCES}/{page}"
