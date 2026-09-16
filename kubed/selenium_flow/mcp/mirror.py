"""Two tools that read resources, for the clients that cannot.

Everything this server publishes to read — the session status, the skill, the
flow library, the kept files, the secrets catalogue — is a resource with a URI,
and the text an agent reads refers to it by that URI: a hint, an error, a
prompt, the skill itself. A client that reads resources already knows what to
do with one. A client that cannot — VS Code Copilot's model has no way to — is
given these two tools and nothing else, so the same URI works there too:

    list_resources()                        every URI there is to read
    read_resource("flow://flows/login")     one of them

It used to be seven tools, one per resource, each with a name the text had to
use instead of the URI. Seven names to keep in step with the resources, and a
second vocabulary for the one act (saga §F3.6). This is the vocabulary
`mcp-kb` uses too, so an agent learns it once.

Both are hidden from a client that reads resources, and stay callable; see
`HideMirrors`. What decides "reads resources" is `clients.reads_resources`.
"""

from __future__ import annotations

import json
import mimetypes

from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import get_context
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent

from . import apps, clients
from .annotations import reads

LIST_TOOL = "list_resources"
READ_TOOL = "read_resource"
MIRROR_TOOLS = frozenset({LIST_TOOL, READ_TOOL})

# The MCP Apps shell: HTML a host draws in an iframe, not something to read.
NOT_FOR_READING = ("ui://",)

# Images a model can look at, returned as images. Anything else that is binary
# is described instead of dumped: base64 of a downloaded PDF is megabytes of
# text a model can do nothing with, and it would spend the context doing it.
VIEWABLE = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


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
    for template in await server.list_resource_templates():
        if _listed(template.uri_template):
            rows.append(
                {
                    "uri_template": template.uri_template,
                    "name": template.title or template.name,
                    "description": template.description or "",
                }
            )
    return rows


def _media_type(uri: str, declared: str | None) -> str:
    """A content's real type. A template declares one type for everything it
    serves, so `application/octet-stream` is a shrug, and the name knows more."""
    if declared and declared != "application/octet-stream":
        return declared
    guessed, _ = mimetypes.guess_type(uri)
    return guessed or declared or "application/octet-stream"


def _binary(uri: str, data: bytes, media: str) -> TextContent | Image:
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
                "read": (
                    "Not returned: binary content is not text. Read "
                    "session://files for this file's link, which any HTTP "
                    "client can download."
                    if uri.startswith("session://files/")
                    else "Not returned: binary content is not text."
                ),
            }
        ),
    )


def register(mcp) -> frozenset[str]:
    """Register both tools; return their names for the listing filter."""

    @mcp.tool(
        name=LIST_TOOL,
        description=(
            "List every resource this server has to read: each row's uri, name "
            "and description, and uri_template rows whose {placeholders} you "
            "fill in. Read one with read_resource."
        ),
        annotations=reads("List resources", open_world=False),
    )
    async def list_resources() -> list[dict]:
        return await _rows(get_context().fastmcp)

    @mcp.tool(
        name=READ_TOOL,
        description=(
            "Read the resource at uri: session://current, skill://selenium-flow/"
            "SKILL.md, flow://flows/{name}, anything list_resources shows. "
            "Images come back as images; other binary files are described, not "
            "returned."
        ),
        # Open world: session://current asks the Grid whether the browser is
        # still alive. One tool reads every resource, so it is annotated for the
        # widest of them.
        annotations=reads("Read a resource"),
    )
    async def read_resource(uri: str) -> ToolResult:
        server = get_context().fastmcp
        if not _listed(uri):
            raise ValueError(f"{uri} is an app for a host to draw, not to read")
        try:
            result = await server.read_resource(uri)
        except NotFoundError:
            templates = ", ".join(
                row["uri_template"]
                for row in await _rows(server)
                if "uri_template" in row
            )
            raise ValueError(
                f"no resource at {uri!r}. list_resources shows every URI; the "
                f"templates are {templates}"
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

    Filtering the listing rather than registering conditionally is what keeps
    one server object correct for every client at once: the decision depends on
    who is asking, so it is made per request. Hidden tools stay callable.

    ``app_tools`` draw something for a host that renders MCP Apps, which is the
    only reason they exist, so they are listed exactly when the host can draw.
    """

    def __init__(self, app_tools=(), apps_enabled: bool = True):
        self.app_tools = set(app_tools)
        self.apps_enabled = apps_enabled

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        hidden = set() if not clients.reads_resources() else set(MIRROR_TOOLS)
        if not (self.apps_enabled and apps.supported()):
            hidden |= self.app_tools
        return [tool for tool in tools if tool.name not in hidden]
