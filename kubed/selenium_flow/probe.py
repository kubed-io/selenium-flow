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
INTERACTIVE = (
    "a[href], button, input, select, textarea, summary, label, "
    '[role], [onclick], [contenteditable=""], [contenteditable="true"], '
    '[tabindex]:not([tabindex="-1"])'
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

const reasonFor = (el) => {
  if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') {
    return {reason: 'disabled', detail: nameOf(el)};
  }
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' ||
        style.opacity === '0' || node.hidden === true) {
      return {reason: 'hidden', detail: nameOf(node)};
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

const textOf = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();

const accessibleName = (el) => {
  const aria = (el.getAttribute('aria-label') || '').trim();
  if (aria) return aria;
  const labelledBy = el.getAttribute('aria-labelledby');
  if (labelledBy) {
    const target = document.getElementById(labelledBy);
    if (target) return textOf(target).slice(0, 80);
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

const onlyOne = (css) => {
  try {
    return document.querySelectorAll(css).length === 1;
  } catch (e) { return false; }
};

const cssPath = (el) => {
  const parts = [];
  for (let node = el; node && node.nodeType === 1 && parts.length < 5;
       node = node.parentElement) {
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
    if (onlyOne(candidate)) return candidate;
  }
  return parts.join(' > ');
};

// One selector per element, and the key says which kind. CSS where the page
// gives something stable to hold; XPath by text when it does not, because CSS
// cannot match text at all.
const selectorFor = (el) => {
  const tag = el.tagName.toLowerCase();
  if (el.id && onlyOne('#' + CSS.escape(el.id))) return {css: '#' + CSS.escape(el.id)};
  for (const attr of ['data-testid', 'data-test', 'data-qa', 'name',
                      'aria-label', 'placeholder', 'title', 'href']) {
    const value = el.getAttribute(attr);
    if (!value || value.length > 80 || value.includes('"')) continue;
    const candidate = tag + '[' + attr + '="' + value + '"]';
    if (onlyOne(candidate)) return {css: candidate};
  }
  const text = textOf(el);
  if (text && text.length <= 60 && !text.includes('"')) {
    const xpath = '//' + tag + '[normalize-space()="' + text + '"]';
    try {
      const count = document.evaluate('count(' + xpath + ')', document, null,
        XPathResult.NUMBER_TYPE, null).numberValue;
      if (count === 1) return {xpath: xpath};
    } catch (e) { /* an unusable expression is simply not the answer */ }
  }
  return {css: cssPath(el)};
};

const seen = [];
for (const el of root.querySelectorAll(interactiveOnly ? selector : '*')) {
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
  }
  const expanded = el.getAttribute('aria-expanded');
  if (expanded !== null) entry.expanded = expanded === 'true';
  seen.push(entry);
  if (seen.length >= limit) break;
}
return seen;
"""

# What to do about it, in the same voice as the error it is appended to: the
# reason, then the move. The move is the part a timeout never had.
SENTENCES = {
    "hidden": (
        "It exists, but {detail} is hidden — a menu that opens on mouse-over "
        'looks exactly like this. Try interact(action="hover") on it first; a '
        "script cannot open one, because synthetic events do not set :hover."
    ),
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
        return sentence.format(detail=answer.get("detail") or "something")
    # Broad on purpose: a diagnosis must never outrank the failure it decorates.
    except Exception:
        log.debug("could not probe %r", target, exc_info=True)
        return ""
