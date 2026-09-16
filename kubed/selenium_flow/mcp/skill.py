"""The embedded skill: how to use this server well, shipped inside it.

An agent that can call the tools still has to decide *when* to screenshot rather
than extract, whether it owns its session, and what a timeout on a good XPath
actually means. That knowledge normally lives in whatever prompt the operator
wrote, which means every deployment reinvents it and none of it travels with the
version of the server it describes.

So it ships in the wheel. ``skills/selenium-flow/`` is package data, installed
with the code and served straight from the installed package, which makes the
guidance and the tools it describes impossible to version apart.

Serving it is FastMCP's job, not ours: ``SkillProvider`` publishes the skill at
the URIs the ecosystem already agrees on — ``skill://<name>/SKILL.md`` for the
instructions, ``skill://<name>/_manifest`` for the file listing, and one URI per
supporting file. Conform and FastMCP's own client helpers (``list_skills``,
``download_skill``, ``sync_skills``) work against this server for free; invent a
prettier URI and the skill is invisible to every one of them.

**The directory name is the skill name.** SkillProvider takes it from the folder,
not the frontmatter, so ``skills/selenium-flow/`` is what makes the URI
``skill://selenium-flow/SKILL.md``. Renaming that directory renames the skill.

A client that cannot read resources reads the same URIs through
``mirror.read_resource``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastmcp.server.providers.skills.skill_provider import SkillProvider

log = logging.getLogger(__name__)

SKILLS_DIR = "skills"
SKILL_NAME = "selenium-flow"
ENTRY = "SKILL.md"
MANIFEST = "_manifest"

RESOURCE_URI = f"skill://{SKILL_NAME}/{ENTRY}"
MANIFEST_URI = f"skill://{SKILL_NAME}/{MANIFEST}"


def skill_path() -> Path:
    """The skill directory, installed or in a source checkout.

    ``skills/`` lives at the repo root, where it reads as documentation rather
    than as buried package data. pyproject maps it into the package at build
    time, so an installed wheel finds it beside this module; a source checkout
    has no such copy, hence the fallback to the root.
    """
    packaged = Path(__file__).parent / SKILLS_DIR / SKILL_NAME
    if packaged.is_dir():
        return packaged
    # parents[3] because this module lives in mcp/: package, kubed, repo root.
    return Path(__file__).parents[3] / SKILLS_DIR / SKILL_NAME


def enabled(env: dict | None = None) -> bool:
    """Whether to serve the skill. On unless explicitly turned off."""
    env = os.environ if env is None else env
    return str(env.get("SKILL_ENABLED", "true")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def load() -> SkillProvider | None:
    """The packaged skill as a provider, or None if it is not installed.

    Missing package data is not fatal: the tools work without the guidance, so a
    stripped install should start and say so rather than crash.
    """
    path = skill_path()
    if not (path / ENTRY).is_file():
        log.warning("no packaged skill at %s", path)
        return None
    try:
        # "resources", not the default "template": with a handful of files
        # the full enumeration is cheap, and it makes each reference an
        # individually listed, linkable resource rather than something a client
        # can only find by reading the manifest first.
        return SkillProvider(path, supporting_files="resources")
    except Exception as exc:  # noqa: BLE001 - bad package data must not stop the boot
        log.warning("packaged skill at %s could not be loaded: %s", path, exc)
        return None


def read(provider: SkillProvider, file: str) -> str | None:
    """One of the skill's files, by the path the manifest names.

    Traversal is refused by name rather than by resolved path: the manifest is
    the list of what exists, so anything not on it is simply not a file here.
    """
    if file == MANIFEST:
        return manifest_json(provider)
    known = {f.path for f in provider.skill_info.files}
    if file not in known:
        return None
    return (skill_path() / file).read_text(encoding="utf-8")


def manifest_json(provider: SkillProvider) -> str:
    """The file listing, in the shape the resource serves it."""
    info = provider.skill_info
    return json.dumps(
        {
            "skill": info.name,
            "files": [
                {"path": f.path, "size": f.size, "hash": f.hash} for f in info.files
            ],
        },
        indent=2,
    )


def register(mcp, provider: SkillProvider) -> None:
    """Serve the skill's resources."""
    mcp.add_provider(provider)
