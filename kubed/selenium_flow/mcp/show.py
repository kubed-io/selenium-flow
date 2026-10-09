"""show(uri): draw one resource as an MCP App, keyed by its URI.

One tool and one shell for every view: the URI says which resource, the table
below says which component draws it, and the data is the resource's own content
(its JSON, a page's markdown text, or for one file its folder listing's entry) —
so a view cannot drift from what the resource serves. It is not read_resource:
that is the model's reading tool, and an app on it would draw a UI on every read
the model makes to think (spec 2026-10-04, ruling 1).
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple
from urllib.parse import unquote

from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import get_context
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from ..core.annotations import reads
from ..names import retired_uri

TOOL = "show"

FILES = "workspace://files"
FOLDERS = ("screenshots", "recordings", "downloads")


class View(NamedTuple):
    """One URI form: how a URI is matched, what draws it, and whether it is
    drawn from its folder's listing entry rather than read whole (a single
    file: never its bytes, programme R17)."""

    form: str
    pattern: re.Pattern[str]
    component: str
    entry: bool = False


def _exactly(form: str, component: str) -> View:
    return View(form, re.compile(re.escape(form)), component)


# The only place a URI is tied to a view: what the tool names, what it matches,
# what draws it. First match wins, so a folder sits before the `{name}` row
# that would otherwise take it. Every URI the server serves has a row, and
# tests/test_show_inventory.py holds that.
VIEWS: tuple[View, ...] = (
    _exactly("workspace://current", "context"),
    _exactly("workspace://site-data", "sites"),
    View(
        "workspace://site-data/{site}",
        re.compile(r"workspace://site-data/[^/]+"),
        "site",
    ),
    _exactly(FILES, "files"),
    *(_exactly(f"{FILES}/{folder}", "folder") for folder in FOLDERS),
    *(
        View(
            f"{FILES}/{folder}/{{name}}",
            re.compile(rf"{FILES}/{folder}/[^/]+"),
            "file",
            entry=True,
        )
        for folder in FOLDERS
    ),
    View(f"{FILES}/{{name}}", re.compile(rf"{FILES}/[^/]+"), "file", entry=True),
    _exactly("flow://flows", "flows"),
    View("flow://flows/{name}", re.compile(r"flow://flows/[^/]+"), "flow"),
    _exactly("flow://schema", "document"),
    # One secret is a closer look inside the app, not a URI: there is no
    # single-secret resource to read (secrets.py, "one read").
    _exactly("secret://secrets", "secrets"),
    View(
        "skill://selenium-flow/{path}",
        re.compile(r"skill://selenium-flow/.+"),
        "document",
    ),
)
SHOWABLE = tuple(view.form for view in VIEWS)

# Claude drops a tool result over ~150k characters, and the app then never gets
# its data: a flow may be 1 MiB of YAML and a listing is unbounded.
MAX_SHOWN = 100_000
# What a listing's `count` counts, for the model's line.
NOUNS = {"files": "kept file", "folder": "file", "flows": "flow", "secrets": "secret"}


def row_for(uri: str) -> View:
    """The row that draws ``uri``, or a ValueError naming every form."""
    retired_uri(uri)
    for view in VIEWS:
        if view.pattern.fullmatch(uri):
            return view
    raise ValueError(f"{uri} has no view; show draws {', '.join(SHOWABLE)}")


def view_for(uri: str) -> str:
    return row_for(uri).component


def summary(uri: str, component: str, data) -> str:
    """One line for the model: what the person is now looking at."""
    count = data.get("count") if isinstance(data, dict) else None
    if component in NOUNS and isinstance(count, int):
        noun = NOUNS[component] + ("" if count == 1 else "s")
        return f"Showing {uri} to the person: {count} {noun}."
    return f"Showing {uri} to the person ({component})."


# The one text type `show` hands a view as text: a document is drawn from it.
MARKDOWN = "text/markdown"

DESCRIPTION = (
    "Draw any resource this server serves - a workspace://, flow://, secret:// "
    "or skill:// URI, as its resources and resource templates list them - for "
    "the person to see. For you to read one, read the resource instead. The "
    "view the person is looking at is shared with you as context."
)


async def _content(uri: str):
    """A resource as a view takes it: markdown as its text, anything else as
    its JSON."""
    try:
        result = await get_context().fastmcp.read_resource(uri)
    except NotFoundError:
        raise ValueError(f"no resource at {uri!r}") from None
    item = result.contents[0]
    content = item.content
    kind = getattr(item, "mime_type", None) or ""
    if isinstance(content, str) and kind.startswith(MARKDOWN):
        return content
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        raise ValueError(f"{uri} is not a resource show can draw") from None


async def _entry(uri: str) -> dict:
    """One file, as its folder's listing describes it: never its bytes. The
    listing never opens a browser, and a download's costs the Grid call the
    Downloads view already makes (spec 2026-10-09-show-everything, ruling 1)."""
    listing_uri, _, leaf = uri.rpartition("/")
    listing = await _content(listing_uri)
    name = unquote(leaf)
    files = listing.get("files", []) if isinstance(listing, dict) else []
    found = next(
        (f for f in files if isinstance(f, dict) and f.get("name") == name), None
    )
    if found is None:
        raise ValueError(f"no file at {uri}. {listing_uri} lists what there is")
    return found


def register(mcp, app_config) -> set[str]:
    @mcp.tool(
        name=TOOL,
        description=DESCRIPTION,
        app=app_config,
        annotations=reads("Show a resource"),
    )
    async def show(uri: str) -> ToolResult:
        view = row_for(uri)
        component = view.component
        data = await (_entry(uri) if view.entry else _content(uri))
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
