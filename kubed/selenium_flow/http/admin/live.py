"""The live view's way in: the mint, and the socket it opens.

The page asks ``GET .../live`` for a short-lived signed URL and opens a
WebSocket on it, because a browser's WebSocket cannot send a header. The mint
sits behind the admin door and binds the link to who was admitted, so the audit
line names the person the link was minted for; the socket takes that link or an
admin credential in a header, checks the workspace again (the browser may have
gone in the minute between) and hands the connection to a `Tunnel`
(spec 2026-10-27-live-view, "Getting in").

`live.audit` is the place that logs the live view. This module logs only a
refused paste: by our own sentence, or by the failure's type alone. The link's
query, a Grid id, the VNC URL, a driver's own wording and pasted text never
reach a line.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from ... import errors
from ...bidi.connection import open_socket
from ...config import LiveSettings
from ...core import cancel
from ...core.actions import CANNOT_TYPE, IN_A_FRAME, NOTHING_FOCUSED, TOO_DEEP
from ...faults import NotFound, SocketClosed, TooLarge
from ...live import audit
from ...live.rfb import Refused as LiveRefused
from ...live.rfb import read_password
from ...live.tunnel import BROKEN, HEARTBEAT, Peer, Tunnel
from ...live.tunnels import Tunnels
from .. import answer, auth, links
from . import page

log = logging.getLogger(__name__)

LIVE_PATH = "/admin/workspaces/{key}/live"
SOCKET_PATH = "/admin/workspaces/{key}/live/socket"
PASTE_PATH = "/admin/workspaces/{key}/live/paste"
LINK_TTL = 60

SWITCHED_OFF = "the live view is switched off"
NO_BROWSER = "this workspace has no browser open"
NOT_BUILT = "the live view is not built"
NO_LIVE_VIEW = "this browser has no live view"
EXPIRED = "this live view link has expired: open the tab again"
UNAUTHORIZED = "unauthorized"
NEEDS_CONTROL = "paste needs Control: switch the live view to Control first"
PASTE_FAILED = "the paste failed in the browser"
NEEDS_TEXT = "paste needs text: send {\"text\": \"...\"}"

OUR_REFUSALS = (IN_A_FRAME, NOTHING_FOCUSED, TOO_DEEP, CANNOT_TYPE)

NOT_AUTHORIZED = 4401
NOT_AVAILABLE = 4004


def _ours(exc: Exception) -> bool:
    """Whether ``exc`` says only our own words, so the route may repeat them."""
    # The classes first: `TooLarge` is a `ValueError`, and says its own words.
    if isinstance(exc, (NotFound, TooLarge, cancel.Ended)):
        return True
    return isinstance(exc, ValueError) and str(exc) in OUR_REFUSALS


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

    @mcp.custom_route(f"{prefix}/live.js", methods=["GET"], name="admin_live_script")
    async def admin_live_script(request: Request) -> Response:
        """noVNC and the live view, built apart so the admin page loads without it.

        Unauthenticated, like the page that asks for it: one fixed file of code,
        no data. Without a build it is a 404.
        """
        built = await run_in_threadpool(page.live_script)
        if built is None:
            return JSONResponse({"error": NOT_BUILT}, status_code=404)
        body, etag = built
        headers = {
            "Cache-Control": "no-cache",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
        }
        if page._matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(
            body, media_type="text/javascript; charset=utf-8", headers=headers
        )

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
        signed_url = (
            links.sign(path, token, LINK_TTL, by=who, exact=True)
            if token
            else path
        )
        return JSONResponse(
            {
                "url": prefix + signed_url,
                "heartbeat": HEARTBEAT,
                "grace": live.grace,
            }
        )

    @mcp.custom_route(
        f"{prefix}{PASTE_PATH}", methods=["POST"], name="admin_live_paste"
    )
    async def admin_live_paste(request: Request) -> JSONResponse:
        """Type the viewer's pasted text at the element they focused.

        Control only. Neither this module nor `audit.pasted` ever logs the text.
        """
        try:
            principal = await door.admit(request)
        except auth.Refused as refused:
            return JSONResponse({"error": refused.reason}, status_code=refused.status)
        key = request.path_params["key"]
        try:
            body = await answer.read_body(request)
            text = body.get("text") if isinstance(body, dict) else None
            if not isinstance(text, str):
                return JSONResponse({"error": NEEDS_TEXT}, status_code=400)
            who = audit.viewer_of(principal)
            if not tunnels.controlling(key, who):
                return JSONResponse({"error": NEEDS_CONTROL}, status_code=409)
            done = await run_in_threadpool(workspaces.paste, key, text)
        except Exception as exc:  # noqa: BLE001 - every failure is an answer
            if _ours(exc):
                return answer.refused(exc, "paste", log)
            status = errors.status_for(exc)
            log.info("paste refused (%s): %s", status, type(exc).__name__)
            return JSONResponse({"error": PASTE_FAILED}, status_code=status)
        record = workspaces.store.get(key)
        session = audit.session_label(record.opened_at if record else 0)
        audit.pasted(who, key, session, done["typed"])
        return JSONResponse(done)

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
