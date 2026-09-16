"""MCP tool annotations, built in one place.

A client reads these to decide how to present a tool and whether to ask the user
before running it — ChatGPT skips the confirmation prompt for a read-only tool,
and Claude uses them to judge how freely a tool can be called. They are advisory
hints, never a security boundary, so the only thing that matters is that they are
HONEST about what the tool does.

Leaving them off is not neutral. MCP's defaults are ``readOnlyHint: false`` and
``destructiveHint: true``, so an unannotated tool is presented as the most
dangerous thing on the server — which is how the three mirror tools here
(``current_session``, ``session_files``, ``selenium_flow_skill``) came to be
advertised as destructive when every one of them only reads.

This module has no intra-package imports on purpose: every surface that
registers a tool needs it, so it must not be able to close an import cycle.
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

    ``destructive`` is the one worth arguing about, and the rule this repo
    follows is: True wherever the tool hands the page an instruction the page is
    free to interpret — a click, a keypress, an answered confirm dialog,
    arbitrary JavaScript. None of those are destructive in themselves, and any
    of them can place an order or delete a record, and this server cannot tell
    which. Claiming otherwise to save a confirmation prompt would be trading the
    user's safety for our convenience.

    ``open_world`` is True for anything that reaches the browser or the Grid,
    and False only for a tool answered entirely from inside this package — the
    embedded skill being the one case.

    Keys are snake_case, not the camelCase from the MCP spec. Both are accepted
    and both serialise to camelCase on the wire, but MCP SDK v2 renamed the
    Python fields and now warns on the camelCase ones.
    """
    return {
        "title": title,
        "read_only_hint": read_only,
        "destructive_hint": destructive,
        "idempotent_hint": idempotent,
        "open_world_hint": open_world,
    }


# The three mirror tools are all pure reads of state this server already holds,
# so they share one shape and are spelled once here rather than three times.
def reads(title: str, *, open_world: bool = True) -> dict:
    """A tool that only reads. Safe to call repeatedly, changes nothing."""
    return hints(title, read_only=True, idempotent=True, open_world=open_world)
