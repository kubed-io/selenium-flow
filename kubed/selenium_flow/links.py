"""Signed URLs for session files.

A file in a session's store is often wanted somewhere that cannot present a
bearer token: an ``<img>`` tag in the admin UI, a markdown image in a chat
transcript, a link handed to someone who is not the caller. Browsers do not
attach an ``Authorization`` header to an image request, and putting the MCP
token in the query string would hand out the key to everything.

So the token stops being the credential for these URLs and becomes the *signing
key* for them. Each URL carries the path it is good for and the moment it stops
working, plus an HMAC over both. That yields a link which:

- grants exactly one file, not the API,
- expires on its own,
- is revoked in bulk by rotating the token that is already rotated,
- and needs no server-side state, which matters because there may be several
  replicas and no shared store between them.

``exp`` is inside the signature, so a recipient cannot extend their own link.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

# Long enough to open a page, follow a link and reload once; short enough that a
# leaked URL is not a lasting grant.
DEFAULT_TTL = 3600


def signature(path: str, expires: int, token: str) -> str:
    """The HMAC binding one path to one expiry."""
    message = f"{path}:{expires}".encode()
    return hmac.new(token.encode(), message, hashlib.sha256).hexdigest()[:32]


def sign(
    path: str, token: str, ttl: int = DEFAULT_TTL, now: float | None = None
) -> str:
    """``path`` with the query parameters that make it fetchable."""
    expires = int((time.time() if now is None else now) + ttl)
    return f"{path}?exp={expires}&sig={signature(path, expires, token)}"


def valid(path: str, expires, provided, token: str, now: float | None = None) -> bool:
    """Whether a request's ``exp``/``sig`` actually authorise ``path``.

    Compared with :func:`hmac.compare_digest` rather than ``==``: this runs on
    an unauthenticated route, so a timing side channel here would be a way to
    forge a signature one byte at a time.
    """
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        return False
    if expires < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(signature(path, expires, token), str(provided or ""))


def file_path(session_id: str, name: str) -> str:
    """The unsigned path of one file in one *browser's* store."""
    return f"/files/{quote(session_id, safe='')}/{quote(name, safe='')}"


def kept_path(session: str, name: str) -> str:
    """The unsigned path of one file kept beyond the browser that made it.

    A separate route rather than a flag on the one above, because it is keyed by
    a different thing: a download belongs to a *browser id*, and a kept file to
    a *session name* that outlives it (§F1.10). One route taking either would
    have to guess which it was handed, and the two namespaces can collide.
    """
    return f"/kept/{quote(session, safe='')}/{quote(name, safe='')}"


def _url(path: str, token: str | None, base: str, ttl: int) -> str:
    """One path, signed when the server has a token to sign with.

    With authentication off there is nothing to sign and nothing to protect, so
    the plain path is already the answer.

    Both file routes come through here rather than each signing for itself: a
    second signing implementation is how one of them ends up unsigned, or signed
    over a path it does not serve.
    """
    return base.rstrip("/") + (sign(path, token, ttl) if token else path)


def file_url(
    session_id: str,
    name: str,
    token: str | None,
    base: str = "",
    ttl: int = DEFAULT_TTL,
) -> str:
    """A URL for one of a browser's downloads."""
    return _url(file_path(session_id, name), token, base, ttl)


def kept_url(
    session: str,
    name: str,
    token: str | None,
    base: str = "",
    ttl: int = DEFAULT_TTL,
) -> str:
    """A URL for one file kept beyond its browser."""
    return _url(kept_path(session, name), token, base, ttl)
