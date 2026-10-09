"""Stopping work nobody is waiting for any more.

A flow run executes in a worker thread, and cancelling the MCP request that
started it cancels the coroutine waiting on that thread — not the thread. Left
alone, a run whose caller gave up keeps driving the browser: an `assert` with
`wait_timeout: 900` polls for fifteen minutes and holds a Grid slot the whole
time, answering nobody.

So the run carries a stop flag, and the places that can wait a long time look
at it. It travels in a context variable rather than as an argument because the
wait that matters is inside `Actions.assert_`, several calls below the run, and
threading a flag through every action's signature would put it into every tool
schema derived from them.

Two things set one. A flow run carries its stop flag for the whole run, and a
browser-driving call carries its browser's (`workspace.locks`), which
`end_browser` sets so a direct `assert` lets go instead of making the call that
exists to interrupt it wait. Flags stack rather than replace each other, so a
step inside a run answers to both, and each says what it raises: the first one
set, outermost first, decides. So a direct call whose browser was ended raises
`Ended` (a 404, like any dead browser), and a flow — whose run-level flags are
outermost — keeps its own sentence.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar


class Cancelled(Exception):
    """The caller stopped waiting, so the work stopped too."""

    SAYS = "the run was cancelled: its caller stopped waiting"


class Ended(Cancelled):
    """The browser this call was driving was ended under it (`end_browser`)."""

    SAYS = "the browser was ended while this call was waiting"


_FLAGS: ContextVar[tuple[tuple[threading.Event, type[Cancelled]], ...]] = (
    ContextVar("cancel_flags", default=())
)


@contextmanager
def watching(flag: threading.Event | None, raises: type[Cancelled] = Cancelled):
    """Add ``flag`` to the ones `check` consults, for the duration of the block.

    ``raises`` is what `check` raises once it is set. Set inside the thread
    doing the work, so it does not depend on whether a context variable was
    copied into that thread on the way. None adds nothing.
    """
    flags = _FLAGS.get()
    token = _FLAGS.set(flags if flag is None else (*flags, (flag, raises)))
    try:
        yield
    finally:
        _FLAGS.reset(token)


def _first_set() -> type[Cancelled] | None:
    return next((kind for flag, kind in _FLAGS.get() if flag.is_set()), None)


def cancelled() -> bool:
    return _first_set() is not None


def check() -> None:
    """Raise `Cancelled` if the work this thread is doing was called off."""
    kind = _first_set()
    if kind is not None:
        raise kind(kind.SAYS)
