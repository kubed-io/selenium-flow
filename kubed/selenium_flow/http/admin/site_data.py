"""A workspace's history and saved site data, for a person to read and clear.

None of these touch the live browser: they read and rewrite the workspace
record, so a clear takes effect the next time a browser is opened for it.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ... import faults
from ...site_data import snapshot as site_data
from ...workspace.store import Session, Workspace
from .. import answer
from .workspaces import known_session

log = logging.getLogger(__name__)


async def secret_rows(catalogue) -> list[dict]:
    if catalogue is None:
        return []
    return (await run_in_threadpool(catalogue.listing))["secrets"]


def live_sessions(workspaces, key: str) -> dict[str, Session]:
    """``key``'s sessions that still have a record, by name, in list order:
    only their current pages are History's to keep (SITE-56), and only their
    snapshots its rows count."""
    return dict(workspaces.listed(key))


def mount(mcp, workspaces, catalogue, prefix, guarded, changes) -> None:
    """Mount history and site data: read, clear, and forget one site.

    ``changes`` marks the routes that change what the workspace list shows.
    """

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/history",
        methods=["GET"],
        name="admin_history",
    )
    @guarded
    async def admin_history(request: Request) -> JSONResponse:
        """Where this workspace has been, by host, the current one first: each
        joined with the secrets allowed there and what the snapshot holds for
        it. Built per request; nothing is stored for it."""
        key = request.path_params["key"]
        try:
            secrets = await secret_rows(catalogue)
            # Listed first: a listing moves a record in the shape before
            # WS-81 (WS-90).
            found = await run_in_threadpool(live_sessions, workspaces, key)
            record = await run_in_threadpool(workspaces.store.get, key)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"history for {key}", log)
        if record is None:
            return JSONResponse({"key": key, "sites": [], "clears": []})
        # Each row counts its own session's snapshot: the one its pill opens.
        snapshots = {name: own.site_data for name, own in found.items()}
        joined = site_data.history_view(record.history, snapshots, secrets)
        live = list(found)
        # What Clear would take, by origin: rows are by host, so a second port
        # of the current host has no row and would otherwise hide Clear. Each
        # live session's current page stays (WS-54).
        current = record.history_cleared(live).history
        clears = [v["origin"] for v in record.history if v not in current]
        return JSONResponse({"key": key, **joined, "clears": clears})

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/history",
        methods=["DELETE"],
        name="admin_history_clear",
    )
    @guarded
    @changes
    async def admin_history_clear(request: Request) -> JSONResponse:
        """Clear: the history down to each session's current site. Site data
        and the live browser are untouched, and the history expires on its own
        anyway."""
        key = request.path_params["key"]

        def clear(record: Workspace) -> tuple[Workspace, list]:
            cleared = record.history_cleared(live)
            left = site_data.history_hosts(cleared.history)
            hosts = [
                h for h in site_data.history_hosts(record.history) if h not in left
            ]
            return cleared, hosts

        try:
            live = list(await run_in_threadpool(live_sessions, workspaces, key))
            # Never a record in the shape before WS-81 as it reads (WS-90).
            _, cleared = await run_in_threadpool(workspaces.change_shared, key, clear)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"clearing the history of {key}", log)
        return JSONResponse({"cleared": cleared or []})

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/site-data",
        methods=["GET"],
        name="admin_site_data",
    )
    @guarded
    async def admin_site_data(request: Request) -> JSONResponse:
        """What a reopened browser gets back: the snapshot by host, each in
        full, values masked, the hosts the workspace went to first. No secrets:
        those are History's."""
        key = request.path_params["key"]
        try:
            skey, named = await run_in_threadpool(
                known_session, workspaces, key, request
            )
            record = await run_in_threadpool(workspaces.store.get, key)
            own = await run_in_threadpool(workspaces.store.get, skey)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"site data for {key}", log)
        # The snapshot is the picked session's own record's (WS-59); the pages
        # that order its hosts are its own entries on the workspace's history,
        # another session's visits being its tab's (UI-65).
        data = own.site_data if own else {}
        history = record.entries(named) if record else []
        listed, details = site_data.views(data, history)
        return JSONResponse({"key": key, **listed, "details": details})

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/site-data",
        methods=["DELETE"],
        name="admin_site_data_clear",
    )
    @guarded
    @changes
    async def admin_site_data_clear(request: Request) -> JSONResponse:
        """Clear: delete the snapshot. The history and the live browser are
        untouched; only the next browser opened comes back signed out."""
        key = request.path_params["key"]

        def clear(record: Session) -> tuple[Session, list]:
            listed = site_data.view(record.site_data, history)
            return record.with_site_data({}), [s["site"] for s in listed["sites"]]

        try:
            skey, named = await run_in_threadpool(
                known_session, workspaces, key, request
            )
            top = await run_in_threadpool(workspaces.store.get, key)
            history = top.entries(named) if top else []
            _, cleared = await run_in_threadpool(workspaces.store.change, skey, clear)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"clearing site data for {key}", log)
        return JSONResponse({"cleared": cleared or []})

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/site-data/{{site}}",
        methods=["DELETE"],
        name="admin_site_data_forget",
    )
    @guarded
    @changes
    async def admin_site_data_forget(request: Request) -> JSONResponse:
        """Forget one site. Parent-domain cookies stay: other sites use them.

        It changes the picked session's record and nothing else: the history stays,
        and a browser open now keeps what it has — only the next one opened
        comes back without it. A save after this saves the site again.
        """
        key = request.path_params["key"]
        host = request.path_params["site"].lower()
        missing = f"no saved site data for {host}"

        # Applied to the record as it is when written, so a save or an open
        # landing while this runs is kept rather than set back.
        def forget(record: Session) -> tuple[Session, dict]:
            left, removed = site_data.forget(record.site_data, host)
            if not (removed["cookies"] or removed["origins"]):
                raise faults.NotFound(missing)
            return record.with_site_data(left), removed

        try:
            skey, _named = await run_in_threadpool(
                known_session, workspaces, key, request
            )
            write = workspaces.store.change
            stored, removed = await run_in_threadpool(write, skey, forget)
            if stored is None:
                raise faults.NotFound(missing)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"forgetting {host} for {key}", log)
        return JSONResponse({"forgotten": removed})
