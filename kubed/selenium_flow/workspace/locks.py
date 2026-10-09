"""One browser-driving call at a time per session, and how `end_browser` cuts in.

Two calls on one session used to overlap: FastMCP runs sync tools in its thread
pool and the HTTP routes run in Starlette's, so two of our action sequences
could interleave on one browser - a click landing between another call's wait
and its act, a navigation between a secret's origin check and its keystrokes.
The record was always safe (every write is a compare-and-set); the browser is
not. So a call that drives the browser holds its session's `Hold` while it does,
and a second one **waits** for it: no refusal, and no timeout of its own, since
the call holding it is bounded by its own.

**Keyed by the session's browser**, its Grid id - which is what `Recipe.run`
is handed. A workspace holds one session at a time, so that is one hold per
workspace at any moment; and a browser that was ended takes its hold with it, cancel
flag set, rather than leaving a flag behind that a later browser would have to
clear.

**`end_browser` takes no hold.** It is the call that exists to stop the others,
so it never queues behind them: it sets the hold's ``ending`` flag, which the
holder is watching through `core.cancel`, and a running `assert` raises its
existing cancellation at its next poll and lets go. `open_session` takes none
either - it makes a browser rather than driving one - and nothing that only
reads (`workspace://current`) does.

**Reentrant**, because the units nest: a flow step and a bound write each hold
it around the page read *and* the action, and the action's own `Recipe.run`
takes it again in the same thread. **Fair**: first come, first served (`Turns`),
so a flow, which lets go between steps, cannot barge back ahead of a call that
queued during one.

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


class Turns:
    """A reentrant lock that is taken in the order it was asked for.

    `threading.RLock` hands off to nobody: a flow releasing between two steps
    re-acquires microseconds later, and a call queued during step one could
    wait the whole run (measured: up to step 39 of 40). Here each acquire takes
    a ticket and waits for its number, so a call queued during a step runs
    before the next one. The thread that holds it may take it again freely.
    """

    __slots__ = ("_abandoned", "_changed", "_depth", "_next", "_owner", "_serving")

    def __init__(self):
        self._changed = threading.Condition(threading.Lock())
        self._owner: int | None = None
        self._depth = 0
        self._next = 0  # the next ticket handed out
        self._serving = 0  # the ticket whose turn it is
        self._abandoned: set[int] = set()

    def __enter__(self):
        me = threading.get_ident()
        with self._changed:
            if self._owner == me:
                self._depth += 1
                return self
            ticket = self._next
            self._next += 1
            try:
                while self._serving != ticket:
                    self._changed.wait()
            except BaseException:
                # Gone without its turn (an interrupt in the main thread): a
                # ticket nobody will release must not stop the queue.
                self._abandoned.add(ticket)
                self._skip_abandoned()
                raise
            self._owner, self._depth = me, 1
            return self

    def __exit__(self, *exc):
        with self._changed:
            if self._owner != threading.get_ident():
                raise RuntimeError("released by a thread that does not hold it")
            self._depth -= 1
            if self._depth:
                return
            self._owner = None
            self._serving += 1
            self._skip_abandoned()

    def _skip_abandoned(self) -> None:
        while self._serving in self._abandoned:
            self._abandoned.discard(self._serving)
            self._serving += 1
        self._changed.notify_all()


class Hold:
    """One browser's turn: a fair reentrant lock, and the flag that ends it early."""

    __slots__ = ("__weakref__", "ending", "lock")

    def __init__(self):
        self.lock = Turns()
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
    """Drive ``browser`` alone for the block, ready to be told it is ending.

    The flag is checked once the turn comes, before the block: a call that
    queued and was ended while it waited must not reconnect to a browser whose
    Grid DELETE may still be in flight, and only a long `assert` polls on its own.
    """
    turn = hold(browser)
    with turn.lock, cancel.watching(turn.ending, raises=cancel.Ended):
        cancel.check()
        try:
            yield turn
        except cancel.Cancelled:
            raise
        except Exception:
            # Ended under a call that was not polling - an element wait, say:
            # the Grid's failure ("Failed to execute request (POST http://<the
            # node>/...)") is what ending looks like from inside, so it is
            # answered as the ending, a 404 like any dead browser.
            cancel.check()
            raise


def interrupt(browser: str) -> None:
    """Tell whatever is driving ``browser`` that it is being ended.

    Nothing to do when nothing holds it. Never waits.
    """
    with _registry:
        found = _holds.get(browser)
    if found is not None:
        found.ending.set()
