"""A workspace's file sections, for a person: list, keep, clear, delete.

The listing and the rules are ``http.files``'; this is the admin's routes over
them, addressed by a workspace key rather than by whoever is calling. Downloads
are one session's (WS-52, UI-65): the downloads routes take ``?session=``, a
sub-session's name, the default when absent.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...names import library_of
from .. import answer
from .. import files as stored
from .workspaces import attached_id, header, library, session_key

log = logging.getLogger(__name__)


def mount(
    mcp, actions, workspaces, flow_store, token, prefix, link_ttl, workspaces_payload,
    guarded, changes,
) -> None:
    """Mount the files listing, keep, and clear/delete.

    ``changes`` marks the routes that change what the workspace list shows.
    """

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/files", methods=["GET"], name="admin_files"
    )
    @guarded
    async def admin_files(request: Request) -> JSONResponse:
        """Every section at once: Downloads, Screenshots, Recordings and Files."""
        key = request.path_params["key"]
        # A workspace whose name cannot be a directory keeps nothing, and must
        # not be shown the shared library's files.
        workspace = library_of(key) or ""
        try:
            # The record can name a browser the Grid already reaped, and that
            # dead id would fail the whole view. `is_alive`, not the header's
            # `live`: it assumes alive when it cannot tell, so a Grid that is
            # down still fails below instead of showing no downloads.
            attached = await run_in_threadpool(attached_id, workspaces, key)
            alive = attached and await run_in_threadpool(
                actions.grid.is_alive, attached
            )
            live_id = attached if alive else ""
            downloads = (
                await run_in_threadpool(actions.grid.files, live_id) if live_id else []
            )
            if not live_id and flow_store is None:
                # `files.sections` raises for this; the page wants an empty tab.
                listing = {
                    "component": "fileSections",
                    "workspace": None,
                    "browser": False,
                    "downloads": [],
                    "screenshots": [],
                    "recordings": [],
                    "files": [],
                }
            else:
                listing = await run_in_threadpool(
                    stored.sections,
                    actions,
                    workspaces,
                    flow_store,
                    token,
                    workspace,
                    mount=prefix,
                    session_id=live_id,
                    downloads=downloads,
                    ttl=link_ttl,
                )
            return JSONResponse(
                {
                    **listing,
                    "key": key,
                    "workspace": await header(workspaces_payload, key, attached),
                }
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"files for {key}", log)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/files/downloads",
        methods=["GET"],
        name="admin_downloads",
    )
    @guarded
    async def admin_downloads(request: Request) -> JSONResponse:
        """One session's downloads: its browser's, none when it holds no live
        one."""
        key = request.path_params["key"]
        try:
            session, named = session_key(key, request)
            attached = await run_in_threadpool(attached_id, workspaces, session)
            alive = attached and await run_in_threadpool(
                actions.grid.is_alive, attached
            )
            live_id = attached if alive else ""
            downloads = await run_in_threadpool(
                stored.listing_of,
                actions,
                flow_store,
                stored.DOWNLOADS,
                library_of(key) or "",
                live_id,
                token,
                mount=prefix,
                ttl=link_ttl,
            ) if live_id else []
            return JSONResponse(
                {
                    "key": key,
                    "session": named,
                    "browser": bool(live_id),
                    "downloads": downloads,
                }
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"downloads for {key}", log)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/files/{{folder}}/{{name}}/keep",
        methods=["POST"],
        name="admin_keep_file",
    )
    @guarded
    @changes
    async def admin_keep_file(request: Request) -> JSONResponse:
        """Move a screenshot or a recording, or copy a download (from the
        session ``?session=`` names), into the workspace's Files."""
        key = request.path_params["key"]
        folder = request.path_params["folder"]
        name = request.path_params["name"]
        try:
            if folder not in (stored.SCREENSHOTS, stored.RECORDINGS, stored.DOWNLOADS):
                raise ValueError(
                    f"{folder!r} is not something to keep from: use "
                    f"{stored.SCREENSHOTS}, {stored.RECORDINGS} or {stored.DOWNLOADS}"
                )
            session, _ = session_key(key, request)
            session_id = await run_in_threadpool(attached_id, workspaces, session)
            kept = await run_in_threadpool(
                stored.keep,
                actions,
                workspaces,
                flow_store,
                stored.uri_of(folder, name),
                name=library(key),
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"keeping {folder}/{name} for {key}", log)
        return JSONResponse(kept)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/files/{{name}}",
        methods=["DELETE"],
        name="admin_delete_file",
    )
    @guarded
    @changes
    async def admin_delete_file(request: Request) -> JSONResponse:
        """Clear a whole section, or delete one file from Files: the section
        names are reserved in Files, so the name decides which. Downloads are
        the session's ``?session=`` names."""
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            if name == stored.DOWNLOADS:
                # The Grid's whole store: it has no per-file delete.
                session, _ = session_key(key, request)
                session_id = await run_in_threadpool(
                    attached_id, workspaces, session
                )
                alive = session_id and await run_in_threadpool(
                    actions.grid.is_alive, session_id
                )
                if alive:
                    await run_in_threadpool(actions.grid.clear_files, session_id)
                return JSONResponse(
                    {"success": True, "key": key, "cleared": stored.DOWNLOADS}
                )
            if name == stored.SCREENSHOTS:
                got = await run_in_threadpool(
                    stored.clear_screenshots, flow_store, library(key)
                )
                return JSONResponse({"success": True, "key": key, **got})
            if name == stored.RECORDINGS:
                got = await run_in_threadpool(
                    stored.clear_recordings, flow_store, library(key)
                )
                return JSONResponse({"success": True, "key": key, **got})
            removed = await run_in_threadpool(
                stored.delete_one, flow_store, library(key), name
            )
            return JSONResponse(removed)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"clearing/deleting {name} for {key}", log)
