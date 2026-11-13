"""Agent definitions, served through the MCP agents extension.

``io.modelcontextprotocol/agents`` is mcp-kb's draft of agents-wg proposal 0000.
The server serves definitions; the host runs them. Agents are package data in
``plugin/agents/``, read once when the server is built, since package data
cannot change under a running process.

``io.kubed/agent`` in a detail's ``_meta`` is the shape both servers share (spec
2026-11-12-plugin-agents, Ruling 2).
"""

from __future__ import annotations

import copy
import logging
import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path

from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams
from pydantic import Field

from . import frontmatter

log = logging.getLogger(__name__)

EXTENSION_ID = "io.modelcontextprotocol/agents"
PLUGIN_DIR = "plugin"
AGENTS_DIR = "agents"
META_KEY = "io.kubed/agent"
AGENTS_SETTINGS = {"listChanged": False}

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
    The test is for ``plugin/``, not ``agents/``: an installed wheel may ship
    ``plugin/`` without ``agents/``, and must not then fall back to a
    ``plugin/agents`` in site-packages that another distribution could own.
    """
    packaged = Path(__file__).parent / PLUGIN_DIR
    if packaged.is_dir():
        return packaged / AGENTS_DIR
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
    try:
        files = sorted(p for p in base.iterdir() if p.suffix == ".md" and p.is_file())
    except OSError as exc:
        log.warning("agent folder %s is skipped: %s", base, exc)
        return []
    for path in files:
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


class GetAgentParams(RequestParams):
    name: str = Field(min_length=1)


def _result(**fields: object) -> dict:
    # The same as the Skills extension's, written out so neither module needs the other.
    return {"resultType": "complete", "ttlMs": 0, "cacheScope": "private", **fields}


class AgentsExtension(ServerExtension):
    """``agents/list`` and ``agents/get`` over the loaded agents."""

    identifier = EXTENSION_ID

    def __init__(self, agents: Sequence[Agent]):
        self._agents = list(agents)

    def settings(self) -> dict:
        return dict(AGENTS_SETTINGS)

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding("agents/list", PaginatedRequestParams, self.list_agents),
            MethodBinding("agents/get", GetAgentParams, self.get_agent),
        )

    async def list_agents(self, ctx, params: PaginatedRequestParams) -> dict:
        if params.cursor is not None:
            # One page; any cursor is one this server never issued.
            raise MCPError(code=INVALID_PARAMS, message="Invalid agents cursor")
        return _result(agents=[copy.deepcopy(a.card) for a in self._agents])

    async def get_agent(self, ctx, params: GetAgentParams) -> dict:
        for agent in self._agents:
            if agent.name == params.name:
                return _result(**copy.deepcopy(agent.detail))
        raise MCPError(
            code=INVALID_PARAMS,
            message="Agent not found",
            data={"reason": "agent_not_found"},
        )


class AdvertiseAgents(Middleware):
    """Declare the extension in a legacy ``initialize`` reply too.

    FastMCP declares registered extensions in ``server/discover`` only, so a
    handshake-era client would never learn the agents methods exist, as with
    AdvertiseSkills.
    """

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            result.capabilities.extensions = {
                **(result.capabilities.extensions or {}),
                EXTENSION_ID: dict(AGENTS_SETTINGS),
            }
        return result


def register(mcp, agents: Sequence[Agent]) -> None:
    """Serve the agents, declared even when there are none."""
    mcp.add_extension(AgentsExtension(agents))
    mcp.add_middleware(AdvertiseAgents())
