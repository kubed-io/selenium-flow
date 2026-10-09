"""The session monitor: which sessions are open, ended and owed, said on a bus.

See `monitor.py` for the watch, `events.py` for the bus, `bidi.py` for the held
socket, and the session monitor spec (docs/superpowers/specs/
2026-10-09-session-monitor-design.md) for why each is the way it is.
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
from .monitor import Monitor, Watch

__all__ = [
    "EVENTS",
    "BidiError",
    "BidiSocket",
    "Bus",
    "CallFinished",
    "Event",
    "LocalBus",
    "Monitor",
    "SessionEnded",
    "SessionOpened",
    "Subscription",
    "Watch",
]
