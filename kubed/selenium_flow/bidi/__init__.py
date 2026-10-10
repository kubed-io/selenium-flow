"""The one WebDriver BiDi client: a `Connection` per browser, held on an event
loop, and the `Channel` worker threads drive it through.

Capture, site data and the session monitor all speak BiDi through this package
(capture spec, ruling 1). It sits below every other layer: no protocol
library, no Selenium (`tests/test_boundaries.py`).
"""

from .channel import CHANNEL_DOWN, Channel, Channels
from .connection import (
    FLUSH_GRACE,
    LAGS_KEPT,
    MAX_MESSAGE,
    OPEN_TIMEOUT,
    PING_INTERVAL,
    QUEUE_LIMIT,
    REPLY_TIMEOUT,
    BidiError,
    Connection,
    Handler,
    Subscriber,
)

__all__ = [
    "CHANNEL_DOWN",
    "FLUSH_GRACE",
    "LAGS_KEPT",
    "MAX_MESSAGE",
    "OPEN_TIMEOUT",
    "PING_INTERVAL",
    "QUEUE_LIMIT",
    "REPLY_TIMEOUT",
    "BidiError",
    "Channel",
    "Channels",
    "Connection",
    "Handler",
    "Subscriber",
]
