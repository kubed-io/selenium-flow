"""The workspace list: its payload, its live stream, and ending a browser.

Workspaces, not Grid sessions. ``workspaces_payload`` is built once per
registration (it remembers whether the store was failing, so an outage warns
once) and handed to whatever else needs a row — the files tab's header.

One :class:`Broadcast` per registration computes it for every open page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import wraps

import anyio
from sse_starlette import EventSourceResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...core.defaults import DEFAULT_BROWSER
from ...names import (
    GLOBAL_WORKSPACE,
    RECORDINGS_DIR,
    SCREENSHOTS_DIR,
    STDIO_WORKSPACE,
    InvalidName,
    library_of,
)
from ...site_data import snapshot as site_data
from ...workspace.workspaces import Caller
from .. import auth, links

log = logging.getLogger(__name__)

EVENTS_PATH = "/admin/events"
# Fast enough that a session appears to show up instantly, slow enough that the
# Grid is asked twice a second at worst no matter how many pages are open.
POLL_SECONDS = 2.0
# Seconds of no change before a keepalive comment goes out.
HEARTBEAT = 20.0
# How old the last broadcast may be and still be handed out as "now": to a
# page that has just connected, and to `GET /admin/workspaces`.
FRESH_SECONDS = 1.0


def owner_label(key: str) -> dict:
    """A store key turned into something worth showing next to a session.

    There is one key shape now: the name its caller chose (§F2.12). This used to
    unpick four — a name, a negotiated transport id, stdio, and a caller with
    nothing stable to key on — and report the last three as kinds because none
    of them was worth printing. The function survives the collapse because the
    admin page still wants to say where a session came from, and `stdio` is
    still a session nobody typed.
    """
    if key == STDIO_WORKSPACE:
        return {"name": key, "owner": "stdio"}
    return {"name": key, "owner": "named"}


def _recency(item) -> float:
    """Newest first, so the session someone just used is at the top."""
    return getattr(item[1], "opened_at", 0.0) or 0.0


def _grid_facts(session: dict) -> dict:
    """The few things only the Grid knows about an attached browser."""
    if not session:
        return {"version": None, "node": None}
    return {"version": session.get("version"), "node": session.get("node")}


def named(call, workspace: str) -> list[str]:
    """What a session has, by name, or nothing if the store cannot say.

    Names rather than a count, because two of these lists overlap: keeping a
    file copies it, so a kept file and its download share a name and adding
    the two lengths counted it twice.
    """
    try:
        return [str(item.get("name", item)) if isinstance(item, dict) else str(item)
                for item in call(workspace)]
    except Exception:  # noqa: BLE001 - a count is not worth failing a listing
        log.info("could not list %s for %s", call.__name__, workspace)
        return []


def revision(flow_store, workspace: str) -> str:
    """A token that changes whenever this session's flows do.

    A *count* cannot see an edit — the YAML changes and the number does not
    — and editing is what the flows panel is for, so a page watching the
    count would sit there showing a document that had already been replaced.
    The store answers this; a backend that cannot is free to return its
    count, and the panel degrades to what the file list already does.
    """
    if flow_store is None or workspace is None:
        return ""
    try:
        return str(flow_store.revision(workspace))
    except AttributeError:
        # A backend that does not implement one. Falling back to the count
        # is what makes the sentence above true: a constant here would make
        # the page's stamp constant too, and it would never repaint again —
        # which is the very bug this helper exists to fix, reintroduced
        # silently for anyone whose store is not the local one.
        return str(len(named(flow_store.names, workspace)))
    except Exception:  # noqa: BLE001 - never worth failing a listing
        log.info("could not read the flow revision for %s", workspace)
        return ""


def library(key: str) -> str:
    """The workspace directory this key owns, or refuse.

    The write paths cannot fall back to ``global`` the way a browser lookup
    does: that would put one workspace's file in the shared library. An
    ``InvalidName`` is a ValueError, so ``errors.py`` already answers 400.
    """
    workspace = library_of(key)
    if workspace is None:
        raise InvalidName(
            f"workspace {key!r} cannot keep files: its name is not one it may "
            "own a library under — either not a usable directory name, or "
            "reserved. Use letters, digits, dots, dashes and underscores, "
            "starting with a letter or digit."
        )
    return workspace


def attached_id(workspaces, key: str) -> str:
    """The browser a flow session currently holds, or "" if none."""
    store = getattr(workspaces, "store", None)
    if store is None:
        return ""
    record = store.get(key)
    return record.session_id if record and record.attached else ""


async def header(workspaces_payload, key: str, session_id: str) -> dict:
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
        listing = await run_in_threadpool(workspaces_payload)
        return next((s for s in listing["workspaces"] if s["key"] == key), detail)
    except Exception as exc:  # noqa: BLE001 - a Grid blip, not a failure
        log.info("session header for %s unavailable: %s", key, exc)
        return detail


class _Page:
    """One connected page: the newest payload it has not been sent yet.

    A slot rather than a queue: the payload is the whole list, so a page that
    falls a tick behind wants the latest one, not every one in between. It is
    also the stream's shutdown event — sse-starlette only ever sets that, and
    setting it has to wake a page waiting for its next payload.
    """

    def __init__(self):
        self.pending: str | None = None
        self.stopping = False
        self._wake = anyio.Event()

    def deliver(self, payload: str) -> None:
        self.pending = payload
        self._wake.set()

    def set(self) -> None:
        self.stopping = True
        self._wake.set()

    def is_set(self) -> bool:
        return self.stopping

    async def next(self) -> str | None:
        """The next payload, or None once the stream is stopping."""
        await self._wake.wait()
        self._wake = anyio.Event()
        payload, self.pending = self.pending, None
        return None if self.stopping else payload


class Broadcast:
    """The session list, computed once a tick for every connected page.

    Each page used to run its own loop, so N open tabs asked the Grid and the
    store N times a tick for the same answer. One task does it now, and only
    while somebody is listening: the first page starts it and it ends after the
    last one leaves (AGENTS.md, "Refresh, not cleanup").

    Everything here but ``compute`` runs on the event loop, so joining, leaving
    and handing a payload out never interleave with each other; ``compute``
    runs in a worker thread and touches none of it.
    """

    def __init__(self, compute):
        # `workspaces_payload`, for a caller that wants the list computed now.
        self.compute = compute
        self._pages: set[_Page] = set()
        self._task: asyncio.Task | None = None
        self._nudge = anyio.Event()
        # (monotonic time the computation started, payload, its JSON).
        self._latest: tuple[float, dict, str] | None = None
        # Nothing computed before this may become `_latest`: see `invalidate`.
        self._floor = 0.0

    def _recent(self) -> tuple[float, dict, str] | None:
        latest = self._latest
        if latest and time.monotonic() - latest[0] < FRESH_SECONDS:
            return latest
        return None

    def fresh(self) -> dict | None:
        """The last broadcast, if it is recent enough to stand for now."""
        recent = self._recent()
        return recent[1] if recent else None

    def invalidate(self) -> None:
        """Forget the last broadcast, and any already under way: an admin has
        just changed what it would say."""
        self._latest = None
        self._floor = time.monotonic()

    def poke(self) -> None:
        """Something an open page should see changed (a recording was filed):
        forget the last broadcast and tick now. Called on the event loop."""
        self.invalidate()
        self._nudge.set()

    def changes(self, handler):
        """For a route that changes what the list shows — End, Forget, Clear,
        keep, delete: once it has run, however it ended, the listing after it
        is computed rather than a tick old. A GET through it changes nothing."""

        @wraps(handler)
        async def wrapper(request: Request):
            try:
                return await handler(request)
            finally:
                if request.method != "GET":
                    self.invalidate()

        return wrapper

    def join(self, page: _Page) -> None:
        self._pages.add(page)
        recent = self._recent()
        if recent:
            page.deliver(recent[2])
        else:
            # Too old to call "now": tick early. Mid-tick this wakes nothing —
            # the tick under way delivers to this page too, and the nudge it
            # set is replaced before the loop next waits.
            self._nudge.set()
        loop = asyncio.get_running_loop()
        task = self._task
        # A task from another event loop is one a test left behind.
        if task is None or task.done() or task.get_loop() is not loop:
            self._task = loop.create_task(self._run())

    def leave(self, page: _Page) -> None:
        self._pages.discard(page)
        if not self._pages:
            self._nudge.set()

    async def _run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                payload = await run_in_threadpool(self.compute)
                text = json.dumps(payload, sort_keys=True)
            except Exception as exc:  # noqa: BLE001 - the Grid can blip
                # Every page keeps what it has, exactly as a failed poll of its
                # own used to leave it.
                log.info("event poll failed: %s", exc)
            else:
                if started >= self._floor:
                    self._latest = (started, payload, text)
                for page in self._pages:
                    page.deliver(text)
            if not self._pages:
                break
            self._nudge = anyio.Event()
            with anyio.move_on_after(POLL_SECONDS):
                await self._nudge.wait()
            if not self._pages:
                break
        if self._task is asyncio.current_task():
            self._task = None


class _Stream(EventSourceResponse):
    """The event stream, joined to the broadcast for exactly as long as the
    response runs.

    Around the ASGI call rather than inside the generator: a client that goes
    away mid-send cancels the send and leaves the generator paused at its
    `yield`, which nothing closes until the garbage collector gets to it — and
    until then the page stayed joined and the Grid was polled for nobody. The
    call returns however the response ends, so leaving here is immediate.
    """

    def __init__(self, broadcast: Broadcast, page: _Page, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._broadcast = broadcast
        self._page = page

    async def __call__(self, scope, receive, send) -> None:
        self._broadcast.join(self._page)
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._broadcast.leave(self._page)


def mount(
    mcp, actions, workspaces, flow_store, token, prefix, guarded
):
    """Mount the session list, its event stream and the end-a-browser route.

    Returns the :class:`Broadcast`: the files tab reads its header from its
    ``compute``, and every route that changes the list wears its ``changes``.
    """

    # Whether the last read of the store failed, so an outage warns once rather
    # than on every two-second poll of every open page.
    store_failing = [False]

    def workspaces_payload() -> dict:
        """Every workspace, and the session (browser) each one currently holds.

        Workspaces, not Grid sessions. The Grid is the superset — it runs
        browsers put there by anything at all — and listing those would be
        showing somebody else's work as though it were ours. What matters here
        is the workspace: who holds it, what it was doing, and whether it still
        has a browser attached.

        Blocking — it talks to the Grid for liveness and file counts — so
        callers on the event loop must run it in a worker thread.
        """
        workspaces_store = getattr(workspaces, "store", None)
        if workspaces_store is None or not hasattr(workspaces_store, "records"):
            # A store that cannot enumerate is not an error: sessions still
            # work, there is simply no history to show.
            return {"workspaces": []}
        try:
            records = workspaces_store.records()
        except Exception as exc:  # noqa: BLE001 - a Redis blip is not an outage
            # A warning, because the page renders this as "no sessions" and that
            # looks exactly like an empty install - at info it hid a store that
            # failed on every poll for a day. Once, because every open page
            # polls every two seconds (Copilot, #33).
            report = log.debug if store_failing[0] else log.warning
            report("could not read the session store: %s", exc)
            store_failing[0] = True
            return {"workspaces": []}
        if store_failing[0]:
            log.info("the session store is readable again")
            store_failing[0] = False

        # One Grid listing for the whole payload rather than a liveness call per
        # row: the answer for every session is in it, and it is one round trip.
        running: dict = {}
        try:
            running = {s["session_id"]: s for s in actions.grid.sessions()}
        except Exception as exc:  # noqa: BLE001 - the rows are still worth showing
            log.info("could not read the grid: %s", exc)

        # Once per payload, not once per row: it is the same answer for every
        # session, and inside the loop it made each heartbeat walk and stat the
        # whole shared library once per session — O(sessions x shared flows) on
        # a two-second poll.
        shared_rev = (
            revision(flow_store, GLOBAL_WORKSPACE) if flow_store is not None else ""
        )

        rows = []
        for key, record in sorted(records.items(), key=_recency, reverse=True):
            sid = record.session_id
            live = bool(sid) and sid in running
            downloads = None
            if live:
                # A file count per row is worth one call each: it is the reason
                # to click into a session, so a list without it is guesswork.
                try:
                    downloads = [f.get("name", "") for f in actions.grid.files(sid)]
                except Exception:  # noqa: BLE001 - a session can end mid-call
                    downloads = []
            # Downloads are countable only while a browser is attached; kept
            # files and flows belong to the session and are countable always.
            # So a detached session still reports a number, which is the whole
            # reason keeping exists — a count that emptied when the Grid reaped
            # a browser would make the durable half look lost.
            # None when the session named itself something no directory can be
            # called. Such a session keeps nothing, and must not be shown the
            # shared library's counts as though they were its own.
            workspace = library_of(key)
            stores = flow_store is not None and workspace is not None
            flow_names = named(flow_store.names, workspace) if stores else []
            # Screenshots and Files are the session's own — countable whenever
            # there is a store and a usable name, independent of whether a
            # browser is attached. Downloads are the Grid's and countable only
            # while one is live, which is why that count alone can be None for
            # a reason `stores` never causes.
            shots = (
                named(lambda s: flow_store.files(s, SCREENSHOTS_DIR), workspace)
                if stores
                else []
            )
            recs = (
                named(lambda s: flow_store.files(s, RECORDINGS_DIR), workspace)
                if stores
                else []
            )
            kept = named(flow_store.files, workspace) if stores else []
            counts = {
                "downloads": len(downloads) if downloads is not None else None,
                "screenshots": len(shots) if stores else None,
                "recordings": len(recs) if stores else None,
                "files": len(kept) if stores else None,
            }
            known = [v for v in counts.values() if v is not None]
            # The sum of what is known, not "unknown treated as zero": a live,
            # empty browser and an empty store must still show 0 rather than the
            # None that would claim nothing here can be counted. Only every
            # section being unknown earns the None.
            files_count = sum(known) if known else None
            # The stamp the page actually watches. A count cannot tell two
            # states apart: a name moving between folders — kept out of
            # Screenshots and into Files — leaves the total the same while the
            # tile for that name switches folder, mark and URL. So which name is
            # in which folder goes in, not just how many there are (§F1.39).
            #
            # JSON rather than joining on a delimiter, because a FILE name is
            # not ours to choose — `valid_file_name` permits `:` and `;`
            # deliberately, since the site's Content-Disposition picked it. A
            # file called `a:d;b` in one folder and the pair (`a`, `b`) split
            # across two would both flatten to the same joined string, so two
            # different sessions shared one token and the later one would not
            # repaint. Flow names cannot do this — `NAME` has no punctuation to
            # collide with — which is why `revision` may join and this may not.
            files_rev = json.dumps(
                sorted(
                    [folder_name, name]
                    for folder_name, names in (
                        ("downloads", downloads or []),
                        ("screenshots", shots),
                        ("recordings", recs),
                        ("files", kept),
                    )
                    for name in names
                )
            )
            listed = site_data.view(record.site_data, record.history)
            # A save moves the time; a Forget or a Clear moves the hosts.
            site_data_rev = json.dumps(
                [record.site_data.get("saved_at"), [s["site"] for s in listed["sites"]]]
            )
            visited = site_data.history_hosts(record.history)
            rows.append(
                {
                    # The store key addresses the session on this API. It is not
                    # the browser id, which comes and goes underneath it.
                    "key": key,
                    **owner_label(key),
                    "session_id": sid or None,
                    "attached": bool(sid),
                    "live": live,
                    "recording": bool(live and (record.settings or {}).get("record")),
                    "url": record.url or None,
                    "browser": (record.settings or {}).get("browser")
                    or DEFAULT_BROWSER,
                    # Beside the browser because it is the same kind of fact:
                    # what this session is running, and how big.
                    "window": record.window,
                    "started": record.opened_at or None,
                    "files_count": files_count,
                    "files_rev": files_rev,
                    "site_data_count": len(listed["sites"]),
                    "site_data_rev": site_data_rev,
                    "history_count": len(visited),
                    # Every row's origin and page, in order: a new site, a
                    # return to an older one, or a new page on any row
                    # repaints History. Only the clock moving does not, so the
                    # times are left out (Copilot, #51).
                    "history_rev": json.dumps(
                        [[v["origin"], v["url"]] for v in record.history]
                    ),
                    # Named per section, so a row can say "one screenshot" and
                    # "no browser to have downloads at all" instead of one
                    # number that means both.
                    "counts": counts,
                    # Beside the file count because it is the same kind of fact:
                    # what this session has accumulated, and the other reason to
                    # click into it. None when flows are off, which is not zero.
                    "flows_count": (len(flow_names) if stores else None),
                    # What the flows panel watches. A count cannot see an edit —
                    # the YAML changes and the number does not — and editing is
                    # the thing the panel is for, so a page keyed off the count
                    # would sit there showing the old document. The revision
                    # covers this session's library and the shared one, because
                    # the panel lists both.
                    "flows_rev": (
                        revision(flow_store, workspace) + "+" + shared_rev
                        if stores
                        else None
                    ),
                    **_grid_facts(running.get(sid, {})),
                }
            )
        return {"workspaces": rows}

    broadcast = Broadcast(workspaces_payload)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces", methods=["GET"], name="admin_workspaces")
    @guarded
    async def admin_workspaces(request: Request) -> JSONResponse:
        # What the open pages were just sent, when that is recent: a page loads
        # this and opens the stream together, and both are the same answer.
        payload = broadcast.fresh() or await run_in_threadpool(workspaces_payload)
        # A signed URL for the event stream, because EventSource cannot send an
        # Authorization header — the same reason the file route is signed. It is
        # minted here so it is only ever handed to a caller that had the token.
        return JSONResponse(
            {
                **payload,
                # Mounted, so a caller other than the page can follow it; signed
                # over the unprefixed path the route checks (Copilot, #35).
                "events_url": prefix
                + (links.sign(EVENTS_PATH, token) if token else EVENTS_PATH),
            }
        )

    @mcp.custom_route(
        f"{prefix}/admin/events", methods=["GET"], name="admin_events")
    async def admin_events(request: Request) -> Response:
        """The session list, pushed when it changes.

        There is nothing to subscribe to — a browser appears on the Grid, put
        there by anything at all — so the change has to be discovered by asking.
        Asking once here, for every connected page, is the point: the polling
        that would otherwise happen in each open tab collapses into one loop.
        """
        if not (
            auth.authorized(request, token)
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

        page = _Page()

        async def stream():
            last = None
            while not page.is_set():
                payload = await page.next()
                if payload is not None and payload != last:
                    last = payload
                    yield {"data": payload}

        # sse-starlette rather than a StreamingResponse, for shutdown: uvicorn
        # waits for open connections before it runs the lifespan, so a stream
        # that never ends held every SIGTERM for FastMCP's whole 2s grace, was
        # cancelled, and left a traceback per open admin page in the log. This
        # one hears uvicorn's exit, sets `page`, and the loop above returns,
        # so the response finishes the way any other does (§F4.19). The grace
        # is under FastMCP's 2s. It also sends the heartbeat comment, which
        # keeps proxies from closing an idle stream, and `X-Accel-Buffering: no`.
        return _Stream(
            broadcast,
            page,
            stream(),
            ping=HEARTBEAT,
            headers={"Cache-Control": "no-cache, no-transform"},
            shutdown_event=page,
            shutdown_grace_period=1.0,
        )

    @mcp.custom_route(
        f"{prefix}/admin/workspaces/{{key}}/session",
        methods=["DELETE"],
        name="admin_end_session",
    )
    @guarded
    @broadcast.changes
    async def admin_end_session(request: Request) -> JSONResponse:
        """End the browser session a workspace holds, keeping the workspace itself.

        **This does not delete the workspace.** It detaches the browser and leaves
        the record — its browser choice and the page it was on — so the caller's
        next ``open_session`` carries on where it left off rather than starting
        from the server's defaults. A flow session is only ever removed by
        expiring, which is what makes all of this safe to click.

        The Grid reaps an idle browser on its own timeout, so this is not the
        only way one ends. It is the way that does not cost minutes of a scarce
        Grid slot while somebody waits.
        """
        key = request.path_params["key"]
        session_id = attached_id(workspaces, key)
        if not session_id:
            # Nothing attached is success, not a failure: the button's whole
            # job is "make sure this session is not holding a browser".
            return JSONResponse({"success": True, "key": key, "session_id": None})
        # Through the same command the tool uses, so the button and the tool
        # cannot mean different things: the admin names the session by its
        # key. Blocking HTTP to the Grid, so off the event loop — a slow Grid
        # would stall every connected dashboard.
        await run_in_threadpool(workspaces.end_browser, Caller(key, "admin"))
        return JSONResponse({"success": True, "key": key, "session_id": session_id})

    return broadcast
