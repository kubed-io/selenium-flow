"""The one WebDriver BiDi client: a `Connection` per browser, held on an event
loop, and the `Channel` worker threads drive it through.

Capture, site data and the session monitor all speak BiDi through this package.
It imports no protocol layer and no Selenium (`tests/test_boundaries.py`).
"""

from __future__ import annotations

from .channel import CHANNEL_DOWN, Channel, Channels
from .connection import REPLY_TIMEOUT, BidiError, Connection, MessageTooBig

__all__ = [
    "CHANNEL_DOWN",
    "REPLY_TIMEOUT",
    "BidiError",
    "Channel",
    "Channels",
    "Connection",
    "MessageTooBig",
]
