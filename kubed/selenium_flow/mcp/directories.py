"""Folders as directory resources (SEP-2640), for any scheme.

A folder's URI has no trailing slash and the type ``inode/directory``, and it
is never listed. Its direct children come from ``resources/directory/read``,
an extension method, or from the two index files beside it, which plain
``resources/read`` reaches through a proxy that refuses methods it does not
know: ``_index.json`` is the method's ``resources`` array, ``_index.md`` one
``<uri>: <description>`` line per child. The folder itself serves nothing.

FastMCP has no counterpart, so this module knows no scheme. A provider hands
it a ``Folders``: the wire ``Resource`` of each child of a folder URI.
"""

from __future__ import annotations

import json
from typing import Protocol

import mcp_types
from fastmcp.resources import Resource, TextResource

INODE_DIRECTORY = "inode/directory"
INDEX_JSON = "_index.json"
INDEX_MD = "_index.md"


class Folders(Protocol):
    async def children(self, uri: str) -> list[dict] | None:
        """The direct children of folder ``uri`` as wire ``Resource`` dicts,
        sorted by URI, or None when ``uri`` names no folder."""


def wire(resource: Resource | mcp_types.Resource) -> dict:
    """A resource as ``resources/list`` puts it on the wire."""
    if isinstance(resource, Resource):
        resource = resource.to_mcp_resource()
    return resource.model_dump(mode="json", by_alias=True, exclude_none=True)


def folder(uri: str, name: str, description: str) -> dict:
    """A folder's directory resource."""
    return wire(
        mcp_types.Resource(
            uri=uri, name=name, description=description, mimeType=INODE_DIRECTORY
        )
    )


def index_json(children: list[dict]) -> str:
    return json.dumps(children, indent=2)


def index_md(children: list[dict]) -> str:
    return "".join(f"{c['uri']}: {c.get('description', '')}\n" for c in children)


_INDEXES = {
    INDEX_JSON: ("application/json", index_json),
    INDEX_MD: ("text/markdown", index_md),
}


async def index_resource(folders: Folders, uri: str) -> Resource | None:
    """The index file at ``uri``, or None when it is not one beside a folder."""
    parent, _, name = uri.rpartition("/")
    if name not in _INDEXES:
        return None
    children = await folders.children(parent)
    if children is None:
        return None
    mime_type, render = _INDEXES[name]
    return TextResource(
        uri=uri,
        name=uri.split("://", 1)[-1],
        description=f"What is in {parent}",
        mime_type=mime_type,
        text=render(children),
    )
