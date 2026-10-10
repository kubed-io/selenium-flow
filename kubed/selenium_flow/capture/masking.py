"""Masking, once, at capture (ruling 13).

What capture keeps and what it tails are the same masked rows, so a value is
masked here or nowhere. Credential headers are masked by name, whatever their
value; the values the secrets catalogue bound in this session are masked
wherever they appear — a URL, a header, a console text. Request cookies and
``goog:postData`` are never read, so they never reach a row.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from . import HEADER_LIMIT, HEADER_VALUE_CAP, MASK, MASKED_HEADERS, TEXT_CAP


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

    def text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, MASK)
        return value

    def url(self, value: str, *, keep_query: bool) -> str:
        """A URL masked, without its query unless ``keep_query``, and cut to
        ``TEXT_CAP`` (a ``data:`` URL can be megabytes)."""
        masked = self.text(value)
        if not keep_query:
            masked = strip_query(masked)
        return masked[:TEXT_CAP]

    def headers(self, raw: list) -> list[dict]:
        """BiDi headers as ``[{"name", "value"}]``: at most ``HEADER_LIMIT``,
        credentials masked by name, every value masked and cut to
        ``HEADER_VALUE_CAP``."""
        kept = []
        for header in (raw or [])[:HEADER_LIMIT]:
            if not isinstance(header, dict):
                continue
            name = str(header.get("name") or "")
            if name.lower() in MASKED_HEADERS:
                value = MASK
            else:
                value = self.text(_header_value(header.get("value")))[:HEADER_VALUE_CAP]
            kept.append({"name": self.text(name)[:HEADER_VALUE_CAP], "value": value})
        return kept


def _header_value(value) -> str:
    """A BiDi ``BytesValue`` as text; a binary one is never decoded."""
    if isinstance(value, dict):
        if value.get("type") == "base64":
            return "(binary)"
        value = value.get("value")
    return "" if value is None else str(value)
