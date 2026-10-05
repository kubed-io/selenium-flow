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

# The only place a URI is tied to a view: what the tool names, what it matches,
# what draws it. First match wins.
VIEWS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("session://current", re.compile(r"session://current"), "context"),
    ("session://files", re.compile(r"session://files"), "files"),
    (
        "session://files/screenshots",
        re.compile(r"session://files/screenshots"),
        "folder",
    ),
    ("session://files/downloads", re.compile(r"session://files/downloads"), "folder"),
    ("flow://flows", re.compile(r"flow://flows"), "flows"),
    ("flow://flows/{name}", re.compile(r"flow://flows/[^/]+"), "flow"),
)
SHOWABLE = tuple(form for form, _, _ in VIEWS)

# Claude drops a tool result over ~150k characters, and the app then never gets
# its data: a flow may be 1 MiB of YAML and a listing is unbounded.
MAX_SHOWN = 100_000


def view_for(uri: str) -> str:
    for _, pattern, component in VIEWS:
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
        payload = {"component": component, "uri": uri, "data": data}
        size = len(json.dumps(payload))
        if size > MAX_SHOWN:
            raise ValueError(
                f"{uri} is too large to draw here ({size} characters); "
                "read it with the resource instead"
            )
        return payload

    return {TOOL}
