"""The MCP Skills extension (SEP-2640) for the embedded skill.

``skills/list`` and ``skills/get`` are JSON-RPC methods beside
``resources/list``. They return an entry naming every file of the skill with the
digest and size of what ``resources/read`` hands back, so a client can verify
each file it loads.

FastMCP 4.2's ``SkillCatalogProvider`` serves the extension itself, and this
module is apart from ``skill.py`` so it can be deleted once the server moves to
it. The entry is built once, with the server, since package data cannot change
under a running process.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import mimetypes
from collections.abc import Sequence
from pathlib import Path

from fastmcp.server.extensions import MethodBinding, ServerExtension
from fastmcp.server.providers.skills.skill_provider import SkillProvider
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams
from pydantic import Field

from . import frontmatter
from .extensions import AdvertiseLegacyExtensions, complete

log = logging.getLogger(__name__)

EXTENSION_ID = "io.modelcontextprotocol/skills"
MAX_DESCRIPTION = 1024


class GetSkillParams(RequestParams):
    uri: str = Field(min_length=1)


def _served_bytes(path: Path) -> bytes:
    """What ``resources/read`` returns for this file, as bytes.

    SkillProvider reads a ``text/*`` file with ``read_text``, which normalises
    newlines, and anything else raw. A host verifies the bytes it receives, so
    the digest follows the read, not the disk (which ``_manifest`` hashes).
    Importing SkillProvider registers ``.md`` as ``text/markdown``.
    """
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type and mime_type.startswith("text/"):
        return path.read_text(encoding="utf-8").encode("utf-8")
    return path.read_bytes()


def _problem(metadata: object, name: str) -> str | None:
    """Why this frontmatter cannot be offered as a conforming entry, if it cannot."""
    if not isinstance(metadata, dict):
        return "SKILL.md has no YAML frontmatter mapping"
    if not frontmatter.survives_json(metadata):
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
    parsed = frontmatter.split((info.path / info.main_file).read_text(encoding="utf-8"))
    metadata = parsed[0] if parsed else None
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
            # One page, so no cursor was ever issued.
            raise MCPError(code=INVALID_PARAMS, message="Invalid skills cursor")
        return complete(skills=[copy.deepcopy(self._entry)])

    async def get_skill(self, ctx, params: GetSkillParams) -> dict:
        if params.uri != self._entry["uri"]:
            raise MCPError(code=INVALID_PARAMS, message="Skill not found")
        return complete(skill=copy.deepcopy(self._entry))


def register(mcp, provider: SkillProvider) -> bool:
    """Offer the skill through the extension.

    False, with nothing added, when the skill does not conform; its resources
    are still served.
    """
    entry = entry_for(provider)
    if entry is None:
        return False
    mcp.add_extension(SkillsExtension(entry))
    mcp.add_middleware(AdvertiseLegacyExtensions({EXTENSION_ID: {}}))
    return True
