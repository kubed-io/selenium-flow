"""Signed file links: what a URL that cannot send a header is allowed to open.

``/files/*``, ``/kept/*`` and ``/screenshots/*`` are for anything that renders a
URL — an ``<img>`` on the admin page, a markdown image in a chat transcript, a
link sent to someone else — and are authorised by signature rather than by
header, because none of those can set one. The signature covers one exact path
and an expiry, so a link grants one file for a while rather than the API.

The three routes differ in where the bytes live and nothing else, which is why
there is one factory (``route``) and three calls to it.
"""

from __future__ import annotations

import logging
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ... import errors, faults
from ...core.browser import is_partial
from ...names import RECORDINGS_DIR, SCREENSHOTS_DIR, InvalidName
from .. import files, links

log = logging.getLogger(__name__)


def _basename(name: str) -> str:
    """The last path segment of ``name``, whichever separator was used.

    ``name`` arrives as a URL path parameter, so it is the caller's string.
    Kept in step with ``naming.safe_name``, which narrows the same thing on
    the way in.
    """
    return PurePosixPath(str(name).replace("\\", "/")).name


def disposition(name: str) -> str:
    """A ``Content-Disposition`` that survives whatever a site named its file.

    A header is encoded as latin-1, and a file name is not ours to choose — the
    site's ``Content-Disposition`` or Chrome picked it. So ``emoji😊.txt``
    raised while the response was being built, and a name containing a quote
    produced a malformed header. Neither name is invalid; both were unfetchable.

    RFC 6266 is the answer, and it is why the field is written twice: a
    printable-ASCII ``filename`` that any client can parse, with every other
    character folded to ``_``, and the real name percent-encoded in
    ``filename*``, which every current browser prefers. Folding rather than
    dropping also closes the header-injection route a raw CR or LF would open.
    """
    base = _basename(name)
    # Quotes and backslashes are printable but would end or escape the quoted
    # string, so they are folded too rather than left to be parsed as syntax.
    ascii_name = re.sub(r'[^\x20-\x7e]|["\\]', "_", base) or "file"
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(base, safe='')}"


# No script and nothing fetched, for a document that needs neither: an inert
# file opened in a tab, and the page saying a link would not open.
NO_SCRIPT = "default-src 'none'; style-src 'unsafe-inline'"

REFUSED_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>body{{font:16px system-ui,sans-serif;display:grid;place-items:center;
min-height:90vh;margin:0;text-align:center}}p{{opacity:.7}}</style>
<main><h1>{title}</h1><p>{detail}</p></main>
"""


def link_refused(request: Request, why: str) -> Response:
    """The answer to a signed link that does not open, for whoever followed it.

    A person gets a page: a raw JSON error in a browser tab reads as the server
    being broken, when the fix is only to ask for a fresh link — and people
    open these from chat transcripts, well after they were made. Anything that
    did not ask for HTML (an ``<img>``, a script) keeps the JSON it always had.
    """
    if why == "expired":
        title, message = "This link has expired", "expired link"
        when = time.strftime(
            "%Y-%m-%d %H:%M UTC", time.gmtime(int(request.query_params["exp"]))
        )
        detail = f"It stopped working at {when}. Ask for a fresh one."
    else:
        title, message = "This link is not valid", "invalid link"
        detail = "It may have been cut short, or this server's key has changed."
    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse({"error": message}, status_code=403)
    return HTMLResponse(
        REFUSED_PAGE.format(title=title, detail=detail),
        status_code=403,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": NO_SCRIPT,
            "X-Content-Type-Options": "nosniff",
        },
    )


def unsigned(request: Request, token: str | None, path: str) -> Response | None:
    """The refusal for a request whose signature does not open ``path``, or
    None when it does — or when there is no token, so nothing is signed."""
    if not token:
        return None
    why = links.refusal(
        path, request.query_params.get("exp"), request.query_params.get("sig"), token
    )
    return None if why is None else link_refused(request, why)


def fresh_for(request: Request) -> int:
    """Seconds a file may be reused from cache: never past its own link's
    expiry, or a copy cached under a short ``link_ttl`` reopens after the link
    stopped working (Copilot, #48). Unsigned, with auth off, an hour."""
    try:
        return max(0, int(request.query_params["exp"]) - int(time.time()))
    except (KeyError, ValueError):
        return 3600


# A video or audio file opened in a tab is a media document that loads itself:
# it needs `media-src 'self'` and nothing else, and like an image it is not
# sandboxed, for the reason RASTER_TYPES gives.
MEDIA_ONLY = "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'"


def headers_for(name: str, max_age: int) -> dict:
    """The headers every stored file is served with; see `served`."""
    kind = files.content_type(name)
    headers = {
        # Named for download, but shown inline when the browser can: the
        # common case is looking at a screenshot, not saving it.
        "Content-Disposition": disposition(name),
        # Safe to cache for as long as the link lasts: a stored file never
        # changes under its own name.
        "Cache-Control": f"private, max-age={max_age}",
        # Served as what its name says and nothing a browser sniffs instead.
        "X-Content-Type-Options": "nosniff",
    }
    if kind in files.RASTER_TYPES:
        # No sandbox: its opaque origin is felt by every extension in the tab,
        # and one that reads localStorage threw on each render until a link
        # hung. With nosniff these types cannot run anything, and `default-src
        # 'none'` backs that — as GitHub serves its avatars. The inline style is
        # Firefox's image viewer.
        headers["Content-Security-Policy"] = NO_SCRIPT
    elif kind.startswith(("video/", "audio/")):
        headers["Content-Security-Policy"] = MEDIA_ONLY
    elif kind != "application/pdf":
        # These bytes are a site's: a page kept by `print(format="html")`, an
        # SVG, an .html some site downloaded. Opened inline on this origin,
        # their scripts would run beside the admin UI and could read its token
        # (Copilot, #40). `sandbox` gives the document an opaque origin and no
        # scripts, so it still shows and cannot act. Every type but the few
        # known inert, because a list of dangerous ones is what misses `.xht`;
        # a PDF is the exception, since Chrome's viewer does not load under it.
        headers["Content-Security-Policy"] = "sandbox"
    return headers


def served(name: str, data: bytes, max_age: int) -> Response:
    """One stored file's bytes, however it was stored.

    Shared by the signed routes that hold bytes — a browser's download — because
    the only thing that differs between them is where the bytes came from. Two
    copies of this is how one of them ends up without the ``Content-Disposition``
    and downloads as ``shot.png`` called ``name``.
    """
    return Response(
        data, media_type=files.content_type(name), headers=headers_for(name, max_age)
    )


def signed_refused(exc: Exception, what: str) -> JSONResponse:
    """Answer a signature-only route's failure with no exception text at all.

    `answer.refused` is for the token-guarded admin surface, where whoever
    is asking already holds the one credential this server has. These
    three routes (``file``, ``kept_file``, ``screenshot_file``) are not
    that: a signed URL is a shareable link with no further auth check, so
    anything `faults.message` might say — a Grid outage's
    `requests.ConnectionError` names `GRID_URL`'s own host:port, a storage
    fault can name a path under `DATA_DIR` — must never reach it. Only
    the status class survives to the body; the real detail still goes to
    the log, same as `refused` (Copilot, PR #41).
    """
    status = errors.status_for(exc)
    log.info("%s refused (%s): %s", what, status, faults.message(exc))
    if status == 404:
        text = "not found"
    elif status < 500:
        text = "this link cannot be served"
    else:
        text = "the file could not be read right now"
    return JSONResponse({"error": text}, status_code=status)


def route(
    mcp,
    token: str | None,
    *,
    url: str,
    name: str,
    owner: str,
    path_of: Callable[[str, str], str],
    read: Callable[[str, str], bytes] | None = None,
    path: Callable[[str, str], Path] | None = None,
    closed: Callable[[str], bool],
    absent: tuple[type[Exception], ...] = (),
    what: str,
    doc: str,
) -> None:
    """Mount one signed file route.

    ``owner`` is the URL parameter naming whose file it is (a browser id or a
    session); ``path_of`` is the path the signature was minted over; ``read``
    fetches the bytes, in a worker thread; ``path`` instead resolves the file on
    disk and streams it, answering ``Range`` so a player can seek.
    ``closed(name)`` answers 404 before
    anything is read — a partial download, or no store to look in — and
    ``absent`` lists the exceptions that mean the file is genuinely not there
    (or a name the store would never have written). Anything else is a fault
    and goes through ``signed_refused``, whose body carries no exception text.
    ``what`` names the read in the log, with ``{name}`` and ``{owner}``.
    """

    async def handler(request: Request) -> Response:
        who = request.path_params[owner]
        leaf = request.path_params["name"]
        refused_link = unsigned(request, token, path_of(who, leaf))
        if refused_link is not None:
            return refused_link
        if closed(leaf):
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            if path is not None:
                resolved = await run_in_threadpool(path, who, leaf)
                # stat, not is_file: is_file turns a permission fault into
                # "absent", and a storage fault must stay a 5xx.
                info = await run_in_threadpool(resolved.stat)
                if not stat.S_ISREG(info.st_mode):
                    return JSONResponse({"error": "not found"}, status_code=404)
                return FileResponse(
                    resolved,
                    media_type=files.content_type(leaf),
                    headers=headers_for(leaf, fresh_for(request)),
                )
            data = await run_in_threadpool(read, who, leaf)
        except absent:
            return JSONResponse({"error": "not found"}, status_code=404)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return signed_refused(exc, what.format(name=leaf, owner=who))
        return served(leaf, data, fresh_for(request))

    handler.__name__ = name
    handler.__doc__ = doc
    mcp.custom_route(url, methods=["GET"], name=name)(handler)


def mount(mcp, actions, flow_store, token, prefix) -> None:
    """Mount the four signed routes."""
    route(
        mcp,
        token,
        url=f"{prefix}/files/{{session_id}}/{{name}}",
        name="file",
        owner="session_id",
        path_of=links.file_path,
        # Looked up per request: the Grid object is the actions', not ours.
        read=lambda session_id, leaf: actions.grid.read_file(session_id, leaf),
        closed=is_partial,
        # The Grid's own status decides what its refusal means: a 404 (the
        # browser or the file is gone) still answers 404, but an unreachable
        # Grid or another failure must not be flattened into "not found" —
        # that tells a client to stop retrying something that could work on a
        # retry (Copilot, PR #41).
        what="reading {name} for {owner}",
        doc="""One stored file, authorised by the signature in its own URL.

        No bearer check here, and that is the point: this route exists to be put
        in an ``<img src>``. The signature covers this exact path and an expiry,
        so the URL grants one file for a while rather than access to the API.
        """,
    )
    # Genuinely absent, or a name the store would never have written (§F1.2's
    # traversal guard) — both answer the same way these routes always have for a
    # bad name. Anything else is a storage fault, not an absence, and must not
    # be reported as one (Copilot, PR #41).
    gone = (FileNotFoundError, InvalidName)
    route(
        mcp,
        token,
        url=f"{prefix}/kept/{{session}}/{{name}}",
        name="kept_file",
        owner="session",
        path_of=links.kept_path,
        path=lambda session, leaf: flow_store.file_path(session, leaf),
        closed=lambda _leaf: flow_store is None,
        absent=gone,
        what="reading kept {name} for {owner}",
        doc="""One kept file, authorised by the signature in its own URL.

        A route of its own rather than a flag on the one above, because it is
        keyed by a different thing: a download belongs to a browser id, a kept
        file to a session name that outlives it. The signature covers whichever
        path it was minted for, so a link to one is not a link to the other.
        """,
    )
    route(
        mcp,
        token,
        url=f"{prefix}/screenshots/{{session}}/{{name}}",
        name="screenshot_file",
        owner="session",
        path_of=links.screenshot_path,
        path=lambda session, leaf: flow_store.file_path(
            session, leaf, SCREENSHOTS_DIR
        ),
        closed=lambda _leaf: flow_store is None,
        absent=gone,
        what="reading screenshot {name} for {owner}",
        doc="""One screenshot, authorised by the signature in its own URL.

        A route of its own rather than a flag on ``kept_file``, for the same
        reason that one is not a flag on ``file``: the signature is minted over
        one exact path, and a screenshot outlives the browser that took it the
        same way a kept file does, but it is not a kept file — it lives in its
        own folder until someone keeps or clears it (§F4.6).
        """,
    )
    route(
        mcp,
        token,
        url=f"{prefix}/recordings/{{session}}/{{name}}",
        name="recording_file",
        owner="session",
        path_of=links.recording_path,
        path=lambda session, leaf: flow_store.file_path(session, leaf, RECORDINGS_DIR),
        closed=lambda _leaf: flow_store is None,
        absent=gone,
        what="reading recording {name} for {owner}",
        doc="""One recording, authorised by the signature in its own URL, streamed
        from disk so a player can seek (Range).""",
    )
