"""Masking, once, at capture (ruling 13).

What capture keeps and what it tails are the same masked rows, so a value is
masked here or nowhere. Credential headers are masked by name, whatever their
value; the values the secrets catalogue bound in this session are masked
wherever they appear — a URL, a header, a console text, a body. So are the
values those headers and the request's cookies carried, since a page can echo
them: they are learned as they pass, never shown. ``goog:postData`` is never
read, so it never reaches a row.
"""

from __future__ import annotations

import bisect
import re
from collections import OrderedDict
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from ..flows.redact import hidden_forms
from . import (
    AUTH_FORMS,
    COOKIE_FORMS,
    COOKIE_MIN,
    CREDENTIAL_MAX,
    CREDENTIAL_MIN,
    FORM_CHARS,
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


class _Tier:
    """Learned credentials of one kind: value -> its forms, least recently
    seen first, within a budget of forms and of their characters. The newest
    value is always kept, so a tier is over its character budget by at most
    one value's forms."""

    def __init__(self, forms: int, chars: int = FORM_CHARS):
        self.budget, self.chars_budget = forms, chars
        self.values: OrderedDict[str, frozenset[str]] = OrderedDict()
        self.forms = self.chars = 0

    def learn(self, value: str, forms: frozenset[str] | None = None) -> bool:
        """Keep ``value`` as the newest; True when it was not known."""
        if value in self.values:
            self.values.move_to_end(value)
            return False
        forms = frozenset(hidden_forms([value])) if forms is None else forms
        self.values[value] = forms
        self.forms += len(forms)
        self.chars += sum(map(len, forms))
        while len(self.values) > 1 and (
            self.forms > self.budget or self.chars > self.chars_budget
        ):
            _, gone = self.values.popitem(last=False)
            self.forms -= len(gone)
            self.chars -= sum(map(len, gone))
        return True


class Masker:
    """The secrets bound in one session, and how a value is shown without them.

    Two kinds: the forms the secrets catalogue bound, kept for the session's
    life; and the credentials its headers and cookies carried, learned as they
    pass within the budgets in `capture` (`_Tier`): an Authorization token's
    tier, and the cookies'. ``skipped`` counts the values too long to learn;
    none is ever logged.
    """

    def __init__(self, secrets: Iterable[str] = ()):
        self._bound: set[str] = set()
        self._auth = _Tier(AUTH_FORMS)
        self._cookies = _Tier(COOKIE_FORMS)
        self.skipped = 0
        # Every form masked, longest first, and their negated lengths: one
        # tuple, replaced whole, because a body is masked on a worker thread
        # while the loop learns and evicts. Read apart, an eviction between
        # the two reads could shift the forms under an index into them.
        self._state: tuple[tuple[str, ...], tuple[int, ...]] = ((), ())
        self.add(secrets)

    @property
    def secrets(self) -> tuple[str, ...]:
        """Every form masked, longest first."""
        return self._state[0]

    def add(self, forms: Iterable[str]) -> None:
        """Mask ``forms`` from now on: a secret's raw value and its encodings."""
        self._bound |= {f for f in forms if isinstance(f, str) and f}
        self._sort()

    def carry(self, other: Masker) -> None:
        """Mask what ``other`` masks: the session this one replaces, its
        credentials still credentials, under the same budgets."""
        self._bound |= other._bound
        tiers = ((self._auth, other._auth), (self._cookies, other._cookies))
        for mine, theirs in tiers:
            for value, forms in theirs.values.items():
                mine.learn(value, forms)
        self._sort()

    def cookies(self, raw) -> None:
        """Mask the values of a request's BiDi ``cookies`` from now on."""
        changed = False
        for c in raw if isinstance(raw, list) else ():
            if isinstance(c, dict) and not _binary(c.get("value")):
                value = _header_value(c.get("value"))
                if self._learnable(value, COOKIE_MIN):
                    changed = self._cookies.learn(value) or changed
        if changed:
            self._sort()

    def _learn_header(self, name: str, value: str) -> bool:
        """Learn what a credential header carries; True when anything new."""
        if name in ("authorization", "proxy-authorization"):
            # A scheme alone ("Negotiate") is no secret: only a token after
            # it is learned, with the whole value it came in.
            scheme_and_token = value.split(None, 1)
            if len(scheme_and_token) < 2:
                return False
            token = scheme_and_token[1].strip()
            if not self._learnable(token, CREDENTIAL_MIN):
                return False
            changed = self._auth.learn(token)
            if len(value) <= CREDENTIAL_MAX:
                changed = self._auth.learn(value) or changed
            return changed
        if name == "cookie":
            pairs = value.split(";")
        elif name == "set-cookie":  # one per line where a browser folds several
            pairs = [line.split(";", 1)[0] for line in value.splitlines()]
        else:
            return False
        changed = False
        for pair in pairs:
            if "=" in pair:
                cookie = pair.split("=", 1)[1].strip().strip('"')
                if self._learnable(cookie, COOKIE_MIN):
                    changed = self._cookies.learn(cookie) or changed
        return changed

    def _learnable(self, value: str, minimum: int) -> bool:
        """Whether ``value`` can be a secret worth masking (see `capture`). One
        too long to learn is counted in ``skipped``."""
        if len(value) > CREDENTIAL_MAX:
            self.skipped += 1
            return False
        return not (
            len(value) < minimum
            or value.isalpha()
            or value.isdigit()
            or "\x00" in value
        )

    def _sort(self) -> None:
        known = self._bound.union(
            *self._auth.values.values(), *self._cookies.values.values()
        )
        # Longest first, so a secret containing another is masked whole.
        secrets = tuple(sorted(known, key=len, reverse=True))
        self._state = (secrets, tuple(-len(f) for f in secrets))  # ascending

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
        secrets, lengths = self._state  # once: see `__init__`
        longest = len(secrets[0]) if secrets else 0
        read = value[: cap + longest]
        spans = []
        # A secret longer than what was read cannot be in it: most header
        # values are shorter than a token, and each secret costs a scan.
        fits = bisect.bisect_left(lengths, -len(read))
        for secret in secrets[fits:]:
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
        # Learned before any value is shown, so one header cannot echo another.
        learned = False
        for header in given[:HEADER_LIMIT]:
            name = str(header.get("name") or "").lower()
            if name in MASKED_HEADERS and not _binary(header.get("value")):
                value = _header_value(header.get("value"))
                learned = self._learn_header(name, value) or learned
        if learned:
            self._sort()
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


def _binary(value) -> bool:
    return isinstance(value, dict) and value.get("type") == "base64"


def _header_value(value) -> str:
    """A BiDi ``BytesValue`` as text; a binary one is never decoded."""
    if isinstance(value, dict):
        if value.get("type") == "base64":
            return "(binary)"
        value = value.get("value")
    return "" if value is None else str(value)
