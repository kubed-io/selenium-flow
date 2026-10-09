"""Which browser a caller means, and the name that says so.

**Every session is named, and the caller supplies the name.** There is one
contract, on both surfaces: say who you are, and the server hands you the
browser that belongs to that name. Nothing here invents a name, and the Grid's
own session id is never part of the contract — it is an implementation detail
of how a browser is reached, held in the record beside the name.

A name arrives one of two ways, and they are the two things any client can set:

1. **``X-Session-Key``**, a header, or **``X-Workspace``**, the same header by
   the name Claude.ai custom connectors accept. What an admin pins inside a
   credential when one credential should mean one session.
2. **``?session=<name>``**, on the URL. The ergonomic path: one shared bearer
   credential, each caller naming itself in its own URL.

**Both at once is refused**, rather than one quietly winning. A request
carrying two names has two ideas about who is calling, and picking one hides
that from whoever wired it up — and so is one name given twice with two values,
``?session=a&session=b``, for the same reason. Neither is refused too, with a
message saying how to name yourself.

The request is read **once, at the edge** — a tool, a route — into a
:class:`Caller`, and that is what everything below takes. Nothing here reads
the request itself.

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

import contextlib
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from .. import faults
from ..core import guidance
from ..core.actions import Actions
from ..core.browser import in_frame
from ..core.coerce import as_bool
from ..core.defaults import DEFAULT_BROWSER
from ..names import GLOBAL_WORKSPACE, valid_workspace_name
from ..principal import Principal
from ..site_data import snapshot as site_data_module
from ..urls import allowed_navigation
from . import locks
from . import settings as settings_module
from .store import MemoryStore, Workspace, WorkspaceStore

log = logging.getLogger(__name__)

RECORDING_OFF = (
    "Recording is not set up on this server (recording.enabled is off). "
    "See the README's Recording section."
)

# `settle`'s default page: the one the result itself reports.
FROM_RESULT = object()

REPLACED_BEFORE_SAVE = (
    "another browser took this session before the save landed: nothing was saved"
)

# A client names its session with either of these. The query parameter is the
# one most clients can set, since an MCP server is usually configured by URL.
NAME_PARAM = "session"
NAME_HEADER = "x-session-key"
# The same name by the header Claude.ai custom connectors accept without review.
# Either header, or both with one value, names the session.
WORKSPACE_HEADER = "x-workspace"

# The name a stdio caller has. One process, one client, so a constant is
# exactly right and there is nothing to tell apart.
STDIO_NAME = "stdio"

UNNAMED = (
    "name your session: add ?session=<name> to the URL, or send an "
    "X-Session-Key (or X-Workspace) header. Every session here is named by "
    "whoever calls, and the server does not invent one. The name is yours to "
    "choose and to reuse — calling again with the same name is how you get the "
    "same browser back."
)

BOTH = (
    "name your session once: this request carries both a session header "
    "(X-Session-Key or X-Workspace) and ?session=, and they are two answers to "
    "one question. Send whichever "
    "one you control and drop the other."
)

# What a client may declare about itself in its connection config, beside its
# name: flag -> (query parameter, header). The header wins, as it does for a
# setting: it is set in a credential, by an admin.
FLAGS = {"resources": ("resources", "x-mcp-resources")}

# The names a client's setting defaults and flags arrive under.
CLIENT_PARAMS = {
    param for param, _, _ in settings_module.SETTINGS.values() if param
} | {param for param, _ in FLAGS.values()}
CLIENT_HEADERS = {
    header for _, header, _ in settings_module.SETTINGS.values() if header
} | {header for _, header in FLAGS.values()}


@dataclass(frozen=True)
class Caller:
    """Who is calling, read once at the edge of a request and handed in.

    Everything below the edge takes one of these rather than reading the
    request itself, so the rule for naming a session lives in exactly one
    place (:meth:`from_request`) and the manager can be driven by any host.

    A request that names no session, or names it badly, still makes a caller:
    reading the shared flow library and listing tools need no name, so the
    refusal waits until something asks for :attr:`name` (or, for a name that
    is malformed or given twice, :attr:`library`).

    ``named_by`` exists for the log line and the status resource: when sessions
    misbehave, the first question is always which mechanism supplied the name.
    ``client`` is the calling client's ``clientInfo`` name, or "". ``flags``
    is what the client declared about itself (``resources``), as it said it.
    """

    named: str = ""
    named_by: str = ""
    client: str = ""
    flags: Mapping[str, str] = field(default_factory=dict)
    # Why a request's name is not one, raised when the name is asked for.
    refusal: ValueError | None = None
    # The client's own setting defaults, as (query, header) values, last of each.
    said: tuple[Mapping[str, str], Mapping[str, str]] = field(
        default=({}, {}), repr=False
    )
    # Who the verified credential says this is (principal.py), or None on an
    # open server. Carried, not consulted: nothing decides anything from it yet.
    principal: Principal | None = None

    @classmethod
    def from_request(
        cls,
        params: Mapping[str, list[str]],
        headers: Mapping[str, list[str]],
        *,
        client: str = "",
    ) -> Caller:
        """The caller a request describes: every value a list, headers lowercased.

        Every value is kept, because a repeated ``?session=`` is two names, and
        a reader that kept only the last would quietly pick one of them.
        """
        header = _names(
            [*(headers.get(NAME_HEADER) or ()), *(headers.get(WORKSPACE_HEADER) or ())]
        )
        param = _names(params.get(NAME_PARAM))
        named, named_by, refusal = "", "", None
        if header and param:
            refusal = ValueError(BOTH)
        elif header or param:
            chosen = header or param
            named_by = "header" if header else "query"
            if len(chosen) > 1:
                refusal = ValueError(_two_names(chosen))
            else:
                try:
                    named = valid_workspace_name(chosen[0])
                except ValueError as exc:
                    refusal = exc
        said = (_last(params, CLIENT_PARAMS), _last(headers, CLIENT_HEADERS))
        flags = {}
        for flag, (param_name, header_name) in FLAGS.items():
            value = said[1].get(header_name) or said[0].get(param_name)
            if value is not None:
                flags[flag] = value
        return cls(named, named_by, client, flags, refusal, said)

    @classmethod
    def stdio(cls, *, client: str = "") -> Caller:
        """The one client a stdio process serves, named by the transport."""
        return cls(STDIO_NAME, "stdio", client)

    @property
    def name(self) -> str:
        """The session this caller is about. Raises when it named none."""
        if self.refusal is not None:
            raise type(self.refusal)(*self.refusal.args)
        if not self.named:
            raise ValueError(UNNAMED)
        return self.named

    @property
    def library(self) -> str:
        """The flow library this caller is about.

        Its own, or the **shared** one when it named no session. That is the
        one place a missing name is not an error, and it is not an exception
        to E18 so much as the other half of it: `global` is read-only to
        everyone (§F1.2), so an unnamed caller can list and read the shared
        flows and can write nowhere at all. Anything that touches a browser
        still has to say who it is (§F2.13).
        """
        if self.refusal is not None:
            raise type(self.refusal)(*self.refusal.args)
        return self.named or GLOBAL_WORKSPACE

    @property
    def defaults(self) -> dict:
        """The setting defaults this client set in its connection config."""
        return settings_module.from_client(*self.said)


def _two_names(names: list[str]) -> str:
    """The refusal for a request that repeats its name with different values."""
    count = "two" if len(names) == 2 else str(len(names))
    return f"the request names {count} sessions: {', '.join(names)}"


def _names(values) -> list[str]:
    """The distinct names a request gives in one place, blanks dropped.

    Surrounding whitespace is not part of a name, and a blank one is no name.
    """
    return list(dict.fromkeys(str(v).strip() for v in values or () if str(v).strip()))


def _last(values: Mapping[str, list[str]], wanted) -> dict[str, str]:
    """The last value of each name in ``wanted``: a client setting, read as it
    always has been, where a repeat is the later one."""
    return {k: v[-1] for k, v in values.items() if k in wanted and v}


def values_of(request) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(query params, headers) of a Starlette request, every value a list.

    Header names are lowercased here, so a header is matched whatever case the
    client wrote it in.
    """
    params: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        params.setdefault(key, []).append(value)
    headers: dict[str, list[str]] = {}
    for key, value in request.headers.items():
        headers.setdefault(key.lower(), []).append(value)
    return params, headers


class Workspaces:
    """Resolves a named workspace to the browser it holds, through a ``WorkspaceStore``.

    The store maps **name -> record**, and the record carries the Grid session
    id, the page, and the settings a reopen has to replay. The name is the key
    in every sense: it is the store key, the flow library's directory, and the
    only identifier a caller ever sees.
    """

    def __init__(
        self,
        actions: Actions,
        store: WorkspaceStore | None = None,
        skill_available: bool = True,
        defaults: dict | None = None,
        recordings=None,
    ):
        self.actions = actions
        self.store = store if store is not None else MemoryStore()
        # Whether this server serves the skill. The status points at a reference
        # for the caller to read, and a skill:// URI nobody can read teaches an
        # agent the manual is broken (Copilot, #36).
        self.skill_available = skill_available
        # The operator's floor for a new workspace, from the config's session
        # section. See `workspace/settings.py` for where this sits in the cascade.
        self.defaults = dict(defaults or {})
        # Who files a recorded browser's video (`recordings.Collector`), or None
        # when recording is off. Told on open and on end; it owns no browser.
        self.recordings = recordings

    @property
    def kind(self) -> str:
        """Which backend is in play, for /ready, /info and the config log line."""
        return getattr(self.store, "kind", "memory")

    def describe(self, caller: Caller) -> dict:
        """What this workspace is, and whether it currently holds a browser.

        Deliberately side-effect free: reading a status resource must never open
        a browser, so this peeks at the store rather than going through
        ``resolve``. The Grid's session id is not in it — that is the whole
        point of E18 — so what a caller reads back is what it may say.
        """
        name = caller.name
        status = {
            "session": name,
            "named_by": caller.named_by,
            "principal": caller.principal.status() if caller.principal else None,
            "browser": None,
            "url": None,
            "live": False,
            "recording": False,
            "in_frame": None,
            "window": None,
            "store": self.kind,
            "settings": {},
        }
        if self.skill_available:
            status["guidance"] = guidance.pointer("SESSIONS.md")
        record = self.store.get(name)
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
        status["recording"] = bool(status["live"] and status["settings"].get("record"))
        if status["live"]:
            # Only worth a round trip when there is a live browser to ask, and
            # one reconnect answers both questions.
            try:
                driver = self.actions.grid.reconnect(record.session_id)
                status["in_frame"] = in_frame(driver)
                size = driver.get_window_size()
                status["window"] = f"{size['width']}x{size['height']}"
            except Exception:  # noqa: BLE001 - status must never fail
                pass
        return status

    def browser(self, name: str) -> str:
        """The Grid id this workspace is driving, or "" if it holds no live one.

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
        replay = dict(record.settings or {})
        if self.recordings is None:
            # Nobody would collect the video (recording was turned off since).
            replay.pop("record", None)
        opened = self.actions.open_session(
            url=record.url or None,
            **(self._recorded(name, replay) if replay.get("record") else {}),
            **({"site_data": saved} if saved else {}),
            **replay,
        )
        # Nobody asked for this reopen, so the first result after it says what
        # came back: the record holds the report until `touch` hands it over.
        # A reopen alongside this one may bind first; then act on its browser.
        # The settings this browser was opened with: without `record` when
        # recording is off, so nothing reports a video nobody is making.
        kept = self.remember(
            name,
            opened["session_id"],
            opened.get("url", record.url or ""),
            replay,
            replacing=record.session_id,
            report=opened.get("site_data"),
        )
        # A browser this session does not hold keeps its provisional note.
        if replay.get("record") and kept == opened["session_id"]:
            try:
                self.recordings.expect(
                    name,
                    opened["session_id"],
                    replay.get("browser") or DEFAULT_BROWSER,
                )
            except (OSError, ValueError) as exc:
                # The browser is open and remembered; failing the caller's
                # action over the filing would help nobody.
                log.warning(
                    "recording for %s cannot be filed: %s", name, faults.message(exc)
                )
        return kept

    def _recorded(self, name: str, settings: dict) -> dict:
        """The arguments a recorded open adds: the video's name, and a note
        for the collector the moment the browser exists, marked ``discard``.

        Provisional: the caller notes it again without the mark once the
        session holds the browser. Until then its video is no session's, and
        it stays that way when the open fails after the Grid made the browser
        or a concurrent open binds first and this one is quit (Copilot, #59)."""

        def created(grid_id: str) -> None:
            try:
                self.recordings.expect(
                    name, grid_id, settings.get("browser") or DEFAULT_BROWSER,
                    discard=True,
                )
            except (OSError, ValueError) as exc:
                # The type alone: the note's path names the Grid id. Never the
                # open's failure: the browser is made, and the open goes on.
                log.warning(
                    "a recording for %s cannot be noted: %s",
                    name, type(exc).__name__,
                )

        return {"video_name": name, "on_created": created}

    def act(self, caller: Caller, call, *, reshapes: bool = False) -> dict:
        """Resolve this workspace's browser, act on it, remember where it ended up.

        The three steps every action shares, on **both** surfaces. It lives here
        rather than in each of them because the two used to carry their own copy
        of it and could therefore disagree: the HTTP surface never touched a
        record, so it never slid a TTL and never reopened a reaped browser, and
        that difference was invisible until a session expired mid-workflow.

        ``reshapes`` is for the one action that changes a setting the record
        *stores* rather than just the page it is on. See :meth:`reshape`.

        The action takes the session's turn on the browser itself, in
        `Recipe.run` (`workspace.locks`). Resolving and settling do not need it:
        every record write is a compare-and-set.
        """
        name = caller.name
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
        url=FROM_RESULT,
    ) -> None:
        """What a finished action means for the record, in place on ``result``.

        **Every record write after an action comes through here**: a single
        call (`act`), a flow step and the run's pages (`flows/api.run_for`),
        and a bound write (`secrets.perform_write`). So the capture is stripped
        and stored, the page recorded and a reopen's report handed over in one
        place, and a new consumer cannot forget to forward the report.

        ``url`` is the page to record: the result's own by default, ``None``
        to withhold it (§F1.24) — the TTL still slides — or the pages a run
        reached, in order. ``touch=False`` is for a flow step: the runner
        records its pages before a save step and at the end rather than after
        every step, and so carries a reopen's report on the run, not on a step.

        ``browser`` is the one that produced ``result``: once the record names
        another, its page is not recorded and its save is not kept.
        """
        if not isinstance(result, dict):
            return
        # Popped before the store is asked anything, so a store that fails
        # cannot leave the capture — every value — in what the caller gets.
        captured = result.pop(site_data_module.CAPTURED, None)
        told = None
        if touch:
            if url is FROM_RESULT:
                url = result.get("url")
            pages = url if isinstance(url, (list, tuple)) else (url,)
            told = self.touch(name, *pages, browser=browser)
        if told:
            result["site_data"] = told
        try:
            if reshapes:
                self.reshape(name, result, browser=browser)
            self._save_site_data(name, result, captured, browser)
        except Exception:
            # The caller gets the error, not this result: the reopen's report
            # goes back on the record for the next one (Copilot, #51).
            if told:
                self._hold_again(name, told, browser)
            raise

    def _hold_again(self, name: str, report: dict, browser: str | None) -> None:
        """Put a reopen's report back when the result carrying it failed."""

        def held(r: Workspace) -> Workspace | None:
            if r.reopened or (browser and r.session_id != browser):
                return None
            return replace(r, reopened={"browser": r.session_id, "report": report})

        with contextlib.suppress(Exception):  # the caller's own error is the one to see
            self.store.update(name, held)

    def _save_site_data(
        self,
        name: str,
        result: dict,
        captured: dict | None,
        producer: str | None = None,
    ) -> None:
        """A captured save, stored on the record and replaced in the result by
        a short receipt, so no caller ever sees a value.

        Written through ``store.update``, applied to the record as it is then,
        and only by the browser that captured it.
        """
        if not captured:
            return

        def save(r: Workspace) -> tuple[Workspace | None, dict]:
            if producer is not None and r.session_id != producer:
                # Another browser holds the session now — perhaps one opened
                # with restore_site_data=false. What this one captured is not
                # its to keep (Copilot, #50).
                return None, {
                    "cookies": 0, "sites": [],
                    "skipped": [{"reason": REPLACED_BEFORE_SAVE}],
                }
            data, receipt = site_data_module.snapshot(
                r.site_data, captured, r.history, time.time()
            )
            return r.with_site_data(data), receipt

        stored, receipt = self.store.change(name, save)
        if stored is None:
            # The record expired before `save` ever ran, or between its runs.
            return
        result["saved"] = receipt
        result["uri"] = site_data_module.LIST_URI

    @staticmethod
    def _restorable(record: Workspace) -> dict:
        """The snapshot a new browser is given, or {} when there is none."""
        if not site_data_module.restorable(record.site_data):
            return {}
        return dict(record.site_data)

    def open_browser(
        self,
        caller: Caller,
        url: str | None = None,
        fresh: bool = False,
        restore_site_data: bool = True,
        **wanted,
    ) -> dict:
        """Open this workspace's browser, or pick up the one it was using.

        The only place a browser is created, and the only place its settings can
        be chosen, which is why it is never done implicitly. Shared by both
        surfaces for the same reason :meth:`act` is.

        It takes no turn on the browser (`workspace.locks`): it makes one rather
        than driving it, and the one it replaces goes through `end_browser`.
        """
        name = caller.name
        # What this session was last using. It sits between the client's
        # defaults and the explicit arguments: a caller that names nothing means
        # "carry on where I was", which is a stronger signal than a server-wide
        # default and a weaker one than an argument it just typed.
        previous = self.context(name)
        # Resolved BEFORE the browser being held is ended, because this
        # validates as well as merges: an explicit browser is checked strictly,
        # and doing it afterwards meant a typo in `browser=` quit a perfectly
        # good browser and then failed. A rejected argument must cost nothing.
        inherited_settings = {
            k: v for k, v in (previous.get("settings") or {}).items() if k != "record"
        }
        resolved = settings_module.resolve(
            wanted,
            defaults=self.defaults,
            previous=inherited_settings,
            client=caller.defaults,
        )
        if resolved.get("record") and self.recordings is None:
            raise ValueError(RECORDING_OFF)
        # The caller's page, checked for the same reason. A remembered one is
        # where the browser already was, so it is not the caller's to check.
        if url:
            allowed_navigation(url)
        # A session holds one browser. Opening a second without ending the first
        # leaves it on the Grid referenced by nothing, holding a slot until the
        # idle timeout — which switching browser did.
        ended = self.end_browser(caller)
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
            forgotten = (site_data_module.summary(saved) or {"sites": 0})["sites"]
            saved = {}
        opened = self.actions.open_session(
            url=url or inherited,
            **(self._recorded(name, resolved) if resolved.get("record") else {}),
            **({"site_data": saved} if saved else {}),
            **resolved,
        )
        kept = self.remember(
            name, opened["session_id"], opened.get("url", ""), resolved,
            replacing=ended, forget_site_data=forgotten is not None,
        )
        if kept != opened["session_id"]:
            # A concurrent open bound first and this browser was quit: describe
            # the one the session holds, not the discarded one (Copilot, #50).
            # Its provisional note stays, so its video is deleted, not filed.
            return self._held(name)
        noted = None
        if resolved.get("record"):
            try:
                self.recordings.expect(
                    name,
                    opened["session_id"],
                    resolved.get("browser") or DEFAULT_BROWSER,
                )
            except (OSError, ValueError) as exc:
                # ValueError: `InvalidName` from a link on the note's path. The
                # browser is open either way, so it is never the caller's 400.
                # The browser is open and recording on the Grid; only the filing
                # failed. Said in the result, as `file_error` is for a screenshot,
                # and never with the path (names a layout nobody asked about).
                log.warning(
                    "recording for %s cannot be filed: %s", name, faults.message(exc)
                )
                noted = f"this recording cannot be filed: {type(exc).__name__}"
        if forgotten is not None:
            opened["site_data"] = {"forgotten": forgotten}
        # The Grid's id is dropped here rather than never fetched: it is how the
        # browser is reached, and it is not part of what a caller is told.
        told = {k: v for k, v in opened.items() if k != "session_id"}
        told["recording"] = bool(resolved.get("record"))
        if noted:
            told["recording_error"] = noted
        return {"session": name, **told}

    def _held(self, name: str) -> dict:
        """What ``open_session`` reports for the browser the record holds."""
        record = self.store.get(name)
        settings = dict(record.settings or {}) if record else {}
        held = {
            "session": name,
            "browser": settings.get("browser"),
            "url": record.url if record else "",
            "settings": settings,
            "recording": bool(settings.get("record")),
            "note": "another open of this session bound its browser first: "
            "this is that browser",
        }
        for key in ("width", "height"):
            if key in settings:
                held[key] = settings[key]
        return held

    def remember(
        self,
        name: str,
        session_id: str,
        url: str = "",
        settings: dict | None = None,
        replacing: str | None = None,
        forget_site_data: bool = False,
        report: dict | None = None,
    ) -> str:
        """Bind a browser to this workspace, and say which browser it holds.

        The settings are stored because a reopen has to use the *same* browser,
        not a default one — swapping Firefox for Chrome, or a 1400x900 window
        for the node default, midway through a task would be a silent change of
        shape. They are also what ``open_session`` with no arguments inherits.

        ``replacing`` is the browser this open ended or found dead. A record
        naming any other one was bound by an open that ran alongside this one,
        and the first to bind wins: that browser may already be in its
        caller's hands, so quitting it would fail a call (Copilot, #50). This
        one is quit instead — or it would hold a Grid slot referenced by
        nothing — and its caller is handed the browser that was kept.

        ``forget_site_data`` is a declined restore: the saved data is erased in
        the same write that binds, so an open that loses erases nothing from
        the browser that won.

        ``report`` is a silent reopen's site data report: the record holds it
        for this browser until `touch` hands it to the first result. Any other
        bind drops whatever an earlier reopen still held.
        """
        ttl = self.store.ttl

        def bound(r: Workspace | None) -> tuple[Workspace | None, str | None]:
            current = r.session_id if r is not None else ""
            if current and current not in (session_id, replacing):
                # Bound first by another open: that browser is the one kept.
                return None, current
            # Replaced, not rebuilt: a field the record gains later survives a
            # bind. History (where the session has been) survives a new browser.
            fresh = replace(
                r if r is not None else Workspace(),
                session_id=session_id,
                opened_at=time.time(),
                settings=dict(settings or {}),
                site_data={} if forget_site_data or r is None else dict(r.site_data),
                # Reset explicitly: any other bind drops an earlier reopen's report.
                reopened={"browser": session_id, "report": report} if report else {},
            )
            return fresh.visited(url, ttl=ttl), None

        # One atomic create-or-update: a save landing while the browser
        # opened is kept, and two first opens on a new name cannot both write.
        _, kept = self.store.change(name, bound, create=True)
        if not kept:
            return session_id
        log.info(
            "two opens raced for session %s: quitting browser %s", name, session_id
        )
        try:
            self.actions.end_browser(session_id)
        except Exception as exc:  # noqa: BLE001 - it may be gone already
            # faults.message strips the Grid URL's credentials, which a
            # requests HTTPError quotes whole (Copilot, #50).
            log.info("could not end browser %s: %s", session_id, faults.message(exc))
        return kept

    def touch(
        self, name: str, *urls: str | None, browser: str | None = None
    ) -> dict | None:
        """Record where the browser landed, and slide the record's TTL.

        Called after an action so a later reopen goes back to the right page —
        the top of the history — and so a session in active use does not
        expire out of the store underneath the caller. Reached through
        :meth:`settle`; a flow run passes every page it reached, in order.

        **No URL still slides the TTL**, and records nothing. The two halves
        are separate facts: "the browser is somewhere I should not write down"
        is not "this session is idle". A bound write lands on `?q=<the
        password>` and its URL is deliberately withheld (§F1.24); withholding
        it used to stop the clock, so a flow that logs in every few minutes —
        the one thing secrets exist for — expired out of the store while it
        was being used.

        ``browser`` is the one that produced the result. Once the record names
        another, this page is not that browser's: the TTL still slides, the
        history is left alone (Copilot, #50).

        Returns a silent reopen's report the first time this browser is touched
        after it, and never again.
        """
        ttl = self.store.ttl

        def at(r: Workspace) -> tuple[Workspace, dict | None]:
            if browser and r.session_id != browser:
                return r, None
            held = r.reopened
            report = held.get("report") if held.get("browser") == r.session_id else None
            return r.visited(*urls, ttl=ttl).delivered(), report

        return self.store.change(name, at)[1]

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
        """The browser and page this workspace last had, for a reopen.

        Empty when there is no record, which is the same answer as "nothing to
        inherit" and lets ``open_session`` treat both alike.
        """
        record = self.store.get(name)
        if record is None:
            return {}
        return {"settings": dict(record.settings or {}), "url": record.url or ""}

    def visited(self, name: str) -> list[str]:
        """The origins this workspace has been to, newest first.

        As the next write would keep them: a save reads this before its own
        touch prunes, so an origin that aged out since the last call would
        otherwise still be read and saved (Copilot, #51).
        """
        record = self.store.get(name)
        if record is None:
            return []
        return [v["origin"] for v in record.pruned(time.time(), self.store.ttl).history]

    def end_browser(self, caller: Caller) -> str | None:
        """End the browser a workspace holds, keeping the workspace itself.

        **The one place a browser is ended.** The caller ending its own, the
        admin End button, and ``open_session`` replacing one all come through
        here, so what happens to the session cannot differ between them: the
        browser is quit on the Grid and the record is detached, keeping the
        browser choice and the last page for whatever opens next.

        A session is never removed here, or anywhere. It expires on
        ``SESSION_TTL``, slid forward on every use — and it simply reappears on
        the next call, because the name comes from the caller's own URL or
        header rather than from anything stored.

        **It never waits its turn** (`workspace.locks`): a call driving the
        browser is told it is ending - a long `assert` raises its cancellation
        at its next poll - and the browser is quit without waiting for it.
        Queued behind a 900 s `assert`, the call that exists to interrupt one
        could not.

        Returns the browser that was ended, or None if there was none.
        """
        name = caller.name
        record = self.store.get(name)
        if record is None or not record.attached:
            return None
        target = record.session_id
        locks.interrupt(target)
        quit_ok = False
        try:
            self.actions.end_browser(target)
            quit_ok = True
        except Exception as exc:  # noqa: BLE001 - it is going either way
            # Already gone, or the Grid is unreachable. Detach regardless: a
            # record naming a browser that cannot be ended is worse than one
            # naming nothing, because the next call would try to use it.
            log.info("could not end browser %s: %s", target, faults.message(exc))
        # Only a confirmed quit (a 404 counts: Grid.quit treats it as done)
        # starts the collector's clock; after a failure the browser may still
        # be recording, and the collector's Grid listing finds it later.
        if quit_ok and self.recordings is not None:
            self.recordings.ended(target)
        # Ending took a Grid round trip: detach the record as it is now, and
        # only if it still names this browser — one opened meanwhile stays.
        self.store.update(
            name, lambda r: r.detached() if r.session_id == target else None
        )
        log.info("ended browser %s for session %s", target, name)
        return target
