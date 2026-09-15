"""Prompts: templates a *person* picks, served from markdown files.

A skill is read by the model when it decides to; a prompt is chosen by somebody
— Claude Code lists them as slash commands — who fills in a few arguments before
the model sees anything. Different primitive, different module.

This is why a failed run's `hint` names one: an agent cannot invoke a prompt, but
it can tell a person which to pick, and that person picks it in their own client
(saga §F2.6). The admin page has no run view and no picker — it shows sessions,
files and flows — so the hint reaches a person through whoever ran the flow.

The file format and this loader follow `kubed-io/skills-mcp`, which has run them
in production for months: frontmatter declaring the arguments, a body with
`{{ placeholders }}`, strict loading. **Copied, not imported** — this server does
not take a dependency on another server's package for sixty lines. What must not
drift is the format; `skills-mcp/kubed/skills_mcp/prompts.py` is the other copy.

Double braces rather than `str.format`'s single ones, and it matters more here
than there: these bodies are full of flow YAML, whose parameter references are
`${name}` and whose JSON examples are all braces.
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

log = logging.getLogger(__name__)

PROMPTS_DIR = "prompts"
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
    """The prompts directory, installed or in a source checkout.

    `prompts/` lives at the repo root, where it reads as documentation rather
    than as buried package data, and pyproject maps it into the package at build
    time — the same trick `skills/` uses, and for the same reason: without it
    they are simply absent from an installed wheel.
    """
    packaged = Path(__file__).parent / PROMPTS_DIR
    if packaged.is_dir():
        return packaged
    return Path(__file__).parents[2] / PROMPTS_DIR


def _split(text: str) -> tuple[dict, str] | None:
    """Frontmatter and body, or None when there is no frontmatter block."""
    if not text.startswith("---"):
        return None
    _, _, rest = text.partition("---")
    block, separator, body = rest.partition("\n---")
    if not separator:
        return None
    meta = yaml.safe_load(block)
    if meta is not None and not isinstance(meta, dict):
        # `description: x` is a mapping; a bare list or scalar is a file
        # somebody got wrong, and publishing it with no metadata leaves an
        # unusable entry in the picker rather than a skipped file.
        raise ValueError(f"frontmatter must be a mapping, got {type(meta).__name__}")
    return (meta or {}), body.lstrip("\n")


def load_prompt(path: Path) -> FilePrompt:
    """Parse one prompt file, raising ValueError on anything malformed.

    An undeclared placeholder is an error rather than an empty substitution: it
    is almost always a typo, and rendered blank it produces a prompt that reads
    perfectly well and asks the model for the wrong thing.
    """
    split = _split(path.read_text(encoding="utf-8"))
    if split is None:
        raise ValueError("no YAML frontmatter")
    meta, body = split

    declared = meta.get("arguments") or []
    if not isinstance(declared, list):
        # `arguments: 1` would otherwise raise TypeError out of the loop, which
        # `load_prompts` does not catch — and one malformed file would take the
        # server's whole prompt registration down with it.
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

    A broken file is logged and skipped rather than raised: one bad prompt must
    not take the server down with it. `tests/test_prompts.py` loads every
    shipped prompt strictly, so this only ever fires for a file that arrived
    after the tests did.
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
