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
from pathlib import Path, PurePosixPath

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

from . import auth, links
from .browser import DEFAULT_BROWSER, is_partial

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
    if key.startswith("session:"):
        # A caller with nothing stable to key on. It owns its browser by holding
        # the id, so this record exists to give it a place in the history rather
        # than to resolve anyone.
        return {"name": None, "owner": "stateless"}
    return {"name": key, "owner": "saved"}


def _recency(item) -> float:
    """Newest first, so the session someone just used is at the top."""
    return getattr(item[1], "opened_at", 0.0) or 0.0


def _grid_facts(session: dict) -> dict:
    """The few things only the Grid knows about an attached browser."""
    if not session:
        return {"version": None, "node": None}
    return {"version": session.get("version"), "node": session.get("node")}


def register(
    mcp, actions, token: str | None, console_url: str | None = None, sessions=None
) -> None:
    """Mount the admin pages, their JSON API, and the signed file route."""
    console = console_url or os.environ.get("GRID_CONSOLE_URL", DEFAULT_CONSOLE_URL)

    def authorized(request: Request) -> bool:
        return auth.authorized(request, token)

    @mcp.custom_route("/admin", methods=["GET"], name="admin_ui")
    async def admin_ui(_request: Request) -> HTMLResponse:
        """The page itself. Unauthenticated on purpose — it is the sign-in form,
        and every byte of data it shows is fetched separately with the token."""
        return HTMLResponse(page("admin.html", CONSOLE=console))

    def sessions_payload() -> dict:
        """Every flow session, and the browser each one currently holds.

        Flow sessions, not Grid sessions. The Grid is the superset — it runs
        browsers put there by anything at all — and listing those would be
        showing somebody else's work as though it were ours. What matters here
        is the session: who holds it, what it was doing, and whether it still
        has a browser attached.

        Blocking — it talks to the Grid for liveness and file counts — so
        callers on the event loop must run it in a worker thread.
        """
        store = getattr(sessions, "store", None)
        if store is None or not hasattr(store, "records"):
            # A store that cannot enumerate is not an error: sessions still
            # work, there is simply no history to show.
            return {"sessions": []}
        try:
            records = store.records()
        except Exception as exc:  # noqa: BLE001 - a Redis blip is not an outage
            log.info("could not read the session store: %s", exc)
            return {"sessions": []}

        # One Grid listing for the whole payload rather than a liveness call per
        # row: the answer for every session is in it, and it is one round trip.
        running: dict = {}
        try:
            running = {s["session_id"]: s for s in actions.grid.sessions()}
        except Exception as exc:  # noqa: BLE001 - the rows are still worth showing
            log.info("could not read the grid: %s", exc)

        rows = []
        for key, record in sorted(records.items(), key=_recency, reverse=True):
            sid = record.session_id
            live = bool(sid) and sid in running
            count = None
            if live:
                # A file count per row is worth one call each: it is the reason
                # to click into a session, so a list without it is guesswork.
                try:
                    count = len(actions.grid.files(sid))
                except Exception:  # noqa: BLE001 - a session can end mid-call
                    count = 0
            rows.append(
                {
                    # The store key addresses the session on this API. It is not
                    # the browser id, which comes and goes underneath it.
                    "key": key,
                    **owner_label(key),
                    "session_id": sid or None,
                    "attached": bool(sid),
                    "live": live,
                    "url": record.url or None,
                    "browser": (record.settings or {}).get("browser")
                    or DEFAULT_BROWSER,
                    "started": record.opened_at or None,
                    "files_count": count,
                    **_grid_facts(running.get(sid, {})),
                }
            )
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

    def attached_id(key: str) -> str:
        """The browser a flow session currently holds, or "" if none."""
        store = getattr(sessions, "store", None)
        if store is None:
            return ""
        record = store.get(key)
        return record.session_id if record and record.attached else ""

    @mcp.custom_route(
        "/admin/sessions/{key}",
        methods=["DELETE"],
        name="admin_end_session",
    )
    async def admin_end_session(request: Request) -> JSONResponse:
        """End the browser a flow session holds, keeping the session itself.

        **This does not delete the session.** It detaches the browser and leaves
        the record — its browser choice and the page it was on — so the caller's
        next ``open_session`` carries on where it left off rather than starting
        from the server's defaults. A flow session is only ever removed by
        expiring, which is what makes all of this safe to click.

        The Grid reaps an idle browser on its own timeout, so this is not the
        only way one ends. It is the way that does not cost minutes of a scarce
        Grid slot while somebody waits.
        """
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = request.path_params["key"]
        session_id = attached_id(key)
        if not session_id:
            # Nothing attached is success, not a failure: the button's whole
            # job is "make sure this session is not holding a browser".
            return JSONResponse({"success": True, "key": key, "session_id": None})
        # Through the same command the tool uses, so the button and the tool
        # cannot mean different things. Blocking HTTP to the Grid, so off the
        # event loop — a slow Grid would stall every connected dashboard.
        await run_in_threadpool(sessions.end_browser, key)
        return JSONResponse({"success": True, "key": key, "session_id": session_id})

    @mcp.custom_route(
        "/admin/sessions/{key}/files",
        methods=["GET", "DELETE"],
        name="admin_files",
    )
    async def admin_files(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = request.path_params["key"]
        # Files belong to the browser, which the flow session may not have. A
        # detached session has none rather than an error — it had them, and the
        # Grid deleted them with the browser.
        session_id = attached_id(key)
        if not session_id:
            detail = {"key": key, "session_id": None, "attached": False}
            try:
                listing = await run_in_threadpool(sessions_payload)
                detail = next(
                    (s for s in listing["sessions"] if s["key"] == key), detail
                )
            except Exception as exc:  # noqa: BLE001 - a Grid blip, not a failure
                log.info("session header for %s unavailable: %s", key, exc)
            return JSONResponse({"key": key, "session": detail, "files": []})
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
            detail = {"key": key, "session_id": session_id}
            try:
                listing = await run_in_threadpool(sessions_payload)
                detail = next(
                    (s for s in listing["sessions"] if s["key"] == key),
                    {"key": key, "session_id": session_id, "live": False},
                )
            except Exception as exc:  # noqa: BLE001 - a Grid blip, not a failure
                log.info("session header for %s unavailable: %s", key, exc)
            return JSONResponse({"key": key, "session": detail, "files": files})
        except Exception as exc:  # noqa: BLE001 - usually a session that ended
            log.info("files for %s failed: %s", key, exc)
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
                "Content-Disposition": f'inline; filename="{PurePosixPath(name).name}"',
                # Safe to cache hard — the signature already bounds the lifetime,
                # and a stored file never changes under its own name.
                "Cache-Control": "private, max-age=3600",
            },
        )
