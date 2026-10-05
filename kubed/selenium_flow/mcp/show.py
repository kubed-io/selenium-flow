"""show(uri): draw one resource as an MCP App, keyed by its URI.

One tool and one shell for every view: the URI says which resource, the table
below says which component draws it, and the data is the resource's own JSON —
so a view cannot drift from what the resource serves. It is not read_resource:
that is the model's reading tool, and an app on it would draw a UI on every read
the model makes to think (spec 2026-10-04, ruling 1).
"""

from __future__ import annotations

import json
import re

from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import get_context

from ..core.annotations import reads

TOOL = "show"

# First match wins. The only place a URI is tied to a view.
VIEWS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"session://current"), "context"),
    (re.compile(r"session://files"), "files"),
    (re.compile(r"session://files/(screenshots|downloads)"), "folder"),
    (re.compile(r"flow://flows"), "flows"),
    (re.compile(r"flow://flows/[^/]+"), "flow"),
)
SHOWABLE = (
    "session://current", "session://files", "session://files/screenshots",
    "session://files/downloads", "flow://flows", "flow://flows/{name}",
)


def view_for(uri: str) -> str:
    for pattern, component in VIEWS:
        if pattern.fullmatch(uri):
            return component
    raise ValueError(f"{uri} has no view; show draws {', '.join(SHOWABLE)}")


def register(mcp, app_config) -> set[str]:
    @mcp.tool(
        name=TOOL,
        description=(
            "Draw a resource for the person to see: "
            + ", ".join(SHOWABLE)
            + ". For you to read one, read the resource instead. The view the "
            "person is looking at is shared with you as context."
        ),
        app=app_config,
        annotations=reads("Show a resource"),
    )
    async def show(uri: str) -> dict:
        component = view_for(uri)
        try:
            result = await get_context().fastmcp.read_resource(uri)
        except NotFoundError:
            raise ValueError(f"no resource at {uri!r}") from None
        try:
            data = json.loads(result.contents[0].content)
        except (TypeError, ValueError):
            raise ValueError(f"{uri} is not a resource show can draw") from None
        return {"component": component, "uri": uri, "data": data}

    return {TOOL}
