"""Where to send a caller that needs the manual: the skill reference URIs a
failed flow run, a refused session and the workspace status point at."""

from __future__ import annotations

from ..names import SERVER_NAME

# The skills the plugin ships. The server's name is the namespace every one of
# them sits under: skill://<SERVER_NAME>/<skill>/SKILL.md.
SKILLS = (
    "driving-a-browser",
    "building-flows",
    "signing-in",
    "using-the-admin-ui",
    "deploying-the-server",
)


def root(skill: str) -> str:
    """The URI a skill's files sit under."""
    return f"skill://{SERVER_NAME}/{skill}"


# The entry point: the one skill the instructions and the mirror name.
ENTRY = f"{root('driving-a-browser')}/SKILL.md"

# The references a pointer may name, and the skill that ships each. A file name
# is unique across the skills, so the page alone names its skill. A `skill://`
# URI that cannot be read teaches an agent the manual is broken:
# `test_guidance.py` holds this map against the folders.
PAGES = {
    "CONFIGURATION.md": "deploying-the-server",
    "FLOWS.md": "building-flows",
    "INTERACTION.md": "driving-a-browser",
    "READING_PAGES.md": "driving-a-browser",
    "SECRETS.md": "signing-in",
    "WORKSPACES.md": "driving-a-browser",
    "SITE_DATA.md": "signing-in",
    "TROUBLESHOOTING.md": "driving-a-browser",
}


def pointer(page: str) -> str:
    """The resource URI for one reference, refusing a page that is not shipped.

    No anchor: everything after ``skill://selenium-flow/<skill>/`` is a file
    path. A section travels beside the URI, in its own field.
    """
    if page not in PAGES:
        raise ValueError(
            f"{page!r} is not a reference the skills ship — one of: "
            + ", ".join(PAGES)
        )
    return f"{root(PAGES[page])}/references/{page}"
