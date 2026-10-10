"""Masking, once, at capture (ruling 13).

What capture keeps and what it tails are the same masked rows, so a value is
masked here or nowhere. Credential headers are masked by name, whatever their
value; the values the secrets catalogue bound in this session are masked
wherever they appear — a URL, a header, a console text. Request cookies and
``goog:postData`` are never read, so they never reach a row.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from . import (
    HEADER_BYTES,
    HEADER_LIMIT,
    HEADER_NAME_CAP,
    HEADER_VALUE_CAP,
    MASK,
    MASKED_HEADERS,
    TEXT_CAP,
)

# Header values that carry a URL: a password in one is masked, and they lose
# their query with their row's navigation.
URL_HEADERS = frozenset({"referer", "location", "content-location"})

# A URL's authority: after leading whitespace, an optional scheme and two
# slashes (protocol-relative ``//host`` included, and the backslashes a browser
# reads as slashes), everything up to the path, query or fragment (RFC 3986
# §3.2, WHATWG URL).
_AUTHORITY = re.compile(r"\s*(?:[A-Za-z][A-Za-z0-9+.-]*:)?[/\\]{2}([^/\\?#]*)")


def strip_query(url: str) -> str:
    """``url`` without its query string or fragment: origin and path.

    A query or fragment is where OAuth codes and reset tokens travel, so only
    the newest navigation's URLs keep one (as the history does).
    """
    try:
        parts = urlsplit(url)
    except ValueError:  # an unparseable netloc, such as an open IPv6 bracket
        return url.split("#", 1)[0].split("?", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class Masker:
    """The secrets bound in one session, and how a value is shown without them."""

    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets: tuple[str, ...] = ()
        self.add(secrets)

    def add(self, forms: Iterable[str]) -> None:
        """Mask ``forms`` from now on: a secret's raw value and its encodings."""
        known = set(self._secrets) | {f for f in forms if isinstance(f, str) and f}
        # Longest first, so a secret containing another is masked whole.
        self._secrets = tuple(sorted(known, key=len, reverse=True))

    def text(
        self, value: str, cap: int = TEXT_CAP, *, also: tuple[int, int] | None = None
    ) -> str:
        """``value`` with every occurrence of every secret masked, cut to
        ``cap``.

        Occurrences are found in the original text and overlapping or touching
        ones become one mask: replacing one secret after another (or matching
        a regex alternation) lets the first take characters the second needs,
        and leaves the rest of the second behind.

        Only the first ``cap`` plus the longest secret's characters are read,
        so the work and the memory are bounded by what a row keeps, not by
        what a page sends. Masking before the cut keeps a secret straddling it
        masked; and since a mask can be shorter than its secret, pulling later
        text inside the cap, the last characters read — where a secret may
        begin and run on past what was read — are masked too when the value
        was longer.

        ``also`` is one more span of ``value`` to mask, merged with the
        secrets' (a URL's password, so a secret holding it is masked whole).
        """
        longest = len(self._secrets[0]) if self._secrets else 0
        read = value[: cap + longest]
        spans = []
        for secret in self._secrets:
            at = read.find(secret)
            while at != -1:
                spans.append((at, at + len(secret)))
                at = read.find(secret, at + 1)
        if len(read) < len(value) and longest > 1:
            spans.append((len(read) - (longest - 1), len(read)))
        if also is not None and also[0] < len(read):
            spans.append((also[0], min(also[1], len(read))))
        if not spans:
            return read[:cap]
        spans.sort()
        out, kept_to, start, end = [], 0, *spans[0]
        for s, e in spans[1:]:
            if s <= end:
                end = max(end, e)
                continue
            out += [read[kept_to:start], MASK]
            kept_to, start, end = end, s, e
        out += [read[kept_to:start], MASK, read[end:]]
        return "".join(out)[:cap]

    def url(self, value: str, *, keep_query: bool) -> str:
        """A URL masked — bound secrets, and any password in its userinfo —
        without its query unless ``keep_query``, and cut to ``TEXT_CAP`` (a
        ``data:`` URL can be megabytes)."""
        # The password is found on the whole URL (a cut can fall inside a long
        # userinfo, leaving what reads as a host and a port) and masked with
        # the secrets, so a secret holding the userinfo is still masked whole.
        masked = self.text(value, also=_password(value))
        if not keep_query:
            masked = strip_query(masked)
        return masked[:TEXT_CAP]

    def headers(self, raw: list) -> tuple[list[dict], int]:
        """BiDi headers as ``[{"name", "value"}]``, and how many were not kept.

        At most ``HEADER_LIMIT``, and only while their names and values fit
        ``HEADER_BYTES``: past it, that header and every later one is dropped
        and counted. Credentials are masked by name, every value is masked (a
        URL-bearing one's password too) and cut to ``HEADER_VALUE_CAP``, every
        name to ``HEADER_NAME_CAP``."""
        given = [h for h in (raw or ()) if isinstance(h, dict)]
        kept: list[dict] = []
        spent = 0
        for header in given[:HEADER_LIMIT]:
            name = str(header.get("name") or "")
            if name.lower() in MASKED_HEADERS:
                value = MASK
            else:
                value = _header_value(header.get("value"))
                password = _password(value) if name.lower() in URL_HEADERS else None
                value = self.text(value, HEADER_VALUE_CAP, also=password)
            shown = {"name": self.text(name, HEADER_NAME_CAP), "value": value}
            spent += len(shown["name"].encode()) + len(value.encode())
            if spent > HEADER_BYTES:
                break
            kept.append(shown)
        return kept, len(given) - len(kept)


def _password(url: str) -> tuple[int, int] | None:
    """Where the password in ``url``'s userinfo is, if it has one:
    ``https://user:pw@host/`` is shown as ``https://user:•••@host/``, since a
    credential in a URL is an ``Authorization`` header by another name.

    Only the authority is read, so a URL of megabytes costs no copy.
    """
    authority = _AUTHORITY.match(url)
    if authority is None:
        return None
    start, end = authority.span(1)
    at = url.rfind("@", start, end)
    colon = url.find(":", start, at) if at != -1 else -1
    return None if colon == -1 else (colon + 1, at)


def _header_value(value) -> str:
    """A BiDi ``BytesValue`` as text; a binary one is never decoded."""
    if isinstance(value, dict):
        if value.get("type") == "base64":
            return "(binary)"
        value = value.get("value")
    return "" if value is None else str(value)
