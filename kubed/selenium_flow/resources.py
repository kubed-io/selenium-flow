"""The session status, offered as both an MCP resource and a tool.

A resource is the right shape for this: it is state to read, not an action to
perform, and a client can pull it into context without spending a tool call.

But resources are the least widely implemented part of MCP — n8n, for one, has
no notion of them — and a capability the client cannot see may as well not
exist. So the same status is also available as a tool, and the client says which
it can use: ``?resources=off`` on the MCP URL, or an ``X-MCP-Resources: off``
header, which wins for the same reason it does for session names.

The tool is hidden by default rather than absent. Advertising both to a client
that supports resources is noise — two ways to ask one question — so it is
filtered out of ``tools/list`` unless asked for. It stays *callable* either way,
because hiding a tool from a listing is a presentation choice and refusing to
run it would be a different, worse contract.
"""

from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

# Imported under the old name: this module (and its tests) patch _http to
# simulate a request, and the alias keeps one seam rather than two.
from . import apps
from .sessions import SessionManager
from .sessions import http_request as _http

log = logging.getLogger(__name__)

RESOURCE_URI = "session://current"
STATUS_TOOL = "current_session"

# How a client declares it cannot read resources. Header beats parameter, as
# with session names: the header is set in the credential, by an admin.
RESOURCES_PARAM = "resources"
RESOURCES_HEADER = "x-mcp-resources"
_OFF = ("off", "false", "0", "no", "none")

DESCRIPTION = (
    "The browser session this client is currently using, if any.\n\n"
    "Returns session_id, the page it is on, and whether the Grid still has it "
    "(live). Reading this never opens a browser: a null session_id means "
    "nothing is held yet."
)


def client_reads_resources() -> bool:
    """Whether this caller can be expected to read MCP resources.

    Defaults to true — the assumption is that a client is spec-complete, and a
    client that is not says so.
    """
    http = _http()
    if http is None:
        return True
    params, headers = http
    declared = headers.get(RESOURCES_HEADER) or params.get(RESOURCES_PARAM)
    if declared is None:
        return True
    return str(declared).strip().lower() not in _OFF


class ShapeSessionId(Middleware):
    """Advertise ``session_id`` the way this request may actually use it.

    The two session modes have opposite rules, and a model cannot follow a rule
    it cannot see. So the listing is rewritten per request:

    - **Saved**: ``session_id`` is removed from every schema. The server knows
      which browser is yours, so there is nothing to pass and no way to pass the
      wrong thing.
    - **Stateless**: ``session_id`` becomes **required**, and loses its null
      branch. A caller discovers it is mandatory by reading the schema rather
      than by failing a call.

    ``open_session`` is untouched: it has no ``session_id`` to shape, and it is
    the one tool both modes call the same way.

    Schemas are copied, never mutated — the registered tools are shared by every
    client, and rewriting one in place would leak this request's mode into all
    of them.
    """

    def __init__(self, sessions):
        self.sessions = sessions

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        saved = self.sessions.mode() == self.sessions.SAVED
        return [_shaped(tool, saved) for tool in tools]


def _shaped(tool, saved: bool):
    """One tool with its ``session_id`` shaped for the mode, or unchanged."""
    schema = tool.parameters or {}
    properties = schema.get("properties") or {}
    if "session_id" not in properties:
        return tool

    properties = dict(properties)
    required = list(schema.get("required") or [])

    if saved:
        properties.pop("session_id", None)
        required = [name for name in required if name != "session_id"]
    else:
        prop = dict(properties["session_id"])
        branches = [b for b in prop.get("anyOf", []) if b.get("type") != "null"]
        if len(branches) == 1:
            prop = dict(branches[0])
        prop.pop("default", None)
        prop.setdefault(
            "description",
            "Required: this server cannot identify you, so you own the session.",
        )
        properties["session_id"] = prop
        if "session_id" not in required:
            required.insert(0, "session_id")

    updated = {**schema, "properties": properties}
    if required:
        updated["required"] = required
    else:
        updated.pop("required", None)
    return tool.model_copy(update={"parameters": updated})


class HideMirrorTools(Middleware):
    """Drop resource-mirroring tools from ``tools/list`` for clients that read
    resources.

    A "mirror" is a tool that exists only because some clients cannot read
    resources — the session status, the embedded skill. Advertising both shapes
    to a client that has resources is noise: two ways to ask one question.

    Filtering the listing rather than registering conditionally is what keeps
    one server object correct for every client at once. The decision depends on
    who is asking, so it can only be made per request; registration happens once
    at boot, when there is no asker.
    """

    def __init__(self, names: set[str], app_tools: set[str] | None = None):
        self.names = set(names)
        # Mirrors that are also apps. A tool carrying an app config is not a
        # duplicate of its resource — it is the only way a host that renders UI
        # gets to draw one, since a resource has no app to attach. So for those
        # clients the tool stays, and "two ways to ask one question" becomes
        # "the readable way and the pretty way", which is worth the noise.
        self.app_tools = set(app_tools or ())

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        if not self.names or not client_reads_resources():
            return tools
        hidden = self.names
        if self.app_tools and apps.supported():
            hidden = hidden - self.app_tools
        return [tool for tool in tools if tool.name not in hidden]


def register(mcp: FastMCP, sessions: SessionManager) -> set[str]:
    """Register the session status as a resource and as a tool that mirrors it.

    Returns the mirror tool names, for the caller to hide from clients that read
    resources.
    """

    @mcp.resource(RESOURCE_URI, description=DESCRIPTION, mime_type="application/json")
    def current_session_resource() -> dict:
        return sessions.describe()

    @mcp.tool(name=STATUS_TOOL, description=DESCRIPTION)
    def current_session_tool() -> dict:
        return sessions.describe()

    return {STATUS_TOOL}
