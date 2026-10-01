"""Which browser a caller means, and the name that says so.

**Every session is named, and the caller supplies the name.** There is one
contract, on both surfaces: say who you are, and the server hands you the
browser that belongs to that name. Nothing here invents a name, and the Grid's
own session id is never part of the contract — it is an implementation detail
of how a browser is reached, held in the record beside the name.

A name arrives one of two ways, and they are the two things any client can set:

1. **``X-Session-Key``**, a header. What an admin pins inside a credential when
   one credential should mean one session.
2. **``?session=<name>``**, on the URL. The ergonomic path: one shared bearer
   credential, each caller naming itself in its own URL.

**Both at once is refused**, rather than one quietly winning. A request
carrying two names has two ideas about who is calling, and picking one hides
that from whoever wired it up. Neither is refused too, with a message saying
how to name yourself.

On **stdio** there is no request to read, and one process serves exactly one
client, so the name is the transport's own: :data:`STDIO_NAME`. That is not a
generated name — it is a constant, decided by how the server was started.

What is deliberately *not* used is ``Context.session_id``. When it cannot find a
real session it falls back to ``str(uuid4())``, so it never fails — it just
returns a brand new key on every call, which silently leaks one browser per tool
call. Reading the header ourselves means a missing name is visible as one.

**The name is validated where it arrives**, by the same rule that validates a
flow library's directory, because a session name *is* that directory. So
everything downstream — the browser record, the flow library, the kept files —
can take the name as given, and there is no third answer for a name that is
usable as one and not the other (§F2.12).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .. import errors
from ..core import site_data as site_data_module
from ..core.actions import Actions
from ..core.browser import DEFAULT_BROWSER
from ..mcp import guidance
from .store import MemoryStore, SessionRecord, SessionStore

log = logging.getLogger(__name__)

REPLACED_BEFORE_SAVE = (
    "another browser took this session before the save landed: nothing was saved"
)

# A client names its session with either of these. The query parameter is the
# one most clients can set, since an MCP server is usually configured by URL.
NAME_PARAM = "session"
NAME_HEADER = "x-session-key"

# The name a stdio caller has. One process, one client, so a constant is
# exactly right and there is nothing to tell apart.
STDIO_NAME = "stdio"

UNNAMED = (
    "name your session: add ?session=<name> to the URL, or send an "
    "X-Session-Key header. Every session here is named by whoever calls, and "
    "the server does not invent one. The name is yours to choose and to reuse "
    "— calling again with the same name is how you get the same browser back."
)

BOTH = (
    "name your session once: this request carries both an X-Session-Key header "
    "and ?session=, and they are two answers to one question. Send whichever "
    "one you control and drop the other."
)


@dataclass(frozen=True)
class Caller:
    """Who is calling, and how they said so.

    ``source`` exists for the log line: when sessions misbehave, the first
    question is always which mechanism actually supplied the name.
    """

    name: str
    source: str


def http_request() -> tuple[dict, dict] | None:
    """(query params, headers) for the current request, or None off HTTP.

    Both are looked up through FastMCP's dependency helpers, which read the
    request from a context variable set by the ASGI stack. That is a different
    path from the one ``Context.session_id`` uses, and it keeps working where
    that one gives up.
    """
    try:
        from fastmcp.server.dependencies import get_http_request
    except ImportError:  # pragma: no cover - fastmcp is a hard dependency
        return None
    try:
        request = get_http_request()
    except Exception:  # noqa: BLE001 - outside an HTTP request this raises
        request = None
    if request is None:
        # Headers may still be reachable even when the request object is not.
        try:
            from fastmcp.server.dependencies import get_http_headers

            headers = get_http_headers()
        except Exception:  # noqa: BLE001
            return None
        if not headers:
            return None
        return {}, {k.lower(): v for k, v in headers.items()}
    return (
        dict(request.query_params),
        {k.lower(): v for k, v in request.headers.items()},
    )


def name_in(params: dict, headers: dict) -> Caller | None:
    """The name a request carries, or None if it carries none.

    Takes the two dictionaries rather than reading the request itself, so the
    HTTP routes — which have a Starlette request in hand — resolve a session
    through exactly this function rather than a second copy of the rule.
    """
    from ..flows.library import valid_session_name

    header = str(headers.get(NAME_HEADER) or "").strip()
    param = str(params.get(NAME_PARAM) or "").strip()
    if header and param:
        raise ValueError(BOTH)
    chosen = header or param
    if not chosen:
        return None
    return Caller(valid_session_name(chosen), "header" if header else "query")


def requested() -> Caller | None:
    """The caller of the current MCP request, or None if it named no session."""
    http = http_request()
    if http is None:
        return Caller(STDIO_NAME, "stdio")
    params, headers = http
    return name_in(params, headers)


def name_from(request) -> str:
    """The session a Starlette request names. Raises when it names none.

    The HTTP routes hold a request object, so they read it directly rather than
    through the ambient lookup :func:`requested` uses. Same rule, one
    implementation: :func:`name_in` is where it lives.
    """
    caller = name_in(
        dict(request.query_params),
        {k.lower(): v for k, v in request.headers.items()},
    )
    if caller is None:
        raise ValueError(UNNAMED)
    return caller.name


def library_from(request) -> str:
    """The flow library a Starlette request is about.

    See :meth:`SessionManager.library` for why a missing name is not an error
    here.
    """
    from ..flows.library import GLOBAL_SESSION

    caller = name_in(
        dict(request.query_params),
        {k.lower(): v for k, v in request.headers.items()},
    )
    return caller.name if caller else GLOBAL_SESSION


class SessionManager:
    """Resolves a named session to the browser it holds, through a ``SessionStore``.

    The store maps **name -> record**, and the record carries the Grid session
    id, the page, and the settings a reopen has to replay. The name is the key
    in every sense: it is the store key, the flow library's directory, and the
    only identifier a caller ever sees.
    """

    def __init__(
        self,
        actions: Actions,
        store: SessionStore | None = None,
        skill_available: bool = True,
        defaults: dict | None = None,
    ):
        self.actions = actions
        self.store = store if store is not None else MemoryStore()
        # Whether this server serves the skill. The status points at a reference
        # for the caller to read, and a skill:// URI nobody can read teaches an
        # agent the manual is broken (Copilot, #36).
        self.skill_available = skill_available
        # The operator's floor for a new session, from the config's session
        # section. See `session/settings.py` for where this sits in the cascade.
        self.defaults = dict(defaults or {})

    @property
    def kind(self) -> str:
        """Which backend is in play, for /ready, /info and the config log line."""
        return getattr(self.store, "kind", "memory")

    def caller(self) -> Caller:
        """Who is calling. Raises when the request named no session."""
        caller = requested()
        if caller is None:
            raise ValueError(UNNAMED)
        return caller

    def name(self) -> str:
        """The session this request is about. Raises when it named none."""
        return self.caller().name

    def library(self) -> str:
        """The flow library this request is about.

        The caller's own, or the **shared** one when the request named no
        session. That is the one place a missing name is not an error, and it
        is not an exception to E18 so much as the other half of it: `global` is
        read-only to everyone (§F1.2), so an unnamed caller can list and read
        the shared flows and can write nowhere at all. Anything that touches a
        browser still has to say who it is (§F2.13).
        """
        from ..flows.library import GLOBAL_SESSION

        caller = requested()
        return caller.name if caller else GLOBAL_SESSION

    def describe(self, name: str | None = None) -> dict:
        """What this session is, and whether it currently holds a browser.

        Deliberately side-effect free: reading a status resource must never open
        a browser, so this peeks at the store rather than going through
        ``resolve``. The Grid's session id is not in it — that is the whole
        point of E18 — so what a caller reads back is what it may say.
        """
        # A name is passed in by the HTTP routes, which read it off a request
        # they are holding; the MCP path asks the ambient request instead. Same
        # rule either way — `name_in` is where it lives.
        caller = Caller(name, "request") if name else self.caller()
        status = {
            "session": caller.name,
            "named_by": caller.source,
            "browser": None,
            "url": None,
            "live": False,
            "in_frame": None,
            "window": None,
            "store": self.kind,
            "settings": {},
        }
        if self.skill_available:
            status["guidance"] = guidance.pointer("SESSIONS.md")
        record = self.store.get(caller.name)
        if record is None:
            return status

        status["url"] = record.url or None
        status["settings"] = dict(record.settings or {})
        # Reported at the top level as well as inside settings, because "which
        # browser am I driving" is the question this resource exists to answer
        # and a caller should not have to know it is stored as a setting. A
        # record written before browsers were selectable has none, and that
        # session really is the default one.
        status["browser"] = status["settings"].get("browser") or DEFAULT_BROWSER
        # Same reasoning for the window: "how big is the page I am looking at"
        # is a question an agent has to answer before it can judge whether
        # something is off-screen or a layout has collapsed, and it should not
        # have to dig it out of settings or take a screenshot to find out.
        status["window"] = record.window
        saved = site_data_module.summary(record.site_data)
        if saved is not None:
            status["site_data"] = saved
        # A record with no browser is an ordinary state, not a broken one: the
        # Grid reaped it or an admin ended it, and the context it left behind is
        # what the next open_session inherits.
        status["live"] = (
            self.actions.grid.is_alive(record.session_id) if record.attached else False
        )
        if status["live"]:
            # Only worth a round trip when there is a live browser to ask, and
            # one reconnect answers both questions.
            try:
                from ..core import browser as browser_module

                driver = self.actions.grid.reconnect(record.session_id)
                status["in_frame"] = browser_module.in_frame(driver)
                size = driver.get_window_size()
                status["window"] = f"{size['width']}x{size['height']}"
            except Exception:  # noqa: BLE001 - status must never fail
                pass
        return status

    def browser(self, name: str) -> str:
        """The Grid id this session is driving, or "" if it holds no live one.

        For the callers that need the browser but must never *open* one — the
        file listing, which exists to keep answering after a browser is gone.
        A reaped browser keeps its id in the record until something refreshes
        it, so liveness is asked rather than assumed: trusting the id would dial
        the Grid for a browser that is gone and fail the listing in precisely
        the state kept files exist to survive.
        """
        record = self.store.get(name)
        if record is None or not record.attached:
            return ""
        live = self.actions.grid.is_alive(record.session_id)
        return record.session_id if live else ""

    def resolve(self, name: str) -> str:
        """The Grid session id ``name`` is driving, reopening a reaped browser.

        Nothing here opens a *first* browser. ``open_session`` is the one place
        that happens, and hiding it behind a first use hid the only place a
        session's settings could be chosen.
        """
        record = self.store.get(name)
        if record is None or not record.attached:
            # Detached reads the same as absent on purpose. A caller is told it
            # has no browser and calls open_session, which is the one path that
            # opens one — and which now inherits this record's browser and page.
            raise ValueError(
                f"no browser is open for session {name!r} yet: call open_session "
                "first. It takes the window size and timeouts this session "
                "should use, which is why it is not done implicitly."
            )

        if self.actions.grid.is_alive(record.session_id):
            log.debug("session %s recalled for %s", record.session_id, name)
            return record.session_id

        # The Grid reaped it. Reopen where the caller left off, with the same
        # settings, so a refresh does not silently change the browser's shape.
        log.info(
            "browser %s is gone from the grid, reopening for session %s",
            record.session_id,
            name,
        )
        saved = self._restorable(record)
        opened = self.actions.open_session(
            url=record.url or None,
            **({"site_data": saved} if saved else {}),
            **(record.settings or {}),
        )
        self.remember(
            name,
            opened["session_id"],
            opened.get("url", record.url or ""),
            record.settings,
            replacing=record.session_id,
        )
        # Nobody asked for this reopen, so the next result says it happened.
        self._hold_pending(
            name, opened, {"announce": True, "report": opened.get("site_data")}
        )
        return opened["session_id"]

    def act(self, name: str, call, *, reshapes: bool = False) -> dict:
        """Resolve this session's browser, act on it, remember where it ended up.

        The three steps every action shares, on **both** surfaces. It lives here
        rather than in each of them because the two used to carry their own copy
        of it and could therefore disagree: the HTTP surface never touched a
        record, so it never slid a TTL and never reopened a reaped browser, and
        that difference was invisible until a session expired mid-workflow.

        ``reshapes`` is for the one action that changes a setting the record
        *stores* rather than just the page it is on. See :meth:`reshape`.
        """
        resolved = self.resolve(name)
        result = call(resolved)
        self.settle(name, result, browser=resolved, reshapes=reshapes)
        return result

    def settle(
        self,
        name: str,
        result,
        *,
        browser: str | None = None,
        reshapes: bool = False,
        touch: bool = True,
    ) -> None:
        """What a finished action means for the record, in place on ``result``.

        The one place a result is merged into site data and stripped of its
        private capture, so a flow step reaches it the same way a single call
        does. ``touch=False`` is for the flow runner, which touches once per
        run rather than once per step.

        ``browser`` is the one that produced ``result``. When the record names
        another by now, that browser's pending note is not this result's to
        settle.
        """
        if not isinstance(result, dict):
            return
        if touch:
            self.touch(name, result.get("url"), browser=browser)
        if reshapes:
            self.reshape(name, result, browser=browser)
        self._site_data_after(name, result, browser)

    def _site_data_after(
        self, name: str, result: dict, producer: str | None = None
    ) -> None:
        """What a finished call means for this session's site data.

        Three things, in order: a captured save is merged into the record and
        replaced in the result by a short receipt, and any origin it evicted
        stops waiting in the live browser; a page arriving on an origin
        still waiting to be restored says so once, and the last arrival retires
        the preload script; a silent reopen is announced on its first result.

        Every write goes through ``store.update``, applied to the record as it
        is then: retiring a script is a BiDi round trip, and a whole record
        read before it and set after reverted whatever opened or saved meanwhile.
        """
        # Popped before the store is asked anything, so a store that fails
        # cannot leave the capture — every value — in what the caller gets.
        captured = result.pop(site_data_module.CAPTURED, None)
        evicted: set[str] = set()
        if captured:
            receipt = {}

            def save(r: SessionRecord) -> SessionRecord | None:
                if producer is not None and r.session_id != producer:
                    # Another browser holds the session now — perhaps one
                    # opened with restore_site_data=false. What this one
                    # captured is not its to keep (Copilot, #50).
                    receipt["replaced"] = True
                    return None
                data, receipt["saved"] = site_data_module.merge(
                    r.site_data, captured, time.time()
                )
                gone = set(r.site_data.get("origins") or {}) - set(data["origins"])
                # An evicted origin has nothing left to restore: the browser
                # waiting on it stops, or a later visit is announced restored.
                data = self._without(data, gone)
                receipt["evicted"] = gone & set(
                    (r.site_data.get("pending") or {}).get("origins") or []
                )
                pending = data.get("pending")
                if receipt["evicted"] and pending and pending.get("script"):
                    # Marked in the same write: until the swap below lands,
                    # the live script still carries what was evicted, and a
                    # crash in between must leave that visible to retry.
                    data = {**data, "pending": {**pending, "stale": True}}
                return r.with_site_data(data)

            if self.store.update(name, save) is None and not receipt.get("replaced"):
                return
            result["uri"] = site_data_module.LIST_URI
            if receipt.get("replaced"):
                result["saved"] = {
                    "cookies": 0, "sites": [],
                    "skipped": [{"reason": REPLACED_BEFORE_SAVE}],
                }
                return
            evicted = receipt.pop("evicted", set())
            result["saved"] = receipt["saved"]
        record = self.store.get(name)
        if record is None:
            return
        pending = record.site_data.get("pending") or {}
        browser = record.session_id
        if not pending or pending.get("browser") != browser:
            return
        # A result from a browser the record no longer names says nothing
        # about where this one is; only an eviction still concerns its note.
        ours = producer is None or producer == browser
        if not ours and not evicted:
            return
        hint = (
            dict(pending.get("report") or {})
            if ours and pending.get("announce") else {}
        )
        origin = site_data_module.origin_of(result.get("url") or "") if ours else ""
        arrived = origin if origin in (pending.get("origins") or []) else None
        swapped = None
        if arrived:
            hint["restored"] = [*hint.get("restored", []), arrived]
            if "waiting" in hint:
                # The reopen's own report listed it as waiting; one answer
                # must not call the same origin both.
                hint["waiting"] = [o for o in hint["waiting"] if o != arrived]
            hint["uri"] = site_data_module.site_uri(site_data_module.host_of(arrived))
        if (arrived or evicted or pending.get("stale")) and pending.get("script"):
            # Still carrying an origin that arrived, the script would refill
            # it in every new tab; carrying an evicted one, it would restore
            # what the save just dropped. The replacement carries only what
            # still waits. A swap that could not remove the old script left it
            # marked stale, and every later call retries it (Copilot, #50).
            still = [o for o in pending.get("origins") or [] if o != arrived]
            keep = self._waiting(record.site_data, still)
            swapped = (
                pending["script"],
                self.actions.retire_site_data(browser, pending["script"], keep),
            )
        if hint:
            result["site_data"] = hint

        def settle(r: SessionRecord) -> SessionRecord | None:
            now = dict(r.site_data.get("pending") or {})
            if r.session_id != browser or now.get("browser") != browser:
                # Another browser opened meanwhile, with a note of its own.
                return None
            if ours:
                now["announce"] = False
                now.pop("report", None)
            if arrived:
                now["origins"] = [o for o in now.get("origins") or [] if o != arrived]
            if swapped and now.get("script") == swapped[0]:
                now["script"] = swapped[1]
                if swapped[1] == swapped[0]:
                    # The old script could not be removed and still carries
                    # what it should not: keep retrying until it goes.
                    now["stale"] = True
                else:
                    now.pop("stale", None)
                if now["origins"] and not now["script"]:
                    # No script is left to fill them: announcing them later
                    # would promise a restore that cannot happen.
                    now["origins"] = []
            data = {k: v for k, v in r.site_data.items() if k != "pending"}
            if (
                now.get("origins") or now.get("script") or now.get("announce")
                or now.get("stale")
            ):
                data["pending"] = now
            return None if data == r.site_data else r.with_site_data(data)

        self.store.update(name, settle)

    @staticmethod
    def _without(data: dict, origins: set[str]) -> dict:
        """``data`` with ``origins`` gone from the pending note and its report."""
        pending = data.get("pending")
        if not origins or not pending:
            return data
        pending = {
            **pending,
            "origins": [o for o in pending.get("origins") or [] if o not in origins],
        }
        report = pending.get("report")
        if report and report.get("waiting"):
            pending["report"] = {
                **report,
                "waiting": [o for o in report["waiting"] if o not in origins],
            }
        return {**data, "pending": pending}

    @staticmethod
    def _waiting(data: dict, origins: list[str]) -> dict:
        """The saved storage of the origins still waiting to be restored."""
        saved = data.get("origins") or {}
        return {o: saved[o] for o in origins if o in saved}

    @staticmethod
    def _restorable(record: SessionRecord) -> dict:
        """The saved data a browser can be given: the pending note is ours."""
        if site_data_module.summary(record.site_data) is None:
            return {}
        return {k: v for k, v in record.site_data.items() if k != "pending"}

    def _hold_pending(self, name: str, opened: dict, extra: dict) -> None:
        """Note in the record what a fresh browser is still waiting to restore.

        Consumes ``_site_data_pending`` from ``opened``, so it never reaches a
        caller. Whatever an earlier browser was waiting on is dropped.
        """
        waiting = opened.pop("_site_data_pending", None)
        record = self.store.get(name)
        browser = opened["session_id"]
        if record is None or record.session_id != browser:
            # Another open replaced this browser already; its note is its own.
            return
        arrived = (waiting or {}).pop("arrived", None)
        if waiting and waiting.get("script") and (
            arrived or not waiting.get("origins")
        ):
            # The landing origin already loaded under the script. Left in place
            # it would overwrite what the app changes since, on every later
            # tab: swap it for one carrying only what still waits, if anything.
            script = self.actions.retire_site_data(
                browser,
                waiting["script"],
                self._waiting(record.site_data, waiting.get("origins") or []),
            )
            waiting = {**waiting, "script": script}
            if waiting.get("origins") and not script:
                waiting["origins"] = []

        def hold(r: SessionRecord) -> SessionRecord | None:
            if r.session_id != browser:
                # A newer browser, whose own open holds its own note.
                return None
            data = {k: v for k, v in r.site_data.items() if k != "pending"}
            if waiting and (waiting.get("origins") or extra.get("announce")):
                data["pending"] = {"browser": browser, **waiting, **extra}
            return None if data == r.site_data else r.with_site_data(data)

        self.store.update(name, hold)

    def open_browser(
        self,
        name: str,
        url: str | None = None,
        fresh: bool = False,
        restore_site_data: bool = True,
        **wanted,
    ) -> dict:
        """Open this session's browser, or pick up the one it was using.

        The only place a browser is created, and the only place its settings can
        be chosen, which is why it is never done implicitly. Shared by both
        surfaces for the same reason :meth:`act` is.
        """
        from ..core.browser import as_bool
        from . import settings as settings_module

        # What this session was last using. It sits between the client's
        # defaults and the explicit arguments: a caller that names nothing means
        # "carry on where I was", which is a stronger signal than a server-wide
        # default and a weaker one than an argument it just typed.
        previous = self.context(name)
        # Resolved BEFORE the browser being held is ended, because this
        # validates as well as merges: an explicit browser is checked strictly,
        # and doing it afterwards meant a typo in `browser=` quit a perfectly
        # good browser and then failed. A rejected argument must cost nothing.
        resolved = settings_module.resolve(
            wanted, defaults=self.defaults, previous=previous.get("settings")
        )
        # A session holds one browser. Opening a second without ending the first
        # leaves it on the Grid referenced by nothing, holding a slot until the
        # idle timeout — which switching browser did.
        ended = self.end_browser(name)
        # `fresh` drops only the remembered page. The settings still come
        # through the cascade above, because coming back as Chrome when the
        # session was using Firefox is a silent change of shape, not a fresh
        # start - and an explicit `url` is a start the caller named, which
        # `fresh` has no business overriding.
        inherited = None if as_bool(fresh) else (previous.get("url") or None)
        record = self.store.get(name)
        saved = self._restorable(record) if record else {}
        forgotten = None
        if saved and not as_bool(restore_site_data, True):
            # Declining a restore is also how saved data is thrown away — but
            # only once the clean browser is open, so a failed open cannot
            # erase a sign-in the caller never got a new browser for.
            forgotten = site_data_module.summary(saved)["sites"]
            saved = {}
        opened = self.actions.open_session(
            url=url or inherited, **({"site_data": saved} if saved else {}), **resolved
        )
        if forgotten is not None:
            self.store.update(name, lambda r: r.with_site_data({}))
        self.remember(
            name, opened["session_id"], opened.get("url", ""), resolved,
            replacing=ended,
        )
        self._hold_pending(name, opened, {"announce": False})
        if forgotten is not None:
            opened["site_data"] = {"forgotten": forgotten}
        # The Grid's id is dropped here rather than never fetched: it is how the
        # browser is reached, and it is not part of what a caller is told.
        told = {k: v for k, v in opened.items() if k != "session_id"}
        return {"session": name, **told}

    def remember(
        self,
        name: str,
        session_id: str,
        url: str = "",
        settings: dict | None = None,
        replacing: str | None = None,
    ) -> None:
        """Bind a browser to this session.

        The settings are stored because a reopen has to use the *same* browser,
        not a default one — swapping Firefox for Chrome, or a 1400x900 window
        for the node default, midway through a task would be a silent change of
        shape. They are also what ``open_session`` with no arguments inherits.

        ``replacing`` is the browser this open ended or found dead. A record
        naming any other one was bound by an open that ran alongside this one;
        the newest browser is kept and that one is quit, or it would hold a
        Grid slot referenced by nothing until the idle reap.
        """
        loser = {}

        def bound(r: SessionRecord | None) -> SessionRecord:
            current = r.session_id if r is not None else ""
            loser["id"] = (
                current if current and current not in (session_id, replacing) else None
            )
            return SessionRecord(
                session_id=session_id,
                url=url,
                opened_at=time.time(),
                settings=dict(settings or {}),
                site_data=dict(r.site_data if r is not None else {}),
            )

        # One atomic create-or-update: a save landing while the browser
        # opened is kept, and two first opens on a new name cannot both write.
        self.store.upsert(name, bound)
        if loser.get("id"):
            log.info(
                "two opens raced for session %s: quitting browser %s", name, loser["id"]
            )
            try:
                self.actions.end_browser(loser["id"])
            except Exception as exc:  # noqa: BLE001 - it may be gone already
                # errors.message strips the Grid URL's credentials, which a
                # requests HTTPError quotes whole (Copilot, #50).
                log.info(
                    "could not end browser %s: %s", loser["id"], errors.message(exc)
                )

    def touch(self, name: str, url: str | None, browser: str | None = None) -> None:
        """Record where the browser ended up, and slide the record's TTL.

        Called after an action so a later reopen restores the right page, and so
        a session in active use does not expire out of the store underneath the
        caller.

        **No URL still slides the TTL**, keeping the page already recorded —
        `SessionRecord.at` was written for exactly that (`url or self.url`) and
        an early return here contradicted it. The two halves are separate facts:
        "the browser is somewhere I should not write down" is not "this session
        is idle". A bound write lands on `?q=<the password>` and its URL is
        deliberately withheld (§F1.24), and withholding it used to stop the
        clock — so a flow that logs in every few minutes, the one thing secrets
        exist for, expired out of the store while it was being used.

        ``browser`` is the one that produced the result. Once the record names
        another, this page is not that browser's: the TTL still slides, the
        page it would replay after a reap is left alone (Copilot, #50).
        """
        def at(r: SessionRecord) -> SessionRecord:
            return r if browser and r.session_id != browser else r.at(url)

        self.store.update(name, at)

    def reshape(self, name: str, result: dict, browser: str | None = None) -> None:
        """Record the window size an action just gave the browser.

        Only ``resize`` reaches this, because it is the only action that changes
        something the record *stores* rather than just the page. The stored
        settings are what a reopen replays, so a resize that stopped at the
        browser would be silently undone the next time the Grid reaps it.
        """
        size = {k: result[k] for k in ("width", "height") if result.get(k)}
        if size:
            self.store.update(
                name,
                lambda r: None
                if browser and r.session_id != browser
                else r.reshaped(size),
            )

    def context(self, name: str) -> dict:
        """The browser and page this session last had, for a reopen.

        Empty when there is no record, which is the same answer as "nothing to
        inherit" and lets ``open_session`` treat both alike.
        """
        record = self.store.get(name)
        if record is None:
            return {}
        return {"settings": dict(record.settings or {}), "url": record.url or ""}

    def end_browser(self, name: str) -> str | None:
        """End the browser a session holds, keeping the session itself.

        **The one place a browser is ended.** The caller ending its own, the
        admin End button, and ``open_session`` replacing one all come through
        here, so what happens to the session cannot differ between them: the
        browser is quit on the Grid and the record is detached, keeping the
        browser choice and the last page for whatever opens next.

        A session is never removed here, or anywhere. It expires on
        ``SESSION_TTL``, slid forward on every use — and it simply reappears on
        the next call, because the name comes from the caller's own URL or
        header rather than from anything stored.

        Returns the browser that was ended, or None if there was none.
        """
        record = self.store.get(name)
        if record is None or not record.attached:
            return None
        target = record.session_id
        try:
            self.actions.end_browser(target)
        except Exception as exc:  # noqa: BLE001 - it is going either way
            # Already gone, or the Grid is unreachable. Detach regardless: a
            # record naming a browser that cannot be ended is worse than one
            # naming nothing, because the next call would try to use it.
            log.info("could not end browser %s: %s", target, errors.message(exc))
        # Ending took a Grid round trip: detach the record as it is now, and
        # only if it still names this browser — one opened meanwhile stays.
        self.store.update(
            name, lambda r: r.detached() if r.session_id == target else None
        )
        log.info("ended browser %s for session %s", target, name)
        return target
