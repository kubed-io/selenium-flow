"""What a URL is made of, and what must never leave with it.

The origin and host rules, the page identity, the comparison that treats two
spellings of a page as one, and the one place a URL's credentials are cut out of
a string. Nothing here knows about a browser, a session or a request; the Grid
URL's userinfo, a stored history row and a refusal message all go through the
same few functions, so a fix to one is a fix to all of them.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

# The userinfo of a URL. `GRID_URL` may carry credentials, and an exception's
# text is quoted into logs and into the error a caller reads — so it is stripped
# from every message, not only from the one failure known to print a URL
# (Copilot, #36). every scrubber imports this rather than keeping a copy.
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


DEFAULT_PORTS = {"http": 80, "https": 443}


def origin_of(url: str) -> str:
    """``url``'s origin as ``location.origin`` spells it, or "" when it has none."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if not parts.scheme or not host:
        return ""
    scheme = parts.scheme.lower()
    # `hostname` drops an IPv6 literal's brackets; `location.origin` keeps them.
    if ":" in host:
        host = f"[{host}]"
    shown = f":{port}" if port is not None and port != DEFAULT_PORTS.get(scheme) else ""
    return f"{scheme}://{host}{shown}"


def host_of(url: str) -> str:
    try:
        return (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def page_of(url: str) -> str:
    """``url`` without its query, fragment or credentials: its origin and path."""
    return origin_of(url) + urlsplit(url).path


def normalize_url(url: str) -> str:
    """Drop the fragment and any trailing slash so equivalent URLs compare equal."""
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, "")
    )


def public_url(url: str) -> str:
    """``url`` with any credentials removed, for anything that leaves this process.

    ``GRID_URL`` may carry userinfo — ``http://user:pass@grid:4444`` — and the
    probes and the admin page both name the Grid. Printing it whole puts the
    Grid's credential in an unauthenticated response and in whatever scrapes it.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:  # IPv6 — `hostname` drops the brackets the authority wants
            host = f"[{host}]"
        if parts.port:  # parses, and raises when it is not a port
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))
    except ValueError:
        # A malformed GRID_URL — a bad port, an unclosed IPv6 literal — reaches
        # the probes like any other, and a probe answers rather than raises. The
        # credentials still have to go, so they go by pattern (Copilot, #35).
        return without_userinfo(url.split("?", 1)[0])


def scrub(text: str, url: str) -> str:
    """``text`` with ``url``'s credentials cut out, wherever it quoted them.

    ``faults.message`` trims the one Grid failure known to print its URL, but a
    proxy or parse error can quote it too, and ``/ready`` answers to anyone.
    """
    try:
        parts = urlsplit(url)
        secrets = (parts.netloc.rpartition("@")[0], parts.password)
    except ValueError:  # same malformed URL, same credentials to remove
        found = USERINFO.search(url)
        secrets = (found.group("userinfo") if found else "",)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
