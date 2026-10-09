"""The session monitor: which sessions are open, ended and owed, said on a bus.

See `events.py` for the bus, and the session monitor spec
(docs/superpowers/specs/2026-10-09-session-monitor-design.md) for why it is
the way it is.
"""

from .bidi import BidiError, BidiSocket
from .events import (
    EVENTS,
    Bus,
    CallFinished,
    Event,
    LocalBus,
    SessionEnded,
    SessionOpened,
    Subscription,
)

__all__ = [
    "EVENTS",
    "BidiError",
    "BidiSocket",
    "Bus",
    "CallFinished",
    "Event",
    "LocalBus",
    "SessionEnded",
    "SessionOpened",
    "Subscription",
]
