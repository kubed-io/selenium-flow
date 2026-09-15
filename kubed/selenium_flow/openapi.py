"""The OpenAPI description of the HTTP surface.

Generated, never hand-written. Request schemas come straight from the MCP tools
— the same objects FastMCP publishes to agents — so the two contracts are not
merely consistent, they are the same schema. Adding a parameter to a tool
changes this document with no further work.

Response shapes are the one hand-maintained half: the actions return plain
dicts, so there is nothing to introspect. ``RESPONSES`` below is that
declaration, and a test asserts every endpoint has one.

**No transform is applied on the way through, and that is new.** There used to
be three sanctioned differences, all of them consequences of the HTTP surface
having no session of its own: ``session_id`` became required, ``fresh`` was
dropped because there was no session to be fresh *from*, and ``upload_file``
gained a ``session`` field so a kept file could name a library. §F2.12 and
§F2.13 removed the cause rather than the symptoms — both surfaces now name a
session the same way — so a request body here is exactly the tool's schema, and
a test asserts it.

What is described here and not in a tool schema is the session itself, as the
header and query parameter every operation takes. That is not a body field on
either surface, which is the whole point of it.

What is described *differently* is the shape, and only the shape: a tool is a
verb with arguments, an endpoint is a path with a method. Those come from the
route tables — ``routes.ENDPOINTS``, ``flowapi.FLOW_ROUTES``,
``files.FILE_ROUTES`` — so this document cannot describe a path that is not
served.
"""

from __future__ import annotations

import copy
from importlib.metadata import PackageNotFoundError, version

from fastmcp import FastMCP

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
        elements={
            "type": "array",
            "description": "What is on the page, in document order.",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "name": {"type": "string"},
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

# One schema per question, because the ops endpoints answer different ones.
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

DESCRIPTION = """\
Drive a persistent browser on Selenium Grid over plain HTTP.

Every operation here is also an MCP tool at `{mount}/mcp`, backed by the same code —
the request schemas in this document are generated from those tools, so the two
surfaces cannot describe different things.

Name your session on every request — an `X-Session-Key` header or `?session=`
— and every call is about that session's browser. Sending both is a 400, and so
is sending neither on anything that touches a browser. There is no browser id
in this API.

`POST {mount}/browser` opens yours, `DELETE {mount}/browser` ends it when you
are finished — do that even after a failure, or it holds a Grid slot until it
times out.
"""


# What the committed openapi.yaml carries instead of a real version. The field
# is required, so it cannot be omitted, but a real value would churn the file on
# every commit.
PLACEHOLDER_VERSION = "0.0.0"


def _version() -> str:
    try:
        return version("kubed-selenium-flow")
    except PackageNotFoundError:
        return "0.0.0"


async def build_spec(
    mcp: FastMCP,
    endpoints: dict[str, str],
    prefix: str,
    authenticated: bool,
) -> dict:
    """Assemble the OpenAPI 3.1 document for the HTTP surface.

    3.1 rather than 3.0 on purpose: it is a strict superset of JSON Schema, so
    the tool schemas can be embedded verbatim instead of being down-converted.

    ``prefix`` is where the whole server is mounted (§F1.11). Every tree hangs
    off it, `/openapi.*` included. Only the four probes — `/health`, `/started`,
    `/ready`, `/info` — also answer at the root, so a probe never depends on it.
    """
    browser_root = f"{prefix}/browser"
    # get_tool, not list_tools. A listing is shaped for whoever is asking, and
    # this document describes the surface rather than one client's view of it —
    # building from a listing is how the spec once omitted a field every
    # endpoint required. Not guarded: an action in the route table with no tool
    # behind it is a broken build, and a document quietly missing an endpoint is
    # how that omission survived as long as it did.
    tools = {action: await mcp.get_tool(action) for action in set(endpoints.values())}

    schemas: dict[str, dict] = {"Error": ERROR, "Health": HEALTH}
    paths: dict[str, dict] = {}

    from .routes import ACTION_IN_PATH

    for path, action in endpoints.items():
        tool = tools.get(action)
        if tool is None:  # pragma: no cover - the surfaces test forbids this
            continue

        request_name = f"{_camel(action)}Request"
        response_name = f"{_camel(action)}Response"
        # Verbatim: the body an endpoint accepts IS the tool's schema now.
        request, nested = _hoisted(copy.deepcopy(tool.parameters))
        schemas.update(nested)
        schemas[request_name] = request
        schemas[response_name] = RESPONSES.get(action, {"type": "object"})

        in_path = action == ACTION_IN_PATH
        route = f"{browser_root}/{path}" + ("/{action}" if in_path else "")
        parameters = list(SESSION_PARAMETERS)
        if in_path:
            # The mouse action is the path, not a field: /browser/interact/click
            # reads as the thing it does, and the enum is already closed.
            choice = dict(request.get("properties", {}).get("action", {}))
            request = _without(request, "action")
            schemas[request_name] = request
            parameters = [
                {
                    "name": "action",
                    "in": "path",
                    "required": True,
                    "schema": {k: v for k, v in choice.items() if k != "description"}
                    or {"type": "string"},
                    "description": choice.get("description", ""),
                },
                *parameters,
            ]

        paths[route] = {
            "post": {
                "operationId": action,
                "parameters": parameters,
                # The MCP tool this endpoint is the other half of. Redundant for
                # a browser action, where the two names are the same by
                # construction — and stated anyway, so a reader of the document
                # never has to know which surface a name came from. See
                # `_mcp_tools` for why the other two surfaces need it.
                "x-mcp-tool": action,
                "summary": _summary(tool.description),
                "description": tool.description or "",
                "tags": ["browser"],
                "requestBody": {
                    "required": True,
                    "content": _request_content(action, request_name),
                },
                "responses": {
                    "200": {
                        "description": "The action succeeded.",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": f"#/components/schemas/{response_name}"
                                }
                            }
                        },
                    },
                    # These are a contract with a machine, so they are split by
                    # what the caller should DO, not by what went wrong. See
                    # errors.py.
                    "400": _error(
                        "The request cannot succeed as sent — a missing field, "
                        "a value that was rejected, or a locator that matched "
                        "nothing before the wait ran out. Do not retry it "
                        "unchanged."
                    ),
                    "401": _error("Missing or wrong bearer token."),
                    "404": _error(
                        "No such browser session. It ended, the Grid reaped it, "
                        "or the id was never real. Open a new one and retry."
                    ),
                    "500": _error("Something failed that this server did not expect."),
                    "503": _error(
                        "The Grid could not serve this — unreachable, or no free "
                        "slot for a new browser. Worth retrying after a wait."
                    ),
                },
            }
        }

    # The browser this caller holds: one resource, three methods. Not one path
    # per verb, because which browser is a question about who is asking and the
    # answer is in the header (§F2.13).
    resource = (
        (
            "open_session",
            "post",
            "Open this session's browser, or pick up the one it was using.",
        ),
        (
            "end_browser",
            "delete",
            "Quit the browser, keeping the session and its context.",
        ),
    )
    for action, method, summary in resource:
        tool = await mcp.get_tool(action)
        request_name = f"{_camel(action)}Request"
        response_name = f"{_camel(action)}Response"
        request, nested = _hoisted(copy.deepcopy(tool.parameters))
        schemas.update(nested)
        schemas[request_name] = request
        schemas[response_name] = RESPONSES.get(action, {"type": "object"})
        paths.setdefault(browser_root, {})[method] = {
            "operationId": action,
            "x-mcp-tool": action,
            "parameters": list(SESSION_PARAMETERS),
            "summary": summary,
            "description": tool.description or "",
            "tags": ["browser"],
            "requestBody": {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{request_name}"}
                    }
                },
            },
            "responses": {
                "200": {
                    "description": summary,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{response_name}"}
                        }
                    },
                },
                "400": _error(
                    "The request cannot succeed as sent — no session named, two "
                    "names given, or a setting that was rejected."
                ),
                "401": _error("Missing or wrong bearer token."),
                "503": _error(
                    "The Grid could not serve this — unreachable, or no free "
                    "slot for a new browser. Worth retrying after a wait."
                ),
            },
        }

    status_tool = await mcp.get_tool("current_session")
    schemas["SessionStatus"] = RESPONSES["current_session"]
    paths.setdefault(browser_root, {})["get"] = {
        "operationId": "currentSession",
        "x-mcp-tool": "current_session",
        "parameters": list(SESSION_PARAMETERS),
        "summary": "What this session is, and whether it holds a browser.",
        "description": status_tool.description or "",
        "tags": ["browser"],
        "responses": {
            "200": {
                "description": "The session's current state. Opens nothing.",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/SessionStatus"}
                    }
                },
            },
            "400": _error("No session named, or two names given."),
            "401": _error("Missing or wrong bearer token."),
        },
    }

    schemas.update(FLOW_SCHEMAS)
    paths.update(_flow_paths(prefix))
    schemas.update(FILE_SCHEMAS)
    paths.update(_file_paths(prefix))

    # The ops endpoints. One per question a probe asks — `/openapi.json` was
    # standing in for a liveness probe in this cluster, which it is not (Dr K).
    # Each answers at the root as well as here, for a reader that did not choose
    # the mount; the document names the mounted one.
    schemas["Started"] = STARTED
    schemas["Ready"] = READY
    schemas["Info"] = INFO
    ops = (
        (
            "health",
            "Liveness: is this process still working?",
            "Says nothing about the Grid deliberately — a liveness probe that "
            "failed on a Grid outage would restart every replica for a fault in "
            "another service. Also answers at the root, whatever the mount.",
            "Health",
            False,
        ),
        (
            "started",
            "Startup: has this process finished coming up?",
            "Answers once the routes are registered. Separate from liveness "
            "because the two differ in what a failure means: not yet, versus no "
            "longer.",
            "Started",
            False,
        ),
        (
            "ready",
            "Readiness: can this process serve a request right now?",
            "The one that depends on the Grid: a server that cannot reach one "
            "can accept a call and do nothing with it. A 503 takes the pod out "
            "of the Service and leaves it running.",
            "Ready",
            True,
        ),
        (
            "info",
            "What this process is: version, mount, and what it is wired to.",
            "Not a probe — the question an operator asks when a call went "
            "somewhere unexpected. The Grid is named without its credentials.",
            "Info",
            False,
        ),
    )
    for name, summary, description, schema_name, grid in ops:
        answers = {
            "200": {
                "description": summary,
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                    }
                },
            }
        }
        if grid:
            answers["503"] = {
                "description": "The Grid is unreachable or not ready.",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Ready"}
                    }
                },
            }
        paths[f"{prefix}/{name}"] = {
            "get": {
                "operationId": name,
                "summary": summary,
                "description": description,
                "tags": ["ops"],
                "security": [],
                "responses": answers,
            }
        }

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Selenium Flow",
            "version": _version(),
            "description": DESCRIPTION.format(mount=prefix),
            # identifier, not just name: an SPDX id is machine-readable, and
            # redocly's info-license-strict rule warns without one.
            "license": {"name": "MIT", "identifier": "MIT"},
        },
        # Declared because a spec without servers cannot be exercised from a
        # docs UI or a generated client, and redocly rejects an empty list.
        # The prefix is in the paths rather than in these, deliberately: a
        # server URL is where this process answers, and the mount point is part
        # of every path it serves — including for a reader who pastes one into
        # something that has never heard of `servers`.
        "servers": [
            {
                "url": "http://selenium-flow.flow.svc.cluster.local:8000",
                "description": "In-cluster Service.",
            },
            {
                "url": "http://localhost:8000",
                "description": "docker compose, where auth is off by default.",
            },
        ],
        "tags": [
            {"name": "browser", "description": "Browser actions."},
            {
                "name": "flows",
                "description": (
                    "Saved sequences of browser actions. A layer above "
                    "/browser: these are about documents that contain actions."
                ),
            },
            {
                "name": "files",
                "description": (
                    "What a session has downloaded, and what it has kept "
                    "beyond the browser that downloaded it."
                ),
            },
            {"name": "ops", "description": "Operational endpoints."},
        ],
        "paths": paths,
        "components": {"schemas": dict(sorted(schemas.items()))},
    }

    if authenticated:
        spec["components"]["securitySchemes"] = {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "The token from MCP_AUTH_TOKEN. The bare token is also "
                    "accepted as the Authorization value, for clients that "
                    "cannot express a scheme."
                ),
            }
        }
        spec["security"] = [{"bearerAuth": []}]

    return spec


def _request_content(action: str, request_name: str) -> dict:
    """The accepted request bodies for one endpoint.

    Everything takes JSON. Uploading additionally takes a multipart form,
    because sending a file over HTTP should be a file, not base64 wrapped in
    JSON — that shape only exists because MCP tool arguments must be JSON.
    """
    content = {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{request_name}"}}
    }
    if action == "upload_file":
        content["multipart/form-data"] = {
            "schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": (
                            "The element, as JSON: {\"css\": \"input[type=file]\"} "
                            "or {\"xpath\": \"//input\"}. A form field carries "
                            "text, so this one is the object encoded rather than "
                            "nested."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "format": "binary",
                        "description": "The file itself, as a normal file part.",
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "The file's content as plain text, instead of a "
                            "file part. For JSON, CSV, YAML and similar."
                        ),
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "Overrides the part's own filename. Its extension "
                            "is what sets the MIME type the page reports."
                        ),
                    },
                    "mime_type": {
                        "type": "string",
                        "description": "Picks an extension when filename has none.",
                    },
                    "kept": {
                        "type": "string",
                        "description": (
                            "The name of a file keep_file has kept, instead of "
                            "sending any bytes at all. Exactly one source: a "
                            "content part, text, kept, or path."
                        ),
                    },
                    "url": {"type": "string"},
                    "wait_timeout": {"type": "integer"},
                },
                # Nothing is required: the input is addressed by EITHER xpath
                # OR css, which this hand-written schema cannot say without a
                # oneOf, and the session is a header rather than a field.
                # `browser.locator` enforces the selector rule at the boundary
                # and returns a 400 naming both, exactly as it does for a JSON
                # body.
            }
        }
    return content


# The session every operation is about, named the way §F2.13 says: a header, or
# a query parameter, and never a body field or a path segment. Published on
# every operation because it is how a caller says who it is, and a generated
# client that cannot see it cannot hold a session at all.
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


def _named_in_path(template: str) -> list[dict]:
    """The path parameters a route template declares."""
    if "{name}" not in template:
        return []
    return [
        {
            "name": "name",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
    ]


def _without(schema: dict, *names: str) -> dict:
    """A request schema with fields that are no longer body fields removed.

    `name` moved into the path and `session`/`session_id` into the header, so
    publishing them here would describe a request nothing accepts.
    """
    schema = copy.deepcopy(schema)
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for name in names:
        properties.pop(name, None)
        if name in required:
            required.remove(name)
    if not required:
        schema.pop("required", None)
    return schema


def _error(description: str) -> dict:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
        },
    }


def _summary(description: str | None) -> str:
    """First sentence of the tool description, which is written as a summary."""
    return (description or "").strip().split("\n", 1)[0].strip()


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


# ---------------------------------------------------------------------------
# The /flows surface, written by hand.
#
# Unlike the browser endpoints, these are not derived from tool schemas: their
# request bodies are a document plus a session name, not an action's arguments.
# That makes them the same kind of liability `_request_content`'s multipart form
# turned out to be — a hand-written schema next to derived ones drifts, quietly,
# and only a test notices. `test_openapi.py` holds the path list to
# `flowapi.FLOW_ENDPOINTS` for exactly that reason.

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

FLOW_SCHEMAS = {
    "FlowStep": FLOW_STEP,
    "Flow": {
        "type": "object",
        "required": ["name", "steps"],
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "parameters": {
                "type": "object",
                "description": "JSON Schema for the values a run accepts.",
            },
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

# A session name is optional everywhere and defaults to the shared library, the
# same way it does on the MCP surface for a caller with no name of its own.
_SESSION = {
    "session": {
        "type": "string",
        "description": (
            "Whose library. Defaults to the caller's session name if the "
            "request carries one, else the shared 'global' library."
        ),
    }
}

# The write endpoints need a different sentence. Falling back to `global` is
# right for a read and is a *refusal* for a write, so advertising the same
# default on both would hand a generated client a 400 it had no way to see
# coming. It is still not `required`, because a caller naming itself through
# the X-Session-Key header legitimately omits it.
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


def _mcp_tools() -> tuple[dict, dict]:
    """The MCP tool behind each ``/flows`` and ``/files`` endpoint.

    Imported here rather than at module scope because the import runs the other
    way at load time: ``routes`` imports ``build_spec`` from this module and
    ``flowapi`` imports ``ENDPOINTS`` from ``routes``, so naming either one up
    top closes the loop. Reading the constants is still the point — an endpoint
    and its tool are one action, and a second hand-written copy of these six
    names is how the wiki ends up generating a page called ``saveFlow`` for a
    tool nobody can call.
    """
    from . import files as files_module
    from . import flowapi

    return (
        {
            "list": flowapi.LIST_TOOL,
            "get": flowapi.GET_TOOL,
            "save": flowapi.SAVE_TOOL,
            "delete": flowapi.DELETE_TOOL,
            "run": flowapi.RUN_TOOL,
            "schema": flowapi.SCHEMA_TOOL,
        },
        {"list": files_module.FILES_TOOL, "keep": files_module.KEEP_TOOL},
    )


def _flow_paths(prefix: str = "") -> dict:
    """The flow library as resources, from `flowapi.FLOW_ROUTES`."""
    from .flowapi import FLOW_ROUTES

    tools, _ = _mcp_tools()
    paths: dict = {}
    for path, (op, summary, description, request, response) in _FLOW_OPERATIONS.items():
        method, template = FLOW_ROUTES[path]
        route = f"{prefix}{template}" if template.startswith("/schemas") else (
            f"{prefix}/flows{template}"
        )
        schema = (
            {"$ref": f"#/components/schemas/{response}"}
            if response
            else {"type": "object"}
        )
        # `name` is the path and the session is a header, so neither is a body
        # field any more.
        request = _without(request, "name", "session", "session_id")
        has_body = method in ("post", "put")
        operation = {
            "operationId": op,
            "x-mcp-tool": tools[path],
            "parameters": [*_named_in_path(template), *SESSION_PARAMETERS],
            "summary": summary,
            "description": description,
            "tags": ["flows"],
            "responses": {
                    "200": {
                        "description": summary,
                        "content": {"application/json": {"schema": schema}},
                    },
                    "400": _error(
                        "The request cannot succeed as sent — an unusable name, "
                        "a flow that does not exist, a document that would not "
                        "run, or a write aimed at the read-only shared library. "
                        "Do not retry it unchanged."
                    ),
                "401": _error("Missing or wrong bearer token."),
                "500": _error("Something failed that this server did not expect."),
            },
        }
        if has_body:
            operation["requestBody"] = {
                "required": method == "put",
                "content": {"application/json": {"schema": request}},
            }
        paths.setdefault(route, {})[method] = operation
    return paths


# ---------------------------------------------------------------------------
# The /files surface, written by hand for the same reason /flows is: these take
# a file name and a session, not an action's arguments, so there is no tool
# schema to derive them from. `test_kept_files.py` holds the path list to
# `files.FILE_ENDPOINTS` so a new one cannot go undocumented.

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

# path -> (operationId, summary, description, request, response). Every one of
# these dials the Grid, so they all carry its failure modes; deleting a kept
# file never leaves this server, and is deliberately not here — it is an
# operator action on the admin surface, with no tool and so no endpoint.
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


def _file_paths(prefix: str = "") -> dict:
    """A session's files as resources, from `files.FILE_ROUTES`."""
    from .files import FILE_ROUTES

    _, tools = _mcp_tools()
    paths = {}
    for path, (op, summary, description, request, response) in (
        _FILE_OPERATIONS.items()
    ):
        responses = {
            "200": {
                "description": summary,
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{response}"}
                    }
                },
            },
            "400": _error(
                "The request cannot succeed as sent — an unusable name, or a "
                "server with no FLOW_DATA_DIR to keep files in. Do not retry it "
                "unchanged."
            ),
            "401": _error("Missing or wrong bearer token."),
            "404": _error(
                "No such browser session, or no such file in it. It ended, the "
                "Grid reaped it, or the id was never real."
            ),
            "500": _error("Something failed that this server did not expect."),
            "503": _error(
                "The Grid could not serve this — unreachable, or failing. Worth "
                "retrying after a wait."
            ),
        }
        method, template = FILE_ROUTES[path]
        operation = {
            "operationId": op,
            "x-mcp-tool": tools[path],
            "parameters": [*_named_in_path(template), *SESSION_PARAMETERS],
            "summary": summary,
            "description": description,
            "tags": ["files"],
            "responses": responses,
        }
        if method in ("post", "put"):
            operation["requestBody"] = {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": _without(request, "name", "session", "session_id")
                    }
                },
            }
        paths.setdefault(f"{prefix}/files{template}", {})[method] = operation
    return paths


def _hoisted(schema: dict) -> tuple[dict, dict]:
    """A schema with its ``$defs`` lifted into ``components/schemas``.

    Pydantic writes a nested model as ``$ref: '#/$defs/ValueFrom'`` with the
    definition alongside. A ``$ref`` resolves against the **document root**, and
    once the schema is a component there is no ``$defs`` there — so the
    reference points at nothing and a strict reader rejects the whole document.

    Lifting rather than inlining, so a model used by two actions is described
    once and a generated client gets one class for it.
    """
    definitions = schema.pop("$defs", None)
    if not definitions:
        return schema, {}
    return _repointed(schema), {
        name: _repointed(body) for name, body in definitions.items()
    }


def _repointed(node):
    """``node`` with every ``#/$defs/x`` reference aimed at the components."""
    if isinstance(node, dict):
        return {
            key: (
                value.replace("#/$defs/", "#/components/schemas/")
                if key == "$ref" and isinstance(value, str)
                else _repointed(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_repointed(item) for item in node]
    return node
