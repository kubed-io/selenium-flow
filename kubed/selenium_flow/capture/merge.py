"""From BiDi events to rows: a console entry, a request's type and duration,
what is kept and what the hint counts, and the key Chrome's replay is aligned
on."""

from __future__ import annotations

from . import HINTED_TYPES, STATIC_TYPES, TEXT_CAP
from .masking import Masker

# (realm, timestamp, type, level, text, first frame) — ruling 9.
Key = tuple[str, float, str, str, str, str]

_DESTINATIONS = {
    "document": "document",
    "iframe": "document",
    "frame": "document",
    "style": "stylesheet",
    "image": "image",
    "font": "font",
    "script": "script",
    "worker": "script",
    "sharedworker": "script",
    "serviceworker": "script",
    "audio": "media",
    "video": "media",
    "track": "media",
}
_INITIATORS = {"fetch": "fetch", "xmlhttprequest": "xhr"}


def _first_frame(params: dict) -> dict | None:
    frames = (params.get("stackTrace") or {}).get("callFrames") or ()
    first = frames[0] if frames else None
    return first if isinstance(first, dict) else None


def entry_key(params: dict) -> Key:
    """What identifies a console entry across Chrome's replay, read from the raw
    event before masking (ruling 9). Identical keys are legitimate entries."""
    frame = _first_frame(params)
    where = (
        f"{frame.get('url')}:{frame.get('lineNumber')}:{frame.get('columnNumber')}"
        if frame
        else ""
    )
    return (
        str((params.get("source") or {}).get("realm") or ""),
        params.get("timestamp"),
        str(params.get("type") or ""),
        str(params.get("level") or ""),
        str(params.get("text") or ""),
        where,
    )


def console_entry(params: dict, masker: Masker) -> dict:
    """A ``log.entryAdded`` as a console row, masked and capped.

    The text is masked before it is cut, so a secret straddling the cut is
    still gone. ``args`` are never kept: their text is already in ``text``.
    """
    frame = _first_frame(params)
    source = None
    if frame is not None:
        line = frame.get("lineNumber")
        source = {
            "url": masker.url(str(frame.get("url") or ""), keep_query=True),
            "line": line + 1 if isinstance(line, int) else None,
        }
    return {
        "level": str(params.get("level") or "info")[:TEXT_CAP],
        "type": str(params.get("type") or "console")[:TEXT_CAP],
        "text": masker.text(str(params.get("text") or "")),
        "source": source,
        "context": (params.get("source") or {}).get("context"),
        "at": seconds(params.get("timestamp")),
    }


def resource_type(request: dict, mime: str = "", navigation: bool = False) -> str:
    """What a request fetched: from the navigation, the fetch destination, the
    initiator, then the MIME type; ``"other"`` when none of them says."""
    if navigation:
        return "document"
    kind = _DESTINATIONS.get(str(request.get("destination") or "").lower())
    if kind:
        return kind
    kind = _INITIATORS.get(str(request.get("initiatorType") or "").lower())
    if kind:
        return kind
    mime = str(mime or "").split(";", 1)[0].strip().lower()
    if mime == "text/css":
        return "stylesheet"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith(("font/", "application/font")):
        return "font"
    if "javascript" in mime:
        return "script"
    if mime.startswith(("audio/", "video/")):
        return "media"
    if mime == "text/html":
        return "document"
    return "other"


def counts_in_hint(row: dict) -> bool:
    """A failed or 4xx/5xx document, fetch or xhr: what a call's hint counts
    (ruling 12). A failed image is kept but never hinted (programme R10)."""
    return row["type"] in HINTED_TYPES and (
        row["failed"] or (row["status"] or 0) >= 400
    )


def is_static_success(row: dict) -> bool:
    """A stylesheet, image, font, script or media that answered 2xx or 3xx:
    counted, not kept (ruling 3)."""
    return (
        row["type"] in STATIC_TYPES
        and not row["failed"]
        and row["status"] is not None
        and 200 <= row["status"] < 400
    )


# The longest a request is believed to take: past it, or below zero, two
# numbers on different clocks were subtracted, and None is the true answer.
TOOK_MAX_MS = 3_600_000


def took_ms(params: dict, begin) -> int | None:
    """How long a request took, from its end event's ``request.timings``.

    One formula serves both browsers: start = ``timeOrigin + requestTime``,
    end = ``timeOrigin + responseEnd`` (Chrome's origin is the epoch and the
    rest relative to it; Firefox's origin is 0 and the rest epoch). Only when
    ``responseEnd`` is 0 or absent is it the end event's ``timestamp`` less
    the start's ``begin`` — and since Chrome stamps every event of a request
    with its start, a fallback of 0 says nothing: None, never a false 0.
    A result below zero or past `TOOK_MAX_MS` mixed two time bases: None.
    """
    timings = (params.get("request") or {}).get("timings")
    timings = timings if isinstance(timings, dict) else {}
    end = timings.get("responseEnd")
    start = timings.get("requestTime")
    if number(end) and end > 0 and number(start):
        origin = timings.get("timeOrigin")
        origin = origin if number(origin) else 0
        took = round((origin + end) - (origin + start))
        return took if 0 <= took <= TOOK_MAX_MS else None
    stamp = params.get("timestamp")
    if not (number(stamp) and number(begin)):
        return None
    took = round(stamp - begin)
    return took if 0 < took <= TOOK_MAX_MS else None


def number(value) -> bool:
    """A JSON number: an int or a float, never a bool."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def seconds(timestamp) -> float | None:
    """A BiDi timestamp (epoch milliseconds) in seconds."""
    return timestamp / 1000 if number(timestamp) else None
