"""One decision about what an HTTP request is, shared by every JSON tree.

Four wrappers used to make this decision separately — ``routes._answer``,
``files.answer``, ``flowapi.answer`` and ``admin.guarded`` — and they had
already drifted: two of them logged an unreachable Grid at WARNING and one at
ERROR, and the browser tree carried a dead ``except ValueError`` branch for a
body reader that has not raised since it learned to tolerate an empty body.

What stays separate is what genuinely differs. ``admin`` has its handlers
answer with HTML and event streams, not only JSON, so what it shares is the
credential check (``guarded``) and the failure shaper (``refused``).

``errors`` is deliberately not imported by any framework: it decides what a
failure *means*, for the MCP surface as much as this one. Turning that meaning
into a response is this module's job, which is why ``as_response`` lives here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import errors, faults
from ..faults import TooLarge
from ..workspace.workspaces import Caller, values_of
from . import auth

JSON_CAP = 2**20
UPLOAD_CAP = 64 * 2**20


async def _within(request: Request, cap: int, what: str) -> None:
    """Read the body, refusing as soon as it passes ``cap``.

    Counted as it arrives rather than from ``Content-Length`` alone, which a
    chunked request does not send. The bytes are left on the request, where
    ``json()`` and ``form()`` find them.
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

    Multipart exists for one reason: uploading a file over HTTP should be a
    normal file upload, not base64 wrapped in JSON. A file part arrives as raw
    bytes in ``content`` with its ``filename`` alongside, which is exactly what
    the upload action already accepts — so the action needs no special case.

    An absent or unreadable body decodes to ``{}`` rather than failing. Every
    one of these trees has an operation that takes nothing, and demanding an
    empty object from it would be ceremony.
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

    The logger is the caller's own, so a record still names the module that
    failed rather than this one — the point of sharing the decision is that the
    answer reads the same, not that the log stops saying where it came from.
    """
    status = errors.status_for(exc)
    text = faults.message(exc)
    if status == 500:
        # Only the status we do not understand earns a traceback — and it is
        # written through `faults.formatted`, not `log.exception`, because the
        # frames quote the Grid URL with its credentials and nothing sanitises
        # what the logger writes otherwise (Copilot, #36).
        log.error("%s failed\n%s", what, faults.formatted(exc))
    elif status > 500:
        log.warning("%s unavailable (%s): %s", what, status, text)
    else:
        # A refused request is not an incident. Logging a mistyped XPath with a
        # full traceback buried the real failures.
        log.info("%s refused (%s): %s", what, status, text)
    return JSONResponse({"error": text}, status_code=status)


def refused(exc: Exception, what: str, log: logging.Logger) -> JSONResponse:
    """A failed admin request, as the status and message ``errors`` gives it.

    The log line keeps whatever ``faults.message`` says, path included — an
    operator chasing an NFS outage needs to know which mount. A real
    filesystem failure's ``str()`` quotes that same path, though, and the body
    a caller reads is not the place for DATA_DIR's layout — the same
    reason ``naming._why_unsaved`` keeps a screenshot-save failure to a type
    name rather than the OSError's own message (Copilot, PR #41).

    Unlike ``as_response`` this is for the admin surface, where a refusal is
    what the page shows beside the button that caused it, so every status
    logs at INFO; ``what`` is the sentence's subject.
    """
    status = errors.status_for(exc)
    text = faults.message(exc)
    log.info("%s refused (%s): %s", what, status, text)
    if isinstance(exc, OSError) and exc.filename:
        text = f"{exc.strerror or type(exc).__name__} ({type(exc).__name__})"
    return JSONResponse({"error": text}, status_code=status)


def guarded(token: str | None) -> Callable:
    """A decorator: refuse a request with no token before the handler sees it.

    A decorator rather than two lines at the top of each route, and the
    reason is not the fourteen lines: every one of these returns data only a
    token-holder may see, so the check has to be impossible to leave out of the
    next one. Written per-route it was seven chances to forget.
    """

    def decorate(handler):
        @wraps(handler)
        async def wrapper(request: Request):
            if not auth.authorized(request, token):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
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
) -> JSONResponse:
    """Authorise, read the body, name the workspace, run ``call``, shape a failure.

    ``call`` takes the :class:`Caller` this request describes, read here once,
    and the body. One signature is what lets one wrapper serve every tree.

    ``named`` is why this is a keyword rather than an assumption. A browser or a
    file belongs to a caller, so naming none is a refusal, before ``call`` runs.
    A **flow** does not: an unnamed caller reads the shared ``global`` library,
    which is deliberate (``caller.library``, not ``caller.name``). Demanding a
    name for them turned "here is the shared library" into "name your workspace"
    — a refusal in place of an answer.

    Naming happens inside the try on purpose: an unnamed request and one
    carrying two names are both refusals ``errors`` already classifies, and they
    read the same here as everywhere else.
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
        if named:
            _ = caller.name  # its refusal, if it has one, before the call
        # In a worker thread: almost every call here is synchronous Selenium,
        # a `requests` call to the Grid or a whole flow run, and on the event
        # loop one `assert` waiting 900s stalled every other request, MCP and
        # `/health` included — FastMCP already runs the same sync tools in a
        # thread pool. An `async def` call only builds its coroutine there,
        # and it is awaited back here on the loop (§F4.19).
        result = await run_in_threadpool(call, caller, body)
        if hasattr(result, "__await__"):
            result = await result
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - whatever the call raises is an
        # answer, not a crash: `errors` classifies every failure these trees can
        # produce, and a handler that let one escape would hand a caller an
        # HTML 500 from Starlette instead of the JSON error this API promises.
        return as_response(exc, what, log)
