"""Where the mouse pointer is, per browser, and how it gets somewhere else.

WebDriver has no "where is the pointer" command. It does not need one: nothing
moves a WebDriver pointer except WebDriver, so whoever sends every move can
remember where it ended up. This module is that memory, plus the two gestures
that read it.

Two things depend on knowing the start.

**A glide** — many small moves instead of one — needs a line to walk, and a line
needs both ends. Asked to glide from a position we do not have, it jumps and the
result says so; a path plotted from a guessed origin crosses the wrong elements,
which is worse than no path at all (saga §F2.3).

**A nudge** — moving away before moving back — is what makes a hover onto a
target the pointer is already inside actually fire. Measured on the live Grid,
Chrome and Firefox alike: the second hover of the same element delivers no
`mouseover` and the action still reports success, which cost one pilot the same
menu twice (saga §F2.10).

Keyed by the Grid's session id rather than by a caller key, so a browser opened
through the HTTP surface — which has no flow session at all — has a pointer too.
Kept in Redis when Redis is configured, for the same reason the window size is:
this process restarts and the browser does not.
"""

from __future__ import annotations

import json
import logging
import os
import time

from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.mouse_button import MouseButton

log = logging.getLogger(__name__)

# Long enough to outlive any browser: the Grid reaps an idle session well
# before this, and a stale entry costs one wrong glide origin rather than
# anything durable.
DEFAULT_TTL_SECONDS = 86400
DEFAULT_PREFIX = "selenium-flow:pointer:"

# The path. About one step per 20px so a short move is not 60 events and a long
# one is not a teleport, clamped at both ends, with a frame-length pause between
# steps — all of it inside one `perform()`, which is one round trip.
PIXELS_PER_STEP = 20
MIN_STEPS = 6
MAX_STEPS = 40
STEP_MS = 16

# How far a nudge moves before coming back. Far enough to leave the element,
# near enough that the pointer does not cross the whole page to do it.
NUDGE = 40

# How long the pointer rests between pressing and moving in a drag. Several drag
# libraries arm on a delay or a distance rather than on the press itself.
HOLD_MS = 120


class MemoryPointers:
    """Process-local positions. Correct for one replica, lost on restart."""

    kind = "memory"

    def __init__(self, ttl: int = DEFAULT_TTL_SECONDS, clock=time.time):
        self._data: dict[str, tuple[float, tuple[float, float]]] = {}
        self._ttl = ttl
        self._clock = clock

    def get(self, session_id: str) -> tuple[float, float] | None:
        entry = self._data.get(session_id)
        if entry is None:
            return None
        expires_at, point = entry
        if self._clock() >= expires_at:
            del self._data[session_id]
            return None
        return point

    def set(self, session_id: str, x: float, y: float) -> None:
        self._data[session_id] = (self._clock() + self._ttl, (x, y))

    def forget(self, session_id: str) -> None:
        self._data.pop(session_id, None)


class RedisPointers:
    """Shared positions, so any replica plots a path from the same start."""

    kind = "redis"

    def __init__(
        self, client, prefix: str = DEFAULT_PREFIX, ttl: int = DEFAULT_TTL_SECONDS
    ):
        self._client = client
        self._prefix = prefix
        self._ttl = ttl

    def _k(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def get(self, session_id: str) -> tuple[float, float] | None:
        try:
            raw = self._client.get(self._k(session_id))
            if not raw:
                return None
            point = json.loads(raw)
            return float(point[0]), float(point[1])
        # A position is a convenience, never a reason to fail an action: an
        # unreachable Redis means "we do not know where the pointer is", which
        # is a state this module already handles.
        except Exception:
            log.debug("could not read the pointer for %s", session_id, exc_info=True)
            return None

    def set(self, session_id: str, x: float, y: float) -> None:
        try:
            self._client.set(self._k(session_id), json.dumps([x, y]), ex=self._ttl)
        except Exception:  # see `get`
            log.debug("could not record the pointer for %s", session_id, exc_info=True)

    def forget(self, session_id: str) -> None:
        try:
            self._client.delete(self._k(session_id))
        except Exception:  # see `get`
            log.debug("could not clear the pointer for %s", session_id, exc_info=True)


def from_env(env: dict | None = None):
    """The pointer store the environment asks for, alongside the session store.

    Deliberately reads the same switches: an install that shares session records
    between replicas wants to share this too, and a second variable to forget is
    a second way for one replica to plot a path from another's stale origin.
    """
    from . import store as store_module

    env = os.environ if env is None else env
    ttl = int(env.get("SESSION_TTL", DEFAULT_TTL_SECONDS))
    if store_module.chosen_backend(env) != "redis":
        return MemoryPointers(ttl=ttl)
    client = store_module.redis_client(env)
    if client is None:
        return MemoryPointers(ttl=ttl)
    prefix = env.get("REDIS_PREFIX", store_module.DEFAULT_PREFIX) + "pointer:"
    return RedisPointers(client, prefix=prefix, ttl=ttl)


def centre(driver, element) -> tuple[float, float]:
    """Where ``element`` is *now*, in viewport coordinates.

    Read at the moment of the move and never cached: scrolling moves every
    element under a pointer that stays put, so a destination worked out one call
    ago is a destination somewhere else (saga §F2.3).
    """
    rect = driver.execute_script(
        "const r = arguments[0].getBoundingClientRect();"
        "return [r.left + r.width / 2, r.top + r.height / 2];",
        element,
    )
    return float(rect[0]), float(rect[1])


def steps_between(start: tuple[float, float], end: tuple[float, float]) -> int:
    """How many moves a glide between these two points is worth."""
    distance = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5
    return max(MIN_STEPS, min(MAX_STEPS, int(distance / PIXELS_PER_STEP) or MIN_STEPS))


def _eased(fraction: float) -> float:
    """Ease in and out, so a glide starts and stops the way a hand does."""
    if fraction < 0.5:
        return 2 * fraction * fraction
    return 1 - ((-2 * fraction + 2) ** 2) / 2


def path(start: tuple[float, float], end: tuple[float, float]) -> list[tuple[int, int]]:
    """A straight, eased line from ``start`` to ``end``, ending exactly on it.

    Straight on purpose. Leaving a flyout diagonally can cross the sibling below
    it, and the fix for that is a waypoint — not proposed until a real site needs
    one, because hovering the parent and then the child already works.
    """
    total = steps_between(start, end)
    points = []
    for step in range(1, total + 1):
        at = _eased(step / total)
        points.append(
            (
                round(start[0] + (end[0] - start[0]) * at),
                round(start[1] + (end[1] - start[1]) * at),
            )
        )
    return points


# One round trip for both questions a move has to ask about its destination: is
# the pointer already inside it, and if so where is there to step to first.
NUDGE_JS = """
const [el, atX, atY, gap] = [arguments[0], arguments[1], arguments[2], arguments[3]];
const box = el.getBoundingClientRect();
const within = (x, y, r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
if (atX === null || atY === null || !within(atX, atY, box)) {
  return {inside: false, away: null};
}
const vw = window.innerWidth, vh = window.innerHeight;
const parent = el.parentElement ? el.parentElement.getBoundingClientRect() : null;
const candidates = [
  [box.left - gap, box.top + box.height / 2],
  [box.right + gap, box.top + box.height / 2],
  [box.left + box.width / 2, box.top - gap],
  [box.left + box.width / 2, box.bottom + gap],
];
const usable = (c) => c[0] >= 0 && c[1] >= 0 && c[0] < vw && c[1] < vh &&
  !within(c[0], c[1], box);
// A point still inside the element's parent is preferred: stepping right out
// of an open flyout to nudge would close the very thing we are about to hover.
for (const c of candidates) {
  if (usable(c) && parent && within(c[0], c[1], parent)) {
    return {inside: true, away: [Math.round(c[0]), Math.round(c[1])]};
  }
}
for (const c of candidates) {
  if (usable(c)) return {inside: true, away: [Math.round(c[0]), Math.round(c[1])]};
}
return {inside: true, away: null};
"""


def _nudge_point(driver, element, start) -> tuple[bool, tuple[int, int] | None]:
    """Whether the pointer is already inside ``element``, and where to step."""
    at = (None, None) if start is None else start
    try:
        answer = driver.execute_script(NUDGE_JS, element, at[0], at[1], NUDGE) or {}
    # Best effort: not knowing costs one hover that does nothing, which is
    # exactly what happened before any of this existed.
    except Exception:
        log.debug("could not work out a nudge point", exc_info=True)
        return False, None
    away = answer.get("away")
    return bool(answer.get("inside")), (
        (int(away[0]), int(away[1])) if away else None
    )


def move(driver, element, start=None, glide=False) -> dict:
    """Put the pointer on ``element``, and say how it got there.

    The last move is always WebDriver's element-origin move — the one this
    package has always sent, which scrolls the element into view for us. A
    glide adds intermediate points *before* it rather than replacing it, so an
    off-screen destination still works and nothing that works today changes.
    """
    inside, away = _nudge_point(driver, element, start)
    builder = ActionBuilder(driver, duration=STEP_MS)
    if inside and away is not None:
        builder.pointer_action.move_to_location(away[0], away[1])
        start = (float(away[0]), float(away[1]))

    glided = False
    if glide and start is not None:
        end = centre(driver, element)
        for x, y in path(start, end)[:-1]:
            builder.pointer_action.move_to_location(x, y)
        glided = True
    builder.pointer_action.move_to(element)
    builder.perform()

    # Read after the move, not before: the element-origin move may have
    # scrolled the page, which moves every rect including this one.
    landed = centre(driver, element)
    return {
        "at": landed,
        "glided": glided,
        "nudged": inside and away is not None,
        "unknown_start": glide and start is None,
    }


def drag_to(driver, start, end, glide=True) -> None:
    """Press where the pointer is, travel to ``end``, release.

    A short hold either side of the travel, because several drag libraries arm
    on a delay or a distance rather than on the press itself, and a press and
    release in the same frame reads as a click.
    """
    builder = ActionBuilder(driver, duration=STEP_MS)
    builder.pointer_action.pointer_down(MouseButton.LEFT)
    builder.pointer_action.pause(HOLD_MS / 1000)
    points = path(start, end) if glide else [(round(end[0]), round(end[1]))]
    for x, y in points:
        builder.pointer_action.move_to_location(x, y)
    builder.pointer_action.pause(HOLD_MS / 1000)
    builder.pointer_action.pointer_up(MouseButton.LEFT)
    builder.perform()


def viewport(driver) -> tuple[int, int]:
    """The visible area, so a destination can be kept inside it."""
    size = driver.execute_script("return [window.innerWidth, window.innerHeight];")
    return int(size[0]), int(size[1])


def clamped(point: tuple[float, float], size: tuple[int, int]) -> tuple[float, float]:
    """``point`` pulled inside the viewport.

    A pointer move to a coordinate outside it is an error, not a scroll: an
    offset drag that would leave the window has to stop at the edge, which is
    also what a hand does.
    """
    width, height = size
    return (
        min(max(point[0], 0.0), float(max(width - 1, 0))),
        min(max(point[1], 0.0), float(max(height - 1, 0))),
    )
