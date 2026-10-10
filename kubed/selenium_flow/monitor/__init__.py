"""The session monitor: which sessions are open, ended and owed, said on a bus.

See `monitor.py` for the watch and the held connection, `events.py` for the
bus, `bidi/` for the connection itself, and the session monitor spec
(docs/superpowers/specs/2026-10-09-session-monitor-design.md) for why each is
the way it is.
"""

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
from .monitor import Monitor, Sink, Watch

__all__ = [
    "EVENTS",
    "Bus",
    "CallFinished",
    "Event",
    "LocalBus",
    "Monitor",
    "SessionEnded",
    "SessionOpened",
    "Sink",
    "Subscription",
    "Watch",
]
