"""Keeping a value nobody may see out of everything a run says.

The forms, the marker and the scanner are `redaction`'s; this adds which result
fields carry an argument's value back out. `secrets.perform_write` uses it for a
single bound write as well.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..redaction import Forms as Forms
from ..redaction import escapes
from ..redaction import scrub as scrub
from ..redaction import scrub_values as scrub_values
from ..redaction import taints as taints

# A result field carries an argument's value back out under the same name
# (`navigate(url=...)` answers with `url`) or under these: `write` returns
# what the field holds as `value`.
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

    For capture's learned credentials (cookies, Authorization tokens), which
    have no catalogue entry and no marker: these forms, not `Forms.of`'s.
    """
    forms = set()
    for value in values:
        if value is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if text:
            forms |= escapes(text)
    return forms
