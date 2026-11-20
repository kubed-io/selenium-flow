"""The MCP Skills extension (SEP-2640), as FastMCP serves it plus what it lacks.

FastMCP's ``SkillsExtension`` answers ``skills/list`` and ``skills/get`` from
its skill providers, but binds them for the modern protocol versions only. This
subclass binds the same handlers for every version, declares ``directoryRead``,
and binds ``resources/directory/read`` from ``directories`` over the folders of
every provider that knows its folders. ``skill.register`` puts it in place
of the one FastMCP's provider brings.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from fastmcp.server.extensions import MethodBinding
from fastmcp.skills import extension

from . import directories

EXTENSION_ID = extension.SkillsExtension.identifier
SETTINGS = {"directoryRead": True}


class SkillsExtension(extension.SkillsExtension):
    """FastMCP's extension, for every protocol version, with folders."""

    def settings(self) -> dict:
        return dict(SETTINGS)

    def methods(self) -> Sequence[MethodBinding]:
        # FastMCP's bindings with no protocol_versions: a handshake-era client
        # is answered too.
        return (
            *(
                dataclasses.replace(binding, protocol_versions=None)
                for binding in super().methods()
            ),
            directories.DirectoryRead(self).binding(),
        )

    def clone(self) -> SkillsExtension:
        return type(self)()

    async def children(self, uri: str) -> list[dict] | None:
        """The ``directories.Folders`` of the server's providers that know theirs."""
        for provider in self.server.providers:
            children = getattr(provider, "children", None)
            if children is not None and (found := await children(uri)) is not None:
                return found
        return None
