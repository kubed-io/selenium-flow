"""Starlette's trailing-slash redirect, made relative.

Starlette answers a path that matches only with a slash added or removed with a
307 to an absolute URL built from the request as this server sees it. Behind
an ingress that strips a prefix and ends TLS, that is the wrong path on the
wrong scheme; a relative Location resolves against what the browser asked for.

Only that redirect is rewritten: a 307 whose Location is the request's own path
with one slash added or taken away.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# What a path segment may carry unescaped (RFC 3986 pchar).
_SEGMENT_SAFE = "!$&'()*+,;=:@-._~"


def relative(path: str, location: str) -> str | None:
    """The relative form of Starlette's slash redirect for ``path``, or None
    when ``location`` is not that redirect."""
    if path.endswith("//") or path == "/":
        return None
    toggled = path[:-1] if path.endswith("/") else path + "/"
    found = urlsplit(location)
    # The scope's path is decoded and the Location is not.
    if unquote(found.path) != toggled:
        return None
    query = found.query
    segment = quote(toggled.rstrip("/").rsplit("/", 1)[-1], safe=_SEGMENT_SAFE)
    # `/admin/` -> `../admin`; `/flows` -> `./flows/`. The `./` keeps a segment
    # with a colon in it from reading as a scheme.
    target = f"../{segment}" if path.endswith("/") else f"./{segment}/"
    return f"{target}?{query}" if query else target


class RelativeSlashRedirects:
    """ASGI middleware: see the module docstring."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def sending(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 307:
                headers = MutableHeaders(scope=message)
                target = relative(scope["path"], headers.get("location", ""))
                if target is not None:
                    headers["location"] = target
            await send(message)

        await self.app(scope, receive, sending)
