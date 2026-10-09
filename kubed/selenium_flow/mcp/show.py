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
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from ..core.annotations import reads
from ..names import retired_uri

TOOL = "show"

# The only place a URI is tied to a view: what the tool names, what it matches,
# what draws it. First match wins.
VIEWS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("workspace://current", re.compile(r"workspace://current"), "context"),
    ("workspace://files", re.compile(r"workspace://files"), "files"),
    (
        "workspace://files/screenshots",
        re.compile(r"workspace://files/screenshots"),
        "folder",
    ),
    (
        "workspace://files/recordings",
        re.compile(r"workspace://files/recordings"),
        "folder",
    ),
    (
        "workspace://files/downloads",
        re.compile(r"workspace://files/downloads"),
        "folder",
    ),
    ("flow://flows", re.compile(r"flow://flows"), "flows"),
    ("flow://flows/{name}", re.compile(r"flow://flows/[^/]+"), "flow"),
    # One secret is a closer look inside the app, not a URI: there is no
    # single-secret resource to read (secrets.py, "one read").
    ("secret://secrets", re.compile(r"secret://secrets"), "secrets"),
)
SHOWABLE = tuple(form for form, _, _ in VIEWS)

# Claude drops a tool result over ~150k characters, and the app then never gets
# its data: a flow may be 1 MiB of YAML and a listing is unbounded.
MAX_SHOWN = 100_000
# What a listing's `count` counts, for the model's line.
NOUNS = {"files": "kept file", "folder": "file", "flows": "flow", "secrets": "secret"}


def view_for(uri: str) -> str:
    retired_uri(uri)
    for _, pattern, component in VIEWS:
        if pattern.fullmatch(uri):
            return component
    raise ValueError(f"{uri} has no view; show draws {', '.join(SHOWABLE)}")


def summary(uri: str, component: str, data) -> str:
    """One line for the model: what the person is now looking at."""
    count = data.get("count") if isinstance(data, dict) else None
    if component in NOUNS and isinstance(count, int):
        noun = NOUNS[component] + ("" if count == 1 else "s")
        return f"Showing {uri} to the person: {count} {noun}."
    return f"Showing {uri} to the person ({component})."


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
    async def show(uri: str) -> ToolResult:
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
        # Sent once, as structured content: a plain dict would go out as a JSON
        # text block too, doubling it. The model reads resources to think
        # (ruling 1), so its text is one line saying what was shown.
        text = summary(uri, component, data)
        size = len(json.dumps({
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
        }))
        if size > MAX_SHOWN:
            raise ValueError(
                f"{uri} is too large to draw here ({size} characters); "
                "read it with the resource instead"
            )
        return ToolResult(
            content=[TextContent(type="text", text=text)], structured_content=payload
        )

    return {TOOL}
