"""The HTTP surface: the same actions, as plain REST.

Served alongside ``/mcp`` so a caller that is not an MCP client — an n8n HTTP
Request node, a shell script, a health probe — can drive the browser without
speaking JSON-RPC. The handlers call ``actions.py`` directly, so the two
surfaces cannot disagree about what an operation does.

Bodies are JSON and mirror the tool parameters one for one.
"""

from __future__ import annotations

import inspect
import logging

import yaml
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import auth, errors, settings
from .actions import Actions
from .openapi import build_spec

log = logging.getLogger(__name__)

# The action behind each path. The signature of each method is what determines
# the accepted body, so this table is the only thing a new endpoint needs.
ENDPOINTS = {
    "open": "open_session",
    "end": "end_browser",
    "navigate": "navigate",
    "interact": "interact",
    "write": "write",
    "press-key": "press_key",
    "extract": "extract",
    "script": "execute_script",
    "screenshot": "screenshot",
    "frame": "frame",
    "resize": "resize",
    "dialog": "dialog",
    "upload": "upload_file",
    "pdf": "save_pdf",
}

# Paths that named an action before it was renamed, kept working because their
# callers cannot be found: an n8n workflow lives in a database, not in this
# repo. Not in ENDPOINTS, so the spec and the wiki describe one name per action
# rather than advertising both.
LEGACY_PATHS = {"close": "end_browser"}


def register(
    mcp: FastMCP,
    actions: Actions,
    token: str | None,
    prefix: str,
    sessions_kind: str = "memory",
    catalogue=None,
) -> None:
    """Register ``/health`` and the ``<prefix>/*`` action endpoints on ``mcp``."""

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        """Readiness probe. Deliberately unauthenticated so a kubelet can call it.

        Reports the Grid as well as the process: this server is useless without
        a reachable Grid, so a pod that cannot see one is not actually ready.
        """
        try:
            ready = bool(actions.grid.status()["value"]["ready"])
            sessions = actions.grid.session_count()
        except Exception as exc:  # noqa: BLE001 - the probe must never raise
            return JSONResponse(
                {"status": "degraded", "grid": actions.grid.url, "error": str(exc)},
                status_code=503,
            )
        return JSONResponse(
            {
                "status": "ok" if ready else "degraded",
                "grid": actions.grid.url,
                "grid_ready": ready,
                "sessions": sessions,
                "saved_sessions": sessions_kind,
            },
            status_code=200 if ready else 503,
        )

    # Built once on first request rather than at import: list_tools is async,
    # and by request time every tool is certainly registered.
    cache: dict[str, dict] = {}

    async def spec() -> dict:
        if "spec" not in cache:
            cache["spec"] = await build_spec(mcp, ENDPOINTS, prefix, bool(token))
        return cache["spec"]

    @mcp.custom_route("/openapi.yaml", methods=["GET"])
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

    @mcp.custom_route("/openapi.json", methods=["GET"])
    async def openapi_json(_request: Request) -> JSONResponse:
        """The same document as JSON, for tools that will not read YAML."""
        return JSONResponse(await spec())

    for path, method_name in {**ENDPOINTS, **LEGACY_PATHS}.items():
        _add(mcp, actions, token, prefix, path, method_name, catalogue)


def _add(mcp, actions, token, prefix, path, method_name, catalogue=None) -> None:
    """Bind one action method to ``<prefix>/<path>``."""
    method = getattr(actions, method_name)
    accepted = set(inspect.signature(method).parameters)
    # `value_from` is not an argument of the action — resolving it needs the
    # secret catalogue, which the behaviour layer deliberately cannot see. It is
    # still a parameter of the *capability*, so this surface has to accept it or
    # the two surfaces differ in what they can do, which is the one divergence
    # this package does not allow. It is dropped from `accepted` by that same
    # signature check, so without this branch an HTTP caller's binding vanished
    # silently while the published spec advertised it.
    binds = method_name == "write"
    if binds:
        accepted = accepted | {"value_from"}

    @mcp.custom_route(f"{prefix}/{path}", methods=["POST"], name=f"browser_{path}")
    async def handler(request: Request) -> JSONResponse:
        if not auth.authorized(request, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await _body(request)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "body must be a JSON object"}, status_code=400
            )

        # Drop unknown keys rather than 400 on them: a caller sending a field a
        # newer version accepts should not be a hard failure.
        kwargs = {k: v for k, v in body.items() if k in accepted}
        guarded: set[str] = set()
        try:
            if binds and kwargs.get("value_from") is not None:
                from . import secrets as secrets_module

                session = kwargs.get("session_id") or ""
                if not session:
                    raise ValueError("session_id is required")
                kwargs, guarded = secrets_module.prepare_write(
                    catalogue, actions, session, kwargs
                )
                kwargs["read_back"] = False
            elif binds:
                kwargs.pop("value_from", None)
            if method_name == "open_session":
                # Same cascade as the tool: server default < client default <
                # body. Inside the try because it VALIDATES as well as merges —
                # an explicit browser is checked strictly — and run outside it
                # that ValueError escaped the handler entirely, so a caller
                # asking for Safari got a bare 500 with no body while the MCP
                # surface got "unknown browser 'safari'; known browsers: ...".
                kwargs = settings.resolve(kwargs) | {
                    k: v for k, v in kwargs.items() if k not in settings.SETTINGS
                }
            result = method(**kwargs)
            if guarded:
                from . import flowrun

                hidden = {kwargs.get(name) for name in guarded}
                result = flowrun.scrub_values(
                    {**result, "value_from": "secret"}, hidden
                )
            return JSONResponse(result)
        except Exception as exc:
            # errors.py decides what the failure means; see it for why a
            # timeout is the caller's problem and an unknown one is ours.
            status = errors.status_for(exc)
            text = errors.message(exc)
            if guarded:
                from . import flowrun

                text = flowrun.scrub(text, {kwargs.get(n) for n in guarded})
            if status >= 500:
                log.exception("%s failed", path)
            else:
                # A refused request is not an incident. Logging a mistyped
                # XPath with a full traceback buried the real failures.
                log.info("%s refused (%s): %s", path, status, text)
            return JSONResponse({"error": text}, status_code=status)

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
    except Exception:  # noqa: BLE001 - an empty body is legitimate for /open
        return {}
