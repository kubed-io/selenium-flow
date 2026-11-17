"""Signed URLs for workspace files.

For a place that cannot send a bearer token: an ``<img>`` in the admin UI, a
markdown image in a chat transcript, a link handed to someone else. The token
is the signing key. Each URL carries its path and expiry and an HMAC over both,
so it grants one file, expires on its own, dies when the token rotates, and
needs no state shared between replicas. ``exp`` is signed, so a recipient
cannot extend a link.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

# Long enough to open a page, follow a link and reload once; short enough that a
# leaked URL is not a lasting grant.
DEFAULT_TTL = 3600

# Expiries round up to this, so every link to a file signed within one window
# is the same URL and the browser cache can hit.
EXPIRY_STEP = 600


def signature(path: str, expires: int, token: str, by: str | None = None) -> str:
    """The HMAC binding one path to one expiry, and to a viewer when ``by`` is
    given: a link minted for one admin opens nothing as another, so the audit
    line's name is the one the link was minted for."""
    message = f"{path}:{expires}" + ("\0" + by if by is not None else "")
    message = message.encode()
    return hmac.new(token.encode(), message, hashlib.sha256).hexdigest()[:32]


def sign(
    path: str,
    token: str,
    ttl: int = DEFAULT_TTL,
    now: float | None = None,
    *,
    by: str | None = None,
    exact: bool = False,
) -> str:
    """``path`` with the query parameters that make it fetchable.

    When ``exact=True``, expires at exactly ``int(now + ttl)`` with no rounding.
    When ``exact=False`` (default), expires are rounded up to ``EXPIRY_STEP``.
    """
    current_time = time.time() if now is None else now
    if exact:
        expires = int(current_time + ttl)
    else:
        earliest = int(current_time + ttl)
        expires = -(-earliest // EXPIRY_STEP) * EXPIRY_STEP
    signed = f"{path}?exp={expires}&sig={signature(path, expires, token, by)}"
    return signed if by is None else f"{signed}&by={quote(by, safe='')}"


def refusal(
    path: str,
    expires,
    provided,
    token: str,
    now: float | None = None,
    *,
    by: str | None = None,
) -> str | None:
    """Why a request's ``exp``/``sig`` do not authorise ``path``, ``"invalid"``
    or ``"expired"``, or None when they do.

    The signature is checked first, so "expired" is only said of a link this
    server minted. Compared in constant time, as bytes: ``compare_digest``
    raises on a non-ASCII ``str``, and ``sig`` is whatever the caller typed.
    """
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        return "invalid"
    expected = signature(path, expires, token, by).encode()
    if not hmac.compare_digest(expected, str(provided or "").encode()):
        return "invalid"
    if expires < (time.time() if now is None else now):
        return "expired"
    return None


def valid(path: str, expires, provided, token: str, now: float | None = None) -> bool:
    """Whether a request's ``exp``/``sig`` actually authorise ``path``."""
    return refusal(path, expires, provided, token, now) is None


def file_path(session_id: str, name: str) -> str:
    """The unsigned path of one file in one *browser's* store."""
    return f"/files/{quote(session_id, safe='')}/{quote(name, safe='')}"


def kept_path(workspace: str, name: str) -> str:
    """The unsigned path of one file kept beyond the browser that made it.

    Keyed by workspace name where a download is keyed by browser id; the two
    namespaces can collide, so they are separate routes.
    """
    return f"/kept/{quote(workspace, safe='')}/{quote(name, safe='')}"


def _url(path: str, token: str | None, base: str, ttl: int) -> str:
    """One path, signed when the server has a token to sign with.

    Every file route signs here, so none ends up unsigned or signed over a path
    it does not serve.
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
    workspace: str,
    name: str,
    token: str | None,
    base: str = "",
    ttl: int = DEFAULT_TTL,
) -> str:
    """A URL for one file kept beyond its browser."""
    return _url(kept_path(workspace, name), token, base, ttl)


def screenshot_path(workspace: str, name: str) -> str:
    """The unsigned path of one screenshot. Keyed by workspace name, like a kept
    file, because a screenshot outlives the browser that took it."""
    return f"/screenshots/{quote(workspace, safe='')}/{quote(name, safe='')}"


def screenshot_url(
    workspace: str,
    name: str,
    token: str | None,
    mount: str = "",
    ttl: int = DEFAULT_TTL,
) -> str:
    """A URL for one screenshot."""
    return _url(screenshot_path(workspace, name), token, mount, ttl)


def recording_path(workspace: str, name: str) -> str:
    """The unsigned path of one recording, keyed by workspace like a screenshot."""
    return f"/recordings/{quote(workspace, safe='')}/{quote(name, safe='')}"


def recording_url(
    workspace: str,
    name: str,
    token: str | None,
    mount: str = "",
    ttl: int = DEFAULT_TTL,
) -> str:
    """A URL for one recording."""
    return _url(recording_path(workspace, name), token, mount, ttl)
