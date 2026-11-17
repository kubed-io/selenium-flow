"""Signed file links: what a URL that cannot send a header is allowed to open.

``/files/*``, ``/kept/*``, ``/screenshots/*`` and ``/recordings/*`` are
authorised by a signature over one path and an expiry. They differ only in
where the bytes live, so one factory (``route``) mounts all four. Files on disk
are streamed (``Range``); a Grid download is read as bytes.
"""

from __future__ import annotations

import errno
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from ... import errors, faults
from ...core.browser import is_partial
from ...flows.store import open_regular
from ...names import RECORDINGS_DIR, SCREENSHOTS_DIR, InvalidName
from .. import files, links

log = logging.getLogger(__name__)


def _basename(name: str) -> str:
    """The last path segment of ``name``, whichever separator was used.
    Kept in step with ``naming.safe_name``."""
    return PurePosixPath(str(name).replace("\\", "/")).name


def disposition(name: str) -> str:
    """A ``Content-Disposition`` that survives whatever a site named its file.

    A header is latin-1 and the site chose the name. Per RFC 6266 it carries a
    printable-ASCII ``filename`` with every other character folded to ``_``
    (a CR or LF included), and the real name percent-encoded in ``filename*``.
    """
    base = _basename(name)
    # Quotes and backslashes would end or escape the quoted string.
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
    """The answer to a signed link that does not open: a page for a browser
    that asked for HTML, JSON for anything else."""
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
    None when it does, or when there is no token, so nothing is signed."""
    if not token:
        return None
    why = links.refusal(
        path, request.query_params.get("exp"), request.query_params.get("sig"), token
    )
    return None if why is None else link_refused(request, why)


def fresh_for(request: Request) -> int:
    """Seconds a file may be reused from cache: never past its own link's
    expiry. Unsigned, with auth off, an hour."""
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
        "Content-Disposition": disposition(name),
        # A stored file never changes under its own name.
        "Cache-Control": f"private, max-age={max_age}",
        "X-Content-Type-Options": "nosniff",
    }
    if kind in files.RASTER_TYPES:
        # No sandbox: an extension that reads localStorage throws in an opaque
        # origin. With nosniff these types run nothing. The inline style is
        # Firefox's image viewer.
        headers["Content-Security-Policy"] = NO_SCRIPT
    elif kind.startswith(("video/", "audio/")):
        headers["Content-Security-Policy"] = MEDIA_ONLY
    elif kind != "application/pdf":
        # A site's bytes (a kept html page, an SVG) opened inline would run
        # scripts beside the admin UI and could read its token. Every type but
        # the known inert ones, since a list of dangerous ones misses `.xht`;
        # Chrome's PDF viewer does not load under a sandbox.
        headers["Content-Security-Policy"] = "sandbox"
    return headers


def served(name: str, data: bytes, max_age: int) -> Response:
    """One stored file's bytes, with the headers every stored file gets."""
    return Response(
        data, media_type=files.content_type(name), headers=headers_for(name, max_age)
    )


def signed_refused(exc: Exception, what: str) -> JSONResponse:
    """Answer a signed route's failure with no exception text at all.

    A signed URL is a shareable link, and a failure's text can name the Grid's
    host or a path under `DATA_DIR`. Only the status class reaches the body;
    the detail goes to the log.
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


class PinnedFileResponse(FileResponse):
    """A ``FileResponse`` that streams a descriptor it already checked.

    Starlette reopens ``path`` by name while sending, so a file removed or
    replaced after the check would be streamed with the old size and range
    headers. ``/proc/self/fd/N`` reopens the inode the descriptor holds, name or
    no name, and the descriptor is closed when the response ends however it ends.
    """

    def __init__(self, fd: int, info: os.stat_result, **kwargs) -> None:
        self._fd = fd
        try:
            super().__init__(f"/proc/self/fd/{fd}", stat_result=info, **kwargs)
        except BaseException:
            os.close(fd)
            raise

    async def __call__(self, scope, receive, send) -> None:
        # No pathsend: a server sending that path itself would race the close.
        scope = {**scope, "extensions": {}}
        try:
            await super().__call__(scope, receive, send)
        finally:
            os.close(self._fd)


def _pinned(resolved: Path):
    """The open descriptor and its stat; None when not a regular file."""
    try:
        fd = open_regular(resolved)
    except OSError as exc:
        if exc.errno == errno.EINVAL:
            return None
        raise
    try:
        return fd, os.fstat(fd)
    except BaseException:
        os.close(fd)
        raise


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
) -> None:
    """Mount one signed file route.

    ``owner`` is the URL parameter naming whose file it is (a browser id or a
    workspace); ``path_of`` is the path the signature was minted over; ``read``
    fetches the bytes, in a worker thread; ``path`` instead resolves the file on
    disk and streams it, answering ``Range``. ``closed(name)`` answers 404
    before anything is read, and ``absent`` lists the exceptions that mean the
    file is not there. Anything else goes through ``signed_refused``. ``what``
    names the read in the log, with ``{name}`` and ``{owner}``.
    """

    if (read is None) == (path is None):
        raise ValueError(f"route {name}: give exactly one of read= and path=")

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
                # The bytes streamed are the inode checked, whatever happens to
                # the name meanwhile. A permission fault stays a 5xx.
                pinned = await run_in_threadpool(_pinned, resolved)
                if pinned is None:
                    return JSONResponse({"error": "not found"}, status_code=404)
                fd, info = pinned
                kwargs = {
                    "media_type": files.content_type(leaf),
                    "headers": headers_for(leaf, fresh_for(request)),
                }
                if not Path("/proc/self/fd").is_dir():
                    os.close(fd)
                    return FileResponse(resolved, stat_result=info, **kwargs)
                return PinnedFileResponse(fd, info, **kwargs)
            data = await run_in_threadpool(read, who, leaf)
        except absent:
            return JSONResponse({"error": "not found"}, status_code=404)
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            return signed_refused(exc, what.format(name=leaf, owner=who))
        return served(leaf, data, fresh_for(request))

    handler.__name__ = name
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
        # `absent` stays empty here: the Grid's own status decides what its
        # refusal means. A 404 (the browser or the file is gone) answers 404,
        # but an unreachable Grid must not read as "not found", which would
        # tell a client to stop retrying something a retry could fetch.
        what="reading {name} for {owner}",
    )
    # Absent, or a name the store would never have written.
    gone = (FileNotFoundError, InvalidName)
    route(
        mcp,
        token,
        url=f"{prefix}/kept/{{workspace}}/{{name}}",
        name="kept_file",
        owner="workspace",
        path_of=links.kept_path,
        path=lambda workspace, leaf: flow_store.file_path(workspace, leaf),
        closed=lambda _leaf: flow_store is None,
        absent=gone,
        what="reading kept {name} for {owner}",
    )
    route(
        mcp,
        token,
        url=f"{prefix}/screenshots/{{workspace}}/{{name}}",
        name="screenshot_file",
        owner="workspace",
        path_of=links.screenshot_path,
        path=lambda workspace, leaf: flow_store.file_path(
            workspace, leaf, SCREENSHOTS_DIR
        ),
        closed=lambda _leaf: flow_store is None,
        absent=gone,
        what="reading screenshot {name} for {owner}",
    )
    route(
        mcp,
        token,
        url=f"{prefix}/recordings/{{workspace}}/{{name}}",
        name="recording_file",
        owner="workspace",
        path_of=links.recording_path,
        path=lambda workspace, leaf: flow_store.file_path(
            workspace, leaf, RECORDINGS_DIR
        ),
        closed=lambda _leaf: flow_store is None,
        absent=gone,
        what="reading recording {name} for {owner}",
    )
