"""What the page can tell you about its own elements.

A wait that times out says the element never became clickable, which is true and
almost never the diagnosis. The page usually knows: the link is inside a menu
whose ancestor is `display: none`, a banner is on top of it, it is below the
fold, it is disabled, or it is there with no size at all. Each of those has a
different next move, and an agent told only "timed out" guesses.

Two callers read the same answer. :func:`explain` decorates a failure with it,
and :func:`outline` hands it out *before* anything is tried — the map an agent
would otherwise build by hand out of three `execute_script` DOM dumps, which is
what the first pilot report said cost it the most.

They share `_HELPERS`, because a second copy of this reasoning is how the error
and the map start disagreeing about the same element (saga §F2.8).

Script, not CDP: the answer has to be the same on Chrome and on Firefox, which
is a promise every other action in this package already keeps.
"""

from __future__ import annotations

import logging

from . import js

log = logging.getLogger(__name__)

# How many elements `outline` returns unless asked for more. A page map is only
# cheaper than reading the DOM if it stays small.
DEFAULT_LIMIT = 50

# What counts as worth listing when a caller has not asked for everything: the
# things an agent can act on. `[role]` is here because a div with a role is a
# button someone built by hand, and those are exactly the ones a tag-based list
# misses.
# `iframe` is here because `frame` is an action and needs a selector for one -
# a page whose real content is inside a frame would otherwise map to nothing.
# The roles are listed rather than matched with `[role]`: that also catches
# `main`, `navigation`, `region` and `heading`, and a page's structural
# containers would eat the budget before its buttons were reached.
ACTIONABLE_ROLES = (
    "button",
    "link",
    "checkbox",
    "radio",
    "switch",
    "tab",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "option",
    "textbox",
    "combobox",
    "searchbox",
    "slider",
    "spinbutton",
)

# What a page says about a control that opens something else. All three are
# declarations rather than guesses, and the one that matters is `aria-expanded`:
# the trigger gating half a real site's nav was an `<a>` with no `href`, so the
# tag-and-href rules above skipped it and the map had no way to say what opened
# the menu it had just listed as hidden (saga §F2.10).
DECLARES_A_CONTROL = ("aria-expanded", "aria-controls", "aria-haspopup")

INTERACTIVE = (
    # `input:not([type="hidden"])`: a hidden field cannot be clicked or typed
    # into, and a form with thirty of them would fill the budget before a
    # single visible control was reached.
    'a[href], button, input:not([type="hidden"]), select, textarea, '
    "summary, label, iframe, "
    # A bare `<a>` — no href — is almost always a control somebody wired up in
    # JavaScript. The one false positive is the legacy `<a name="top">` bookmark,
    # which costs an entry with an empty name; missing a nav toggle costs the
    # whole nav.
    "a:not([href]), "
    '[onclick], [contenteditable=""], [contenteditable="true"], '
    '[tabindex]:not([tabindex="-1"]), '
    + ", ".join(f"[{attr}]" for attr in DECLARES_A_CONTROL)
    + ", "
    + ", ".join(f'[role="{role}"]' for role in ACTIONABLE_ROLES)
)

# The scripts, from `js/`. `_HELPERS` is shared by both because a second copy of
# this reasoning is how the error and the map start disagreeing about the same
# element (saga §F2.8) - and concatenation rather than an import because
# `execute_script` hands the browser one string and has no module loader.
_HELPERS_FILE = "helpers.js"

# One element, as `explain` reads it.
USABLE_JS = js.script(_HELPERS_FILE, "usable.js")

# The map. Every entry carries a selector that is checked to match exactly one
# element before it is handed out - a selector an agent has to verify itself is
# most of the work it came here to avoid.
OUTLINE_JS = js.script(_HELPERS_FILE, "outline.js")


# What to do about it, in the same voice as the error it is appended to: the
# reason, then the move. The move is the part a timeout never had.
SENTENCES = {
    # Deliberately says nothing about HOW it opens. That belongs to `MOVES`,
    # which knows: a base sentence asserting "a menu that opens on mouse-over
    # looks exactly like this" and then advising a click contradicts itself in
    # two consecutive clauses (Copilot, #31).
    "hidden": "It exists, but {detail} is hidden.{trigger}",
    "covered": (
        "It exists, but {detail} is on top of it — a cookie banner or an "
        "overlay. Dismiss that first, or act on it instead."
    ),
    "zero_size": (
        "It exists but has no size, so there is nothing to click. It may still "
        "be rendering, or it may be a wrapper whose content has not arrived."
    ),
    "offscreen": (
        "It exists but is outside the viewport. "
        'interact(action="scroll_to") brings it into view first.'
    ),
    "disabled": (
        "It exists but is disabled, so it will not respond until something on "
        "the page enables it — usually a form that is not valid yet."
    ),
}


# The move, which is not the same move for every menu. A trigger carrying
# `aria-expanded` is a control somebody CLICKS: on selenium.dev the advice said
# hover, hovering did nothing, and the page had been saying `expanded: false`
# the whole time (saga §F2.10). The :hover caveat belongs only where the answer
# actually is a hover — appended to "click this" it reads as a contradiction.
MOVES = {
    "click": (
        " {trigger} opens it and says so with aria-expanded, so "
        'interact(action="click") on that — hovering it will do nothing.'
    ),
    "hover": (
        " A menu that opens on mouse-over looks exactly like this: hover "
        'whatever reveals it — interact(action="hover") on {trigger}. You '
        "cannot hover the hidden part itself, and a script cannot open one "
        "either, because synthetic events do not set :hover."
    ),
    "": (
        " A menu that opens on mouse-over looks exactly like this. Hover "
        "whatever reveals it; you cannot hover the hidden part itself, and a "
        "script cannot open one either, because synthetic events do not set "
        ":hover."
    ),
}


def move_for(answer: dict) -> str:
    """The instruction clause for a hidden element, or "" when there is none."""
    trigger = answer.get("trigger")
    if not trigger:
        return MOVES[""]
    gesture = answer.get("gesture") or "hover"
    return MOVES.get(gesture, MOVES["hover"]).format(trigger=trigger)


def usable(driver, element) -> dict:
    """Ask the page whether ``element`` can be used, and why not."""
    return driver.execute_script(USABLE_JS, element) or {}


def outline(driver, scope=None, text="", limit=DEFAULT_LIMIT, interactive=True):
    """Every element worth acting on under ``scope``, with a checked selector.

    Returns ``(elements, total)``: the page's content before its chrome, cut to
    ``limit``, and how many matched before the cut.
    """
    found = driver.execute_script(
        OUTLINE_JS, scope, text or "", int(limit), bool(interactive), INTERACTIVE
    )
    if isinstance(found, dict):
        elements = found.get("elements") or []
        return elements, int(found.get("total") or len(elements))
    # A list is what the script answered before it counted; a driver double
    # that still returns one is answered the same way.
    elements = found or []
    return elements, len(elements)


def explain(driver, target) -> str:
    """One sentence about why the element matching ``target`` cannot be used.

    Empty when there is nothing useful to add: no element, a usable one, or a
    probe that failed. **This runs inside a failure path**, so it never raises
    and never replaces the error it is decorating — a diagnosis that throws
    would hide the timeout that prompted it.
    """
    try:
        found = driver.find_elements(*target)
        if not found:
            return ""
        answer = usable(driver, found[0])
        sentence = SENTENCES.get(answer.get("reason") or "")
        if not sentence:
            return ""
        return sentence.format(
            detail=answer.get("detail") or "something",
            trigger=move_for(answer),
        )
    # Broad on purpose: a diagnosis must never outrank the failure it decorates.
    except Exception:
        log.debug("could not probe %r", target, exc_info=True)
        return ""
