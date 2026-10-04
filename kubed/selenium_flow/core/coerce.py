"""Reading a value that arrived as JSON, a form field or a query string.

Callers send everything as strings, or not at all; these say what a value means
and what to do when it means nothing.
"""

from __future__ import annotations


def as_bool(value, default: bool = False) -> bool:
    """Coerce a JSON or form value to bool.

    Callers that send everything as strings would otherwise make ``"false"``
    true, because every non-empty string is truthy in Python.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value, default: int) -> int:
    """Coerce to int, falling back on anything unusable.

    An omitted optional parameter often arrives as an empty string, and
    ``int("")`` raises.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def seconds(value, default: float, name: str = "value") -> float:
    """A duration in seconds, from JSON that may have sent it as a string.

    `as_int` cannot serve here: half a second is a sensible stability window and
    `int("0.5")` raises. Wider type, and deliberately **less** forgiving.

    "Coerce, don't trust" is the rule everywhere else because a fallback there
    is harmless - a `wait_timeout` that cannot be read becomes the default wait,
    and the call still waits. This one is different in kind: the fallback is
    zero, and zero means *the stability check does not happen*. A typo would
    quietly take away the guard the argument exists to add, and the assertion
    would then pass on the transient it was written to reject (Copilot, #31).
    """
    if value is None or value == "":
        return default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be a number of seconds; got {value!r}"
        ) from None
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be a finite number of seconds")
    if seconds < 0:
        raise ValueError(f"{name} cannot be negative; got {seconds}")
    return seconds
