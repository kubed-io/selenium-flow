"""Every spelling of a secret's value, one marker to name it, one scanner to find it.

A secret is typed into a page and must come back out nowhere: not in a step's
result, its error, a captured request, or a log line. This is the primitive all
of those share. A value becomes `Forms`, every way it can be spelt, each labelled
with the secret (name, key) it came from; a `Scanner` finds them in a text; and
what it finds is shown as the marker `[secret:<name>/<key>]`, which reads
straight back as `write(secret={"name": ..., "key": ...})`.

Plain: no Selenium, no protocol layer (`tests/test_boundaries.py`).
"""

from __future__ import annotations

import base64
import html
import json
import math
import re
import string
from collections.abc import Iterator, Mapping
from typing import NamedTuple
from urllib.parse import quote, quote_plus

# (name, key) of a catalogued secret.
Id = tuple[str, str]
Span = tuple[int, int, frozenset]

# What an unlabelled form shows: a value nobody catalogued (a header, a cookie).
MASK = "•••"

# A form this short is masked only where it stands alone, or a 3-character
# secret would turn every word containing it into noise (ruling 4).
SHORT = 4
# A base64 core shorter than this turns up in data URIs and scripts by chance:
# about once a megabyte at 4 characters.
B64_CORE_MIN = 6

# Case folding is ASCII-only: `lower()` or `casefold()` change the length of
# `İ` and `ß`, which would shift every later span (ruling 18).
ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)

# Go's encoding/json escapes these as \u00XX (lowercase), to be safe in HTML.
GO_JSON = str.maketrans(
    {
        "<": "\\u003c",
        ">": "\\u003e",
        "&": "\\u0026",
        "\u2028": "\\u2028",
        "\u2029": "\\u2029",
    }
)
HEX_ESCAPE = re.compile(r"\\u[0-9a-f]{4}")
# How an HTML body may spell `"`, `'` and `+` once `&`, `<` and `>` are
# escaped: Python's html.escape, none, the decimal apostrophe, PHP 8.1's
# htmlspecialchars, HTML5 / XML, and Go's html/template.
HTML_QUOTES = (
    ("&quot;", "&#x27;", "+"),
    ('"', "'", "+"),
    ("&quot;", "&#39;", "+"),
    ("&quot;", "&#039;", "+"),
    ("&quot;", "&apos;", "+"),
    ("&#34;", "&#39;", "&#43;"),
)

_NAME = r"[^\[\]\s|/]+"
_KEY = r"[^\[\]\s|]+"
MARKER = re.compile(rf"\[secret:{_NAME}/{_KEY}(?:\|{_NAME}/{_KEY})*\]")


class Label(NamedTuple):
    """What one form belongs to: its secrets, and whether it matches case-blind."""

    ids: frozenset
    fold: bool


def marker(ids) -> str:
    """The marker for ``ids``: sorted, each ``name/key``, joined by ``|``."""
    return "[secret:" + "|".join(f"{n}/{k}" for n, k in sorted(ids)) + "]"


def _ids_in(found: str) -> frozenset:
    """The ids a marker found by `MARKER` names."""
    return frozenset(
        tuple(part.split("/", 1)) for part in found[len("[secret:") : -1].split("|")
    )


def escapes(text: str) -> set[str]:
    """Every spelling a value can come back in through a page, but not its encodings.

    A submitting write lands the browser on `?q=<what was typed>`, and the
    browser percent-encodes it on the way — so `a/b` comes back as `a%2Fb` and a
    literal replacement misses it entirely. Both quoting styles are covered
    because a form submission uses `+` for spaces and a path does not.

    A page echoes it too, and capture keeps what the page said: a JSON body
    escapes `"` and `\\`, maybe `/`, and maybe every non-ASCII letter; an HTML
    one escapes `&`, `<`, `>` and maybe the quotes, the apostrophe as `&#x27;`
    or `&#39;`. Other servers spell them their own ways: Go's encoding/json
    writes `<` `>` `&` as `\\u003c` and so on, a JSON encoder may write its hex
    in capitals, PHP the apostrophe as `&#039;`, HTML5 as `&apos;`, and Go's
    html/template `"` as `&#34;` and `+` as `&#43;`. Each of those spellings is
    a form, or a body or a console line would hand the value to whoever reads
    it — a fixed few per value, so the sweep's cost does not grow with it.
    """
    forms = {text, quote(text, safe=""), quote_plus(text)}
    for ascii_only in (True, False):
        escaped = json.dumps(text, ensure_ascii=ascii_only)[1:-1]
        forms.add(escaped)
        forms.add(escaped.replace("/", "\\/"))
        if ascii_only:  # the same escapes, their hex in capitals
            forms.add(HEX_ESCAPE.sub(lambda m: "\\u" + m[0][2:].upper(), escaped))
        else:  # Go's encoding/json: UTF-8, but < > & as \u00XX
            forms.add(escaped.translate(GO_JSON))
    bare = html.escape(text, quote=False)
    for quot, apos, plus in HTML_QUOTES:
        forms.add(bare.replace('"', quot).replace("'", apos).replace("+", plus))
    return forms


def _cores(data: bytes) -> set[str]:
    """The base64 text that is the same wherever ``data`` sits in a longer one.

    Base64 reads three bytes as four characters, so ``data`` at offset 0, 1 or 2
    of a longer string is spelt three ways. Each is its encoding with the
    characters that depend on a neighbour dropped: the leading ones (shared with
    the bytes before) and, unless the length lands on a boundary, the last.
    """
    cores = set()
    for k in range(3):
        padded = b"\0" * k + data
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            core = encode(padded).decode().rstrip("=")[math.ceil(8 * k / 6) :]
            if len(padded) % 3:
                core = core[:-1]
            if len(core) >= B64_CORE_MIN:
                cores.add(core)
        if k == 0:  # the whole string, `=` padding and all, is one form too
            for encode in (base64.b64encode, base64.urlsafe_b64encode):
                whole = encode(data).decode()
                if len(whole) >= B64_CORE_MIN:
                    cores.add(whole)
    return cores


class Forms(Mapping):
    """An immutable mapping from a form to its `Label`. Hashed by nothing."""

    __slots__ = ("_forms", "_ids", "_scanner")

    def __init__(self, forms: Mapping[str, Label] | None = None) -> None:
        self._forms: dict[str, Label] = dict(forms or {})
        self._ids: frozenset | None = None
        self._scanner: Scanner | None = None

    @classmethod
    def of(cls, value, ident: Id | None) -> Forms:
        """Every spelling of one value, labelled with ``ident``.

        Coerced, not skipped: `Actions.write` does `str(text)`, so a secret whose
        stored value is not a string really is typed into the page.
        """
        if value is None:
            return cls()
        text = value if isinstance(value, str) else str(value)
        if not text:
            return cls()
        ids = frozenset({ident}) if ident is not None else frozenset()
        folded = escapes(text)
        stripped = text.replace("\r", "").replace("\n", "")
        if stripped != text and stripped:
            folded |= escapes(stripped)
        folded.add(repr(text)[1:-1])
        folded.add(text.encode("utf-8", "surrogatepass").hex())
        folded.add(text[::-1])
        # ASCII folding cannot reach `é` -> `É`, so the whole-string upper case
        # is a form of its own (its ASCII part is folded with the rest).
        folded.add(text.upper())
        forms = {form: Label(ids, True) for form in folded if form}
        for core in _cores(text.encode("utf-8", "surrogatepass")):
            forms[core] = Label(ids, forms[core].fold if core in forms else False)
        return cls(forms)

    def __getitem__(self, form: str) -> Label:
        return self._forms[form]

    def __iter__(self) -> Iterator[str]:
        return iter(self._forms)

    def __len__(self) -> int:
        return len(self._forms)

    def __or__(self, other: Forms) -> Forms:
        if not isinstance(other, Forms):
            return NotImplemented
        if not other._forms:
            return self
        if not self._forms:
            return other
        merged = dict(self._forms)
        for form, label in other._forms.items():
            had = merged.get(form)
            merged[form] = (
                label
                if had is None
                else Label(had.ids | label.ids, had.fold or label.fold)
            )
        return Forms(merged)

    @property
    def ids(self) -> frozenset:
        if self._ids is None:
            labels = self._forms.values()
            self._ids = frozenset().union(*(label.ids for label in labels))
        return self._ids

    @property
    def scanner(self) -> Scanner:
        if self._scanner is None:
            self._scanner = Scanner(self)
        return self._scanner


class Scanner:
    """Finds a `Forms` in text: one `find` loop per form."""

    def __init__(self, forms: Forms, short: int = SHORT) -> None:
        self._short = short
        exact: dict[str, frozenset] = {}
        folded: dict[str, frozenset] = {}
        for form, (ids, fold) in forms.items():
            into, key = (folded, form.translate(ASCII_LOWER)) if fold else (exact, form)
            into[key] = into.get(key, frozenset()) | ids
        # Longest first: a short form inside a longer one is the longer's span
        # either way, but a stable order keeps the result the same every time.
        self._exact = sorted(exact.items(), key=lambda item: -len(item[0]))
        self._folded = sorted(folded.items(), key=lambda item: -len(item[0]))
        self._ids = forms.ids

    def spans(self, text: str, sheltered: bool = True) -> list[Span]:
        """Every place a form is in ``text``: `(start, end, ids)`, unmerged.

        ``sheltered`` lets a match inside a marker of this scanner's own ids
        stand aside (ruling 20), which is right for not scrubbing twice and
        wrong for asking whether a value is present.
        """
        if not text or not (self._exact or self._folded):
            return []
        found: list[Span] = []
        self._find(text, self._exact, found)
        if self._folded:
            self._find(text.translate(ASCII_LOWER), self._folded, found, text)
        if not found:
            return []
        return self._outside_markers(text, found) if sheltered else found

    def _find(
        self, haystack: str, needles, found: list[Span], original: str | None = None
    ) -> None:
        edges = original if original is not None else haystack
        for needle, ids in needles:
            width, at = len(needle), haystack.find(needle)
            alone = width < self._short
            while at != -1:
                end = at + width
                if not alone or (
                    (at == 0 or not edges[at - 1].isalnum())
                    and (end == len(edges) or not edges[end].isalnum())
                ):
                    found.append((at, end, ids))
                at = haystack.find(needle, at + 1)

    def _outside_markers(self, text: str, found: list[Span]) -> list[Span]:
        """Drop a match lying wholly inside a marker of this scanner's own ids.

        Opaque only to its own secrets (ruling 20): a page that writes a marker
        around a value with other ids gains nothing.
        """
        if "[secret:" not in text:
            return found
        own = [
            (m.start(), m.end())
            for m in MARKER.finditer(text)
            if _ids_in(m[0]) <= self._ids
        ]
        if not own:
            return found
        return [
            span
            for span in found
            if not any(s <= span[0] and span[1] <= e for s, e in own)
        ]


def merge(spans) -> list[Span]:
    """Overlapping or touching spans as one, holding the union of their ids."""
    merged: list[Span] = []
    for start, end, ids in sorted(spans, key=lambda span: (span[0], span[1])):
        if merged and start <= merged[-1][1]:
            was = merged[-1]
            merged[-1] = (was[0], max(was[1], end), was[2] | ids)
        else:
            merged.append((start, end, frozenset(ids)))
    return merged


def render(text: str, spans, mask: str = MASK) -> str:
    """``text`` with each merged span shown as its marker, or ``mask`` if none."""
    out, at = [], 0
    for start, end, ids in merge(spans):
        out.append(text[at:start])
        out.append(marker(ids) if ids else mask)
        at = end
    out.append(text[at:])
    return "".join(out)


def scrub(text: str, forms: Forms) -> str:
    """``text`` with every value in ``forms`` shown as its marker.

    Needed because an action puts its arguments in its error text: `upload_file`
    naming a kept file that is not there raises `no file at <uri>`, and a bad URL
    comes back from Selenium with the URL in it. A mangled message beats a
    leaked credential.
    """
    if not forms or not isinstance(text, str) or not text:
        return text
    spans = forms.scanner.spans(text)
    return render(text, spans) if spans else text


def scrub_values(obj, forms: Forms):
    """``obj`` with every value in ``forms`` replaced, however deep it sits.

    Keys too, below the top: a script can return an object keyed by what was
    typed. The top level's keys are the response's own fields, named by this
    code and never a secret, so they are left as they are. Two keys that scrub
    to the same one keep one, holding the later key's value.
    """
    if isinstance(obj, dict):
        return {key: _deep(value, forms) for key, value in obj.items()}
    return _deep(obj, forms)


def _deep(obj, forms: Forms):
    if isinstance(obj, str):
        return scrub(obj, forms)
    if isinstance(obj, dict):
        return {
            scrub(key, forms) if isinstance(key, str) else key: _deep(value, forms)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [_deep(value, forms) for value in obj]
    return obj


def taints(text, forms: Forms) -> bool:
    """Whether any value in ``forms`` is actually present in ``text``.

    Asked directly rather than inferred by comparing a string with its scrubbed
    form: a marker is evidence of nothing, the question is whether the value is
    in the text. So the marker shelter of ruling 20, which is about not
    scrubbing twice, does not apply: a secret whose value is its own marker is
    still present where the marker is.
    """
    if not forms or not isinstance(text, str) or not text:
        return False
    return bool(forms.scanner.spans(text, sheltered=False))


class Unscrubbed(Exception):
    """An exception still carried a value after `scrub_error` walked it.

    A fixed, value-free message. The caller's choke point (the scrubber) turns
    it into the call's own failure (`faults.ScrubFailed`).
    """

    def __init__(self) -> None:
        super().__init__("an error could not be scrubbed of a secret's value")


def scrub_error(exc: BaseException, forms: Forms) -> None:
    """Scrub ``exc`` in place, and what it was raised from. The type is kept.

    Every `str` in `args`; every `str`, or list of `str`, among its attributes
    (Selenium's `msg` and `stacktrace`); and its notes — through `__cause__` and
    `__context__`, each once. Kept in place so `errors.status_for` reads the same
    status and so a log line written from it is clean.

    State this cannot reach (an `OSError`'s `filename`, `UnicodeError.object`, a
    class's own `__slots__`) is caught by checking `str()` of each exception
    afterwards: one that still carries a value raises `Unscrubbed`, from nothing,
    so the value is in neither its message nor its chain. Fails closed.
    """
    if not forms:
        return
    seen: set[int] = set()
    chain: list[BaseException] = []
    pending = [exc]
    while pending:
        err = pending.pop()
        if err is None or id(err) in seen:
            continue
        seen.add(id(err))
        chain.append(err)
        err.args = tuple(scrub(a, forms) if isinstance(a, str) else a for a in err.args)
        attributes = getattr(err, "__dict__", None)
        if attributes:
            for name, value in list(attributes.items()):
                if isinstance(value, str):
                    attributes[name] = scrub(value, forms)
                elif isinstance(value, list) and all(
                    isinstance(v, str) for v in value
                ):
                    attributes[name] = [scrub(v, forms) for v in value]
        notes = getattr(err, "__notes__", None)
        if isinstance(notes, list):
            notes[:] = [scrub(n, forms) if isinstance(n, str) else n for n in notes]
        pending.extend((err.__cause__, err.__context__))
    for err in chain:
        try:
            said = str(err)
        except Exception:  # noqa: BLE001 - a str() that fails cannot be vouched for
            said = None
        # Sheltered: this checks text `scrub` produced, whose markers are not residue.
        if said is None or forms.scanner.spans(said):
            raise Unscrubbed from None
