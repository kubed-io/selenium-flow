"""The MCP Skills extension (SEP-2640) for the embedded skill.

``skill.py`` serves the skill as resources, which is FastMCP's job. A client that
implements the Skills extension looks for more: the extension declared in the
server's capabilities, then ``skills/list`` and ``skills/get``. They return an
entry naming every file of the skill with the digest and size of what
``resources/read`` hands back, so the client can verify each file it loads. They
are JSON-RPC methods beside ``resources/list``, not tools.

FastMCP has no built-in support yet (its #5016), so this part is ours, and kept
apart from ``skill.py`` so it is easy to delete once FastMCP ships its own.

The entry is built once, with the server: package data cannot change under a
running process.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import mimetypes
from collections.abc import Sequence
from pathlib import Path

import yaml
from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.middleware import Middleware
from fastmcp.server.providers.skills.skill_provider import SkillProvider
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams
from pydantic import Field

log = logging.getLogger(__name__)

EXTENSION_ID = "io.modelcontextprotocol/skills"
MAX_DESCRIPTION = 1024


class GetSkillParams(RequestParams):
    uri: str = Field(min_length=1)


def _served_bytes(path: Path) -> bytes:
    """What ``resources/read`` returns for this file, as bytes.

    SkillProvider reads a ``text/*`` file with ``read_text``, which normalises
    newlines, and anything else raw. A host verifies the bytes it receives, so
    the digest follows the read, not the disk (which is what ``_manifest``
    hashes). Importing SkillProvider registers ``.md`` as ``text/markdown``.
    """
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type and mime_type.startswith("text/"):
        return path.read_text(encoding="utf-8").encode("utf-8")
    return path.read_bytes()


def _frontmatter(text: str) -> object:
    """The YAML header of a SKILL.md, as parsed, or None without one."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    for end, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            try:
                return yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError:
                return None
    return None


def _problem(metadata: object, name: str) -> str | None:
    """Why this frontmatter cannot be offered as a conforming entry, if it cannot."""
    if not isinstance(metadata, dict):
        return "SKILL.md has no YAML frontmatter mapping"
    try:
        # Frontmatter travels as JSON; a YAML-only value would not survive it.
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError):
        return "its frontmatter is not representable as JSON"
    if metadata.get("name") != name:
        return f"its frontmatter name is not the folder name {name!r}"
    description = metadata.get("description")
    if not isinstance(description, str) or not (
        1 <= len(description) <= MAX_DESCRIPTION
    ):
        return f"its description is not a string of 1-{MAX_DESCRIPTION} characters"
    return None


def entry_for(provider: SkillProvider) -> dict | None:
    """The skill's SEP-2640 entry, or None (logged) if it does not conform."""
    info = provider.skill_info
    metadata = _frontmatter((info.path / info.main_file).read_text(encoding="utf-8"))
    problem = _problem(metadata, info.name)
    if problem:
        log.warning(
            "skill %s is not offered through the Skills extension: %s",
            info.name,
            problem,
        )
        return None
    resources = []
    for file in info.files:
        data = _served_bytes(info.path / file.path)
        resources.append(
            {
                "uri": f"skill://{info.name}/{file.path}",
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
                "size": len(data),
            }
        )
    resources.sort(key=lambda resource: resource["uri"])
    return {
        "uri": f"skill://{info.name}/{info.main_file}",
        "frontmatter": metadata,
        "resources": resources,
    }


def _result(**fields: object) -> dict:
    # The same freshness fields as mcp-kb: a hint, not an integrity property.
    return {"resultType": "complete", "ttlMs": 0, "cacheScope": "private", **fields}


class SkillsExtension(ServerExtension):
    """``skills/list`` and ``skills/get`` over the one embedded skill."""

    identifier = EXTENSION_ID

    def __init__(self, entry: dict):
        self._entry = entry

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding("skills/list", PaginatedRequestParams, self.list_skills),
            MethodBinding("skills/get", GetSkillParams, self.get_skill),
        )

    async def list_skills(self, ctx, params: PaginatedRequestParams) -> dict:
        if params.cursor is not None:
            # One skill, so one page; any cursor is one this server never issued.
            raise MCPError(code=INVALID_PARAMS, message="Invalid skills cursor")
        return _result(skills=[copy.deepcopy(self._entry)])

    async def get_skill(self, ctx, params: GetSkillParams) -> dict:
        if params.uri != self._entry["uri"]:
            raise MCPError(code=INVALID_PARAMS, message="Skill not found")
        return _result(skill=copy.deepcopy(self._entry))


class AdvertiseSkills(Middleware):
    """Declare the extension in a legacy ``initialize`` reply too.

    FastMCP declares registered extensions in ``server/discover`` but drops
    ``capabilities.extensions`` from ``initialize``, and still answers the
    methods; without this, a handshake-era client never learns they exist.
    """

    async def on_initialize(self, context, call_next):
        result = await call_next(context)
        if result is not None:
            result.capabilities.extensions = {
                **(result.capabilities.extensions or {}),
                EXTENSION_ID: {},
            }
        return result


def register(mcp, provider: SkillProvider) -> bool:
    """Offer the skill through the extension.

    False, with nothing added, when the skill does not conform: its resources
    are still served, so a bad SKILL.md costs the extension, not the boot.
    """
    entry = entry_for(provider)
    if entry is None:
        return False
    mcp.add_extension(SkillsExtension(entry))
    mcp.add_middleware(AdvertiseSkills())
    return True
