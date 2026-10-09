"""A small web UI for the workspaces and the sessions open in them, and their files.

Two audiences, one set of routes. ``/admin`` is the page itself and
``/admin/<thing>`` is its data, for a person holding the token: live browsers,
and what each has downloaded.

``/files/*`` is for anything that renders a URL — an ``<img>`` on that page, a
markdown image in a chat transcript, a link sent to someone else — and is
authorised by signature rather than by header, because none of those can set one.

There is no user database and no session cookie. The server's token is the only
credential it has, so the sign-in box asks for that: you have it or you do not.
That is weak as an identity system and exactly right as an access check, since
anyone holding the token can already drive every browser through the API.

The files themselves are the Grid's, not ours — see ``Grid.files``.

One module per tab: ``page`` (the shell), ``workspaces`` (the list and its live
stream), ``site_data`` (history and saved site data), ``files``, ``flows``, and
``signed`` (the links that carry their own authority).
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import answer, links
from . import files, flows, page, signed, site_data
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
) -> workspace_list.Broadcast:
    """Mount the admin pages, their JSON API, and the signed file routes.

    Returns the workspace list's ``Broadcast``, so the server can tell open pages
    a recording was filed.

    ``flow_store`` is the *documents and kept files* store — what
    ``DATA_DIR`` points at — and is deliberately not spelled ``store``:
    ``Workspaces.store`` is a different thing entirely, holding workspace records,
    and the two sat one scope apart with the same name until one shadowed the
    other and a listing died on ``MemoryStore.files``.
    """
    guarded = answer.guarded(token)

    page.mount(mcp, prefix, console_url, frame_ancestors)

    @mcp.custom_route(f"{prefix}/admin/secrets", methods=["GET"], name="admin_secrets")
    @guarded
    async def admin_secrets(_request: Request) -> JSONResponse:
        """The catalogue: every secret this server knows, never a value.

        Never a value: the catalogue has none to give.
        """
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
        mcp, actions, workspaces, flow_store, token, prefix, guarded
    )
    site_data.mount(mcp, workspaces, catalogue, prefix, guarded, broadcast.changes)
    files.mount(
        mcp, actions, workspaces, flow_store, token, prefix, link_ttl,
        broadcast.compute, guarded, broadcast.changes,
    )
    flows.mount(mcp, flow_store, schemas, prefix, guarded, broadcast.changes)
    signed.mount(mcp, actions, flow_store, token, prefix)
    return broadcast
