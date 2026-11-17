"""How an HTTP request is answered: the body, the caller, and a failure's status.

``answer`` serves the browser, flows, files and site-data trees. The admin
routes also answer with HTML and event streams, so they share only the
credential check (``guarded``) and the failure shaper (``refused``). ``errors``
decides what a failure means for every surface; turning that into a response
is this module's job.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from dataclasses import replace
from functools import wraps

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import errors, faults
from ..faults import TooLarge
from ..principal import ADMIN
from ..workspace.workspaces import Caller, values_of
from . import auth, links

JSON_CAP = 2**20
UPLOAD_CAP = 64 * 2**20


async def _within(request: Request, cap: int, what: str) -> None:
    """Read the body, refusing as soon as it passes ``cap``.

    Counted as it arrives, since a chunked request sends no
    ``Content-Length``. The bytes are left on the request, where ``json()``
    and ``form()`` find them.
    """
    refusal = TooLarge(f"{what} is limited to {cap // 2**20} MiB")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise refusal
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise refusal
        chunks.append(chunk)
    request._body = b"".join(chunks)


async def read_body(request: Request) -> dict:
    """The request body as kwargs, from JSON or a multipart form.

    A file part becomes raw bytes in ``content`` with its ``filename``, which
    is what the upload action takes. An absent or unreadable body is ``{}``,
    for the operations that take nothing.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        await _within(request, UPLOAD_CAP, "an upload")
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

    await _within(request, JSON_CAP, "a JSON body")
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 - an empty body is legitimate for an open
        return {}


def as_response(exc: Exception, what: str, log: logging.Logger) -> JSONResponse:
    """One failure, as the status and message ``errors`` says it deserves.

    Logged to the caller's logger, so the record names the module that failed.
    Only a 500 gets a traceback; a refusal is one INFO line.
    """
    status = errors.status_for(exc)
    text = faults.message(exc)
    if status == 500:
        log.error("%s failed\n%s", what, faults.formatted(exc))
    elif status > 500:
        log.warning("%s unavailable (%s): %s", what, status, text)
    else:
        log.info("%s refused (%s): %s", what, status, text)
    return JSONResponse({"error": text}, status_code=status)


def refused(exc: Exception, what: str, log: logging.Logger) -> JSONResponse:
    """A failed admin request, as the status and message ``errors`` gives it.

    Every status logs at INFO, with the path a filesystem failure names, so an
    operator knows which mount. The body gives such a failure by its strerror
    and type, keeping DATA_DIR's layout out of it.
    """
    status = errors.status_for(exc)
    text = faults.message(exc)
    log.info("%s refused (%s): %s", what, status, text)
    if isinstance(exc, OSError) and exc.filename:
        text = f"{exc.strerror or type(exc).__name__} ({type(exc).__name__})"
    return JSONResponse({"error": text}, status_code=status)


async def stream_door(
    request: Request, path: str, token: str | None, door: auth.AdminDoor
) -> JSONResponse | None:
    """Admit an event stream: a signed link for ``path``, else the admin door.

    ``EventSource`` cannot send a header, so a stream is opened by a link the
    admin API minted for an admitted caller. ``None`` admits; a response refuses.
    """
    if token and links.valid(
        path,
        request.query_params.get("exp"),
        request.query_params.get("sig"),
        token,
    ):
        return None
    try:
        await door.admit(request)
    except auth.Refused as refused:
        return JSONResponse({"error": refused.reason}, status_code=refused.status)
    return None


def guarded(door: auth.AdminDoor) -> Callable:
    """A decorator: refuse a request the admin door does not admit, with the
    door's 401 or 403 and its reason."""

    def decorate(handler):
        @wraps(handler)
        async def wrapper(request: Request):
            try:
                await door.admit(request)
            except auth.Refused as refused:
                return JSONResponse(
                    {"error": refused.reason}, status_code=refused.status
                )
            return await handler(request)

        return wrapper

    return decorate


async def answer(
    request: Request,
    token: str | None,
    what: str,
    call: Callable,
    log: logging.Logger,
    *,
    named: bool = True,
    scrubbed: Callable | None = None,
) -> JSONResponse:
    """Authorise, read the body, name the workspace, run ``call``, shape a failure.

    ``call`` takes the :class:`Caller` this request describes and the body.
    With ``named`` an unnamed caller is refused before ``call`` runs; the
    flows tree passes False, because an unnamed caller reads the shared
    library.

    ``scrubbed`` is `Workspaces.scrubbed`, for a tree whose answers may carry a
    page's words without being a call on the browser (site data, files, flow
    documents): the answer and its failure are checked for secrets in the
    worker thread. Only for a synchronous ``call``.
    """
    if not auth.authorized(request, token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await read_body(request)
    except TooLarge as exc:
        return as_response(exc, what, log)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    try:
        caller = Caller.from_request(*values_of(request))
        if token:
            # Only a token holder gets this far, so the caller is the admin.
            caller = replace(caller, principal=ADMIN)
        if named:
            _ = caller.name  # its refusal, if it has one, before the call
        # A synchronous call runs in a worker thread: on the loop one `assert`
        # waiting 900s stalled every other request. An `async def` call is
        # awaited on the loop and takes no thread, so a long poll never queues
        # behind busy threads.
        if inspect.iscoroutinefunction(call):
            result = await call(caller, body)
        elif scrubbed is not None:
            result = await run_in_threadpool(
                scrubbed, caller.named, functools.partial(call, caller, body)
            )
        else:
            result = await run_in_threadpool(call, caller, body)
            if hasattr(result, "__await__"):
                result = await result
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - whatever the call raises is an
        # answer: one that escaped would hand a caller Starlette's HTML 500
        # instead of the JSON error this API promises.
        return as_response(exc, what, log)
