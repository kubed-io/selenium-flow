"""A session's history and saved site data, for a person to read and clear.

None of these touch the live browser: they read and rewrite the session
record, so a clear takes effect the next time a browser is opened for it.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ... import faults
from ...session.store import SessionRecord
from ...site_data import snapshot as site_data
from .. import answer

log = logging.getLogger(__name__)


async def secret_rows(catalogue) -> list[dict]:
    if catalogue is None:
        return []
    return (await run_in_threadpool(catalogue.listing))["secrets"]


def mount(mcp, sessions, catalogue, prefix, guarded) -> None:
    """Mount history and site data: read, clear, and forget one site."""

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/history",
        methods=["GET"],
        name="admin_history",
    )
    @guarded
    async def admin_history(request: Request) -> JSONResponse:
        """Where this session has been, by host, the current one first: each
        joined with the secrets allowed there and what the snapshot holds for
        it. Built per request; nothing is stored for it."""
        key = request.path_params["key"]
        try:
            secrets = await secret_rows(catalogue)
            record = await run_in_threadpool(sessions.store.get, key)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"history for {key}", log)
        if record is None:
            return JSONResponse({"key": key, "sites": [], "clears": []})
        joined = site_data.history_view(record.history, record.site_data, secrets)
        # What Clear would take, by origin: rows are by host, so a second port
        # of the current host is no row of its own and would otherwise leave
        # Clear hidden with something still to clear (Copilot, #51).
        clears = [v["origin"] for v in record.history[1:]]
        return JSONResponse({"key": key, **joined, "clears": clears})

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/history",
        methods=["DELETE"],
        name="admin_history_clear",
    )
    @guarded
    async def admin_history_clear(request: Request) -> JSONResponse:
        """Clear: the history down to the current site. Site data and the live
        browser are untouched, and the history expires on its own anyway."""
        key = request.path_params["key"]

        def clear(record: SessionRecord) -> tuple[SessionRecord, list]:
            hosts = site_data.history_hosts(record.history)[1:]
            return record.history_cleared(), hosts

        try:
            _, cleared = await run_in_threadpool(sessions.store.change, key, clear)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"clearing the history of {key}", log)
        return JSONResponse({"cleared": cleared or []})

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/site-data",
        methods=["GET"],
        name="admin_site_data",
    )
    @guarded
    async def admin_site_data(request: Request) -> JSONResponse:
        """What a reopened browser gets back: the snapshot by host, each in
        full, values masked, the hosts the session went to first. No secrets:
        those are History's."""
        key = request.path_params["key"]
        try:
            record = await run_in_threadpool(sessions.store.get, key)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"site data for {key}", log)
        data, history = (record.site_data, record.history) if record else ({}, [])
        listed, details = site_data.views(data, history)
        return JSONResponse({"key": key, **listed, "details": details})

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/site-data",
        methods=["DELETE"],
        name="admin_site_data_clear",
    )
    @guarded
    async def admin_site_data_clear(request: Request) -> JSONResponse:
        """Clear: delete the snapshot. The history and the live browser are
        untouched; only the next browser opened comes back signed out."""
        key = request.path_params["key"]

        def clear(record: SessionRecord) -> tuple[SessionRecord, list]:
            listed = site_data.view(record.site_data, record.history)
            return record.with_site_data({}), [s["site"] for s in listed["sites"]]

        try:
            _, cleared = await run_in_threadpool(sessions.store.change, key, clear)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"clearing site data for {key}", log)
        return JSONResponse({"cleared": cleared or []})

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/site-data/{{site}}",
        methods=["DELETE"],
        name="admin_site_data_forget",
    )
    @guarded
    async def admin_site_data_forget(request: Request) -> JSONResponse:
        """Forget one site. Parent-domain cookies stay: other sites use them.

        It changes the session's store and nothing else: the history stays,
        and a browser open now keeps what it has — only the next one opened
        comes back without it. A save after this saves the site again.
        """
        key = request.path_params["key"]
        host = request.path_params["site"].lower()
        missing = f"no saved site data for {host}"

        # Applied to the record as it is when written, so a save or an open
        # landing while this runs is kept rather than set back.
        def forget(record: SessionRecord) -> tuple[SessionRecord, dict]:
            left, removed = site_data.forget(record.site_data, host)
            if not (removed["cookies"] or removed["origins"]):
                raise faults.NotFound(missing)
            return record.with_site_data(left), removed

        try:
            write = sessions.store.change
            stored, removed = await run_in_threadpool(write, key, forget)
            if stored is None:
                raise faults.NotFound(missing)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"forgetting {host} for {key}", log)
        return JSONResponse({"forgotten": removed})
