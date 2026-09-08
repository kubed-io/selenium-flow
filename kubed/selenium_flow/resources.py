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

from .sessions import SessionManager, _http

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


class HideStatusToolFromResourceClients(Middleware):
    """Drop the status tool from ``tools/list`` for clients that read resources.

    Filtering the listing rather than conditionally registering the tool keeps
    one server object serving every client correctly: the decision is per
    request, because it depends on who is asking.
    """

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        if client_reads_resources():
            return [tool for tool in tools if tool.name != STATUS_TOOL]
        return tools


def register(mcp: FastMCP, sessions: SessionManager) -> None:
    """Register the session status as a resource and as a hidden-by-default tool."""

    @mcp.resource(RESOURCE_URI, description=DESCRIPTION, mime_type="application/json")
    def current_session_resource() -> dict:
        return sessions.describe()

    @mcp.tool(name=STATUS_TOOL, description=DESCRIPTION)
    def current_session_tool() -> dict:
        return sessions.describe()

    mcp.add_middleware(HideStatusToolFromResourceClients())
