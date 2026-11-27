"""Who is calling: the workspace, the session it pins and the bearer; and the
middleware that carries a caller to a task and refuses a refused workspace.

Whether a client reads resources is not decided here. ``Sf-Resources`` (or
``X-MCP-Resources``) and ``?resources=`` are kubed-mcp's (DLV-2), and so is
VS Code's default of off, by its table of known clients (DLV-15). The other
delivery inputs, Via among them, are kubed-mcp's too.
"""

from __future__ import annotations

import json
from dataclasses import replace
from urllib.parse import urlsplit

import anyio
from kubed.mcp import Middleware, ToolError
from kubed.mcp import request as mcp_request
from kubed.mcp.auth import get_access_token
from kubed.mcp.protocol import INVALID_PARAMS, MCPError
from kubed.mcp.request import Values

from .. import faults
from ..principal import Principal
from ..urls import without_userinfo
from ..workspace.workspaces import (
    SESSION_HEADER,
    WORKSPACE_ALIAS,
    WORKSPACE_HEADER,
    Caller,
    values_of,
)
from .failures import nameless

# The workspace a background task's worker is about. The worker has no
# request, only the headers snapshotted at submission, so ``?workspace=`` never
# reaches it: `Carry` writes the caller's name here at submission, and only the
# no-request branch below reads it. A client sending it is ignored.
CARRIED = "sf-carried-workspace"
# The session a connection pins, carried the same way: ``?session=`` never
# reaches the worker either.
CARRIED_SESSION = "sf-carried-session"
# Who the caller is, as JSON {"kind", "subject", "username"}: the worker's
# access token is rebuilt as a plain one, which drops the principal, and the
# principal decides the caller's own session and who holds a session (WS-82,
# WS-84). Roles are not carried: nothing past the door reads them (AUTH-21).
CARRIED_PRINCIPAL = "sf-carried-principal"


def request_values() -> tuple[Values, Values] | None:
    """(query params, headers) for the current request, or None off HTTP.

    quick-mcp's reader, with a background task's snapshot passed through
    `snapshot` first.
    """
    return mcp_request.request_values(snapshot=snapshot)


def snapshot(values: Values) -> Values:
    """A task worker's snapshotted headers, as its workspace and pin are read.

    A task submitted over HTTP is named and pinned by what `Carry` wrote and
    nothing else: never by a workspace or session header the snapshot kept,
    never the stdio constant.
    """
    values = dict(values)
    carried = values.pop(CARRIED, None)
    pin = values.pop(CARRIED_SESSION, None)
    values.pop(CARRIED_PRINCIPAL, None)
    values.pop(WORKSPACE_HEADER, None)
    values.pop(WORKSPACE_ALIAS, None)
    values.pop(SESSION_HEADER, None)
    if carried:
        values[WORKSPACE_HEADER] = carried
    if pin:
        values[SESSION_HEADER] = pin
    return values


def principal() -> Principal | None:
    """Who this request's verified bearer is, or None on an open server."""
    try:
        token = get_access_token()
    except Exception:  # noqa: BLE001 - off a request there is no bearer
        return None
    return getattr(token, "principal", None)


def carried_principal() -> Principal | None:
    """The principal `Carry` wrote for a background task's worker, or None.

    Read only off a live request: on one, the header is a client's, and
    `Carry` drops it from every tool call anyway. Anything that is not a
    principal `Carry` would write reads as none.
    """
    try:
        mcp_request.get_http_request()
    except RuntimeError:
        pass
    else:
        return None
    try:
        headers = mcp_request.get_http_headers(include_all=True)
    except Exception:  # noqa: BLE001 - off any request there is nothing carried
        return None
    raw = {k.lower(): v for k, v in (headers or {}).items()}.get(CARRIED_PRINCIPAL)
    return _principal_of(raw) if raw else None


def _principal_of(raw: str) -> Principal | None:
    """The principal ``raw`` (`_carried`'s JSON) describes, or None."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    subject, username = data.get("subject"), data.get("username")
    username = username if isinstance(username, str) else None
    if data.get("kind") == "admin":
        return Principal("admin")
    if data.get("kind") == "oidc" and isinstance(subject, str) and subject:
        return Principal("oidc", subject=subject, username=username)
    return None


def _carried(found: Principal) -> bytes:
    """``found`` as `CARRIED_PRINCIPAL` holds it."""
    return json.dumps(
        {"kind": found.kind, "subject": found.subject, "username": found.username}
    ).encode()


def caller() -> Caller:
    """Who is calling this MCP request: read once, here, and handed in.

    Off HTTP it is the stdio caller. A background task's worker has the
    principal `Carry` carried, since its access token comes back without one.
    """
    values = request_values()
    if values is None:
        return Caller.stdio()
    return replace(
        Caller.from_request(*values), principal=principal() or carried_principal()
    )


class Carry(Middleware):
    """Carry the caller's workspace, the session it pins and its principal to
    a background task's worker.

    On every call over HTTP, a ``CARRIED``, ``CARRIED_SESSION`` or
    ``CARRIED_PRINCIPAL`` header the client sent is dropped. For a tool that
    may run as a task (by its own task config), the caller's name, pin and
    principal are read from the request now and written into its headers,
    which the task's snapshot is taken from later.
    In place, in ``scope["headers"]``: Starlette's `Request` reads that list,
    and a `MutableHeaders` over it is a copy the snapshot never sees.

    A request that names no workspace, or names it or its pin badly, is
    refused here, as the tool would refuse it: its worker sees only headers,
    and could neither say why nor tell a URL's name from a header's.
    """

    async def on_call_tool(self, context, call_next):
        try:
            request = mcp_request.get_http_request()
        except Exception:  # noqa: BLE001 - off HTTP there is no request
            request = None
        if request is not None:
            headers = request.scope["headers"]
            key, pin_key = CARRIED.encode(), CARRIED_SESSION.encode()
            who_key = CARRIED_PRINCIPAL.encode()
            dropped = (key, pin_key, who_key)
            headers[:] = [h for h in headers if h[0].lower() not in dropped]
            name = context.message.name
            if await _tasked(context, name):
                caller = Caller.from_request(*values_of(request))
                try:
                    named, pinned = caller.name, caller.pinned
                except ValueError as refusal:
                    raise ToolError(
                        f"Error calling tool {name!r}: {faults.message(refusal)}"
                    ) from refusal
                headers.append((key, named.encode()))
                if pinned:
                    headers.append((pin_key, pinned.encode()))
                if (found := principal()) is not None:
                    headers.append((who_key, _carried(found)))
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
        named = caller()
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
            uri = nameless(without_userinfo(uri))
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
