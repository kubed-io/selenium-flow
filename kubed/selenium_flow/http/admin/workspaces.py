"""The workspace list: its payload, its live stream, and ending a browser.

``workspaces_payload`` is built once per registration (it remembers whether the
store was failing, so an outage warns once) and also serves the files tab's
header. One :class:`Broadcast` per registration computes it for every open page.
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

from ... import faults
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
from ...workspace.calls import told
from ...workspace.workspaces import Caller
from .. import answer, links

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
    """A store key and where the workspace came from: `stdio`, or named."""
    if key == STDIO_WORKSPACE:
        return {"name": key, "owner": "stdio"}
    return {"name": key, "owner": "named"}


def _recency(item) -> float:
    """Newest first, so the workspace someone just used is at the top."""
    return getattr(item[1], "opened_at", 0.0) or 0.0


def capture_facts(workspaces, key: str, record, live: bool) -> dict:
    """The row's capture fields: whether it is on, whether the connection is
    held now, what was kept (None when off), and how long it is kept."""
    capture = getattr(workspaces, "capture", None)
    on = bool((record.settings or {}).get("capture"))
    monitor = getattr(workspaces, "monitor", None)
    return {
        "capture": on,
        # Never fails the list: a monitor that raises reads as not capturing.
        "capturing": bool(
            on
            and live
            and told(monitor, "capturing", record.session_id)
        ),
        "captured": (capture.counts(key) or _NOTHING_YET) if on and capture else None,
        "capture_ttl": capture.ttl if capture is not None else None,
    }


_NOTHING_YET = {"console": 0, "network": 0, "errors": 0, "failed_requests": 0}


def _grid_facts(session: dict) -> dict:
    """The few things only the Grid knows about an attached browser."""
    if not session:
        return {"version": None, "node": None}
    return {"version": session.get("version"), "node": session.get("node")}


def named(call, workspace: str) -> list[str]:
    """What a workspace has, by name, or nothing if the store cannot say."""
    try:
        return [str(item.get("name", item)) if isinstance(item, dict) else str(item)
                for item in call(workspace)]
    except Exception:  # noqa: BLE001 - a count is not worth failing a listing
        log.info("could not list %s for %s", call.__name__, workspace)
        return []


def revision(flow_store, workspace: str) -> str:
    """A token that changes whenever this workspace's flows do, edits included.

    A store without ``revision`` gives its flow count instead.
    """
    if flow_store is None or workspace is None:
        return ""
    try:
        return str(flow_store.revision(workspace))
    except AttributeError:
        # A constant here would stop the page repainting at all.
        return str(len(named(flow_store.names, workspace)))
    except Exception:  # noqa: BLE001 - never worth failing a listing
        log.info("could not read the flow revision for %s", workspace)
        return ""


def library(key: str) -> str:
    """The workspace directory this key owns, or an ``InvalidName`` (a 400).

    No fallback to ``global``: that would put one workspace's file in the
    shared library.
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
    """The Grid id of the session a workspace holds open, or "" if none."""
    store = getattr(workspaces, "store", None)
    if store is None:
        return ""
    record = store.get(key)
    return record.session_id if record and record.attached else ""


async def header(workspaces_payload, key: str, session_id: str) -> dict:
    """The workspace facts the detail view leads with. Best effort: a failure
    gives the bare facts and never fails the files beside it."""
    detail = {
        "key": key,
        "session_id": session_id or None,
        "attached": bool(session_id),
    }
    try:
        listing = await run_in_threadpool(workspaces_payload)
        return next((s for s in listing["workspaces"] if s["key"] == key), detail)
    except Exception as exc:  # noqa: BLE001 - a Grid blip, not a failure
        # faults.message: a requests error quotes the Grid URL, userinfo and all.
        log.info(
            "workspace header for %s unavailable: %s", key, faults.message(exc)
        )
        return detail


class _Page:
    """One connected page: the newest payload it has not been sent yet.

    A slot, not a queue: a page a tick behind wants only the latest list. It is
    also the stream's shutdown event, which sse-starlette sets, and setting it
    wakes a page waiting for its next payload.
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
    """The workspace list, computed once a tick for every connected page.

    One task, running only while somebody listens: the first page starts it
    and it ends after the last one leaves. Everything but ``compute`` runs on
    the event loop, so joining, leaving and delivering never interleave;
    ``compute`` runs in a worker thread and touches none of it.
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
            # Too old to call "now": tick early. Mid-tick this wakes nothing:
            # the tick under way delivers to this page too.
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
                # Every page keeps what it has.
                log.info("event poll failed: %s", faults.message(exc))
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
    """The event stream, joined to the broadcast for as long as the response
    runs.

    Around the ASGI call, not in the generator: a client that goes away leaves
    the generator paused at its `yield` until garbage collection, and the Grid
    would be polled for nobody until then.
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
    mcp, actions, workspaces, flow_store, token, prefix, guarded, door,
    live_enabled: bool = False,
):
    """Mount the workspace list, its event stream and the end-a-session route.

    Returns the :class:`Broadcast`: the files tab reads its header from its
    ``compute``, and every route that changes the list wears its ``changes``.
    """

    # Whether the last read of the store failed, so an outage warns once rather
    # than on every two-second poll of every open page.
    store_failing = [False]

    def workspaces_payload() -> dict:
        """Every workspace, and the browser each one holds.

        Workspaces, not Grid sessions: the Grid runs browsers anything put
        there. Blocking (it asks the Grid), so run it in a worker thread.
        """
        workspaces_store = getattr(workspaces, "store", None)
        if workspaces_store is None or not hasattr(workspaces_store, "records"):
            # A store that cannot enumerate has no list to show.
            return {"workspaces": [], "live_enabled": live_enabled}
        try:
            records = workspaces_store.records()
        except Exception as exc:  # noqa: BLE001 - a Redis blip is not an outage
            # A warning, since the page shows this as an empty install; once,
            # since every open page polls every two seconds.
            report = log.debug if store_failing[0] else log.warning
            report("could not read the workspace store: %s", exc)
            store_failing[0] = True
            return {"workspaces": [], "live_enabled": live_enabled}
        if store_failing[0]:
            log.info("the workspace store is readable again")
            store_failing[0] = False

        # One Grid listing for the whole payload, not a liveness call per row.
        running: dict = {}
        grid_listed = True
        try:
            running = {s["session_id"]: s for s in actions.grid.sessions()}
        except Exception as exc:  # noqa: BLE001 - the rows are still worth showing
            grid_listed = False
            log.info("could not read the grid: %s", faults.message(exc))

        # Once per payload: the shared library's revision is the same for every row.
        shared_rev = (
            revision(flow_store, GLOBAL_WORKSPACE) if flow_store is not None else ""
        )

        rows = []
        for key, record in sorted(records.items(), key=_recency, reverse=True):
            sid = record.session_id
            live = bool(sid) and sid in running
            downloads = None
            if live:
                try:
                    downloads = [f.get("name", "") for f in actions.grid.files(sid)]
                except Exception:  # noqa: BLE001 - a session can end mid-call
                    downloads = []
            # Downloads are countable only while a browser is live; the rest
            # belong to the workspace and need a store and a name that can be a
            # directory. Without one, it must not be shown the shared library's.
            workspace = library_of(key)
            stores = flow_store is not None and workspace is not None
            # One list bound to four names: none is appended to, each is rebound.
            flow_names = shots = recs = kept = []
            if stores:
                flow_names = named(flow_store.names, workspace)
                shots = named(lambda s: flow_store.files(s, SCREENSHOTS_DIR), workspace)
                recs = named(lambda s: flow_store.files(s, RECORDINGS_DIR), workspace)
                kept = named(flow_store.files, workspace)
            counts = {
                "downloads": len(downloads) if downloads is not None else None,
                "screenshots": len(shots) if stores else None,
                "recordings": len(recs) if stores else None,
                "files": len(kept) if stores else None,
            }
            known = [v for v in counts.values() if v is not None]
            # None only when no section can be counted; an empty one is 0.
            files_count = sum(known) if known else None
            # The stamp the page watches: which name is in which folder, since
            # a keep moves a name without changing the total. JSON, since a
            # file name may hold any delimiter a join would use.
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
                    "key": key,
                    **owner_label(key),
                    "session_id": sid or None,
                    "attached": bool(sid),
                    "live": live,
                    # Without a listing `live` is a guess, so the stored
                    # `record` setting stands in for the badge.
                    "grid_listed": grid_listed,
                    "recording": bool(
                        (live or (sid and not grid_listed))
                        and (record.settings or {}).get("record")
                    ),
                    "insecure": bool((record.settings or {}).get("insecure")),
                    "headless": bool((record.settings or {}).get("headless")),
                    # Where each setting came from; admin payload only.
                    "set_by": dict(record.set_by),
                    **capture_facts(workspaces, key, record, live),
                    "url": record.url or None,
                    "browser": (record.settings or {}).get("browser")
                    or DEFAULT_BROWSER,
                    # Live, the size it has; idle, the size the next open asks
                    # for (None: the node's default).
                    "window": (record.window_seen or record.window)
                    if live
                    else record.window,
                    "grid_timeout": record.grid_timeout,
                    # The Session tab's: kept when the session ends, so the
                    # idle card shows the last browser.
                    "asked_version": (record.settings or {}).get("version"),
                    "sent": record.sent,
                    "ran": record.ran,
                    "ended": record.ended,
                    "started": record.opened_at or None,
                    "opened_by": record.opened_by,
                    "files_count": files_count,
                    "files_rev": files_rev,
                    "site_data_count": len(listed["sites"]),
                    "site_data_rev": site_data_rev,
                    "history_count": len(visited),
                    # Origins and pages in order, without times, so only a
                    # visit repaints History.
                    "history_rev": json.dumps(
                        [[v["origin"], v["url"]] for v in record.history]
                    ),
                    "counts": counts,
                    # None when flows are off, which is not zero.
                    "flows_count": (len(flow_names) if stores else None),
                    # This workspace's library and the shared one: the panel
                    # lists both.
                    "flows_rev": (
                        revision(flow_store, workspace) + "+" + shared_rev
                        if stores
                        else None
                    ),
                    **_grid_facts(running.get(sid, {})),
                }
            )
        return {"workspaces": rows, "live_enabled": live_enabled}

    broadcast = Broadcast(workspaces_payload)

    @mcp.custom_route(
        f"{prefix}/admin/workspaces", methods=["GET"], name="admin_workspaces")
    @guarded
    async def admin_workspaces(request: Request) -> JSONResponse:
        # What the open pages were just sent, when that is recent.
        payload = broadcast.fresh() or await run_in_threadpool(workspaces_payload)
        # A signed URL for the event stream, since EventSource cannot send a
        # header, minted only for a caller the admin door admitted.
        return JSONResponse(
            {
                **payload,
                # Mounted, and signed over the unprefixed path the route checks.
                "events_url": prefix
                + (links.sign(EVENTS_PATH, token) if token else EVENTS_PATH),
            }
        )

    @mcp.custom_route(
        f"{prefix}/admin/events", methods=["GET"], name="admin_events")
    async def admin_events(request: Request) -> Response:
        """The workspace list, pushed when it changes. There is nothing to
        subscribe to, so one loop polls for every connected page."""
        refusal = await answer.stream_door(request, EVENTS_PATH, token, door)
        if refusal is not None:
            return refusal

        page = _Page()

        async def stream():
            last = None
            while not page.is_set():
                payload = await page.next()
                if payload is not None and payload != last:
                    last = payload
                    yield {"data": payload}

        # sse-starlette, for shutdown: uvicorn waits for open connections
        # before the lifespan, so a stream that never ends would hold each
        # SIGTERM for FastMCP's 2s grace. This one hears uvicorn's exit and sets
        # `page`, within a shorter grace. Its heartbeat keeps proxies from
        # closing an idle stream.
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
        """End the browser session a workspace holds, keeping the workspace.

        The record stays (the browser choice, the page it was on), so the next
        ``open_session`` carries on where it left off. Nothing attached is
        success.
        """
        key = request.path_params["key"]
        session_id = attached_id(workspaces, key)
        if not session_id:
            return JSONResponse({"success": True, "key": key, "session_id": None})
        # The tool's own command. `admin=True`: a person's control never refuses
        # the End, and ending releases it.
        await run_in_threadpool(
            workspaces.end_browser, Caller(key, "admin"), admin=True
        )
        return JSONResponse({"success": True, "key": key, "session_id": session_id})

    return broadcast
