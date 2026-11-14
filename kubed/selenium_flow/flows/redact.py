"""Keeping a value nobody may see out of everything a run says.

A secret is typed into the page and must come back out nowhere: not in a step's
result, its error, the page it landed on, the run's own URL, or a log line. This
is the sweep, and it is a security primitive rather than a part of running a
flow — `secrets.perform_write` uses it for a single bound write as well.

The forms, the marker and the scanner live in `redaction`; this keeps the
flow-shaped parts: which result fields carry an argument's value back out.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..redaction import Forms as Forms
from ..redaction import escapes
from ..redaction import scrub as scrub
from ..redaction import scrub_values as scrub_values
from ..redaction import taints as taints

# A result field carries an argument's value back out. Most do it by having the
# same name — `navigate(url=...)` answers with the page state, whose `url` is
# the one it was given — and one does it under a different name: `write` reads
# the field back and returns it as `value`, deliberately, so a caller can
# confirm the text landed.
#
# Both routes have to be closed. Redacting only `value` left a magic-link token
# bound to `url` coming straight back in the result, in the run's top-level
# `url`, and — worst of the three — in `Workspaces.touch`, which persists it to
# Redis as the page a later reopen should return to.
RESULT_FROM_ARGUMENT = {"text": "value", "script": "result"}


def redacted_fields(guarded: Iterable[str]) -> set:
    """Result fields that would carry a guarded argument's value back out."""
    fields = set(guarded)
    for argument, field in RESULT_FROM_ARGUMENT.items():
        if argument in guarded:
            fields.add(field)
    return fields


def hidden_forms(values) -> set:
    """Every spelling of each value that a page can echo it in: `escapes` over them.

    Capture's learned credentials (cookies, Authorization tokens) are not
    catalogued and have no marker to show, so they keep these forms and not the
    wider set `Forms.of` makes (ruling 19).
    """
    forms = set()
    for value in values:
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if text:
            forms |= escapes(text)
    return forms
