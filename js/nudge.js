const [el, atX, atY, gap] = [arguments[0], arguments[1], arguments[2], arguments[3]];
const box = el.getBoundingClientRect();
const within = (x, y, r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
if (atX === null || atY === null || !within(atX, atY, box)) {
  return {inside: false, away: null};
}
const vw = window.innerWidth, vh = window.innerHeight;
const parent = el.parentElement ? el.parentElement.getBoundingClientRect() : null;
const candidates = [
  [box.left - gap, box.top + box.height / 2],
  [box.right + gap, box.top + box.height / 2],
  [box.left + box.width / 2, box.top - gap],
  [box.left + box.width / 2, box.bottom + gap],
];
const usable = (c) => c[0] >= 0 && c[1] >= 0 && c[0] < vw && c[1] < vh &&
  !within(c[0], c[1], box);
// A point still inside the element's parent is preferred: stepping right out
// of an open flyout to nudge would close the very thing we are about to hover.
for (const c of candidates) {
  if (usable(c) && parent && within(c[0], c[1], parent)) {
    return {inside: true, away: [Math.round(c[0]), Math.round(c[1])]};
  }
}
for (const c of candidates) {
  if (usable(c)) return {inside: true, away: [Math.round(c[0]), Math.round(c[1])]};
}
return {inside: true, away: null};
