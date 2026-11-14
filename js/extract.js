// An element's HTML as `innerHTML` has it, with no password input's value in
// it. The `value` attribute is where a page keeps one it pre-filled or that a
// script wrote; the clone loses it, the live element is never touched. A
// password input read on its own answers its own tag, since it has no inner.
const source = arguments[0];
const copy = source.cloneNode(true);
const PASSWORD = 'input[type=password i]';
const own = copy.matches && copy.matches(PASSWORD);
// A <template>'s content is a separate fragment that querySelectorAll does not
// enter but innerHTML serialises, so each one is cleaned in turn, nested too.
const strip = (root) => {
  root.querySelectorAll(PASSWORD).forEach(field => field.removeAttribute('value'));
  root.querySelectorAll('template').forEach(t => strip(t.content));
};
strip(copy);
if (copy.tagName === 'TEMPLATE') strip(copy.content);
if (own) copy.removeAttribute('value');
return own ? copy.outerHTML : copy.innerHTML;
