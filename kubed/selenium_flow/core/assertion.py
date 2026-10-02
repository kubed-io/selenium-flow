"""The assertion engine: ask until true, and hold until it stays true.

It knows nothing of a browser. It is handed `evaluate()`, which asks the page
once, and `state()`, which says where the page is, plus a clock; what `assert`
means (true after a wait, true for a while, never truthy by accident) lives here.
"""

from __future__ import annotations

import time

from ..errors import AssertionFailed
from . import cancel
from .coerce import as_int, seconds

# How often the page is asked again. Short enough to catch a route change
# in the frame after it lands, long enough not to spin the Grid on a wait that
# is going to take seconds.
ASSERT_POLL = 0.2


def _shape(value) -> str:
    """What came back, without saying what was in it.

    The refusal is returned to the caller and logged, and an assertion can
    return anything the page holds — `document.cookie`, an innerHTML, a token
    in a data attribute. The author needs to know their expression answered
    with a string rather than a comparison; nobody needs the string.
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
    # Resolved before the browser is touched, like every other argument
    # mistake here: `_at` reconnects and may NAVIGATE, so validating after
    # it means an impossible request moves the caller's browser and then
    # answers 400. A rejected argument must cost nothing (Copilot, #31).
    timeout = max(as_int(wait_timeout, default_timeout), 0)
    hold = seconds(stable_for, 0.0, "stable_for")
    # `>=`, not `>`. Verifying a hold needs at least one poll AFTER the
    # answer first came back true, and the deadline stops that poll at
    # `wait_timeout` - so a hold exactly equal to the timeout can never be
    # confirmed and every such assertion failed with "did not hold",
    # whatever the page did (Copilot, #31).
    if hold and hold >= timeout:
        # Refused rather than silently impossible, and the message names the
        # unit: `stable_for` and `wait_timeout` are both seconds, and a
        # caller who read one of them as milliseconds finds out here rather
        # than from an assertion that can never pass.
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

    def run(self) -> dict:
        """The page's state at the moment it held, or `AssertionFailed`."""
        timeout, hold, message = self.timeout, self.hold, self.message
        deadline = self.clock.monotonic() + timeout
        true_since = None
        ever_true = False
        first = True
        while True:
            answer = self.evaluate()
            if not isinstance(answer, bool):
                raise ValueError(
                    f"assert must return true or false; this returned "
                    f"{_shape(answer)}. Compare, rather than returning the "
                    "thing itself - return !!document.querySelector('#x')"
                )
            now = self.clock.monotonic()
            # The script itself can run past the deadline - a blocking
            # expression, a page that stops responding - and an answer that
            # arrived after the caller stopped waiting is not an answer to
            # `wait_timeout` (Copilot, #31). The FIRST evaluation is exempt,
            # because `wait_timeout=0` promises exactly one look and any script
            # takes longer than nothing.
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
                # The clock restarts, it does not pause. A transient that
                # flickers true, false, true has not held true for anything.
                true_since = None
            # Bounded by what is left, and re-checked before the next
            # evaluation: a fixed pause here could carry the call past
            # `wait_timeout` and then report an answer that arrived after the
            # caller had stopped waiting for it.
            remaining = deadline - self.clock.monotonic()
            if remaining <= 0:
                break
            self.clock.sleep(min(ASSERT_POLL, remaining))
            # The one wait here long enough to outlive its caller: a flow that
            # waits fifteen minutes for an order to ship. Looked at every poll,
            # so a cancelled run lets go of the browser within one.
            cancel.check()
            if self.clock.monotonic() > deadline:
                # A sleep can wake late. Without this, the answer that arrived
                # after the caller stopped waiting would still be accepted, so
                # a slow page could pass an assertion it had already failed.
                # The first evaluation is above the loop's exits, so
                # wait_timeout=0 still asks exactly once.
                break

        state = self.state()
        if hold and ever_true:
            # A different failure and worth saying so: the page DID answer true,
            # it just would not stay that way. Told apart from "never true",
            # because the fixes are opposite - one is a wrong assertion, the
            # other is a page still settling.
            raise AssertionFailed(
                message
                or f"the assertion became true on {state.get('url')!r} but did "
                f"not hold for {hold}s. Give the step a message to say what "
                "should have been true"
            )
        # The script is not echoed. It is the author's text rather than the
        # page's, but it can carry a literal a run report must not: a token
        # compared inline, a serialised request body. This package already keeps
        # `script` out of run summaries (`SAFE_IN_SUMMARY`) for that reason, and
        # a failure message is read in more places than a summary is - the HTTP
        # error, the flow report, the log. So: where it was false, and a nudge
        # to write the sentence that would have said what should have been true.
        raise AssertionFailed(
            message
            or f"assertion failed after {timeout}s on {state.get('url')!r}. "
            "Give the step a message to say what should have been true"
        )
