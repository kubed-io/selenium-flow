"""The OpenAPI description of the HTTP surface.

Generated, never hand-written. Request schemas come straight from the MCP tools
— the same objects FastMCP publishes to agents — so the two contracts are not
merely consistent, they are the same schema. Adding a parameter to a tool
changes this document with no further work.

Response shapes are the one hand-maintained half: the actions return plain
dicts, so there is nothing to introspect. ``RESPONSES`` below is that
declaration, and a test asserts every endpoint has one.

One transform is applied on the way through. Saved sessions let an MCP caller
omit ``session_id``, so the tool schema marks it optional. The HTTP endpoints
never do that — they take a session in and give one back so the caller owns it —
so ``_http_schema`` puts it back as required. That is the single sanctioned
difference between the two schemas, and a test pins it.
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
            "session_id": {
                "type": "string",
                "description": "Pass this to every other call.",
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
            "session_id": {"type": "string"},
        },
    },
    "navigate": _page(),
    "interact": _page(
        action={"type": "string", "description": "The gesture that was performed."}
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
        file={
            "type": "object",
            "description": "The stored file, as the Grid's download store lists it.",
            "properties": {
                "name": {"type": "string"},
                "size": {"type": "integer"},
                "creationTime": {"type": "integer"},
            },
        },
    ),
    "save_pdf": _page(
        file={
            "type": "object",
            "description": "The stored file, as the Grid's download store lists it.",
            "properties": {
                "name": {"type": "string"},
                "size": {"type": "integer"},
                "creationTime": {"type": "integer"},
            },
        },
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
    "properties": {
        "status": {"type": "string", "enum": ["ok", "degraded"]},
        "grid": {"type": "string", "description": "Grid hub URL this server dials."},
        "grid_ready": {"type": "boolean"},
        "sessions": {"type": "integer", "description": "Sessions held Grid-wide."},
        "saved_sessions": {
            "type": "string",
            "enum": ["disabled", "memory", "redis"],
            "description": (
                "Backend remembering a browser per MCP session. Affects MCP "
                "callers only — these endpoints are always explicit."
            ),
        },
        "error": {"type": "string", "description": "Only present when degraded."},
    },
}

DESCRIPTION = """\
Drive a persistent browser on Selenium Grid over plain HTTP.

Every operation here is also an MCP tool at `/mcp`, backed by the same code —
the request schemas in this document are generated from those tools, so the two
surfaces cannot describe different things.

Call `/browser/open` first and pass the `session_id` it returns to every other
call; nothing is stored server-side. Call `/browser/end` when finished,
including after a failure, or the browser holds a Grid slot until it times out.

`/browser/close` is the old name for `/browser/end` and still works. It is not
listed here, so that this document describes one name per action.
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
    """
    # get_tool, not list_tools. A listing is shaped for whoever is asking —
    # ShapeSessionId removes session_id when the server can identify the caller,
    # and outside a request that is always true, so building from the listing
    # produced a document that omitted the one field every endpoint requires.
    # This spec describes the HTTP surface, which has no caller to adapt to.
    # Not guarded: an action in the route table with no tool behind it is a
    # broken build, and this document quietly missing an endpoint is how the
    # session_id omission survived for as long as it did.
    tools = {action: await mcp.get_tool(action) for action in set(endpoints.values())}

    schemas: dict[str, dict] = {"Error": ERROR, "Health": HEALTH}
    paths: dict[str, dict] = {}

    for path, action in endpoints.items():
        tool = tools.get(action)
        if tool is None:  # pragma: no cover - the surfaces test forbids this
            continue

        request_name = f"{_camel(action)}Request"
        response_name = f"{_camel(action)}Response"
        request, nested = _hoisted(http_schema(tool.parameters))
        schemas.update(nested)
        schemas[request_name] = request
        schemas[response_name] = RESPONSES.get(action, {"type": "object"})

        paths[f"{prefix}/{path}"] = {
            "post": {
                "operationId": action,
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

    schemas.update(FLOW_SCHEMAS)
    paths.update(_flow_paths())
    schemas.update(FILE_SCHEMAS)
    paths.update(_file_paths())

    paths["/health"] = {
        "get": {
            "operationId": "health",
            "summary": "Readiness probe.",
            "description": (
                "Reports Grid reachability, not just process liveness, so a "
                "server that cannot see a Grid is correctly not ready. "
                "Unauthenticated so a kubelet can call it."
            ),
            "tags": ["ops"],
            "security": [],
            "responses": {
                "200": {
                    "description": "Server and Grid are both up.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Health"}
                        }
                    },
                },
                "503": {
                    "description": "The Grid is unreachable or not ready.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Health"}
                        }
                    },
                },
            },
        }
    }

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Selenium Flow",
            "version": _version(),
            "description": DESCRIPTION,
            # identifier, not just name: an SPDX id is machine-readable, and
            # redocly's info-license-strict rule warns without one.
            "license": {"name": "MIT", "identifier": "MIT"},
        },
        # Declared because a spec without servers cannot be exercised from a
        # docs UI or a generated client, and redocly rejects an empty list.
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
                    "session_id": {"type": "string"},
                    "xpath": {"type": "string"},
                    "css": {"type": "string"},
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
                    "url": {"type": "string"},
                    "wait_timeout": {"type": "integer"},
                },
                # Not the selector: the input is addressed by EITHER xpath OR
                # css, which this hand-written schema cannot say without a
                # oneOf. `browser.locator` enforces it at the boundary and
                # returns a 400 naming both, exactly as it does for the JSON
                # body. Requiring `xpath` here would publish a contract that
                # forbids a call the route accepts.
                "required": ["session_id"],
            }
        }
    return content


def http_schema(tool_schema: dict) -> dict:
    """A tool's schema as the HTTP surface actually accepts it.

    Only one thing changes: ``session_id`` becomes required and loses its null
    branch. MCP callers may omit it because saved sessions can supply it; an
    endpoint has no session to draw on and must be told.
    """
    schema = copy.deepcopy(tool_schema)
    prop = schema.get("properties", {}).get("session_id")
    if prop is None:
        return schema

    required = schema.setdefault("required", [])
    if "session_id" not in required:
        required.insert(0, "session_id")

    branches = [b for b in prop.get("anyOf", []) if b.get("type") != "null"]
    if len(branches) == 1:
        prop.clear()
        prop.update(branches[0])
    prop.pop("default", None)
    prop.setdefault(
        "description", "The session_id returned by /browser/open. Required here."
    )
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
        "Every step, in order, server-side, against the browser named by "
        "session_id. Stops at the first failing step unless that step says "
        "onError: continue, and reports which step stopped it and what page the "
        "browser was on. Returns a line per step; pass verbose for every step's "
        "full result.",
        {
            "type": "object",
            "required": ["name", "session_id"],
            "properties": {
                **_SESSION,
                "name": {"type": "string"},
                "session_id": {
                    "type": "string",
                    "description": (
                        "The browser to run in. Required: this surface is "
                        "always explicit, so open one with /browser/open first."
                    ),
                },
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


def _flow_paths(prefix: str = "/flows") -> dict:
    """The five /flows endpoints."""
    tools, _ = _mcp_tools()
    paths = {}
    for path, (op, summary, description, request, response) in _FLOW_OPERATIONS.items():
        schema = (
            {"$ref": f"#/components/schemas/{response}"}
            if response
            else {"type": "object"}
        )
        paths[f"{prefix}/{path}"] = {
            "post": {
                "operationId": op,
                "x-mcp-tool": tools[path],
                "summary": summary,
                "description": description,
                "tags": ["flows"],
                "requestBody": {
                    "required": path not in ("list", "schema"),
                    "content": {"application/json": {"schema": request}},
                },
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
        }
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
            "session_id": {
                "type": ["string", "null"],
                "description": "The browser these downloads belong to, if any.",
            },
            "session": {
                "type": ["string", "null"],
                "description": "The session whose kept files these are.",
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

_FILE_SESSION = {
    "session": {
        "type": "string",
        "description": (
            "Whose kept files. Defaults to the caller's session name if the "
            "request carries one, else the shared 'global' session."
        ),
    }
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
        {
            "type": "object",
            "properties": {**_FILE_SESSION, "session_id": {"type": "string"}},
        },
        "FileList",
    ),
    "keep": (
        "keepFile",
        "Keep one download beyond its browser.",
        "Copies the file out of the Grid's store onto the server, where it "
        "survives the browser. Keeping a name that is already kept replaces it. "
        "The original download stays: the Grid offers no way to remove one file.",
        {
            "type": "object",
            "required": ["session_id", "name"],
            "properties": {
                **_FILE_SESSION,
                "session_id": {
                    "type": "string",
                    "description": "The browser holding the file to copy.",
                },
                "name": {"type": "string"},
            },
        },
        "FileKept",
    ),
}


def _file_paths(prefix: str = "/files") -> dict:
    """The four /files endpoints."""
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
        paths[f"{prefix}/{path}"] = {
            "post": {
                "operationId": op,
                "x-mcp-tool": tools[path],
                "summary": summary,
                "description": description,
                "tags": ["files"],
                "requestBody": {
                    "required": path != "list",
                    "content": {"application/json": {"schema": request}},
                },
                "responses": responses,
            }
        }
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
