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

import json
import logging
import mimetypes
import os
from pathlib import Path

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

from . import links
from .browser import is_partial

log = logging.getLogger(__name__)

STATIC_DIR = "static"
# What the Grid console is reachable at *from a browser*. Behind the shared
# ingress both halves sit on one host, so the console is simply the root; the
# default says so, and an env var covers any other arrangement.
DEFAULT_CONSOLE_URL = "/"

IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml")

EVENTS_PATH = "/admin/events"
# Fast enough that a session appears to show up instantly, slow enough that the
# Grid is asked twice a second at worst no matter how many pages are open.
POLL_SECONDS = 2.0
# Seconds of no change before a keepalive comment goes out.
HEARTBEAT = 20.0


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


def owner_label(key: str) -> dict:
    """A caller key turned into something worth showing next to a session.

    The three key shapes carry different amounts of meaning. A name someone
    chose is worth showing as-is; a negotiated transport id is noise, so it is
    reported as a kind rather than printed.
    """
    if key.startswith("named:"):
        return {"name": key[len("named:") :], "owner": "named"}
    if key.startswith("mcp:"):
        return {"name": None, "owner": "mcp client"}
    if key == "stdio":
        return {"name": None, "owner": "stdio"}
    return {"name": key, "owner": "saved"}


def register(
    mcp, actions, token: str | None, console_url: str | None = None, sessions=None
) -> None:
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

    def sessions_payload() -> dict:
        """Every running browser, with the count that makes a row worth a click.

        Blocking — it talks to the Grid — so callers on the event loop must run
        it in a worker thread.
        """
        # Who owns which browser, if the store can say. Best-effort: a store
        # without owners(), or a Redis blip, costs the name and nothing else.
        owners: dict[str, str] = {}
        store = getattr(sessions, "store", None)
        if store is not None and hasattr(store, "owners"):
            try:
                owners = store.owners()
            except Exception as exc:  # noqa: BLE001
                log.info("could not read session owners: %s", exc)

        rows = []
        for session in actions.grid.sessions():
            sid = session["session_id"]
            # A file count per row is worth one call each: it is the reason to
            # click into a session, so a list without it is a list of guesses.
            try:
                count = len(actions.grid.files(sid))
            except Exception:  # noqa: BLE001 - a session can end mid-listing
                count = 0
            # The Grid is the superset: it runs every browser on it, whoever
            # asked for one. A session this server holds a record for is ours;
            # anything else was opened by something we know nothing about, and
            # saying so is more useful than listing it as though it were ours.
            mine = sid in owners
            named = owner_label(owners[sid]) if mine else {"name": None, "owner": None}
            rows.append({**session, **named, "flow": mine, "files_count": count})
        return {"sessions": rows}

    @mcp.custom_route("/admin/sessions", methods=["GET"], name="admin_sessions")
    async def admin_sessions(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        payload = await run_in_threadpool(sessions_payload)
        # A signed URL for the event stream, because EventSource cannot send an
        # Authorization header — the same reason the file route is signed. It is
        # minted here so it is only ever handed to a caller that had the token.
        return JSONResponse(
            {
                **payload,
                "events_url": links.sign(EVENTS_PATH, token) if token else EVENTS_PATH,
            }
        )

    @mcp.custom_route("/admin/events", methods=["GET"], name="admin_events")
    async def admin_events(request: Request) -> Response:
        """The session list, pushed when it changes.

        There is nothing to subscribe to — a browser appears on the Grid, put
        there by anything at all — so the change has to be discovered by asking.
        Asking once here, for every connected page, is the point: the polling
        that would otherwise happen in each open tab collapses into one loop.
        """
        if not (
            authorized(request)
            or (
                token
                and links.valid(
                    EVENTS_PATH,
                    request.query_params.get("exp"),
                    request.query_params.get("sig"),
                    token,
                )
            )
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        async def stream():
            last = None
            quiet = 0.0
            while True:
                try:
                    payload = json.dumps(
                        await run_in_threadpool(sessions_payload), sort_keys=True
                    )
                except Exception as exc:  # noqa: BLE001 - the Grid can blip
                    log.info("event poll failed: %s", exc)
                    payload = last
                if payload is not None and payload != last:
                    last = payload
                    quiet = 0.0
                    yield f"data: {payload}\n\n"
                elif quiet >= HEARTBEAT:
                    # A comment keeps proxies from closing an idle stream, and
                    # tells the page the connection is alive rather than stuck.
                    quiet = 0.0
                    yield ": ping\n\n"
                await anyio.sleep(POLL_SECONDS)
                quiet += POLL_SECONDS

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                # Tells nginx-style proxies not to buffer, which would hold every
                # event until the response ended — i.e. never.
                "X-Accel-Buffering": "no",
            },
        )

    @mcp.custom_route(
        "/admin/sessions/{session_id}",
        methods=["DELETE"],
        name="admin_end_session",
    )
    async def admin_end_session(request: Request) -> JSONResponse:
        """End a browser from the dashboard, giving its Grid slot back now.

        The Grid reaps an idle session on its own timeout, so this is not the
        only way one ends — it is the way that does not cost five minutes of a
        scarce slot while somebody waits. The listing already shows which
        sessions this server has no record of, which are the ones most likely
        to be worth ending by hand.

        Goes through ``actions.close_session`` rather than the Grid directly, so
        there is one path a session ends by. The saved-session mapping is left
        alone deliberately: its owner's next call finds the browser gone and
        transparently reopens where it left off, which is better than being told
        an admin deleted something.
        """
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        session_id = request.path_params["session_id"]
        try:
            # Blocking HTTP to the Grid, so off the event loop — a slow Grid
            # would otherwise stall every other connected dashboard with it.
            result = await run_in_threadpool(actions.close_session, session_id)
        except Exception as exc:  # noqa: BLE001 - usually a session that ended
            log.info("ending %s failed: %s", session_id, exc)
            return JSONResponse({"error": str(exc)}, status_code=502)
        log.info("session %s ended from the admin UI", session_id)
        return JSONResponse(result)

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
            # The detail view leads with a header about the session itself, so
            # it is sent alongside rather than fetched separately. Best-effort:
            # the files are what was asked for, and losing the header is a worse
            # answer than no answer only if it takes the files down with it.
            detail = {"session_id": session_id}
            try:
                listing = await run_in_threadpool(sessions_payload)
                detail = next(
                    (s for s in listing["sessions"] if s["session_id"] == session_id),
                    {"session_id": session_id, "live": False},
                )
            except Exception as exc:  # noqa: BLE001 - a Grid blip, not a failure
                log.info("session header for %s unavailable: %s", session_id, exc)
            return JSONResponse(
                {"session_id": session_id, "session": detail, "files": files}
            )
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
