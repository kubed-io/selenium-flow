"""The admin UI: the page, its JSON API under ``/admin``, and the signed file links.

The API admits the server token or, when `oidc.client_id` is set, an admin-UI
JWT holding an admin role; there is no user database or session cookie. The
REST routes that drive browsers stay token-only. The signed links serve
whatever renders a URL and cannot send a header.

One module per tab: ``page`` (the shell), ``workspaces`` (the list and its live
stream), ``site_data`` (history and saved site data), ``files``, ``flows``,
``capture`` (console, network and the live tail), ``live`` (the live view's
socket onto a browser's VNC stream), and ``signed`` (the file links).
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import answer, auth, links
from . import capture as capture_tab
from . import files, flows, page, signed, site_data
from . import live as live_view
from . import workspaces as workspace_list

log = logging.getLogger(__name__)


def register(
    mcp,
    actions,
    token: str | None,
    console_url: str | None = None,
    workspaces=None,
    flow_store=None,
    schemas=None,
    catalogue=None,
    prefix: str = "",
    settings_payload=None,
    link_ttl: int = links.DEFAULT_TTL,
    frame_ancestors: list[str] | None = None,
    door: auth.AdminDoor | None = None,
    oidc_page: str = "",
    capture=None,
    live=None,
    tunnels=None,
    monitor=None,
) -> workspace_list.Broadcast:
    """Mount the admin pages, their JSON API, and the signed file routes.

    Returns the workspace list's ``Broadcast``, so the server can tell open pages
    a recording was filed.

    ``flow_store`` is the flows and files store under ``DATA_DIR``, named apart
    from ``Workspaces.store``, which holds workspace records.
    """
    # The server passes the door `auth.doors` built; without one, the token's.
    door = door or auth.AdminDoor(token)
    guarded = answer.guarded(door)

    page.mount(mcp, prefix, console_url, frame_ancestors, oidc_page)

    @mcp.custom_route(f"{prefix}/admin/secrets", methods=["GET"], name="admin_secrets")
    @guarded
    async def admin_secrets(_request: Request) -> JSONResponse:
        """The catalogue: every secret this server knows, never a value."""
        if catalogue is None:
            return JSONResponse({"enabled": False, "count": 0, "secrets": []})
        try:
            listed = await run_in_threadpool(catalogue.listing)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, "secrets", log)
        return JSONResponse({
            "enabled": True,
            "count": listed["count"],
            "secrets": listed["secrets"],
        })

    @mcp.custom_route(
        f"{prefix}/admin/settings", methods=["GET"], name="admin_settings"
    )
    @guarded
    async def admin_settings(_request: Request) -> JSONResponse:
        """How this server was started: every setting and where it came from.

        Never a sensitive value and never a secret definition; the Secrets tab
        has those. Read-only, like every admin view of configuration.
        """
        if settings_payload is None:
            return JSONResponse({"sections": []})
        return JSONResponse(settings_payload())

    broadcast = workspace_list.mount(
        mcp, actions, workspaces, flow_store, token, prefix, guarded, door,
        live_enabled=bool(live and live.enabled),
    )
    site_data.mount(mcp, workspaces, catalogue, prefix, guarded, broadcast.changes)
    files.mount(
        mcp, actions, workspaces, flow_store, token, prefix, link_ttl,
        broadcast.compute, guarded, broadcast.changes,
    )
    flows.mount(mcp, flow_store, schemas, prefix, guarded, broadcast.changes)
    if capture is not None:
        capture_tab.mount(
            mcp, actions, workspaces, capture, token, prefix, guarded, door
        )
    if live is not None:
        live_view.mount(
            mcp, actions, workspaces, live, token, prefix, door, tunnels, monitor
        )
    signed.mount(mcp, actions, flow_store, token, prefix)
    return broadcast
