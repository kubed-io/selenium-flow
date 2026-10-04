"""Starlette's trailing-slash redirect, made relative.

A path that only matches with its slash added or taken away is answered with a
307 to an *absolute* URL that Starlette builds from the request as this server
sees it. Behind the ingress that is the wrong URL twice over: the ingress
strips the public prefix (`/flow`) before the request arrives and ends TLS, so
`https://host/flow/admin/` was sent to `http://host/admin`, a 404 on another
scheme. A relative Location resolves against the URL the browser actually
asked for, so it is right behind any prefix and on either scheme - the same
reasoning as the admin page's own `./` redirect.

Only that redirect is rewritten: a 307 whose Location is the request's own path
with one slash added or taken away. Every other redirect passes untouched.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

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
    if found.path != toggled:
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
