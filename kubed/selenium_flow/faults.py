"""The failures this package raises itself, and what any failure says.

The exception types, the one line a caller reads and the traceback a log
writes, each with any credential stripped out. `errors.py` holds the statuses
and is the half that imports Selenium, so a module that only raises or reports
a failure needs neither.
"""

from __future__ import annotations

import re
import traceback

import requests

from .urls import without_userinfo

# How the note begins that says what the page reported while a failed call ran
# (`workspace.calls`).
PAGE_NOTE = "This call caused "

# How the note begins that says a call's browser was replaced before it, and
# why (`Workspaces.settle`, `Workspaces.failed`).
REOPEN_NOTE = "The browser was replaced before this call: "
REOPENED = {
    "reaped": REOPEN_NOTE + (
        "the old one sat idle too long and the Grid ended it, so a new one was "
        "opened at the same page with the same settings. Anything that lived "
        "only in the old page is gone: unsaved work, page state and the page's "
        "own connections. Check the page before you go on."
    ),
    "lost": REOPEN_NOTE + (
        "the old one ended while this workspace was in use (its Grid node may "
        "have stopped or restarted), so a new one was opened at the same page "
        "with the same settings. Anything that lived only in the old page is "
        "gone: unsaved work, page state and the page's own connections. Check "
        "the page before you go on."
    ),
}


def reopen_note(cause: str, restored: int) -> str:
    """What a replaced browser says: its cause, and how many sites' saved data
    was put back."""
    text = REOPENED[cause]
    if restored:
        text += f" Saved site data was put back for {restored} site(s)."
    return text


class AssertionFailed(Exception):
    """An `assert` step's JavaScript came back false: a 400 carrying the
    flow author's message."""


class NotFound(LookupError):
    """The thing the caller named is not there: a 404, with its own words."""


class Controlled(Exception):
    """A person holds this workspace's browser: a 409.

    Its message names who has control, since when, and `await_control`, after
    which the same call succeeds."""

    def __init__(self, message: str):
        super().__init__(message)


class NoSession(ValueError):
    """The caller's workspace has no session open: a 400, since the fix is the
    caller's — call ``open_session`` — unless the call says otherwise
    (`Workspaces.act`'s ``closed``)."""


class TooLarge(ValueError):
    """A body or a document over its cap: a 413 over HTTP, and over MCP a
    refusal that says which cap, as every ValueError does."""


class Unresolved(Exception):
    """A page the caller named, whose host has no address: a 400.

    It carries the host and a fixed reason, never the browser's own text, which
    quotes the whole URL on Firefox. The reason allows for a redirect because
    the browser does not say which lookup failed."""

    def __init__(self, host: str | None):
        self.host = host
        where = f"the host {host}" if host else "the URL's host"
        super().__init__(
            f"{where}, or a host it redirected to, did not resolve: check the URL"
        )


UNTRUSTED = (
    "the site's certificate is not trusted: for a site with a self-signed "
    "certificate, open_session(insecure=true); "
    "an insecure browser gets no saved site data"
)


class Untrusted(Exception):
    """A page whose certificate the browser does not trust: a 400 that says what
    to do. It carries a fixed sentence and never the browser's own text, which
    quotes the URL, credentials included."""

    def __init__(self):
        super().__init__(UNTRUSTED)


# How each browser reports a host with no DNS address: Chrome's net error, and
# the error page Firefox says it reached. No other connection failure is read
# as the caller's: a refused connection or a timeout can be the network's.
_DNS_NOT_FOUND = re.compile(
    r"\bnet::ERR_NAME_NOT_RESOLVED\b|\babout:neterror\?(?:\S*&)?e=dnsNotFound\b"
)


def unresolved(exc: BaseException) -> bool:
    """Whether a driver's failure says the page's host did not resolve.

    Read from the driver's own ``msg``, which only a WebDriver failure has, so
    words of ours that quote the marker are never mistaken for the browser's.
    """
    said = getattr(exc, "msg", None)
    return isinstance(said, str) and bool(_DNS_NOT_FOUND.search(said))


class ScriptTimedOut(Exception):
    """A script that ran past the session's script timeout: a 400.

    Selenium raises a bare `TimeoutException`, which reads as a failed wait for
    an element. ``seconds`` is the timeout in force, or None when unreadable."""

    def __init__(self, seconds: float | None):
        self.seconds = seconds
        if seconds is None:
            of = ""
        else:
            of = f" of {int(seconds) if float(seconds).is_integer() else seconds}s"
        super().__init__(
            f"the script ran past this session's script timeout{of}: make it "
            "shorter, or raise it with open_session(script_timeout=<seconds>)"
        )


NODE_LOST = (
    "the browser's Grid node stopped answering during this call (it may have "
    "crashed or run out of memory): call open_session for a new browser, which "
    "goes back to the page this one was on"
)


class NodeLost(Exception):
    """The Grid node holding the browser stopped answering mid-call: a 404.

    A fixed sentence and no arguments, because the hub's own text quotes the
    node's address and the Grid's session id."""

    def __init__(self):
        super().__init__(NODE_LOST)


SCRUB_FAILED = (
    "this call's result could not be checked for secrets, so it is not "
    "returned. The action itself may have run: read the page before you "
    "repeat it."
)


class ScrubFailed(Exception):
    """A result or error could not be checked for secrets: a 500.

    A fixed sentence and no arguments: the failure it stands for may carry a
    value, so it is raised from nothing and logged by its type alone
    (`workspace.scrub`)."""

    def __init__(self):
        super().__init__(SCRUB_FAILED)


# How a browser that died under a call is reported: the hub's answer when its
# node does not reply, and Chrome's two crash messages. Case-sensitive, as the
# drivers write them.
_LOST_MARKERS = (
    "Unable to execute request for an existing session",
    "session deleted because of page crash",
    "tab crashed",
)


def lost(exc: BaseException) -> bool:
    """Whether a driver's failure says the browser's node stopped answering.

    Read from the driver's own ``msg``, as `unresolved` is, so words of ours
    quoting a marker are never mistaken for the browser's."""
    said = getattr(exc, "msg", None)
    return isinstance(said, str) and any(m in said for m in _LOST_MARKERS)


class BidiUnavailable(ConnectionError):
    """The browser answers WebDriver but its BiDi socket does not: a 503.

    Its message says what to do, and never carries the socket's URL."""


class SocketClosed(ConnectionError):
    """One end of a socket closed, or never opened.

    ``code`` and ``reason`` are what the closing side said, or None and the
    failure's type when the socket never opened: an open's own message can
    quote the URL, credentials and all, so it is never carried."""

    def __init__(self, code: int | None = None, reason: str = ""):
        super().__init__(f"socket closed ({code}): {reason}" if reason else
                         f"socket closed ({code})")
        self.code = code
        self.reason = reason


class NoNodeTook(ConnectionError):
    """No Grid node took a new browser before the Grid's queue gave up: a 503.

    Raised in place of the Grid's ``SessionNotCreatedException``, which says
    only that the request timed out; this says what was asked for and where to
    look. A node class scaled to zero may come up before the retry."""


class NodeRefused(Exception):
    """A Grid node took the request and could not start the browser.

    ``blamed`` says the open asked for a raw capability or a version, the
    usual cause then, so the answer is a 400. Otherwise it is the node's
    trouble and a 503."""

    def __init__(self, text: str, *, blamed: bool):
        super().__init__(text)
        self.blamed = blamed


def formatted(exc: BaseException) -> str:
    """The traceback, with any credential stripped out of it.

    Write this, never `log.exception`: a requests or urllib3 failure quotes the
    Grid URL, userinfo included, inside the frames.
    """
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return without_userinfo(text)


class Traceback:
    """`formatted(exc)`, built when a log line is written: a debug line that is
    not shown must not cost a traceback."""

    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def __str__(self) -> str:
        return formatted(self.exc)


def message(exc: BaseException) -> str:
    """One actionable line, without the driver's internals.

    Selenium's ``str()`` is ``"Message: <what happened>\\nStacktrace:\\n"``
    and twenty lines of ``chrome://remote/...``; everything before
    ``Stacktrace:`` is kept, without its ``Message:``. A driver's lines are
    joined into one, dropping Chrome's ``(Session info:`` line. Never returns
    an empty string: with no text it is the class name.

    A Grid refusal is cut to its status and reason: ``raise_for_status`` ends
    with ``for url: <the full URL>``, and ``GRID_URL`` may carry credentials.

    Two notes may follow after a space, the reopen's first: one beginning
    `REOPEN_NOTE` (`Workspaces.failed`) and one beginning `PAGE_NOTE`
    (`workspace.calls`). No other note is ever said.
    """
    text = _said(exc)
    notes = [
        note
        for note in getattr(exc, "__notes__", None) or ()
        if isinstance(note, str)
    ]
    reopened = [note for note in notes if note.startswith(REOPEN_NOTE)]
    page = [note for note in notes if note.startswith(PAGE_NOTE)]
    return " ".join([text, *reopened, *page])


def _said(exc: BaseException) -> str:
    """`message` before the page's note."""
    if isinstance(exc, requests.HTTPError):
        text = str(exc).split(" for url:", 1)[0].strip()
        return without_userinfo(text) or "the grid refused the request"
    driver_said = getattr(exc, "msg", None)
    text = str(driver_said or exc)
    text = text.split("Stacktrace:", 1)[0].strip()
    if text.lower().startswith("message:"):
        text = text[len("message:") :].strip()
    if driver_said:
        # Chrome adds "(Session info: chrome=…)" on a line of its own. Only a
        # driver's message is joined: ours may be lists, like a flow that
        # cannot be saved.
        text = " ".join(
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("(Session info:")
        )
    # A connection failure quotes the whole URL, credentials included.
    return without_userinfo(text) or type(exc).__name__
