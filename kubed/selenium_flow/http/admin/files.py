"""A session's three file sections, for a person: list, keep, clear, delete.

The listing and the rules are ``http.files``'; this is the admin's routes over
them, addressed by a session key rather than by whoever is calling.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...names import library_of
from .. import answer
from .. import files as stored
from .sessions import attached_id, header, library

log = logging.getLogger(__name__)


def mount(
    mcp, actions, sessions, flow_store, token, prefix, link_ttl, sessions_payload,
    guarded, changes,
) -> None:
    """Mount the files listing, keep, and clear/delete.

    ``changes`` marks the routes that change what the session list shows.
    """

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/files", methods=["GET"], name="admin_files"
    )
    @guarded
    async def admin_files(request: Request) -> JSONResponse:
        """The three sections at once: Downloads, Screenshots and Files.

        Clearing and deleting have their own route now — one per name, below —
        because DELETE on this same path used to mean only one thing (clear the
        Grid's store) and the admin now offers three different erasures that a
        single boolean could not tell apart.
        """
        key = request.path_params["key"]
        # A session whose name cannot be a directory keeps nothing, so it has no
        # kept files to list — and must not be shown the shared library's.
        session = library_of(key) or ""
        try:
            # Whether there are downloads to list depends on whether the browser
            # is still on the Grid. The record can name one the Grid already
            # reaped — an ordinary state, not a broken one — and handing that
            # dead id to `sections` would fail the whole view in exactly the
            # case kept files exist to survive.
            #
            # `is_alive` is the right question, and the header's `live` is not:
            # that comes from a best-effort bulk listing which reports
            # `live: false` when the Grid could not be read *at all*, so an
            # outage would quietly render an empty download list instead of an
            # error. `is_alive` assumes alive when it cannot tell, so a Grid
            # that is down still surfaces from the call below.
            attached = attached_id(sessions, key)
            alive = attached and await run_in_threadpool(
                actions.grid.is_alive, attached
            )
            live_id = attached if alive else ""
            downloads = (
                await run_in_threadpool(actions.grid.files, live_id) if live_id else []
            )
            if not live_id and flow_store is None:
                # Genuinely nothing: no store configured, and no browser to ask.
                # `files.sections` raises for this — the right answer for a
                # caller asking "what do I have", and the wrong one for a page
                # rendering an (empty) tab of its own.
                listing = {
                    "component": "fileSections",
                    "session": None,
                    "browser": False,
                    "downloads": [],
                    "screenshots": [],
                    "files": [],
                }
            else:
                listing = await run_in_threadpool(
                    stored.sections,
                    actions,
                    sessions,
                    flow_store,
                    token,
                    session,
                    mount=prefix,
                    session_id=live_id,
                    downloads=downloads,
                    ttl=link_ttl,
                )
            return JSONResponse(
                {
                    **listing,
                    "key": key,
                    "session": await header(sessions_payload, key, attached),
                }
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"files for {key}", log)

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/files/{{folder}}/{{name}}/keep",
        methods=["POST"],
        name="admin_keep_file",
    )
    @guarded
    @changes
    async def admin_keep_file(request: Request) -> JSONResponse:
        """Move a screenshot, or copy a download, into the session's own Files
        — so it outlives the browser. There is no matching unkeep: see
        ``files.py``."""
        key = request.path_params["key"]
        folder = request.path_params["folder"]
        name = request.path_params["name"]
        try:
            if folder not in (stored.SCREENSHOTS, stored.DOWNLOADS):
                raise ValueError(
                    f"{folder!r} is not something to keep from: use "
                    f"{stored.SCREENSHOTS} or {stored.DOWNLOADS}"
                )
            kept = await run_in_threadpool(
                stored.keep,
                actions,
                sessions,
                flow_store,
                stored.uri_of(folder, name),
                # The library this key owns, refused the same way the delete
                # and clear handlers refuse it, not the raw key.
                name=library(key),
                session_id=attached_id(sessions, key),
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"keeping {folder}/{name} for {key}", log)
        return JSONResponse(kept)

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/files/{{name}}",
        methods=["DELETE"],
        name="admin_delete_file",
    )
    @guarded
    @changes
    async def admin_delete_file(request: Request) -> JSONResponse:
        """Clear a whole section, or delete one file from Files — the name in
        the path decides which, since ``downloads`` and ``screenshots`` are
        reserved names Files itself can never hold (§F4.6)."""
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            if name == stored.DOWNLOADS:
                # Clears the Grid's whole store, which is all it offers: no
                # per-file delete. Screenshots and Files are ours and
                # elsewhere, so they are untouched — which is exactly what
                # makes this safe to put behind a button (§F1.10).
                session_id = attached_id(sessions, key)
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
            removed = await run_in_threadpool(
                stored.delete_one, flow_store, library(key), name
            )
            return JSONResponse(removed)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"clearing/deleting {name} for {key}", log)
