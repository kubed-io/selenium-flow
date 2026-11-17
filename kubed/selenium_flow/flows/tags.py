"""A flow's tags, and the filter that picks flows by them.

A tag is a short lowercase string and nothing more to the code: `auth:ldap` and
`auth/ldap` are structure for a person, never split here. This module is the
only place that knows the rule.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

TAGS = "tags"
MAX_TAGS = 20
TAG = re.compile(r"[a-z0-9][a-z0-9:_\-/.]{0,49}")
RULE = (
    "a tag is 1 to 50 characters of lowercase letters, digits, :, _, -, / and ., "
    "starting with a letter or a digit"
)
SHAPE = "tags must be a list of tags, such as [auth:ldap, smoke]"

DESCRIPTION = (
    "Tags that group this flow, at most 20. " + RULE[0].upper() + RULE[1:] + "; "
    "case does not matter and a tag is saved lowercase. Read "
    "workspace://current/tags and reuse a tag already in use before making up "
    "a new one. Tags never decide where a flow applies: sites and paths do."
)

# The published shape, for `flow://schema` and the REST spec.
SCHEMA = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 50},
    "maxItems": MAX_TAGS,
    "description": DESCRIPTION,
}


def _one(tag) -> str:
    """``tag`` trimmed and lowercased, or ValueError naming it and the rule."""
    folded = tag.strip().lower() if isinstance(tag, str) else None
    if folded is None or not TAG.fullmatch(folded):
        raise ValueError(f"tag {tag!r} is refused: {RULE}")
    return folded


def read(value) -> list[str]:
    """``value``'s tags, each trimmed and lowercased, a repeat dropped and the
    order kept. Raises ValueError naming a bad tag and the rule."""
    if not isinstance(value, list):
        raise ValueError(SHAPE)
    found: list[str] = []
    for tag in value:
        folded = _one(tag)
        if folded not in found:
            found.append(folded)
    if len(found) > MAX_TAGS:
        raise ValueError(
            f"a flow takes at most {MAX_TAGS} tags, and this one has {len(found)}"
        )
    return found


@dataclass(frozen=True)
class Filter:
    """Rows of tags: a flow passes when it carries every tag of any one row."""

    rows: tuple[frozenset[str], ...]

    @classmethod
    def parse(cls, rows) -> Filter:
        """Rows of comma-joined tags, each read as a saved tag is.

        An empty row, or one holding something that is not a tag, is refused
        naming the row. No rows passes no flow.
        """
        if not isinstance(rows, (list, tuple)):
            raise ValueError(
                "a tag filter is a list of rows, such as [auth:ldap,smoke]"
            )
        parsed = []
        for row in rows:
            if not isinstance(row, str):
                raise ValueError(
                    f"filter row {row!r} is not a row: write tags joined by commas"
                )
            if not row.strip():
                raise ValueError(
                    f"filter row {row!r} is empty: write tags joined by commas"
                )
            try:
                parsed.append(frozenset(_one(tag) for tag in row.split(",")))
            except ValueError as exc:
                raise ValueError(f"filter row {row!r}: {exc}") from None
        return cls(tuple(parsed))

    def passes(self, tags: Iterable[str]) -> bool:
        carried = set(tags)
        return any(row <= carried for row in self.rows)
