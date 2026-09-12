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
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

from . import auth, errors, files, flows, links
from .browser import DEFAULT_BROWSER, is_partial

log = logging.getLogger(__name__)

STATIC_DIR = "static"
# What the Grid console is reachable at *from a browser*. Behind the shared
# ingress both halves sit on one host, so the console is simply the root; the
# default says so, and an env var covers any other arrangement.
DEFAULT_CONSOLE_URL = "/"

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


def _basename(name: str) -> str:
    """The last path segment of ``name``, whichever separator was used.

    ``name`` arrives as a URL path parameter, so it is the caller's string.
    Kept in step with ``actions._safe_name``, which narrows the same thing on
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


def served(name: str, data: bytes) -> Response:
    """One stored file's bytes, however it was stored.

    Shared by the two signed routes — a browser's download and a kept file —
    because the only thing that differs between them is where the bytes came
    from. Two copies of this is how one of them ends up without the
    ``Content-Disposition`` and downloads as ``shot.png`` called ``name``.
    """
    return Response(
        data,
        media_type=files.content_type(name),
        headers={
            # Named for download, but shown inline when the browser can: the
            # common case is looking at a screenshot, not saving it.
            "Content-Disposition": disposition(name),
            # Safe to cache hard — the signature already bounds the lifetime,
            # and a stored file never changes under its own name.
            "Cache-Control": "private, max-age=3600",
        },
    )


def register(
    mcp,
    actions,
    token: str | None,
    console_url: str | None = None,
    sessions=None,
    flow_store=None,
) -> None:
    """Mount the admin pages, their JSON API, and the two signed file routes.

    ``flow_store`` is the *documents and kept files* store — what
    ``FLOW_DATA_DIR`` points at — and is deliberately not spelled ``store``:
    ``sessions.store`` is a different thing entirely, holding session records,
    and the two sat one scope apart with the same name until one shadowed the
    other and a listing died on ``MemoryStore.files``.
    """
    console = console_url or os.environ.get("GRID_CONSOLE_URL", DEFAULT_CONSOLE_URL)

    def counted(call, session: str) -> int:
        """How many of something a session has, or 0 if the store cannot say."""
        try:
            return len(call(session))
        except Exception:  # noqa: BLE001 - a count is not worth failing a listing
            log.info("could not count %s for %s", call.__name__, session)
            return 0

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
        sessions_store = getattr(sessions, "store", None)
        if sessions_store is None or not hasattr(sessions_store, "records"):
            # A store that cannot enumerate is not an error: sessions still
            # work, there is simply no history to show.
            return {"sessions": []}
        try:
            records = sessions_store.records()
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
            downloads = None
            if live:
                # A file count per row is worth one call each: it is the reason
                # to click into a session, so a list without it is guesswork.
                try:
                    downloads = len(actions.grid.files(sid))
                except Exception:  # noqa: BLE001 - a session can end mid-call
                    downloads = 0
            # Downloads are countable only while a browser is attached; kept
            # files and flows belong to the session and are countable always.
            # So a detached session still reports a number, which is the whole
            # reason keeping exists — a count that emptied when the Grid reaped
            # a browser would make the durable half look lost.
            session = flows.session_for(key)
            kept = counted(flow_store.files, session) if flow_store is not None else 0
            count = None
            if live or flow_store is not None:
                count = (downloads or 0) + kept
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
                    # Beside the browser because it is the same kind of fact:
                    # what this session is running, and how big.
                    "window": record.window,
                    "started": record.opened_at or None,
                    "files_count": count,
                    # Beside the file count because it is the same kind of fact:
                    # what this session has accumulated, and the other reason to
                    # click into it. None when flows are off, which is not zero.
                    "flows_count": (
                        counted(flow_store.names, session)
                        if flow_store is not None
                        else None
                    ),
                    "kept_count": kept if flow_store is not None else None,
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

    async def header(key: str, session_id: str) -> dict:
        """The session facts the detail view leads with.

        Sent alongside the files rather than fetched separately, and
        best-effort: the files are what was asked for, and losing the header is
        a worse answer than no answer only if it takes the files down with it.
        """
        detail = {
            "key": key,
            "session_id": session_id or None,
            "attached": bool(session_id),
        }
        try:
            listing = await run_in_threadpool(sessions_payload)
            return next((s for s in listing["sessions"] if s["key"] == key), detail)
        except Exception as exc:  # noqa: BLE001 - a Grid blip, not a failure
            log.info("session header for %s unavailable: %s", key, exc)
            return detail

    @mcp.custom_route(
        "/admin/sessions/{key}/files",
        methods=["GET", "DELETE"],
        name="admin_files",
    )
    async def admin_files(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = request.path_params["key"]
        session = flows.session_for(key)
        try:
            if request.method == "DELETE":
                session_id = attached_id(key)
                # Clears the DOWNLOADS, which is all the Grid offers: its store
                # has no per-file delete. Kept files are ours and elsewhere, so
                # they are untouched — which is exactly what makes this safe to
                # put behind a button (§F1.10).
                if session_id:
                    await run_in_threadpool(actions.grid.clear_files, session_id)
                return JSONResponse(
                    {"success": True, "key": key, "session_id": session_id or None}
                )
            # Whether there are downloads to list depends on whether the browser
            # is still on the Grid. The record can name one the Grid already
            # reaped — an ordinary state, not a broken one — and handing that
            # dead id to `merged` would fail the whole view in exactly the case
            # kept files exist to survive.
            #
            # `is_alive` is the right question, and the header's `live` is not:
            # that comes from a best-effort bulk listing which reports
            # `live: false` when the Grid could not be read *at all*, so an
            # outage would quietly render an empty download list instead of an
            # error. `is_alive` assumes alive when it cannot tell, so a Grid
            # that is down still surfaces from the call below.
            attached = attached_id(key)
            alive = attached and await run_in_threadpool(
                actions.grid.is_alive, attached
            )
            listing = await run_in_threadpool(
                files.merged, actions, flow_store, session, attached if alive else "",
                token,
            )
            return JSONResponse(
                {
                    "key": key,
                    "session": await header(key, attached),
                    "files": listing,
                }
            )
        except Exception as exc:  # noqa: BLE001 - usually a session that ended
            log.info("files for %s failed: %s", key, exc)
            return JSONResponse({"error": str(exc)}, status_code=502)

    @mcp.custom_route(
        "/admin/sessions/{key}/files/{name}/keep",
        methods=["POST"],
        name="admin_keep_file",
    )
    async def admin_keep_file(request: Request) -> JSONResponse:
        """Copy one download into the session's own store, so it outlives the
        browser. There is no matching unkeep: see ``files.py``."""
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            kept = await run_in_threadpool(
                files.keep_one,
                actions,
                flow_store,
                flows.session_for(key),
                attached_id(key),
                name,
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            status = errors.status_for(exc)
            log.info("keeping %s for %s refused (%s)", name, key, status)
            return JSONResponse({"error": errors.message(exc)}, status_code=status)
        return JSONResponse(kept)

    @mcp.custom_route(
        "/admin/sessions/{key}/files/{name}",
        methods=["DELETE"],
        name="admin_delete_file",
    )
    async def admin_delete_file(request: Request) -> JSONResponse:
        """Delete one KEPT file. A download cannot be deleted singly — the Grid
        offers no such operation — so this refuses rather than pretending."""
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        key = request.path_params["key"]
        name = request.path_params["name"]
        try:
            removed = await run_in_threadpool(
                files.delete_one, flow_store, flows.session_for(key), name
            )
        except Exception as exc:  # noqa: BLE001 - errors.py says what it means
            status = errors.status_for(exc)
            log.info("deleting %s for %s refused (%s)", name, key, status)
            return JSONResponse({"error": errors.message(exc)}, status_code=status)
        return JSONResponse(removed)

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
            data = await run_in_threadpool(actions.grid.read_file, session_id, name)
        except Exception as exc:  # noqa: BLE001 - gone, or never existed
            log.info("read %s/%s failed: %s", session_id, name, exc)
            return JSONResponse({"error": "not found"}, status_code=404)
        return served(name, data)

    @mcp.custom_route("/kept/{session}/{name}", methods=["GET"], name="kept_file")
    async def kept_file(request: Request) -> Response:
        """One kept file, authorised by the signature in its own URL.

        A route of its own rather than a flag on the one above, because it is
        keyed by a different thing: a download belongs to a browser id, a kept
        file to a session name that outlives it. The signature covers whichever
        path it was minted for, so a link to one is not a link to the other.
        """
        session = request.path_params["session"]
        name = request.path_params["name"]
        if token and not links.valid(
            links.kept_path(session, name),
            request.query_params.get("exp"),
            request.query_params.get("sig"),
            token,
        ):
            return JSONResponse({"error": "invalid or expired link"}, status_code=403)
        if flow_store is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            data = await run_in_threadpool(flow_store.read_file, session, name)
        except Exception as exc:  # noqa: BLE001 - gone, or never existed
            log.info("read kept %s/%s failed: %s", session, name, exc)
            return JSONResponse({"error": "not found"}, status_code=404)
        return served(name, data)
