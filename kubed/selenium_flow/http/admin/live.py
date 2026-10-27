"""The live view's way in: the mint, and the socket it opens.

The page asks ``GET .../live`` for a short-lived signed URL and opens a
WebSocket on it, because a browser's WebSocket cannot send a header. The mint
sits behind the admin door and binds the link to who was admitted, so the audit
line names the person the link was minted for; the socket takes that link or an
admin credential in a header, checks the workspace again (the browser may have
gone in the minute between) and hands the connection to a `Tunnel`
(spec 2026-10-27-live-view, "Getting in").

This module logs nothing itself: the link's query, a Grid id and the VNC URL
must not reach a line, and `live.audit` is the one place that logs.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from ...bidi.connection import open_socket
from ...config import LiveSettings
from ...faults import SocketClosed
from ...live import audit
from ...live.rfb import Refused as LiveRefused
from ...live.rfb import read_password
from ...live.tunnel import BROKEN, HEARTBEAT, Peer, Tunnel
from ...live.tunnels import Tunnels
from .. import auth, links

LIVE_PATH = "/admin/workspaces/{key}/live"
SOCKET_PATH = "/admin/workspaces/{key}/live/socket"
LINK_TTL = 60

SWITCHED_OFF = "the live view is switched off"
NO_BROWSER = "this workspace has no browser open"
NO_LIVE_VIEW = "this browser has no live view"
EXPIRED = "this live view link has expired: open the tab again"
UNAUTHORIZED = "unauthorized"

NOT_AUTHORIZED = 4401
NOT_AVAILABLE = 4004


def socket_path(key: str) -> str:
    """The unprefixed path a socket link for ``key`` is signed over, the key
    quoted as it is on the wire, so a link for one workspace opens no other."""
    return SOCKET_PATH.format(key=quote(key, safe=""))


def websocket_route(mcp, path: str, endpoint, name: str) -> None:
    """Mount a WebSocket route beside the custom HTTP ones: FastMCP's
    ``custom_route`` makes only HTTP routes, and its app takes these alike."""
    mcp._additional_http_routes.append(WebSocketRoute(path, endpoint, name=name))


class _Viewer:
    """A Starlette WebSocket as the tunnel's `Peer`."""

    def __init__(self, websocket: WebSocket):
        self._socket = websocket
        # The tunnel's pipe and its mode answer send from two tasks.
        self._out = asyncio.Lock()

    async def receive(self) -> bytes | str:
        message = await self._socket.receive()
        if message["type"] == "websocket.disconnect":
            raise SocketClosed(message.get("code"))
        if message.get("bytes") is not None:
            return message["bytes"]
        return message.get("text") or ""

    async def send(self, data: bytes | str) -> None:
        try:
            async with self._out:
                if isinstance(data, bytes):
                    await self._socket.send_bytes(data)
                else:
                    await self._socket.send_text(data)
        except WebSocketDisconnect as gone:
            raise SocketClosed(gone.code) from None
        except (RuntimeError, OSError) as error:
            raise SocketClosed(None, type(error).__name__) from None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        with contextlib.suppress(RuntimeError, OSError, WebSocketDisconnect):
            await self._socket.close(code, reason)


def mount(
    mcp,
    actions,
    workspaces,
    live: LiveSettings,
    token: str | None,
    prefix: str,
    door: auth.AdminDoor,
    tunnels: Tunnels,
    monitor=None,
    *,
    dial: Callable[[str], Awaitable[Peer]] | None = None,
) -> None:
    """Mount the mint and the socket.

    ``dial`` is how the hub is reached; unset, ``open_socket`` as this module
    has it when a viewer connects, so a test may replace either.
    """

    def look(key: str):
        """The workspace's record and why it has no live view, or None."""
        if not live.enabled:
            return None, SWITCHED_OFF
        record = workspaces.store.get(key)
        if record is None or not record.attached:
            return None, NO_BROWSER
        if (record.ran or {}).get("live_view") is not True:
            return None, NO_LIVE_VIEW
        return record, None

    @mcp.custom_route(f"{prefix}{LIVE_PATH}", methods=["GET"], name="admin_live")
    async def admin_live(request: Request) -> JSONResponse:
        """A signed socket URL for this workspace's live view.

        Not ``guarded``: the principal it drops is who the link is bound to.
        """
        try:
            principal = await door.admit(request)
        except auth.Refused as refused:
            return JSONResponse({"error": refused.reason}, status_code=refused.status)
        key = request.path_params["key"]
        _record, why = await run_in_threadpool(look, key)
        if why is not None:
            return JSONResponse({"error": why}, status_code=404)
        path = socket_path(key)
        who = audit.viewer_of(principal)
        return JSONResponse(
            {
                "url": prefix
                + (links.sign(path, token, LINK_TTL, by=who) if token else path),
                "heartbeat": HEARTBEAT,
                "grace": live.grace,
            }
        )

    async def admit(websocket: WebSocket, key: str) -> str | None:
        """Who is watching, or None once the socket has been refused (and closed)."""
        if not token:
            return "open"
        query = websocket.query_params
        by = query.get("by")
        # A link names who it was minted for; one without is never valid here.
        why = (
            "invalid"
            if not by
            else links.refusal(
                socket_path(key), query.get("exp"), query.get("sig"), token, by=by
            )
        )
        if why is None:
            return audit.clean(by)
        try:
            principal = await door.admit(websocket)
        except auth.Refused:
            expired = why == "expired"
            reason = EXPIRED if expired else UNAUTHORIZED
            audit.refused(by if expired and by else None, key, reason)
            await _refuse(websocket, NOT_AUTHORIZED, reason)
            return None
        return audit.viewer_of(principal)

    async def _refuse(websocket: WebSocket, code: int, reason: str) -> None:
        await _Viewer(websocket).close(code, reason)

    async def admin_live_socket(websocket: WebSocket) -> None:
        key = websocket.path_params["key"]
        await websocket.accept()
        try:
            await serve(websocket, key)
        except Exception as error:  # noqa: BLE001 - by its type alone, below
            audit.failed(key, error)
            await _refuse(websocket, BROKEN.code, BROKEN.reason)

    async def serve(websocket: WebSocket, key: str) -> None:
        who = await admit(websocket, key)
        if who is None:
            return
        record, why = await run_in_threadpool(look, key)
        if why is not None:
            await _refuse(websocket, NOT_AVAILABLE, why)
            return
        try:
            password = await run_in_threadpool(read_password, live.password_file)
        except LiveRefused as refused:
            await _refuse(websocket, refused.code, refused.reason)
            return
        grid_id = record.session_id
        connect = dial or open_socket
        def touch() -> None:
            if monitor is not None:
                monitor.touch(grid_id)

        tunnel = Tunnel(
            _Viewer(websocket),
            who=who,
            workspace=key,
            session=audit.session_label(record.opened_at),
            grace=live.grace,
            touch=touch,
        )
        tunnels.add(grid_id, tunnel)
        try:
            await tunnel.run(
                lambda: connect(actions.grid.vnc_url(grid_id)), password
            )
        finally:
            tunnels.discard(grid_id, tunnel)

    websocket_route(
        mcp, f"{prefix}{SOCKET_PATH}", admin_live_socket, "admin_live_socket"
    )
