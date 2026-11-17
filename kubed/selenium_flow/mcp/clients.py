"""Who is calling, and what their client lets the model reach.

Resources are published for every client, and a client that cannot read them
is given `mirror`'s two tools instead. Whether a client can is decided here, in
this order:

1. What the caller said: ``?resources=off`` on the MCP URL, or an
   ``X-MCP-Resources: off`` header, which wins because an admin sets it in a
   credential.
2. Who the caller is: a client known not to hand resources to its model counts
   as off. VS Code's model cannot list or read one.
3. Otherwise yes.
"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

import anyio
from fastmcp.exceptions import ToolError
from fastmcp.server import dependencies
from fastmcp.server.dependencies import get_access_token, get_context
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from .. import faults
from ..principal import Principal
from ..urls import without_userinfo
from ..workspace.workspaces import WORKSPACE_ALIAS, WORKSPACE_HEADER, Caller, values_of

_OFF = ("off", "false", "0", "no", "none")

# `clientInfo.name` prefixes of clients whose model cannot read resources. Add
# one only after watching that client fail to reach a resource.
NO_RESOURCES = ("Visual Studio Code",)

# Where the `2026-07-28` protocol carries the client's identity on every request.
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"

# The workspace a background task's worker is about. The worker has no
# request, only the headers snapshotted at submission, so ``?workspace=`` never
# reaches it: `Carry` writes the caller's name here at submission, and only the
# no-request branch below reads it. A client sending it is ignored.
CARRIED = "sf-carried-workspace"


def request_values() -> tuple[dict[str, list[str]], dict[str, list[str]]] | None:
    """(query params, headers) for the current request, or None off HTTP.

    Read through FastMCP's dependency helpers, which find the request in a
    context variable the ASGI stack sets and keep working where
    ``Context.session_id`` gives up. Every value is a list: a repeated name is
    two names.
    """
    try:
        request = dependencies.get_http_request()
    except Exception:  # noqa: BLE001 - outside an HTTP request this raises
        request = None
    if request is not None:
        return values_of(request)
    # A background task's worker has the snapshot of every header its
    # submission carried (`include_all`, so an HTTP submission always has its
    # `host`). None at all means stdio.
    try:
        headers = dependencies.get_http_headers(include_all=True)
    except Exception:  # noqa: BLE001 - no request is no request
        return None
    if not headers:
        return None
    # A task submitted over HTTP is named by what `Carry` wrote and nothing
    # else: never by a workspace header the snapshot kept, never the stdio
    # constant.
    values = {k.lower(): [v] for k, v in headers.items()}
    carried = values.pop(CARRIED, None)
    values.pop(WORKSPACE_HEADER, None)
    values.pop(WORKSPACE_ALIAS, None)
    if carried:
        values[WORKSPACE_HEADER] = carried
    return {}, values


def principal() -> Principal | None:
    """Who this request's verified bearer is, or None on an open server."""
    try:
        token = get_access_token()
    except Exception:  # noqa: BLE001 - off a request there is no bearer
        return None
    return getattr(token, "principal", None)


def caller(client: str | None = None) -> Caller:
    """Who is calling this MCP request: read once, here, and handed in.

    Off HTTP it is the stdio caller. ``client`` is the client's name when the
    caller knows it (a handshake does), and is otherwise looked up.
    """
    client = name() if client is None else client
    values = request_values()
    if values is None:
        return Caller.stdio(client=client)
    return replace(Caller.from_request(*values, client=client), principal=principal())


def name() -> str:
    """The calling client's `clientInfo.name`, or "" when it cannot be told.

    A `2025` client said it once, in `initialize`, and the transport session
    holds it; a `2026-07-28` client says it in every request's `_meta`. Never
    raises.
    """
    try:
        context = get_context()
    except RuntimeError:
        return ""
    try:
        request = context.request_context
        meta = request.meta if request is not None else None
        if isinstance(meta, dict):
            info = meta.get(CLIENT_INFO_META)
            if isinstance(info, dict) and info.get("name"):
                return str(info["name"])
    except Exception:  # noqa: BLE001 - identity is a hint, never a failure
        pass
    try:
        params = context.session.client_params
        info = getattr(params, "client_info", None) if params is not None else None
        return str(getattr(info, "name", "") or "")
    except Exception:  # noqa: BLE001 - no session, no initialize: unidentified
        return ""


def named_in(message) -> str:
    """The client name a handshake message carries, before any session holds it.

    `initialize` has it in `params.clientInfo`, `server/discover` in the
    request's `_meta`. The instructions answer these two messages, before
    `name()` has anywhere to look.
    """
    params = getattr(message, "params", None)
    info = getattr(params, "client_info", None)
    if info is not None and getattr(info, "name", None):
        return str(info.name)
    meta = getattr(params, "meta", None)
    if isinstance(meta, dict):
        info = meta.get(CLIENT_INFO_META)
        if isinstance(info, dict) and info.get("name"):
            return str(info["name"])
    return ""


def reads_resources(caller: Caller) -> bool:
    """Whether this caller's model can be expected to read MCP resources."""
    said = caller.flags.get("resources")
    if said is not None:
        return str(said).strip().lower() not in _OFF
    return not caller.client.startswith(NO_RESOURCES)


class Carry(Middleware):
    """Carry the caller's workspace to a background task's worker.

    On every call over HTTP, a ``CARRIED`` header the client sent is dropped.
    For a tool that may run as a task (by its own task config), the caller's
    name is read from the request now and written into its headers, which the
    task's snapshot is taken from later. In place, in ``scope["headers"]``:
    Starlette's `Request` reads that list, and a `MutableHeaders` over it is a
    copy the snapshot never sees.

    A request that names no workspace, or names it badly, is refused here, as
    the tool would refuse it: its worker sees only headers, and could neither
    say why nor tell a URL's name from a header's.
    """

    async def on_call_tool(self, context, call_next):
        try:
            request = dependencies.get_http_request()
        except Exception:  # noqa: BLE001 - off HTTP there is no request
            request = None
        if request is not None:
            headers = request.scope["headers"]
            key = CARRIED.encode()
            headers[:] = [h for h in headers if h[0].lower() != key]
            name = context.message.name
            if await _tasked(context, name):
                caller = Caller.from_request(*values_of(request))
                try:
                    named = caller.name
                except ValueError as refusal:
                    raise ToolError(
                        f"Error calling tool {name!r}: {faults.message(refusal)}"
                    ) from refusal
                headers.append((key, named.encode()))
        return await call_next(context)


class Refused(Middleware):
    """Refuse every call, read and prompt that names a workspace whose config
    is refused (`workspace.config`), before anything it would do. A
    ``skill://`` read is not refused.

    Here, where the name is read, so no tool or resource can forget to: a
    broken file may be one that locks the workspace, and ignoring it would
    unlock it. A caller that names no workspace, or names it badly, passes on
    to be answered as it would be.
    """

    def __init__(self, configs):
        self.configs = configs

    async def _refusal(self) -> Exception | None:
        if not self.configs.enabled:
            return None
        named = caller(client="")
        if named.refusal is not None or not named.named:
            return None
        return await anyio.to_thread.run_sync(self.configs.refusal, named.named)

    async def on_call_tool(self, context, call_next):
        refusal = await self._refusal()
        if refusal is not None:
            name = context.message.name
            raise ToolError(f"Error calling tool {name!r}: {faults.message(refusal)}")
        return await call_next(context)

    async def on_read_resource(self, context, call_next):
        uri = str(getattr(context.message, "uri", ""))
        # The skill ships in the image and holds nothing of a workspace; it is
        # what tells a refused agent what to do.
        if urlsplit(uri).scheme == "skill":
            return await call_next(context)
        refusal = await self._refusal()
        if refusal is not None:
            uri = without_userinfo(uri)
            raise MCPError(
                code=INVALID_PARAMS,
                message=f"Error reading resource {uri!r}: {faults.message(refusal)}",
            )
        return await call_next(context)

    async def on_get_prompt(self, context, call_next):
        refusal = await self._refusal()
        if refusal is not None:
            raise MCPError(code=INVALID_PARAMS, message=faults.message(refusal))
        return await call_next(context)


async def _tasked(context, name: str) -> bool:
    """Whether the tool ``name`` may run as a task, by its own task config."""
    fastmcp = context.fastmcp_context
    server = fastmcp.fastmcp if fastmcp is not None else None
    if server is None:
        return False
    try:
        tool = await server.get_tool(name)
    except Exception:  # noqa: BLE001 - an unknown tool is the call's to refuse
        return False
    config = getattr(tool, "task_config", None)
    return bool(config is not None and config.supports_tasks())
