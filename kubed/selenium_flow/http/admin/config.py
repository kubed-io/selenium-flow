"""A workspace's config, for the admin: the only way it is written.

No MCP tool and no Token-door route reaches the file, so an agent cannot move
the boundary its config sets. A refused file is still read and edited here,
since this is how it gets fixed.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...faults import Conflict
from ...names import GLOBAL_WORKSPACE
from ...workspace.config import OFF
from .. import answer
from .workspaces import library

log = logging.getLogger(__name__)


def mount(mcp, configs, prefix, guarded, changes) -> None:
    """Mount `GET`, `PUT` and `DELETE /admin/workspaces/{key}/config`."""

    def state(workspace: str) -> dict:
        """What the file is now: as written, as rows, why it is refused, and
        whether it keeps the workspace."""
        loaded = configs.load(workspace)
        return {
            "text": configs.read_text(workspace),
            "config": configs.rows(loaded),
            "error": loaded.error,
            "kept": configs.kept(workspace),
        }

    def answered(workspace: str, method: str, body) -> dict:
        if not configs.enabled:
            raise Conflict(OFF)
        if workspace == GLOBAL_WORKSPACE:
            # No caller is named `global`, so a file there would apply to none.
            raise ValueError("the shared library has no workspace config")
        if method == "PUT":
            text = body.get("text") if isinstance(body, dict) else None
            if not isinstance(text, str):
                raise ValueError("send the config as {text}: the YAML, as a string")
            configs.save(workspace, text)
        elif method == "DELETE":
            configs.remove(workspace)
        return state(workspace)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/config",
        methods=["GET", "PUT", "DELETE"],
        name="admin_config",
    )
    @guarded
    @changes
    async def admin_config(request: Request) -> JSONResponse:
        """Read, replace or remove a workspace's `workspace.yaml`."""
        key = request.path_params["key"]
        try:
            workspace = library(key)
            body = await answer.read_body(request) if request.method == "PUT" else None
            payload = await run_in_threadpool(answered, workspace, request.method, body)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"the config of {key}", log)
        return JSONResponse(payload)
