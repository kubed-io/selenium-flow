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

from fastmcp.server.dependencies import get_context

from ..session.sessions import http_request as _http

RESOURCES_PARAM = "resources"
RESOURCES_HEADER = "x-mcp-resources"
_OFF = ("off", "false", "0", "no", "none")

# `clientInfo.name` prefixes of clients whose model cannot read resources. Each
# entry is a measurement, not a guess: add one only after watching that client
# fail to reach a resource (saga §F3.1).
NO_RESOURCES = ("Visual Studio Code",)

# Where the `2026-07-28` protocol carries the client's identity on every request.
CLIENT_INFO_META = "io.modelcontextprotocol/clientInfo"


def declared() -> bool | None:
    """What the caller said about resources, or None if it said nothing."""
    http = _http()
    if http is None:
        return None
    params, headers = http
    said = headers.get(RESOURCES_HEADER) or params.get(RESOURCES_PARAM)
    if said is None:
        return None
    return str(said).strip().lower() not in _OFF


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


def reads_resources() -> bool:
    """Whether this caller's model can be expected to read MCP resources."""
    said = declared()
    if said is not None:
        return said
    return not name().startswith(NO_RESOURCES)
