"""One decision about what an HTTP request is, shared by every JSON tree.

Four wrappers used to make this decision separately — ``routes._answer``,
``files.answer``, ``flowapi.answer`` and ``admin.guarded`` — and they had
already drifted: two of them logged an unreachable Grid at WARNING and one at
ERROR, and the browser tree carried a dead ``except ValueError`` branch for a
body reader that has not raised since it learned to tolerate an empty body.

What stays separate is what genuinely differs. ``admin`` keeps its own
decorator because its handlers answer with HTML and event streams, not JSON,
so the only thing it shares is the credential check.

``errors`` is deliberately not imported by any framework: it decides what a
failure *means*, for the MCP surface as much as this one. Turning that meaning
into a response is this module's job, which is why ``as_response`` lives here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

from .. import errors
from ..session import sessions as sessions_module
from . import auth


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


def as_response(exc: Exception, what: str, log: logging.Logger) -> JSONResponse:
    """One failure, as the status and message ``errors`` says it deserves.

    The logger is the caller's own, so a record still names the module that
    failed rather than this one — the point of sharing the decision is that the
    answer reads the same, not that the log stops saying where it came from.
    """
    status = errors.status_for(exc)
    text = errors.message(exc)
    if status == 500:
        # Only the status we do not understand earns a traceback — and it is
        # written through `errors.formatted`, not `log.exception`, because the
        # frames quote the Grid URL with its credentials and nothing sanitises
        # what the logger writes otherwise (Copilot, #36).
        log.error("%s failed\n%s", what, errors.formatted(exc))
    elif status > 500:
        log.warning("%s unavailable (%s): %s", what, status, text)
    else:
        # A refused request is not an incident. Logging a mistyped XPath with a
        # full traceback buried the real failures.
        log.info("%s refused (%s): %s", what, status, text)
    return JSONResponse({"error": text}, status_code=status)


async def answer(
    request: Request,
    token: str | None,
    what: str,
    call: Callable,
    log: logging.Logger,
    *,
    named: bool = True,
) -> JSONResponse:
    """Authorise, read the body, name the session, run ``call``, shape a failure.

    ``call`` takes the session name and the body. One signature is what lets one
    wrapper serve every tree.

    ``named`` is why this is a keyword rather than an assumption. A browser or a
    file belongs to a caller, so naming none is a refusal. A **flow** does not:
    an unnamed caller reads the shared ``global`` library, which is deliberate
    (``library_from``, not ``name_from``), and those trees resolve the library
    themselves from the request. Demanding a name for them turned "here is the
    shared library" into "name your session" — a refusal in place of an answer.

    Naming happens inside the try on purpose: an unnamed request and one
    carrying two names are both refusals ``errors`` already classifies, and they
    read the same here as everywhere else.
    """
    if not auth.authorized(request, token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await read_body(request)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    try:
        name = sessions_module.name_from(request) if named else ""
        result = call(name, body)
        if hasattr(result, "__await__"):
            result = await result
        return JSONResponse(result)
    except Exception as exc:  # noqa: BLE001 - whatever the call raises is an
        # answer, not a crash: `errors` classifies every failure these trees can
        # produce, and a handler that let one escape would hand a caller an
        # HTML 500 from Starlette instead of the JSON error this API promises.
        return as_response(exc, what, log)
