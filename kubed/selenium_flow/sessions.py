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

from .actions import Actions
from .browser import DEFAULT_BROWSER
from .store import MemoryStore, SessionRecord, SessionStore

log = logging.getLogger(__name__)

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
    from .flows import valid_session_name

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
    from .flows import GLOBAL_SESSION

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

    def __init__(self, actions: Actions, store: SessionStore | None = None):
        self.actions = actions
        self.store = store if store is not None else MemoryStore()

    @property
    def kind(self) -> str:
        """Which backend is in play, for /health and the config log line."""
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
        from .flows import GLOBAL_SESSION

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
            "guidance": "skill://selenium-flow/references/SESSIONS.md",
        }
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
                from . import browser as browser_module

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
        opened = self.actions.open_session(
            url=record.url or None, **(record.settings or {})
        )
        self.remember(
            name,
            opened["session_id"],
            opened.get("url", record.url or ""),
            record.settings,
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
        if isinstance(result, dict):
            self.touch(name, result.get("url"))
            if reshapes:
                self.reshape(name, result)
        return result

    def open_browser(
        self, name: str, url: str | None = None, fresh: bool = False, **wanted
    ) -> dict:
        """Open this session's browser, or pick up the one it was using.

        The only place a browser is created, and the only place its settings can
        be chosen, which is why it is never done implicitly. Shared by both
        surfaces for the same reason :meth:`act` is.
        """
        from . import settings as settings_module
        from .browser import as_bool

        # What this session was last using. It sits between the client's
        # defaults and the explicit arguments: a caller that names nothing means
        # "carry on where I was", which is a stronger signal than a server-wide
        # default and a weaker one than an argument it just typed.
        previous = self.context(name)
        # Resolved BEFORE the browser being held is ended, because this
        # validates as well as merges: an explicit browser is checked strictly,
        # and doing it afterwards meant a typo in `browser=` quit a perfectly
        # good browser and then failed. A rejected argument must cost nothing.
        resolved = settings_module.resolve(wanted, previous=previous.get("settings"))
        # A session holds one browser. Opening a second without ending the first
        # leaves it on the Grid referenced by nothing, holding a slot until the
        # idle timeout — which switching browser did.
        self.end_browser(name)
        # `fresh` drops only the remembered page. The settings still come
        # through the cascade above, because coming back as Chrome when the
        # session was using Firefox is a silent change of shape, not a fresh
        # start - and an explicit `url` is a start the caller named, which
        # `fresh` has no business overriding.
        inherited = None if as_bool(fresh) else (previous.get("url") or None)
        opened = self.actions.open_session(url=url or inherited, **resolved)
        self.remember(name, opened["session_id"], opened.get("url", ""), resolved)
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
    ) -> None:
        """Bind a browser to this session.

        The settings are stored because a reopen has to use the *same* browser,
        not a default one — swapping Firefox for Chrome, or a 1400x900 window
        for the node default, midway through a task would be a silent change of
        shape. They are also what ``open_session`` with no arguments inherits.
        """
        self.store.set(
            name,
            SessionRecord(
                session_id=session_id,
                url=url,
                opened_at=time.time(),
                settings=dict(settings or {}),
            ),
        )

    def touch(self, name: str, url: str | None) -> None:
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
        """
        record = self.store.get(name)
        if record is not None:
            self.store.set(name, record.at(url))

    def reshape(self, name: str, result: dict) -> None:
        """Record the window size an action just gave the browser.

        Only ``resize`` reaches this, because it is the only action that changes
        something the record *stores* rather than just the page. The stored
        settings are what a reopen replays, so a resize that stopped at the
        browser would be silently undone the next time the Grid reaps it.
        """
        size = {k: result[k] for k in ("width", "height") if result.get(k)}
        record = self.store.get(name) if size else None
        if record is not None:
            self.store.set(name, record.reshaped(size))

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
            log.info("could not end browser %s: %s", target, exc)
        self.store.set(name, record.detached())
        log.info("ended browser %s for session %s", target, name)
        return target
