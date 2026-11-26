"""The HTTP surface: the browser actions as REST, the probes and the spec.

The handlers go through the same ``Workspaces`` the tools use, so both surfaces
agree on what an action does and whose browser it does it to. Each action's
path and method are columns of its row in ``core/capabilities.py``; a tool is a
verb and a route a path plus a method, so neither is derived from the other.

The workspace is who is calling (``Sf-Workspace`` or ``?workspace=``), never a
body field or a path segment, so there is one browser resource: ``POST
/browser`` opens yours. Only ``/admin`` puts a workspace in a path. Which of its
sessions a call acts in is the body's ``session``, as on the tool, or the pin.
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import replace

import yaml
from kubed.mcp import QuickMCP
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import faults, urls
from . import secrets as secrets_module
from .config import LiveSettings
from .core.actions import Actions
from .core.capabilities import CAPABILITIES, ENDPOINTS, Capability
from .http import answer as answer_module
from .live import control as control_module
from .mcp import resources
from .principal import ADMIN
from .spec import build_spec
from .workspace import settings
from .workspace.workspaces import Workspaces

log = logging.getLogger(__name__)

# `/ready` remembers the Grid's answer this long, since anyone may call it and
# each call dials the Grid twice. Read at call time, so a test can swap the clock.
READY_TTL = 2.0
clock = time.monotonic

def mount(value: str | None) -> str:
    """``ROUTE_PREFIX`` as a path segment every route hangs off, or "" for root.

    `/` means root too: it is what an operator types for "no prefix", and kept
    literally every path would start `//`.
    """
    text = str(value or "").strip().strip("/")
    return f"/{text}" if text else ""


def register(
    mcp: QuickMCP,
    actions: Actions,
    workspaces: Workspaces,
    token: str | None,
    prefix: str = "",
    catalogue=None,
    *,
    live: LiveSettings | None = None,
) -> None:
    """Register the ops endpoints, the spec, and the browser resource on ``mcp``.

    Everything hangs under ``prefix``. The four ops endpoints also answer at
    the root, because a kubelet did not choose the mount.
    """
    browser_root = f"{prefix}/browser"
    live = live if live is not None else LiveSettings()

    # ---- the ops endpoints: one question each -------------------------------

    def ops_route(path: str, name: str):
        """Bind one ops endpoint at the root and under the mount."""

        def bind(handler):
            mcp.custom_route(path, methods=["GET"], name=name)(handler)
            if prefix:
                mcp.custom_route(
                    f"{prefix}{path}", methods=["GET"], name=f"{name}_at_mount"
                )(handler)
            return handler

        return bind

    @ops_route("/health", "health")
    async def health(_request: Request) -> JSONResponse:
        """Liveness: is this process still working?

        Says nothing about the Grid: a probe that failed on a Grid outage would
        restart every replica for another service's fault.
        """
        return JSONResponse({"status": "ok"})

    @ops_route("/started", "started")
    async def started(_request: Request) -> JSONResponse:
        """Startup: has this process finished coming up? It answers once the
        routes are registered."""
        return JSONResponse({"status": "started"})

    # [when, body, status] of the last Grid answer, ok or failing alike.
    answered: list = []

    @ops_route("/ready", "ready")
    async def ready(_request: Request) -> JSONResponse:
        """Readiness: can this process serve a request right now?

        The one that asks the Grid. A 503 takes the pod out of the Service and
        leaves it running.
        """
        now = clock()
        if answered and now - answered[0] < READY_TTL:
            return JSONResponse(answered[1], status_code=answered[2])
        grid = urls.public_url(actions.grid.url)
        try:
            # In a thread: a Grid that has gone away blocks these synchronous
            # calls until they time out, and on the loop `/health` would stall too.
            status = await run_in_threadpool(actions.grid.status)
            ready = bool(status["value"]["ready"])
            running = await run_in_threadpool(actions.grid.session_count)
        except Exception as exc:  # noqa: BLE001 - the probe must never raise
            body, code = {
                "status": "degraded",
                "grid": grid,
                "error": urls.scrub(faults.message(exc), actions.grid.url),
            }, 503
        else:
            body, code = {
                "status": "ok" if ready else "degraded",
                "grid": grid,
                "grid_ready": ready,
                "browsers": running,
                "workspaces": workspaces.kind,
            }, (200 if ready else 503)
        # Measured after the dial, so a slow Grid does not use up its own window.
        answered[:] = [clock(), body, code]
        return JSONResponse(body, status_code=code)

    @ops_route("/info", "info")
    async def info(_request: Request) -> JSONResponse:
        """What this process is: version, mount, the Grid without its
        credentials, and the workspace store. Not a probe."""
        from .spec.builder import _version

        return JSONResponse(
            {
                "name": "selenium-flow",
                "version": _version(),
                "mount": prefix or "/",
                "mcp": f"{prefix}/mcp",
                "grid": urls.public_url(actions.grid.url),
                "workspaces": workspaces.kind,
            }
        )

    # Built once on first request rather than at import: list_tools is async,
    # and by request time every tool is certainly registered.
    cache: dict[str, dict] = {}

    async def spec() -> dict:
        if "spec" not in cache:
            cache["spec"] = await build_spec(mcp, ENDPOINTS, prefix, bool(token))

        return cache["spec"]

    @mcp.custom_route(f"{prefix}/openapi.yaml", methods=["GET"], name="openapi_yaml")
    async def openapi_yaml(_request: Request) -> Response:
        """The HTTP surface as OpenAPI 3.1. Unauthenticated: it describes the
        API and opens nothing."""
        return Response(
            yaml.safe_dump(await spec(), sort_keys=False, width=100),
            media_type="application/yaml",
        )

    @mcp.custom_route(f"{prefix}/openapi.json", methods=["GET"], name="openapi_json")
    async def openapi_json(_request: Request) -> JSONResponse:
        """The same document as JSON, for tools that will not read YAML."""
        return JSONResponse(await spec())

    # ---- the browser this caller holds ------------------------------------
    #
    # Opening and ending it have no path of their own and are served by the
    # workspace manager, not `workspaces.act`. The manager times each as a call
    # that takes no turn (`Calls.timed`).

    def opened(caller, body):
        """Open a session in this workspace, or pick up the one it was using."""
        return workspaces.open_browser(
            caller,
            url=body.get("url"),
            fresh=body.get("fresh", False),
            restore_site_data=body.get("restore_site_data", True),
            session=body.get("session"),
            surface="http",
            **{k: v for k, v in body.items() if k in settings.SETTINGS},
        )

    def ended(caller, body):
        """Quit the browser, keeping the workspace and what it was doing."""
        # The Grid id is never reported: it is how a browser is reached.
        workspaces.end_browser(caller, session=body.get("session"), surface="http")
        return {"success": True, "workspace": caller.name}

    on_the_resource = {
        "open_session": ("open", "browser_open", opened),
        "end_browser": ("end", "browser_end", ended),
    }

    @mcp.custom_route(browser_root, methods=["GET"], name="browser_status")
    async def browser_status(request: Request) -> JSONResponse:
        """What this workspace is and whether it holds a browser. Opens nothing."""
        # Named by `request` here, where MCP says `query` or `header`. With a
        # token only its holder gets here, the admin; an open server has none.
        return await answer_module.answer(
            request,
            token,
            "status",
            lambda caller, _body: workspaces.describe(
                replace(
                    caller, named_by="request", principal=ADMIN if token else None
                )
            ),
            log,
            configs=workspaces.configs,
        )

    # The literal route first, so the listing is never taken for the template.
    @mcp.custom_route(f"{prefix}/site-data", methods=["GET"], name="site_data_list")
    async def site_data_list(request: Request) -> JSONResponse:
        """The sites this workspace has saved data for. Never a value."""
        return await answer_module.answer(
            request, token, "site-data/list",
            lambda caller, _body: resources.site_listing(workspaces, caller), log,
            scrubbed=workspaces.scrubbed, configs=workspaces.configs,
        )

    @mcp.custom_route(
        f"{prefix}/site-data/{{site}}", methods=["GET"], name="site_data_one"
    )
    async def site_data_one(request: Request) -> JSONResponse:
        """One site's saved cookies and storage, httpOnly values masked."""
        site = request.path_params["site"]
        return await answer_module.answer(
            request, token, "site-data/get",
            lambda caller, _body: resources.one_site(workspaces, caller, site),
            log,
            scrubbed=workspaces.scrubbed,
            configs=workspaces.configs,
        )

    # Awaited on the loop: a long poll holding a worker thread would starve
    # every other call while it waited.
    async def awaited(caller, body):
        """Wait for the person to hand back, up to `live.await_cap`."""
        key = await run_in_threadpool(
            workspaces.reading, caller, body.get("session")
        )
        own = await run_in_threadpool(
            workspaces.own, caller, body.get("session"), key
        )
        return await control_module.await_control(
            workspaces.control, workspaces, key,
            cap=live.await_cap, surface="http", own=own,
        )

    waiting = {"await_control": awaited}

    # Every row of the table, at the path and method it declares. A row the
    # resource has no call for is a KeyError at startup, not a missing route.
    for row in CAPABILITIES:
        if row.waits:
            _bind(
                mcp, token, f"{browser_root}/{row.route}", row.http_method,
                f"browser_{row.route}", row.route, waiting[row.name],
                workspaces.configs,
            )
            continue
        if row.route:
            _add(mcp, actions, workspaces, token, browser_root, row, catalogue)
            continue
        what, name, call = on_the_resource[row.name]
        _bind(
            mcp, token, browser_root, row.http_method, name, what, call,
            workspaces.configs,
        )


def _bind(mcp, token, route, http_method, name, what, call, configs) -> None:
    """Mount ``call`` at ``route`` for one method, through ``http.answer``."""

    @mcp.custom_route(route, methods=[http_method], name=name)
    async def handler(request: Request) -> JSONResponse:
        return await answer_module.answer(
            request, token, what, call, log, configs=configs
        )


def _add(mcp, actions, workspaces, token, prefix, row: Capability, catalogue) -> None:
    """Bind one action to ``<prefix>/<route>``, at the method its row declares."""
    method = getattr(actions, row.method)
    # `session_id` comes from the workspace manager, and `library_arg` names the
    # caller, which only a flow run may supply. Both are dropped like any
    # unknown field, so naming another workspace's library falls back to one's own.
    accepted = set(inspect.signature(method).parameters) - {
        "session_id",
        row.library_arg,
    }
    # `secret` is a parameter of `write` the capability, not of the action:
    # resolving it needs the catalogue, which the action cannot see.
    binds = row.name == "write"
    if binds:
        # `read_back` is a bound write's internal switch: accepted, it would let
        # a caller ask for `value: null` with no binding.
        accepted = (accepted | {"secret"}) - {"read_back"}
    path = row.route
    route = f"{prefix}/{path}" + ("/{action}" if row.in_path else "")

    @mcp.custom_route(route, methods=[row.http_method], name=f"browser_{path}")
    async def handler(request: Request) -> JSONResponse:
        def call(caller, body):
            # Unknown keys are dropped, so a field a newer version accepts is
            # no failure.
            kwargs = {k: v for k, v in body.items() if k in accepted}
            # The route's, as on the tool: which session it acts in.
            session = body.get("session")
            if row.in_path:
                kwargs["action"] = request.path_params["action"]
            if binds and kwargs.get("secret") is not None:
                # A bound write must not let `act` store the page it landed on.
                key = workspaces.session_of(caller, session)
                return secrets_module.perform_write(
                    catalogue, actions, workspaces, key, kwargs,
                    surface="http", own=workspaces.own(caller, session, key),
                )
            kwargs.pop("secret", None)
            return workspaces.act(
                caller,
                lambda s: method(s, **kwargs),
                session=session,
                reshapes=row.reshapes,
                tool=row.name,
                surface="http",
                closed=row.closed
                and (lambda name: getattr(actions, row.closed)(name, **kwargs)),
            )

        return await answer_module.answer(
            request, token, path, call, log, configs=workspaces.configs
        )
