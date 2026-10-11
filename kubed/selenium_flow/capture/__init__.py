"""Console and network capture: what a held BiDi connection hears, kept.

The monitor holds a connection for a session opened with ``capture`` and hands
every event to `store.Capture` (its `Sink`). Capture keeps two bounded buffers
per workspace — the console and the requests — masked once, at capture, for
the buffer and the live tail alike (capture spec §4, ruling 13), and reads them
back for the resources, the admin's Captured view and a call's hint.

The package is kernel: plain Python and `cachetools`, nothing of the protocol
layers and no Selenium (`tests/test_boundaries.py`). This module is the
constants, verbatim from the spec.
"""

from __future__ import annotations

# The capture subscription and the data collector it adds (spec §2).
EVENTS = (
    "log.entryAdded",
    "network.beforeRequestSent",
    "network.responseCompleted",
    "network.fetchError",
    "browsingContext.contextCreated",
    "browsingContext.contextDestroyed",
    "browsingContext.navigationStarted",
    "browsingContext.navigationCommitted",
    "browsingContext.historyUpdated",
    "browsingContext.fragmentNavigated",
)
COLLECTOR = {
    "dataTypes": ["response"],
    "maxEncodedDataSize": 10_000_000,
    "collectorType": "blob",
}

# Buffer sizes and expiry (ruling 10).
CONSOLE_LIMIT = 1000
REQUEST_LIMIT = 1000
NAVIGATIONS = 5
DEFAULT_TTL = 10_800

# Caps on what one entry may hold (spec §4), the tail's queue (ruling 2) and a
# fetched body (ruling 11).
TEXT_CAP = 4096
HEADER_LIMIT = 32
HEADER_VALUE_CAP = 4096
# A header's name, and the bytes of names and values one side of a request may
# keep: a hostile page and its server choose how many large headers there are,
# and 32 of 4 kB a side, 1 000 rows a workspace, is half a gigabyte.
HEADER_NAME_CAP = 256
HEADER_BYTES = 16_384
TAIL_QUEUE = 500
BODY_CAP = 100_000

REPLAY_DEPTH = 100  # Chrome replays the last 100 per browsing context (measured)
WORKSPACES = 256  # plan ruling 15

# Masking (ruling 13).
MASK = "•••"
MASKED_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie"}
)
# A value one of those headers (or a request cookie) carried is masked wherever
# else it appears — a page can echo it. Each is kept with every spelling an echo
# gives it (its forms), and every form costs every masked text a scan, so what a
# session learns is a budget of forms and of their characters, in two tiers: an
# Authorization token's, which cookies cannot evict, the least recently seen
# going first; and the cookies', which past the budget holds the cookies that
# rank highest (`masking._Ranked`), one not sent for COOKIE_STALE events
# ranking below every one that was, each cookie domain sure of 1/COOKIE_SHARE
# of it against the others. A value is learned only when it can be a secret:
# an Authorization token of CREDENTIAL_MIN or more, a cookie of COOKIE_MIN or
# more, neither only letters nor only digits ("accepted", "undefined", a
# timestamp), none over CREDENTIAL_MAX (counted instead), none holding a NUL.
AUTH_FORMS = 16
COOKIE_FORMS = 48
FORM_CHARS = 32_768  # per tier, past the one value it may be over by
CREDENTIAL_MIN = 8
COOKIE_MIN = 16
CREDENTIAL_MAX = 4096
COOKIE_STALE = 64  # events: a request is two or three (cookies, headers, response)
COOKIE_SHARE = 2  # each cookie domain is sure of 1/2 of the cookie tier
COOKIE_TAKES = 8  # new cookies an event learns at most, the highest-ranked

# Counted, not kept, when they succeed (ruling 3); counted in the hint when they
# fail (ruling 12).
STATIC_TYPES = frozenset({"stylesheet", "image", "font", "script", "media"})
HINTED_TYPES = frozenset({"document", "fetch", "xhr"})

# What a reader may ask for.
LEVELS = ("all", "error", "warn")
FILTERS = ("all", "failed", "xhr")
MAX_PAGES = NAVIGATIONS

CONSOLE_URI = "workspace://console"
NETWORK_URI = "workspace://network"

# A capture resource of a workspace that does not capture says so; it is not an
# error (spec §7).
OFF = {"capture": False, "how": "open_session(capture=true)"}
