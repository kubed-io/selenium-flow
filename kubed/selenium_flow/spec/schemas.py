"""The response shapes and hand-written schemas the spec assembles.

Data, not logic. Request schemas come from the MCP tools themselves — see
``builder.py`` — so what lives here is the half with nothing to introspect:
what each action returns, the ops endpoints' answers, and the ``/flows`` and
``/files`` operations, which are paths carrying documents rather than an
action's arguments.

Split from the builder because fifteen literal blobs beside fourteen functions
made one file nobody could hold in their head — and because a schema is read,
while a builder is followed.
"""

from __future__ import annotations

# Fields every action echoes back, so a caller always knows where the browser
# ended up without a second call.
PAGE_STATE = {
    "url": {"type": "string", "description": "Current URL after the action."},
    "title": {"type": "string", "description": "Page title after the action."},
}


def _page(**extra) -> dict:
    return {"type": "object", "properties": {**extra, **PAGE_STATE}}


RESPONSES = {
    "open_session": {
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
            "width": {"type": "integer", "description": "Window width in use."},
            "height": {"type": "integer", "description": "Window height in use."},
            "settings": {
                "type": "object",
                "description": (
                    "The settings this session actually opened with, after the "
                    "server default / client default / explicit cascade."
                ),
            },
        },
    },
    "end_browser": {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "session": {"type": "string"},
        },
    },
    # What `GET /browser` and `session://current` answer with. Hand-written like
    # the rest of RESPONSES, and it was missing — so the published status
    # operation was an empty object and a generated client could not read it
    # (Copilot, #34).
    "current_session": {
        "type": "object",
        "properties": {
            "session": {"type": "string", "description": "The name you called with."},
            "named_by": {
                "type": "string",
                "enum": ["header", "query", "stdio", "request"],
                "description": "Which mechanism supplied the name.",
            },
            "browser": {"type": ["string", "null"]},
            "url": {"type": ["string", "null"], "description": "The page it is on."},
            "live": {
                "type": "boolean",
                "description": "Whether a browser is open for this session.",
            },
            "in_frame": {"type": ["boolean", "null"]},
            "window": {
                "type": ["string", "null"],
                "description": "Window size as WxH, when one is known.",
            },
            "store": {"type": "string", "enum": ["memory", "redis"]},
            "settings": {"type": "object"},
            "guidance": {
                "type": "string",
                "description": "The skill reference that explains sessions.",
            },
        },
    },
    "navigate": _page(),
    "interact": _page(
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
    "drag": _page(
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
    "frame": _page(
        action={"type": "string", "description": "The switch that was performed."},
        in_frame={
            "type": "boolean",
            "description": "Whether the session is now inside a frame.",
        },
    ),
    "resize": _page(
        width={"type": "integer", "description": "Window width now in effect."},
        height={"type": "integer", "description": "Window height now in effect."},
    ),
    "dialog": _page(
        action={"type": "string", "description": "What was done with the dialog."},
        message={
            "type": "string",
            "description": "The dialog's text, read before it was answered.",
        },
    ),
    "upload_file": _page(
        filename={
            "type": "string",
            "description": "The name the page sees for the attached file.",
        },
        bytes={"type": "integer", "description": "Size of the file that was sent."},
    ),
    "write": _page(
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
    "press_key": _page(key={"type": "string", "description": "The key that was sent."}),
    "extract": _page(
        html={"type": "string", "description": "innerHTML of the matched element."},
        text={"type": "string", "description": "Visible text of the element."},
    ),
    "assert": _page(
        asserted={
            "type": "boolean",
            "description": "Always true: a false assertion is an error, not a result.",
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
    "outline": _page(
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
    "execute_script": _page(
        result={"description": "Whatever the script returned. Any JSON type."}
    ),
    "screenshot": _page(
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
                "Present instead of file when the capture could not be stored. "
                "The image is still returned."
            ),
        },
    ),
    "save_pdf": _page(
        file={"$ref": "#/components/schemas/FileEntry"},
        bytes={"type": "integer", "description": "Size of the PDF in bytes."},
    ),
}

ERROR = {
    "type": "object",
    "properties": {"error": {"type": "string"}},
    "required": ["error"],
}

HEALTH = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "ok"}},
}

STARTED = {
    "type": "object",
    "properties": {"status": {"type": "string", "const": "started"}},
}

READY = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "degraded"]},
        "grid": {
            "type": "string",
            "description": "Grid hub this server dials, without any credentials.",
        },
        "grid_ready": {"type": "boolean"},
        "browsers": {"type": "integer", "description": "Browsers held Grid-wide."},
        "sessions": {
            "type": "string",
            "enum": ["memory", "redis"],
            "description": "Where session records are kept.",
        },
        "error": {"type": "string", "description": "Only present when degraded."},
    },
}

INFO = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "mount": {
            "type": "string",
            "description": "Where this server is mounted — `/` at the root.",
        },
        "mcp": {"type": "string", "description": "Path of the MCP endpoint."},
        "grid": {"type": "string"},
        "sessions": {"type": "string", "enum": ["memory", "redis"]},
    },
}

SESSION_PARAMETERS = [
    {
        "name": "X-Session-Key",
        "in": "header",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "Which session this call is about. What an admin pins inside a "
            "credential when one credential should mean one session. Sending "
            "this AND ?session= is a 400: two names is two ideas about who is "
            "calling."
        ),
    },
    {
        "name": "session",
        "in": "query",
        "required": False,
        "schema": {"type": "string"},
        "description": (
            "The same thing on the URL, for a caller that cannot set a header. "
            "Anything touching a browser needs one of the two; the flow library "
            "falls back to the shared 'global' one, which is read-only."
        ),
    },
]


FLOW_STEP = {
    "type": "object",
    "required": ["tool"],
    "description": "One tool call. See GET /flows/schema for what each tool takes.",
    # A real step, so anything generating an example from this document produces
    # something that would actually run. Sampling the properties instead yields
    # `{"tool": "…"}`, which is the right shape and names no tool that exists.
    "example": {"tool": "navigate", "args": {"url": "https://example.com"}},
    "properties": {
        "tool": {"type": "string", "description": "Which action this step runs."},
        "args": {"type": "object", "description": "That action's arguments."},
        # `secret` is not here: it is a parameter of `write`, so it lives in
        # `args` and is described by that action's own schema. A step key would
        # have been a second place to say it, and the two would drift.
        "id": {"type": "string"},
        "note": {"type": "string"},
        "onError": {"type": "string", "enum": ["abort", "continue"]},
        "return": {"type": "boolean"},
    },
}

# Said once, because a flow's document is described twice — as what a read
# returns and as what a save takes — and the save side is the one that forgot it
# (Copilot, #37).
FLOW_TIMEOUT = {
    "type": "integer",
    "minimum": 1,
    "description": (
        "Seconds the whole run may take before no further step starts. Defaults "
        "to 120; a step already running is bounded by its own wait_timeout."
    ),
}

FLOW_SCHEMAS = {
    "FlowStep": FLOW_STEP,
    "Flow": {
        "type": "object",
        "required": ["name", "steps"],
        "properties": {
            "name": {"type": "string"},
            "session": {
                "type": "string",
                "description": "The library it was read from.",
            },
            "shared": {
                "type": "boolean",
                "description": "True when it came from the shared global library.",
            },
            "description": {"type": "string"},
            "parameters": {
                "type": "object",
                "description": "JSON Schema for the values a run accepts.",
            },
            "timeout": FLOW_TIMEOUT,
            "steps": {
                "type": "array",
                "minItems": 1,
                "items": {"$ref": "#/components/schemas/FlowStep"},
            },
        },
    },
    "FlowSummary": {
        "type": "object",
        "description": "One entry in a listing. Never carries the steps.",
        "properties": {
            "name": {"type": "string"},
            "session": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {"type": "object"},
            "step_count": {"type": "integer"},
            "shared": {
                "type": "boolean",
                "description": "True when it came from the shared global library.",
            },
        },
    },
    "FlowList": {
        "type": "object",
        "properties": {
            "session": {"type": "string"},
            "count": {"type": "integer"},
            "flows": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FlowSummary"},
            },
        },
    },
    "FlowSaved": {
        "type": "object",
        "properties": {
            "saved": {"type": "boolean"},
            "session": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {"type": "object"},
            "timeout": FLOW_TIMEOUT,
            "warnings": {
                "type": "array",
                "description": (
                    "Things that are valid and probably not what was meant — a "
                    "flow whose first step acts on whatever page the browser "
                    "happens to be on. Present only when there are any; the "
                    "flow is saved either way."
                ),
                "items": {"type": "string"},
            },
            "steps": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FlowStep"},
            },
        },
    },
    "FlowRun": {
        "type": "object",
        "properties": {
            "flow": {"type": "string"},
            "session": {"type": "string"},
            "status": {"type": "string", "enum": ["ok", "failed"]},
            "hint": {
                "type": "object",
                "description": (
                    "On a failed run: where to read about this kind of failure "
                    "and which prompt repairs it. `read` is absent when the "
                    "skill is not being served."
                ),
                "properties": {
                    "read": {
                        "type": "string",
                        "description": "A skill:// resource URI, exactly as read.",
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "The heading in that reference, when there is one."
                        ),
                    },
                    "prompt": {"type": "string"},
                    "arguments": {"type": "object"},
                },
            },
            "steps_run": {"type": "integer"},
            "steps_total": {"type": "integer"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "id": {"type": "string"},
                        "tool": {"type": "string"},
                        "ok": {"type": "boolean"},
                        "summary": {"type": "string"},
                        "note": {"type": "string"},
                        "error": {"type": "string"},
                        "url": {
                            "type": "string",
                            "description": (
                                "The page this step ended on, present only when "
                                "it differs from the step before — so silence "
                                "means the page did not change. Withheld when "
                                "the URL could carry a value typed from a "
                                "secret."
                            ),
                        },
                        "file": {
                            "allOf": [
                                {"$ref": "#/components/schemas/FileEntry"}
                            ],
                            "description": (
                                "The file this step produced — a screenshot, a "
                                "PDF — present whether or not the step is "
                                "marked `return`, because a capture nobody can "
                                "find cannot show anyone what it saw. Withheld "
                                "on the same terms as `url`."
                            ),
                        },
                        "result": {
                            "type": "object",
                            "description": (
                                "Present for a step marked return: true, or "
                                "every step when verbose was set. This is the "
                                "only place a result appears: a run answers "
                                "with the steps that said they were the "
                                "answer, and any number of them may."
                            ),
                        },
                    },
                },
            },
            "url": {"type": "string"},
            "title": {"type": "string"},
        },
    },
    "FlowDeleted": {
        "type": "object",
        "properties": {
            "deleted": {
                "type": "boolean",
                "description": (
                    "False when there was no such flow, which is not an error."
                ),
            },
            "session": {"type": "string"},
            "name": {"type": "string"},
        },
    },
}

_SESSION = {
    "session": {
        "type": "string",
        "description": (
            "Whose library. Defaults to the caller's session name if the "
            "request carries one, else the shared 'global' library."
        ),
    }
}

_WRITE_SESSION = {
    "session": {
        "type": "string",
        "description": (
            "Whose library to write to. Needed unless the request names a "
            "session another way, with the X-Session-Key header: the shared "
            "'global' library is read-only, so a write that resolves to it is "
            "refused rather than defaulted."
        ),
    }
}

_FLOW_OPERATIONS = {
    "list": (
        "listFlows",
        "Every flow this session can run.",
        "Its own, plus the shared global library. A flow of its own wins a name "
        "collision, and each entry says which library it came from.",
        {"type": "object", "properties": dict(_SESSION)},
        "FlowList",
    ),
    "get": (
        "getFlow",
        "One saved flow, with its steps.",
        "Falls back to the shared library when this session has no flow of that "
        "name.",
        {
            "type": "object",
            "required": ["name"],
            "properties": {**_SESSION, "name": {"type": "string"}},
        },
        "Flow",
    ),
    "save": (
        "saveFlow",
        "Create or replace a flow.",
        "The same name updates, a new one creates. Always writes to this "
        "session's own library, never the shared one — so `session` is required "
        "in practice: the shared `global` library is read-only, because every "
        "session lists and runs what is in it. The document is validated "
        "against the live tools and a refusal lists every problem at once.",
        {
            "type": "object",
            "required": ["name", "steps"],
            "properties": {
                **_WRITE_SESSION,
                "name": {"type": "string"},
                "description": {"type": "string"},
                "parameters": {"type": "object"},
                "timeout": FLOW_TIMEOUT,
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"$ref": "#/components/schemas/FlowStep"},
                },
            },
        },
        "FlowSaved",
    ),
    "delete": (
        "deleteFlow",
        "Delete one of this session's flows.",
        "Deleting one that is not there is not an error. A flow in the shared "
        "`global` library is not yours to delete — every session runs those, so "
        "one vanishing mid-run would break somebody else's work — and asking is "
        "refused rather than silently ignored.",
        {
            "type": "object",
            "required": ["name"],
            "properties": {**_WRITE_SESSION, "name": {"type": "string"}},
        },
        "FlowDeleted",
    ),
    "run": (
        "runFlow",
        "Run a saved flow.",
        "Every step, in order, server-side, against this session's browser. "
        "Stops at the first failing step unless that step says "
        "onError: continue, and reports which step stopped it and what page the "
        "browser was on. Returns a line per step; pass verbose for every step's "
        "full result.",
        {
            "type": "object",
            "required": ["name"],
            "properties": {
                "params": {
                    "type": "object",
                    "description": "The values this flow declares.",
                },
                "verbose": {"type": "boolean", "default": False},
            },
        },
        "FlowRun",
    ),
    "schema": (
        "flowSchema",
        "The shape of a flow document.",
        "Every tool that may be a step and the parameters each takes, derived "
        "from the live tools — so it cannot describe a step that would not run.",
        {"type": "object", "properties": {}},
        None,
    ),
}


FILE_SCHEMAS = {
    "FileEntry": {
        "type": "object",
        "description": (
            "One file a session has. Both halves of the list share this shape — "
            "`kept` is what distinguishes them."
        ),
        "properties": {
            "name": {"type": "string"},
            "size": {"type": "integer"},
            "created": {
                "type": ["integer", "null"],
                "description": "When it was written, in epoch milliseconds.",
            },
            "content_type": {"type": "string"},
            "image": {
                "type": "boolean",
                "description": "Whether it can be displayed inline.",
            },
            "kept": {
                "type": "boolean",
                "description": (
                    "True when it belongs to the session and outlives the "
                    "browser. False when it is a download, which the Grid "
                    "deletes with the browser and cannot delete singly."
                ),
            },
            "keep_with": {
                "type": "string",
                "description": (
                    "Present only when `kept` is false: the MCP call that "
                    "makes a copy outliving the browser. Until it is made, "
                    "this file's url stops working when the browser ends. The "
                    "HTTP equivalent is PUT /files/{name}/kept."
                ),
            },
            "url": {
                "type": "string",
                "description": "Signed and time-limited; needs no bearer token.",
            },
            "absolute_url": {
                "type": "string",
                "description": (
                    "The same URL made absolute, when PUBLIC_BASE_URL is set."
                ),
            },
        },
    },
    "FileList": {
        "type": "object",
        "properties": {
            "component": {"type": "string"},
            "session": {
                "type": ["string", "null"],
                "description": "The session these files belong to.",
            },
            "count": {"type": "integer"},
            "files": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/FileEntry"},
            },
        },
    },
    "FileKept": {
        "type": "object",
        "properties": {
            "kept": {"type": "boolean"},
            "session": {"type": "string"},
            "name": {"type": "string"},
            "size": {"type": "integer"},
            "creationTime": {"type": "integer"},
        },
    },
}

_FILE_OPERATIONS = {
    "list": (
        "listFiles",
        "Every file this session has.",
        "The browser's downloads and the session's kept files as one list, "
        "newest first, each entry saying which it is. A name in both resolves "
        "to the kept one. Works after the browser is gone, returning the kept "
        "files alone.",
        {"type": "object", "properties": {}},
        "FileList",
    ),
    "keep": (
        "keepFile",
        "Keep one download beyond its browser.",
        "Copies the file out of the Grid's store onto the server, where it "
        "survives the browser. Keeping a name that is already kept replaces it. "
        "The original download stays: the Grid offers no way to remove one file.",
        {"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string"},
        }},
        "FileKept",
    ),
}
