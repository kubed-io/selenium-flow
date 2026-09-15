const [el, bringIntoView] = [arguments[0], arguments[1]];
const inView = (r) => {
  const w = window.innerWidth, h = window.innerHeight;
  const left = Math.max(r.left, 0), right = Math.min(r.right, w);
  const top = Math.max(r.top, 0), bottom = Math.min(r.bottom, h);
  if (right <= left || bottom <= top) return null;
  return [(left + right) / 2, (top + bottom) / 2];
};
let at = inView(el.getBoundingClientRect());
let scrolled = false;
if (bringIntoView && at === null) {
  el.scrollIntoView({block: 'center', inline: 'center'});
  at = inView(el.getBoundingClientRect());
  scrolled = true;
}
if (at === null) {
  const r = el.getBoundingClientRect();
  return {at: [r.left + r.width / 2, r.top + r.height / 2],
          scrolled: scrolled, outside: true};
}
return {at: at, scrolled: scrolled, outside: false};
