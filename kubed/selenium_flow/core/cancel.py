"""Stopping work nobody is waiting for any more.

Cancelling the MCP request that started a flow run cancels the coroutine, not
the worker thread, which would keep driving the browser: an `assert` with
`wait_timeout: 900` holds a Grid slot for fifteen minutes. So the places that
can wait a long time look at a stop flag. It travels in a context variable,
because an argument would land in every tool schema derived from the actions.

A flow run carries its flag for the whole run, and a browser-driving call
carries its browser's (`workspace.locks`), which `end_browser` sets. Flags
stack, and the first one set, outermost first, decides what is raised: a direct
call whose browser was ended raises `Ended` (a 404), and a flow keeps its own
sentence.
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


_FLAGS: ContextVar[tuple[tuple[threading.Event, type[Cancelled]], ...]] = ContextVar(
    "cancel_flags", default=()
)


@contextmanager
def watching(flag: threading.Event | None, raises: type[Cancelled] = Cancelled):
    """Add ``flag`` to the ones `check` consults, for the duration of the block.

    ``raises`` is what `check` raises once it is set. Called inside the thread
    doing the work, so it does not depend on a context variable being copied
    into it. None adds nothing.
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
