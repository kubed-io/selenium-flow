"""Agent definitions, served through the MCP agents extension.

``io.modelcontextprotocol/agents`` is mcp-kb's draft of agents-wg proposal 0000.
The server serves definitions; the host runs them. Agents are package data in
``plugin/agents/``, read once when the server is built, since package data
cannot change under a running process.

``io.kubed/agent`` in a detail's ``_meta`` is the shape both servers share (spec
2026-11-12-plugin-agents, Ruling 2).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter

log = logging.getLogger(__name__)

EXTENSION_ID = "io.modelcontextprotocol/agents"
PLUGIN_DIR = "plugin"
AGENTS_DIR = "agents"
META_KEY = "io.kubed/agent"

_HINTS = {"opus": "large", "sonnet": "medium", "haiku": "small"}
_BAD_CATEGORIES = {"Cc", "Cf", "Co", "Cs", "Cn"}


@dataclass(frozen=True)
class Agent:
    name: str
    card: dict  # what agents/list sends
    detail: dict  # what agents/get sends


def agents_path() -> Path:
    """The agents directory, installed or in a source checkout.

    ``plugin/`` lives at the repo root; pyproject maps it into the package at
    build time, so an installed wheel finds it beside this module, and a source
    checkout falls back to the root. It may not exist: no agents is a valid state.
    """
    packaged = Path(__file__).parent / PLUGIN_DIR / AGENTS_DIR
    if packaged.is_dir():
        return packaged
    # parents[3] because this module lives in mcp/: package, kubed, repo root.
    return Path(__file__).parents[3] / PLUGIN_DIR / AGENTS_DIR


def routing_hint(model: object) -> dict | None:
    """A hint for the host's model choice, from a model name, or None."""
    if not isinstance(model, str):
        return None
    for word in re.split(r"[^a-z0-9]+", model.lower()):
        if word in _HINTS:
            return {"role": _HINTS[word], "vision": False}
    return None


def _bad_name(name: object) -> bool:
    if not isinstance(name, str) or not name:
        return True
    return any(
        ch.isspace() or unicodedata.category(ch) in _BAD_CATEGORIES or ch in "/@"
        for ch in name
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    return []


def _build(text: str, skills: Collection[str]) -> Agent | str:
    """The agent, or the sentence saying why the file is skipped."""
    parsed = frontmatter.split(text)
    if parsed is None:
        return "no YAML frontmatter"
    header, body = parsed
    if not isinstance(header, dict) or not header:
        return "its frontmatter is not a non-empty mapping"
    if not frontmatter.survives_json(header):
        return "its frontmatter is not representable as JSON"
    name = header.get("name")
    if _bad_name(name):
        return (
            "its name must be a non-empty string with no whitespace, control or "
            'invisible characters, "/" or "@"'
        )
    description = header.get("description")
    if not isinstance(description, str) or not description.strip():
        return "its description must be a non-empty string"

    card = {
        "name": name,
        "description": " ".join(description.split()),
        "capabilities": _strings(header.get("capabilities")),
    }
    detail: dict = {"agent": name}
    if body.strip():
        detail["instructions"] = body.strip()
    hint = routing_hint(header.get("model"))
    if hint:
        detail["modelRoutingHint"] = hint
    detail["tools"] = []
    detail["_meta"] = {
        META_KEY: {
            "frontmatter": header,
            "skills": {
                s: f"skill://{s}/SKILL.md"
                for s in _strings(header.get("skills"))
                if s in skills
            },
        }
    }
    return Agent(name=name, card=card, detail=detail)


def load(base: Path | None = None, skills: Collection[str] = ()) -> list[Agent]:
    """The agents in a folder, in name order. A missing folder is no agents."""
    base = agents_path() if base is None else base
    if not base.is_dir():
        return []
    found: dict[str, tuple[Agent, str]] = {}
    for path in sorted(p for p in base.iterdir() if p.suffix == ".md" and p.is_file()):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("agent file %s is skipped: %s", path.name, exc)
            continue
        built = _build(text, skills)
        if isinstance(built, str):
            log.warning("agent file %s is skipped: %s", path.name, built)
        elif built.name in found:
            log.warning(
                "agent file %s is skipped: %s is already served by %s",
                path.name,
                built.name,
                found[built.name][1],
            )
        else:
            found[built.name] = (built, path.name)
    return sorted((a for a, _ in found.values()), key=lambda a: a.name)
