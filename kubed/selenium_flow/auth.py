"""Does this request carry the server's token?

One question, asked from three places — the action endpoints, the admin API and
the event stream — and previously answered by two hand-rolled copies of the same
header parsing that had already drifted in shape. Answering it in one place is
what lets the comparison below be careful exactly once.

This is the *bearer* half of the server's auth. The other half is `links.py`,
which signs a URL for one file so it can be opened by something that cannot send
a header at all. The split is deliberate: this module answers "are you the
operator?", `links.py` answers "may this one URL be fetched?".
"""

from __future__ import annotations

import hmac

from starlette.requests import Request

BEARER = "bearer"


def presented(request: Request) -> str:
    """The credential this request is offering, as a bare string.

    Accepts ``Authorization: Bearer <token>`` and a bare ``Authorization:
    <token>``. The first is what MCP clients send; the second is what a curl or
    an n8n credential field usually ends up sending, and rejecting it would buy
    nothing but support questions.
    """
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == BEARER:
        return value.strip()
    return header.strip()


def authorized(request: Request, token: str | None) -> bool:
    """Whether ``request`` may proceed.

    No token configured means the server is deliberately open — that is a
    supported deployment (a private network, or a sidecar), not a
    misconfiguration to fail closed on.

    The comparison is :func:`hmac.compare_digest` rather than ``==``. Python's
    string equality returns as soon as two bytes differ, so the time it takes
    leaks how long a common prefix was, and these routes are reachable by anyone
    who can reach the port — which is enough to recover a token a byte at a
    time. ``links.py`` already took this care for signatures; the token itself
    had been compared with ``==`` in two separate copies of this check.
    """
    if not token:
        return True
    return hmac.compare_digest(presented(request), token)
