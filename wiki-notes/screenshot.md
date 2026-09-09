## Notes

**Reach for [`extract`](extract) first.** An image of text costs orders of
magnitude more than the text, cannot be quoted, and may be a picture of a
half-rendered page. Screenshot when the *appearance* is the answer — layout,
styling, a chart that only exists once rendered.

**`bytes` is the blank-capture check.** A capture of a page that never painted
still returns a valid PNG; it is just small. Comparing `bytes` against a floor
of a couple of thousand catches that, and is far more reliable than checking a
particular expected size — a full-page capture of a mostly-white page is only
about 10KB, so a tight threshold goes flaky on a rendering difference.

**`width`/`height` leave the window resized.** They are a resize, not a capture
option, and everything afterwards sees the new size. `full_page` is different:
it grows the window to the document height for the capture and puts it back.

**Whether the caller can *see* it depends on the client.** MCP returns a real
image content block, and Claude Code and Claude Desktop render it. Several
clients do not, and n8n's agent cannot see one at all — the base64 rides along in
the tool message costing ~18k tokens for nothing. When a person will look at the
result, pass `save=true` and give them the link.
