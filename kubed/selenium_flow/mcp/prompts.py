"""Prompts: templates a person picks, served from markdown files.

A person picks a prompt (Claude Code lists them as slash commands) and fills in
its arguments before the model sees anything. An agent cannot invoke one, so a
failed run's `hint` names the prompt for the person to pick.

The format and this loader are a copy of `kubed-io/skills-mcp`'s
(`kubed/skills_mcp/prompts.py`): frontmatter declaring the arguments, a body
with `{{ placeholders }}`, strict loading. The format must not drift between the
two. Placeholders take double braces because the bodies are full of flow YAML,
whose `${name}` references and JSON examples are braces.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from fastmcp import FastMCP
from fastmcp.exceptions import PromptError
from fastmcp.prompts import Prompt, PromptArgument
from pydantic import Field

from .skill import plugin_folder

log = logging.getLogger(__name__)

PROMPTS_DIR = "commands"
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class FilePrompt(Prompt):
    """One prompt file, rendered by substituting its placeholders."""

    template: str
    defaults: dict[str, str] = Field(default_factory=dict)

    async def render(self, arguments: dict | None = None) -> str:
        # A field left blank arrives as "" from most prompt pickers, so it counts
        # as not given: a default applies, and a required argument still fails.
        given = {
            key: str(value)
            for key, value in (arguments or {}).items()
            if value not in (None, "")
        }
        missing = [
            argument.name
            for argument in self.arguments or []
            if argument.required and argument.name not in given
        ]
        if missing:
            raise PromptError(f"Missing required arguments: {', '.join(missing)}")
        values = {**self.defaults, **given}
        return PLACEHOLDER.sub(
            lambda match: values.get(match.group(1), ""), self.template
        )


def prompts_path() -> Path:
    """The prompts directory, installed or in a source checkout."""
    return plugin_folder(__file__, PROMPTS_DIR)


def _split(text: str) -> tuple[dict, str] | None:
    """Frontmatter and body, or None when there is no frontmatter block."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return None
    end = next(
        (n for n, line in enumerate(lines[1:], start=1) if line.rstrip() == "---"),
        None,
    )
    if end is None:
        return None
    meta = yaml.safe_load("".join(lines[1:end]))
    if meta is not None and not isinstance(meta, dict):
        # Published with no metadata, it would be an unusable picker entry.
        raise ValueError(f"frontmatter must be a mapping, got {type(meta).__name__}")
    return (meta or {}), "".join(lines[end + 1 :]).lstrip("\r\n")


def load_prompt(path: Path) -> FilePrompt:
    """Parse one prompt file, raising ValueError on anything malformed.

    An undeclared placeholder is an error: it is almost always a typo, and
    rendered blank it asks the model for the wrong thing.
    """
    split = _split(path.read_text(encoding="utf-8"))
    if split is None:
        raise ValueError("no YAML frontmatter")
    meta, body = split

    declared = meta.get("arguments") or []
    if not isinstance(declared, list):
        # Otherwise `arguments: 1` raises a TypeError `load_prompts` does not
        # catch.
        raise ValueError(f"arguments must be a list, got {type(declared).__name__}")

    arguments: list[PromptArgument] = []
    defaults: dict[str, str] = {}
    for raw in declared:
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValueError(f"argument without a name: {raw!r}")
        name = str(raw["name"])
        required = bool(raw.get("required", False))
        if required and "default" in raw:
            raise ValueError(f"required argument {name!r} cannot have a default")
        arguments.append(
            PromptArgument(
                name=name, description=raw.get("description"), required=required
            )
        )
        if "default" in raw:
            defaults[name] = str(raw["default"])

    undeclared = sorted(set(PLACEHOLDER.findall(body)) - {a.name for a in arguments})
    if undeclared:
        raise ValueError(f"placeholders with no argument: {', '.join(undeclared)}")

    return FilePrompt(
        name=path.stem,
        description=" ".join(str(meta.get("description", "")).split()) or None,
        arguments=arguments,
        template=body,
        defaults=defaults,
    )


def load_prompts(base: Path | None = None) -> list[FilePrompt]:
    """Every prompt file, as the server will serve them.

    A broken file is logged and skipped, so one bad prompt cannot stop the
    server.
    """
    base = prompts_path() if base is None else base
    if not base.is_dir():
        return []
    found: list[FilePrompt] = []
    for path in sorted(base.glob("*.md")):
        try:
            found.append(load_prompt(path))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            log.warning("skipping prompt %s: %s", path, exc)
    return found


def register(mcp: FastMCP, prompts=None) -> set[str]:
    """Publish the prompts. Returns their names."""
    published = load_prompts() if prompts is None else prompts
    for prompt in published:
        mcp.add_prompt(prompt)
    return {prompt.name for prompt in published}
