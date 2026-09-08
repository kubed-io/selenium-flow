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
    "close_session": {
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
            "type": "string",
            "description": "The field's value read back off the element, so a "
            "caller can confirm the text landed.",
        }
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
call; nothing is stored server-side. Call `/browser/close` when finished,
including after a failure, or the session holds a Grid slot until it times out.
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
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    schemas: dict[str, dict] = {"Error": ERROR, "Health": HEALTH}
    paths: dict[str, dict] = {}

    for path, action in endpoints.items():
        tool = tools.get(action)
        if tool is None:  # pragma: no cover - the surfaces test forbids this
            continue

        request_name = f"{_camel(action)}Request"
        response_name = f"{_camel(action)}Response"
        schemas[request_name] = http_schema(tool.parameters)
        schemas[response_name] = RESPONSES.get(action, {"type": "object"})

        paths[f"{prefix}/{path}"] = {
            "post": {
                "operationId": action,
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
                    "400": _error("A required field is missing or a value is invalid."),
                    "401": _error("Missing or wrong bearer token."),
                    "500": _error(
                        "The Grid rejected the action or the session is gone."
                    ),
                },
            }
        }

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
                "required": ["session_id", "xpath"],
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
    first = (description or "").strip().split("\n", 1)[0].strip()
    return first


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))
