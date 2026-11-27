"""The Captured tab's API: what a workspace's browsers logged and fetched.

Console and network are read through ``Workspaces.captured``, the view the
``workspace://console`` and ``workspace://network`` resources serve. A response
body is asked of the live browser and never reopens one. The live tail is a
signed-link stream, like ``/admin/events``, because ``EventSource`` cannot send
a header.
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

from ...core.actions import BODY_CLOSED, cut_body
from ...faults import NotFound
from ...workspace import sessions
from .. import answer, links
from .workspaces import HEARTBEAT, known_session, session_key

log = logging.getLogger(__name__)

TAIL_PATH = "/admin/workspaces/{key}/tail"
KINDS = ("console", "network")


def tail_path(key: str) -> str:
    """The unprefixed path a tail link for ``key`` is signed over, the key
    quoted as it is on the wire, so a link for one workspace opens no other. A
    session's key is signed over too, so a link opens no other session."""
    return TAIL_PATH.format(key=quote(key, safe=""))


class _Stream(EventSourceResponse):
    """The tail, subscribed for as long as the response runs.

    Closed around the ASGI call, not in the generator: a client that goes away
    leaves the generator paused at its ``yield`` until garbage collection. The
    waiter is cancelled by the response's task group; ``close`` only
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


def request_uri(workspace: str, name: str, id: str) -> str:
    """Where an agent of ``workspace`` reads request ``id`` of its session
    ``name``: ``default``'s at ``workspace://``, as a caller with no principal
    reads its own (WS-82), any other at ``session://<name>/``."""
    return sessions.pointer(
        sessions.key_of(workspace, name),
        sessions.key_of(workspace, sessions.DEFAULT),
        f"network/{id}",
    )


def mount(mcp, actions, workspaces, capture, token, prefix, guarded, door) -> None:
    """Mount console, network, one request, its body, and the live tail."""

    def tail_url(key: str, name: str) -> str:
        """The tail's URL for session ``name`` of workspace ``key``: its path
        names the workspace, ``?session=`` the session, always, so it opens
        that session whichever heads the list later, and the link is signed
        over the session's own key, so it opens no other."""
        path = tail_path(sessions.key_of(key, name))
        signed = links.sign(path, token) if token else ""
        parts = [f"session={quote(name, safe='')}"]
        if signed:
            parts.append(signed.partition("?")[2])
        return prefix + tail_path(key) + "?" + "&".join(parts)

    async def view(request: Request, kind: str, *names: str, **fixed) -> JSONResponse:
        workspace = request.path_params["key"]
        query = {
            name: request.query_params[name]
            for name in names
            if name in request.query_params
        }
        try:
            key, named = await run_in_threadpool(
                known_session, workspaces, workspace, request
            )
            if kind == "request":
                # The URI it is read at by an agent in that session's workspace.
                fixed["at"] = request_uri(workspace, named, fixed["id"])
            shown = await run_in_threadpool(
                workspaces.captured, key, kind, **query, **fixed
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"captured {kind}", log)
        # Minted here so only a caller the admin door admitted ever holds one.
        if kind in KINDS and shown.get("capture"):
            shown = {**shown, "tail_url": tail_url(workspace, named)}
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
        fetched it. Not scrubbed, but cut at the cap as an agent's is
        (`cut_body`): the action returns it whole.
        """
        try:
            key, named = await run_in_threadpool(
                known_session, workspaces, request.path_params["key"], request
            )
            session_id = await run_in_threadpool(workspaces.browser, key)
            if not session_id:
                raise NotFound(BODY_CLOSED)
            body = await run_in_threadpool(
                actions.network_body, session_id, request.path_params["id"]
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, "request body", log)
        if isinstance(body, dict) and "uri" in body:
            body = {
                **body,
                "uri": request_uri(
                    request.path_params["key"], named, request.path_params["id"]
                ),
            }
        return JSONResponse(cut_body(body))

    @mcp.custom_route(
        f"{prefix}{TAIL_PATH}", methods=["GET"], name="admin_capture_tail"
    )
    async def admin_capture_tail(request: Request) -> Response:
        """Rows as they land, one ``data:`` per row; ``{"dropped": n}`` when the
        server-side queue overflowed. Each event is one ``json.dumps``, which
        escapes newlines, so a captured value cannot forge a frame."""
        try:
            key, _named = await run_in_threadpool(
                session_key, workspaces, request.path_params["key"], request
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        refusal = await answer.stream_door(request, tail_path(key), token, door)
        if refusal is not None:
            return refusal
        kind = request.query_params.get("kind")
        try:
            await run_in_threadpool(
                known_session, workspaces, request.path_params["key"], request
            )
            subscription = capture.tail(key, kind)  # a ValueError for a bad kind
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, "capture tail", log)
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
