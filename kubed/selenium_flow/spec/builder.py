"""The OpenAPI description of the HTTP surface.

Generated, never hand-written. Request schemas come straight from the MCP tools
— the same objects FastMCP publishes to agents — so the two contracts are not
merely consistent, they are the same schema. Adding a parameter to a tool
changes this document with no further work.

Response shapes are the one hand-maintained half: the actions return plain
dicts, so there is nothing to introspect. Each is the ``response`` column of its
row in ``core/capabilities.py``, so an action cannot be declared without one.

**No transform is applied on the way through, and that is new.** There used to
be three sanctioned differences, all of them consequences of the HTTP surface
having no session of its own: ``session_id`` became required, ``fresh`` was
dropped because there was no session to be fresh *from*, and ``upload_file``
gained a ``session`` field so a kept file could name a library. §F2.12 and
§F2.13 removed the cause rather than the symptoms — both surfaces now name a
workspace the same way — so a request body here is exactly the tool's schema,
and a test asserts it.

What is described here and not in a tool schema is the workspace itself, as the
header and query parameter every operation takes. That is not a body field on
either surface, which is the whole point of it.

What is described *differently* is the shape, and only the shape: a tool is a
verb with arguments, an endpoint is a path with a method. Those come from the
route tables — ``core.capabilities``, ``flowapi.FLOW_ROUTES``,
``files.FILE_ROUTES`` — so this document cannot describe a path that is not
served.
"""

from __future__ import annotations

import copy
import re
from importlib.metadata import PackageNotFoundError, version

from fastmcp import FastMCP

from ..core.capabilities import CAPABILITIES, capability
from .schemas import (
    _FILE_OPERATIONS,
    _FLOW_OPERATIONS,
    ERROR,
    FILE_SCHEMAS,
    FLOW_SCHEMAS,
    HEALTH,
    INFO,
    READY,
    RESPONSES,
    SECRET_SCHEMAS,
    SITE_DATA_SCHEMAS,
    STARTED,
    WORKSPACE_PARAMETERS,
)

# One schema per question, because the ops endpoints answer different ones.
DESCRIPTION = """\
Drive a persistent browser on Selenium Grid over plain HTTP.

Every operation here is also an MCP tool at `{mount}/mcp`, backed by the same code —
the request schemas in this document are generated from those tools, so the two
surfaces cannot describe different things.

Name your workspace on every request — an `X-Workspace` header or
`?workspace=` — and every call is about that workspace's browser. Sending both
is a 400, and so is sending neither on anything that touches a browser. There
is no browser id in this API.

`POST {mount}/browser` opens yours, `DELETE {mount}/browser` ends it when you
are finished — do that even after a failure, or it holds a Grid slot until it
times out.
"""


# What the committed openapi.yaml carries instead of a real version. The field
# is required, so it cannot be omitted, but a real value would churn the file on
# every commit.
PLACEHOLDER_VERSION = "0.0.0"


# The browser resource's operations are summarised by hand: a command's summary
# is its tool description's first line, which for these two reads as advice to
# a model rather than as what the operation is. Keyed by capability, so a new
# row on the resource without one fails the build rather than going unnamed.
RESOURCE_SUMMARIES = {
    "open_session": "Open this workspace's browser, or pick up the one it was using.",
    "end_browser": "Quit the browser, keeping the workspace and its context.",
}


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

    ``endpoints`` is ``core.capabilities.ENDPOINTS``, the commands under
    ``/browser`` by path; everything else about each — its method, whether its
    choice is in the path, what it answers — is read from its row.

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

    for path, action in endpoints.items():
        tool = tools.get(action)
        if tool is None:  # pragma: no cover - the surfaces test forbids this
            continue
        row = capability(action)

        request_name = f"{_camel(action)}Request"
        response_name = f"{_camel(action)}Response"
        # Verbatim: the body an endpoint accepts IS the tool's schema now.
        request, nested = _hoisted(copy.deepcopy(tool.parameters))
        schemas.update(nested)
        schemas[request_name] = request
        schemas[response_name] = row.response

        route = f"{browser_root}/{path}" + ("/{action}" if row.in_path else "")
        parameters = list(WORKSPACE_PARAMETERS)
        if row.in_path:
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
            row.http_method.lower(): {
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
                    "413": _error(TOO_LARGE),
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
    # answer is in the header (§F2.13). Its two capabilities are the rows with
    # no path of their own; the third method is the status read, below.
    for row in CAPABILITIES:
        if row.route:
            continue
        action = row.name
        summary = RESOURCE_SUMMARIES[action]
        tool = await mcp.get_tool(action)
        request_name = f"{_camel(action)}Request"
        response_name = f"{_camel(action)}Response"
        request, nested = _hoisted(copy.deepcopy(tool.parameters))
        schemas.update(nested)
        schemas[request_name] = request
        schemas[response_name] = row.response
        paths.setdefault(browser_root, {})[row.http_method.lower()] = {
            "operationId": action,
            "x-mcp-tool": action,
            "parameters": list(WORKSPACE_PARAMETERS),
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
                    "The request cannot succeed as sent — no workspace named, two "
                    "names given, or a setting that was rejected."
                ),
                "401": _error("Missing or wrong bearer token."),
                "413": _error(TOO_LARGE),
                "503": _error(
                    "The Grid could not serve this — unreachable, or no free "
                    "slot for a new browser. Worth retrying after a wait."
                ),
            },
        }

    from ..mcp import resources as status

    schemas["SessionStatus"] = RESPONSES["current_session"]
    paths.setdefault(browser_root, {})["get"] = {
        "operationId": "currentSession",
        "x-mcp-resource": status.RESOURCE_URI,
        "parameters": list(WORKSPACE_PARAMETERS),
        "summary": "What this workspace is, and whether it holds a browser.",
        "description": status.DESCRIPTION,
        "tags": ["browser"],
        "responses": {
            "200": {
                "description": "The workspace's current state. Opens nothing.",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/SessionStatus"}
                    }
                },
            },
            "400": _error("No workspace named, or two names given."),
            "401": _error("Missing or wrong bearer token."),
        },
    }

    schemas.update(FLOW_SCHEMAS)
    paths.update(_flow_paths(prefix))
    schemas.update(FILE_SCHEMAS)
    paths.update(_file_paths(prefix))
    schemas.update(SECRET_SCHEMAS)
    paths.update(_secret_paths(prefix))
    schemas.update(SITE_DATA_SCHEMAS)
    paths.update(_site_data_paths(prefix))

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
                    "What a workspace has downloaded, and what it has kept "
                    "beyond the browser that downloaded it."
                ),
            },
            {
                "name": "secrets",
                "description": (
                    "The secrets a caller may bind to a field, by name. Values "
                    "never leave the server."
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
                    "The token from AUTH_TOKEN. The bare token is also "
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
                    "file": {
                        "type": "string",
                        "description": (
                            "The uri of a file this workspace has — a screenshot, "
                            "a download, or a file in Files — instead of "
                            "sending any bytes at all. Exactly one source: a "
                            "content part, text, file, or path."
                        ),
                    },
                    "url": {"type": "string"},
                    "wait_timeout": {"type": "integer"},
                },
                # Nothing is required: the input is addressed by EITHER xpath
                # OR css, which this hand-written schema cannot say without a
                # oneOf, and the workspace is a header rather than a field.
                # `browser.locator` enforces the selector rule at the boundary
                # and returns a 400 naming both, exactly as it does for a JSON
                # body.
            }
        }
    return content


# The workspace every operation is about, named the way §F2.13 says: a header, or
# a query parameter, and never a body field or a path segment. Published on
# every operation because it is how a caller says who it is, and a generated
# client that cannot see it cannot name a workspace at all.
def _named_in_path(template: str) -> list[dict]:
    """The path parameters a route template declares, in order.

    Generic over the placeholder's name so a route with more than one — the
    file keep route names both a folder and a name — publishes all of them,
    not just the first. `folder` is special-cased with the two values it may
    ever hold, the same way `files.SCREENSHOTS`/`files.DOWNLOADS` narrow it at
    the route itself — a generated client should not have to guess a third.
    """
    return [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": (
                {"type": "string", "enum": ["screenshots", "recordings", "downloads"]}
                if name == "folder"
                else {"type": "string"}
            ),
        }
        for name in re.findall(r"\{(\w+)\}", template)
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


# What a body over its cap answers: 1 MiB of JSON, 64 MiB of upload.
TOO_LARGE = (
    "The body is over its cap: 1 MiB of JSON, 64 MiB of upload, 1 MiB of "
    "flow YAML. Send less; retrying it unchanged will fail again."
)


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
# request bodies are a document plus a workspace name, not an action's arguments.
# That makes them the same kind of liability `_request_content`'s multipart form
# turned out to be — a hand-written schema next to derived ones drifts, quietly,
# and only a test notices. `test_openapi.py` holds the path list to
# `flowapi.FLOW_ENDPOINTS` for exactly that reason.

# A workspace name is optional everywhere and defaults to the shared library, the
# same way it does on the MCP surface for a caller with no name of its own.
# The write endpoints need a different sentence. Falling back to `global` is
# right for a read and is a *refusal* for a write, so advertising the same
# default on both would hand a generated client a 400 it had no way to see
# coming. It is still not `required`, because a caller naming itself through
# the X-Workspace header legitimately omits it.
def _mcp_tools() -> tuple[dict, dict]:
    """The MCP side of each ``/flows`` and ``/files`` endpoint.

    Each is an extension key and its value: ``x-mcp-tool`` for an action, and
    ``x-mcp-resource`` for a read, which over MCP is a resource at that URI —
    read directly, or through ``read_resource`` by a client that cannot
    (§F3.6).

    Imported here rather than at module scope: ``routes`` loads this module,
    and the flow and file trees are not needed until a document is built.
    Reading the constants is still the point — a second
    hand-written copy of these names is how the wiki ends up generating a page
    for a tool nobody can call.
    """
    from ..flows import api as flowapi
    from ..http import files as files_module

    return (
        {
            "list": ("x-mcp-resource", flowapi.LIST_URI),
            "get": ("x-mcp-resource", flowapi.FLOW_URI),
            "save": ("x-mcp-tool", flowapi.SAVE_TOOL),
            "delete": ("x-mcp-tool", flowapi.DELETE_TOOL),
            "run": ("x-mcp-tool", flowapi.RUN_TOOL),
            "schema": ("x-mcp-resource", flowapi.SCHEMA_URI),
        },
        {
            "list": ("x-mcp-resource", files_module.ROOT_URI),
            "screenshots": (
                "x-mcp-resource",
                files_module.FOLDER_URI[files_module.SCREENSHOTS],
            ),
            "recordings": (
                "x-mcp-resource",
                files_module.FOLDER_URI[files_module.RECORDINGS],
            ),
            "downloads": (
                "x-mcp-resource",
                files_module.FOLDER_URI[files_module.DOWNLOADS],
            ),
            "keep": ("x-mcp-tool", files_module.KEEP_TOOL),
        },
    )


def _flow_paths(prefix: str = "") -> dict:
    """The flow library as resources, from `flowapi.FLOW_ROUTES`."""
    from ..flows.api import FLOW_ROUTES

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
        # `name` is the path and the workspace is a header, so neither is a body
        # field any more.
        request = _without(request, "name", "workspace", "session_id")
        has_body = method in ("post", "put")
        operation = {
            "operationId": op,
            tools[path][0]: tools[path][1],
            "parameters": [*_named_in_path(template), *WORKSPACE_PARAMETERS],
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
            operation["responses"]["413"] = _error(TOO_LARGE)
            operation["requestBody"] = {
                "required": method == "put",
                "content": {"application/json": {"schema": request}},
            }
        paths.setdefault(route, {})[method] = operation
    return paths


# ---------------------------------------------------------------------------
# The /files surface, written by hand for the same reason /flows is: these take
# a file name and a workspace, not an action's arguments, so there is no tool
# schema to derive them from. `test_kept_files.py` holds the path list to
# `files.FILE_ENDPOINTS` so a new one cannot go undocumented.

# path -> (operationId, summary, description, request, response). Every one of
# these dials the Grid, so they all carry its failure modes; deleting a kept
# file never leaves this server, and is deliberately not here — it is an
# operator action on the admin surface, with no tool and so no endpoint.
def _file_paths(prefix: str = "") -> dict:
    """A workspace's files as resources, from `files.FILE_ROUTES`."""
    from ..http.files import FILE_ROUTES

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
                "server with no DATA_DIR to keep files in. Do not retry it "
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
            tools[path][0]: tools[path][1],
            "parameters": [*_named_in_path(template), *WORKSPACE_PARAMETERS],
            "summary": summary,
            "description": description,
            "tags": ["files"],
            "responses": responses,
        }
        if method in ("post", "put"):
            responses["413"] = _error(TOO_LARGE)
            operation["requestBody"] = {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": _without(request, "name", "workspace", "session_id")
                    }
                },
            }
        paths.setdefault(f"{prefix}/files{template}", {})[method] = operation
    return paths


def _site_data_paths(prefix: str = "") -> dict:
    """The two reads of saved site data, which are resources over MCP."""
    from ..mcp import resources

    def operation(op, uri, summary, description, schema, parameters=()):
        return {
            "get": {
                "operationId": op,
                "x-mcp-resource": uri,
                "parameters": [*parameters, *WORKSPACE_PARAMETERS],
                "summary": summary,
                "description": description,
                "tags": ["browser"],
                "responses": {
                    "200": {
                        "description": summary,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": f"#/components/schemas/{schema}"}
                            }
                        },
                    },
                    "400": _error(
                        "No workspace named, two names given, or no saved data for "
                        "that site. Do not retry it unchanged."
                    ),
                    "401": _error("Missing or wrong bearer token."),
                    "500": _error("Something failed that this server did not expect."),
                },
            }
        }

    site = {
        "name": "site",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
        "description": "A host from the listing, such as app.example.com.",
    }
    return {
        f"{prefix}/site-data": operation(
            "listSiteData",
            resources.site_data.LIST_URI,
            "The sites this workspace has saved data for.",
            resources.SITE_DESCRIPTION,
            "SiteList",
        ),
        f"{prefix}/site-data/{{site}}": operation(
            "getSiteData",
            f"{resources.site_data.LIST_URI}/{{site}}",
            "One site's saved cookies and storage.",
            resources.ONE_SITE_DESCRIPTION,
            "SiteData",
            [site],
        ),
    }


def _secret_paths(prefix: str = "") -> dict:
    """The secrets catalogue: one read, which is `secret://secrets` over MCP.

    Missing from this document from the day the route was added, and so from
    the wiki too — the one readable resource an HTTP caller could not discover
    (Copilot, #39).
    """
    from .. import secrets

    return {
        f"{prefix}/secrets": {
            "get": {
                "operationId": "listSecrets",
                "x-mcp-resource": secrets.LIST_URI,
                "parameters": list(WORKSPACE_PARAMETERS),
                "summary": "The secrets this caller may bind.",
                "description": secrets.LIST_DESCRIPTION,
                "tags": ["secrets"],
                "responses": {
                    "200": {
                        "description": "Names, keys and sites. Never a value.",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SecretList"}
                            }
                        },
                    },
                    "400": _error(
                        "No workspace named, two names given, or a server started "
                        "with no secrets.dirs and no secrets.entries, so there "
                        "are no secrets to list."
                    ),
                    "401": _error("Missing or wrong bearer token."),
                    "500": _error("Something failed that this server did not expect."),
                },
            }
        }
    }


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
