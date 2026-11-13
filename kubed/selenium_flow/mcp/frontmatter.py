"""The YAML header of a markdown file, shared by both extensions.

``skill_extension`` and ``agents`` each read a header and each check that it
survives JSON. They import it from here, not from one another, so either can be
deleted alone.
"""

from __future__ import annotations

import json

import yaml


def split(text: str) -> tuple[object, str] | None:
    """The header as YAML parses it, and the body, or None without a header.

    The body is every line after the closing ``---``, joined by ``"\\n"``. None
    when the first line is not ``---``, the header is unterminated, or its YAML
    fails. A ``---`` inside a value is not the end: only a line that is ``---``
    after ``rstrip`` closes it.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    for end, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            try:
                header = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError:
                return None
            return header, "\n".join(lines[end + 1 :])
    return None


def survives_json(value: object) -> bool:
    """Whether the value arrives as written after a trip through JSON.

    A YAML-only value fails to encode, and a non-string key (1 beside "1")
    encodes but comes back renamed or lost.
    """
    try:
        return json.loads(json.dumps(value, allow_nan=False)) == value
    except (TypeError, ValueError):
        return False
