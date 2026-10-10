"""The Captured tab's API: what a workspace's browsers logged and fetched.

Console and network are read through ``Workspaces.captured`` — the same view the
``workspace://console`` and ``workspace://network`` resources serve — so the tab
and the model cannot disagree. A response body is asked of the live browser and
never reopens one (plan ruling 10). The live tail is a signed-link stream, the
same door as ``/admin/events``, because ``EventSource`` cannot send a header.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

import anyio
from sse_starlette import EventSourceResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import answer, links
from .workspaces import HEARTBEAT

log = logging.getLogger(__name__)

TAIL_PATH = "/admin/workspaces/{key}/tail"
KINDS = ("console", "network")
NO_SESSION = (
    "no session is open in this workspace; a body lives only as long as its browser"
)


def tail_path(key: str) -> str:
    """The unprefixed path a tail link for ``key`` is signed over, the key
    quoted as it is on the wire, so a link for one workspace opens no other."""
    return TAIL_PATH.format(key=quote(key, safe=""))


class _Stream(EventSourceResponse):
    """The tail, subscribed for exactly as long as the response runs.

    Around the ASGI call rather than inside the generator, for the same reason
    as the workspace list's: a client that goes away leaves the generator paused
    at its ``yield``, which nothing closes until the garbage collector does.
    The waiter is cancelled by the response's task group; ``close`` only
    unregisters.
    """

    def __init__(self, subscription, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._subscription = subscription

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._subscription.close()


def mount(mcp, actions, workspaces, capture, token, prefix, guarded, door) -> None:
    """Mount console, network, one request, its body, and the live tail."""

    def tail_url(key: str) -> str:
        path = tail_path(key)
        return prefix + (links.sign(path, token) if token else path)

    async def view(request: Request, kind: str, *names: str, **fixed) -> JSONResponse:
        key = request.path_params["key"]
        query = {
            name: request.query_params[name]
            for name in names
            if name in request.query_params
        }
        try:
            shown = await run_in_threadpool(
                workspaces.captured, key, kind, **query, **fixed
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"captured {kind}", log)
        # Minted here so only a caller the admin door admitted ever holds one.
        if kind in KINDS and shown.get("capture"):
            shown = {**shown, "tail_url": tail_url(key)}
        return JSONResponse(shown)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/console",
        methods=["GET"],
        name="admin_console",
    )
    @guarded
    async def admin_console(request: Request) -> JSONResponse:
        """The console entries a workspace's browsers logged."""
        return await view(request, "console", "level", "pages")

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/network",
        methods=["GET"],
        name="admin_network",
    )
    @guarded
    async def admin_network(request: Request) -> JSONResponse:
        """The requests a workspace's browsers made."""
        return await view(request, "network", "filter", "pages")

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/network/{{id}}",
        methods=["GET"],
        name="admin_request",
    )
    @guarded
    async def admin_request(request: Request) -> JSONResponse:
        """One captured request in full."""
        return await view(request, "request", id=request.path_params["id"])

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/network/{{id}}/body",
        methods=["POST"],
        name="admin_request_body",
    )
    @guarded
    async def admin_request_body(request: Request) -> JSONResponse:
        """One captured request's response body, asked of the live browser.

        Never reopens a browser: a body lives only as long as the browser that
        fetched it.
        """
        try:
            session_id = await run_in_threadpool(
                workspaces.browser, request.path_params["key"]
            )
            if not session_id:
                return JSONResponse({"error": NO_SESSION}, status_code=404)
            body = await run_in_threadpool(
                actions.network_body, session_id, request.path_params["id"]
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, "request body", log)
        return JSONResponse(body)

    @mcp.custom_route(
        f"{prefix}{TAIL_PATH}", methods=["GET"], name="admin_capture_tail"
    )
    async def admin_capture_tail(request: Request) -> Response:
        """Rows as they land, one ``data:`` per row; ``{"dropped": n}`` when the
        server-side queue overflowed. Each event is one ``json.dumps``, which
        escapes newlines, so a captured value cannot forge a frame."""
        key = request.path_params["key"]
        refusal = await answer.stream_door(request, tail_path(key), token, door)
        if refusal is not None:
            return refusal
        kind = request.query_params.get("kind")
        if kind not in KINDS:
            return JSONResponse(
                {"error": "kind is console or network"}, status_code=400
            )
        subscription = capture.tail(key, kind)
        stopping = anyio.Event()  # the stream's shutdown event: set() / is_set()

        async def stream():
            while True:
                yield {"data": json.dumps(await subscription.next())}

        return _Stream(
            subscription,
            stream(),
            ping=HEARTBEAT,
            headers={"Cache-Control": "no-cache, no-transform"},
            shutdown_event=stopping,
            shutdown_grace_period=1.0,
        )
