"""The HTTP surface: the same capabilities, as REST.

Served alongside ``/mcp`` so a caller that is not an MCP client — an n8n HTTP
Request node, a shell script, a health probe — can drive the browser without
speaking JSON-RPC. The handlers call ``actions.py`` through the same
``SessionManager`` the tools use, so the two surfaces cannot disagree about what
an operation does *or* about whose browser it does it to.

**Paths and methods are declared, not derived** (§F2.13). Generating this
surface from the tool list produced RPC wearing URLs — ``POST /flows/get`` for
what is plainly ``GET /flows/{name}`` — because a tool is a verb with arguments
while a resource is a path plus a method. Each browser action's path and method
are columns of its row in ``core/capabilities.py``, and every row is mounted
here. What the two surfaces still share is what matters: the bodies and the
results come from the same action signatures, so neither can accept something
the other refuses.

**The session is who is calling, never a body field and never a path segment**
— ``X-Session-Key`` or ``?session=``, read by ``Caller.from_request``. A
browser is addressed by naming yourself, which is why there is one browser
resource here rather than one per id: ``POST /browser`` opens *yours*.

The one place a session appears in a path is ``/admin``, which is the same rule
from the other side: the token holder looking across sessions is the only role
that addresses them as resources.
"""

from __future__ import annotations

import inspect
import logging

import yaml
from fastmcp import FastMCP
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import faults, urls
from . import secrets as secrets_module
from .core.actions import Actions
from .core.capabilities import CAPABILITIES, ENDPOINTS, Capability
from .http import answer as answer_module
from .mcp import resources
from .session import settings
from .session.sessions import Caller, SessionManager
from .spec import build_spec

log = logging.getLogger(__name__)

def mount(value: str | None) -> str:
    """``ROUTE_PREFIX`` as a path segment every route hangs off, or "" for root.

    **The whole server moves, not the browser endpoints** (§F1.11). It used to
    rename `/browser` while `/mcp`, `/admin` and `/files` stayed fixed, which is
    backwards: nobody wants the browser endpoints called something else, and
    everybody eventually wants the server mounted under a path.

    `/` and `""` both mean root, because `/` is what an operator types when they
    mean "no prefix" and a literal `/` would make every path start `//`.
    """
    text = str(value or "").strip().strip("/")
    return f"/{text}" if text else ""


def register(
    mcp: FastMCP,
    actions: Actions,
    sessions: SessionManager,
    token: str | None,
    prefix: str = "",
    catalogue=None,
) -> None:
    """Register the ops endpoints, the spec, and the browser resource on ``mcp``.

    ``prefix`` is where the **whole server** is mounted, and every tree is fixed
    beneath it (§F1.11) — the spec included, because a person reading it in a
    browser should find it where everything else lives.

    **The four ops endpoints are the only things served twice** — `/health`,
    `/started`, `/ready` and `/info`: under the prefix like the rest, and at the
    root whatever the prefix is. A probe that 404s after a config change is the
    failure §F1.11 named, and a kubelet is not the one who chose the mount.
    """
    browser_root = f"{prefix}/browser"

    # ---- the ops endpoints -------------------------------------------------
    #
    # One endpoint per question, rather than one endpoint answering several.
    # `/openapi.json` was being used as a liveness probe in this cluster, which
    # it is not: it happens to be unauthenticated and cheap, so it stood in for
    # a probe that did not exist. These are that probe, and each says exactly
    # one thing (Dr K).
    #
    # All four answer at the root AS WELL as under the mount, because their
    # reader is a kubelet or a load balancer that did not choose the mount —
    # and a probe that 404s after a config change is the failure §F1.11 named.

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
        """**Liveness**: is this process still working?

        Deliberately says nothing about the Grid. A liveness probe that failed
        on a Grid outage would restart every replica for a fault in another
        service, which is the one thing a liveness probe must never do — and it
        is why this cluster was probing `/openapi.json` instead.
        """
        return JSONResponse({"status": "ok"})

    @ops_route("/started", "started")
    async def started(_request: Request) -> JSONResponse:
        """**Startup**: has this process finished coming up?

        It answers only once the routes are registered, which is the whole
        question a startup probe asks. Separate from `/health` because they
        differ in what a failure means: not yet, versus no longer.
        """
        return JSONResponse({"status": "started"})

    @ops_route("/ready", "ready")
    async def ready(_request: Request) -> JSONResponse:
        """**Readiness**: can this process serve a request right now?

        This is the one that depends on the Grid, because a server that cannot
        reach one can accept a call and do nothing with it. A 503 here takes the
        pod out of the Service and leaves it running, which is what a Grid
        outage should cost.
        """
        grid = urls.public_url(actions.grid.url)
        try:
            # In a thread: both are synchronous `requests` calls, and a Grid
            # that has gone away blocks until it times out. On the event loop
            # that stall would take `/health` — and every other request — down
            # with it, which is the outage this split exists to survive.
            status = await run_in_threadpool(actions.grid.status)
            ready = bool(status["value"]["ready"])
            running = await run_in_threadpool(actions.grid.session_count)
        except Exception as exc:  # noqa: BLE001 - the probe must never raise
            return JSONResponse(
                {
                    "status": "degraded",
                    "grid": grid,
                    "error": urls.scrub(faults.message(exc), actions.grid.url),
                },
                status_code=503,
            )
        return JSONResponse(
            {
                "status": "ok" if ready else "degraded",
                "grid": grid,
                "grid_ready": ready,
                "browsers": running,
                "sessions": sessions.kind,
            },
            status_code=200 if ready else 503,
        )

    @ops_route("/info", "info")
    async def info(_request: Request) -> JSONResponse:
        """What this process **is** — version, where it is mounted, what is on.

        Not a probe: the question an operator asks when a call went somewhere
        unexpected. The Grid is named without its credentials, which `GRID_URL`
        may carry and which this answers to anyone (`urls.public_url`).
        """
        from .spec.builder import _version

        return JSONResponse(
            {
                "name": "selenium-flow",
                "version": _version(),
                "mount": prefix or "/",
                "mcp": f"{prefix}/mcp",
                "grid": urls.public_url(actions.grid.url),
                "sessions": sessions.kind,
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
        """The HTTP surface as OpenAPI 3.1.

        Unauthenticated, like /health: it is a description of the API, not a way
        into it, and a client that cannot read the contract before presenting a
        token is needlessly awkward to work with.
        """
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
    # One resource, not one per browser id. Which browser is a question about
    # who is asking, and the answer is in the header or the query string.
    #
    # Opening and ending it are the two capabilities with no path of their own.
    # The session manager serves them rather than `sessions.act`, because it is
    # where a browser is made and let go, so their calls are written here; they
    # are mounted with every other row, at the method the row declares.

    def opened(caller, body):
        """Open this session's browser, or pick up the one it was using."""
        return sessions.open_browser(
            caller,
            url=body.get("url"),
            fresh=body.get("fresh", False),
            restore_site_data=body.get("restore_site_data", True),
            **{k: v for k, v in body.items() if k in settings.SETTINGS},
        )

    def ended(caller, _body):
        """Quit the browser, keeping the session and what it was doing."""
        # The browser that was ended is deliberately NOT reported: the Grid's
        # id is how a browser is reached, not part of what a caller is told
        # (E18). Returning it here was the one place that leaked (Copilot, #34).
        sessions.end_browser(caller)
        return {"success": True, "session": caller.name}

    on_the_resource = {
        "open_session": ("open", "browser_open", opened),
        "end_browser": ("end", "browser_end", ended),
    }

    @mcp.custom_route(browser_root, methods=["GET"], name="browser_status")
    async def browser_status(request: Request) -> JSONResponse:
        """What this session is and whether it holds a browser. Opens nothing."""
        # Reported as named by `request` on this surface, as it always has been,
        # where MCP says `query` or `header` (M36): a divergence to settle on
        # its own, not inside a refactor.
        return await _answer(request, token, "status", lambda caller, _body: (
            sessions.describe(Caller(caller.name, "request"))
        ))

    # The same two reads the resources make, beside them the way `/files` is
    # beside its own. Literal route first, so the listing is never the template.
    @mcp.custom_route(f"{prefix}/site-data", methods=["GET"], name="site_data_list")
    async def site_data_list(request: Request) -> JSONResponse:
        """The sites this session has saved data for. Never a value."""
        return await answer_module.answer(
            request, token, "site-data/list",
            lambda caller, _body: resources.site_listing(sessions, caller.name), log,
        )

    @mcp.custom_route(
        f"{prefix}/site-data/{{site}}", methods=["GET"], name="site_data_one"
    )
    async def site_data_one(request: Request) -> JSONResponse:
        """One site's saved cookies and storage, httpOnly values masked."""
        site = request.path_params["site"]
        return await answer_module.answer(
            request, token, "site-data/get",
            lambda caller, _body: resources.one_site(sessions, caller.name, site),
            log,
        )

    # Every row of the table, at the path and method it declares. A row the
    # resource has no call for is a KeyError at startup, not a missing route.
    for row in CAPABILITIES:
        if row.route:
            _add(mcp, actions, sessions, token, browser_root, row, catalogue)
            continue
        what, name, call = on_the_resource[row.name]
        _bind(mcp, token, browser_root, row.http_method, name, what, call)


async def _answer(request, token, what, call) -> JSONResponse:
    """Authorise, name the session, run ``call``, and turn a failure into JSON.

    The decision itself lives in ``http.answer``, shared with the flows and
    files trees so a refusal reads the same whichever one produced it. This
    passes our own logger, so a record still names this module.
    """
    return await answer_module.answer(request, token, what, call, log)


def _bind(mcp, token, route, http_method, name, what, call) -> None:
    """Mount ``call`` at ``route`` for one method, through ``_answer``."""

    @mcp.custom_route(route, methods=[http_method], name=name)
    async def handler(request: Request) -> JSONResponse:
        return await _answer(request, token, what, call)


def _add(mcp, actions, sessions, token, prefix, row: Capability, catalogue) -> None:
    """Bind one action to ``<prefix>/<route>``, at the method its row declares."""
    method = getattr(actions, row.method)
    # `session_id` is the Grid's, supplied by the session manager. It was never
    # a field a caller filled in and now it is not one it could.
    #
    # `library_arg` is the same story for a different reason: it names the
    # *caller*, not the action, and only a flow run is allowed to supply it
    # (Copilot, #41). Dropped the same way an unknown field is — silently,
    # below — rather than refused, so a request naming another session's
    # library over HTTP just falls back to its own, the way naming none at
    # all always has.
    accepted = set(inspect.signature(method).parameters) - {
        "session_id",
        row.library_arg,
    }
    # `secret` is not an argument of the action — resolving it needs the secret
    # catalogue, which the behaviour layer deliberately cannot see. It is still
    # a parameter of the *capability*, so this surface has to accept it or the
    # two surfaces differ in what they can do, which is the one divergence this
    # package does not allow. It is dropped from `accepted` by that same
    # signature check, so without this an HTTP caller's binding vanished
    # silently while the published spec advertised it.
    binds = row.name == "write"
    if binds:
        # `read_back` is an internal switch for a bound write, not a request
        # field: accepting it would let a caller ask for `value: null` with no
        # binding, which neither MCP nor the published spec offers.
        accepted = (accepted | {"secret"}) - {"read_back"}
    path = row.route
    route = f"{prefix}/{path}" + ("/{action}" if row.in_path else "")

    @mcp.custom_route(route, methods=[row.http_method], name=f"browser_{path}")
    async def handler(request: Request) -> JSONResponse:
        def call(caller, body):
            # Unknown keys are dropped rather than refused: a caller sending a
            # field a newer version accepts should not be a hard failure.
            kwargs = {k: v for k, v in body.items() if k in accepted}
            if row.in_path:
                kwargs["action"] = request.path_params["action"]
            if binds and kwargs.get("secret") is not None:
                # Its own path, because a bound write must not let the shared
                # wrapper store the page it landed on. See secrets.perform_write.
                return secrets_module.perform_write(
                    catalogue, actions, sessions, caller.name, kwargs
                )
            kwargs.pop("secret", None)
            return sessions.act(
                caller,
                lambda s: method(s, **kwargs),
                reshapes=row.reshapes,
            )

        return await _answer(request, token, path, call)
