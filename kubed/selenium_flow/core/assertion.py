"""The assertion engine: ask until true, and hold until it stays true.

It knows nothing of a browser. It is handed `evaluate()`, which asks the page
once, and `state()`, which says where the page is, plus a clock; what `assert`
means (true after a wait, true for a while, never truthy by accident) lives here.
"""

from __future__ import annotations

import time

from ..faults import AssertionFailed
from . import cancel
from .coerce import as_int, seconds

# How often the page is asked again.
ASSERT_POLL = 0.2


def _shape(value) -> str:
    """What came back, without saying what was in it.

    The refusal is returned and logged, and an assertion can return anything
    the page holds, `document.cookie` included.
    """
    if value is None:
        return "null"
    if isinstance(value, str):
        return f"a string of {len(value)} characters"
    if isinstance(value, list):
        return f"an array of {len(value)} items"
    if isinstance(value, dict):
        return f"an object with {len(value)} keys"
    if isinstance(value, (int, float)):
        return f"a number ({type(value).__name__})"
    return f"a {type(value).__name__}"


def window(wait_timeout, stable_for, default_timeout: int) -> tuple[int, float]:
    """The wait and the hold, read from what the caller sent and checked
    against each other, before anything touches the browser."""
    timeout = max(as_int(wait_timeout, default_timeout), 0)
    hold = seconds(stable_for, 0.0, "stable_for")
    # `>=`: confirming a hold needs a poll after the answer first came back
    # true, and the deadline stops polls at `wait_timeout`.
    if hold and hold >= timeout:
        raise ValueError(
            f"stable_for ({hold}s) leaves no room inside wait_timeout "
            f"({timeout}s): the answer has to be asked again AFTER it has "
            "held, so the wait must be longer than the hold. Both are in "
            "seconds; raise wait_timeout, or lower stable_for"
        )
    return timeout, hold


class Assertion:
    """One assertion: a poll until the page answers true, and stays true."""

    def __init__(self, evaluate, state, timeout: int, hold: float, message=None,
                 clock=time):
        self.evaluate = evaluate
        self.state = state
        self.timeout = timeout
        self.hold = hold
        self.message = message
        self.clock = clock

    def _look(self):
        """One evaluation. A look that fails because the browser was ended under
        it says so, rather than with whatever the Grid said about the quit."""
        try:
            return self.evaluate()
        except Exception:
            cancel.check()
            raise

    def run(self) -> dict:
        """The page's state at the moment it held, or `AssertionFailed`."""
        timeout, hold, message = self.timeout, self.hold, self.message
        deadline = self.clock.monotonic() + timeout
        true_since = None
        ever_true = False
        first = True
        while True:
            answer = self._look()
            # Truthiness is how an assertion passes by accident: a query that
            # answers an element is right until it answers `0` or `[]`.
            if not isinstance(answer, bool):
                raise ValueError(
                    f"assert must return true or false; this returned "
                    f"{_shape(answer)}. Compare, rather than returning the "
                    "thing itself - return !!document.querySelector('#x')"
                )
            now = self.clock.monotonic()
            # An answer that arrived after the deadline is not an answer to
            # `wait_timeout`. The first is exempt: `wait_timeout=0` promises
            # one look.
            if answer and not first and now > deadline:
                answer = False
            first = False
            if answer:
                ever_true = True
                if true_since is None:
                    true_since = now
                if now - true_since >= hold:
                    return self.state()
            else:
                true_since = None  # the clock restarts, it does not pause
            # Bounded by what is left, and re-checked before the next look.
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                break
            self.clock.sleep(min(ASSERT_POLL, remaining))
            # A flow can wait minutes here; a cancelled run lets go of the
            # browser within one poll.
            cancel.check()
            if self.clock.monotonic() > deadline:
                break  # a sleep can wake late

        # A browser ended during the last look has no page for `message` to
        # describe.
        cancel.check()
        state = self.state()
        if hold and ever_true:
            # Told apart from "never true": a page still settling, not a wrong
            # assertion.
            raise AssertionFailed(
                message
                or f"the assertion became true on {state.get('url')!r} but did "
                f"not hold for {hold}s. Give the step a message to say what "
                "should have been true"
            )
        # The script is not echoed: it can carry a token compared inline, and
        # is kept out of run summaries (`SAFE_IN_SUMMARY`) for that reason.
        raise AssertionFailed(
            message
            or f"assertion failed after {timeout}s on {state.get('url')!r}. "
            "Give the step a message to say what should have been true"
        )
