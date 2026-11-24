"""Where the plugin this server serves sits: its skill, its prompts and its agents.

``plugin/`` is package data, so the guidance ships with the version of the server
it describes. ``mcp.add_plugin(plugin_path())`` serves all of it (kubed-mcp's
plugin layer): the skill at ``skill://selenium-flow/selenium-flow/SKILL.md``, the
commands as prompts, the agents through the agents extension.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_DIR = "plugin"
SKILLS_DIR = "skills"
SKILL_NAME = "selenium-flow"


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


def plugin_path() -> Path:
    """The plugin folder, installed or in a source checkout."""
    return plugin_folder(__file__)


def skill_path() -> Path:
    """The skill directory, installed or in a source checkout."""
    return plugin_folder(__file__, SKILLS_DIR, SKILL_NAME)
