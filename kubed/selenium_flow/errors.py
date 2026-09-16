"""What a failure means, decided in one place.

An HTTP status is a contract with a *machine*, not a mood. ``4xx`` says "your
request is wrong and sending it again will not help"; ``5xx`` says "we are
broken and retrying might work". Getting that backwards is not cosmetic: an n8n
node with Retry-On-Fail will dutifully replay a mistyped XPath three times, and
an alert on the 5xx rate counts somebody's typo as an outage.

Everything below the auth check used to be a 500. A wait that timed out because
the element was never there, a malformed XPath, a window sized ``-5``, a
``session_id`` naming a browser that ended an hour ago — all of them reported as
"this server failed", which is the one thing none of them are.

Selenium raises nearly everything as a subclass of ``WebDriverException``, so
the mapping has to be explicit. **The default stays 500**, deliberately: for a
failure we do not recognise, "it is the caller's fault" is the dangerous guess,
because it tells a client to stop retrying something that may well be ours.

The MCP surface is unaffected — a tool raises and FastMCP reports the message.
Only HTTP has status codes to get right.
"""

from __future__ import annotations

import re
import traceback

import requests
import urllib3.exceptions
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
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


# The caller asked for something that cannot happen as asked. Retrying the
# identical request is guaranteed to fail again, so say 400 and let a workflow
# stop rather than burn its retries.
#
# TimeoutException is the one worth justifying, because "it timed out" sounds
# transient. Here it is not: every wait in this package is for an element, a
# frame or a dialog that the *caller named*, and `browser._waited` re-raises it
# saying which locator and which URL. A page that never contained `//nope` will
# still not contain it on the retry. A slow Grid surfaces as a connection error
# instead, which is below.
class AssertionFailed(Exception):
    """An `assert` step's JavaScript came back false.

    Its own type because it is not a browser fault and not a bad request: the
    page is simply not what the flow said it must be. It carries the author's
    message, which is the whole point of the tool — the flow's author knows why
    the condition matters and this package does not.
    """


CALLER = (
    AssertionFailed,
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

# The browser named in the request is not on the Grid — ended, reaped, or never
# real. Its own status because the fix is specific and a workflow can automate
# it: call /browser/open and carry on. Lumped into 400 it is indistinguishable
# from a bad XPath, which needs a human.
GONE = (InvalidSessionIdException, NoSuchDriverException)

# The Grid cannot serve this right now: unreachable, or out of free slots. Both
# are worth retrying after a wait, which is exactly what 503 means, and neither
# is a fault in the request. `/ready` already answers 503 for the same reason.
#
# Two transport libraries, because this package talks to the Grid two ways and
# they fail differently — measured, not assumed: the Selenium driver surfaces an
# unreachable Grid as a bare `urllib3.exceptions.MaxRetryError`, while the plain
# HTTP calls (`quit`, `files`, `status`) raise `requests.ConnectionError`. Only
# the second was classified at first, so the same unreachable Grid answered 503
# on two endpoints and 500 on the other twelve.
UNAVAILABLE = (
    SessionNotCreatedException,
    requests.ConnectionError,
    requests.Timeout,
    urllib3.exceptions.HTTPError,
)


# The userinfo of a URL. `GRID_URL` may carry credentials, and an exception's
# text is quoted into logs and into the error a caller reads — so it is stripped
# from every message, not only from the one failure known to print a URL
# (Copilot, #36). `core.browser` imports this rather than keeping a second copy.
#
# Anchored to a scheme, and blind to brackets. Unanchored, `//` then `@` is also
# an XPath attribute test: `//input[@name='q']` came out as `//name='q']` in
# every timeout that quoted one, which is most of them. A URL's userinfo can
# contain neither `[` nor `]`, and an XPath has no `scheme:` before its `//`.
USERINFO = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*:)//(?P<userinfo>[^/@\s\[\]]*)@"
)


def without_userinfo(text: str) -> str:
    """``text`` with the credentials of every URL in it removed."""
    return USERINFO.sub(r"\g<scheme>//", text)


def formatted(exc: BaseException) -> str:
    """The traceback, with any credential stripped out of it.

    `log.exception` writes the frames verbatim, and a requests or urllib3
    failure quotes the whole Grid URL — userinfo included — inside them. So the
    sanitising that :func:`message` does for what a caller reads has to happen
    for what the logger writes as well (Copilot, #36).
    """
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return without_userinfo(text)


def status_for(exc: BaseException) -> int:
    """The HTTP status that tells the truth about ``exc``."""
    if isinstance(exc, GONE):
        return 404
    if isinstance(exc, requests.HTTPError):
        # The Grid answered, and its answer was no. Every plain HTTP call to it
        # — `files`, `read_file`, `status` — reports that through
        # `raise_for_status`, and this is NOT a connection failure: it must not
        # fall into UNAVAILABLE below and be called a 503.
        #
        # A 404 from the Grid means the browser or the file is gone, which is
        # the same diagnosis `GONE` carries and has the same fix. Its own 5xx is
        # worth retrying. Anything else it refuses is the request's problem.
        # Without this branch a reaped browser answered 500 while the published
        # /files contract promised 404.
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


def message(exc: BaseException) -> str:
    """One actionable line, without the driver's internals.

    Selenium's ``str()`` is ``"Message: <what happened>\\nStacktrace:\\n"``
    followed by twenty lines of ``chrome://remote/...``. The first line is the
    whole of the useful part, and the rest is noise in a JSON field a model or a
    workflow has to read. The bare ``Message:`` prefix is the same artefact
    ``AGENTS.md`` already calls out as useless on its own.

    Never returns an empty string: an error with no text at all is worse than a
    class name, which at least says what kind of thing went wrong.

    A Grid refusal is cut short deliberately. ``raise_for_status`` formats its
    message as ``"404 Client Error: Not Found for url: <the full URL>"``, and
    ``GRID_URL`` may carry credentials in its userinfo — so that string would
    hand the Grid's credential to whoever made the request, and write it to the
    log besides. The status and reason are the whole of the useful part. Done
    here rather than in each of the three handlers, for the reason this module
    exists: one place decides what a failure says.
    """
    if isinstance(exc, requests.HTTPError):
        text = str(exc).split(" for url:", 1)[0].strip()
        return without_userinfo(text) or "the grid refused the request"
    text = str(getattr(exc, "msg", None) or exc)
    text = text.split("Stacktrace:", 1)[0].strip()
    if text.lower().startswith("message:"):
        text = text[len("message:") :].strip()
    # A connection failure quotes the whole URL — `status_for` calls those 503
    # and nothing truncated them, so the credential travelled with the message.
    return without_userinfo(text) or type(exc).__name__
