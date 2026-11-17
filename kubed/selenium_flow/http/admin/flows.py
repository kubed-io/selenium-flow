"""Flows, for a person rather than an agent.

This addresses any workspace and skips ``flowapi.writable``, the gate that
keeps agents out of the shared library: an operator may write there.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...flows import api as flowapi
from ...flows import library as flowlib
from ...flows import tags as flowtags
from ...flows import template
from ...flows.shape import Shape
from ...names import GLOBAL_WORKSPACE, library_of, valid_name
from .. import answer
from .workspaces import flows_revision, library, revision

log = logging.getLogger(__name__)


def _uses(document: dict) -> dict[str, list[int]]:
    """Which steps each declared parameter reaches, by index.

    Read with `template.references`, so it counts what the validator counts.
    A parameter nothing reads has an empty list, which the panel shows as a
    likely typo. Read through `Shape`, because a stored flow is a hand-edited
    file and may be malformed.
    """
    shape = Shape(document)
    declared = shape.properties
    if not declared:
        return {}
    uses: dict[str, list[int]] = {name: [] for name in declared}
    for index, step in enumerate(shape.steps):
        if not isinstance(step, dict):
            continue
        for name in template.references(step.get("args")):
            # Only the declared ones: a hand-edited file may hold others.
            if name in uses and index not in uses[name]:
                uses[name].append(index)
    return uses


def mount(mcp, flow_store, schemas, prefix, guarded, changes, configs=None) -> None:
    """Mount the flow catalogue, the editor's read/write/delete, and move.

    ``changes`` marks the routes that change what the workspace list shows.
    ``configs`` is every workspace's config, whose revision the listing's
    carries.
    """

    def enabled() -> None:
        if flow_store is None:
            raise ValueError(flowapi.OFF)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/flows", methods=["GET"], name="admin_flows"
    )
    @guarded
    async def admin_flows(request: Request) -> JSONResponse:
        """What this workspace can run: its own flows and the shared library's,
        merged as the agent sees them, each saying where it came from."""
        key = request.path_params["key"]
        workspace = library_of(key)
        # Flows off, or a name that cannot be a directory: an empty list, so
        # the panel still renders.
        if flow_store is None or workspace is None:
            return JSONResponse(
                {"key": key, "workspace": workspace, "enabled": False, "flows": []}
            )
        # Read before the listing: an edit between the two then costs one
        # redundant refresh, where the other order would pair old summaries
        # with the new revision and the page would never repaint.
        rev = await run_in_threadpool(
            lambda: flows_revision(
                revision(flow_store, workspace),
                revision(flow_store, GLOBAL_WORKSPACE),
                configs.revision(workspace) if configs is not None else "",
            )
        )
        try:
            payload = await run_in_threadpool(
                flowapi.catalogue, flow_store, workspace, True
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"flows for {key}", log)
        return JSONResponse(
            {
                "key": key,
                "enabled": True,
                "rev": rev,
                **payload,
            }
        )

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/flows/{{name}}",
        methods=["GET", "PUT", "DELETE"],
        name="admin_flow",
    )
    @guarded
    @changes
    async def admin_flow(request: Request) -> JSONResponse:
        """Read, rewrite or remove one flow.

        A rewrite takes YAML and stores it verbatim, comments and order
        included, after validating it as `save_flow` does.
        """
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            workspace = library(key)
            enabled()
            if request.method in ("GET", "DELETE"):
                # The flow as resolved: this workspace's, else the shared one.
                found = await run_in_threadpool(
                    flowapi.read_one, flow_store, workspace, name, True
                )
                where = found["workspace"]
                if request.method == "GET":
                    text = await run_in_threadpool(
                        flow_store.read_text, where, name
                    )
                    return JSONResponse(
                        {**found, "yaml": text or "", "uses": _uses(found)}
                    )
                # From the folder it lives in, which may be the shared one.
                removed = await run_in_threadpool(flow_store.delete, where, name)
                return JSONResponse(
                    {"deleted": removed, "workspace": where, "name": name}
                )

            body = await answer.read_body(request)
            text = body.get("yaml") if isinstance(body, dict) else None
            return JSONResponse(
                await flowapi.save_text(flow_store, workspace, name, text, schemas)
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"flow {name} for {key}", log)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/flows/{{name}}/tags",
        methods=["PUT"],
        name="admin_flow_tags",
    )
    @guarded
    @changes
    async def admin_flow_tags(request: Request) -> JSONResponse:
        """Set one flow's tags where it lives, rewriting only its top-level
        `tags` in the stored text."""
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            workspace = library(key)
            enabled()
            body = await answer.read_body(request)
            tags = flowtags.read(body.get("tags") if isinstance(body, dict) else None)
            found = await run_in_threadpool(
                flowapi.read_one, flow_store, workspace, name, True
            )
            where = found["workspace"]
            text = await run_in_threadpool(flow_store.read_text, where, name)
            if text is None:  # pragma: no cover - read_one just found it
                raise ValueError(f"no flow called {name!r} in {where}")
            rewritten = flowlib.with_tags(text, tags)
            flowlib.check_size(rewritten)
            await run_in_threadpool(flow_store.write_text, where, name, rewritten)
            return JSONResponse(
                {"saved": True, "workspace": where, "name": name, "tags": tags}
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"tags of {name} for {key}", log)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/flows/{{name}}/move",
        methods=["POST"],
        name="admin_move_flow",
    )
    @guarded
    @changes
    async def admin_move_flow(request: Request) -> JSONResponse:
        """Move one flow into another library; `global` is one like any other,
        so promoting and claiming are both a move."""
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            workspace = library(key)
            enabled()
            body = await answer.read_body(request)
            target = valid_name(
                body.get("to") if isinstance(body, dict) else None, "workspace name"
            )
            found = await run_in_threadpool(
                flowapi.read_one, flow_store, workspace, name, True
            )
            source = found["workspace"]
            if source == target:
                return JSONResponse(
                    {"moved": False, "workspace": target, "name": name}
                )
            text = await run_in_threadpool(flow_store.read_text, source, name)
            if text is None:  # pragma: no cover - read_one just found it
                raise ValueError(f"no flow called {name!r} in {source}")
            # Write, then delete: a failure between leaves it in both places,
            # where the other order would lose it.
            await run_in_threadpool(flow_store.write_text, target, name, text)
            await run_in_threadpool(flow_store.delete, source, name)
            log.info("flow %s moved from %s to %s", name, source, target)
            return JSONResponse(
                {"moved": True, "from": source, "workspace": target, "name": name}
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"moving {name} for {key}", log)
