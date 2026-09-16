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

Nothing but a flow run ever sets one. An action called directly, over either
surface, sees no flag and behaves exactly as it always has.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar

_FLAG: ContextVar[threading.Event | None] = ContextVar("cancel_flag", default=None)


class Cancelled(Exception):
    """The caller stopped waiting, so the work stopped too."""


@contextmanager
def watching(flag: threading.Event | None):
    """Make ``flag`` the one `check` consults, for the duration of the block.

    Set inside the thread doing the work, so it does not depend on whether a
    context variable was copied into that thread on the way.
    """
    token = _FLAG.set(flag)
    try:
        yield
    finally:
        _FLAG.reset(token)


def cancelled() -> bool:
    flag = _FLAG.get()
    return flag is not None and flag.is_set()


def check() -> None:
    """Raise `Cancelled` if the work this thread is doing was called off."""
    if cancelled():
        raise Cancelled("the run was cancelled: its caller stopped waiting")
