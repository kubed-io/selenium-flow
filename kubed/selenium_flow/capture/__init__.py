"""Console and network capture: what a held BiDi connection hears, kept.

The monitor holds a connection for a session opened with ``capture`` and hands
every event to `store.Capture` (its `Sink`). Capture keeps two bounded buffers
per workspace, the console and the requests, masked once at capture for the
buffer and the live tail alike, and reads them back for the resources, the
admin's Captured view and a call's hint.

The package imports no protocol layer and no Selenium
(`tests/test_boundaries.py`). This module holds the constants the spec names.
"""

from __future__ import annotations

# The capture subscription and the data collector it adds.
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

CONSOLE_LIMIT = 1000
REQUEST_LIMIT = 1000
NAVIGATIONS = 5
DEFAULT_TTL = 10_800

# Caps on what one entry may hold, the tail's queue and a fetched body.
TEXT_CAP = 4096
HEADER_LIMIT = 32
HEADER_VALUE_CAP = 4096
# A header's name, and the bytes of names and values one side of a request may
# keep: a hostile page and its server choose how many large headers there are,
# and 32 of 4 kB a side, 1000 rows a workspace, is half a gigabyte.
HEADER_NAME_CAP = 256
HEADER_BYTES = 16_384
TAIL_QUEUE = 500
BODY_CAP = 100_000

REPLAY_DEPTH = 100  # Chrome replays the last 100 per browsing context (measured)
WORKSPACES = 256

# While the secrets in scope on a context's new origin are read, its events
# wait raw and are masked once they land: at most PENDING_EVENTS per context,
# and none older than PENDING_SECONDS when the next arrives. What either bound
# drops is counted, never kept raw.
PENDING_EVENTS = 200
PENDING_SECONDS = 10

MASK = "•••"
MASKED_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie"}
)
# A value one of those headers (or a request cookie) carried is masked wherever
# else it appears, since a page can echo it, in every spelling an echo gives it
# (its forms). Every form costs every masked text a scan, so what a session
# learns is a budget of forms and of their characters, in two tiers: the
# Authorization tokens', which cookies cannot evict, least recently seen going
# first; and the cookies', which holds the highest ranked (`masking._Ranked`).
# A value is learned only when it can be a secret: a token of CREDENTIAL_MIN or
# more, a cookie of COOKIE_MIN or more, neither only letters nor only digits
# ("accepted", "undefined", a timestamp), with no NUL, and none over
# CREDENTIAL_MAX (AUTH_MAX for a token spelt as RFC 6750's b64token); one too
# long is counted once as ``unmasked``.
AUTH_FORMS = 16
COOKIE_FORMS = 48
FORM_CHARS = 32_768  # per tier, past the one value it may be over by
CREDENTIAL_MIN = 8
COOKIE_MIN = 16
CREDENTIAL_MAX = 4096  # a cookie: the browser's own limit
AUTH_MAX = 16_384  # a b64token: Entra ID's, with group claims, run to 5-10 kB
COOKIE_STALE = 64  # events: a request is two or three (cookies, headers, response)
COOKIE_SHARE = 2  # each cookie domain is sure of 1/2 of the cookie tier
COOKIE_TAKES = 8  # new cookies an event learns at most, the highest-ranked

# Counted, not kept, when they succeed. HINTED_TYPES count in the hint when
# they fail.
STATIC_TYPES = frozenset({"stylesheet", "image", "font", "script", "media"})
HINTED_TYPES = frozenset({"document", "fetch", "xhr"})

# What a reader may ask for.
LEVELS = ("all", "error", "warn")
FILTERS = ("all", "failed", "xhr")
MAX_PAGES = NAVIGATIONS

CONSOLE_URI = "workspace://console"
NETWORK_URI = "workspace://network"
# Where a request id is listed, for an error that cannot know the session asked in
# (a named one's name is never put in an error's text).
NETWORK_LISTINGS = (
    f"{NETWORK_URI} lists them for your own session, "
    "session://{name}/network for a named one"
)


def network_request_at(id: str) -> str:
    """Where request ``id`` is read, for an error that cannot name the session."""
    return (
        f"{NETWORK_URI}/{id} for your own session, "
        f"session://{{name}}/network/{id} for a named one"
    )

# What every capture resource of a workspace that does not capture answers.
OFF = {"capture": False, "how": "open_session(capture=true)"}
