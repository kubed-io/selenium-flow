"""The OpenAPI description of the HTTP surface.

A browser endpoint's request body is its MCP tool's schema, unchanged, so a
tool's new parameter reaches this document by itself. Responses are
hand-written: each is the ``response`` column of its row in
``core/capabilities.py``. The workspace, a header or query parameter on every
operation, is in no tool schema.

Paths and methods come from the route tables (``core.capabilities``,
``flowapi.FLOW_ROUTES``, ``files.FILE_ROUTES``), so the document cannot describe
a path that is not served.
"""

from __future__ import annotations

import copy
import re
from importlib.metadata import PackageNotFoundError, version

from kubed.mcp import QuickMCP

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

DESCRIPTION = """\
Drive a persistent browser on Selenium Grid over plain HTTP.

Every operation here is also an MCP tool at `{mount}/mcp`, backed by the same code —
the request schemas in this document are generated from those tools, so the two
surfaces cannot describe different things.

Name your workspace on every request — an `Sf-Workspace` header or
`?workspace=` — and every call is about that workspace's browser. Sending both
is a 400, and so is sending neither on anything that touches a browser. There
is no browser id in this API.

`POST {mount}/browser` opens yours, `DELETE {mount}/browser` ends it when you
are finished — do that even after a failure, or it holds a Grid slot until it
times out.
"""


# What the committed openapi.yaml carries instead of a real version, which
# would churn the file on every commit.
PLACEHOLDER_VERSION = "0.0.0"


# A command's summary is its tool description's first line, which for these two
# reads as advice to a model. A new row on the resource without one fails the
# build.
RESOURCE_SUMMARIES = {
    "open_session": (
        "Open a session in this workspace, or pick up the one it was using."
    ),
    "end_browser": "Quit the browser, keeping the workspace and its context.",
}


# What a 400 and a 503 on the browser resource mean, per capability: opening a
# browser can fail in ways ending one cannot. "" is every other row's.
RESOURCE_400 = {
    "": (
        "The request cannot succeed as sent — no workspace named, two "
        "names given, or a setting that was rejected."
    ),
    "open_session": (
        "The request cannot succeed as sent — no workspace named, two names "
        "given, a setting that was rejected, a capability selenium-flow "
        "controls, a vendor block of the wrong shape, a browser a Grid "
        "node refused for a capability or version you asked for, "
        "or a first page whose certificate the browser does not trust."
    ),
}
RESOURCE_503 = {
    "": (
        "The Grid could not serve this — unreachable, or no free "
        "slot for a new browser. Worth retrying after a wait."
    ),
    "open_session": (
        "The Grid could not serve this — unreachable, no node took the "
        "browser before the Grid's queue timed out, or a node could not "
        "start it. Worth retrying after a wait."
    ),
}


def _version() -> str:
    try:
        return version("kubed-selenium-flow")
    except PackageNotFoundError:
        return "0.0.0"


async def build_spec(
    mcp: QuickMCP,
    endpoints: dict[str, str],
    prefix: str,
    authenticated: bool,
) -> dict:
    """Assemble the OpenAPI 3.1 document for the HTTP surface.

    3.1 is a superset of JSON Schema, so the tool schemas are embedded as they
    are.

    ``endpoints`` is ``core.capabilities.ENDPOINTS``, the commands under
    ``/browser`` by path; everything else about each is read from its row.

    ``prefix`` is where the whole server is mounted, `/openapi.*` included.
    Only the four probes also answer at the root.
    """
    browser_root = f"{prefix}/browser"
    # get_tool, never list_tools: a listing is shaped for whoever is asking.
    # Not guarded: an action in the route table with no tool is a broken build.
    # FastMCP's own, through `mcp.server`: QuickMCP does not pass it through.
    tools = {
        action: await mcp.server.get_tool(action)
        for action in set(endpoints.values())
    }

    schemas: dict[str, dict] = {"Error": ERROR, "Health": HEALTH}
    paths: dict[str, dict] = {}

    for path, action in endpoints.items():
        tool = tools.get(action)
        if tool is None:  # pragma: no cover - the surfaces test forbids this
            continue
        row = capability(action)

        request_name = f"{_camel(action)}Request"
        response_name = f"{_camel(action)}Response"
        request, nested = _hoisted(copy.deepcopy(tool.parameters))
        schemas.update(nested)
        schemas[request_name] = request
        schemas[response_name] = row.response

        route = f"{browser_root}/{path}" + ("/{action}" if row.in_path else "")
        parameters = list(WORKSPACE_PARAMETERS)
        if row.in_path:
            # The action is the path (/browser/interact/click), not a field.
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
                # The same name here, stated so a reader never has to know
                # which surface a name came from.
                "x-mcp-tool": action,
                "summary": _summary(tool.description),
                "description": tool.description or "",
                "tags": ["browser"],
                "requestBody": {
                    "required": True,
                    "content": _request_content(action, request_name, request),
                },
                "responses": {
                    "200": _answer("The action succeeded.", _ref(response_name)),
                    # Split by what the caller should do (errors.py).
                    "400": _error(
                        "The request cannot succeed as sent — a missing field, "
                        "a value that was rejected, a locator that matched "
                        "nothing before the wait ran out, or a page whose certificate "
                        "the browser does not trust. Do not retry it "
                        "unchanged."
                    ),
                    "401": _error(UNAUTHORIZED),
                    "413": _error(TOO_LARGE),
                    "404": _error(NOT_FOUND.get(action, GONE_BROWSER)),
                    "409": _error(ACTION_CONFLICT),
                    "500": _error(UNEXPECTED),
                    "503": _error(
                        "The Grid could not serve this — unreachable, or no free "
                        "slot for a new browser. Worth retrying after a wait."
                    ),
                },
            }
        }
        if row.waits:
            # It takes nothing, is what a 409 sends a caller to, and reports an
            # ended browser in its answer, never as a 404.
            operation = paths[route][row.http_method.lower()]
            operation["requestBody"]["required"] = False
            for status in ("404", "409"):
                operation["responses"].pop(status)

    # The browser this caller holds: one resource, three methods. Its two
    # capabilities are the rows with no path of their own; the third method is
    # the status read, below.
    for row in CAPABILITIES:
        if row.route:
            continue
        action = row.name
        summary = RESOURCE_SUMMARIES[action]
        tool = await mcp.server.get_tool(action)
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
                "content": _json(_ref(request_name)),
            },
            "responses": {
                "200": _answer(summary, _ref(response_name)),
                "400": _error(RESOURCE_400.get(action, RESOURCE_400[""])),
                "401": _error(UNAUTHORIZED),
                "409": _error(CONTROLLED),
                "413": _error(TOO_LARGE),
                "503": _error(RESOURCE_503.get(action, RESOURCE_503[""])),
            },
        }

    from ..mcp import resources as status

    schemas["WorkspaceStatus"] = RESPONSES["current_workspace"]
    paths.setdefault(browser_root, {})["get"] = {
        "operationId": "currentWorkspace",
        "x-mcp-resource": status.RESOURCE_URI,
        "parameters": list(WORKSPACE_PARAMETERS),
        "summary": "What this workspace is, and whether it holds a browser.",
        "description": status.DESCRIPTION,
        "tags": ["browser"],
        "responses": {
            "200": _answer(
                "The workspace's current state. Opens nothing.", _ref("WorkspaceStatus")
            ),
            "400": _error("No workspace named, or two names given."),
            "401": _error(UNAUTHORIZED),
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

    # One ops endpoint per question a probe asks. Each also answers at the
    # root; the document names the mounted one.
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
        answers = {"200": _answer(summary, _ref(schema_name))}
        if grid:
            answers["503"] = _answer(
                "The Grid is unreachable or not ready.", _ref("Ready")
            )
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
            # redocly's info-license-strict rule warns without an identifier.
            "license": {"name": "MIT", "identifier": "MIT"},
        },
        # A docs UI or a generated client needs servers, and redocly rejects an
        # empty list. The mount is in the paths, so a pasted path works even
        # where `servers` is unknown.
        "servers": [
            {
                "url": "/",
                "description": "The server this document was fetched from.",
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


def _request_content(action: str, request_name: str, request: dict) -> dict:
    """The accepted request bodies for one endpoint.

    Everything takes JSON. Uploading also takes a multipart form, so a file is
    sent as a file instead of base64 in JSON; its ``session`` is described as
    the JSON body's (``request``) is.
    """
    content = _json(_ref(request_name))
    if action == "upload_file":
        content["multipart/form-data"] = {
            "schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": (
                            'The element, as JSON: {"css": "input[type=file]"} '
                            'or {"xpath": "//input"}. A form field carries '
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
                    "session": {
                        "type": "string",
                        "description": request["properties"]["session"][
                            "description"
                        ],
                    },
                },
                # Nothing is required: `browser.locator` refuses a bad selector
                # with a 400, as it does for a JSON body.
            }
        }
    return content


def _named_in_path(template: str) -> list[dict]:
    """The path parameters a route template declares, in order.

    `folder` is published with the three values the keep route accepts, the
    folders a file can be kept out of.
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

    `name` is in the path and the workspace is a header.
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


UNAUTHORIZED = "Missing or wrong bearer token."
UNEXPECTED = "Something failed that this server did not expect."
TOO_LARGE = (
    "The body is over its cap: 1 MiB of JSON, 64 MiB of upload, 1 MiB of "
    "flow YAML. Send less; retrying it unchanged will fail again."
)


# A body is not a browser: a new browser cannot bring one back.
GONE_BROWSER = (
    "No such browser session. It ended, the Grid reaped it, "
    "or the id was never real. Open a new one and retry."
)
CONTROLLED = (
    "A person holds this workspace's browser in the live view. Call POST "
    "/browser/await-control, then retry."
)
# An action's 409: a person in control, or its tab gone (`faults.TabClosed`).
ACTION_CONFLICT = (
    "A person holds this workspace's browser in the live view (call POST "
    "/browser/await-control, then retry), or the tab the call was to act on "
    "closed before it could act (call again)."
)
NOT_FOUND = {
    "network_body": (
        "No such body. No session is open in this workspace, or the browser "
        "that made this request has ended or no longer holds its body: a body "
        "lives only as long as its browser. A new browser cannot return it, so "
        "do not retry; and opening one without capture forgets what was "
        "captured."
    ),
}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _json(schema: dict) -> dict:
    """A JSON body of ``schema``, as a request or response ``content``."""
    return {"application/json": {"schema": schema}}


def _answer(description: str, schema: dict) -> dict:
    """A response with a JSON body."""
    return {"description": description, "content": _json(schema)}


def _error(description: str) -> dict:
    return _answer(description, _ref("Error"))


def _summary(description: str | None) -> str:
    """First sentence of the tool description, which is written as a summary."""
    return (description or "").strip().split("\n", 1)[0].strip()


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


# The /flows, /files, secrets and site-data operations are written by hand:
# their bodies are documents and names, not an action's arguments.
def _mcp_tools() -> tuple[dict, dict]:
    """The MCP side of each ``/flows`` and ``/files`` endpoint.

    Each is an extension key and its value: ``x-mcp-tool`` for an action, and
    ``x-mcp-resource`` for a read, which over MCP is a resource at that URI.
    Read from the modules' constants, imported here because ``routes`` loads
    this module and the flow and file trees are not needed until a document is
    built.
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
        route = (
            f"{prefix}{template}"
            if template.startswith("/schemas")
            else (f"{prefix}/flows{template}")
        )
        schema = _ref(response) if response else {"type": "object"}
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
                "200": _answer(summary, schema),
                "400": _error(
                    "The request cannot succeed as sent — an unusable name, "
                    "a flow that does not exist, a document that would not "
                    "run, or a write aimed at the read-only shared library. "
                    "Do not retry it unchanged."
                ),
                "401": _error(UNAUTHORIZED),
                "500": _error(UNEXPECTED),
            },
        }
        if has_body:
            operation["responses"]["413"] = _error(TOO_LARGE)
            operation["requestBody"] = {
                "required": method == "put",
                "content": _json(request),
            }
        paths.setdefault(route, {})[method] = operation
    return paths


# An operation whose commonest 400 is not the shared one, by its `FILE_ROUTES`
# key. The downloads listing is refused when no session is open.
_FILE_400 = {
    "downloads": (
        "No session is open in this workspace: a download lives only as long "
        "as the browser that took it. Or an unusable workspace name."
    ),
}


def _file_paths(prefix: str = "") -> dict:
    """A workspace's files as resources, from `files.FILE_ROUTES`."""
    from ..http.files import FILE_ROUTES

    _, tools = _mcp_tools()
    paths = {}
    for path, (op, summary, description, request, response) in _FILE_OPERATIONS.items():
        responses = {
            "200": _answer(summary, _ref(response)),
            "400": _error(
                _FILE_400.get(
                    path,
                    "The request cannot succeed as sent — an unusable name, or a "
                    "server with no DATA_DIR to keep files in. Do not retry it "
                    "unchanged.",
                )
            ),
            "401": _error(UNAUTHORIZED),
            "404": _error(
                "No such browser session, or no such file in it. It ended, the "
                "Grid reaped it, or the id was never real."
            ),
            "500": _error(UNEXPECTED),
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
            # The downloads listing is the session's.
            "tags": ["browser" if template.startswith("/browser") else "files"],
            "responses": responses,
        }
        if method in ("post", "put"):
            responses["413"] = _error(TOO_LARGE)
            operation["requestBody"] = {
                "required": False,
                "content": _json(_without(request, "name", "workspace", "session_id")),
            }
        paths.setdefault(f"{prefix}{template}", {})[method] = operation
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
                    "200": _answer(summary, _ref(schema)),
                    "400": _error(
                        "No workspace named, two names given, or no saved data for "
                        "that site. Do not retry it unchanged."
                    ),
                    "401": _error(UNAUTHORIZED),
                    "500": _error(UNEXPECTED),
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
    """The secrets catalogue: one read, which is `secret://secrets` over MCP."""
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
                    "200": _answer(
                        "Names, keys and sites. Never a value.", _ref("SecretList")
                    ),
                    "400": _error(
                        "No workspace named, two names given, or a server started "
                        "with no secrets.dirs and no secrets.entries, so there "
                        "are no secrets to list."
                    ),
                    "401": _error(UNAUTHORIZED),
                    "500": _error(UNEXPECTED),
                },
            }
        }
    }


def _hoisted(schema: dict) -> tuple[dict, dict]:
    """A schema with its ``$defs`` lifted into ``components/schemas``.

    Pydantic writes a nested model as ``$ref: '#/$defs/ValueFrom'`` with the
    definition alongside, and a ``$ref`` resolves against the document root,
    where there is no ``$defs``. Lifted, not inlined, so a model two actions
    use is described once.
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
