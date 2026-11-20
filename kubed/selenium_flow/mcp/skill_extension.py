"""The MCP Skills extension (SEP-2640), as FastMCP serves it plus what it lacks.

FastMCP's ``SkillsExtension`` answers ``skills/list`` and ``skills/get`` from
its skill providers, but binds them for the modern protocol versions only. This
subclass binds the same handlers for every version, declares ``directoryRead``,
and binds ``resources/directory/read`` from ``directories`` over the folders of
every skill provider that knows its folders. ``skill.register`` puts it in place
of the one FastMCP's provider brings.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp.server.extensions import MethodBinding
from fastmcp.skills import extension
from fastmcp.skills.models import GetSkillParams, ListSkillsParams

from . import directories

EXTENSION_ID = extension.SkillsExtension.identifier
SETTINGS = {"directoryRead": True}


class SkillsExtension(extension.SkillsExtension):
    """FastMCP's extension, for every protocol version, with folders."""

    def settings(self) -> dict:
        return dict(SETTINGS)

    def methods(self) -> Sequence[MethodBinding]:
        # No protocol_versions: a handshake-era client is answered too.
        return (
            MethodBinding("skills/list", ListSkillsParams, self._handle_list),
            MethodBinding("skills/get", GetSkillParams, self._handle_get),
            directories.DirectoryRead(self).binding(),
        )

    def clone(self) -> SkillsExtension:
        return type(self)()

    async def children(self, uri: str) -> list[dict] | None:
        """The ``directories.Folders`` of the server's skill providers."""
        for source in self._candidate_sources():
            children = getattr(source, "children", None)
            if children is not None and (found := await children(uri)) is not None:
                return found
        return None
