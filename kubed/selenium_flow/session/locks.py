"""One browser-driving call at a time per session, and how `end_browser` cuts in.

Two calls on one session used to overlap: FastMCP runs sync tools in its thread
pool and the HTTP routes run in Starlette's, so two of our action sequences
could interleave on one browser - a click landing between another call's wait
and its act, a navigation between a secret's origin check and its keystrokes.
The record was always safe (every write is a compare-and-set); the browser is
not. So a call that drives the browser holds its session's `Hold` while it does,
and a second one **waits** for it: no refusal, and no timeout of its own, since
the call holding it is bounded by its own.

**Keyed by the browser a session holds**, its Grid id - which is what
`Recipe.run` is handed. A session holds one browser at a time, so that is one
hold per session; and a browser that was ended takes its hold with it, cancel
flag set, rather than leaving a flag behind that a later browser would have to
clear.

**`end_browser` takes no hold.** It is the call that exists to stop the others,
so it never queues behind them: it sets the hold's ``ending`` flag, which the
holder is watching through `core.cancel`, and a running `assert` raises its
existing cancellation at its next poll and lets go. `open_session` takes none
either - it makes a browser rather than driving one - and nothing that only
reads (`session://current`) does.

**Reentrant**, because the units nest: a flow step and a bound write each hold
it around the page read *and* the action, and the action's own `Recipe.run`
takes it again in the same thread.

**Weak**: a hold lives only while a call holds or waits for it, or a flow run
is under way on its browser, so a browser nobody is driving costs nothing and
there is nothing to clean up.

**Process-local**, Redis store or not. This server runs as one replica (AGENTS.md
"Scaling: one replica"); a second would need a lock in the store, and its MCP
sessions would be the first thing to break anyway.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from weakref import WeakValueDictionary

from ..core import cancel


class Hold:
    """One browser's turn: a reentrant lock, and the flag that ends it early."""

    __slots__ = ("__weakref__", "ending", "lock")

    def __init__(self):
        self.lock = threading.RLock()
        self.ending = threading.Event()


_holds: WeakValueDictionary[str, Hold] = WeakValueDictionary()
_registry = threading.Lock()


def hold(browser: str) -> Hold:
    """The hold for ``browser``: the one any other call on it is using, or new."""
    with _registry:
        found = _holds.get(browser)
        if found is None:
            found = _holds[browser] = Hold()
        return found


@contextmanager
def driving(browser: str):
    """Drive ``browser`` alone for the block, ready to be told it is ending."""
    turn = hold(browser)
    with turn.lock, cancel.watching(turn.ending):
        yield turn


def interrupt(browser: str) -> None:
    """Tell whatever is driving ``browser`` that it is being ended.

    Nothing to do when nothing holds it. Never waits.
    """
    with _registry:
        found = _holds.get(browser)
    if found is not None:
        found.ending.set()
