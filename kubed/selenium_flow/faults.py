"""The failures this package raises itself, and what any failure says.

`errors.py` decides what a failure *means* — its HTTP status — and has to know
Selenium's exception classes to do it. This is the half that does not: the
package's own exception types, the one line a caller reads, and the traceback a
log writes, each with any credential stripped out. So a module that only raises
or reports a failure reaches neither the driver nor the classification.
"""

from __future__ import annotations

import traceback

import requests

from .urls import without_userinfo

# How the one note a caller reads begins: what the page reported while a call
# that failed was running (`workspace.calls`, plan ruling 7). Any other note on
# an exception is a library's or a log's, and never reaches a caller.
PAGE_NOTE = "This call caused "


class AssertionFailed(Exception):
    """An `assert` step's JavaScript came back false.

    Its own type because it is not a browser fault and not a bad request: the
    page is simply not what the flow said it must be. It carries the author's
    message, which is the whole point of the tool — the flow's author knows why
    the condition matters and this package does not.
    """


class NotFound(LookupError):
    """The thing the caller named is not there: a 404, with its own words."""


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
    quotes the whole URL on Firefox (`errors.unresolved` reads that text)."""

    def __init__(self, host: str | None):
        self.host = host
        where = f"the host {host}" if host else "the URL's host"
        super().__init__(f"{where} did not resolve: check the URL")


class BidiUnavailable(ConnectionError):
    """The browser answers WebDriver but its BiDi socket does not: a 503.

    Selenium surfaces that as a closed websocket or a BiDi timeout, neither of
    which says what to do; this carries a message that does, and never the
    socket's URL."""


def formatted(exc: BaseException) -> str:
    """The traceback, with any credential stripped out of it.

    `log.exception` writes the frames verbatim, and a requests or urllib3
    failure quotes the whole Grid URL — userinfo included — inside them. So the
    sanitising that :func:`message` does for what a caller reads has to happen
    for what the logger writes as well (Copilot, #36).
    """
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return without_userinfo(text)


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

    A failed call that made the page report errors carries one more sentence,
    as a note beginning `PAGE_NOTE` (`workspace.calls`); it follows the line
    after a space. No other note is ever said.
    """
    text = _said(exc)
    page = [
        note
        for note in getattr(exc, "__notes__", None) or ()
        if isinstance(note, str) and note.startswith(PAGE_NOTE)
    ]
    return " ".join([text, *page])


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
        # A driver's message is one line plus its own furniture: Chrome adds
        # "(Session info: chrome=…)" on a line of its own. Only the driver's -
        # our own messages may be lists, like a flow that cannot be saved.
        text = " ".join(
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("(Session info:")
        )
    # A connection failure quotes the whole URL — `status_for` calls those 503
    # and nothing truncated them, so the credential travelled with the message.
    return without_userinfo(text) or type(exc).__name__
