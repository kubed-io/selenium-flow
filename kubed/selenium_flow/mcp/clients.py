"""Who is calling, and what their client lets the model reach.

Resources are published for every client, and a client that cannot read them
is given `mirror`'s two tools instead. Whether a client can is decided here, in
this order:

1. **What the caller said.** ``?resources=off`` on the MCP URL, or an
   ``X-MCP-Resources: off`` header, which wins for the same reason it does for
   workspace names: the header is set in a credential, by an admin.
2. **Who the caller is.** A client known not to hand resources to its model is
   treated as saying off. VS Code is the one measured: its model has no way to
   list or read a resource, which a user can only attach by hand (saga §F3.1).
3. **Otherwise yes.** The assumption is that a client is spec-complete, and one
   that is not says so.
"""

from __future__ import annotations

from dataclasses import replace

from fastmcp.exceptions import ToolError
from fastmcp.server import dependencies
from fastmcp.server.dependencies import get_access_token, get_context
from fastmcp.server.middleware import Middleware

from .. import faults
from ..principal import Principal
from ..workspace.workspaces import WORKSPACE_HEADER, Caller, values_of

_OFF = ("off", "false", "0", "no", "none")

# `clientInfo.name` prefixes of clients whose model cannot read resources. Each
# entry is a measurement, not a guess: add one only after watching that client
# fail to reach a resource (saga §F3.1).
NO_RESOURCES = ("Visual Studio Code",)

# Where the `2026-07-28` protocol carries the client's identity on every request.
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"

# The workspace a background task's worker is about (live control spec, ruling
# 31). The worker has no request, only the headers snapshotted at submission,
# so ``?workspace=`` never reaches it: `Carry` writes the caller's name here as
# the call is submitted, and only the no-request branch below reads it. A
# client sending it is ignored on a request and overwritten at submission.
CARRIED = "x-selenium-flow-workspace"


def request_values() -> tuple[dict[str, list[str]], dict[str, list[str]]] | None:
    """(query params, headers) for the current request, or None off HTTP.

    Read through FastMCP's dependency helpers, which find the request in a
    context variable the ASGI stack sets. That is a different path from the one
    ``Context.session_id`` uses, and it keeps working where that one gives up.
    Every value is a list, because a repeated name is two names.
    """
    try:
        request = dependencies.get_http_request()
    except Exception:  # noqa: BLE001 - outside an HTTP request this raises
        request = None
    if request is not None:
        return values_of(request)
    # No request, but maybe its headers: a background task's worker has the
    # snapshot of every header its submission carried (`include_all`, so an
    # HTTP submission always has some - its `host`). None at all means no HTTP
    # request was ever behind this call: stdio, a task submitted over stdio
    # included.
    try:
        headers = dependencies.get_http_headers(include_all=True)
    except Exception:  # noqa: BLE001 - no request is no request
        return None
    if not headers:
        return None
    # A task submitted over HTTP is named by what `Carry` wrote at submission,
    # and by nothing else: never by an X-Workspace the snapshot kept, and never
    # the stdio constant (§F2.12). Without it the worker is unnamed.
    values = {k.lower(): [v] for k, v in headers.items()}
    carried = values.pop(CARRIED, None)
    values.pop(WORKSPACE_HEADER, None)
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
    caller already knows it — a handshake does — and is otherwise looked up.
    """
    client = name() if client is None else client
    values = request_values()
    if values is None:
        return Caller.stdio(client=client)
    return replace(Caller.from_request(*values, client=client), principal=principal())


def name() -> str:
    """The calling client's `clientInfo.name`, or "" when it cannot be told.

    Two places, one per protocol generation: a `2025` client said it once, in
    `initialize`, and the transport session holds it; a `2026-07-28` client
    says it in every request's `_meta`. Never raises — a client that cannot be
    identified is simply not in any table.
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

    `initialize` has it in `params.clientInfo`; `server/discover` in the
    request's `_meta`. Needed because the instructions are answered by exactly
    those two messages — the moment a client is being introduced, and before
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
    """Carry the caller's workspace to a background task's worker (R3, ruling 31).

    On every call over HTTP, a ``CARRIED`` header the client sent is dropped:
    it is ours. For a call to a tool that may run as a task - read off the
    tool's own task config, so no second list can drift from it - the caller's
    name is read from the request now, while there is one, and written into
    its headers, which the task's snapshot is taken from after this runs. In
    place, in ``scope["headers"]``: Starlette's `Request` reads that very list,
    and a `MutableHeaders` over it is a copy the snapshot never sees.

    A request that names no workspace, or names it badly (twice, malformed,
    the old spelling), is refused here, at submission, as the tool would
    refuse it: its worker sees only headers, and could neither say why nor
    tell a URL's name from a header's. Off HTTP there is nothing to carry.
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
