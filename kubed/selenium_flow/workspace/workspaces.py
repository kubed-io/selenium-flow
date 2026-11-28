"""Which browser a caller means, and the name that says so.

Every workspace is named, and the caller supplies the name: nothing here invents
one. The Grid's session id is never part of the contract; it is held in the
record beside the name.

A name arrives one of two ways:

1. ``Sf-Workspace``, a header: what an admin pins inside a credential when one
   credential should mean one workspace. Behind agentgateway, the gateway sets
   it from ``?workspace=``, the caller's ``Sf-Workspace``, its ``X-Workspace``
   or the token's user, and drops the query string.
2. ``?workspace=<name>``, on the URL: one shared bearer credential, each caller
   naming itself in its own URL.

``X-Workspace`` is an alias of ``Sf-Workspace``, for a client such as a
Claude.ai connector that can only send allowlisted header names, and
``Sf-Workspace`` wins when both are sent. The other old ``X-`` headers are
refused with the new spelling.

Both at once is refused, and so is one name given twice with two values
(``?workspace=a&workspace=b``): such a request has two ideas about who is
calling. Neither is refused too, with a message saying how to name yourself.

The request is read once, at the edge (a tool, a route), into a :class:`Caller`,
and that is what everything below takes.

On stdio there is no request, and one process serves one client, so the name is
the constant :data:`STDIO_NAME`.

``Context.session_id`` is not used: when it cannot find a real session it
returns a new ``str(uuid4())`` on every call, which would leak one browser per
tool call.

The name is validated where it arrives, by the rule that validates a flow
library's directory, because a workspace name is that directory. Everything
downstream (the browser record, the flow library, the kept files) takes the name
as given.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Literal, NamedTuple

from .. import faults
from ..capture import NAMED_OFF as CAPTURE_NAMED_OFF
from ..capture import NETWORK_LISTINGS
from ..capture import OFF as CAPTURE_STATE_OFF
from ..capture import views as capture_views
from ..capture.store import Buffer
from ..core import cancel, guidance, session_caps
from ..core import tabs as tabs_module
from ..core.actions import CUTS, INSECURE_SKIP, OPEN_PRIVATE, Actions, cut_names
from ..core.browser import in_frame
from ..core.coerce import as_bool
from ..core.defaults import DEFAULT_BROWSER, normalize_browser
from ..monitor.monitor import ATTACH_FAILED, DEFAULT_TIMEOUT, KEEPALIVE
from ..names import GLOBAL_WORKSPACE, valid_workspace_name
from ..principal import Principal, subject_of
from ..redaction import Forms
from ..site_data import snapshot as site_data_module
from ..urls import (
    allowed_navigation,
    origin_of,
    page_key,
    page_of,
    without_userinfo,
)
from . import locks, sessions
from . import settings as settings_module
from .calls import Calls, Timing, told
from .config import Configs
from .origins import OriginChanged, Origins
from .scrub import Scrubber
from .store import (
    DEFAULT_TTL_SECONDS,
    MemoryStore,
    Old,
    Session,
    StoreConflict,
    Workspace,
    WorkspaceStore,
)

log = logging.getLogger(__name__)

RECORDING_OFF = (
    "Recording is not set up on this server (recording.enabled is off). "
    "See the README's Recording section."
)

CAPTURE_OFF = "Capture is not set up on this server."

# A headless browser draws nothing for the recorder to film. The second is for
# headless remembered from an earlier open, so the caller knows what to clear.
HEADLESS_RECORD = (
    "a headless browser draws nothing to film: "
    "record=true cannot be asked with headless=true"
)
HEADLESS_RECORD_REMEMBERED = (
    "this workspace's browser is headless, remembered from an earlier open: "
    "pass headless=false to record"
)

# What an open says when a concurrent open bound its browser first.
RACED_NOTE = (
    "another open in this workspace bound its browser first: this is that browser"
)


# How many times a move (WS-90) starts again when an old pod names another
# sub-session between its steps.
MOVE_TRIES = 3
# What `Workspaces.change_shared`'s write notes when it met a record still to move.
_OLD = object()

# `settle`'s default page: the one the result itself reports.
FROM_RESULT = object()

# What a result carries that is not text a page spelt, set aside from the
# scrub and put back (`Workspaces._scrubbed`): a captured site data save,
# which a restore needs exactly as read, and a screenshot's pixels, which a
# match would only corrupt.
_NOT_TEXT = (site_data_module.CAPTURED,)
_PIXELS = ("image",)


class Said(NamedTuple):
    """One said call's answer (`Workspaces._said`): its result, scrubbed; the
    page hint, or None; and whether the result's URL carried a value, so is
    withheld from the record."""

    result: dict
    page: dict | None
    url_hidden: bool


# The session key the call in flight acts in, for what the action layer asks
# back about it (`Actions.visited`): set by `_said` in the call's own thread, as
# `core.cancel` carries its flags, since an argument would land in every tool
# schema derived from the actions.
_ACTING: ContextVar[str] = ContextVar("acting_session", default="")


def acting() -> str:
    """The session key the call in flight acts in, or "" outside a call."""
    return _ACTING.get()


@contextlib.contextmanager
def _acting_in(key: str):
    token = _ACTING.set(key)
    try:
        yield
    finally:
        _ACTING.reset(token)


# `remember`'s "leave who opened it alone": a silent reopen after a reap is the
# same caller's browser coming back, not a new opener.
KEEP = object()

REPLACED_BEFORE_SAVE = (
    "another session took over this workspace before the save landed: nothing was saved"
)

NAME_PARAM = "workspace"

# Every key `workspace://current` can carry, whether or not this workspace has
# a value for it: the parts `workspace://current/{part}` answers. A key the
# context gains is added here.
CONTEXT_KEYS = (
    "workspace",
    "session",
    "main",
    "sessions",
    "named_by",
    "principal",
    "browser",
    "url",
    "live",
    "recording",
    "capture",
    "capturing",
    "in_frame",
    "window",
    "tabs",
    "grid_timeout",
    "version",
    "store",
    "settings",
    "tags",
    "flows_here",
    "captured",
    "site_data",
    "guidance",
    "config",
    "config_url",
)
WORKSPACE_HEADER = "sf-workspace"
# Claude.ai's connectors can send only allowlisted header names, and
# `x-workspace` is one where `sf-workspace` is not. Read only when
# `Sf-Workspace` gives nothing.
WORKSPACE_ALIAS = "x-workspace"

# Refused, so a client wired the old way hears it on its first call rather
# than as "name your workspace". Keyed by the spelling a person reads; a
# request's headers are looked up lowercased. The refusal follows this order.
OLD_HEADERS = {
    "X-Browser": "Sf-Browser",
    "X-Version": "Sf-Version",
    "X-Window-Width": "Sf-Window-Width",
    "X-Window-Height": "Sf-Window-Height",
    "X-Page-Load-Timeout": "Sf-Page-Load-Timeout",
    "X-Script-Timeout": "Sf-Script-Timeout",
}
RENAMED = "`{old}` is now `{new}`: rename the header"

# The name a stdio caller has: one process serves one client.
STDIO_NAME = "stdio"

UNNAMED = (
    "name your workspace: add ?workspace=<name> to the URL, or send an "
    "Sf-Workspace header. Every workspace here is named by whoever calls, and "
    "the server does not invent one. The name is yours to choose and to reuse "
    "— calling again with the same name is how you get the same browser back."
)

BOTH = (
    "name your workspace once: this request carries both an Sf-Workspace header "
    "and ?workspace=, and they are two answers to one question. Send whichever "
    "one you control and drop the other."
)

# The pin: a connection that sends one reaches that session and no other (WS-82).
SESSION_PARAM = "session"
SESSION_HEADER = "sf-session"

BOTH_SESSIONS = (
    "pin your session once: this request carries both an Sf-Session header and "
    "?session=, and they are two answers to one question. Send whichever one "
    "you control and drop the other."
)

# Neither text quotes a session's name, the pin's or the call's: a caller's own
# is named by its username or subject, and a refusal's text is logged (WS-82).
PIN_DIFFERS = (
    "this connection is pinned to a session and the call names another: drop "
    "the session argument, or name the one the connection is pinned to"
)

NO_SUB = (
    "no such session in workspace {workspace!r}: call open_session with the "
    "same session to open it. No other call opens one, so a mistyped name "
    "starts no browser."
)

# Never quotes the session's name: a caller's own is named by its username or
# subject, and a refusal's text is logged (WS-82).
NO_BROWSER = (
    "no browser is open in this session of workspace {workspace!r}: call "
    "open_session for the same session first. Only open_session opens one."
)

# The names a client's setting defaults arrive under.
CLIENT_PARAMS = {
    param for param, _, _ in settings_module.SETTINGS.values() if param
}
CLIENT_HEADERS = {
    header for _, header, _ in settings_module.SETTINGS.values() if header
}


@dataclass(frozen=True)
class Caller:
    """Who is calling, read once at the edge of a request and handed in.

    A request that names no workspace, or names it badly, still makes a caller:
    reading the shared flow library and listing tools need no name, so the
    refusal waits until something asks for :attr:`name` (or, for a name that
    is malformed or given twice, :attr:`library`).

    ``named_by`` says which mechanism supplied the name, for the log line and
    the status resource.
    """

    named: str = ""
    named_by: str = ""
    # Why a request's name is not one, raised when the name is asked for.
    refusal: ValueError | None = None
    # The client's own setting defaults, as (query, header) values, last of each.
    said: tuple[Mapping[str, str], Mapping[str, str]] = field(
        default=({}, {}), repr=False
    )
    # Who the verified credential says this is (principal.py), or None on an
    # open server. Only its subject is consulted: which session an unnamed
    # call reaches (`Workspaces.session_of`).
    principal: Principal | None = None
    # The session this connection is pinned to (`Sf-Session`, `?session=`), or
    # "", and why a pin is not one, raised when the pin is asked for.
    pin: str = ""
    pin_refusal: ValueError | None = None

    @classmethod
    def from_request(
        cls,
        params: Mapping[str, list[str]],
        headers: Mapping[str, list[str]],
    ) -> Caller:
        """The caller a request describes: every value a list, headers lowercased.

        Every value is kept, because a repeated ``?workspace=`` is two names,
        and a reader that kept only the last would quietly pick one of them.
        An old name anywhere in the request is refused, even beside a new one.
        """
        header = _names(headers.get(WORKSPACE_HEADER)) or _names(
            headers.get(WORKSPACE_ALIAS)
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
        if (renamed := _old_headers(headers)) is not None:
            named, named_by, refusal = "", "", ValueError(renamed)
        said = (_last(params, CLIENT_PARAMS), _last(headers, CLIENT_HEADERS))
        pin, pin_refusal = _pin(params, headers)
        return cls(
            named, named_by, refusal, said,
            pin=pin, pin_refusal=pin_refusal,
        )

    @classmethod
    def stdio(cls) -> Caller:
        """The one client a stdio process serves, named by the transport."""
        return cls(STDIO_NAME, "stdio")

    @property
    def name(self) -> str:
        """The workspace this caller is about. Raises when it named none."""
        if self.refusal is not None:
            raise type(self.refusal)(*self.refusal.args)
        if not self.named:
            raise ValueError(UNNAMED)
        return self.named

    @property
    def pinned(self) -> str:
        """The session this caller's connection is pinned to, or "" when none.
        Raises when the pin is given twice or is no session's name."""
        if self.pin_refusal is not None:
            raise type(self.pin_refusal)(*self.pin_refusal.args)
        return self.pin

    @property
    def library(self) -> str:
        """The flow library this caller is about: its own, or the shared one
        when it named no workspace.

        `global` is read-only to everyone, so an unnamed caller can list and
        read the shared flows and write nowhere. Anything that touches a
        browser still needs a name.
        """
        if self.refusal is not None:
            raise type(self.refusal)(*self.refusal.args)
        return self.named or GLOBAL_WORKSPACE

    @property
    def defaults(self) -> dict:
        """The setting defaults this client set in its connection config."""
        return settings_module.from_client(*self.said)


def _kind(settings: Mapping | None) -> str:
    """The kind of browser ``settings`` open (``chrome``, ``firefox``)."""
    return (settings or {}).get("browser") or DEFAULT_BROWSER


def _wanted_kind(browser) -> str:
    """The kind of browser an open asks for before its settings are resolved:
    a known kind or "", never what the caller typed, since a metric's
    attributes are a closed set."""
    if browser is None or not str(browser).strip():
        return ""
    try:
        return normalize_browser(browser)
    except ValueError:
        return ""


def _named_by(caller: Caller) -> str:
    """The workspace ``caller`` names, or "" when it names none (yet)."""
    try:
        return caller.name
    except ValueError:
        return ""


def _measured(opened: dict) -> str | None:
    """The window an open measured its browser at, as ``WxH``, or None."""
    width, height = opened.get("width"), opened.get("height")
    return f"{width}x{height}" if width and height else None


def _hinted(before: str, urls: tuple[str | None, ...]) -> str | None:
    """The page to hint for after landing on ``urls``, ``before`` being the
    page the session was on: the last of them with an origin, when its host,
    port, path or route differs. Pure and never raises (`page_key` only), so it
    runs inside a store's ``fn``."""
    after = next((u for u in reversed(urls) if origin_of(u or "")), None)
    key = page_key(after)
    return after if key is not None and key != page_key(before) else None


def _two_names(
    names: list[str], verb: str = "names", noun: str = "workspaces"
) -> str:
    """The refusal for a request that repeats its name with different values."""
    count = "two" if len(names) == 2 else str(len(names))
    return f"the request {verb} {count} {noun}: {', '.join(names)}"


def _pin(
    params: Mapping[str, list[str]], headers: Mapping[str, list[str]]
) -> tuple[str, ValueError | None]:
    """The session a request pins, and why it is not one, read as its
    workspace's name is: both places, or two values, are refused."""
    header = _names(headers.get(SESSION_HEADER))
    param = _names(params.get(SESSION_PARAM))
    if header and param:
        return "", ValueError(BOTH_SESSIONS)
    chosen = header or param
    if not chosen:
        return "", None
    if len(chosen) > 1:
        return "", ValueError(_two_names(chosen, "pins", "sessions"))
    try:
        return sessions.valid_session_name(chosen[0]), None
    except ValueError as exc:
        return "", exc


def _old_headers(headers: Mapping[str, list[str]]) -> str | None:
    """The one refusal naming every old header a request carries, in the table's
    order, or None. A blank value is absent."""
    found = [
        RENAMED.format(old=old, new=new)
        for old, new in OLD_HEADERS.items()
        if _names(headers.get(old.lower()))
    ]
    return "; ".join(found) or None


def _names(values) -> list[str]:
    """The distinct names a request gives in one place, blanks dropped.

    Surrounding whitespace is not part of a name, and a blank one is no name.
    """
    return list(dict.fromkeys(str(v).strip() for v in values or () if str(v).strip()))


def _last(values: Mapping[str, list[str]], wanted) -> dict[str, str]:
    """The last value of each name in ``wanted``: a repeated client setting is
    the later one."""
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

    The store maps a session's key, ``<workspace>/<name>``, to its record,
    which carries the Grid session id and the settings a reopen has to replay;
    and the workspace's name, which is also the flow library's directory, to
    the workspace's record: its list of sessions and its history, which holds
    each session's page (`workspace.store`). Everything that acts on a browser
    takes the key `session_of` resolves.
    """

    def __init__(
        self,
        actions: Actions,
        store: WorkspaceStore | None = None,
        defaults: dict | None = None,
        recordings=None,
        monitor=None,
        capture=None,
        channels=None,
        control=None,
        hints: Callable[[str, str], list[dict]] | None = None,
        catalogue=None,
        configs: Configs | None = None,
        tags: Callable[[str], dict[str, int]] | None = None,
        unnamed: str = sessions.DEFAULT,
    ):
        self.actions = actions
        self.store = store if store is not None else MemoryStore()
        # The subject a moved record with no owner is held by (WS-90): the
        # one whose unnamed calls reached it, the token's over HTTP with auth
        # on, else no principal's (`principal.subject_of`).
        self.unnamed = unnamed
        # The operator's floor for a new session (`workspace/settings.py`).
        self.defaults = dict(defaults or {})
        # Who files a recorded browser's video (`recordings.Collector`), or None
        # when recording is off. Told on open; it owns no browser.
        self.recordings = recordings
        # Who announces each session opened and ended (`monitor.Monitor`), or
        # None, which announces nothing: a unit test's manager.
        self.monitor = monitor
        if monitor is not None:
            # A ping the Grid answered is activity the reap clock counts.
            monitor.answered = self.pinged
        # What the held connections' events are kept in (`capture.Capture`), or
        # None when capture is not set up; and how a BiDi channel to a session's
        # browser is opened (`bidi.Channels`).
        self.capture = capture
        self.channels = channels
        # Who says a person holds a browser in the live view
        # (`live.control.Controls`, duck-typed: ``refusal(session_id, tool)``),
        # or None, which refuses nothing: a unit test's manager.
        self.control = control
        # Says every call (`workspace.calls`), and refuses one a person's
        # control forbids.
        self.calls = Calls(monitor=monitor, capture=capture, control=control)
        # Whether a call's session is not at the head of its list, for its span.
        self.calls.is_head = self.is_head
        # Which saved flows say they apply to a page, by workspace and URL
        # (`FlowStore.flows_here`), or None with no flow store: then nothing
        # is hinted. Asked only by `_hint`, never inside a store write.
        self.hints = hints
        # Each workspace's own config (`workspace.config`): none without one.
        self.configs = configs if configs is not None else Configs(None)
        # The tags in use in a workspace's flows (`FlowStore.tags_in_use`), or
        # None with no flow store.
        self.tags = tags
        # Where each workspace's browser is, and the secrets in scope there.
        # ``catalogue`` is the secrets catalogue, duck-typed (`forms_for`,
        # `leashed`, `revision`), or None with secrets off: then every answer is
        # left unscanned. The scrubber is the first to hear an origin change.
        # Both forget a browser after the workspace's TTL (the default for a
        # store without `ttl`).
        ttl = getattr(self.store, "ttl", DEFAULT_TTL_SECONDS)
        self.origins = Origins(ttl=ttl)
        self.scrubber = Scrubber(catalogue, capture, origins=self.origins, ttl=ttl)
        self.origins.listen(self.scrubber.changed)

    def on_origin_changed(self, fn: Callable[[OriginChanged], object]) -> None:
        """Call ``fn(event)`` whenever a workspace's browser reaches a new origin
        (`origins.Origins.listen`): outside any store write, after the scrubber."""
        self.origins.listen(fn)

    def browser_ended(self, workspace: str, session_id: str) -> None:
        """Forget ``session_id``'s origin and the secret forms it held: it ended,
        however it ended (`end_browser`, and `session.ended` from the bus)."""
        self.origins.forget(workspace, session_id)
        self.scrubber.ended(workspace, session_id)

    def capture_heard(
        self,
        workspace: str,
        session_id: str,
        url: str | None,
        frame: bool,
        done: Callable[[Forms | None], object],
    ) -> None:
        """Capture heard ``session_id`` start a navigation to ``url``: note it,
        and hand ``done`` the forms of the secrets in scope there once they are
        read, or None when they cannot be. With no URL the origin is unknown,
        and ``done`` gets every leashed secret. Read off the caller's thread
        either way, so it is safe on the loop."""
        origin = self.origins.arrived(workspace, session_id, url, frame=frame)
        if origin is None:
            self.scrubber.every(done)
        else:
            self.scrubber.build(workspace, session_id, origin, done)

    @property
    def kind(self) -> str:
        """Which backend is in play, for /ready, /info and the config log line."""
        return getattr(self.store, "kind", "memory")

    def describe(self, caller: Caller, at: str = "") -> dict:
        """What this workspace is, and whether it currently holds a browser.

        Never opens a browser: it peeks at the store rather than going through
        ``resolve``. The Grid's session id is not in it. Scrubbed as a resource
        is, so `workspace://current` and its REST route answer alike. ``at``
        is the ``session://<name>`` it was read at, when it was.
        """
        key = self.reading(caller)
        return self.scrubber.resource(key, self._described(caller, key, at))

    def describe_part(self, caller: Caller, part: str) -> dict:
        """One key of `describe`, as ``{workspace, <part>: value}``: null when
        this workspace's context leaves it out. A part that is no key of the
        context is a ValueError naming them."""
        if part not in CONTEXT_KEYS:
            raise ValueError(
                f"no part {part!r} in workspace://current; the parts are "
                + ", ".join(CONTEXT_KEYS)
            )
        whole = self.describe(caller)
        return {"workspace": whole["workspace"], part: whole.get(part)}

    def _described(self, caller: Caller, key: str, at: str = "") -> dict:
        """`describe`, before the scrub: the context of the session ``key``
        (`reading`), its workspace's config and tags beside it."""
        workspace, session = sessions.split(key)
        name = key
        status = {
            "workspace": workspace,
            "session": session,
            "main": session == self.head_of(workspace),
            "sessions": self.listed_sessions(
                workspace, own=session, subject=subject_of(caller.principal)
            ),
            "named_by": caller.named_by,
            "principal": caller.principal.status() if caller.principal else None,
            "browser": None,
            "url": None,
            "live": False,
            "recording": False,
            "capture": False,
            "capturing": False,
            "in_frame": None,
            "window": None,
            "tabs": [],
            "grid_timeout": None,
            "version": None,
            "store": self.kind,
            "settings": {},
            "tags": {},
            "guidance": guidance.pointer("WORKSPACES.md"),
        }
        if self.configs.enabled:
            status["config"] = self.configs.rows(self.configs.load(workspace))
            if url := self.configs.url(workspace):
                status["config_url"] = url
        if self.tags is not None:
            try:
                status["tags"] = self.tags(workspace)
            except Exception as exc:  # noqa: BLE001 - status must never fail
                log.warning(
                    "tags for workspace %s could not be read: %s",
                    sessions.logged(workspace), type(exc).__name__,
                )
        record = self.store.get(name)
        if record is None:
            return status

        status["url"] = self._page(name) or None
        # From the record, like `url`: a page that moved by itself since the
        # last call is not reflected.
        self._hint(status, name, status["url"])
        status["settings"] = dict(record.settings or {})
        # At the top level too: "which browser am I driving" is what this
        # resource answers. A record without one is on the default browser.
        status["browser"] = _kind(status["settings"])
        # The window too: an agent needs it to judge whether something is
        # off-screen, without a screenshot.
        status["window"] = record.window
        # The idle timeout the Grid gave the last session: kept when it has
        # ended, like the window, because it is what the next one will get.
        status["grid_timeout"] = record.grid_timeout
        # The version as it ran, beside `browser`; `settings` holds the one
        # asked for. Kept when the session ends, like the idle timeout.
        status["version"] = (record.ran or {}).get("version")
        saved = site_data_module.summary(
            record.site_data,
            f"{at}/site-data" if at else site_data_module.LIST_URI,
        )
        if saved is not None:
            status["site_data"] = saved
        # A record with no browser is an ordinary state: the Grid reaped it or
        # an admin ended it.
        status["live"] = (
            self.actions.grid.is_alive(record.session_id) if record.attached else False
        )
        status["recording"] = bool(status["live"] and status["settings"].get("record"))
        status["capture"] = bool(status["settings"].get("capture"))
        status["capturing"] = bool(
            status["live"] and self._told("capturing", record.session_id)
        )
        if status["capture"]:
            counts = self.capture.counts(name) if self.capture is not None else None
            status["captured"] = counts or {
                "console": 0, "network": 0, "errors": 0, "failed_requests": 0,
            }
        if status["live"]:
            # One reconnect answers both.
            try:
                driver = self.actions.grid.reconnect(record.session_id)
                status["in_frame"] = in_frame(driver)
                size = driver.get_window_size()
                status["window"] = f"{size['width']}x{size['height']}"
            except Exception:  # noqa: BLE001 - status must never fail
                pass
            # Its own read: it never follows a popup, and a failure leaves it empty.
            try:
                status["tabs"] = self.actions.tabs_listed(record.session_id)
            except Exception as exc:  # noqa: BLE001 - status must never fail
                log.debug("the tabs could not be read: %s", type(exc).__name__)
        return status

    def captured(
        self,
        name: str,
        kind: Literal["console", "network", "request"],
        at: str = "",
        named: bool = False,
        **query,
    ) -> dict:
        """What this workspace's browsers logged and fetched, as one view.

        A workspace that does not capture answers with how to turn it on, which
        is an answer and not an error. One that does, and has seen
        nothing yet, answers with an empty view. ``capturing`` says whether the
        connection is held right now: false while it is being re-held, so a quiet
        buffer is not mistaken for a quiet page. A request read at ``at``
        says so as its ``uri``. Read by a session's name (``named``), the way
        to turn it on names that session.
        """
        record = self.store.get(name)
        on = bool(record and (record.settings or {}).get("capture"))
        if self.capture is None or not on:
            return dict(CAPTURE_NAMED_OFF if named else CAPTURE_STATE_OFF)
        if kind == "console":
            view = self.capture.console(name, **query)
            if view is None:
                view = capture_views.console(
                    Buffer(),
                    capture_views.check_level(query.get("level")),
                    capture_views.check_pages(query.get("pages")),
                )
        elif kind == "network":
            view = self.capture.network(name, **query)
            if view is None:
                view = capture_views.network(
                    Buffer(),
                    capture_views.check_filter(query.get("filter")),
                    capture_views.check_pages(query.get("pages")),
                )
        else:
            view = self.capture.request(name, **query)
            if view is None:
                raise ValueError(
                    f"no captured request {query.get('id')!r}: "
                    f"{NETWORK_LISTINGS}"
                )
            if at:
                view = {**view, "uri": at}
        capturing = bool(
            record.attached and self._told("capturing", record.session_id)
        )
        # Scrubbed as a resource is; the answer for capture off is this
        # server's own words and is not.
        return self.scrubber.resource(name, {**view, "capturing": capturing})

    @staticmethod
    def acting_in(key: str):
        """Run a block as acting in the session ``key``, for what the action
        layer asks back about it (`acting`): a flow's steps, which no `_said`
        wraps one by one."""
        return _acting_in(key)

    def own(self, caller: Caller, named: str | None, acted: str) -> str:
        """The key of ``caller``'s own session, for a call that named
        ``named`` and acts in ``acted``: what its pointers are read against
        (`sessions.pointer`). An unnamed call acts in its own. One whose own
        cannot be read, because its own name is refused or its own session is
        another subject's, has none: ``""``, which no session's key equals."""
        if not named:
            return acted
        try:
            return self.reading(caller)
        except (ValueError, faults.Held):
            return ""

    def session_of(
        self,
        caller: Caller,
        named: str | None = None,
        *,
        opening: bool = False,
        admin: bool = False,
    ) -> str:
        """The key of the session ``caller`` reaches (WS-82, WS-83): its pin,
        else the session ``named``, else its own (`sessions.own_name`).

        A session whose record another subject holds is refused with
        `faults.Held`, a 403, unless ``admin``, which only the admin's End and
        End all pass (WS-84). A named session with no record is refused
        with `faults.NoSession` and nothing is made, so a mistyped name opens
        nothing (WS-78), unless ``opening``, which `open_session` passes: then
        it is seated (`_seat`). The caller's own session with no record, named
        by its own name or not named at all, is its key, and nothing is made:
        what needs a browser is refused by `resolved` (WS-12).
        """
        workspace = caller.name
        chosen = self._chosen(caller, named)
        name = chosen or sessions.own_name(caller.principal)
        key = sessions.key_of(workspace, name)
        subject = subject_of(caller.principal)
        # Before any read of its sessions: a record an old pod wrote since
        # boot holds its default's browser inline.
        self.migrated(workspace)
        if not admin:
            self._holds(key, subject)
        if opening:
            return self._seat(workspace, name, subject)
        if chosen and chosen != self._own_name(caller):
            self._recorded_sub(key)
        return key

    def reading(self, caller: Caller, named: str | None = None) -> str:
        """The key of the session ``caller`` reads, as `session_of` resolves
        it but never writing: no record is made. Another subject's session is
        `faults.Held`, and a named session with no record
        `faults.NoSession`; the caller's own that does not exist yet, named by
        its own name or not, is the key it will be made under, which reads as
        a session with nothing in it (WS-43)."""
        workspace = caller.name
        chosen = self._chosen(caller, named)
        name = chosen or sessions.own_name(caller.principal)
        key = sessions.key_of(workspace, name)
        self.migrated(workspace)
        self._holds(key, subject_of(caller.principal))
        if chosen and chosen != self._own_name(caller):
            self._recorded_sub(key)
        return key

    @staticmethod
    def _chosen(caller: Caller, named: str | None) -> str:
        """The session ``caller`` picks by its pin or by ``named``, "" when it
        picks none; a name that differs from its pin is refused."""
        pin = caller.pinned
        name = sessions.valid_session_name(named) if named else ""
        if pin and name and name != pin:
            raise ValueError(PIN_DIFFERS)
        return pin or name

    @staticmethod
    def _own_name(caller: Caller) -> str:
        """``caller``'s own session's name (`sessions.own_name`), or "" when
        it has none a session can have."""
        try:
            return sessions.own_name(caller.principal)
        except ValueError:
            return ""

    def _recorded_sub(self, key: str) -> None:
        """Refuse the named session ``key`` with `faults.NoSession` when it
        has no record (WS-78). Not asked for the caller's own session named
        by its own name: that is its key, as an unnamed call's is."""
        if self.store.get(key) is not None:
            return
        if self.capture is not None:
            # Its record is gone, and its capture goes with it (CAP-81).
            self.capture.discard(key)
        raise faults.NoSession(NO_SUB.format(workspace=sessions.split(key)[0]))

    def migrated(self, workspace: str) -> Workspace | None:
        """``workspace``'s record, moved first when it is in the shape before
        WS-81 (WS-90); None when it has none.

        Four steps, each of which a second run finds done, and the one write
        that changes the shape last, so a move cut short anywhere is finished
        by the next read:

        1. Read the record. Absent or in the new shape, it is the answer.
        2. Make ``<workspace>/default`` from the inline fields, only when it
           is absent, held by the old ``owner``, else by `unnamed`. The old
           shape is headed by ``default``, so it gets the head's lifetime.
        3. Hold each old sub-session's record that no one holds by its name
           when its old ``label`` is set and differs from it (it was that
           subject's own). Any other is left held by none, for the next
           subject that opens it.
        4. In one compare-and-set, only while the record is still in the old
           shape, write the list ``default``, the old sub-sessions, then any
           other session with a record, and the same history.

        An old pod that names another sub-session between the steps sends
        the move round again, so every name listed was given its holder.
        """
        for _ in range(MOVE_TRIES):
            record = self.store.get(workspace)
            if record is None or record.old is None:
                return record
            stored, again = self._move(workspace, record.old)
            if not again:
                return stored
        raise StoreConflict(
            f"workspace {sessions.logged(workspace)} kept changing while it "
            f"was being moved; gave up after {MOVE_TRIES} tries"
        )

    def _move(self, workspace: str, old: Old) -> tuple[Workspace | None, bool]:
        """Steps 2 to 4 of `migrated` for the old record's ``old``: the record
        stored after, and whether its sub-sessions changed since, which wants
        the move again. Each step's function only computes: it may run more
        than once."""
        holder = old.owner or self.unnamed
        default = sessions.key_of(workspace, sessions.DEFAULT)
        subs = [name for name in old.subs if name != sessions.DEFAULT]

        def made(r: Session | None) -> tuple[Session | None, None]:
            if r is not None:
                return None, None
            return replace(old.session, holder=holder, label_was=None), None

        self.store.change(default, made, create=True)

        def held_by(subject: str) -> Callable[[Session], Session | None]:
            return lambda r: None if r.holder else replace(r, holder=subject)

        keys = [sessions.key_of(workspace, name) for name in subs]
        # All read before any is written: a holder is chosen by the old label,
        # which a write drops. A label that is empty or the name itself says
        # nothing of whose it was (an agent's `session=`, or a JWT labelled by
        # a subject that is its name), so it is left held by none, and the
        # next subject to open it adopts it (WS-84).
        for name, key, sub in zip(subs, keys, self.store.get_many(keys), strict=True):
            if sub is None or sub.holder or sub.label_was in (None, "", name):
                continue
            self.store.update(key, held_by(name))
        # Every other session with a record follows, in the order each was
        # opened: an old pod that wrote a moved record back in its own shape
        # named none of them.
        found = self.store.sessions_of(workspace)
        others = sorted(
            (n for n in found if n != sessions.DEFAULT and n not in subs),
            key=lambda n: (found[n].opened_at, n),
        )
        names = [sessions.DEFAULT, *subs, *others]

        def flipped(r: Workspace | None) -> tuple[Workspace | None, bool]:
            if r is None or r.old is None:
                return None, False
            if r.old.subs != old.subs:
                return None, True
            return Workspace(sessions=names, history=r.history), False

        # A listed sub-session whose record is gone is pruned by the first
        # listing (`listed`), as any is.
        return self.store.change(workspace, flipped)

    def change_shared(
        self, workspace: str, fn: Callable, *, create: bool = False
    ) -> tuple[Workspace | None, object]:
        """`store.change` of ``workspace``'s record: every write of one goes
        through here. A record in the shape before WS-81 is moved first
        (`migrated`), since writing one as it reads would drop its default's
        browser and its sub-sessions: a write may come with no resolution
        before it (a ping, an end, the admin's History), or after an old pod
        wrote the record again."""

        def run(r: Workspace | None):
            if r is not None and r.old is not None:
                return None, _OLD
            return fn(r)

        stored, note = self.store.change(workspace, run, create=create)
        if note is not _OLD:
            return stored, note
        self.migrated(workspace)
        stored, note = self.store.change(workspace, run, create=create)
        # Written old again since: nothing is written, and the next read moves it.
        return stored, None if note is _OLD else note

    def migrate_all(self) -> int:
        """Move every record in the shape before WS-81 (`migrated`), once at
        boot, and answer how many moved. A workspace that fails is logged by
        type and left for its first read; nothing here stops the boot."""
        records = getattr(self.store, "records", None)
        if records is None:
            return 0
        try:
            found = records()
        except Exception as exc:  # noqa: BLE001 - the boot goes on
            log.warning("the workspace records could not be listed to move: %s",
                        type(exc).__name__)
            return 0
        moved = 0
        for workspace, record in found.items():
            if record.old is None:
                continue
            try:
                self.migrated(workspace)
            except Exception as exc:  # noqa: BLE001 - its first read moves it
                log.warning(
                    "the record of workspace %s could not be moved: %s",
                    sessions.logged(workspace), type(exc).__name__,
                )
                continue
            moved += 1
        log.info("moved %d workspace records into their default session", moved)
        return moved

    def _holds(self, key: str, subject: str) -> None:
        """Refuse with `faults.Held` when the session ``key`` has a record
        held by a subject other than ``subject`` (WS-84). No record, and a
        record no one holds yet, pass."""
        record = self.store.get(key)
        if record is not None and record.holder not in ("", subject):
            raise faults.Held()

    def _seat(self, workspace: str, name: str, subject: str) -> str:
        """The key of ``workspace``'s session ``name``, seated for an
        `open_session` by ``subject``: listed on the workspace's record (made
        too if absent), and its record made held by ``subject`` if absent, or
        taken when no one holds it. Inside the record's compare-and-set, so
        of two subjects opening one new name, one holds it and the other is
        refused with `faults.Held` (WS-84). The first name listed heads the
        list (WS-81).
        """
        key = sessions.key_of(workspace, name)

        def listed(r: Workspace | None) -> tuple[Workspace | None, None]:
            if r is not None and name in r.sessions:
                return None, None
            return (r or Workspace()).listing(name), None

        def seat(r: Session | None) -> tuple[Session | None, str | None]:
            if r is None:
                return Session(holder=subject), "made"
            if not r.holder:
                # An old pod writing during a rollout leaves one, and so does
                # the move (`migrated`) for a sub-session it cannot place.
                return replace(r, holder=subject), None
            if r.holder == subject:
                return None, None
            return None, "held"

        # The name first, so a record whose write fails is never one no
        # listing shows: the name with no record is what a listing prunes
        # (`listed`). Listed again after, in case a listing pruned it in
        # between. Listed before its record is made, so its first write
        # already has its place's lifetime (WS-87).
        self.change_shared(workspace, listed, create=True)
        _, note = self.store.change(key, seat, create=True)
        if note == "held":
            raise faults.Held()
        if note == "made" and self.capture is not None:
            # What an expired record of this name captured is not this one's.
            self.capture.discard(key)
        self.change_shared(workspace, listed, create=True)
        return key

    def browser(self, name: str) -> str:
        """The Grid id this workspace is driving, or "" if it holds no live one.

        For callers that need the browser but must never open one, such as the
        file listing, which keeps answering after a browser is gone. A reaped
        browser keeps its id in the record until something refreshes it, so
        liveness is asked of the Grid.
        """
        return self._live(name)[0]

    def _live(self, name: str) -> tuple[str, str]:
        """`browser`, with its kind read off the same record; ("", "") with
        no live one."""
        record = self._session(name)
        if record is None or not record.attached:
            return "", ""
        if not self.actions.grid.is_alive(record.session_id):
            return "", ""
        return record.session_id, _kind(record.settings)

    def resolve(self, name: str) -> str:
        """The Grid session id ``name`` is driving, reopening a reaped browser.

        Never opens a first browser: ``open_session`` is the one place that
        happens, since it is where a session's settings are chosen.
        """
        return self.resolved(name)[0]

    def resolved(self, name: str) -> tuple[str, str]:
        """`resolve`, with the kind of browser it is (``chrome``, ``firefox``),
        read off the same record: what a call says it ran on."""
        record = self._session(name)
        if record is None or not record.attached:
            # Detached reads as absent: the caller is sent to open_session, the
            # one path that opens a browser.
            workspace, session = sessions.split(name)
            if session != sessions.DEFAULT:
                raise faults.NoSession(NO_BROWSER.format(workspace=workspace))
            raise faults.NoSession(
                f"no session is open in workspace {workspace!r} yet: call "
                "open_session first. It takes the window size and timeouts this "
                "session should use, which is why it is not done implicitly."
            )

        if self.actions.grid.is_alive(record.session_id):
            log.debug("browser recalled for workspace %s", sessions.logged(name))
            return record.session_id, _kind(record.settings)
        # A first page that failed may quote where it landed, and no browser
        # holds the slot yet.
        return self._scrubbing(name, lambda: self._reopen(name, record))

    def _reopen(self, name: str, record: Session) -> tuple[str, str]:
        """`resolved`'s reopen of a browser the Grid reaped, before its scrub:
        where the caller left off, with the same settings."""
        cause = self._cause(record, time.time())
        log.info(
            "the browser is gone from the grid (%s), reopening for workspace %s",
            cause, sessions.logged(name),
        )
        self._ended(name, record.session_id, "lost")
        saved = self._restorable(record)
        replay = dict(record.settings or {})
        if self.recordings is None:
            # Nobody would collect the video (recording was turned off since).
            replay.pop("record", None)
        if not self._can_capture():
            # Nobody would hear it (capture was turned off since).
            replay.pop("capture", None)
        # A remembered key an upgrade made owned is refused, saying how to
        # clear it: dropping it quietly would change the browser's shape.
        session_caps.check(replay.get("capabilities"), remembered=True)
        # The whole stored set is replayed, and each value keeps its source: a
        # config value stays the config's, so the next explicit open reads the
        # config afresh, and a refused version is blamed on whoever set it.
        sources = {
            k: record.set_by.get(k, settings_module.CALLER) for k in replay
        }
        page = self._page(name)
        opened = self._opened(
            name,
            replay,
            url=page or None,
            version_from=settings_module.version_from(sources),
            **(self._recorded(name, replay) if replay.get("record") else {}),
            **({"site_data": saved} if saved else {}),
            **replay,
        )
        # Nobody asked for this reopen, so the record holds a notice until
        # `touch` hands it to the first result. A reopen alongside this one may
        # bind first; then this acts on its browser.
        timeout = self._timeout_of(opened["session_id"])
        reopened_at = opened.get("url", page)
        kept = self.remember(
            name,
            opened["session_id"],
            self._landing(name, reopened_at),
            replay,
            set_by=sources,
            replacing=record.session_id,
            reopened={"cause": cause, "report": opened.get("site_data")},
            grid_timeout=timeout,
            window_seen=_measured(opened),
            sent=opened.get("sent"),
            ran=opened.get("ran"),
        )
        if kept == opened["session_id"]:
            # The same workspace on the same pages: the reaped browser's secret
            # forms carry over, and the new one starts unframed on the page it
            # reopened at.
            self.scrubber.carry(name, record.session_id, kept)
            self.origins.opened(name, kept)
            self.origins.arrived(name, kept, reopened_at or None)
            self._told("opened", name, kept, _kind(replay), timeout, reopened=True)
        # A browser this workspace does not hold keeps its provisional note.
        if replay.get("record") and kept == opened["session_id"]:
            try:
                self._expect(name, opened["session_id"], _kind(replay))
            except (OSError, ValueError) as exc:
                # The browser is open and remembered; failing the caller's
                # action over the filing would help nobody.
                log.warning(
                    "recording for %s cannot be filed: %s",
                    sessions.logged(name), faults.message(exc),
                )
        return kept, _kind(replay)

    def _cause(self, record: Session, now: float) -> str:
        """Why a browser is gone: ``reaped`` when the workspace sat idle for the
        node's timeout (the one read at the open; the default when it did not
        say), ``lost`` when it was in use, so something else ended it.

        Idleness is measured from the latest of the last call, the open and the
        last ping the Grid answered: each is activity to the node.
        """
        last = record.last_used
        window = record.grid_timeout or DEFAULT_TIMEOUT
        return "reaped" if now - last >= window else "lost"

    def pinged(self, name: str, session_id: str) -> None:
        """Record a keep-alive ping the Grid answered, on the record that still
        holds that session. Never a call: ``called_at`` stays. A ping is use
        (WS-88), so it slides the workspace's record too, and its head's with
        it (`_visit`)."""

        def mark(r: Session) -> tuple[Session | None, bool]:
            if r.session_id != session_id:
                return None, False
            return replace(r, pinged_at=time.time()), True

        if self.store.change(name, mark)[1]:
            self._visit(name)

    def _recorded(self, key: str, settings: dict) -> dict:
        """The arguments a recorded open adds: the video's name, and a note
        for the collector the moment the browser exists, marked ``discard``.
        Both are the workspace's, whichever of its sessions ``key`` is: its
        recordings are among its files.

        The caller notes it again without the mark once the workspace holds the
        browser. Until then its video is no workspace's, and stays so when the
        open fails after the Grid made the browser, or a concurrent open binds
        first and this one is quit."""
        name = sessions.split(key)[0]

        def created(grid_id: str) -> None:
            try:
                self._expect(key, grid_id, _kind(settings), discard=True)
            except (OSError, ValueError) as exc:
                # The type alone: the note's path names the Grid id. Never the
                # open's failure: the browser is made, and the open goes on.
                log.warning(
                    "a recording for %s cannot be noted: %s",
                    sessions.logged(name), type(exc).__name__,
                )

        return {"video_name": name, "on_created": created}

    def _expect(self, key: str, grid_id: str, browser: str, **kwargs) -> None:
        """`recordings.expect` for a browser of the session ``key``: filed with
        its workspace, and known to the monitor by the session's key, as
        every other announcement of it is."""
        self.recordings.expect(
            sessions.split(key)[0], grid_id, browser, session=key, **kwargs
        )

    def _timeout_of(self, session_id: str) -> int | None:
        """The idle timeout the Grid gives this session, or None when it will
        not say. Never fails the open that asks."""
        try:
            return self.actions.grid.session_timeout(session_id)
        except Exception as exc:  # noqa: BLE001 - a fact to show, not to need
            log.info("the Grid's idle timeout is unknown: %s", type(exc).__name__)
            return None

    def _can_capture(self) -> bool:
        return self.monitor is not None and self.capture is not None

    def _opened(self, name: str, settings: dict, **kwargs) -> dict:
        """``actions.open_session``, attaching capture first when ``settings``
        ask for it.

        A failed attach, or a monitor that was never started, fails the open as
        a 503. Whatever fails once the browser exists (the attach, or the first
        page timing out after it), the open quits the browser and releases the
        capture hold, including one that completed just as the attach gave up.
        The session is announced ended only once its quit is done
        (``on_quit``); after a quit that failed it may still run."""
        if not settings.get("capture"):
            return self.actions.open_session(**kwargs)
        browser = _kind(settings)
        tried: list[str] = []

        def ready(session_id: str) -> None:
            tried.append(session_id)
            try:
                self.monitor.attach(name, session_id, "capture", browser=browser)
            except Exception as exc:
                if isinstance(exc, faults.BidiUnavailable):
                    raise
                log.info(
                    "capture did not attach for %s: %s",
                    sessions.logged(name), type(exc).__name__,
                )
                log.debug("%s", faults.Traceback(exc))
                raise faults.BidiUnavailable(ATTACH_FAILED) from exc

        try:
            return self.actions.open_session(
                on_ready=ready,
                on_quit=lambda session_id: self._ended(name, session_id, "ended"),
                **kwargs,
            )
        except Exception:
            for session_id in tried:
                self._told("release", session_id, "capture")
            raise

    def _told(self, what: str, *args, **kwargs):
        """Tell the monitor ``what``, when there is one; never fails (`told`)."""
        return told(self.monitor, what, *args, **kwargs)

    def _ended(self, name: str, session_id: str, cause: str) -> None:
        """Announce that ``session_id`` ended, when there is a monitor."""
        self._told("ended", name, session_id, cause)

    def _let_go(self, session_id: str) -> None:
        """Stop keeping alive a browser whose quit failed: pinged, a browser left
        running would never time out."""
        self._told("release", session_id, KEEPALIVE)

    def _refuse(self, key: str, tool: str) -> None:
        """Raise the refusal for ``tool`` while a person holds the browser of
        the session ``key``: for `open_session` and `end_browser`, which take
        no turn. Nothing to refuse with no browser open (no Grid id, no gate)."""
        if self.control is None:
            return
        record = self._session(key)
        if record is None or not record.attached:
            return
        self.calls.refuse(record.session_id, tool)

    def _said(
        self,
        name: str,
        session_id: str,
        kind: str,
        call,
        *,
        tool: str,
        surface: str,
        own: str | None = None,
    ) -> Said:
        """Run ``call(session_id)`` as one said call: the result, scrubbed, the
        page hint (or None), and whether the result's URL was withheld.
        ``name`` is the session's key: its origin, frame and secret forms are
        that session's, and the turn is its browser's. ``own`` is the
        caller's own session (`own`), which the page hint points from.

        The bracket every host of a call shares. The call holds the session's
        turn from before it begins to after it ends (the lock is reentrant, so
        `Recipe.run` inside takes it again for free). With its turn, it is
        refused if a person holds the browser, so a call that queued before
        they asked is refused when its turn comes, never begun. Reads take
        their turn too.

        The tabs are followed at both ends (`followed`). The check before
        comes first, so a popup that opened since the last call is noted, and
        the call is scrubbed with the origin it acts on; when it fails, the
        call never begins. The check after is merged into the result
        (`core.tabs.merged`), and never fails a call that acted; a call that
        failed because its tab closed is `faults.TabClosed`.

        Still inside the turn, the answer goes through the scrubber: the
        result's URL and ``in_frame`` are noted first, then the result is
        scrubbed with the secrets in scope on the origins before and after the
        call, or an error with those before and every leashed secret. A scrub
        that fails raises `faults.ScrubFailed` instead of the answer.
        """
        with locks.driving(session_id):
            failure = None
            try:
                moved = self.followed(name, session_id)
            except Exception as exc:  # noqa: BLE001 - re-raised below, scrubbed
                failure = exc
            if failure is not None:
                # Outside the `except`, as below. Its page is unknown.
                self.scrubber.error(name, session_id, failure, [None])
                raise failure
            before = self.origins.current(name, session_id)
            begun = None
            try:
                # A refusal here (a person in control) begins nothing.
                begun = self.calls.begin(
                    name, session_id, browser=kind, tool=tool, own=own
                )
                with _acting_in(name):
                    result = call(session_id)
            except Exception as exc:  # noqa: BLE001 - re-raised below, scrubbed
                if begun is not None:
                    self.calls.end(
                        begun, tool=tool, surface=surface, browser=kind, error=exc
                    )
                failure = exc
            page = None
            after: dict = {}
            if begun is not None:
                if failure is None:
                    page = self.calls.end(
                        begun, tool=tool, surface=surface, browser=kind
                    )
                try:
                    after = self.followed(name, session_id, failure, after=True)
                except faults.TabClosed as closed:
                    failure = closed
            if failure is not None:
                # A move the checks kept is said, or no call would say it.
                faults.note_moved(failure, moved, after, ran=begun is not None)
                # Outside the `except`: a `ScrubFailed` raised there would carry
                # the original, value and all, as its context.
                self.scrubber.error(name, session_id, failure, [before, None])
                raise failure
            result = tabs_module.merged(result, moved, after)
            result, hidden = self._scrubbed(name, session_id, result, before, tool)
        return Said(result, page, hidden)

    def followed(
        self,
        name: str,
        session_id: str,
        failure: BaseException | None = None,
        *,
        after: bool = False,
    ) -> dict:
        """What checking the tabs found (`Actions.check_tabs`), with a move
        noted: the session is at the top of the new tab's page, so its frame is
        cleared and its URL's origin is where secret scope is read.

        ``failure`` is how the call failed, when it did: one whose window was
        gone, while the check moved the session, raises `faults.TabClosed`
        naming the tab it is on now, with the failure's notes. ``after`` is the
        check after a call, which never fails it: a check that fails is logged
        by type and finds nothing, so the call's own result or failure stands.
        It is not made at all once the browser is ending (`locks.driving`):
        its Grid DELETE may be in flight.
        """
        if after and cancel.cancelled():
            return {}
        try:
            change = self.actions.check_tabs(session_id)
        except Exception as exc:
            if not after:
                raise
            # By type: a driver's text quotes the page's URL.
            log.info(
                "the tabs after a call for %s could not be checked: %s",
                sessions.logged(name), type(exc).__name__,
            )
            return {}
        if "tab" in change:
            self.origins.framed(name, session_id, False)
            self.origins.arrived(name, session_id, change.get("url"))
            if failure is not None and faults.window_gone(failure):
                closed = faults.TabClosed(change["tab"], change.get("url"))
                for note in getattr(failure, "__notes__", ()):
                    closed.add_note(note)
                raise closed from None
        return change

    def _arrived(self, name: str, session_id: str, result) -> str | None:
        """Note where ``result`` says the browser is: its URL's origin, or None
        when it has no URL (unknown); and a `frame` result's ``in_frame``."""
        if not isinstance(result, dict):
            return None
        framed = result.get("in_frame")
        if isinstance(framed, bool):
            self.origins.framed(name, session_id, framed)
        if "url" not in result:
            return None
        return self.origins.arrived(name, session_id, result["url"])

    def scrubbed(self, name: str, call: Callable[[], object]):
        """``call()``'s answer, or its failure, checked for secrets as a resource
        read is: for what is not a call on the browser but may still carry a
        page's words (a site-data snapshot, a file listing, a flow saved, a file
        kept). Every leashed secret, and what the workspace holds; ``name`` may
        be "" (none named).

        Blocking. A failure is scrubbed in place and re-raised with its own
        type; one that cannot be scrubbed is `faults.ScrubFailed`.
        """
        return self.scrubber.resource(name, self._scrubbing(name, call))

    def _scrubbing(self, name: str, call: Callable[[], object]):
        """``call()``, or its failure scrubbed with every leashed secret and
        what the workspace holds, re-raised with its own type."""
        failure = None
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - re-raised below, scrubbed
            failure = exc
        # Outside the `except`: see `_said`.
        self.scrubber.error(name, "", failure, [None])
        raise failure

    def in_scope(
        self,
        name: str,
        session_id: str,
        result,
        before: str | None,
        tool: str = "",
    ) -> Forms:
        """What a flow step of ``tool`` on ``session_id`` that began on
        ``before`` is scrubbed with: ``result`` is noted as a call's is (its
        URL, its ``in_frame``), then the secrets in scope on the origins before
        and after it are read. ``result`` is None for a step that raised, whose
        page is unknown: every leashed secret.

        Blocking, for the run's worker thread. Raises `faults.ScrubFailed`.
        """
        after = None if result is None else self._arrived(name, session_id, result)
        origins = [before, after]
        opened = isinstance(result, dict) and "opened" in result
        if tool in ("network_body", "tab") or opened:
            # As `_scrubbed` reads them: a body's origin, or the pages of tabs
            # the step was not on.
            origins.append(None)
        return self.scrubber.forms(name, session_id, origins)

    def _scrubbed(
        self, name: str, session_id: str, result, before: str | None, tool: str = ""
    ) -> tuple[object, bool]:
        """``result`` noted and scrubbed for a call that began on ``before``;
        and whether its URL carried a value, so must not be recorded.

        What is not text a page spelt (`_NOT_TEXT`, a screenshot's pixels) is
        set aside first and put back after. Then the tool's cut
        (`actions.CUTS`), secrets off too: a text is scrubbed whole and cut
        after, so a value the cut would split is masked first.
        """
        cut = CUTS.get(tool, lambda as_it_is: as_it_is)
        after = self._arrived(name, session_id, result)
        if not self.scrubber.active:  # secrets off: the very object, unscanned
            return cut(result), False
        aside = {}
        if isinstance(result, dict):
            for key in _NOT_TEXT + (_PIXELS if tool == "screenshot" else ()):
                if key in result:
                    aside[key] = result.pop(key)
        origins = [before, after]
        opened = isinstance(result, dict) and "opened" in result
        if tool in ("network_body", "tab") or opened:
            # A body comes from the request's origin, which the browser may
            # never have been on, and a tab list or ``opened`` carries the
            # pages of tabs the call was not on: unknown, so every leashed
            # secret.
            origins.append(None)
        scrubbed, hidden = self.scrubber.result(name, session_id, result, origins)
        if aside:
            scrubbed.update(aside)
        return cut(scrubbed), hidden

    def act(
        self,
        caller: Caller,
        call,
        *,
        session: str | None = None,
        reshapes: bool = False,
        tool: str = "",
        surface: str = "mcp",
        closed: Callable[[str], None] | None = None,
    ) -> dict:
        """Resolve the caller's session's browser, act on it, remember where it
        ended up.

        The three steps every action shares, on both surfaces, so the surfaces
        cannot disagree about sliding a TTL or reopening a reaped browser.

        ``session`` is the call's own argument; which session it reaches is
        `session_of`'s answer (WS-82), and everything below is keyed by that
        session's key. A named session with no record, or a session with no
        browser, is `faults.NoSession`, and nothing is opened (WS-78).

        ``reshapes`` is for the one action that changes a setting the record
        stores rather than just the page it is on. See :meth:`reshape`.

        The call takes the session's turn on the browser (`workspace.locks`)
        from its start to its end, and is refused there while a person holds
        the browser (`faults.Controlled`). Resolving and settling need no turn:
        every record write is a compare-and-set.

        Every call is said (`workspace.calls`): ``tool`` on ``surface``. With
        capture on, what the page reported during its turn comes back as
        ``page``, or, when it raised, as a sentence on its error.

        ``closed`` is asked first when no session is open, with the session's
        key: a call whose answer went with the browser raises its own refusal
        there (`network_body`'s 404), since opening one cannot bring it back.

        A pointer the result carries (``page.see``, ``uri``, ``site_data.uri``)
        names the session it acted in, ``session://<name>/…``, when the call
        named one that is not the caller's own (`sessions.pointer`).
        """
        name = self.session_of(caller, session)
        # What its pointers are read against: they name the session it acted
        # in when that is not the caller's own. Read before the browser is
        # touched, so a store that fails here has reopened nothing.
        own = self.own(caller, session, name)
        try:
            resolved, kind = self.resolved(name)
        except faults.NoSession:
            if closed is not None:
                closed(name)
            raise
        try:
            result, page, hidden = self._said(
                name, resolved, kind, call, tool=tool, surface=surface, own=own
            )
            # A URL the scrub changed carried a value: never recorded.
            withheld = {"url": None} if hidden else {}
            self.settle(
                name, result, browser=resolved, reshapes=reshapes, own=own,
                **withheld,
            )
            if page is not None and isinstance(result, dict):
                result["page"] = page
        except Exception as exc:
            # `_said` scrubbed its failure before raising it; the note `failed`
            # adds is this server's fixed words and a count, never a page's.
            self.failed(name, exc, browser=resolved)
            raise
        return result

    def paste(self, name: str, text: str) -> dict:
        """Type ``text`` at the element a person focused in this workspace's
        browser: ``{"typed": n}``.

        Never reopens a browser: with none open it is a 404. It takes the
        session's turn, so a call in flight finishes first, and it is never
        refused for control: the person in control is the one pasting, and
        only the admin's paste route, after its own `controlling` check, calls
        this.

        The page is not recorded: a pasted newline can submit a form and land
        on a URL carrying the text, which is never kept.
        """
        session_id, kind = self._live(name)
        if not session_id:
            raise faults.NotFound("this workspace has no browser open")
        result = self._said(
            name,
            session_id,
            kind,
            lambda resolved: self.actions.paste(resolved, text),
            tool="paste",
            surface="http",
        ).result
        # A paste slides the TTL but is not the agent's call: a held notice
        # stays held for the next result that is.
        self.settle(name, result, browser=session_id, url=None, deliver=False)
        return {"typed": result["typed"]}

    def handed_back(
        self,
        name: str,
        episode: dict | None,
        *,
        surface: str = "mcp",
        own: str | None = None,
    ) -> dict:
        """`await_control`'s summary of a hand-back, or of nobody in control
        (``episode`` None): everything but its sentence, which
        `live.control.await_control` says.

        ``episode`` is plain values (`live.control.handed`): who and for how
        long, whether the browser ended under it, and the pages the person
        reached as heard. The page and its outline are read live, as one said
        call that answers under control, on the browser this workspace holds
        now, never reopening one: with none live, a hand-back says ``ended``
        and carries no page. A page that cannot be read is said in
        ``page_error``, never raised.

        It settles with the URL withheld: the TTL slides, History is not
        written. ``visited`` is shaped as History keeps a URL: the last
        page whole, credentials cut; earlier ones origin, path and hash route
        (never the route's query). ``own`` is the caller's own session: a
        restore report it carries names ``name`` when that is another (`act`).
        """
        summary: dict = {
            "handed_back": episode is not None,
            "ended": bool(episode and episode["ended"]),
            "control": dict(episode["control"]) if episode else None,
        }
        if episode is not None and episode["followed"]:
            pages = episode["pages"]
            summary["visited"] = [
                {
                    "url": (
                        without_userinfo(page["url"])
                        if at == len(pages) - 1
                        else page_of(page["url"])
                    ),
                    **({"title": page["title"]} if "title" in page else {}),
                }
                for at, page in enumerate(pages)
            ]
            summary["dropped"] = int(episode["dropped"])
        if "visited" in summary:
            # The pages a person reached may be on any origin: scrubbed as a
            # resource is, with every leashed secret.
            summary["visited"] = self.scrubber.resource(name, summary["visited"])
        session_id, kind = ("", "") if summary["ended"] else self._live(name)
        if not session_id:
            summary["ended"] = episode is not None
            return summary
        try:
            read = self._said(
                name,
                session_id,
                kind,
                self.actions.outline,
                tool="await_control",
                surface=surface,
            ).result
            # Said as `await_control`, cut as the outline it is, after its scrub.
            read = cut_names(read)
        except Exception as exc:  # noqa: BLE001 - the hand-back is said regardless
            # By type: a Grid failure's text quotes the Grid's URL.
            log.info(
                "the page after a hand-back could not be read for %s: %s",
                sessions.logged(name), type(exc).__name__,
            )
            summary["page_error"] = faults.message(exc)
            return summary
        summary["page"] = {"url": read.get("url"), "title": read.get("title")}
        # The bracket may have followed a popup the person opened: said, so the
        # agent knows which tab it is on now.
        for key in ("tab", "opened", "closed"):
            if key in read:
                summary[key] = read[key]
        summary["outline"] = {
            key: read[key] for key in ("count", "total", "elements") if key in read
        }
        settled: dict = {}
        try:
            self.settle(name, settled, browser=session_id, url=None, own=own)
        except Exception as exc:  # noqa: BLE001 - a TTL not slid is no reason
            # to lose a hand-back this answer has already said.
            log.warning(
                "a hand-back for %s did not settle: %s",
                sessions.logged(name), type(exc).__name__,
            )
        for key in ("reopened", "site_data"):
            if settled.get(key):
                summary[key] = settled[key]
        if settled.get("main_moved"):
            summary["main"] = False
            summary["main_moved"] = settled["main_moved"]
        return summary

    def settle(
        self,
        name: str,
        result,
        *,
        browser: str | None = None,
        reshapes: bool = False,
        touch: bool = True,
        url=FROM_RESULT,
        since: str | None = None,
        hint: bool = True,
        deliver: bool = True,
        own: str | None = None,
    ) -> None:
        """What a finished action means for the record, in place on ``result``.

        Every record write after an action comes through here: a single call
        (`act`), a flow step and the run's pages (`flows/api.run_for`), and a
        bound write (`secrets.perform_write`). So the capture is stripped and
        stored, the page recorded and a reopen's notice handed over in one
        place.

        ``url`` is the page to record: the result's own by default, ``None``
        to withhold it (the TTL still slides), or the pages a run reached, in
        order. ``touch=False`` is for a flow step: the runner records its pages
        before a save step and at the end, and so carries a reopen's notice on
        the run, not on a step.

        ``deliver=False`` writes the record and leaves a held notice held: a
        paste is not the agent's call, and a run's flush before a save step
        leaves it for the run's own result.

        ``browser`` is the one that produced ``result``: once the record names
        another, its page is not recorded and its save is not kept.

        A result that left the browser on a new page names the flows saved to
        apply there, as ``flows_here``. Whether the page is new is decided by
        the touch, inside the store's ``fn`` against the record as it is then;
        the flows are asked for outside it, because reading them is slow. So a
        withheld page never hints, nor does a step (``touch=False``) or a run's
        flush before a save (``hint=False``); ``since`` is the page a run began
        on, which a flush has moved the record from.

        ``own`` is the caller's own session: the result's pointers into this
        one's state are made to name it when they differ (`pointed`).
        """
        if not isinstance(result, dict):
            return
        # Popped before the store is asked anything, so a store that fails
        # cannot leave the capture, every value, in what the caller gets.
        captured = result.pop(site_data_module.CAPTURED, None)
        told = moved = None
        if touch:
            if url is FROM_RESULT:
                url = result.get("url")
            pages = url if isinstance(url, (list, tuple)) else (url,)
            told, page, moved = self._touch(
                name, *pages, browser=browser, since=since, deliver=deliver
            )
            if hint and page:
                self._hint(result, name, page, scrubbed=True)
        if told:
            # This server's own words, from a fixed table: nothing to scrub.
            result["reopened"] = {
                "cause": told["cause"],
                "message": faults.REOPENED[told["cause"]],
            }
        try:
            if told and told["report"]:
                # Words the browser said (a skipped site's reason): scrubbed as
                # a resource is, on the result the scrub has already been past.
                result["site_data"] = self.scrubber.resource(name, told["report"])
            if reshapes:
                self.reshape(name, result, browser=browser)
            self._save_site_data(name, result, captured, browser)
            self.pointed(name, own, result)
        except Exception:
            # The caller gets the error, not this result: the reopen's notice
            # goes back on the record for the next one, and so does a move's.
            if told:
                self._hold_again(name, told, browser)
            if moved:
                self._hold_moved_again(name, moved)
            raise
        if moved:
            # Said once the result is the caller's, so one put back is never
            # also said (WS-86).
            result["main"] = False
            result["main_moved"] = moved

    @staticmethod
    def pointed(name: str, own: str | None, result: dict) -> None:
        """``result``'s ``uri`` and ``site_data.uri``, pointers into the
        caller's own session, made to point into ``name`` when ``own`` is
        another (`sessions.repointed`). In place."""
        if own is None or own == name:
            return
        if "uri" in result:
            result["uri"] = sessions.repointed(result["uri"], name, own)
        report = result.get("site_data")
        if isinstance(report, dict) and "uri" in report:
            result["site_data"] = {
                **report, "uri": sessions.repointed(report["uri"], name, own)
            }

    def failed(self, name: str, exc: BaseException, *, browser: str) -> None:
        """Say the reopen's notice on ``exc``, once, and stamp the call's end.

        The hosts call it in an `except` and re-raise the original, so it never
        raises: a store failure is logged by type and the caller's own error is
        the one to see. A record holding another browser is only stamped.
        """

        def took(r: Session | None) -> tuple[Session | None, dict | None]:
            if r is None:
                return None, None
            if r.session_id != browser:
                return replace(r, called_at=time.time()), None
            notice = r.notice
            held = replace(r, called_at=time.time())
            return (held.delivered() if notice else held), notice

        try:
            notice = self.store.change(name, took)[1]
            # A failed call is use too, and no touch slides the workspace for it.
            self._visit(name)
            if notice:
                restored = len((notice.get("report") or {}).get("restored") or [])
                exc.add_note(faults.reopen_note(notice["cause"], restored))
        except Exception as problem:  # noqa: BLE001 - never masks the caller's error
            log.warning(
                "the reopen notice for %s was not said: %s",
                sessions.logged(name),
                type(problem).__name__,
            )

    def _hold_again(self, name: str, notice: dict, browser: str | None) -> None:
        """Put a reopen's notice back when the result carrying it failed."""

        def held(r: Session) -> Session | None:
            if r.reopened or (browser and r.session_id != browser):
                return None
            return replace(r, reopened={"browser": r.session_id, **notice})

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
        # The session's own sites, read off its workspace's history first.
        history = self._history(name)

        def save(r: Session) -> tuple[Session | None, dict]:
            if producer is not None and r.session_id != producer:
                # Another browser holds the workspace now, perhaps one opened
                # with restore_site_data=false: what this one captured is not
                # its to keep.
                return None, {
                    "cookies": 0, "sites": [],
                    "skipped": [{"reason": REPLACED_BEFORE_SAVE}],
                }
            if r.settings.get("insecure"):
                # It accepted any certificate, so what it holds may have been
                # set by anyone in the middle, and a later browser restores it.
                return None, {
                    "cookies": 0, "sites": [], "skipped": [{"reason": INSECURE_SKIP}],
                }
            data, receipt = site_data_module.snapshot(
                r.site_data, captured, history, time.time()
            )
            return r.with_site_data(data), receipt

        stored, receipt = self.store.change(name, save)
        if stored is None:
            # The record expired before `save` ever ran, or between its runs.
            return
        result["saved"] = receipt
        result["uri"] = site_data_module.LIST_URI

    @staticmethod
    def _restorable(record: Session) -> dict:
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
        *,
        session: str | None = None,
        surface: str = "mcp",
        **wanted,
    ) -> dict:
        """Open a browser in the caller's session, or pick up the one it was
        using. ``session`` names it, else the caller's own (WS-82); the
        session is seated here, the one place that may (WS-78), unless another
        subject holds it (WS-84).

        On a session whose live browser the caller's subject holds, it joins
        that browser (`_joinable`, `_joined`) instead of replacing it (WS-85);
        any other open is `_open_browser` (WS-13).

        Its answer is scrubbed (`_open_browser`), and so is its failure, with
        every leashed secret: a first page that failed may quote where it
        landed, and no browser holds the slot yet.

        It takes no turn, so the whole open is one `Calls.timed` call on
        ``surface``, a Grid wait and a setting it refuses included.
        """
        with self.calls.timed(
            _named_by(caller),
            tool="open_session",
            surface=surface,
            browser=_wanted_kind(wanted.get("browser")),
        ) as timing:
            name = self._chosen(caller, session) or sessions.own_name(
                caller.principal
            )
            # Checked before its record is made, so a refused open makes and
            # lists nothing (WS-78, WS-84). `_open_browser` checks the settings
            # again, against the record as it is then.
            planned = sessions.key_of(caller.name, name)
            timing.workspace = planned
            self._holds(planned, subject_of(caller.principal))
            _, resolved = self._scrubbing(
                planned, lambda: self._resolve_open(caller, planned, url, wanted)
            )
            key = self.session_of(caller, session, opening=True)
            timing.workspace = key
            # Read before the open, as `act` reads it.
            own = self.own(caller, session, key)
            record = self._scrubbing(
                key, lambda: self._joinable(caller, key, fresh, resolved)
            )
            if record is not None:
                timing.browser = _kind(record.settings)
                told = self._scrubbing(
                    key,
                    lambda: self._joined(
                        caller, key, record, url, surface=surface, own=own
                    ),
                )
            else:
                told = self._scrubbing(
                    key,
                    lambda: self._open_browser(
                        caller, key, url, fresh, restore_site_data, timing=timing,
                        **wanted,
                    ),
                )
            # Its restore report names the session it opened (`act`).
            self.pointed(key, own, told)
            self._tell_moved(key, told)
            return told

    def _open_browser(
        self,
        caller: Caller,
        key: str,
        url: str | None,
        fresh,
        restore_site_data,
        *,
        timing: Timing | None = None,
        **wanted,
    ) -> dict:
        """`open_browser`, before its failure's scrub. ``timing`` is the open's
        `Calls.timed` call, told the kind of browser once it is resolved.

        The only place a browser is created and its settings chosen. Shared by
        both surfaces for the same reason :meth:`act` is.

        It opens with the cascade in `workspace.settings`: the config and the
        client's defaults as they are now, then what this workspace's caller
        passed before, then the arguments. Only the caller's values are carried
        over, each stored with its source.

        It takes no turn on the browser (`workspace.locks`): it makes one rather
        than driving it, and the one it replaces goes through `end_browser`. So
        a person's control of that one is checked directly, before anything
        else.

        Everything is the session ``key``'s: the settings it inherits, the
        browser it replaces and the record it binds. Another session's browser
        is never ended, nor its settings read (WS-51, WS-59).
        """
        name = key
        self._refuse(key, "open_session")
        previous, resolved = self._resolve_open(caller, key, url, wanted)
        settings = resolved.settings
        if timing is not None:
            timing.browser = _kind(settings)
        # A session holds one browser: a second opened without ending the
        # first would hold a Grid slot, referenced by nothing, until the idle
        # timeout.
        ended = self._end_browser(key)
        # `fresh` drops only the remembered page. The settings still come
        # through the cascade (coming back as Chrome when the workspace was
        # using Firefox is no fresh start), and an explicit `url` stands.
        inherited = None if as_bool(fresh) else (previous.get("url") or None)
        record = self.store.get(name)
        saved = self._restorable(record) if record else {}
        forgotten = None
        if saved and not as_bool(restore_site_data, True):
            # Declining a restore is also how saved data is thrown away, but
            # only once the clean browser is open, so a failed open cannot
            # erase a sign-in.
            forgotten = (site_data_module.summary(saved) or {"sites": 0})["sites"]
            saved = {}
        opened = self._opened(
            name,
            settings,
            url=url or inherited,
            version_from=resolved.version_from,
            **(self._recorded(name, settings) if settings.get("record") else {}),
            **({"site_data": saved} if saved else {}),
            **settings,
        )
        timeout = self._timeout_of(opened["session_id"])
        # Asked just before the bind, so the head is as idle as it is now: a
        # head with a live browser is never moved (WS-86).
        idle = self._idle_head(key)
        kept = self.remember(
            name, opened["session_id"], self._landing(name, opened.get("url", "")),
            settings,
            idle_head=idle,
            set_by=resolved.set_by,
            replacing=ended, forget_site_data=forgotten is not None,
            opened_by=caller.principal.opener() if caller.principal else None,
            grid_timeout=timeout,
            window_seen=_measured(opened),
            sent=opened.get("sent"),
            ran=opened.get("ran"),
        )
        if kept != opened["session_id"]:
            # A concurrent open bound first and this browser was quit: describe
            # the one the workspace holds. This one's provisional note stays, so
            # its video is deleted, not filed. That open gives its browser the
            # slot itself; this answer is scrubbed as a resource is.
            return self._named(
                key,
                self.scrubber.resource(name, self._held(name, RACED_NOTE)),
                subject_of(caller.principal),
            )
        # This browser holds the workspace now: it starts afresh, unframed, on
        # the page it opened at.
        self.origins.opened(name, kept)
        self.scrubber.opened(name, kept)
        landed = self.origins.arrived(name, kept, opened.get("url"))
        if self.capture is not None and not settings.get("capture"):
            # An explicit open without capture is a fresh start for it: what an
            # earlier browser captured is not this one's.
            self.capture.discard(name)
        self._told("opened", name, kept, _kind(settings), timeout)
        noted = None
        if settings.get("record"):
            try:
                self._expect(key, opened["session_id"], _kind(settings))
            except (OSError, ValueError) as exc:
                # ValueError: `InvalidName` from a link on the note's path. The
                # browser is open and recording either way; only the filing
                # failed, so it is said in the result, never as a 400 and never
                # with the path.
                log.warning(
                    "recording for %s cannot be filed: %s",
                    sessions.logged(name), faults.message(exc),
                )
                noted = f"this recording cannot be filed: {type(exc).__name__}"
        if forgotten is not None:
            opened["site_data"] = {"forgotten": forgotten}
        # The Grid's id is how the browser is reached and not part of what a
        # caller is told; nor are what was sent and what ran, which are the
        # admin's.
        told = {k: v for k, v in opened.items() if k not in OPEN_PRIVATE}
        told["recording"] = bool(settings.get("record"))
        told["capture"] = bool(settings.get("capture"))
        if noted:
            told["recording_error"] = noted
        # What it says is checked for the secrets in scope where it opened.
        # Its URL was asked about before it was recorded (`_landing`).
        told, _ = self.scrubber.result(name, kept, told, [landed])
        # The page it opened on, new or remembered, is new to this caller in
        # this browser.
        self._hint(told, name, told.get("url"), scrubbed=True)
        return self._named(key, told, subject_of(caller.principal))

    def _joinable(
        self,
        caller: Caller,
        key: str,
        fresh,
        resolved: settings_module.Sourced,
    ) -> Session | None:
        """The record of the session ``key`` an open joins, or None when it
        replaces or opens one (WS-85): the open does not pass ``fresh``, the
        record holds a browser that ``caller``'s subject holds, no setting the
        open passed resolves to a value other than the live browser's
        (`settings.changed`), and the Grid finds that browser live. Asked in
        that order, so the Grid is asked only when everything else joins."""
        if as_bool(fresh):
            return None
        record = self._session(key)
        if record is None or not record.attached:
            return None
        if record.holder != subject_of(caller.principal):
            return None
        if settings_module.changed(resolved, record.settings or {}):
            return None
        if not self.actions.grid.is_alive(record.session_id):
            return None
        return record

    def _joined(
        self,
        caller: Caller,
        key: str,
        record: Session,
        url: str | None,
        *,
        surface: str,
        own: str | None = None,
    ) -> dict:
        """`open_session`'s join of ``record``'s live browser, before its
        failure's scrub (WS-85). It ends, opens and restores nothing, and takes
        no person's control into account, except for ``url``: that navigates,
        as one said call (`_said`), refused while a person holds the browser.
        Either way the record's ``called_at`` slides and its held reopen
        notice stays held for the next action. ``own`` is the caller's own
        session, which the navigate's pointers are read against (`act`).

        It answers the session as `_held` describes it, with ``joined``; after
        a navigate, its ``url``, ``title`` and ``flows_here`` are the
        navigate's.
        """
        browser = record.session_id
        subject = subject_of(caller.principal)
        if not url:
            self._touch(key, browser=browser, deliver=False)
            held = self.scrubber.resource(key, self._held(key))
            return self._named(key, {**held, "joined": True}, subject)
        navigated, page, hidden = self._said(
            key,
            browser,
            _kind(record.settings),
            lambda s: self.actions.navigate(s, url=url),
            tool="navigate",
            surface=surface,
            own=own,
        )
        # A URL the scrub changed carried a value: never recorded (`act`).
        withheld = {"url": None} if hidden else {}
        self.settle(
            key, navigated, browser=browser, deliver=False, own=own, **withheld
        )
        if page is not None:
            navigated["page"] = page
        held = self.scrubber.resource(key, self._held(key))
        # The flows are the navigate's page's, or none: never the page before.
        held.pop("flows_here", None)
        return self._named(key, {**held, **navigated, "joined": True}, subject)

    def _resolve_open(
        self, caller: Caller, key: str, url: str | None, wanted: dict
    ) -> tuple[dict, settings_module.Sourced]:
        """What an open of the session ``key`` inherits (`context`) and the
        settings it resolves to, or the ValueError that refuses it. Computes
        and checks only: nothing is written and no browser is touched.
        """
        previous = self.context(key)
        sources = previous.get("set_by") or {}
        # Resolved before the held browser is ended, because this validates as
        # well as merges: a typo in `browser=` must not cost a good browser.
        inherited_settings = {
            k: v
            for k, v in (previous.get("settings") or {}).items()
            if k not in ("record", "capture")
            and sources.get(k) == settings_module.CALLER
        }
        # `sourced` drops a version when a later layer names another browser,
        # this call's included.
        resolved = settings_module.sourced(
            wanted,
            defaults=self.defaults,
            previous=inherited_settings,
            client=caller.defaults,
        )
        settings = resolved.settings
        if settings.get("record") and self.recordings is None:
            raise ValueError(RECORDING_OFF)
        if settings.get("record") and settings.get("headless"):
            coerce = settings_module.SETTINGS["headless"][2]
            passed = coerce(wanted.get("headless")) is not None
            raise ValueError(
                HEADLESS_RECORD if passed else HEADLESS_RECORD_REMEMBERED
            )
        if settings.get("capture") and not self._can_capture():
            raise ValueError(CAPTURE_OFF)
        # An owned key or a vendor block a merge cannot add to: a 400 before
        # the held browser is ended, like a bad `browser=`. One this call did
        # not pass is the workspace's, and the refusal says how to clear it.
        session_caps.check(
            settings.get("capabilities"),
            remembered=wanted.get("capabilities") in (None, ""),
        )
        # The caller's page, checked for the same reason. A remembered one is
        # where the browser already was, so it is not the caller's to check.
        if url:
            allowed_navigation(url)
        return previous, resolved

    def _named(self, key: str, answer: dict, subject: str) -> dict:
        """``answer`` as `open_session` gives it: its workspace, its session's
        name and whether that heads the list (``main``, WS-48), and
        ``others``, every other session in the workspace, when there are any,
        each ``yours`` when ``subject`` holds it (WS-89)."""
        workspace, name = sessions.split(key)
        named = {
            "workspace": workspace,
            "session": name,
            "main": name == self.head_of(workspace),
            **{k: v for k, v in answer.items() if k != "workspace"},
        }
        others = [
            s
            for s in self.listed_sessions(workspace, own=name, subject=subject)
            if s["name"] != name
        ]
        if others:
            # Their pages may be on any origin: scrubbed as a resource is.
            named["others"] = self.scrubber.resource(key, others)
        return named

    def listed_sessions(
        self, workspace: str, own: str, subject: str
    ) -> list[dict]:
        """Every session of ``workspace``, in list order, as ``{name, main,
        live, last_used, url, uri, yours}`` (WS-89): ``main`` says which heads
        the list, ``last_used`` is in epoch seconds or None before its first
        bind, and ``yours`` says whether ``subject`` holds it. One whose
        record expired is left out (`listed`). Which browsers live is one
        Grid listing (`_running`). A workspace with no session lists none.

        The session ``own`` (the reader's) has its page whole; every other
        has its origin, path and route only (`page_of`), as a pruned history
        entry does: a query or fragment carries OAuth codes and reset tokens,
        and the other session may be another subject's (WS-84)."""
        self.sweep_captures()
        shared, found = self._listed(workspace)
        if shared is None or not found:
            return []
        running = self._running([r for _, r in found])

        def live(r: Session) -> bool:
            return r.attached and (running is None or r.session_id in running)

        def page(name: str) -> str | None:
            # Every session's page is on the workspace's history (WS-54).
            url = shared.page(name)
            return (url if name == own else page_of(url)) or None

        return [
            {
                "name": name,
                "main": name == shared.head,
                "live": live(r),
                "last_used": int(r.last_used) or None,
                "url": page(name),
                "uri": f"session://{name}",
                "yours": r.holder == subject,
            }
            for name, r in found
        ]

    def listed(self, workspace: str) -> list[tuple[str, Session]]:
        """``workspace``'s sessions with a record, as ``(name, record)`` in
        list order (WS-81), read in one round trip; none when it has no
        record.

        A listed name whose record is gone (expired, or its write failed) is
        dropped from the list, inside its compare-and-set, so the list never
        outgrows what lives. Each is read again there: a record made since the
        first read keeps its name.
        """
        return self._listed(workspace)[1]

    def _listed(
        self, workspace: str
    ) -> tuple[Workspace | None, list[tuple[str, Session]]]:
        """`listed`, with the workspace's record as it is after the prune.
        Read through `migrated`: a record in the shape before WS-81 lists
        none of its sessions until it is moved, and the admin's History
        writes the record it lists (`http/admin/site_data.py`)."""
        shared = self.migrated(workspace)
        names = list(shared.sessions) if shared is not None else []
        if not names:
            return shared, []
        keys = [sessions.key_of(workspace, name) for name in names]
        read = list(zip(names, self.store.get_many(keys), strict=True))
        dead = [name for name, record in read if record is None]
        if dead:
            pruned = self.store.update(
                workspace, lambda r: self._pruned(workspace, r, dead)
            )
            shared = pruned if pruned is not None else shared
        return shared, [(name, record) for name, record in read if record is not None]

    def head_of(self, workspace: str) -> str | None:
        """The name at the head of ``workspace``'s list, or None when it has
        none (WS-81)."""
        shared = self.store.get(workspace)
        return shared.head if shared is not None else None

    def is_head(self, key: str) -> bool:
        """Whether the session ``key`` heads its workspace's list. A
        workspace's own key counts as its head."""
        workspace, name = sessions.split(key)
        return not name or self.head_of(workspace) == name

    def _session(self, key: str) -> Session | None:
        """The record of the session ``key``, or None: a workspace's own key
        holds no browser."""
        return self.store.get(key) if sessions.split(key)[1] else None

    def _pruned(
        self, workspace: str, record: Workspace, dead: list[str]
    ) -> Workspace | None:
        """``record`` without those of ``dead`` that still have no record, or
        None when there is nothing to drop."""
        listed = [name for name in dead if name in record.sessions]
        if not listed:
            return None
        again = self.store.get_many([sessions.key_of(workspace, n) for n in listed])
        gone = {n for n, sub in zip(listed, again, strict=True) if sub is None}
        if not gone:
            return None
        return record.without(gone)

    def _running(self, records: list[Session]) -> set[str] | None:
        """The ids the Grid is running, from one listing, when any of
        ``records`` holds a browser; None when it cannot be listed, which reads
        an attached browser as alive, as `Grid.is_alive` does.

        Never a command to a browser: one is activity that keeps it past its
        keep-alive, and one to a session mid-call would wait on it (WS-39).
        """
        if not any(r.attached for r in records):
            return set()
        try:
            return {s.get("session_id") for s in self.actions.grid.sessions()}
        except Exception as exc:  # noqa: BLE001 - a listing never fails a read
            log.debug("the grid could not be listed: %s", type(exc).__name__)
            return None

    def sweep_captures(self) -> None:
        """Drop the capture of every session whose record is gone: a
        session's capture lasts as its record (CAP-81)."""
        if self.capture is None:
            return
        for key in self.capture.kept():
            if self.store.get(key) is None:
                self.capture.discard(key)

    def forget(self, workspace: str) -> None:
        """Forget ``workspace``: every session's record and its capture."""
        self.store.forget_workspace(workspace)
        if self.capture is not None:
            for key in self.capture.kept():
                if sessions.split(key)[0] == workspace:
                    self.capture.discard(key)

    def _landing(self, name: str, url: str) -> str:
        """``url``, the page a new browser landed on, as the record may keep it:
        "" when it carries the value of a secret in scope there, or one the
        workspace holds, and when that cannot be checked. Asked before the
        bind, so nothing is written and taken back: the history keeps the entry
        it had for that origin."""
        try:
            carries = self.scrubber.carries(name, url)
        except faults.ScrubFailed:
            carries = True
        return "" if carries else url

    def _held(self, name: str, note: str | None = None) -> dict:
        """What ``open_session`` reports for the browser the record holds,
        with ``note`` when one is given."""
        record = self.store.get(name)
        settings = dict(record.settings or {}) if record else {}
        held = {
            "workspace": sessions.split(name)[0],
            "browser": settings.get("browser"),
            "url": self._page(name),
            "settings": settings,
            "recording": bool(settings.get("record")),
            "capture": bool(settings.get("capture")),
        }
        if note is not None:
            held["note"] = note
        for key in ("width", "height"):
            if key in settings:
                held[key] = settings[key]
        self._hint(held, name, held["url"] or None)
        return held

    def remember(
        self,
        name: str,
        session_id: str,
        url: str = "",
        settings: dict | None = None,
        replacing: str | None = None,
        forget_site_data: bool = False,
        reopened: dict | None = None,
        opened_by: dict | object | None = KEEP,
        grid_timeout: int | None = None,
        window_seen: str | None = None,
        sent: list | None = None,
        ran: dict | None = None,
        set_by: dict | None = None,
        idle_head: str | None = None,
    ) -> str:
        """Bind a browser to this workspace, and say which browser it holds.

        The settings are stored because a reopen has to use the same browser:
        swapping Firefox for Chrome, or a 1400x900 window for the node default,
        midway through a task would be a silent change of shape. A reopen
        replays them all; ``open_session`` with no arguments inherits only the
        caller's.

        ``replacing`` is the browser this open ended or found dead. A record
        naming any other one was bound by an open that ran alongside this one,
        and the first to bind wins: that browser may already be in its caller's
        hands. This one is quit instead, or it would hold a Grid slot referenced
        by nothing, and its caller is handed the browser that was kept.

        ``forget_site_data`` is a declined restore: the saved data is erased in
        the same write that binds, so an open that loses erases nothing from
        the browser that won.

        ``reopened`` is a silent reopen's cause and report: the record holds
        them for this browser until `touch` hands them to the first result. Any
        other bind drops whatever an earlier reopen still held.

        ``set_by`` says who set each setting, by key; one it does not name is
        the caller's.

        ``idle_head`` is the idle head an `open_session` found (`_idle_head`):
        a bind that wins moves this session to the head in front of it, in
        the same write that lists it, while it is still the head (WS-86).
        """
        # The page goes on the workspace's history, once bound (WS-54).
        def bound(r: Session | None) -> tuple[Session | None, str | None]:
            current = r.session_id if r is not None else ""
            if current and current not in (session_id, replacing):
                # Bound first by another open: that browser is the one kept.
                return None, current
            # Replaced, not rebuilt: a field the record gains later survives a
            # bind, and so does History.
            fresh = replace(
                r if r is not None else Session(),
                session_id=session_id,
                opened_at=time.time(),
                settings=dict(settings or {}),
                set_by={
                    k: (set_by or {}).get(k, settings_module.CALLER)
                    for k in (settings or {})
                },
                site_data={} if forget_site_data or r is None else dict(r.site_data),
                reopened={"browser": session_id, **reopened} if reopened else {},
                **({} if opened_by is KEEP else {"opened_by": opened_by}),
                grid_timeout=grid_timeout,
                window_seen=window_seen,
                # What this browser's open sent and what ran; a new browser
                # has not ended.
                sent=list(sent or []),
                ran=dict(ran) if ran else None,
                ended=None,
            )
            return fresh, None

        # One atomic create-or-update: a save landing while the browser
        # opened is kept, and two first opens on a new name cannot both write.
        _, kept = self.store.change(name, bound, create=True)
        if not kept:
            # Listed again, its workspace's record made if it lapsed, so a
            # session the list lost comes back to it on a reopen (WS-81). An
            # open that found the head idle takes it here (WS-86).
            workspace, session = sessions.split(name)
            now = time.time()

            def listed(r: Workspace | None) -> tuple[Workspace | None, bool]:
                base = (r or Workspace()).listing(session)
                headed = base.headed(session, idle_head, now)
                if headed is r:
                    return None, False
                return headed, headed is not base

            _, moved = self.change_shared(workspace, listed, create=True)
            if moved:
                log.info(
                    "workspace %s: an open moved to the head of its list",
                    sessions.logged(workspace),
                )
                # Its place is no longer the head's: written as it is, so it
                # takes the lifetime of where it is now (WS-87).
                self._restamp(sessions.key_of(workspace, idle_head))
            self._visit(name, url)
            return session_id
        log.info(
            "two opens raced for workspace %s: quitting the loser's browser",
            sessions.logged(name),
        )
        try:
            self.actions.end_browser(session_id)
        except Exception as exc:  # noqa: BLE001 - it may be gone already
            # By type: an HTTPError quotes the Grid URL, Grid id and all.
            log.info(
                "could not end the losing browser for workspace %s: %s",
                sessions.logged(name),
                type(exc).__name__,
            )
            self._let_go(session_id)
        else:
            self._ended(name, session_id, "ended")
        return kept

    def touch(
        self, name: str, *urls: str | None, browser: str | None = None
    ) -> dict | None:
        """Record where the browser landed, and slide the record's TTL.

        Called after an action (through :meth:`settle`) so a later reopen goes
        back to the right page, the top of the history, and so a workspace in
        use does not expire under the caller. A flow run passes every page it
        reached, in order.

        No URL still slides the TTL, and records nothing: a bound write lands
        on `?q=<the password>` and its URL is withheld, and a flow that logs in
        every few minutes must not expire while it is used.

        ``browser`` is the one that produced the result. Once the record names
        another, the TTL still slides and the history is left alone.

        Returns a silent reopen's notice, ``{"cause", "report"}``, the first
        time this browser is touched after it, and never again; a move's notice
        (WS-86) is left for the next result.
        """
        return self._touch(name, *urls, browser=browser, tell=False)[0]

    def _touch(
        self,
        name: str,
        *urls: str | None,
        browser: str | None = None,
        since: str | None = None,
        deliver: bool = True,
        tell: bool = True,
    ) -> tuple[dict | None, str | None, dict | None]:
        """:meth:`touch`, the page to hint for, and the notice of a move off
        the head told (`_visit`, WS-86). The page is the last of ``urls`` with
        an origin, when its host, port, path or route differs from the page
        before (the record's, or ``since`` for a run that flushed its pages
        part-way). None when there is nothing to hint for.

        Decided inside the store's ``fn``, against the record as it is at write
        time, with `page_key` only, which is pure and never raises: the hint
        itself is slow work, asked for outside it by the caller.

        Stamps ``called_at`` on every write, and hands over the held notices
        only when ``deliver``: a move's only when ``tell`` too.
        """
        # The pages go on the workspace's history, after the session's own
        # record says this browser is still the one it holds. That write is
        # made with no page too: it is what slides the workspace's expiry, and
        # its head's, while any of its sessions is in use (WS-87).

        def at(r: Session) -> tuple[Session, tuple[dict | None, bool]]:
            now = time.time()
            if browser and r.session_id != browser:
                return replace(r, called_at=now), (None, False)
            notice = r.notice if deliver else None
            fresh = replace(r, called_at=now)
            return (fresh.delivered() if deliver else fresh), (notice, True)

        noted = self.store.change(name, at)[1]
        if noted is None:
            return None, None, None
        notice, held = noted
        page, moved = self._visit(
            name, *(urls if held else ()), since=since, tell=deliver and tell
        )
        return notice, page, moved

    def _visit(
        self,
        key: str,
        *urls: str | None,
        since: str | None = None,
        tell: bool = False,
    ) -> tuple[str | None, dict | None]:
        """The session ``key`` landing on ``urls``, written to its
        workspace's history tagged with its name (WS-54), through that record's
        compare-and-set so no other session's entry is lost. Answers the page
        to hint for, as `_touch` does, and with ``tell`` the session's notice
        of a move off the head, taken off the record in that same write, so
        only one of two racing calls gets it (`Workspace.told`, WS-86).
        Written with no page too, which slides the workspace's expiry and its
        head's (WS-87); nothing is written when the workspace has no record."""
        workspace, name = sessions.split(key)
        ttl, sub_ttl = self.store.ttl, self.store.sub_ttl

        def at(r: Workspace) -> tuple[Workspace, tuple[str | None, dict | None]]:
            page = _hinted(r.page(name) if since is None else since, urls)
            fresh = r.visited(*urls, ttl=ttl, session=name, sub_ttl=sub_ttl)
            moved = None
            if tell:
                fresh, moved = fresh.told(name)
            return fresh, (page, moved)

        noted = self.change_shared(workspace, at)[1]
        return noted if noted is not None else (None, None)

    def _restamp(self, key: str) -> None:
        """Write the record of ``key`` as it is, so it takes the lifetime of
        its place in the list now (WS-87): an old head moved behind an opener
        would otherwise keep the head's, for ever in a kept workspace."""
        self.store.update(key, lambda r: r)

    def _idle_head(self, key: str) -> str | None:
        """The head of the list an `open_session` in the session ``key`` may
        take (WS-86): the head when it is another session and is idle, with
        no record, no browser, or a browser one `Grid.is_alive` finds dead.
        None otherwise."""
        workspace, name = sessions.split(key)
        head = self.head_of(workspace)
        if head is None or head == name:
            return None
        record = self.store.get(sessions.key_of(workspace, head))
        if record is None or not record.attached:
            return head
        return None if self.actions.grid.is_alive(record.session_id) else head

    def _tell_moved(self, key: str, result: dict) -> None:
        """Tell the session ``key``'s notice of a move off the head on
        ``result`` (an `open_session` answer), taking it off the record in
        one write (WS-86). A store that fails leaves the notice for the next
        result: it never fails the open."""
        workspace, name = sessions.split(key)

        def took(r: Workspace) -> tuple[Workspace | None, dict | None]:
            rest, moved = r.told(name)
            # A notice dropped untold (`Workspace.told`) is written away too.
            return (None if rest is r else rest), moved

        try:
            moved = self.change_shared(workspace, took)[1]
        except Exception as exc:  # noqa: BLE001 - the open has happened
            log.warning(
                "the move notice for %s was not told: %s",
                sessions.logged(key), type(exc).__name__,
            )
            return
        if moved:
            result["main"] = False
            result["main_moved"] = moved

    def _hold_moved_again(self, key: str, moved: dict) -> None:
        """Put a notice of a move off the head back when the result carrying
        it failed (WS-86): only while its session is listed, is not the head,
        and has no newer notice."""
        workspace, name = sessions.split(key)

        def held(r: Workspace) -> tuple[Workspace | None, None]:
            if name not in r.sessions or r.head == name or name in r.main_moved:
                return None, None
            # Its `at` as told, whole seconds, which is all it is ever told as.
            entry = {"to": moved["to"], "at": float(moved["at"])}
            back = {**r.main_moved, name: entry}
            return replace(r, main_moved=back), None

        with contextlib.suppress(Exception):  # the caller's own error is the one to see
            self.change_shared(workspace, held)

    def _page(self, key: str) -> str:
        """The page the session ``key`` is on: its newest entry in its
        workspace's history, or ""."""
        workspace, name = sessions.split(key)
        shared = self.store.get(workspace)
        return shared.page(name) if shared is not None else ""

    def history(self, key: str) -> list[dict]:
        """The session ``key``'s own history entries, newest first."""
        return self._history(key)

    def _history(self, key: str) -> list[dict]:
        """The session ``key``'s own entries in its workspace's history, newest
        first, as the next write would keep them."""
        workspace, name = sessions.split(key)
        record = self.store.get(workspace)
        if record is None:
            return []
        return record.pruned(
            time.time(), self.store.ttl, self.store.sub_ttl
        ).entries(name)

    def _hint(
        self, answer: dict, name: str, url: str | None, scrubbed: bool = False
    ) -> None:
        """Put the flows saved to apply to ``url`` on ``answer`` as
        ``flows_here``, when there are any: the one caller of ``hints``. A hint
        never fails a call: anything it raises is logged by type and adds
        nothing.

        ``scrubbed`` for a hint put on an answer after its scrub: a flow's
        description is a caller's words, checked as a resource read is. A scrub
        that fails drops the hint, never the call.
        """
        if self.hints is None or not url:
            return
        try:
            # Flows are the workspace's, whichever session asks (WS-54).
            here = list(self.hints(sessions.split(name)[0], url) or [])
            if scrubbed and here:
                here = self.scrubber.resource(name, here)
        except Exception as exc:  # noqa: BLE001 - a hint never fails a call
            log.warning(
                "flows for workspace %s could not be hinted: %s",
                sessions.logged(name), type(exc).__name__,
            )
            return
        if here:
            answer["flows_here"] = here

    def reshape(self, name: str, result: dict, browser: str | None = None) -> None:
        """Record the window size an action just gave the browser.

        Only ``resize`` reaches this. The stored settings are what a reopen
        replays, so a resize that stopped at the browser would be undone the
        next time the Grid reaps it.
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
        """The browser and page this workspace last had, for a reopen, with
        who set each setting: an explicit open inherits only the caller's.

        Empty when there is no record, which is the same answer as "nothing to
        inherit" and lets ``open_session`` treat both alike.
        """
        record = self.store.get(name)
        if record is None:
            return {}
        return {
            "settings": dict(record.settings or {}),
            "set_by": dict(record.set_by),
            "url": self._page(name),
        }

    def visited(self, name: str) -> list[str]:
        """The origins the session ``name`` (a key) has been to, newest first:
        its own entries only, never another session's sites.

        As the next write would keep them: a save reads this before its own
        touch prunes, so an origin that aged out since the last call would
        otherwise still be read and saved.
        """
        return [v["origin"] for v in self._history(name)]

    def end_browser(
        self,
        caller: Caller,
        *,
        admin: bool = False,
        session: str | None = None,
        surface: str = "mcp",
    ) -> str | None:
        """End the browser of the caller's session (``session`` names it,
        WS-82), keeping its record: every other session's browser is left
        open (WS-74). See `_end_browser`.

        It takes no turn, so the end is one `Calls.timed` call on
        ``surface``, unless it is the admin's End, which is no call."""
        if admin:
            # The admin's End is not bound by who holds the session (WS-84).
            key = self.session_of(caller, session, admin=True)
            return self._end_browser(key, admin=True)
        with self.calls.timed(
            _named_by(caller), tool="end_browser", surface=surface
        ) as timing:
            key = self.session_of(caller, session)
            timing.workspace = key
            return self._end_browser(key, timing=timing)

    def _end_browser(
        self, key: str, *, admin: bool = False, timing: Timing | None = None
    ) -> str | None:
        """End the browser the session ``key`` holds, keeping its record.

        The one place a browser is ended: the caller ending its own, the admin
        End button, and ``open_session`` replacing one. The browser is quit on
        the Grid and the record detached, keeping the browser choice and the
        last page for whatever opens next. A workspace is never removed; it
        expires on ``WORKSPACE_TTL``, slid forward on every use.

        It never waits its turn (`workspace.locks`): a call driving the browser
        is told it is ending (a long `assert` raises its cancellation at its
        next poll) and the browser is quit without waiting for it, since this
        is the call that exists to interrupt one.

        It is refused while a person holds the browser, checked directly since
        it takes no turn, except for the admin's End: ending the session
        releases control. ``admin`` says the call is that End, and is passed by
        its route alone (`http/admin/workspaces.py`); it is also who the record
        says ended it.

        Returns the browser that was ended, or None if there was none.
        ``timing`` is the caller's `end_browser` call, told which kind it ends.
        """
        name = key
        if not admin:
            self._refuse(key, "end_browser")
        record = self.store.get(name)
        if record is None or not record.attached:
            return None
        if timing is not None:
            timing.browser = _kind(record.settings)
        target = record.session_id
        locks.interrupt(target)
        quit_ok = False
        try:
            self.actions.end_browser(target)
            quit_ok = True
        except Exception as exc:  # noqa: BLE001 - it is going either way
            # Already gone, or the Grid is unreachable. Detach regardless, or
            # the next call would try to use it. By type: an HTTPError quotes
            # the Grid URL, Grid id and all.
            log.info(
                "could not end the browser for workspace %s: %s",
                sessions.logged(name),
                type(exc).__name__,
            )
        # Ending took a Grid round trip: detach the record as it is now, and
        # only if it still names this browser: one opened meanwhile stays.
        ended = {
            "at": time.time(),
            "by": "admin" if admin else "caller",
        }
        self.store.update(
            name, lambda r: r.detached(ended) if r.session_id == target else None
        )
        # The workspace is finished with it, quit or not: its origin and its
        # secret forms go now (`session.ended` says the same, later).
        self.browser_ended(name, target)
        # Only a confirmed quit (a 404 counts: Grid.quit treats it as done) is
        # announced, and only once detached, so a page redrawn on it never
        # draws the old row; after a failure the session may still run, and
        # the monitor's listing finds it later if it is watched.
        if quit_ok:
            self._ended(name, target, "ended")
        else:
            self._let_go(target)
        try:
            # Ending is use, as a call is: it slides the workspace's record too.
            self._visit(name)
        except Exception as exc:  # noqa: BLE001 - the browser has ended either way
            log.warning(
                "the workspace of %s did not slide: %s",
                sessions.logged(name), type(exc).__name__,
            )
        log.info("ended the browser for workspace %s", sessions.logged(name))
        return target
