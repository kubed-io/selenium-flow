"""Every browser action, once: the one table both surfaces are generated from.

A capability is one thing a caller can do to its browser, and it is reachable
two ways — an MCP tool and an HTTP endpoint — with the same arguments and the
same answer (AGENTS.md). Everything the two surfaces, the OpenAPI document, the
flow runner and the wiki need to know about one is a column of its row here:

* ``name`` is the tool, the flow step's ``tool`` and the spec's operationId;
* ``method`` is the ``Actions`` method that does it — the name, except where
  the name is a Python keyword (``assert``) or a builtin (``print``);
* ``route`` is its path under ``/browser``, **declared**, never derived from the
  name (§F2.13): ``press_key`` is ``/browser/press-key``. ``""`` is the browser
  resource itself, which ``open_session`` and ``end_browser`` are methods on;
* ``http_method`` is declared beside it, for the same reason;
* ``annotations`` are the MCP hints a client reads before calling it, built by
  ``core/annotations.py`` and held honest by ``tests/test_surfaces.py``;
* ``response`` is what it answers, as JSON Schema. The actions return plain
  dicts, so there is nothing to introspect: this is the hand-written half of the
  published document, and a row cannot be written without it;
* ``in_path`` puts the action's choice in the path (``/browser/interact/click``);
* ``reshapes`` marks the action that changes what the session record stores
  rather than only the page, so every surface asks ``sessions.reshape``;
* ``library_arg`` names an argument that carries the caller's own library: a
  flow run injects it, and no request may set it.

What is *not* here is the prompt. A tool's parameters and docstring are its
schema and its description, and they stay literal Python in ``mcp/tools.py``
where FastMCP reads them — that file declares a signature per row and the body
comes from this table.

No protocol import, on purpose: the flow engine reads this table, and it sits
below the protocol layers (``tests/test_boundaries.py``), and the annotations
it carries are built by ``core.annotations`` beside it, which imports nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .annotations import hints


@dataclass(frozen=True)
class Capability:
    """One row: a browser action, as both surfaces publish it."""

    name: str
    method: str
    route: str
    http_method: str
    annotations: dict
    response: dict
    in_path: bool = False
    reshapes: bool = False
    library_arg: str | None = None


# ---- what each one answers -------------------------------------------------


# Fields every action echoes back, so a caller always knows where the browser
# ended up without a second call.
PAGE_STATE = {
    "url": {"type": "string", "description": "Current URL after the action."},
    "title": {"type": "string", "description": "Page title after the action."},
}


# Present only when a call did something with site data, so it is left out of
# `required` and of every response that never carries it.
SITE_DATA_HINT = {
    "type": "object",
    "description": (
        "Present only when site data was restored, skipped or forgotten in "
        "this call."
    ),
    "properties": {
        "restored": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Each host whose saved cookies or storage are in the browser "
                "now (app.example.com). All of it is in place before the call "
                "returns."
            ),
        },
        "forgotten": {
            "type": "integer",
            "description": "Sites deleted because restore_site_data was false.",
        },
        "skipped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cookie": {"type": "string", "description": "A cookie's name."},
                    "domain": {"type": "string", "description": "Its domain."},
                    "site": {
                        "type": "string",
                        "description": "An origin whose storage did not come back.",
                    },
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
            "description": "Items that could not be restored, each with a reason.",
        },
        "uri": {"type": "string", "description": "Where to read what is saved."},
    },
}


def _page(**extra) -> dict:
    return {
        "type": "object",
        "properties": {**extra, **PAGE_STATE, "site_data": SITE_DATA_HINT},
    }


# ---- the table ------------------------------------------------------------

# Every one is a POST except ending the browser, reads included. A body is
# then the same object as a tool's arguments and a flow step's `args` — one
# shape in three places — and a GET that waits thirty seconds for an
# element surprises caches and proxies besides (§F2.13, Dr K).
CAPABILITIES: tuple[Capability, ...] = (
    # The browser resource's own two: POST and DELETE on /browser itself,
    # because which browser is a question about who is asking (§F2.13). Both
    # are served by `SessionManager`, which is where a browser is made and
    # ended, rather than through `sessions.act`.
    Capability(
        name="open_session",
        method="open_session",
        route="",
        http_method="POST",
        annotations=hints("Open browser session", destructive=True),
        response={
            "type": "object",
            "properties": {
                "session": {
                    "type": "string",
                    "description": (
                        "The session this browser belongs to — the name you called "
                        "with. There is no browser id to keep."
                    ),
                },
                "browser": {
                    "type": "string",
                    "description": "The browser this session is running.",
                },
                **PAGE_STATE,
                "site_data": SITE_DATA_HINT,
                "width": {"type": "integer", "description": "Window width in use."},
                "height": {"type": "integer", "description": "Window height in use."},
                "settings": {
                    "type": "object",
                    "description": (
                        "The settings this session actually opened with, after the "
                        "server default / client default / explicit cascade."
                    ),
                },
                "recording": {
                    "type": "boolean",
                    "description": "Whether this browser is being recorded.",
                },
                "recording_error": {
                    "type": "string",
                    "description": (
                        "Why this recording cannot be filed, when it cannot."
                    ),
                },
            },
        },
    ),
    Capability(
        name="navigate",
        method="navigate",
        route="navigate",
        http_method="POST",
        annotations=hints("Navigate to URL", idempotent=True),
        response=_page(),
    ),
    # The one action whose choice is a path segment rather than a body
    # field: /browser/interact/click reads as the thing it does, and the
    # enum is already closed (§F2.1). The action layer still validates it.
    Capability(
        name="interact",
        method="interact",
        route="interact",
        http_method="POST",
        in_path=True,
        annotations=hints("Mouse action on an element", destructive=True),
        response=_page(
            action={"type": "string", "description": "The gesture that was performed."},
            glided={
                "type": "boolean",
                "description": (
                    "Whether the pointer travelled to the element in steps rather "
                    "than jumping. Absent for scroll_to, which moves the page."
                ),
            },
            nudged={
                "type": "boolean",
                "description": (
                    "Present when the pointer was already inside the target and had "
                    "to step away first, so the move it was asked for was a move."
                ),
            },
            glide_note={
                "type": "string",
                "description": "Why a requested glide was a jump instead.",
            },
        ),
    ),
    Capability(
        name="drag",
        method="drag",
        route="drag",
        http_method="POST",
        annotations=hints("Drag an element", destructive=True),
        response=_page(
            **{
                "from": {
                    "type": "object",
                    "description": "Where the drag started, in viewport pixels.",
                    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                },
                "to": {
                    "type": "object",
                    "description": "Where it was released, in viewport pixels.",
                    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                },
                "glided": {
                    "type": "boolean",
                    "description": "Whether the travel was incremental.",
                },
                "clamped": {
                    "type": "string",
                    "description": (
                        "Present when the destination was outside the window, so "
                        "the drag stopped at its edge."
                    ),
                },
            }
        ),
    ),
    Capability(
        name="write",
        method="write",
        route="write",
        http_method="POST",
        annotations=hints("Type text into a field"),
        response=_page(
            value={
                "type": ["string", "null"],
                "description": "The field's value read back off the element, so a "
                "caller can confirm the text landed. Null for a write whose value "
                "came from a secret — that read is not performed at all, so the "
                "credential is never returned.",
            },
            text_from={
                "type": "string",
                "description": "Present only when the value came from somewhere "
                "rather than being given: the kind of source it came from, never "
                "the value.",
            },
        ),
    ),
    Capability(
        name="press_key",
        method="press_key",
        route="press-key",
        http_method="POST",
        annotations=hints("Press a named key", destructive=True),
        response=_page(key={"type": "string", "description": "The key that was sent."}),
    ),
    Capability(
        name="extract",
        method="extract",
        route="extract",
        http_method="POST",
        annotations=hints("Read an element", read_only=True, idempotent=True),
        response=_page(
            html={"type": "string", "description": "innerHTML of the matched element."},
            text={"type": "string", "description": "Visible text of the element."},
        ),
    ),
    Capability(
        name="execute_script",
        method="execute_script",
        route="script",
        http_method="POST",
        annotations=hints("Run JavaScript in the page", destructive=True),
        response=_page(
            result={"description": "Whatever the script returned. Any JSON type."}
        ),
    ),
    # `assert` is a keyword and `print` a builtin, so these two are the
    # rows whose name cannot also be their method's.
    Capability(
        name="assert",
        method="assert_",
        route="assert",
        http_method="POST",
        annotations=hints("Assert the page is what you expect", destructive=True),
        response=_page(
            asserted={
                "type": "boolean",
                "description": (
                    "Always true: a false assertion is an error, not a result."
                ),
            },
            script={"type": "string", "description": "The expression that was true."},
            stable_for={
                "type": "number",
                "description": (
                    "Present when a hold was asked for: the seconds the answer had "
                    "to stay true, and did."
                ),
            },
        ),
    ),
    Capability(
        name="outline",
        method="outline",
        route="outline",
        http_method="POST",
        annotations=hints("Map the page's elements", read_only=True, idempotent=True),
        response=_page(
            count={"type": "integer", "description": "How many elements are listed."},
            total={
                "type": "integer",
                "description": (
                    "How many matched before limit cut the list. More than count "
                    "means scope it with selector, filter by text, or raise limit."
                ),
            },
            elements={
                "type": "array",
                "description": (
                    "What is on the page: its content first, then its navigation, "
                    "banner, footer and sidebars, each in document order."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "name": {"type": "string"},
                        "region": {
                            "type": "string",
                            "description": (
                                "The landmark it sits in: navigation, banner, "
                                "contentinfo, complementary, search or main. Absent "
                                "outside one."
                            ),
                        },
                        "css": {"type": "string"},
                        "xpath": {"type": "string"},
                        "visible": {"type": "boolean"},
                        "reason": {
                            "type": "string",
                            "description": (
                                "Why it cannot be used: hidden, covered, "
                                "zero_size, offscreen or disabled."
                            ),
                        },
                        "blocked_by": {"type": "string"},
                        "revealed_by": {
                            "type": "string",
                            "description": (
                                "Selector of the control that opens a hidden "
                                "element. This is the one to act on."
                            ),
                        },
                        "open_with": {
                            "type": "string",
                            "enum": ["click", "hover"],
                            "description": (
                                "Which gesture opens it: click when the trigger "
                                "carries aria-expanded, hover otherwise."
                            ),
                        },
                        "expanded": {"type": "boolean"},
                    },
                },
            },
        ),
    ),
    Capability(
        name="screenshot",
        method="screenshot",
        route="screenshot",
        http_method="POST",
        annotations=hints("Capture a screenshot"),
        response=_page(
            image={"type": "string", "format": "byte", "description": "Base64 PNG."},
            width={"type": "integer", "description": "Image width in pixels."},
            height={"type": "integer", "description": "Image height in pixels."},
            bytes={
                "type": "integer",
                "description": "Decoded size. A value near zero means a blank capture.",
            },
            file={"$ref": "#/components/schemas/FileEntry"},
            file_error={
                "type": "string",
                "description": (
                    "Present instead of file when the capture could not be kept. "
                    "The image is still returned."
                ),
            },
        ),
    ),
    Capability(
        name="frame",
        method="frame",
        route="frame",
        http_method="POST",
        annotations=hints("Switch into or out of an iframe", idempotent=True),
        response=_page(
            action={"type": "string", "description": "The switch that was performed."},
            in_frame={
                "type": "boolean",
                "description": "Whether the session is now inside a frame.",
            },
        ),
    ),
    # What `resize` changes outlives the page, so the record hears of it.
    Capability(
        name="resize",
        method="resize",
        route="resize",
        http_method="POST",
        reshapes=True,
        annotations=hints("Resize window", idempotent=True),
        response=_page(
            width={"type": "integer", "description": "Window width now in effect."},
            height={"type": "integer", "description": "Window height now in effect."},
        ),
    ),
    Capability(
        name="dialog",
        method="dialog",
        route="dialog",
        http_method="POST",
        annotations=hints("Answer a native dialog", destructive=True),
        response=_page(
            action={"type": "string", "description": "What was done with the dialog."},
            message={
                "type": "string",
                "description": "The dialog's text, read before it was answered.",
            },
        ),
    ),
    # `session` names the caller, not the action: a flow run injects it so a
    # step reads from the library the *run* belongs to, and only works as a
    # guard because no request body can set it too (Copilot, #31, #41).
    Capability(
        name="upload_file",
        method="upload_file",
        route="upload",
        http_method="POST",
        library_arg="session",
        annotations=hints("Attach a file to a file input"),
        response=_page(
            filename={
                "type": "string",
                "description": "The name the page sees for the attached file.",
            },
            bytes={"type": "integer", "description": "Size of the file that was sent."},
        ),
    ),
    Capability(
        name="print",
        method="print_",
        route="print",
        http_method="POST",
        annotations=hints("Print the page"),
        response=_page(
            file={"$ref": "#/components/schemas/FileEntry"},
            format={"type": "string", "enum": ["pdf", "html"]},
            bytes={"type": "integer", "description": "Size of the file in bytes."},
        ),
    ),
    Capability(
        name="save_site_data",
        method="save_site_data",
        route="save-site-data",
        http_method="POST",
        annotations=hints("Save site data", destructive=False, idempotent=True),
        response=_page(
            saved={
                "type": "object",
                "description": (
                    "What this save kept, in place of the last. Never a value."
                ),
                "properties": {
                    "cookies": {
                        "type": "integer",
                        "description": "Cookies saved, across every site.",
                    },
                    "sites": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Origins whose storage this save read and kept.",
                    },
                    "skipped": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "site": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["reason"],
                        },
                        "description": (
                            "Origins not read this time, each keeping its last saved "
                            "storage, or left out over the size cap; each with why."
                        ),
                    },
                },
            },
            uri={
                "type": "string",
                "description": "session://site-data, which lists what is saved.",
            },
        ),
    ),
    Capability(
        name="end_browser",
        method="end_browser",
        route="",
        http_method="DELETE",
        annotations=hints("End browser", destructive=True, idempotent=True),
        response={
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "session": {"type": "string"},
            },
        },
    ),
)


_BY_NAME = {row.name: row for row in CAPABILITIES}
_BY_ROUTE = {(row.route, row.http_method) for row in CAPABILITIES}
if len(_BY_NAME) != len(CAPABILITIES) or len(_BY_ROUTE) != len(CAPABILITIES):
    raise RuntimeError("two capabilities share a name, or a route and a method")


def capability(name: str) -> Capability:
    """The row for ``name``. A ``KeyError`` names an action that does not exist."""
    return _BY_NAME[name]


def method_for(name: str) -> str:
    """The ``Actions`` method that serves the capability ``name``."""
    return _BY_NAME[name].method


# ---- views -----------------------------------------------------------------
#
# Read off the table, never written beside it.

# The commands under /browser, by path: everything but the resource's own two.
ENDPOINTS = {row.route: row.name for row in CAPABILITIES if row.route}

# The one action whose choice is in its path.
(ACTION_IN_PATH,) = (row.name for row in CAPABILITIES if row.in_path)

# Per action, the argument that carries the caller's library. See `upload_file`.
LIBRARY_ARG = {row.name: row.library_arg for row in CAPABILITIES if row.library_arg}
