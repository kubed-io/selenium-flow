## Notes

**This is the default way to read a page.** A [`screenshot`](screenshot) of text
costs orders of magnitude more, cannot be quoted back, and may be a picture of a
page that had not finished rendering. Reach for a capture only when the
*appearance* is the answer.

**`//body` works and is usually the wrong idea.** It returns the whole document,
navigation and cookie banners included, and on a large page that is most of what
you pay for. A narrower XPath is not a nicety — it is the difference between
reading a page and reading a site.

**`html` and `text` differ in ways that matter.** `text` is what a person sees:
collapsed whitespace, nothing from hidden elements. `html` is `innerHTML`, so it
keeps markup, attributes and content that is present but invisible. When a value
lives in an attribute rather than in the text, neither helps — use
[`execute_script`](execute_script) and return it directly.

**An empty result is usually a timing or a frame problem, not an XPath one.**
The element is waited for, so an empty string means it was found and genuinely
holds nothing — but a *timeout* means it never appeared. Two common reasons: the
content renders after an XHR that has not landed, or it lives inside an iframe,
where no locator can see it until [`frame`](frame) has switched in.

**Reading many things is one script, not many extracts.** Each call is a round
trip to the Grid. `execute_script` returning an array of the values you want
costs one.
