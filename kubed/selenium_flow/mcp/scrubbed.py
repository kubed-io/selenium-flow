"""Every resource an agent reads, checked for the secrets an agent may not see.

A read is not a call on a page, and what it holds is often from another site
than the browser's (a site-data entry, a kept file, a captured request), so
each read is scrubbed with every leashed secret and whatever the browser of the
session it belongs to holds (`Scrubber.resource`, `_owner`).

`Scrubbed` is a middleware on `on_read_resource`, which FastMCP also runs for
`FastMCP.read_resource`, so a client's own reader, kubed-mcp's `read_resource`
tool (DLV-6) and `show` see one scrubbed text.

Text is scrubbed, and JSON text also value by value: a value a page spelt
backwards, upper-cased or with a quote in it is escaped again by the serialiser,
where the text scan would not see it. Bytes whose media type is text (a kept
`.txt`, a downloaded `.csv`) are scrubbed as UTF-8, any byte that is not carried
through as it is. Bytes of any other type are scrubbed as text too when they
decode as strict UTF-8: `read_resource` types a blob by kubed-mcp's own table of
extensions (a `.cfg`, `.ini` or `.ts` is text there and unknown to `mimetypes`)
and hands it to a model as text when its bytes are UTF-8, so whatever either
table says, what can reach a model as text is scrubbed. Other bytes (an image, a
PDF, a video) pass as they are.

A refusal is scrubbed too, in place and keeping its type. FastMCP logs a failed
read before any middleware runs; `failures.QuietCallerMistakes` handles that.

Exempt: what this server wrote itself and can hold no value (the skill, the app
shell, the flow document schema and the secrets catalogue).

A read whose scrub fails answers `ResourceError` with the `ScrubFailed`
sentence, never the unscrubbed text, and the failure is logged by its type
alone. With secrets off a read is returned as it was, the same object.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from dataclasses import replace
from urllib.parse import unquote

import anyio
from kubed.mcp import Middleware, ResourceContent, ResourceError, ResourceResult

from .. import faults
from ..workspace import sessions
from . import clients

log = logging.getLogger(__name__)

# Text this server wrote, which holds no value.
EXEMPT = ("skill://", "ui://", "flow://schema", "secret://secrets")

# Media types that are text whatever their `text/` prefix says.
TEXT_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-yaml",
        "application/yaml",
        "image/svg+xml",
    }
)


def textual(media: str | None) -> bool:
    """Whether bytes of this media type are text a reader would decode."""
    media = (media or "").split(";", 1)[0].strip().lower()
    return (
        media.startswith("text/")
        or media in TEXT_TYPES
        or media.endswith(("+json", "+xml"))
    )


def _media(uri: str, declared: str | None) -> str:
    """A content's real type: a template declares one for everything it
    serves, so for `application/octet-stream` the name knows more."""
    if declared and declared != "application/octet-stream":
        return declared
    guessed, _ = mimetypes.guess_type(uri)
    return guessed or declared or "application/octet-stream"


def _owner(workspaces, caller, uri: str) -> str:
    """The key of the session whose secrets a read at ``uri`` is scrubbed with,
    since each session holds its own (`Scrubber`): the session a
    `session://<name>/…` URI names; the caller's own (`Workspaces.reading`) for
    its downloads and its session's state; the workspace's for what the
    workspace holds (Files, flows). "" when the request names no workspace: a
    read with no name is scrubbed with every leashed secret, and nothing a
    session holds. Blocking: it reads the store, and makes nothing."""
    workspace = caller.named
    if not workspace:
        return ""
    scheme, _, rest = uri.partition("://")
    first = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if scheme == "session" and first not in sessions.RESERVED:
        caller = replace(caller, pin=unquote(first))
    elif not (
        (scheme == "session" and first == sessions.DOWNLOADS)
        or (scheme == "workspace" and first in (*sessions.STATE, "current"))
    ):
        return workspace
    try:
        return workspaces.reading(caller)
    except Exception:  # noqa: BLE001 - no such session: what the workspace holds
        return workspace


class Scrubbed(Middleware):
    """Scrub every resource read outside `EXEMPT`."""

    def __init__(self, workspaces) -> None:
        self.workspaces = workspaces
        self.scrubber = workspaces.scrubber

    async def on_read_resource(self, context, call_next):
        uri = str(getattr(context.message, "uri", ""))
        if not self.scrubber.active or uri.startswith(EXEMPT):
            return await call_next(context)
        refused = None
        try:
            result = await call_next(context)
        except Exception as exc:  # noqa: BLE001 - re-raised below, scrubbed
            refused = exc
        failure = None
        try:
            # On the request, before the thread: who is asking lives in it.
            caller = clients.caller()
            name = await anyio.to_thread.run_sync(_owner, self.workspaces, caller, uri)
            if refused is not None:
                # A refusal is an answer too: scrubbed in place, its chain
                # included, so `Explained` still reads what it means.
                await anyio.to_thread.run_sync(
                    self.scrubber.error, name, "", refused, [None]
                )
            else:
                contents = await anyio.to_thread.run_sync(
                    self._contents, name, uri, list(result.contents)
                )
        except Exception as exc:  # noqa: BLE001 - fails closed, whatever it was
            failure = type(exc).__name__
        if failure is not None:
            # Outside the `except`, and from nothing: the failure may carry a
            # value, and neither its text nor its chain may reach the reader.
            if failure != faults.ScrubFailed.__name__:  # the scrubber logged its own
                log.warning("a resource read could not be scrubbed: %s", failure)
            raise ResourceError(faults.SCRUB_FAILED) from None
        if refused is not None:
            raise refused
        if contents is None:  # nothing changed: the result as it was
            return result
        return ResourceResult(contents, meta=result.meta)

    def _contents(
        self, name: str, uri: str, contents: list[ResourceContent]
    ) -> list[ResourceContent] | None:
        """``contents`` scrubbed, or None when nothing in them changed.
        Blocking: it reads the catalogue."""
        changed = False
        out = []
        for item in contents:
            content = item.content
            if isinstance(content, bytes):
                scrubbed = self._bytes(name, uri, item, content)
            else:
                scrubbed = self._text(name, item.mime_type, content)
            if scrubbed is not content:
                changed = True
                item = ResourceContent(
                    scrubbed, mime_type=item.mime_type, meta=item.meta
                )
            out.append(item)
        return out if changed else None

    def _bytes(self, name: str, uri: str, item: ResourceContent, data: bytes):
        """Text served as bytes, and any bytes that are UTF-8, scrubbed as text;
        anything else as it is."""
        media = _media(uri, item.mime_type)
        if textual(media):
            # Bytes that are not UTF-8 ride through as surrogates and come back
            # as they were.
            text = data.decode("utf-8", errors="surrogateescape")
        else:
            # A reader may type the name by a table of its own, and decode it.
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return data
        scrubbed = self._text(name, media, text)
        if scrubbed is text:
            return data
        return scrubbed.encode("utf-8", errors="surrogateescape")

    def _text(self, name: str, media: str | None, text: str) -> str:
        """``text`` scrubbed; JSON value by value as well, re-serialised only
        when a value changed. The same object when nothing did."""
        parsed = _json(text, media)
        if parsed is _NOT_JSON:
            return self.scrubber.resource(name, text)
        values, scanned = self.scrubber.resource(name, (parsed, text))
        if values == parsed:
            return scanned
        # A value the text scan cannot see as it is spelt in the text: the
        # document again, from its scrubbed values, and scanned once more.
        rewritten = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        return self.scrubber.resource(name, rewritten)


_NOT_JSON = object()


def _json(text: str, media: str | None):
    """``text`` parsed when it is declared JSON, or `_NOT_JSON`."""
    if not (media or "").split(";", 1)[0].strip().lower().endswith(("/json", "+json")):
        return _NOT_JSON
    try:
        return json.loads(text)
    except ValueError:
        return _NOT_JSON


def install(mcp, workspaces) -> None:
    """Scrub every resource read on ``mcp`` with ``workspaces``' scrubber."""
    mcp.add_middleware(Scrubbed(workspaces))
