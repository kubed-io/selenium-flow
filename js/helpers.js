const nameOf = (node) => {
  if (!node || !node.tagName) return 'something';
  const tag = node.tagName.toLowerCase();
  if (node.id) return tag + '#' + node.id;
  const cls = (node.getAttribute('class') || '').trim().split(/\s+/)[0];
  return cls ? tag + '.' + cls : tag;
};

const textOf = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();

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

// `shown` asks about one element's own styles, which is right where the caller
// is walking the tree itself and wrong for a trigger: a control inside a
// display:none container looks perfectly visible on its own, and offering it as
// `revealed_by` advises clicking something that cannot be reached (Copilot,
// #31). A trigger has to be reachable, so its ancestors are asked too.
const reachable = (el) => {
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    if (!shown(node)) return false;
  }
  return true;
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
  for (const id of named.split(/\s+/)) {
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
    if (!node.contains(el) && reachable(el) && controlsThis(el, node) === true) {
      return el;
    }
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
    if (up.hasAttribute('aria-expanded') && reachable(up) &&
        controlsThis(up, node) !== false) {
      return up;
    }
    for (const el of up.querySelectorAll('[aria-expanded]')) {
      if (!node.contains(el) && el !== node && reachable(el) &&
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
