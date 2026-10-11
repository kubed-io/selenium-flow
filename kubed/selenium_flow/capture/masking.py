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
    AUTH_MAX,
    COOKIE_FORMS,
    COOKIE_MIN,
    COOKIE_SHARE,
    COOKIE_STALE,
    COOKIE_TAKES,
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


# A cookie's name says what it is worth masking. A tracker's is matched whole,
# as its vendor spells it, so Rails' `_gateway_session` or a `mp_session` is a
# session's, not Google's or Mixpanel's; one matched wins over a session-like
# word in it, so Hotjar's `_hjSessionUser_1` and `AMP_TOKEN` are trackers.
_TRACKER = re.compile(
    r"_ga(_.*)?|_gat(_.*)?|_gac_.*|_gid|_fbp|_fbc|_gcl_.*|__utm[a-z](_.*)?"
    r"|_hj(?-i:id|[A-Z].*)|AMP_TOKEN|ajs_.*|_pk_.*|mp_.+_mixpanel|_uet[sv]id",
    re.I,
)
_SESSION = re.compile(
    r"sess|sid|token|auth|jwt|csrf|xsrf|access|refresh|id_token", re.I
)


STRENGTH_READ = 64  # characters: a rank costs the same for a 4 kB cookie
SKIPS_HELD = 256  # values too long to learn, remembered so each counts once


def _rank(name: str, value: str) -> tuple:
    """How much masking a cookie is worth, highest last: its name (a
    tracker's, any other, a session's), then the value's strength (the
    distinct characters of its first ``STRENGTH_READ``, then its length),
    then the value itself, so no two rank alike."""
    hint = 0 if _TRACKER.fullmatch(name) else 2 if _SESSION.search(name) else 1
    return (hint, len(set(value[:STRENGTH_READ])), len(value), value)


class _Ranked:
    """The cookies: value -> its forms, within a budget of forms and of their
    characters, holding the cookies that rank highest (`_rank`). One not
    sent for ``stale`` events ranks below every one that was, so a cookie no
    longer sent gives way, and one that rotates is replaced.

    Each cookie domain has a share, 1 / ``COOKIE_SHARE`` of each budget. Its
    **core** is the values it ranks highest, up to its share; the rest it
    holds is **overflow**. A core value always displaces overflow, whatever
    either ranks, and overflow competes only with overflow; cores compete
    with each other by rank only when no overflow is left. So a domain
    alone may use the whole tier, but a third party's cookies, however they
    are named, cannot evict a first party's session from its share.

    One event (`offer`) ranks its new cookies, refuses before building any
    forms those a full tier would not take, and builds the forms of only the
    ``COOKIE_TAKES`` highest-ranked of the rest: a page that rotates its
    cookies every event costs a few builds, not one per cookie. What is held
    is then ordered once (`_order`), and that order is the next view.

    What is held depends on the cookies sent, not the order they arrive in:
    a page sending more than the budget holds settles on one set and stops
    building forms. A refusal is remembered (within the characters budget)
    until something is taken or a value's freshness changes, so a settled
    page costs a lookup per cookie. The tier is over budget by less than the
    value next to give way."""

    def __init__(self, forms: int, chars: int = FORM_CHARS, stale: int = COOKIE_STALE):
        self.budget, self.chars_budget, self.stale = forms, chars, stale
        self.share = (forms // COOKIE_SHARE, chars // COOKIE_SHARE)
        self.values: dict[str, frozenset[str]] = {}
        self._rank: dict[str, tuple] = {}
        self._seen: dict[str, int] = {}
        self._domain: dict[str, str] = {}
        self._size: dict[str, tuple[int, int]] = {}  # forms, characters
        self.forms = self.chars = 0
        self.event = 0
        self._view: tuple | None = None  # this event's: see `view`
        self._stale_set: frozenset[str] = frozenset()
        self._refused: set[str] = set()
        self._refused_chars = 0

    def begin(self) -> None:
        """An event starts: freshness moves on."""
        self.event += 1
        self._view = None

    def _key(self, value: str) -> tuple:
        return (self.event - self._seen[value] <= self.stale, *self._rank[value])

    def _order(self) -> dict[str, list[tuple]]:
        """What is held, (key, value) per domain, highest first: the one sort
        an event pays for. A change in which values are stale forgets the
        refusals made against the last order."""
        by_domain: dict[str, list[tuple]] = {}
        stale = set()
        for value in self.values:
            key = self._key(value)
            if not key[0]:
                stale.add(value)
            by_domain.setdefault(self._domain[value], []).append((key, value))
        for held in by_domain.values():
            held.sort(reverse=True)
        if stale != self._stale_set:
            self._forget_refusals()
            self._stale_set = frozenset(stale)
        return by_domain

    def _core(self, held: list[tuple]) -> tuple[int, bool]:
        """How many of a domain's values, highest first, are its core (the
        smallest prefix that reaches its share, or all of them), and whether
        they reach it."""
        forms = chars = 0
        for at, (_, value) in enumerate(held):
            forms += self._size[value][0]
            chars += self._size[value][1]
            if forms >= self.share[0] or chars >= self.share[1]:
                return at + 1, True
        return len(held), False

    def view(self) -> tuple[dict[str, tuple], tuple | None, tuple | None]:
        """This event's order, as (key, value) pairs: each domain whose core
        is full and its lowest core value, the lowest core value of all,
        and the lowest overflow, if any."""
        if self._view is None:
            self._view = self._view_of(self._order())
        return self._view

    def _view_of(self, by_domain: dict[str, list[tuple]]) -> tuple:
        full: dict[str, tuple] = {}
        lowest_core = lowest_over = None
        for domain, held in by_domain.items():
            core, reached = self._core(held)
            low = held[core - 1]
            if reached:
                full[domain] = low
            if lowest_core is None or low < lowest_core:
                lowest_core = low
            if core < len(held) and (lowest_over is None or held[-1] < lowest_over):
                lowest_over = held[-1]
        return full, lowest_core, lowest_over

    def _forget_refusals(self) -> None:
        self._refused, self._refused_chars = set(), 0

    def refuses(self, value: str) -> bool:
        """Whether ``value`` was refused and nothing since lets it in."""
        if value not in self._refused:
            return False
        self.view()  # a new event may have staled a value, forgetting them
        return value in self._refused

    def knows(self, value: str) -> bool:
        """Whether ``value`` is held; if so it was sent this event."""
        seen = self._seen.get(value)
        if seen is None:
            return False
        if self.event - seen > self.stale:
            self._view = None  # fresh again, so it ranks higher
        self._seen[value] = self.event
        return True

    def full(self) -> bool:
        return self.forms >= self.budget or self.chars >= self.chars_budget

    def _admits(self, domain: str, key: tuple) -> bool:
        """Whether a full tier takes a new value: into its domain's core,
        displacing overflow or a lower core value; or as overflow, displacing
        lower overflow."""
        full, lowest_core, lowest_over = self.view()
        if domain not in full or key > full[domain][0]:  # core
            return lowest_over is not None or key > lowest_core[0]
        return lowest_over is not None and key > lowest_over[0]

    def offer(
        self,
        offers: list[tuple[str, str, str]],
        forms: Callable[[str], frozenset[str]],
        takes: int = COOKIE_TAKES,
    ) -> bool:
        """One event's new cookies, (name, value, domain), none of them held
        or refused: hold the ``takes`` highest-ranked that the tier would
        take, building their ``forms`` only then. True when what is held
        changed."""
        best: dict[str, tuple] = {}
        for name, value, domain in offers:
            ranked = (_rank(name, value), domain)
            if value not in best or ranked > best[value]:
                best[value] = ranked
        full = bool(self.values) and self.full()
        taken = []
        for value, (rank, domain) in best.items():
            if full and not self._admits(domain, (True, *rank)):
                if self._refused_chars + len(value) <= self.chars_budget:
                    self._refused.add(value)
                    self._refused_chars += len(value)
            else:
                taken.append((rank, domain, value))
        taken.sort(reverse=True)
        return self._hold([(v, d, r, forms(v)) for r, d, v in taken[:takes]])

    def adopt(self, other: _Ranked) -> bool:
        """Hold what ``other`` holds, under this tier's budgets: forms built
        once are not built again."""
        domain, rank = other._domain, other._rank
        return self._hold([(v, domain[v], rank[v], f) for v, f in other.values.items()])

    def _hold(self, entries: list[tuple]) -> bool:
        """Take ``entries`` (value, domain, rank, forms), then keep what ranks
        highest within the budgets; True when what is held changed."""
        if not entries:
            return False
        self._forget_refusals()
        for value, domain, rank, built in entries:
            size = (len(built), sum(map(len, built)))
            self.values[value], self._rank[value], self._size[value] = built, rank, size
            self._seen[value], self._domain[value] = self.event, domain
            self.forms += size[0]
            self.chars += size[1]
        return self._settle({value for value, *_ in entries})

    def _settle(self, taken: set[str]) -> bool:
        """Keep the cores by rank, then the overflow by rank, each while the
        tier is not full; drop the rest. Its order is the next view."""
        by_domain = self._order()
        cores, rests = [], []
        for held in by_domain.values():
            core, _ = self._core(held)
            cores += held[:core]
            rests += held[core:]
        cores.sort(reverse=True)
        rests.sort(reverse=True)
        forms = chars = 0
        dropped = set()
        for _, value in cores + rests:
            if forms < self.budget and chars < self.chars_budget:
                forms += self._size[value][0]
                chars += self._size[value][1]
            else:
                dropped.add(value)
        for value in dropped:
            for held in (self.values, self._rank, self._seen, self._domain, self._size):
                del held[value]
        self.forms, self.chars = forms, chars
        # What each domain keeps is a prefix of its order, so that order,
        # cut, is the view of what is held now.
        self._view = self._view_of({
            domain: kept
            for domain, held in by_domain.items()
            if (kept := [entry for entry in held if entry[1] not in dropped])
        })
        return not taken <= dropped or bool(dropped)


class Masker:
    """The secrets bound in one session, and how a value is shown without them.

    Two kinds: the forms the secrets catalogue bound, kept for the session's
    life; and the credentials its headers and cookies carried, learned as they
    pass within the budgets in `capture`: an Authorization token's tier
    (`_Tier`), and the cookies' (`_Ranked`). ``skipped`` counts the values
    too long to learn, each once however often it is sent (the first
    ``SKIPS_HELD`` remembered, by hash); none is ever logged.
    """

    def __init__(self, secrets: Iterable[str] = ()):
        self._bound: set[str] = set()
        self._auth = _Tier(AUTH_FORMS)
        self._cookies = _Ranked(COOKIE_FORMS)
        self.skipped = 0
        self._skips: set[int] = set()
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
        for skip in other._skips:  # counted once, across the reopen too
            if len(self._skips) < SKIPS_HELD:
                self._skips.add(skip)
        self._auth.begin()
        for value, forms in other._auth.values.items():
            self._auth.learn(value, lambda forms=forms: forms)
        self._cookies.begin()
        self._cookies.adopt(other._cookies)
        self._sort()

    def cookies(self, raw, url: str = "") -> None:
        """Mask the values of a request's BiDi ``cookies`` from now on: each
        of its own ``domain``, else the host of the request's ``url``."""
        self._cookies.begin()
        host = _host(url)
        offers: list[tuple[str, str, str]] = []
        for c in raw if isinstance(raw, list) else ():
            if isinstance(c, dict) and not _binary(c.get("value")):
                name = c.get("name")
                name = name if isinstance(name, str) else ""
                domain = c.get("domain")  # ".example.com" is example.com's
                domain = domain.strip().lstrip(".") if isinstance(domain, str) else ""
                domain = domain.lower()
                value = _header_value(c.get("value"))
                self._offer(name, value, domain or host, offers)
        if self._cookies.offer(offers, self._cookie_forms):
            self._sort()

    def _learn_header(self, name: str, value: str, host: str, offers: list) -> bool:
        """Learn the token a credential header carries, True when it is new;
        or offer the cookies it carries, as ``host``'s."""
        if name in ("authorization", "proxy-authorization"):
            # A scheme alone ("Negotiate") is no secret: only a token after
            # it is learned, with the whole value it came in — as **one**
            # entry, so the newest token is never evicted, whatever the
            # budget, by the forms of its own header.
            scheme_and_token = value.split(None, 1)
            if len(scheme_and_token) < 2:
                return False
            token = scheme_and_token[1].strip()
            if self._auth.knows(token) or not self._learnable(
                token, CREDENTIAL_MIN, AUTH_MAX
            ):
                return False
            # The token is learned: a whole value too long is no skip.
            whole = self._learnable(value, CREDENTIAL_MIN, AUTH_MAX, count=False)
            spellings = [token, value] if whole else [token]
            return self._auth.learn(token, lambda: _forms(spellings))
        if name == "cookie":
            pairs = value.split(";")
        elif name == "set-cookie":  # one per line where a browser folds several
            pairs = [line.split(";", 1)[0] for line in value.splitlines()]
        else:
            return False
        for pair in pairs:
            if "=" in pair:
                cookie_name, cookie = pair.split("=", 1)
                cookie = cookie.strip().strip('"')
                self._offer(cookie_name.strip(), cookie, host, offers)
        return False

    def _offer(self, name: str, value: str, domain: str, offers: list) -> None:
        """Offer a cookie to this event's learning when it is new and can be
        a secret. Its name ranks it (`_rank`), within its ``domain``'s share."""
        tier = self._cookies
        if tier.knows(value) or tier.refuses(value):
            return
        if self._learnable(value, COOKIE_MIN):
            offers.append((name, value, domain))

    def _cookie_forms(self, value: str) -> frozenset[str]:
        """A cookie's forms, and its percent-decoded spelling's when that
        differs and can be a secret: a server echoes the cookie it parsed
        (``s:a8F2…``), not the one on the wire (``s%3Aa8F2…``). One entry, so
        the two go together. Built only for a value the tier takes."""
        spellings = [value]
        try:
            decoded = unquote(value, errors="strict")
        except UnicodeDecodeError:
            decoded = value
        if decoded != value and self._learnable(decoded, COOKIE_MIN):
            spellings.append(decoded)
        return _forms(spellings)

    def _learnable(
        self,
        value: str,
        minimum: int,
        maximum: int = CREDENTIAL_MAX,
        *,
        count: bool = True,
    ) -> bool:
        """Whether ``value`` can be a secret worth masking (see `capture`). One
        too long to learn is counted in ``skipped``, unless ``count`` is off."""
        if len(value) > maximum:
            if count and hash(value) not in self._skips:
                self.skipped += 1
                if len(self._skips) < SKIPS_HELD:
                    self._skips.add(hash(value))
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

    def headers(self, raw: list, url: str = "") -> tuple[list[dict], int]:
        """BiDi headers as ``[{"name", "value"}]``, and how many were not kept.
        The cookies they carry are learned as the host of ``url``'s.

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
        learned, host, offers = False, _host(url), []
        for name, value in credentials:
            learned = self._learn_header(name, value, host, offers) or learned
        learned = self._cookies.offer(offers, self._cookie_forms) or learned
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


def _host(url: str) -> str:
    """The host a request went to: a cookie with no domain of its own is
    counted against it."""
    try:
        return (urlsplit(url).hostname or "") if url else ""
    except ValueError:  # an unparseable netloc
        return ""


def _binary(value) -> bool:
    return isinstance(value, dict) and value.get("type") == "base64"


def _header_value(value) -> str:
    """A BiDi ``BytesValue`` as text; a binary one is never decoded."""
    if isinstance(value, dict):
        if value.get("type") == "base64":
            return "(binary)"
        value = value.get("value")
    return "" if value is None else str(value)
