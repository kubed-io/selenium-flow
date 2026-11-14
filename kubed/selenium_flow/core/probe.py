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

import json
import logging

from . import js

log = logging.getLogger(__name__)

# How many elements `outline` returns unless asked for more. A page map is only
# cheaper than reading the DOM if it stays small.
DEFAULT_LIMIT = 50

# How many matches of one selector a wait will ask about per poll. `is_displayed`
# is a round trip per element, so a broad selector (`//a`) with hundreds of hidden
# matches would make each poll take seconds, inside the session's lock. Past this
# the timeout says only the first of them were tried (spec 2026-11-13, ruling 6).
CANDIDATES = 50

# How many of the matches a timeout lists, each with its reason. The rest are
# counted ("and N more"): the first few are the ones a person acts on.
LISTED = 5

# How many levels inside the element a click may land before it is worth a
# warning (spec 2026-11-13, ruling 13). A button's own label is depth 1.
DEEP = 2

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

# Every match of a selector, each answered as `USABLE_JS` answers one.
USABLES_JS = js.script(_HELPERS_FILE, "usables.js")

# What receives a pointer at an element's click point: the page's half of a
# click's `received_by` and of the intercept's explanation.
RECEIVER_JS = js.script(_HELPERS_FILE, "receiver.js")

# The map. Every entry carries a selector that is checked to match exactly one
# element before it is handed out - a selector an agent has to verify itself is
# most of the work it came here to avoid.
OUTLINE_JS = js.script(_HELPERS_FILE, "outline.js")


# What to do about it, in the same voice as the error it is appended to: the
# reason, then the move. The move is the part a timeout never had.
SENTENCES = {
    "none": "Nothing on the page matches it.",
    # Deliberately says nothing about HOW it opens. That belongs to `MOVES`,
    # which knows: a base sentence asserting "a menu that opens on mouse-over
    # looks exactly like this" and then advising a click contradicts itself in
    # two consecutive clauses (Copilot, #31).
    "hidden": "It exists, but {detail} is hidden.{trigger}",
    "covered": (
        "It exists, but {detail} is on top of it at its centre and would "
        "receive the click instead. If that is what you meant, act on it."
    ),
    "overlay": (
        "It exists, but {detail} is on top of it, fixed or stacked above the "
        "page like a cookie banner or an overlay. Dismiss that first, or act "
        "on it instead."
    ),
    "unpainted": (
        "It exists, but it paints nothing at its centre, so the click goes "
        "through to {detail} behind it. Click a painted part of it (its text "
        "or icon), or the element that carries the interaction."
    ),
    # Not "it may be the real target": a transparent layer on top really does
    # take the click (Penpot's comment layer, reproduced for #138), and what to
    # do is click it, or something inside it, on purpose.
    "invisible_cover": (
        "It exists, but an invisible element, {detail}, is on top of it at "
        "its centre and takes the click there. Clicking that, or something "
        "inside it, may be what you want{selector}."
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

# One clause per reason, for the list a timeout gives when several matched.
SHORT = {
    "hidden": "{detail} is hidden",
    "covered": "{detail} is on top of it",
    "overlay": "{detail}, an overlay, is on top of it",
    "unpainted": "it paints nothing at its centre",
    "invisible_cover": "an invisible {detail} on top of it takes the pointer there",
    "zero_size": "it has no size",
    "offscreen": "it is outside the viewport",
    "disabled": "it is disabled",
    "none": "the page reports nothing wrong with it",
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
    # Only when no trigger was found and a stylesheet could not be read: a
    # guess, and said as one (spec 2026-11-13, ruling 12).
    "unread": (
        " A stylesheet on this page couldn't be read, so if {detail} opens on "
        "mouse-over, hover what opens it."
    ),
}


def move_for(answer: dict) -> str:
    """The instruction clause for a hidden element, or "" when there is none."""
    answer = _answer(answer)
    trigger = answer.get("trigger")
    if trigger:
        gesture = answer.get("gesture") or "hover"
        return MOVES.get(gesture, MOVES["hover"]).format(trigger=trigger)
    if answer.get("unread"):
        return MOVES["unread"].format(detail=answer.get("detail") or "something")
    return ""


def _answer(answer) -> dict:
    """An answer from the page, or {} when it is not one (null, a string)."""
    return answer if isinstance(answer, dict) else {}


def _key(answer: dict) -> str:
    """Which `SENTENCES` / `SHORT` row an answer is."""
    answer = _answer(answer)
    reason = answer.get("reason") or ""
    if reason == "covered" and answer.get("overlay"):
        return "overlay"
    return reason


def short(answer: dict) -> str:
    """The `SHORT` clause for one answer."""
    answer = _answer(answer)
    clause = SHORT.get(_key(answer) or "none", SHORT["none"])
    return clause.format(detail=answer.get("detail") or "something")


def received(driver, element) -> dict:
    """What receives a pointer at ``element``'s click point, or {}.

    Runs inside a gesture that is about to happen, so it never raises: a probe
    that fails leaves the report's fields out and costs the gesture nothing.
    """
    try:
        answer = driver.execute_script(RECEIVER_JS, element, INTERACTIVE)
        if isinstance(answer, dict) and "inside" in answer:
            return answer
    # Broad on purpose: a diagnosis must never outrank the gesture it reports on.
    except Exception as exc:  # noqa: BLE001
        # The type only: a lost node's message quotes the node and the Grid id.
        log.debug("could not read what receives the click: %s", type(exc).__name__)
    return {}


def receiver_report(answer: dict, gesture: str = "click") -> dict:
    """The ``received_by`` and ``warning`` a click-like gesture returns.

    Only the receiver's name, its depth and the selector the page offered go in:
    never a value from the page (spec 2026-11-13, rulings 13 and 14).
    """
    answer = _answer(answer)
    if "inside" not in answer:
        return {}
    name = str(gesture).replace("_", "-")
    element = answer.get("element") or "something"
    painted = bool(answer.get("painted"))
    if not answer.get("inside"):
        if gesture == "click":
            return {}
        why = short(
            {
                "reason": answer.get("reason"),
                "detail": element,
                "overlay": answer.get("overlay"),
            }
        )
        warning = (
            f"The {name} went to {element}, not to the element you selected, "
            f"because {why}."
        )
        chosen = answer.get("selector")
        if chosen:
            warning += (
                f" If that is what you meant, it is selector={json.dumps(chosen)}."
            )
        return {
            "received_by": {"element": element, "depth": None, "painted": painted},
            "warning": warning,
        }
    depth = answer.get("depth")
    report = {"received_by": {"element": element, "depth": depth, "painted": painted}}
    if (
        isinstance(depth, int)
        and depth >= 1
        and (not painted or depth > DEEP)
        and not answer.get("interactive")
    ):
        levels = "1 level" if depth == 1 else f"{depth} levels"
        nothing = "" if painted else ", which paints nothing there"
        report["warning"] = (
            f"The {name} landed on {element}, {levels} inside the element you "
            f"selected{nothing}, and nothing in between is a control: only a "
            f"handler on the selected element, or above it, sees the {name}. "
            "If you meant something inside it, select that instead."
        )
    return report


def _sentence(answer: dict) -> str:
    answer = _answer(answer)
    sentence = SENTENCES.get(_key(answer))
    if not sentence:
        return ""
    chosen = answer.get("selector")
    return sentence.format(
        detail=answer.get("detail") or "something",
        trigger=move_for(answer),
        selector=": try selector=" + json.dumps(chosen) if chosen else "",
    )


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
    elements = found.get("elements") or []
    return elements, int(found.get("total") or len(elements))


def usables(driver, elements: list) -> list[dict]:
    """Ask the page about each of ``elements`` in one round trip."""
    answers = driver.execute_script(USABLES_JS, list(elements)) or []
    return [_answer(answer) for answer in answers]


def explain(driver, target, element=None) -> str:
    """What the page says about why ``target`` cannot be used.

    With ``element``, that element's sentence: the intercept asks about the one
    it clicked, not the first match. Without, by how many matched: none says so,
    one gets its sentence, several are listed, each with its reason, and the
    move of the first that has one.

    Empty when there is nothing useful to add: a usable element, or a probe
    that failed. **This runs inside a failure path**, so it never raises and
    never replaces the error it is decorating — a diagnosis that throws would
    hide the timeout that prompted it.
    """
    try:
        if element is not None:
            return _sentence(usable(driver, element))
        found = driver.find_elements(*target)
        if not found:
            return SENTENCES["none"]
        if len(found) == 1:
            return _sentence(usable(driver, found[0]))
        asked = found[:LISTED]
        # Paired with what was asked, so a short reply from the page costs its
        # missing entries a "nothing wrong" and never the count.
        listed = usables(driver, asked)
        listed += [{}] * (len(asked) - len(listed))
        listed = listed[: len(asked)]
        items = [f"[{i}] {short(answer)}" for i, answer in enumerate(listed)]
        more = len(found) - len(asked)
        if more > 0:
            items.append(f"and {more} more")
        text = (
            f"{len(found)} elements match it, and none could be used: "
            + "; ".join(items)
            + "."
        )
        if len(found) > CANDIDATES:
            text += f" Only the first {CANDIDATES} are tried; narrow the selector."
        for i, answer in enumerate(listed):
            move = move_for(answer)
            if move:
                text += f" [{i}]:{move}"
                break
        return text
    # Broad on purpose: a diagnosis must never outrank the failure it decorates.
    except Exception:
        log.debug("could not probe %r", target, exc_info=True)
        return ""
