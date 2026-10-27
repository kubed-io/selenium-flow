"""The live view's audit line: who watched which browser, and for how long.

A person looking at a browser, and above all one typing into it, is something
an operator may be asked about afterwards (spec 2026-10-27, "The audit line").
This is the one module that logs for the live view, so the rule that no line
carries a frame, a keystroke, pasted text, the password or a Grid id is held in
one place: a line names the viewer, the workspace and the session's start time,
which is what a person can recognise where a Grid id is a secret.
"""

from __future__ import annotations

import logging
import time

from ..principal import Principal

log = logging.getLogger("kubed.selenium_flow.live")

NAME_CAP = 64
REASON_CAP = 200  # a reason can come from the node or the viewer


def clean(name: str, cap: int = NAME_CAP) -> str:
    """Text safe to put in a log line: control characters become ``?``, cut to `cap`."""
    return "".join("?" if not c.isprintable() else c for c in name)[:cap]


def viewer_of(principal: Principal | None) -> str:
    """Who is watching, as one line names them."""
    if principal is None:
        return "open"
    if principal.admin:
        return "admin"
    return clean(principal.username or principal.subject or "oidc")


def session_label(opened_at: float) -> str:
    """The session, by the time it started; 0 when that is not known."""
    if not opened_at:
        return "an unknown session"
    return "session started " + time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(opened_at)
    )


def duration(seconds: float) -> str:
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def opened(who: str, workspace: str, session: str, mode: str) -> None:
    log.info(
        "live: %s opened the live view of %s (%s) in %s",
        who,
        clean(workspace),
        session,
        mode,
    )


def switched(who: str, workspace: str, session: str, mode: str) -> None:
    log.info(
        "live: %s switched to %s in %s (%s)",
        who,
        mode,
        clean(workspace),
        session,
    )


def pasted(who: str, workspace: str, session: str, characters: int) -> None:
    """A paste, by its length: never the text."""
    log.info(
        "live: %s pasted %d characters into %s (%s)",
        who,
        characters,
        clean(workspace),
        session,
    )


def left(
    who: str, workspace: str, session: str, seconds: float, mode: str, reason: str
) -> None:
    log.info(
        "live: %s left the live view of %s (%s) after %s in %s: %s",
        who,
        clean(workspace),
        session,
        duration(seconds),
        mode,
        clean(reason, REASON_CAP),
    )


def refused(who: str | None, workspace: str, why: str) -> None:
    log.warning(
        "live: refused %s the live view of %s: %s",
        who or "someone",
        clean(workspace),
        clean(why, REASON_CAP),
    )


def unreadable(path: str) -> None:
    """The configured VNC password file cannot be read: the path is
    configuration, never the password."""
    log.warning(
        "live: the VNC password file %s cannot be read", clean(path, REASON_CAP)
    )


def failed(workspace: str, error: BaseException) -> None:
    """An unexpected failure, by its type alone: its message may quote a URL."""
    log.warning(
        "live: the live view of %s failed: %s", clean(workspace), type(error).__name__
    )
