"""Where the mouse pointer is, per browser, and how it gets somewhere else.

WebDriver has no "where is the pointer" command, but nothing else moves its
pointer, so the sender of every move remembers where it ended up.

Two gestures need the start. A glide (many small moves) jumps when the start is
unknown and says so: a path from a guessed origin crosses the wrong elements. A
nudge (moving away before moving back) makes a hover fire on a target the
pointer is already inside; otherwise Chrome and Firefox deliver no `mouseover`.

Keyed by the Grid's session id, because a new session is a new browser with a
pointer of its own. Kept in Redis when Redis is configured, since this process
restarts and the browser does not.
"""

from __future__ import annotations

import json
import logging
import time

from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.mouse_button import MouseButton

from ..faults import Traceback
from . import js

log = logging.getLogger(__name__)

# Long enough to outlive any browser; a stale entry costs one wrong glide origin.
DEFAULT_TTL_SECONDS = 86400
DEFAULT_PREFIX = "selenium-flow:pointer:"

# The path: about one step per 20px, clamped at both ends, with a frame-length
# pause between steps, all in one `perform()`.
PIXELS_PER_STEP = 20
MIN_STEPS = 6
MAX_STEPS = 40
STEP_MS = 16

# How far a nudge moves before coming back.
NUDGE = 40

# How long the pointer rests between pressing and moving in a drag (`drag_to`).
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
        # An unreachable Redis means the position is unknown, never a failure.
        except Exception as exc:  # noqa: BLE001 - see the comment above
            log.debug("could not read the pointer:\n%s", Traceback(exc))
            return None

    def set(self, session_id: str, x: float, y: float) -> None:
        try:
            self._client.set(self._k(session_id), json.dumps([x, y]), ex=self._ttl)
        except Exception as exc:  # noqa: BLE001 - see `get`
            log.debug("could not record the pointer:\n%s", Traceback(exc))

    def forget(self, session_id: str) -> None:
        try:
            self._client.delete(self._k(session_id))
        except Exception as exc:  # noqa: BLE001 - see `get`
            log.debug("could not clear the pointer:\n%s", Traceback(exc))


def matching(store):
    """A pointer store on the same backend as the workspace ``store``.

    Built from the store object, not the environment, so an injected shared
    store never gets process-local pointers. ``kind`` and ``client`` are read
    directly: ``getattr`` with a default would swallow an AttributeError raised
    inside the property and degrade silently to memory.
    """
    # An injected store may predate `ttl` in the `WorkspaceStore` protocol;
    # there the default is a real answer.
    ttl = getattr(store, "ttl", DEFAULT_TTL_SECONDS)
    if store.kind != "redis":
        return MemoryPointers(ttl=ttl)
    from ..workspace.store import POINTER_NAMESPACE

    return RedisPointers(store.client, prefix=store.prefix + POINTER_NAMESPACE, ttl=ttl)


# The in-view center point, WebDriver's definition of where an element-origin
# move lands: the center of the element's rectangle intersected with the
# viewport. For an element taller than the window the raw center can be far
# below the fold.
CENTER_JS = js.read("center.js")


def aim(driver, element, bring_into_view: bool = False) -> dict:
    """Where a pointer move onto ``element`` lands, whether one can, and the
    window's size to keep the move inside.

    The in-view center (`CENTER_JS`), in viewport coordinates, read at the
    moment of the move: scrolling moves every element under the pointer.

    ``bring_into_view`` scrolls it to the middle of the window first if none of
    it is in there, which a glide needs: a move to a coordinate outside the
    viewport is an error, not a scroll. A jump names the element and scrolls on
    its own.
    """
    answer = driver.execute_script(CENTER_JS, element, bool(bring_into_view))
    at = answer.get("at") or [0, 0]
    # Strict: a guessed size would clamp a drop to the wrong place silently.
    size = answer["viewport"]
    return {
        "at": (float(at[0]), float(at[1])),
        "scrolled": bool(answer.get("scrolled")),
        "outside": bool(answer.get("outside")),
        "viewport": (int(size[0]), int(size[1])),
    }


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
    """A straight, eased line from ``start`` to ``end``, ending exactly on it."""
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


# One round trip: is the pointer already inside the destination, and if so
# where to step to first.
NUDGE_JS = js.read("nudge.js")


def _nudge_point(driver, element, start) -> tuple[bool, tuple[int, int] | None]:
    """Whether the pointer is already inside ``element``, and where to step."""
    at = (None, None) if start is None else start
    try:
        answer = driver.execute_script(NUDGE_JS, element, at[0], at[1], NUDGE) or {}
    # Best effort: not knowing costs one hover that does nothing.
    except Exception as exc:  # noqa: BLE001 - see the comment above
        log.debug("could not work out a nudge point:\n%s", Traceback(exc))
        return False, None
    away = answer.get("away")
    return bool(answer.get("inside")), ((int(away[0]), int(away[1])) if away else None)


def move(driver, element, start=None, glide=False) -> dict:
    """Put the pointer on ``element``, say how it got there, and the window's
    size as it was read on landing.

    The last move is always WebDriver's element-origin move, which scrolls the
    element into view. A glide adds intermediate points before it, so an
    off-screen destination still works.
    """
    inside, away = _nudge_point(driver, element, start)
    builder = ActionBuilder(driver, duration=STEP_MS)
    unglideable = False
    if inside and away is not None:
        builder.pointer_action.move_to_location(away[0], away[1])
        start = (float(away[0]), float(away[1]))

    glided = False
    if glide and start is not None:
        # Every point on the path is a coordinate, refused outside the window.
        aimed = aim(driver, element, bring_into_view=True)
        if aimed["outside"]:
            # An element taller than the window, or one a scroll container
            # cannot center. A jump still works: it names the element.
            unglideable = True
        else:
            # The window may have been resized since the pointer was left there;
            # a path between two points inside the viewport stays inside it.
            begin = clamped(start, aimed["viewport"])
            for x, y in path(begin, aimed["at"])[:-1]:
                builder.pointer_action.move_to_location(x, y)
            glided = True
    builder.pointer_action.move_to(element)
    builder.perform()

    # Read after the move, which may have scrolled the page.
    landed = aim(driver, element)
    return {
        "at": landed["at"],
        "viewport": landed["viewport"],
        "glided": glided,
        "nudged": inside and away is not None,
        "unknown_start": glide and start is None,
        "unglideable": unglideable,
    }


def drag_to(driver, start, end, glide=True) -> None:
    """Press where the pointer is, travel to ``end``, release.

    A short hold either side of the travel: several drag libraries arm on a
    delay or a distance, and a press and release in one frame reads as a click.
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


def clamped(point: tuple[float, float], size: tuple[int, int]) -> tuple[float, float]:
    """``point`` pulled inside the viewport, where a pointer move outside it is
    an error, not a scroll."""
    width, height = size
    return (
        min(max(point[0], 0.0), float(max(width - 1, 0))),
        min(max(point[1], 0.0), float(max(height - 1, 0))),
    )
