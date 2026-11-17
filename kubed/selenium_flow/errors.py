"""What a failure means: the HTTP status for every surface.

A 4xx tells a client the same request fails again; a 5xx tells it a retry may
work. An n8n node with Retry-On-Fail replays whatever answers 5xx, and an
alert on the 5xx rate counts it as an outage. The default is 500: calling an
unrecognised failure the caller's would stop a client retrying something that
may be the server's.

Over MCP, where a failed call has no status, `mcp/failures.py` uses this to
choose the log level and a resource read's error code. What a failure says,
and the package's own exception types, are in `faults.py`, which imports no
driver.
"""

from __future__ import annotations

import requests
import urllib3.exceptions
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    InsecureCertificateException,
    InvalidArgumentException,
    InvalidSelectorException,
    InvalidSessionIdException,
    JavascriptException,
    MoveTargetOutOfBoundsException,
    NoAlertPresentException,
    NoSuchDriverException,
    NoSuchElementException,
    NoSuchFrameException,
    SessionNotCreatedException,
    StaleElementReferenceException,
    TimeoutException,
)

from .core.cancel import Ended
from .faults import (
    AssertionFailed,
    BidiUnavailable,
    Conflict,
    Controlled,
    NodeLost,
    NodeRefused,
    NoNodeTook,
    NotFound,
    ScriptTimedOut,
    SocketClosed,
    TooLarge,
    Unresolved,
    Untrusted,
)

# The same request fails again, so 400 lets a workflow stop instead of burning
# its retries. A TimeoutException belongs here: every wait in this package is
# for an element, frame or dialog the caller named, and a page without
# `//nope` still lacks it on the retry. A slow Grid is a connection error.
CALLER = (
    AssertionFailed,
    Unresolved,
    Untrusted,
    ScriptTimedOut,
    InsecureCertificateException,
    TimeoutException,
    InvalidSelectorException,
    NoSuchElementException,
    NoSuchFrameException,
    NoAlertPresentException,
    InvalidArgumentException,
    JavascriptException,
    ElementNotInteractableException,
    ElementClickInterceptedException,
    MoveTargetOutOfBoundsException,
    StaleElementReferenceException,
)

# The browser is not on the Grid: a 404, whose fix a workflow can automate
# (open_session). `Ended` is `end_browser` running during this call, and
# `NodeLost` a node that stopped answering mid-call; it is read from the
# driver's error because `Grid.is_alive` just after a crash can still say alive.
GONE = (InvalidSessionIdException, NoSuchDriverException, Ended, NodeLost)

# The Grid cannot serve this now: worth a retry after a wait. Both transport
# libraries are listed: the Selenium driver reports an unreachable Grid as a
# `urllib3` error, and the plain HTTP calls (`quit`, `files`, `status`) as a
# `requests` one. `open_session` turns the Grid's queue timeout and a node's
# refusal into `NoNodeTook` and `NodeRefused`, so `SessionNotCreatedException`
# here is the Grid's own failure.
UNAVAILABLE = (
    BidiUnavailable,
    NoNodeTook,
    SocketClosed,
    SessionNotCreatedException,
    requests.ConnectionError,
    requests.Timeout,
    urllib3.exceptions.HTTPError,
)


def status_for(exc: BaseException) -> int:
    """The HTTP status that tells the truth about ``exc``."""
    if isinstance(exc, (Controlled, Conflict)):
        # The request is fine and succeeds after `await_control`, or once
        # what refused it has changed.
        return 409
    if isinstance(exc, (GONE, NotFound)):
        return 404
    if isinstance(exc, TooLarge):
        return 413
    if isinstance(exc, NodeRefused):
        return 400 if exc.blamed else 503
    if isinstance(exc, requests.HTTPError):
        # The Grid answered no, which is not a connection failure: a 404 means
        # the browser or file is gone, a 5xx is worth retrying, anything else
        # is the request's.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status is None or status >= 500:
            return 503
        return 404 if status == 404 else 400
    if isinstance(exc, UNAVAILABLE):
        return 503
    if isinstance(exc, CALLER):
        return 400
    if isinstance(exc, (TypeError, ValueError)):
        # A missing required argument, or a value an action rejected.
        return 400
    return 500
