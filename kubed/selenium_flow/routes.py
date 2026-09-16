"""The HTTP surface: the same capabilities, as REST.

Served alongside ``/mcp`` so a caller that is not an MCP client — an n8n HTTP
Request node, a shell script, a health probe — can drive the browser without
speaking JSON-RPC. The handlers call ``actions.py`` through the same
``SessionManager`` the tools use, so the two surfaces cannot disagree about what
an operation does *or* about whose browser it does it to.

**Paths and methods are declared, not derived** (§F2.13). Generating this
surface from the tool list produced RPC wearing URLs — ``POST /flows/get`` for
what is plainly ``GET /flows/{name}`` — because a tool is a verb with arguments
while a resource is a path plus a method. What the two surfaces still share is
what matters: the bodies and the results come from the same action signatures,
so neither can accept something the other refuses.

**The session is who is calling, never a body field and never a path segment**
— ``X-Session-Key`` or ``?session=``, resolved by ``sessions.name_from``. A
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

from . import errors
from .http import auth
from .session import settings
from .core import browser
from . import secrets as secrets_module
from .session import sessions as sessions_module
from .core.actions import Actions
from .openapi import build_spec
from .session.sessions import SessionManager

log = logging.getLogger(__name__)

# One row per capability: the path under the browser root, and the action it
# calls. The signature of each method is what determines the accepted body, so
# this table plus `actions.py` is the whole definition of an endpoint.
#
# Every one is a POST, reads included. A body is then the same object as a
# tool's arguments and a flow step's `args` — one shape in three places — and a
# GET that waits thirty seconds for an element surprises caches and proxies
# besides (§F2.13, Dr K).
ENDPOINTS = {
    "navigate": "navigate",
    "interact": "interact",
    "drag": "drag",
    "write": "write",
    "press-key": "press_key",
    "extract": "extract",
    "script": "execute_script",
    "assert": "assert",
    "outline": "outline",
    "screenshot": "screenshot",
    "frame": "frame",
    "resize": "resize",
    "dialog": "dialog",
    "upload": "upload_file",
    "pdf": "save_pdf",
}

# `interact` is the one action whose choice is a path segment rather than a body
# field: /browser/interact/click reads as the thing it does, and the enum is
# already closed (§F2.1). The action layer still validates it.
ACTION_IN_PATH = "interact"

# `assert` is a Python keyword, so the one action whose tool name cannot also be
# its method name. The tool, the route and a flow step all say `assert`; the
# method is `assert_`. One alias, in one place, read by everything that
# dispatches — `flowrun` and `tests/test_surfaces.py` included — because two
# places that map a name to a method is how the two surfaces drift apart.
METHOD_ALIASES = {"assert": "assert_"}

# What `resize` changes outlives the page, so the record has to hear about it.
RESHAPES = "resize"


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


def method_for(tool: str) -> str:
    """The ``Actions`` method that serves ``tool``."""
    return METHOD_ALIASES.get(tool, tool)


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
        grid = browser.public_url(actions.grid.url)
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
                    "error": browser.scrub(errors.message(exc), actions.grid.url),
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
        may carry and which this answers to anyone (`browser.public_url`).
        """
        from .openapi import _version

        return JSONResponse(
            {
                "name": "selenium-flow",
                "version": _version(),
                "mount": prefix or "/",
                "mcp": f"{prefix}/mcp",
                "grid": browser.public_url(actions.grid.url),
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

    @mcp.custom_route(browser_root, methods=["POST"], name="browser_open")
    async def open_browser(request: Request) -> JSONResponse:
        """Open this session's browser, or pick up the one it was using."""
        return await _answer(request, token, "open", lambda name, body: (
            sessions.open_browser(
                name,
                url=body.get("url"),
                fresh=body.get("fresh", False),
                **{k: v for k, v in body.items() if k in settings.SETTINGS},
            )
        ))

    @mcp.custom_route(browser_root, methods=["DELETE"], name="browser_end")
    async def end_browser(request: Request) -> JSONResponse:
        """Quit the browser, keeping the session and what it was doing."""
        def ended(name, _body):
            # The browser that was ended is deliberately NOT reported: the Grid's
            # id is how a browser is reached, not part of what a caller is told
            # (E18). Returning it here was the one place that leaked (Copilot, #34).
            sessions.end_browser(name)
            return {"success": True, "session": name}

        return await _answer(request, token, "end", ended)

    @mcp.custom_route(browser_root, methods=["GET"], name="browser_status")
    async def browser_status(request: Request) -> JSONResponse:
        """What this session is and whether it holds a browser. Opens nothing."""
        return await _answer(request, token, "status", lambda name, _body: (
            sessions.describe(name)
        ))

    for path, method_name in ENDPOINTS.items():
        _add(
            mcp, actions, sessions, token, browser_root, path, method_name, catalogue
        )


async def _answer(request, token, what, call) -> JSONResponse:
    """Authorise, name the session, run ``call``, and turn a failure into JSON.

    Every handler here shares it, so a refusal reads the same whichever endpoint
    produced it — and `errors.py` stays the one place that decides what a
    failure means.
    """
    if not auth.authorized(request, token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await _body(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    try:
        name = sessions_module.name_from(request)
        return JSONResponse(call(name, body))
    except Exception as exc:
        # errors.py decides what the failure means; see it for why a timeout is
        # the caller's problem and an unknown one is ours.
        status = errors.status_for(exc)
        text = errors.message(exc)
        if status >= 500:
            if status != 500:
                # No traceback for a condition we DO recognise — a Grid that is
                # unreachable or refusing — whose text is where the Grid URL,
                # credentials included, lives.
                log.error("%s failed (%s): %s", what, status, text)
            else:
                log.exception("%s failed", what)
        else:
            # A refused request is not an incident. Logging a mistyped XPath
            # with a full traceback buried the real failures.
            log.info("%s refused (%s): %s", what, status, text)
        return JSONResponse({"error": text}, status_code=status)


def _add(mcp, actions, sessions, token, prefix, path, method_name, catalogue) -> None:
    """Bind one action to ``<prefix>/<path>``."""
    method = getattr(actions, method_for(method_name))
    # `session_id` is the Grid's, supplied by the session manager. It was never
    # a field a caller filled in and now it is not one it could.
    accepted = set(inspect.signature(method).parameters) - {"session_id"}
    # `secret` is not an argument of the action — resolving it needs the secret
    # catalogue, which the behaviour layer deliberately cannot see. It is still
    # a parameter of the *capability*, so this surface has to accept it or the
    # two surfaces differ in what they can do, which is the one divergence this
    # package does not allow. It is dropped from `accepted` by that same
    # signature check, so without this an HTTP caller's binding vanished
    # silently while the published spec advertised it.
    binds = method_name == "write"
    if binds:
        # `read_back` is an internal switch for a bound write, not a request
        # field: accepting it would let a caller ask for `value: null` with no
        # binding, which neither MCP nor the published spec offers.
        accepted = (accepted | {"secret"}) - {"read_back"}
    in_path = method_name == ACTION_IN_PATH
    route = f"{prefix}/{path}" + ("/{action}" if in_path else "")

    @mcp.custom_route(route, methods=["POST"], name=f"browser_{path}")
    async def handler(request: Request) -> JSONResponse:
        def call(name, body):
            # Unknown keys are dropped rather than refused: a caller sending a
            # field a newer version accepts should not be a hard failure.
            kwargs = {k: v for k, v in body.items() if k in accepted}
            if in_path:
                kwargs["action"] = request.path_params["action"]
            if binds and kwargs.get("secret") is not None:
                # Its own path, because a bound write must not let the shared
                # wrapper store the page it landed on. See secrets.perform_write.
                return secrets_module.perform_write(
                    catalogue, actions, sessions, name, kwargs
                )
            kwargs.pop("secret", None)
            return sessions.act(
                name,
                lambda s: method(s, **kwargs),
                reshapes=method_name == RESHAPES,
            )

        return await _answer(request, token, path, call)

    return handler


async def _body(request: Request) -> dict:
    """The request body as kwargs, from JSON or a multipart form.

    Multipart exists for one reason: uploading a file over HTTP should be a
    normal file upload, not base64 wrapped in JSON. A file part arrives as raw
    bytes in ``content`` with its ``filename`` alongside, which is exactly what
    the upload action already accepts — so the action needs no special case.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        body: dict = {}
        for key, value in form.multi_items():
            filename = getattr(value, "filename", None)
            if filename is not None:
                body["content"] = await value.read()
                # An explicit filename field wins, so a caller can rename it.
                body.setdefault("filename", filename)
            else:
                body[key] = value
        return body

    try:
        return await request.json()
    except Exception:  # noqa: BLE001 - an empty body is legitimate for an open
        return {}
