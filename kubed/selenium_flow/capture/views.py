"""What a reader gets back: a window of the last navigations, filtered, newest
first — and the checks on what a reader may ask for.

Called by `store.Capture` under its lock. Every row handed out is a copy, so
the buffer can go on changing under a caller serialising it on another thread.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import FILTERS, LEVELS, MAX_PAGES, NETWORK_URI

if TYPE_CHECKING:
    from .store import Buffer

# What only a request's own detail carries: its headers, and how many of them
# were too many to keep.
HEADERS = ("request_headers", "response_headers", "headers_dropped")

PAGES = f"pages is 1 to {MAX_PAGES}: how many of the last navigations to show"


def check_level(level) -> str:
    """``all``, ``error`` or ``warn``, read loosely; omitted is ``all``."""
    return _choice(level, LEVELS, "level")


def check_filter(value) -> str:
    """``all``, ``failed`` or ``xhr``, read loosely; omitted is ``all``."""
    return _choice(value, FILTERS, "filter")


def check_pages(value) -> int:
    """How many of the last navigations: 1 to 5, ``"3"`` read as 3, omitted
    as 1. Anything else is refused rather than guessed at."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 1
    if isinstance(value, bool):  # an int to Python, never a count to a caller
        raise ValueError(PAGES)
    try:
        pages = int(str(value).strip())
    except ValueError:
        raise ValueError(PAGES) from None
    if not 1 <= pages <= MAX_PAGES:
        raise ValueError(PAGES)
    return pages


def _choice(value, allowed: tuple[str, ...], name: str) -> str:
    chosen = "" if value is None else str(value).strip().lower()
    if not chosen:
        return allowed[0]
    if chosen not in allowed:
        raise ValueError(f"{name} is one of {', '.join(allowed)}")
    return chosen


def window(buffer: Buffer, pages: int) -> set[int]:
    """The navigations in view: the last ``pages`` marked, or nav 0 before the
    first navigation."""
    if buffer.nav == 0:
        return {0}
    return {m["nav"] for m in buffer.marks if m["nav"] > buffer.nav - pages}


def _pages(buffer: Buffer, navs: set[int]) -> list[dict]:
    return [dict(m) for m in reversed(buffer.marks) if m["nav"] in navs]


def console(buffer: Buffer, level: str, pages: int) -> dict:
    navs = window(buffer, pages)
    kept = [e for e in reversed(buffer.console) if e["nav"] in navs]
    if level == "error":
        shown = [e for e in kept if _is_error(e)]
    elif level == "warn":
        shown = [e for e in kept if e["level"] == "warn"]
    else:
        shown = kept
    return {
        "capture": True,
        "pages": _pages(buffer, navs),
        "entries": [dict(e) for e in shown],
        "count": len(shown),
        "total": len(kept),
        # What the entries hold, counted here: a view cut to the newest rows
        # (`show`) would otherwise count only those.
        "tally": {
            "errors": sum(1 for e in shown if _is_error(e)),
            "warnings": sum(1 for e in shown if e["level"] == "warn"),
        },
        "not_kept": {"debug": sum(buffer.debug.get(n, 0) for n in navs)},
        **lost(buffer),
    }


def network(buffer: Buffer, filter: str, pages: int) -> dict:
    navs = window(buffer, pages)
    kept = sorted(
        (r for r in buffer.requests if r["nav"] in navs),
        key=lambda r: r["seq"],
        reverse=True,
    )
    if filter == "failed":
        shown = [r for r in kept if _is_failed(r)]
    elif filter == "xhr":
        shown = [r for r in kept if _is_xhr(r)]
    else:
        shown = kept
    return {
        "capture": True,
        "pages": _pages(buffer, navs),
        "requests": [without_headers(r) for r in shown],
        "count": len(shown),
        "total": len(kept),
        # As the console's: what the requests hold, whatever a view draws.
        "tally": {
            "failed": sum(1 for r in shown if _is_failed(r)),
            "xhr": sum(1 for r in shown if _is_xhr(r)),
        },
        "not_kept": {"static": sum(buffer.static.get(n, 0) for n in navs)},
        **lost(buffer),
    }


def _is_error(entry: dict) -> bool:
    return entry["level"] == "error" or entry["type"] == "javascript"


def _is_failed(row: dict) -> bool:
    return row["failed"] or (row["status"] or 0) >= 400


def _is_xhr(row: dict) -> bool:
    return row["type"] in ("xhr", "fetch")


def request(buffer: Buffer, id: str) -> dict | None:
    """One kept request in full, headers included."""
    for row in buffer.requests:
        if row["id"] == id:
            return {**row, "uri": f"{NETWORK_URI}/{id}"}
    return None


def counts(buffer: Buffer) -> dict:
    """What was kept since the last navigation (plan ruling 12): console
    counts kept entries, network every request seen, static included. Its
    ``dropped`` (`lost`) is the exception: since the buffer began."""
    nav = buffer.nav
    return {
        "console": sum(1 for e in buffer.console if e["nav"] == nav),
        "network": buffer.seen.get(nav, 0),
        "errors": buffer.errors.get(nav, 0),
        "failed_requests": buffer.failed.get(nav, 0),
        **lost(buffer),
    }


def lost(buffer: Buffer) -> dict:
    """``dropped``: the events the browser sent that capture never heard, so
    any view may be missing them; ``unmasked``: the credentials too long to
    learn, so any view may show them. Each present only when there were
    some, and counted since the buffer began, not per navigation, unlike
    every count beside them: neither can be placed on a page."""
    counted = {"dropped": buffer.dropped, "unmasked": buffer.unmasked}
    return {name: n for name, n in counted.items() if n}


def without_headers(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in HEADERS}
