"""Reading a value that arrived as JSON, a form field or a query string.

Callers send everything as strings, or not at all; these say what a value means
and what to do when it means nothing.
"""

from __future__ import annotations


def as_bool(value, default: bool = False) -> bool:
    """Coerce a JSON or form value to bool, so ``"false"`` is false."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value, default: int) -> int:
    """Coerce to int, falling back on anything unusable (an omitted optional
    often arrives as "")."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def seconds(value, default: float, name: str = "value") -> float:
    """A duration in seconds, from JSON that may have sent it as a string.

    Unlike `as_int`, a float, and an unreadable value raises: the fallback is
    zero, and a zero `stable_for` would silently drop the guard it adds.
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
