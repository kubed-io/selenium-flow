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

// Whether the text inside `el` is its own: at each level, at most one child
// carries any. A child with none, the icon in
// <button><svg>…</svg><span>Save</span></button>, does not count. Two that do
// would be read with nothing between them ("SaveCancel"), so the text selector
// never uses text that is not its own (saga §F2.10), and nor does the name of
// anything but a control, which ARIA names from its content (#138).
const ownsItsText = (el) => {
  const kids = [...el.children].filter((kid) => /\S/.test(kid.textContent || ''));
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
  // element, as `ownsItsText` decides (saga §F2.10).
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

// The point a pointer would press: the centre of the part of the element's
// first client rect that is in the viewport, as center.js works it out.
const pointAt = (el) => {
  const rects = el.getClientRects();
  const r = rects && rects.length ? rects[0] : el.getBoundingClientRect();
  const w = window.innerWidth, h = window.innerHeight;
  const left = Math.max(r.left, 0), right = Math.min(r.right, w);
  const top = Math.max(r.top, 0), bottom = Math.min(r.bottom, h);
  if (right <= left || bottom <= top) return null;
  return [(left + right) / 2, (top + bottom) / 2];
};

// A colour's alpha. An unset value (jsdom answers '') is transparent.
const alpha = (color) => {
  const text = (color || '').trim().toLowerCase();
  if (text === '' || text === 'transparent') return 0;
  const parts = /^rgba?\(([^)]*)\)$/.exec(text);
  if (!parts) return 1;
  const inner = parts[1];
  let raw = null;
  if (inner.includes('/')) raw = inner.split('/')[1];
  else if (inner.split(',').length === 4) raw = inner.split(',')[3];
  if (raw === null) return 1;
  raw = raw.trim();
  const value = raw.endsWith('%') ? parseFloat(raw) / 100 : parseFloat(raw);
  return Number.isNaN(value) ? 1 : value;
};

// A number from a computed style, where unset (jsdom answers '') is `initial`.
const numberOr = (value, initial) => {
  const parsed = parseFloat(value);
  return Number.isNaN(parsed) ? initial : parsed;
};

const REPLACED = ['img', 'video', 'canvas', 'iframe', 'embed', 'object', 'input',
                  'textarea', 'select'];

// Whether the node's ::before or ::after draws: content other than an empty or
// blank string (an icon font's glyph, text, an image), or an empty one with a
// background, on a box that is shown and not fully transparent (a CSS tooltip
// waits at opacity 0). A hit on a pseudo-element is the node itself (ruling 11,
// amended 11-13 by Dr K). Where on the node it draws is not asked, as for text.
const BLANK = /^(["'])\s*\1$/;
const pseudoPainted = (node) => ['::before', '::after'].some((which) => {
  let style;
  try { style = getComputedStyle(node, which); } catch (e) { return false; }
  const content = (style.content || 'none').trim();
  if (content === 'none' || content === 'normal') return false;
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  if (numberOr(style.opacity, 1) === 0) return false;
  if (!BLANK.test(content)) return true;
  return alpha(style.backgroundColor) > 0 || (style.backgroundImage || 'none') !== 'none';
});

// Whether a pointer at this node's centre would hit something that is drawn
// (spec ruling 11). Borders and shadows are not counted.
const painted = (node) => {
  for (let up = node; up && up.nodeType === 1; up = up.parentElement) {
    if (numberOr(getComputedStyle(up).opacity, 1) === 0) return false;
  }
  const style = getComputedStyle(node);
  if (style.visibility === 'hidden') return false;
  const tag = node.tagName.toLowerCase();
  if (node.namespaceURI === 'http://www.w3.org/2000/svg' && tag !== 'svg') {
    // A paint is nothing when it is `none` or a fully transparent colour (which
    // is also how jsdom spells `none`); unset is the initial value.
    const nothing = (paint) => paint === 'none' || (paint !== '' && alpha(paint) === 0);
    const fill = style.fill || 'black';
    if (!nothing(fill) && numberOr(style.fillOpacity, 1) > 0) return true;
    const stroke = style.stroke || 'none';
    return !nothing(stroke) && numberOr(style.strokeOpacity, 1) > 0 &&
      numberOr(style.strokeWidth, 1) > 0;
  }
  if (REPLACED.includes(tag)) return true;
  if (alpha(style.backgroundColor) > 0) return true;
  const image = style.backgroundImage || 'none';
  if (image !== 'none') return true;
  for (const child of node.childNodes) {
    if (child.nodeType === 3 && child.nodeValue.trim() !== '') return true;
  }
  return pseudoPainted(node);
};

// Whether `top`, which is not inside `el`, is an overlay: it or an ancestor not
// containing `el` is fixed, sticky or stacked above 0.
const overlayAbove = (top, el) => {
  for (let node = top; node && node.nodeType === 1 && !node.contains(el);
       node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.position === 'fixed' || style.position === 'sticky') return true;
    const z = parseInt(style.zIndex, 10);
    if (Number.isInteger(z) && z > 0) return true;
  }
  return false;
};

// What receives a pointer at the element's click point, from the whole stack
// there (spec ruling 10). `interactive` is a selector list, or null to skip it.
const receiverAt = (el, interactive) => {
  const at = pointAt(el);
  if (at === null) return null;
  const stack = document.elementsFromPoint ? document.elementsFromPoint(at[0], at[1]) : [];
  const top = stack[0];
  if (!top) return null;
  const inside = top === el || el.contains(top);
  const under = stack.some((n) => n === el || el.contains(n));
  let depth = null;
  let control = false;
  if (inside) {
    depth = 0;
    for (let node = top; node && node !== el; node = node.parentElement) depth += 1;
    // A control from the hit up to the target, the target included: a link or
    // a button sees a click anywhere inside it (Dr K's QA, 11-13).
    if (interactive) {
      for (let node = top; node; node = node === el ? null : node.parentElement) {
        try { if (node.matches(interactive)) { control = true; break; } } catch (e) { break; }
      }
    }
  }
  const overlay = !inside && overlayAbove(top, el);
  const drawn = painted(top);
  let reason = null;
  if (!inside) reason = !under ? 'unpainted' : (!drawn ? 'invisible_cover' : 'covered');
  return {element: nameOf(top), inside: inside, under: under, depth: depth,
          painted: drawn, interactive: control, overlay: overlay,
          reason: reason, selector: inside ? null : selectorFor(top)};
};

// The page's own :hover rules that set what shows, read once per script run.
// A sheet that cannot be read (cross-origin, no CORS) is skipped, and said.
let hoverCache = null;
let hoverUnread = false;
const hoverRules = () => {
  if (hoverCache) return hoverCache;
  hoverCache = [];
  const walk = (rules) => {
    for (let i = 0; i < rules.length; i++) {
      const rule = rules[i];
      const text = rule.selectorText;
      if (text && text.includes(':hover') && rule.style &&
          (rule.style.display || rule.style.visibility || rule.style.opacity)) {
        for (const part of text.split(',')) {
          const at = part.indexOf(':hover');
          if (at < 0) continue;
          hoverCache.push({head: part.slice(0, at).trim(),
                           base: part.split(':hover').join('').trim()});
        }
      }
      if (rule.cssRules) walk(rule.cssRules);
    }
  };
  const sheets = document.styleSheets;
  for (let i = 0; i < sheets.length; i++) {
    let rules = null;
    try { rules = sheets[i].cssRules; } catch (e) { hoverUnread = true; continue; }
    if (rules) walk(rules);
  }
  return hoverCache;
};

// Whether a rule in the page says hovering `up` shows `node`.
const hoverReveals = (up, node) => {
  for (const rule of hoverRules()) {
    if (!rule.head || !up || !node) continue;
    try { if (up.matches(rule.head) && node.matches(rule.base)) return true; }
    catch (e) { /* a selector this engine cannot match */ }
  }
  return false;
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
          if (!shown(up) || !hoverReveals(up, node)) continue;
          const found = selectorFor(up);
          if (found && (found.css || found.xpath)) {
            trigger = found.css || found.xpath;
            gesture = 'hover';
            break;
          }
        }
      }
      const answer = {reason: 'hidden', detail: nameOf(node), trigger: trigger,
                      gesture: gesture};
      if (!trigger) {
        hoverRules();
        if (hoverUnread) answer.unread = true;
      }
      return answer;
    }
  }
  const rect = el.getBoundingClientRect();
  if (!rect.width || !rect.height) return {reason: 'zero_size', detail: nameOf(el)};
  const onScreen = rect.bottom > 0 && rect.right > 0 &&
    rect.top < (window.innerHeight || 0) && rect.left < (window.innerWidth || 0);
  if (!onScreen) return {reason: 'offscreen', detail: nameOf(el)};
  const hit = receiverAt(el, null);
  if (hit && hit.reason === 'unpainted') return {reason: 'unpainted', detail: hit.element};
  if (hit && hit.reason === 'invisible_cover') {
    return {reason: 'invisible_cover', detail: hit.element, selector: hit.selector};
  }
  if (hit && hit.reason === 'covered') {
    return {reason: 'covered', detail: hit.element, overlay: hit.overlay};
  }
  return {reason: null, detail: nameOf(el)};
};
