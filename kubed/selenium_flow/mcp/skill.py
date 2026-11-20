"""The embedded skill: how to use this server well, shipped inside it.

``plugin/skills/selenium-flow/`` is package data, so the guidance ships with the
version of the server it describes. FastMCP's ``SkillProvider`` serves it at
``skill://<name>/SKILL.md``, the one row ``resources/list`` shows. The
``_manifest`` and every supporting file are not listed but read by exact URI,
which is what FastMCP's client helpers (``list_skills``, ``download_skill``)
use.

SkillProvider takes the skill's name from its folder, not the frontmatter:
renaming the folder renames the skill.

The Skills extension's ``skills/list`` and ``skills/get`` are in
``skill_extension``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from fastmcp.resources import Resource
from fastmcp.server.providers.skills.skill_provider import SkillProvider

from . import skill_extension

log = logging.getLogger(__name__)

PLUGIN_DIR = "plugin"
SKILLS_DIR = "skills"
SKILL_NAME = "selenium-flow"
ENTRY = "SKILL.md"


def plugin_folder(module: str, *parts: str) -> Path:
    """``plugin/<parts>`` beside ``module``, else at the root of a source checkout.

    ``plugin/`` is at the repo root, and pyproject maps it beside the modules of
    ``mcp/`` in a wheel. ``module`` is the caller's ``__file__``.
    """
    here = Path(module).parent
    packaged = here.joinpath(PLUGIN_DIR, *parts)
    if packaged.is_dir():
        return packaged
    # Up from mcp/: the package, kubed, the repo root.
    return here.parents[2].joinpath(PLUGIN_DIR, *parts)


def skill_path() -> Path:
    """The skill directory, installed or in a source checkout."""
    return plugin_folder(__file__, SKILLS_DIR, SKILL_NAME)


class EntryOnlySkillProvider(SkillProvider):
    """A SkillProvider whose listing names the main file and nothing else.

    Reads are untouched: ``_get_resource`` still answers the manifest and every
    supporting file by exact URI.
    """

    async def _list_resources(self) -> list[Resource]:
        entry = f"{self.skill_info.uri_prefix}{quote(self._main_file_name, safe='/')}"
        return [r for r in await super()._list_resources() if str(r.uri) == entry]


def load() -> EntryOnlySkillProvider | None:
    """The packaged skill as a provider, or None if it is not installed.

    Missing package data is logged, not fatal: the tools work without it.
    """
    path = skill_path()
    if not (path / ENTRY).is_file():
        log.warning("no packaged skill at %s", path)
        return None
    try:
        # "resources" reads every file by exact URI; the subclass trims the
        # listing to SKILL.md (PLG-14, ruling 26). Not "template": it serves
        # files as application/octet-stream and lists a template row.
        return EntryOnlySkillProvider(path, supporting_files="resources")
    except Exception as exc:  # noqa: BLE001 - bad package data must not stop the boot
        log.warning("packaged skill at %s could not be loaded: %s", path, exc)
        return None


def register(mcp, provider: SkillProvider) -> None:
    """Serve the skill's resources, and offer it through the Skills extension."""
    mcp.add_provider(provider)
    skill_extension.register(mcp, provider)
