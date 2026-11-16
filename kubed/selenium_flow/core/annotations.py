"""MCP tool annotations.

A client reads these to decide whether to ask the user before running a tool.
MCP's defaults are ``readOnlyHint: false`` and ``destructiveHint: true``, so an
unannotated tool is presented as destructive.

No intra-package imports: every surface that registers a tool needs this module,
so it must not be able to close an import cycle.
"""

from __future__ import annotations


def hints(
    title: str,
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = True,
) -> dict:
    """Annotations for one tool.

    ``destructive`` is True wherever the tool hands the page an instruction it
    is free to interpret (a click, a keypress, an answered confirm, arbitrary
    JavaScript): any of them can place an order or delete a record.

    ``open_world`` is False only for a tool answered entirely from inside this
    package, such as the embedded skill.

    Keys are snake_case: both spellings serialise to camelCase on the wire, but
    MCP SDK v2 warns on the camelCase fields.
    """
    return {
        "title": title,
        "read_only_hint": read_only,
        "destructive_hint": destructive,
        "idempotent_hint": idempotent,
        "open_world_hint": open_world,
    }


def reads(title: str, *, open_world: bool = True) -> dict:
    """A tool that only reads. Safe to call repeatedly, changes nothing."""
    return hints(title, read_only=True, idempotent=True, open_world=open_world)
