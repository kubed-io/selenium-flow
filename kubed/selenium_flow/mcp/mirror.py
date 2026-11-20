"""Two tools that read resources, for the clients that cannot.

Everything this server publishes to read is a resource, and every hint, error,
prompt and skill page names it by URI. A client whose model cannot read
resources (VS Code's) gets these two tools, the same pair mcp-kb serves, so the
same URIs work there too:

    list_resources()                        where to start reading
    read_resource("flow://flows/login")     one of them

`HideMirrors` hides both from a client that reads resources
(`clients.reads_resources`); they stay callable.
"""

from __future__ import annotations

import json
import mimetypes
import re

from kubed.mcp import Image, Middleware, NotFoundError, TextContent, ToolResult
from kubed.mcp.request import get_context
from kubed.mcp.skills import FolderNotFound
from pydantic import BaseModel

from ..core.annotations import reads
from ..names import retired_uri
from . import apps, clients
from .failures import nameless

LIST_TOOL = "list_resources"
READ_TOOL = "read_resource"
MIRROR_TOOLS = frozenset({LIST_TOOL, READ_TOOL})

# The MCP Apps shell: HTML a host draws in an iframe, not something to read.
NOT_FOR_READING = ("ui://",)

# Images a model can look at, returned as images. Other binary is described:
# base64 of a downloaded PDF is megabytes of text a model can do nothing with.
VIEWABLE = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

# Served as bytes and readable as text: a model cannot view SVG as a picture.
TEXTUAL = frozenset({"image/svg+xml"})


class ResourceRow(BaseModel):
    """A resource to read as it stands: pass uri to read_resource."""

    uri: str
    name: str
    description: str = ""
    mime_type: str | None = None


class TemplateRow(BaseModel):
    """A family of resources: fill in uri_template's {placeholders}, then read."""

    uri_template: str
    name: str
    description: str = ""


def _listed(uri: str) -> bool:
    return not uri.startswith(NOT_FOR_READING)


async def _rows(server) -> list[dict]:
    rows = []
    for resource in await server.list_resources():
        uri = str(resource.uri)
        if _listed(uri):
            rows.append(
                {
                    "uri": uri,
                    "name": resource.title or resource.name,
                    "description": resource.description or "",
                    "mime_type": resource.mime_type,
                }
            )
    rows.extend(
        {
            "uri_template": template.uri_template,
            "name": template.title or template.name,
            "description": template.description or "",
        }
        for template in await server.list_resource_templates()
        if _listed(template.uri_template)
    )
    return rows


def _media_type(uri: str, declared: str | None) -> str:
    """A content's real type. A template declares one type for everything it
    serves, so for `application/octet-stream` the name knows more."""
    if declared and declared != "application/octet-stream":
        return declared
    guessed, _ = mimetypes.guess_type(uri)
    return guessed or declared or "application/octet-stream"


# Where a binary file's link is listed, by the prefix of its URI.
LINKED_FROM = (
    ("workspace://files/", "workspace://files"),
    ("session://downloads/", "session://downloads"),
)


# A named session's downloads: its listing is the URI up to the file.
NAMED_DOWNLOAD = re.compile(r"(session://[^/?]+/downloads)/[^/]+")


def _where_to_read(uri: str) -> str:
    """What a model is told in place of bytes it cannot use."""
    said = "Not returned: binary content is not text."
    named = NAMED_DOWNLOAD.fullmatch(uri)
    linked = ((uri, named.group(1)),) if named else LINKED_FROM
    for prefix, listing in linked:
        if uri.startswith(prefix):
            return (
                f"{said} Read {listing} for this file's link, which any HTTP "
                "client can download."
            )
    return said


def _binary(uri: str, data: bytes, media: str) -> TextContent | Image:
    if media in TEXTUAL or media.startswith("text/"):
        try:
            return TextContent(type="text", text=data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    if media in VIEWABLE:
        return Image(data=data, format=media.split("/", 1)[1]).to_image_content()
    return TextContent(
        type="text",
        text=json.dumps(
            {
                "uri": uri,
                "mime_type": media,
                "bytes": len(data),
                "binary": True,
                "read": _where_to_read(uri),
            }
        ),
    )


def register(mcp) -> frozenset[str]:
    """Register both tools; return their names for the listing filter."""

    @mcp.tool(
        name=LIST_TOOL,
        description=(
            "List the resources and templates to start from: each row's uri, name "
            "and description, and uri_template rows whose {placeholders} you "
            "fill in. A skill's other files are named in its SKILL.md. Read one "
            "with read_resource."
        ),
        annotations=reads("List resources", open_world=False),
    )
    async def list_resources() -> list[ResourceRow | TemplateRow]:
        rows = await _rows(get_context().fastmcp)
        return [
            TemplateRow(**row) if "uri_template" in row else ResourceRow(**row)
            for row in rows
        ]

    @mcp.tool(
        name=READ_TOOL,
        description=(
            "Read the resource at uri: workspace://current, "
            "skill://selenium-flow/selenium-flow/SKILL.md, flow://flows/{name}, "
            "anything list_resources shows or a skill page names. "
            "Images come back as images; other binary files are described, not "
            "returned."
        ),
        # Open world, for the widest resource it reads: workspace://current
        # asks the Grid whether the browser is alive.
        annotations=reads("Read a resource"),
    )
    async def read_resource(uri: str) -> ToolResult:
        retired_uri(uri)
        server = get_context().fastmcp
        if not _listed(uri):
            raise ValueError(
                f"{nameless(uri)} is an app for a host to draw, not to read"
            )
        try:
            result = await server.read_resource(uri)
        except FolderNotFound as folder:
            raise ValueError(folder.miss) from None
        except NotFoundError:
            templates = ", ".join(
                row["uri_template"]
                for row in await _rows(server)
                if "uri_template" in row
            )
            raise ValueError(
                f"no resource at {nameless(uri)!r}. "
                f"list_resources shows where to start; the templates are {templates}"
            ) from None
        contents = []
        for item in result.contents:
            if isinstance(item.content, bytes):
                media = _media_type(uri, item.mime_type)
                contents.append(_binary(uri, item.content, media))
            else:
                contents.append(TextContent(type="text", text=item.content))
        return ToolResult(content=contents)

    return MIRROR_TOOLS


class HideMirrors(Middleware):
    """Keep tools that only exist for other clients out of this one's listing.

    One server object serves every client, so the listing is filtered per
    request. Hidden tools stay callable. ``app_tools`` are listed only to a host
    that renders MCP Apps.
    """

    def __init__(self, app_tools=(), apps_enabled: bool = True):
        self.app_tools = set(app_tools)
        self.apps_enabled = apps_enabled

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        hidden = (
            set()
            if not clients.reads_resources(clients.caller())
            else set(MIRROR_TOOLS)
        )
        if not (self.apps_enabled and apps.supported()):
            hidden |= self.app_tools
        return [tool for tool in tools if tool.name not in hidden]
