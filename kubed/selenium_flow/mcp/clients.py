"""Who is calling, and what their client lets the model reach.

Resources are published for every client, and a client that cannot read them
is given `mirror`'s two tools instead. Whether a client can is decided here, in
this order:

1. **What the caller said.** ``?resources=off`` on the MCP URL, or an
   ``X-MCP-Resources: off`` header, which wins for the same reason it does for
   session names: the header is set in a credential, by an admin.
2. **Who the caller is.** A client known not to hand resources to its model is
   treated as saying off. VS Code is the one measured: its model has no way to
   list or read a resource, which a user can only attach by hand (saga §F3.1).
3. **Otherwise yes.** The assumption is that a client is spec-complete, and one
   that is not says so.
"""

from __future__ import annotations

from dataclasses import replace

from fastmcp.server import dependencies
from fastmcp.server.dependencies import get_access_token, get_context

from ..principal import Principal
from ..workspace.workspaces import Caller, values_of

_OFF = ("off", "false", "0", "no", "none")

# `clientInfo.name` prefixes of clients whose model cannot read resources. Each
# entry is a measurement, not a guess: add one only after watching that client
# fail to reach a resource (saga §F3.1).
NO_RESOURCES = ("Visual Studio Code",)

# Where the `2026-07-28` protocol carries the client's identity on every request.
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"


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
    # Headers may still be reachable when the request object is not: a
    # background task carries its originating request's.
    try:
        headers = dependencies.get_http_headers()
    except Exception:  # noqa: BLE001 - no request is no request
        return None
    if not headers:
        return None
    return {}, {k.lower(): [v] for k, v in headers.items()}


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
