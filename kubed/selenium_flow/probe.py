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

# Shared by both scripts below. `reasonFor` is the whole point of the module:
# one answer to "can this be used, and if not, what is in the way".
_HELPERS = """
const nameOf = (node) => {
  if (!node || !node.tagName) return 'something';
  const tag = node.tagName.toLowerCase();
  if (node.id) return tag + '#' + node.id;
  const cls = (node.getAttribute('class') || '').trim().split(/\\s+/)[0];
  return cls ? tag + '.' + cls : tag;
};

const textOf = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();

const onlyOne = (css, el) => {
  try {
    const found = document.querySelectorAll(css);
    return found.length === 1 && (!el || found[0] === el);
  } catch (e) { return false; }
};

// Walked to the root rather than stopped at five ancestors: a path anchored at
// <html> with :nth-of-type at every level matches exactly one element by
// construction, and stopping early could hand out a selector matching several -
// which is the promise this whole function exists to keep.
const cssPath = (el) => {
  const parts = [];
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    let part = node.tagName.toLowerCase();
    if (node.parentElement) {
      const siblings = [...node.parentElement.children]
        .filter(s => s.tagName === node.tagName);
      if (siblings.length > 1) {
        part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
      }
    }
    parts.unshift(part);
    const candidate = parts.join(' > ');
    if (onlyOne(candidate, el)) return candidate;
  }
  return parts.join(' > ');
};

const ownsItsText = (el) => {
  const kids = el.children;
  if (kids.length === 0) return true;
  if (kids.length > 1) return false;
  return ownsItsText(kids[0]);
};

// One selector per element, and the key says which kind. CSS where the page
// gives something stable to hold; XPath by text when it does not, because CSS
// cannot match text at all.
const selectorFor = (el) => {
  const tag = el.tagName.toLowerCase();
  const byId = el.id ? '#' + CSS.escape(el.id) : '';
  if (byId && onlyOne(byId, el)) return {css: byId};
  for (const attr of ['data-testid', 'data-test', 'data-qa', 'name',
                      'aria-label', 'placeholder', 'title', 'href']) {
    const value = el.getAttribute(attr);
    if (!value || value.length > 80 || value.includes('"')) continue;
    const candidate = tag + '[' + attr + '="' + value + '"]';
    if (onlyOne(candidate, el)) return {css: candidate};
  }
  // A container's `normalize-space()` is every descendant's text run together:
  // a nav <li> produced //li[normalize-space()="HelpDeskHelpDeskAdd Ticket..."],
  // which is unreadable, brittle, and not this element's label at all. So the
  // text selector is offered only where the text really belongs to this
  // element - no element children, or a single unbroken chain of wrappers,
  // which is what <button><span>Save</span></button> is (saga §F2.10).
  const text = ownsItsText(el) ? textOf(el) : '';
  if (text && text.length <= 60 && !text.includes('"')) {
    const xpath = '//' + tag + '[normalize-space()="' + text + '"]';
    try {
      const count = document.evaluate('count(' + xpath + ')', document, null,
        XPathResult.NUMBER_TYPE, null).numberValue;
      // Same rule as the CSS candidates: one match, and it has to be this one.
      const first = document.evaluate(xpath, document, null,
        XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
      if (count === 1 && first === el) return {xpath: xpath};
    } catch (e) { /* an unusable expression is simply not the answer */ }
  }
  return {css: cssPath(el)};
};


const shown = (node) => {
  const style = getComputedStyle(node);
  return style.display !== 'none' && style.visibility !== 'hidden' &&
    style.opacity !== '0' && node.hidden !== true;
};

// Whether `el` says, through aria-controls, that it opens `node`.
//   true  - it names this one, or something containing it
//   false - it names something else, so it opens somebody ELSE's menu
//   null  - it says nothing, which disqualifies nothing
// Resolved to elements rather than compared as id strings, because a control
// often points at a wrapper around the hidden part rather than at the hidden
// part itself, and a string match would miss that.
const controlsThis = (el, node) => {
  const named = (el.getAttribute('aria-controls') || '').trim();
  if (!named) return null;
  for (const id of named.split(/\\s+/)) {
    const target = document.getElementById(id);
    if (target && (target === node || target.contains(node) || node.contains(target))) {
      return true;
    }
  }
  return false;
};

// What opens `node`, where the PAGE says so rather than where we guess. A
// control carrying aria-expanded is one somebody clicks, and telling an agent
// to hover a click-toggled menu is advice that does nothing - it happened on
// selenium.dev, on a page this map had already reported `expanded: false` for
// (saga §F2.10).
const statedOpener = (node) => {
  // 1. A control that SAYS it opens this one. The strongest statement a page
  //    can make, and it does not depend on where the control sits in the tree.
  for (const el of document.querySelectorAll('[aria-controls]')) {
    if (!node.contains(el) && shown(el) && controlsThis(el, node) === true) return el;
  }
  // 2. Otherwise the nearest ancestor that declares the state itself, or holds
  //    a control that does: <li class="dropdown"> wrapping the <a aria-expanded>
  //    and the <ul> it opens is the ordinary shape of a nav menu.
  //
  //    Skipping any candidate whose own aria-controls names a DIFFERENT
  //    element. Without that, a <nav> holding two dropdowns hands out whichever
  //    toggle comes first in the document for either menu - advice to click the
  //    control that opens the other one (Copilot, #31).
  for (let up = node.parentElement; up && up.nodeType === 1; up = up.parentElement) {
    const tag = up.tagName.toLowerCase();
    if (tag === 'body' || tag === 'html') break;
    if (up.hasAttribute('aria-expanded') && shown(up) &&
        controlsThis(up, node) !== false) {
      return up;
    }
    for (const el of up.querySelectorAll('[aria-expanded]')) {
      if (!node.contains(el) && el !== node && shown(el) &&
          controlsThis(el, node) !== false) {
        return el;
      }
    }
  }
  return null;
};

const reasonFor = (el) => {
  // `:disabled` rather than `.disabled`: a control inside a disabled
  // <fieldset> reports false for the property and is disabled all the same.
  let isDisabled = el.getAttribute('aria-disabled') === 'true';
  try {
    isDisabled = isDisabled || el.matches(':disabled');
  } catch (e) { /* not a control, so it has no disabled state */ }
  if (isDisabled) return {reason: 'disabled', detail: nameOf(el)};
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    if (!shown(node)) {
      // The hidden node cannot be hovered - nothing with display:none can
      // receive a pointer - so the advice has to name something else. That is
      // only worth saying when it is a target the caller can act on: an
      // ancestor that is visible, is not the page itself, and has a selector
      // checked to match exactly one element. Otherwise say nothing: `body` is
      // a true answer to "what is visible above this" and useless advice.
      let trigger = null;
      let gesture = '';
      const stated = statedOpener(node);
      if (stated) {
        const found = selectorFor(stated);
        if (found && (found.css || found.xpath)) {
          trigger = found.css || found.xpath;
          gesture = stated.hasAttribute('aria-expanded') ? 'click' : 'hover';
        }
      }
      if (!trigger) {
        for (let up = node.parentElement; up && up.nodeType === 1;
             up = up.parentElement) {
          const tag = up.tagName.toLowerCase();
          if (tag === 'body' || tag === 'html') break;
          if (!shown(up)) continue;
          const found = selectorFor(up);
          if (found && (found.css || found.xpath)) {
            trigger = found.css || found.xpath;
            gesture = 'hover';
            break;
          }
        }
      }
      return {reason: 'hidden', detail: nameOf(node), trigger: trigger,
              gesture: gesture};
    }
  }
  const rect = el.getBoundingClientRect();
  if (!rect.width || !rect.height) return {reason: 'zero_size', detail: nameOf(el)};
  const onScreen = rect.bottom > 0 && rect.right > 0 &&
    rect.top < (window.innerHeight || 0) && rect.left < (window.innerWidth || 0);
  if (!onScreen) return {reason: 'offscreen', detail: nameOf(el)};
  const top = document.elementFromPoint(
    rect.left + rect.width / 2, rect.top + rect.height / 2);
  if (top && top !== el && !el.contains(top) && !top.contains(el)) {
    return {reason: 'covered', detail: nameOf(top)};
  }
  return {reason: null, detail: nameOf(el)};
};
"""

# One element, as `explain` reads it.
USABLE_JS = _HELPERS + "\nreturn reasonFor(arguments[0]);"

# The map. Every entry carries a selector that is checked to match exactly one
# element before it is handed out — a selector an agent has to verify itself is
# most of the work it came here to avoid.
OUTLINE_JS = _HELPERS + """
const [root, wanted, limit, interactiveOnly, selector] = [
  arguments[0] || document.body, (arguments[1] || '').toLowerCase(),
  arguments[2], arguments[3], arguments[4]];

const ROLES = {a: 'link', button: 'button', select: 'combobox',
  textarea: 'textbox', summary: 'disclosure', form: 'form', img: 'img',
  nav: 'navigation', label: 'label'};
const INPUT_ROLES = {checkbox: 'checkbox', radio: 'radio', submit: 'button',
  button: 'button', file: 'file', search: 'searchbox', range: 'slider'};

const roleOf = (el) => {
  const explicit = (el.getAttribute('role') || '').trim();
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  if (tag === 'input') {
    return INPUT_ROLES[(el.getAttribute('type') || 'text').toLowerCase()] || 'textbox';
  }
  if (/^h[1-6]$/.test(tag)) return 'heading';
  if (el.isContentEditable) return 'textbox';
  return ROLES[tag] || tag;
};


const accessibleName = (el) => {
  const aria = (el.getAttribute('aria-label') || '').trim();
  if (aria) return aria;
  // An IDREF *list*: `aria-labelledby="label action"` names two elements whose
  // text is joined. Read as one id it resolves to nothing, and the control ends
  // up with an empty name that `text` filtering cannot find.
  const labelledBy = (el.getAttribute('aria-labelledby') || '').trim();
  if (labelledBy) {
    const parts = labelledBy.split(/\\s+/)
      .map(id => document.getElementById(id))
      .filter(Boolean)
      .map(node => textOf(node));
    if (parts.length) return parts.join(' ').trim().slice(0, 80);
  }
  const tag = el.tagName.toLowerCase();
  if (tag === 'img') return (el.getAttribute('alt') || '').trim().slice(0, 80);
  if (tag === 'input' || tag === 'select' || tag === 'textarea') {
    if (el.id) {
      const label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (label) return textOf(label).slice(0, 80);
    }
    const placeholder = (el.getAttribute('placeholder') || '').trim();
    if (placeholder) return placeholder.slice(0, 80);
    const value = (el.value || '').trim();
    if (value && (el.type === 'submit' || el.type === 'button')) {
      return value.slice(0, 80);
    }
    return (el.getAttribute('name') || '').trim().slice(0, 80);
  }
  return textOf(el).slice(0, 80);
};

// Unique *and* this element. Counting alone is not enough: an attribute value
// carrying a quote or a backslash can build a selector that matches exactly one
// element which is not the one it was built from, and handing that out would
// point a later click at the wrong node - the precise failure a checked
// selector exists to prevent.
const seen = [];
for (const el of root.querySelectorAll(interactiveOnly ? selector : '*')) {
  if (seen.length >= limit) break;
  const name = accessibleName(el);
  if (wanted && !name.toLowerCase().includes(wanted)) continue;
  const verdict = reasonFor(el);
  const entry = {role: roleOf(el), name: name, visible: verdict.reason === null,
                 ...selectorFor(el)};
  if (verdict.reason) {
    entry.reason = verdict.reason;
    if (verdict.reason === 'hidden' || verdict.reason === 'covered') {
      entry.blocked_by = verdict.detail;
    }
    // What is in the way was never the question a caller had. `revealed_by`
    // is: the selector of the control that opens this, and `open_with` says
    // whether to click it or hover it (saga §F2.10).
    if (verdict.reason === 'hidden' && verdict.trigger) {
      entry.revealed_by = verdict.trigger;
      entry.open_with = verdict.gesture || 'hover';
    }
  }
  const expanded = el.getAttribute('aria-expanded');
  if (expanded !== null) entry.expanded = expanded === 'true';
  seen.push(entry);
}
return seen;
"""

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
    """Every element worth acting on under ``scope``, with a checked selector."""
    return (
        driver.execute_script(
            OUTLINE_JS, scope, text or "", int(limit), bool(interactive), INTERACTIVE
        )
        or []
    )


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
