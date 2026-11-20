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
from fastmcp.exceptions import NotFoundError
from fastmcp.resources import Resource, TextResource
from fastmcp.server.extensions import MethodBinding
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams
from pydantic import Field

METHOD = "resources/directory/read"
INODE_DIRECTORY = "inode/directory"
INDEX_JSON = "_index.json"
INDEX_MD = "_index.md"


def reserved(path: str) -> bool:
    """Whether a file or folder at ``path`` would sit where its parent's index
    is served. The index answers that URI, so it is never a child."""
    return path.rpartition("/")[2] in (INDEX_JSON, INDEX_MD)


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
    """One line per child: a description's own newlines (YAML ``|``) collapse."""
    return "".join(
        f"{c['uri']}: {' '.join(c.get('description', '').split())}\n" for c in children
    )


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


class DirectoryReadParams(PaginatedRequestParams):
    uri: str = Field(min_length=1)


class DirectoryRead:
    """``resources/directory/read`` over one ``Folders``: a page of direct children.

    The cursor is the decimal offset of the next child. Any offset inside the
    folder, from 1 to one less than the number of children, is accepted;
    anything else is ``-32602``.
    """

    page_size = 100

    def __init__(self, folders: Folders):
        self._folders = folders

    def binding(self) -> MethodBinding:
        return MethodBinding(METHOD, DirectoryReadParams, self.read)

    async def read(self, ctx, params: DirectoryReadParams) -> dict:
        children = await self._folders.children(params.uri)
        if children is None:
            raise MCPError(code=INVALID_PARAMS, message="Directory not found")
        start = _offset(params.cursor, len(children))
        end = start + self.page_size
        result: dict = {"resources": children[start:end]}
        if end < len(children):
            result["nextCursor"] = str(end)
        return result


def _offset(cursor: str | None, total: int) -> int:
    if cursor is None:
        return 0
    # Length first: int() refuses a string of more than 4300 digits.
    if (
        cursor.isascii()
        and cursor.isdecimal()
        and len(cursor) <= len(str(total))
        and 0 < int(cursor) < total
    ):
        return int(cursor)
    raise MCPError(code=INVALID_PARAMS, message="Invalid directory cursor")


class FolderNotFound(MCPError):
    """A read of a folder URI: not found, as a missing URI is, naming the index.

    ``hint`` is the sentence alone, for ``read_resource`` to pass on.
    """

    def __init__(self, uri: str):
        self.hint = f"{uri} is a folder: read {uri}/{INDEX_MD} to list it."
        # What a tool that reads resources says, as it says a plain miss.
        self.miss = f"no resource at {uri!r}. {self.hint}"
        super().__init__(
            code=INVALID_PARAMS,
            message=f"Resource not found: {uri!r}. {self.hint}",
            data={"uri": uri},
        )


class NameTheIndex(Middleware):
    """Turn the not-found of a folder read into one that names its ``_index.md``.

    The provider answers None for a folder URI, as for any missing one, and
    FastMCP reports a bare ``Resource not found``. The code and ``data`` stay
    what FastMCP sends; only the message gains the hint.
    """

    def __init__(self, folders: Folders):
        self._folders = folders

    async def on_read_resource(self, context, call_next):
        try:
            return await call_next(context)
        except NotFoundError:
            uri = str(context.message.uri)
            if await self._folders.children(uri) is None:
                raise
            raise FolderNotFound(uri) from None
