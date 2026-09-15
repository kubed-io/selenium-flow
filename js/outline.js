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
    const parts = labelledBy.split(/\s+/)
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
