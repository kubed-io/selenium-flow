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

from . import HEADER_LIMIT, HEADER_VALUE_CAP, MASK, MASKED_HEADERS, TEXT_CAP

# Header values that carry a URL: a password in one is masked, and they lose
# their query with their row's navigation.
URL_HEADERS = frozenset({"referer", "location", "content-location"})

# A URL's scheme, then its authority: everything up to the path, query or
# fragment (RFC 3986 §3.2).
_AUTHORITY = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://([^/?#]*)")


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

    def text(self, value: str, cap: int = TEXT_CAP) -> str:
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
        # The password first, on the whole URL: a cut can fall inside a long
        # userinfo, and what is left of it then reads as a host and a port.
        masked = self.text(_hide_password(value))
        if not keep_query:
            masked = strip_query(masked)
        return masked[:TEXT_CAP]

    def headers(self, raw: list) -> list[dict]:
        """BiDi headers as ``[{"name", "value"}]``: at most ``HEADER_LIMIT``,
        credentials masked by name, every value masked (a URL-bearing one's
        password too) and cut to ``HEADER_VALUE_CAP``."""
        kept = []
        for header in (raw or [])[:HEADER_LIMIT]:
            if not isinstance(header, dict):
                continue
            name = str(header.get("name") or "")
            if name.lower() in MASKED_HEADERS:
                value = MASK
            else:
                value = _header_value(header.get("value"))
                if name.lower() in URL_HEADERS:
                    value = _hide_password(value)
                value = self.text(value, HEADER_VALUE_CAP)
            kept.append({"name": self.text(name, HEADER_VALUE_CAP), "value": value})
        return kept


def _hide_password(url: str) -> str:
    """``https://user:pw@host/`` as ``https://user:•••@host/``: a credential in
    a URL is an ``Authorization`` header by another name.

    Only the authority is read, so a URL of megabytes costs one copy at most,
    and only when it does carry a password.
    """
    authority = _AUTHORITY.match(url)
    if authority is None:
        return url
    userinfo, at, _ = authority.group(1).rpartition("@")
    if not at or ":" not in userinfo:
        return url
    user = userinfo.split(":", 1)[0]
    start = authority.start(1)
    return f"{url[:start]}{user}:{MASK}{url[start + len(userinfo) :]}"


def _header_value(value) -> str:
    """A BiDi ``BytesValue`` as text; a binary one is never decoded."""
    if isinstance(value, dict):
        if value.get("type") == "base64":
            return "(binary)"
        value = value.get("value")
    return "" if value is None else str(value)
