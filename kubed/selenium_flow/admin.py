"""A small web UI for the sessions the Grid is running, and their files.

Two audiences, one set of routes. ``/admin`` is the page itself and
``/admin/<thing>`` is its data, for a person holding the token: live browsers,
and what each has downloaded.

``/files/*`` is for anything that renders a URL — an ``<img>`` on that page, a
markdown image in a chat transcript, a link sent to someone else — and is
authorised by signature rather than by header, because none of those can set one.

There is no user database and no session cookie. The server's token is the only
credential it has, so the sign-in box asks for that: you have it or you do not.
That is weak as an identity system and exactly right as an access check, since
anyone holding the token can already drive every browser through the API.

The files themselves are the Grid's, not ours — see ``Grid.files``.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from . import links
from .browser import is_partial

log = logging.getLogger(__name__)

STATIC_DIR = "static"
# What the Grid console is reachable at *from a browser*. Behind the shared
# ingress both halves sit on one host, so the console is simply the root; the
# default says so, and an env var covers any other arrangement.
DEFAULT_CONSOLE_URL = "/"

IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")


def static_path() -> Path:
    """The directory holding the shared CSS and the two pages.

    Packaged under the module when installed; falls back to the repo root so a
    checkout runs without an install step, exactly as the skill does.
    """
    packaged = Path(__file__).parent / STATIC_DIR
    if packaged.is_dir():
        return packaged
    return Path(__file__).parents[2] / STATIC_DIR


def read(name: str) -> str:
    """One file from the static directory."""
    return (static_path() / name).read_text(encoding="utf-8")


def page(name: str, **substitutions: str) -> str:
    """A page with ``__NAME__`` placeholders filled in.

    Deliberately not a template engine. There are two pages, they substitute a
    stylesheet and a URL, and a dependency to do that would be worse than the
    four lines it saves.
    """
    html = read(name)
    shared = {"CSS": read("app.css"), "COMPONENTS": read("components.js")}
    for key, value in {**shared, **substitutions}.items():
        html = html.replace(f"__{key}__", value)
    return html


def content_type(name: str) -> str:
    """The type to serve a stored file as, guessed from its name."""
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def describe(session_id: str, entry: dict, token: str | None) -> dict:
    """One stored file, as the UI needs it: named, sized, and fetchable."""
    name = entry.get("name", "")
    kind = content_type(name)
    return {
        "name": name,
        "size": entry.get("size", 0),
        "created": entry.get("creationTime"),
        "content_type": kind,
        "image": kind in IMAGE_TYPES,
        "url": links.file_url(session_id, name, token),
    }


def register(mcp, actions, token: str | None, console_url: str | None = None) -> None:
    """Mount the admin pages, their JSON API, and the signed file route."""
    console = console_url or os.environ.get("GRID_CONSOLE_URL", DEFAULT_CONSOLE_URL)

    def authorized(request: Request) -> bool:
        if not token:
            return True
        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        return (scheme.lower() == "bearer" and value.strip() == token) or (
            header.strip() == token
        )

    @mcp.custom_route("/admin", methods=["GET"], name="admin_ui")
    async def admin_ui(_request: Request) -> HTMLResponse:
        """The page itself. Unauthenticated on purpose — it is the sign-in form,
        and every byte of data it shows is fetched separately with the token."""
        return HTMLResponse(page("admin.html", CONSOLE=console))

    @mcp.custom_route("/admin/sessions", methods=["GET"], name="admin_sessions")
    async def admin_sessions(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        sessions = []
        for session in actions.grid.sessions():
            # A file count per row is worth one call each: it is the reason to
            # click into a session, so a list without it is a list of guesses.
            try:
                count = len(actions.grid.files(session["session_id"]))
            except Exception:  # noqa: BLE001 - a session can end mid-listing
                count = 0
            sessions.append({**session, "files_count": count})
        return JSONResponse({"sessions": sessions})

    @mcp.custom_route(
        "/admin/sessions/{session_id}/files",
        methods=["GET", "DELETE"],
        name="admin_files",
    )
    async def admin_files(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        session_id = request.path_params["session_id"]
        try:
            if request.method == "DELETE":
                actions.grid.clear_files(session_id)
                return JSONResponse({"success": True})
            files = [
                describe(session_id, entry, token)
                for entry in actions.grid.files(session_id)
            ]
            return JSONResponse({"session_id": session_id, "files": files})
        except Exception as exc:  # noqa: BLE001 - usually a session that ended
            log.info("files for %s failed: %s", session_id, exc)
            return JSONResponse({"error": str(exc)}, status_code=502)

    @mcp.custom_route("/files/{session_id}/{name}", methods=["GET"], name="file")
    async def file(request: Request) -> Response:
        """One stored file, authorised by the signature in its own URL.

        No bearer check here, and that is the point: this route exists to be put
        in an ``<img src>``. The signature covers this exact path and an expiry,
        so the URL grants one file for a while rather than access to the API.
        """
        session_id = request.path_params["session_id"]
        name = request.path_params["name"]
        if token and not links.valid(
            links.file_path(session_id, name),
            request.query_params.get("exp"),
            request.query_params.get("sig"),
            token,
        ):
            return JSONResponse({"error": "invalid or expired link"}, status_code=403)
        if is_partial(name):
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            data = actions.grid.read_file(session_id, name)
        except Exception as exc:  # noqa: BLE001 - gone, or never existed
            log.info("read %s/%s failed: %s", session_id, name, exc)
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(
            data,
            media_type=content_type(name),
            headers={
                # Named for download, but shown inline when the browser can:
                # the common case is looking at a screenshot, not saving it.
                "Content-Disposition": f'inline; filename="{os.path.basename(name)}"',
                # Safe to cache hard — the signature already bounds the lifetime,
                # and a stored file never changes under its own name.
                "Cache-Control": "private, max-age=3600",
            },
        )
