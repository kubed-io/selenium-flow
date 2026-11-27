"""Every call, at the recipe's host: the touch, `call.finished`, and the hint.

A call that takes its session's turn has a host (`Workspaces.act` for both
surfaces, the bound write `secrets.perform_write`, a flow step
`flows.api.run_for`) that calls `Calls.begin` as the call starts and
`Calls.end` once it has its answer, and that is the whole of what it does here:

- while a person holds the browser in the live view, a call that would change
  it is refused (`faults.Controlled`) before anything else, so a refused call
  is never begun: no in-flight count, no ``call.finished``. Every host calls
  `begin` holding its turn on the session's lock, so a call that queued before
  the person asked is refused when its turn comes;
- the monitor is asked to keep the session alive (`Monitor.keep`), so a
  session opened before a restart is kept from its next call;
- capture is held again when it is owed and was let go while the agent paused
  (the deadline closes an idle connection), so the call that comes back is
  captured too;
- the monitor is told the call is under way and when it is over, each a touch,
  so a held connection's deadline moves on and is never reached while the call
  runs (a close would take its late events, its flush and any borrowed channel
  with it);
- `call.finished` is published, values-free: who, which tool on which surface,
  how it came out, when and how long;
- with capture on, what the page reported while the call ran is counted and
  comes back as the hint, ``"page": {...}``, which the host puts on its result,
  or, for a call that raised, as one sentence on its error, which
  `faults.message` reads;
- with telemetry on, the call is a ``call <tool>`` span from `begin` to `end`,
  never made current, naming the workspace and never the sub-session.

The host holds the session's lock (`workspace.locks`) from `begin` to `end`, so
another call's events cannot land inside this one's window. At the end, one
round trip on the held connection lets the events the browser sent before it
land first.

A call that takes no turn of its own (`open_session`, `end_browser`, and the
MCP tools `await_control`, `show`, `keep_file` and the flow tools; a
`run_flow`'s steps take theirs, each a call of its own) runs inside
`Calls.timed` instead, which gives it its span and its ``call.finished`` and
nothing else: no keep-alive, touch, capture or hint, and no control check.
`open_session` and `end_browser` make that check themselves, `run_flow`'s
steps are each checked at their turn, and the other tools change no browser.
A turnless call's time is the whole call, a Grid wait included, and a refused
one is not recorded either.

Nothing here fails a call. `end` never raises and never waits longer than one
bounded flush; a monitor or a flush that fails is logged by its type alone,
because its arguments name a Grid id.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Literal, NamedTuple

from .. import errors
from ..bidi import REPLY_TIMEOUT, Channel
from ..capture import CONSOLE_URI, NETWORK_URI
from ..capture.store import Mark
from ..faults import PAGE_NOTE, Controlled
from ..monitor.events import CallFinished
from ..redaction import Forms
from . import sessions

log = logging.getLogger(__name__)

# Where a hint sends its reader: the console when the page threw, else the
# requests that failed.
SEE_ERRORS = f"{CONSOLE_URI}?level=error"
SEE_FAILED = f"{NETWORK_URI}?filter=failed"
# The note's stand-in for a session's name: a note is logged (WS-82).
NAMED = "session://{name}/"


class Begun(NamedTuple):
    """A call under way: whose, on which browser, since when, and the capture's
    totals as it started (None when the workspace captures nothing). ``own``
    is the caller's own session, when the call named another."""

    workspace: str
    session_id: str
    started: float
    mark: Mark | None
    own: str | None = None
    span: object | None = None


def told(monitor, what: str, *args, **kwargs):
    """Tell ``monitor`` ``what``, when there is one. Never fails the caller: a
    monitor that raises is logged by its type (the arguments name a Grid id)
    and the call carries on."""
    if monitor is None:
        return None
    try:
        return getattr(monitor, what)(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a fact to announce, not to need
        log.warning("the monitor failed on %s: %s", what, type(exc).__name__)
        return None


def hint(caused: dict, acted: str = "", own: str = "") -> dict | None:
    """The ``page`` object for what a call caused, or None when it caused nothing.
    ``see`` points into the session ``acted`` for a caller whose own is ``own``
    (`sessions.pointer`)."""
    errors_, failed = caused.get("errors", 0), caused.get("failed_requests", 0)
    if not errors_ and not failed:
        return None
    see = SEE_ERRORS if errors_ else SEE_FAILED
    return {
        "errors": errors_,
        "failed_requests": failed,
        "see": sessions.repointed(see, acted, own),
    }


def total(
    hints: Iterable[dict | None], acted: str = "", own: str = ""
) -> dict | None:
    """A flow run's hint: its steps' summed, or None when none had one."""
    given = [h for h in hints if h is not None]
    return hint(
        {
            "errors": sum(h["errors"] for h in given),
            "failed_requests": sum(h["failed_requests"] for h in given),
        },
        acted,
        own,
    )


def sentence(page: dict) -> str:
    """The hint as the note a failed call's error carries. A ``see`` into a
    named session keeps its form and drops the name, since the note is logged.
    A hint points at the console or the network, never at downloads, so every
    ``session://`` see is a named one (`downloads-2` is a name too)."""
    see, named = page["see"], ""
    if see.startswith("session://"):
        see = NAMED + see.removeprefix("session://").partition("/")[2]
        named = ", {name} being the session this call named"
    return (
        f"{PAGE_NOTE}{page['errors']} page error(s) and "
        f"{page['failed_requests']} failed request(s): read {see}{named}."
    )


def outcome(error: BaseException | None) -> Literal["ok", "4xx", "5xx"]:
    """How a call came out, in the classes its HTTP status would say."""
    if error is None:
        return "ok"
    return "4xx" if errors.status_for(error) < 500 else "5xx"


class Timing:
    """A call `Calls.timed` is timing: whose and on which browser, as the body
    learns them. ``workspace`` is a session's key, or the caller's workspace
    name until the key is known."""

    def __init__(self, workspace: str, browser: str = ""):
        self.workspace = workspace
        self.browser = browser


class Calls:
    """`begin` and `end` around every call that takes a turn, `timed` around
    one that takes none; `bound` before a secret is typed."""

    def __init__(
        self,
        *,
        monitor=None,
        capture=None,
        control=None,
        clock: Callable[[], float] = time.time,
        telemetry=None,
    ):
        self.monitor = monitor
        self.capture = capture
        # Who says a person holds a browser (`live.control.Controls`): anything
        # with ``refusal(session_id, tool)``, or None, which refuses nothing.
        self.control = control
        self.clock = clock
        # `telemetry.Telemetry`, or None: no span.
        self.telemetry = telemetry

    def refuse(self, session_id: str, tool: str) -> None:
        """Raise the refusal for ``tool`` on this browser, if a person holds it."""
        if self.control is None:
            return
        refusal = self.control.refusal(session_id, tool)
        if refusal is not None:
            raise refusal

    def begin(
        self,
        workspace: str,
        session_id: str,
        *,
        browser: str = "",
        tool: str,
        own: str | None = None,
    ) -> Begun:
        """A call starts: refused while a person holds the browser and ``tool``
        would change it; else the monitor is asked to keep the session alive
        and told the call is under way, capture is held again if it
        lapsed, then marked. Called holding the session's turn. ``own`` is the
        caller's own session, which the hint points from."""
        self.refuse(session_id, tool)
        told(self.monitor, "keep", workspace, session_id, browser=browser)
        told(self.monitor, "calling", session_id)
        self._resume(workspace, session_id, browser)
        mark = self._mark(workspace, session_id)
        span = self._span(workspace, tool, browser)
        return Begun(workspace, session_id, self.clock(), mark, own, span)

    def end(
        self,
        begun: Begun,
        *,
        tool: str,
        surface: Literal["mcp", "http", "flow"],
        browser: str,
        error: BaseException | None = None,
    ) -> dict | None:
        """Touch, flush, count and say the call; the hint, or None. Never raises."""
        # The call's own time: the flush below is bookkeeping, not the action.
        duration = max(0.0, self.clock() - begun.started)
        how = outcome(error)
        if self.telemetry is not None:
            self.telemetry.end_call(
                begun.span, surface=surface, outcome=how, error=error
            )
        told(self.monitor, "called", begun.session_id)
        page = None
        if begun.mark is not None:
            self._flush(begun.session_id)
            page = self._caused(begun)
        self._finished(
            CallFinished(
                begun.workspace,
                begun.session_id,
                browser,
                tool,
                surface,
                how,
                begun.started,
                duration,
            )
        )
        if error is not None and page is not None:
            error.add_note(sentence(page))
        return page

    @contextmanager
    def timed(
        self,
        workspace: str,
        *,
        tool: str,
        surface: Literal["mcp", "http", "flow"],
        browser: str = "",
    ) -> Iterator[Timing]:
        """Time a call that takes no turn, around the whole of its body: its
        span and its ``call.finished``, and nothing else. No keep-alive, touch,
        capture or hint, and no control check: the body does its own.

        ``workspace`` is the session's key, or the caller's workspace name; the
        body may set the `Timing`'s ``workspace`` and ``browser`` as it learns
        them. Everything is decided when the body is over: a call a person's
        control refused (`faults.Controlled`) records nothing, and neither does a
        cancelled one, and any other comes out by `outcome`, its error raised
        unchanged. The span is made then, dated from the start, so a refused
        call never has one."""
        timing = Timing(workspace, browser)
        started = self.clock()
        try:
            yield timing
        except Controlled:
            raise
        except Exception as exc:
            self._timed(timing, tool, surface, started, exc)
            raise
        self._timed(timing, tool, surface, started, None)

    def _timed(
        self,
        timing: Timing,
        tool: str,
        surface: str,
        started: float,
        error: BaseException | None,
    ) -> None:
        duration = max(0.0, self.clock() - started)
        how = outcome(error)
        if self.telemetry is not None:
            span = self._span(
                timing.workspace, tool, timing.browser, start_time=int(started * 1e9)
            )
            self.telemetry.end_call(span, surface=surface, outcome=how, error=error)
        # No Grid id: an open may have none yet, and only telemetry reads it.
        self._finished(
            CallFinished(
                timing.workspace,
                "",
                timing.browser,
                tool,
                surface,
                how,
                started,
                duration,
            )
        )

    def _span(
        self, key: str, tool: str, browser: str, *, start_time: int | None = None
    ):
        """A call's span, naming its workspace and never its sub-session; None
        without telemetry."""
        if self.telemetry is None:
            return None
        return self.telemetry.start_call(
            sessions.split(key)[0],
            sub_session=sessions.is_sub(key),
            tool=tool,
            browser=browser,
            start_time=start_time,
        )

    def _finished(self, event: CallFinished) -> None:
        """Publish ``call.finished`` on the monitor's bus, when there is one."""
        bus = getattr(self.monitor, "bus", None) if self.monitor is not None else None
        told(bus, "publish", event)

    def bound(self, workspace: str, forms: Forms) -> None:
        """Mask ``forms`` in what this workspace captures from now on. Called
        before the keystrokes, and allowed to fail the write: typing a value
        capture would keep in the clear is worse than not typing it."""
        if self.capture is not None:
            self.capture.secret(workspace, forms)

    def _resume(self, workspace: str, session_id: str, browser: str) -> None:
        """Hold capture's connection again before the call, when the session
        owes capture and none is held: the deadline let it go while the agent
        paused, and the touch at the end would come too late for this call.
        A hold again aligns Chrome's replay. A failure costs this call its
        capture, never the call; it is logged by type (it names a Grid id),
        and backs off: until it ends, a call does not try, so a route that
        hangs costs one call ATTACH_TIMEOUT rather than every call."""
        monitor = self.monitor
        if monitor is None or self.capture is None:
            return
        try:
            lapsed = (
                monitor.owes(session_id, "capture")
                and not monitor.capturing(session_id)
                and not monitor.backing_off(session_id)
            )
            if lapsed:
                monitor.attach(
                    workspace, session_id, "capture", browser=browser, resume=True
                )
        except Exception as exc:  # noqa: BLE001 - capture is a fact, not a need
            log.warning("capture did not resume for a call: %s", type(exc).__name__)

    def _mark(self, workspace: str, session_id: str) -> Mark | None:
        if self.capture is None:
            return None
        try:
            mark = self.capture.mark(workspace)
            if mark is None and told(self.monitor, "owes", session_id, "capture"):
                # The buffer expired in a quiet spell, and the call may begin
                # one again, counting from zero.
                mark = Mark(0, 0)
        except Exception as exc:  # noqa: BLE001 - no window, so no hint
            log.warning("capture could not mark a call: %s", type(exc).__name__)
            return None
        return mark

    def _caused(self, begun: Begun) -> dict | None:
        try:
            return hint(
                self.capture.caused(begun.workspace, begun.mark),
                begun.workspace,
                begun.workspace if begun.own is None else begun.own,
            )
        except Exception as exc:  # noqa: BLE001 - a hint to give, not to need
            log.warning("capture could not count a call: %s", type(exc).__name__)
            return None

    def _flush(self, session_id: str) -> None:
        """Let what the browser sent before now land, on the held connection.
        Nothing to do when none is held; a failure only costs the hint the
        events still in flight."""
        try:
            conn = self.monitor.connection(session_id) if self.monitor else None
            loop = self.monitor.loop if conn is not None else None
            if conn is None or loop is None:
                return
            Channel(conn, loop).flush(timeout=REPLY_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - logged by type: no Grid id
            log.debug("the call's flush failed: %s", type(exc).__name__)
