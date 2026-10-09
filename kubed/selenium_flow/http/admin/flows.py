"""Flows, for a person rather than an agent.

The agent-facing ``/flows/*`` surface is scoped to whoever is calling. This one
addresses any session, and it is the **operator** path §F1.2 reserves: it
deliberately does not go through ``flowapi.writable``, because that gate exists
to keep agents out of the live shared library and this is the surface where a
person is present and allowed in.
"""

from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from ...flows import api as flowapi
from ...flows import template
from ...flows.shape import Shape
from ...names import GLOBAL_WORKSPACE, library_of, valid_name
from .. import answer
from .workspaces import library, revision

log = logging.getLogger(__name__)


def _uses(document: dict) -> dict[str, list[int]]:
    """Which steps each declared parameter reaches, by index.

    The admin panel answers "what does this parameter actually do" by listing
    the steps a `${name}` lands in, and that question has to be answered the
    same way the validator answers it — an escaped `$${name}` is not a
    reference, and a reference can be nested arbitrarily deep in an argument.
    So it is `template.references` rather than a regex in the page, which is how
    the two would come to disagree about what counts (§F1.40).

    Declared-but-unused parameters are present with an empty list. That is a
    fact worth showing rather than one to hide: a parameter nothing reads is
    almost always a typo in a step, and the panel can only say so if it is
    told about the parameter at all.

    Read through `Shape`, because a stored flow is a file a person edits
    (§F1.6) and the panel is where an operator goes to fix one.
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
            # Only the declared ones. An undeclared `${name}` cannot be saved
            # through any surface, but these documents are hand-editable files
            # (§F1.6) and the panel must render whatever is on disk.
            if name in uses and index not in uses[name]:
                uses[name].append(index)
    return uses


def mount(mcp, flow_store, schemas, prefix, guarded, changes) -> None:
    """Mount the flow catalogue, the editor's read/write/delete, and move.

    ``changes`` marks the routes that change what the session list shows.
    """

    def enabled() -> None:
        if flow_store is None:
            raise ValueError(flowapi.OFF)

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/flows", methods=["GET"], name="admin_flows"
    )
    @guarded
    async def admin_flows(request: Request) -> JSONResponse:
        """What this session can run: its own flows, plus the shared library.

        The same merge the agent sees, so the admin cannot show a different
        library from the one a run would actually use — each entry says which
        it came from, which is what the UI marks with a globe.
        """
        key = request.path_params["key"]
        workspace = library_of(key)
        # Flows off, or a session whose name cannot be a directory: an empty
        # list with a reason, rather than an error that blanks the whole panel.
        if flow_store is None or workspace is None:
            return JSONResponse(
                {"key": key, "session": workspace, "enabled": False, "flows": []}
            )
        # Read BEFORE the listing, deliberately. An edit landing between the two
        # would otherwise pair the old summaries with the new revision — the
        # page records that token, sees no change on the next poll, and keeps
        # showing what it was already showing. This order errs the other way: a
        # token older than the listing costs one redundant refresh.
        rev = await run_in_threadpool(
            lambda: revision(flow_store, workspace)
            + "+"
            + revision(flow_store, GLOBAL_WORKSPACE)
        )
        try:
            payload = await run_in_threadpool(flowapi.catalogue, flow_store, workspace)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"flows for {key}", log)
        # The revision this listing was built from, so the page can record what
        # it is showing and repaint only when that changes — the same shape the
        # file listing uses, and self-correcting: a failed load records nothing
        # and is retried on the next poll.
        return JSONResponse(
            {
                "key": key,
                "enabled": True,
                "rev": rev,
                **payload,
            }
        )

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/flows/{{name}}",
        methods=["GET", "PUT", "DELETE"],
        name="admin_flow",
    )
    @guarded
    @changes
    async def admin_flow(request: Request) -> JSONResponse:
        """Read, rewrite or remove one flow.

        Rewriting takes **YAML**, not JSON, and stores it verbatim (§F1.14): a
        person wrote these, comments and ordering included, and a save that
        round-tripped through a parsed dict would quietly discard both. It is
        validated against the live step schemas first, exactly as `save_flow`
        is, so the editor cannot store something that would not run.
        """
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            workspace = library(key)
            enabled()
            if request.method in ("GET", "DELETE"):
                # Both act on the flow *as resolved* — this session's if it has
                # one, else the shared library's — so the panel and the buttons
                # address the same document the reader is looking at.
                found = await run_in_threadpool(
                    flowapi.read_one, flow_store, workspace, name
                )
                where = found["session"]
                if request.method == "GET":
                    text = await run_in_threadpool(
                        flow_store.read_text, where, name
                    )
                    return JSONResponse(
                        {**found, "yaml": text or "", "uses": _uses(found)}
                    )
                # Deleted from the folder it lives in, which may be the shared
                # one. That is the operator's call to make, and the UI says so.
                removed = await run_in_threadpool(flow_store.delete, where, name)
                return JSONResponse(
                    {"deleted": removed, "session": where, "name": name}
                )

            body = await answer.read_body(request)
            text = body.get("yaml") if isinstance(body, dict) else None
            return JSONResponse(
                await flowapi.save_text(flow_store, workspace, name, text, schemas)
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"flow {name} for {key}", log)

    @mcp.custom_route(
        f"{prefix}/admin/sessions/{{key}}/flows/{{name}}/move",
        methods=["POST"],
        name="admin_move_flow",
    )
    @guarded
    @changes
    async def admin_move_flow(request: Request) -> JSONResponse:
        """Move one flow into another library.

        There is no separate "promote" (§F1.2): a flow lives in exactly one
        directory, so the only action is *which one*. `global` is a folder like
        any other here — promoting is this verb with `global` as the target, and
        claiming a shared flow is the same verb with a session's name. Moving a
        flow between two sessions therefore needs no new mechanism: push it to
        `global` from one and claim it from the other.
        """
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            workspace = library(key)
            enabled()
            body = await answer.read_body(request)
            target = valid_name(
                body.get("to") if isinstance(body, dict) else None, "session name"
            )
            found = await run_in_threadpool(
                flowapi.read_one, flow_store, workspace, name
            )
            source = found["session"]
            if source == target:
                return JSONResponse(
                    {"moved": False, "session": target, "name": name}
                )
            text = await run_in_threadpool(flow_store.read_text, source, name)
            if text is None:  # pragma: no cover - read_one just found it
                raise ValueError(f"no flow called {name!r} in {source}")
            # Write first, then delete. A failure between the two leaves the
            # flow in both places, which an operator can see and fix; the other
            # order loses it outright.
            await run_in_threadpool(flow_store.write_text, target, name, text)
            await run_in_threadpool(flow_store.delete, source, name)
            log.info("flow %s moved from %s to %s", name, source, target)
            return JSONResponse(
                {"moved": True, "from": source, "session": target, "name": name}
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return answer.refused(exc, f"moving {name} for {key}", log)
