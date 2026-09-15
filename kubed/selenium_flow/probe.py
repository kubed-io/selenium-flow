"""Why an element cannot be used, asked of the page itself.

A wait that times out says the element never became clickable, which is true and
almost never the diagnosis. The page usually knows: the link is inside a menu
whose ancestor is `display: none`, a cookie banner is on top of it, it is below
the fold, it is disabled, or it is there with no size at all. Each of those has
a different next move, and an agent told only "timed out" guesses.

One piece of JavaScript answers it, and two callers read that answer: the
failure message here, and `outline` when an agent asks before acting rather than
after failing. One script, because a second copy of this reasoning is how the
error and the map start disagreeing about the same element (saga §F2.8).

Script, not CDP: the answer has to be the same on Chrome and on Firefox, which
is a promise every other action in this package already keeps.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Returns {reason, detail} — reason is null when the element is usable. `detail`
# names the thing responsible, because "something is on top of it" sends an
# agent looking and "the cookie banner is on top of it" does not.
USABLE_JS = """
const el = arguments[0];
const name = (node) => {
  if (!node || !node.tagName) return 'something';
  const tag = node.tagName.toLowerCase();
  if (node.id) return tag + '#' + node.id;
  const cls = (node.getAttribute('class') || '').trim().split(/\\s+/)[0];
  return cls ? tag + '.' + cls : tag;
};

if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') {
  return {reason: 'disabled', detail: name(el)};
}

for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
  const style = getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' ||
      style.opacity === '0' || node.hidden === true) {
    return {reason: 'hidden', detail: name(node)};
  }
}

const rect = el.getBoundingClientRect();
if (!rect.width || !rect.height) return {reason: 'zero_size', detail: name(el)};

const onScreen = rect.bottom > 0 && rect.right > 0 &&
  rect.top < (window.innerHeight || 0) && rect.left < (window.innerWidth || 0);
if (!onScreen) return {reason: 'offscreen', detail: name(el)};

const top = document.elementFromPoint(
  rect.left + rect.width / 2, rect.top + rect.height / 2);
if (top && top !== el && !el.contains(top) && !top.contains(el)) {
  return {reason: 'covered', detail: name(top)};
}

return {reason: null, detail: name(el)};
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
