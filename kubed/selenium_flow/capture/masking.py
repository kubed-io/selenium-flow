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
from collections.abc import Callable, Iterable
from urllib.parse import unquote, urlsplit, urlunsplit

from ..flows.redact import hidden_forms
from . import (
    AUTH_FORMS,
    COOKIE_FORMS,
    COOKIE_MIN,
    COOKIE_STALE,
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
    """Authorization tokens: value -> its forms, least recently seen first,
    within a budget of forms and of their characters.

    What one event (a `Masker.cookies` or `Masker.headers` call) sends is
    ``touched``. A new value is learned, evicting the least recently seen,
    unless the tier is full of what this event already sent: then the first
    one the event sent is kept and the rest are not learned. A value alone
    over the budget is still kept, so a tier is over its budget by at most
    one value's forms. A request carries at most two, and `Masker.headers`
    learns its Authorization before its Proxy-Authorization (see there)."""

    def __init__(self, forms: int, chars: int = FORM_CHARS):
        self.budget, self.chars_budget = forms, chars
        self.values: OrderedDict[str, frozenset[str]] = OrderedDict()
        self.forms = self.chars = 0
        self._touched: set[str] = set()

    def begin(self) -> None:
        """An event starts: nothing is touched yet."""
        self._touched = set()

    def knows(self, value: str) -> bool:
        """Whether ``value`` is learned; if so it is now the newest."""
        if value not in self.values:
            return False
        self.values.move_to_end(value)
        self._touched.add(value)
        return True

    def learn(
        self, value: str, forms: Callable[[], frozenset[str]]
    ) -> bool:
        """Keep ``value`` as the newest, building its forms only when it is
        new and there is room; True when it was learned."""
        if self.knows(value):
            return False
        full = self.forms >= self.budget or self.chars >= self.chars_budget
        if full and self.values and next(iter(self.values)) in self._touched:
            return False  # it would evict what this event sent
        built = forms()
        self.values[value] = built
        self._touched.add(value)
        self.forms += len(built)
        self.chars += sum(map(len, built))
        while len(self.values) > 1 and (
            self.forms > self.budget or self.chars > self.chars_budget
        ):
            oldest = next(iter(self.values))
            if oldest in self._touched and oldest != value:
                break  # this event's; over by this one value at most
            _, gone = self.values.popitem(last=False)
            self.forms -= len(gone)
            self.chars -= sum(map(len, gone))
        return True


# A cookie's name says what it is worth masking. A tracker's is matched first,
# from the start, so Hotjar's `_hjSessionUser` and `AMP_TOKEN` are trackers.
_TRACKER = re.compile(r"_ga|_gid|_fbp|_gcl|__utm|_hj|ajs_|amp_|_pk_|mp_", re.I)
_SESSION = re.compile(
    r"sess|sid|token|auth|jwt|csrf|xsrf|access|refresh|id_token", re.I
)


STRENGTH_READ = 64  # characters: a rank costs the same for a 4 kB cookie


def _rank(name: str, value: str) -> tuple:
    """How much masking a cookie is worth, highest last: its name (a
    tracker's, any other, a session's), then the value's strength (the
    distinct characters of its first ``STRENGTH_READ``, then its length),
    then the value itself, so no two rank alike."""
    hint = 0 if _TRACKER.match(name) else 2 if _SESSION.search(name) else 1
    return (hint, len(set(value[:STRENGTH_READ])), len(value), value)


class _Ranked:
    """The cookies: value -> its forms, within a budget of forms and of their
    characters, holding the cookies that rank highest (`_rank`). One not
    sent for ``stale`` events ranks below every one that was, so a cookie no
    longer sent gives way, and one that rotates is replaced.

    The order is total and does not depend on the order cookies arrive in,
    so a page sending more than the budget holds settles on one set, the
    same whatever its header order, and stops building forms. A new value is
    refused, before its forms are built, when the tier is full and it ranks
    below the lowest it holds; the refusal is remembered (within the
    characters budget) until that lowest changes, so a settled page costs a
    lookup per cookie. What is held is a prefix of the ranking: all of it but
    the lowest value fits the budget, so the tier is over budget by at most
    that value's forms."""

    def __init__(self, forms: int, chars: int = FORM_CHARS, stale: int = COOKIE_STALE):
        self.budget, self.chars_budget, self.stale = forms, chars, stale
        self.values: dict[str, frozenset[str]] = {}
        self._rank: dict[str, tuple] = {}
        self._seen: dict[str, int] = {}
        self.forms = self.chars = 0
        self.event = 0
        self._lowest: tuple | None = None  # (key, value), this event's
        self._refused: set[str] = set()  # ranked below ``_floor``
        self._refused_chars = 0
        self._floor: tuple | None = None

    def begin(self) -> None:
        """An event starts: freshness moves on."""
        self.event += 1
        self._lowest = None

    def _key(self, value: str) -> tuple:
        return (self.event - self._seen[value] <= self.stale, *self._rank[value])

    def lowest(self) -> tuple:
        if self._lowest is None:
            self._lowest = min((self._key(v), v) for v in self.values)
            if self._lowest[0] != self._floor:
                self._forget_refusals()
                self._floor = self._lowest[0]
        return self._lowest

    def _forget_refusals(self) -> None:
        self._refused, self._refused_chars = set(), 0

    def refuses(self, value: str) -> bool:
        """Whether ``value`` was refused and nothing since lets it in."""
        if value not in self._refused:
            return False
        self.lowest()  # a new event may have moved it, forgetting them
        return value in self._refused

    def knows(self, value: str) -> bool:
        """Whether ``value`` is held; if so it was sent this event."""
        if value not in self.values:
            return False
        self._seen[value] = self.event
        if self._lowest is not None and self._lowest[1] == value:
            self._lowest = None  # fresher now
        return True

    def full(self) -> bool:
        return self.forms >= self.budget or self.chars >= self.chars_budget

    def learn(
        self, value: str, rank: tuple, forms: Callable[[], frozenset[str]]
    ) -> bool:
        """Hold ``value`` if it ranks high enough, building its forms only
        then; True when it was learned."""
        if self.knows(value):
            return False
        if self.values and self.full() and (True, *rank) < self.lowest()[0]:
            if self._refused_chars + len(value) <= self.chars_budget:
                self._refused.add(value)
                self._refused_chars += len(value)
            return False  # it would displace nothing
        self._forget_refusals()
        built = forms()
        self.values[value], self._rank[value] = built, rank
        self._seen[value] = self.event
        self.forms += len(built)
        self.chars += sum(map(len, built))
        self._lowest = None
        while len(self.values) > 1:
            gone = self.lowest()[1]
            size = self.values[gone]
            chars = sum(map(len, size))
            rest = (self.forms - len(size), self.chars - chars)
            if rest[0] < self.budget and rest[1] < self.chars_budget:
                break  # the rest would not be full: the lowest stays
            del self.values[gone], self._rank[gone], self._seen[gone]
            self.forms -= len(size)
            self.chars -= chars
            self._lowest = None
        return True


class Masker:
    """The secrets bound in one session, and how a value is shown without them.

    Two kinds: the forms the secrets catalogue bound, kept for the session's
    life; and the credentials its headers and cookies carried, learned as they
    pass within the budgets in `capture`: an Authorization token's tier
    (`_Tier`), and the cookies' (`_Ranked`). ``skipped`` counts the values
    too long to learn; none is ever logged.
    """

    def __init__(self, secrets: Iterable[str] = ()):
        self._bound: set[str] = set()
        self._auth = _Tier(AUTH_FORMS)
        self._cookies = _Ranked(COOKIE_FORMS)
        self.skipped = 0
        # Every form masked, longest first, and their negated lengths: one
        # tuple, replaced whole, because a body is masked on a worker thread
        # while the loop learns and evicts. Read apart, an eviction between
        # the two reads could shift the forms under an index into them.
        # The third: whether no form holds a NUL, so a row's header texts can
        # be scanned joined on one.
        self._state: tuple[tuple[str, ...], tuple[int, ...], bool] = ((), (), True)
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
        self._auth.begin()
        for value, forms in other._auth.values.items():
            self._auth.learn(value, lambda forms=forms: forms)
        self._cookies.begin()
        theirs = other._cookies
        for value in sorted(theirs.values, key=theirs._rank.__getitem__, reverse=True):
            forms = theirs.values[value]
            self._cookies.learn(value, theirs._rank[value], lambda forms=forms: forms)
        self._sort()

    def cookies(self, raw) -> None:
        """Mask the values of a request's BiDi ``cookies`` from now on."""
        self._cookies.begin()
        changed = False
        for c in raw if isinstance(raw, list) else ():
            if isinstance(c, dict) and not _binary(c.get("value")):
                name = c.get("name")
                name = name if isinstance(name, str) else ""
                value = _header_value(c.get("value"))
                changed = self._cookie(name, value) or changed
        if changed:
            self._sort()

    def _learn_header(self, name: str, value: str) -> bool:
        """Learn what a credential header carries; True when anything new."""
        if name in ("authorization", "proxy-authorization"):
            # A scheme alone ("Negotiate") is no secret: only a token after
            # it is learned, with the whole value it came in — as **one**
            # entry, so the newest token is never evicted, whatever the
            # budget, by the forms of its own header.
            scheme_and_token = value.split(None, 1)
            if len(scheme_and_token) < 2:
                return False
            token = scheme_and_token[1].strip()
            if self._auth.knows(token) or not self._learnable(token, CREDENTIAL_MIN):
                return False
            # The token is learned: a whole value too long is no skip.
            whole = self._learnable(value, CREDENTIAL_MIN, count=False)
            spellings = [token, value] if whole else [token]
            return self._auth.learn(token, lambda: _forms(spellings))
        if name == "cookie":
            pairs = value.split(";")
        elif name == "set-cookie":  # one per line where a browser folds several
            pairs = [line.split(";", 1)[0] for line in value.splitlines()]
        else:
            return False
        changed = False
        for pair in pairs:
            if "=" in pair:
                name, cookie = pair.split("=", 1)
                cookie = cookie.strip().strip('"')
                changed = self._cookie(name.strip(), cookie) or changed
        return changed

    def _cookie(self, name: str, value: str) -> bool:
        """Learn a cookie's value, and its percent-decoded spelling when that
        differs and can be a secret: a server echoes the cookie it parsed
        (``s:a8F2…``), not the one on the wire (``s%3Aa8F2…``). One entry, so
        the two go together. Its name ranks it (`_rank`)."""
        tier = self._cookies
        if tier.knows(value) or tier.refuses(value):
            return False
        if not self._learnable(value, COOKIE_MIN):
            return False

        def forms() -> frozenset[str]:  # only for a value the tier takes
            spellings = [value]
            try:
                decoded = unquote(value, errors="strict")
            except UnicodeDecodeError:
                decoded = value
            if decoded != value and self._learnable(decoded, COOKIE_MIN):
                spellings.append(decoded)
            return _forms(spellings)

        return tier.learn(value, _rank(name, value), forms)

    def _learnable(self, value: str, minimum: int, *, count: bool = True) -> bool:
        """Whether ``value`` can be a secret worth masking (see `capture`). One
        too long to learn is counted in ``skipped``, unless ``count`` is off."""
        if len(value) > CREDENTIAL_MAX:
            self.skipped += count
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
        self._state = (
            secrets,
            tuple(-len(f) for f in secrets),  # ascending
            not any(NUL in f for f in secrets),
        )

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
        secrets, lengths, _ = self._state  # once: see `__init__`
        longest = len(secrets[0]) if secrets else 0
        read = value[: cap + longest]
        spans = _scan(read, secrets, lengths, len(read))
        return _shown(read, len(value), cap, spans, longest, also)

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
        self._auth.begin()
        self._cookies.begin()
        # Learned before any value is shown, so one header cannot echo another.
        # Proxy-Authorization last: when the two will not both fit, the one
        # kept is the one an origin can echo, since a proxy strips the other.
        credentials = []
        for header in given[:HEADER_LIMIT]:
            name = str(header.get("name") or "").lower()
            if name in MASKED_HEADERS and not _binary(header.get("value")):
                credentials.append((name, _header_value(header.get("value"))))
        credentials.sort(key=lambda c: c[0] == "proxy-authorization")
        learned = False
        for name, value in credentials:
            learned = self._learn_header(name, value) or learned
        if learned:
            self._sort()
        state = self._state  # once, after the learning: see `__init__`
        longest = len(state[0][0]) if state[0] else 0
        heard = given[:HEADER_LIMIT]
        kept: list[dict] = []
        spent, at = 0, 0
        # In batches of about the bytes a side keeps: a hostile server's 32
        # headers of 4 kB cost one batch's scan, not 128 kB of them.
        while at < len(heard):
            batch, size = [], 0
            while at < len(heard) and size < HEADER_BYTES:
                name = str(heard[at].get("name") or "")
                if name.lower() in MASKED_HEADERS:
                    value, also = None, None
                else:
                    value = _header_value(heard[at].get("value"))
                    also = _password(value) if name.lower() in URL_HEADERS else None
                batch.append((name, value, also))
                size += min(len(name), HEADER_NAME_CAP + longest)
                if value is not None:
                    size += min(len(value), HEADER_VALUE_CAP + longest)
                at += 1
            for name, value in _masked(batch, state, longest):
                spent += len(name.encode()) + len(value.encode())
                if spent > HEADER_BYTES:
                    return kept, len(given) - len(kept)
                kept.append({"name": name, "value": value})
        return kept, len(given) - len(kept)


NUL = "\x00"


def _forms(spellings: list[str]) -> frozenset[str]:
    """Every form of every spelling of one credential, as one entry."""
    return frozenset(hidden_forms(spellings))


def _scan(read: str, secrets, lengths, fit: int) -> list[tuple[int, int]]:
    """Every occurrence in ``read`` of every secret no longer than ``fit``:
    one ``find`` pass per such secret, which is what masking costs."""
    spans = []
    for secret in secrets[bisect.bisect_left(lengths, -fit) :]:
        at = read.find(secret)
        while at != -1:
            spans.append((at, at + len(secret)))
            at = read.find(secret, at + 1)
    return spans


def _shown(
    read: str, whole: int, cap: int, spans: list, longest: int, also
) -> str:
    """``read`` (the first characters of a text ``whole`` long) with ``spans``
    masked, as `Masker.text` describes, cut to ``cap``."""
    if len(read) < whole and longest > 1:
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


def _masked(batch: list, state, longest: int) -> list[tuple[str, str]]:
    """Each header of ``batch`` — (name, value or None for a credential,
    password span) — as shown: masked and cut as `Masker.text` would, but
    every text of the batch found in **one** scan, joined on NUL. Neither a
    header text nor a learned form holds one, so no match crosses a join; a
    bound form or a text that does is scanned on its own."""
    secrets, lengths, nul_free = state
    texts = []  # (read, whole length, cap, password span)
    for name, value, also in batch:
        read = name[: HEADER_NAME_CAP + longest]
        texts.append((read, len(name), HEADER_NAME_CAP, None))
        if value is not None:
            read = value[: HEADER_VALUE_CAP + longest]
            texts.append((read, len(value), HEADER_VALUE_CAP, also))
    reads = [t[0] for t in texts]
    found: list[list] = [[] for _ in texts]
    if nul_free and not any(NUL in r for r in reads):
        starts, offset = [], 0
        for read in reads:
            starts.append(offset)
            offset += len(read) + 1
        fit = max(map(len, reads), default=0)
        for s, e in _scan(NUL.join(reads), secrets, lengths, fit):
            i = bisect.bisect_right(starts, s) - 1
            found[i].append((s - starts[i], e - starts[i]))
    else:
        found = [_scan(r, secrets, lengths, len(r)) for r in reads]
    shown = iter(
        _shown(read, whole, cap, spans, longest, also)
        for (read, whole, cap, also), spans in zip(texts, found, strict=True)
    )
    return [
        (next(shown), MASK if value is None else next(shown))
        for _name, value, _also in batch
    ]


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
