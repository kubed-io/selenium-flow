# Reading a page cheaply

| Want | Use | Rough cost |
|---|---|---|
| Text, values, "did it load" | `extract` | cheap |
| Many values at once, computed state | `execute_script` | cheap, one round trip |
| Layout, styling, a rendered chart | `screenshot` | very expensive |

Never screenshot to read text: an image of a paragraph costs orders of magnitude
more than the paragraph, cannot be quoted, and may show a half-rendered page.

## Addressing an element: `xpath` or `css`

A `selector` is an object with exactly one of `xpath` or `css`. Both or neither
is refused.

```
interact(action="click", selector={"xpath": "//button[@type='submit']"})
interact(action="click", selector={"css": "button[type=submit]"})
```

Which to reach for:

| Situation | Use |
|---|---|
| An id, class, attribute or descendant — most of the time | `css` — shorter, and the syntax you already know from the page's own stylesheet |
| Matching on **visible text** | `xpath` — `//button[contains(., 'Save')]`, which CSS cannot do at all |
| Walking **upwards** to a parent or ancestor | `xpath` — `//input[@id='x']/ancestor::form` |
| Anything positional or structural beyond `:nth-child` | `xpath` |

There is no fallback from one to the other: if the selector matches nothing, the
wait times out and the error says what it waited for and what page the browser
was on (`skill://selenium-flow/references/TROUBLESHOOTING.md`).

## extract

Returns `text` (visible text) and `html` (`innerHTML`) for one element, plus the
page `url` and `title`.

```
extract(selector={"xpath": "//h1"})
extract(url="https://example.com/settings", selector={"xpath": "//main"})
```

`extract` returns the element's whole text and HTML, so `body` on a large app can
be more than you can read in one result. On an unfamiliar page, `outline` first
to see its structure, then `extract` the one element that holds your answer. For
a list of items, one `execute_script` that returns just their names is smaller
than either.

Cheap orientation checks:

```
extract(selector={"xpath": "//title"})   # where am I, really
extract(selector={"xpath": "//h1"})      # did the expected page load
```

## execute_script for batches and anything computed

One `execute_script` beats five `extract` calls when you already know what to
read. `return` the value; anything JSON-serialisable comes through. It suits
many rows at once, computed style, page state (`location`, storage) and
scrolling. To find a button or a panel use `outline(text="...")`, not a script,
and note that a script never waits: read after a tool has waited for the page.

```
execute_script(script="return [...document.querySelectorAll('.row')].map(r => r.innerText)")
execute_script(script="return {url: location.href, rows: document.querySelectorAll('tr').length}")
execute_script(script="return getComputedStyle(document.querySelector('.cta')).backgroundColor")
```

Scrolling is in `skill://selenium-flow/references/INTERACTION.md`.

## outline, when you need a selector

`outline` maps the page: every element worth acting on, with **one selector that
has been checked to match exactly one element**, and whether it can be used.

```
outline(selector={"css": "nav"})
→ {"role": "button", "name": "HelpDesk", "xpath": "//button[normalize-space()=\"HelpDesk\"]",
   "region": "navigation", "visible": true, "expanded": false}
  {"role": "link", "name": "Feature Requests", "css": "a[href=\"/extensions/feature-requests\"]",
   "region": "navigation", "visible": false, "reason": "hidden", "blocked_by": "ul.menu-content",
   "revealed_by": "button[aria-controls=\"menu\"]", "open_with": "click"}
```

Each entry carries `css` or `xpath`, whichever was checked; hand it on as
`selector={"css": ...}`.

In that second entry the link is real and its selector works, but clicking it now
would time out because `ul.menu-content` is hidden. The reasons are `hidden`,
`covered` (with `blocked_by`), `unpainted` (it paints nothing at its centre),
`invisible_cover` (an invisible element on top, named in `blocked_by`, takes the
click), `zero_size`, `offscreen` and `disabled`.

`blocked_by` says what is in the way; `revealed_by` says what to act on, with
the gesture `open_with` names:

- `open_with: "click"` — the trigger carries `aria-expanded`, so the page has
  told you it toggles on a click. Hovering it does nothing.
- `open_with: "hover"` — the page has a `:hover` rule that shows it, or a
  control that names it without `aria-expanded`.

Neither appears when nothing visible above the element has a checked selector,
or when nothing on the page says how it opens.

- The content comes first. Elements in the page's navigation, banner, footer and
  sidebars are listed after everything else, and each entry's `region` says which
  landmark it sits in.
- `total` above `count` means the list was cut. Scope it with a `selector` to one
  region, such as `outline(selector={"css": "main"})`.
- `text` keeps the entries whose name contains it, ignoring case:
  `outline(text="Save")`.
- `limit` defaults to 50. `interactive=false` lists every element, not only the
  ones you can act on.

Do not read HTML to find selectors: a DOM dump does not tell you whether the
element can be used. `outline` is not a flow step; it is how you work out what a
flow should do and how you repair one.

## screenshot, when the visual is the point

Three modes, in precedence order: a `selector` for one element, `full_page` for
the whole scrollable page, otherwise the viewport. `width` and `height` resize the
window first and leave it that size.

```
screenshot(selector={"xpath": "//div[@class='chart']"})     # smallest useful image
screenshot(full_page=true, width=1280)
```

Prefer an element screenshot over a full page: it is smaller and answers the
question you asked. Set `width`/`height` when layout matters.

Over MCP you get an image block. Over HTTP you get base64 plus pixel dimensions
and a `bytes` count; a `bytes` value near zero means a blank capture, usually
because the page had not rendered yet.

A screenshot is kept with the workspace's files by default and the result
carries that file's link. `save=false` skips it, and a server with nowhere to
keep files says `file_error` and still returns the image.

Give a person the link, not the picture: they cannot see a tool result, and many
clients cannot render an image block. Paste the `url`, or `![](url)`; it opens in
any browser and needs no token. A server that has not been told its public
address returns it relative, so hand it over with the address you reached the
server on.

```
screenshot(selector={"xpath": "//div[@class='chart']"})
→ file: {name: "screenshot.png", url: "https://…/screenshots/…?exp=…&sig=…"}
```

The file outlives the browser, and a second `screenshot.png` is kept as
`screenshot (1).png`, so use the name the result gives.

## print, when a person wants the page itself

`print` keeps the page as a file and returns its link, with no image for you to
look at. `format="pdf"` is the browser's own print: selectable text, the whole
document; `landscape=true` turns it, and `background=true` keeps the colours and
images a print leaves out. `format="html"` is the page as it stands now.

```
print(format="pdf", background=true, filename="invoice")
→ file: {name: "invoice.pdf", url: "https://…/kept/…?exp=…&sig=…"}
```

## XPath that keeps working

- Prefer stable attributes: `//input[@name='q']` over `//div[3]/input`.
- Match visible text: `//button[contains(., 'Submit')]`.
- Use `//` freely rather than spelling out the full path from the root.
- Scope to a region when a page repeats a pattern:
  `//table[@id='results']//tr[2]/td[1]`.

If an XPath fails, suspect the page before the expression
(`skill://selenium-flow/references/TROUBLESHOOTING.md`).
