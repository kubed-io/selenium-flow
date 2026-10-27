// The element that has focus, as a person left it: from the document's active
// element, follow each open shadow root's own active element inward. A frame
// element is answered as it is (the caller descends into it). Null when focus
// is on the body or nowhere.
let active = document.activeElement;
while (active && active.shadowRoot && active.shadowRoot.activeElement) {
  active = active.shadowRoot.activeElement;
}
if (!active || active === document.body || active === document.documentElement) {
  return null;
}
return active;
