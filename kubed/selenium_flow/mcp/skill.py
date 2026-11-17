"""The embedded skill: how to use this server well, shipped inside it.

``plugin/skills/selenium-flow/`` is package data, so the guidance ships with the
version of the server it describes. FastMCP's ``SkillProvider`` serves it at
``skill://<name>/SKILL.md``, ``skill://<name>/_manifest`` and one URI per
supporting file, the URIs FastMCP's client helpers (``list_skills``,
``download_skill``) expect.

SkillProvider takes the skill's name from its folder, not the frontmatter:
renaming the folder renames the skill.

The Skills extension's ``skills/list`` and ``skills/get`` are in
``skill_extension``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastmcp.server.providers.skills.skill_provider import SkillProvider

from . import skill_extension

log = logging.getLogger(__name__)

PLUGIN_DIR = "plugin"
SKILLS_DIR = "skills"
SKILL_NAME = "selenium-flow"
ENTRY = "SKILL.md"


def skill_path() -> Path:
    """The skill directory, installed or in a source checkout.

    ``plugin/`` is at the repo root, and pyproject maps it beside this module in
    a wheel; a source checkout falls back to the root.
    """
    packaged = Path(__file__).parent / PLUGIN_DIR / SKILLS_DIR / SKILL_NAME
    if packaged.is_dir():
        return packaged
    # parents[3] because this module lives in mcp/: package, kubed, repo root.
    return Path(__file__).parents[3] / PLUGIN_DIR / SKILLS_DIR / SKILL_NAME


def load() -> SkillProvider | None:
    """The packaged skill as a provider, or None if it is not installed.

    Missing package data is logged, not fatal: the tools work without it.
    """
    path = skill_path()
    if not (path / ENTRY).is_file():
        log.warning("no packaged skill at %s", path)
        return None
    try:
        # "resources", so each supporting file is listed and linkable on its
        # own instead of found through the manifest.
        return SkillProvider(path, supporting_files="resources")
    except Exception as exc:  # noqa: BLE001 - bad package data must not stop the boot
        log.warning("packaged skill at %s could not be loaded: %s", path, exc)
        return None


def register(mcp, provider: SkillProvider) -> None:
    """Serve the skill's resources, and offer it through the Skills extension."""
    mcp.add_provider(provider)
    skill_extension.register(mcp, provider)
