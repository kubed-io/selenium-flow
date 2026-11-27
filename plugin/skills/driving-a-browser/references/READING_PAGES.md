# Reading a page

Read text with `extract` or `execute_script`. A screenshot of the same text
costs orders of magnitude more, cannot be quoted, and may show a half-rendered
page.

## Finding a selector with `outline`

`outline` lists the elements worth acting on, each with one selector checked to
match exactly one element, and whether it can be used now.

```
outline(selector={"css": "nav"})
→ {"role": "button", "name": "HelpDesk", "xpath": "//button[normalize-space()=\"HelpDesk\"]",
   "region": "navigation", "visible": true, "expanded": false}
  {"role": "link", "name": "Feature Requests", "css": "a[href=\"/extensions/feature-requests\"]",
   "region": "navigation", "visible": false, "reason": "hidden", "blocked_by": "ul.menu-content",
   "revealed_by": "button[aria-controls=\"menu\"]", "open_with": "click"}
```

Each entry carries `css` or `xpath`, whichever was checked: pass it on as
`selector={"css": ...}`. The second entry's selector works, but a click now would
time out, because `ul.menu-content` is hidden. Act on `revealed_by` with the
gesture `open_with` names first:

- `open_with: "click"`: the trigger carries `aria-expanded`, so it toggles on a
  click, and hovering it does nothing.
- `open_with: "hover"`: a `:hover` rule shows it, or a control names it without
  `aria-expanded`.

Neither appears when nothing visible above the element has a checked selector,
or nothing on the page says how it opens.

On a big page, scope it, `outline(selector={"css": "main"})`, or filter it,
`outline(text="Save")`. Do not read HTML to find selectors: a DOM dump does not
say whether an element can be used.

## Choosing CSS or XPath

| Situation | Use |
|---|---|
| An id, class, attribute or descendant | `css` |
| Visible text | `xpath`: `//button[contains(., 'Save')]` |
| A parent or ancestor | `xpath`: `//input[@id='x']/ancestor::form` |
| Position or structure beyond `:nth-child` | `xpath` |

There is no fallback from one to the other. An XPath that keeps working matches
a stable attribute (`//input[@name='q']`, not `//div[3]/input`) or visible text,
uses `//` freely, and scopes to a region when the page repeats a pattern:
`//table[@id='results']//tr[2]/td[1]`. When one fails, suspect the page before
the expression (`references/TROUBLESHOOTING.md`).

## `extract`

```
extract(selector={"xpath": "//h1"})                 # did the expected page load
extract(url="https://example.com/settings", selector={"css": "main"})
```

It returns the element's whole text and HTML, so `body` on a large app can be
more than one result holds. On an unfamiliar page, `outline` first, then extract
the one element that holds the answer.

## `execute_script` for many values at once

One script beats five `extract` calls when you know what to read. `return` the
value; anything JSON-serialisable comes back.

```
execute_script(script="return [...document.querySelectorAll('.row')].map(r => r.innerText)")
execute_script(script="return {url: location.href, rows: document.querySelectorAll('tr').length}")
execute_script(script="return getComputedStyle(document.querySelector('.cta')).backgroundColor")
```

A script never waits, so run it after a tool has waited for the page.

## Screenshots

```
screenshot(selector={"xpath": "//div[@class='chart']"})    # smallest useful image
screenshot(full_page=true, width=1280)
```

A `selector` beats `full_page`, which beats the viewport. `width` and `height`
resize the window first and leave it that size. Prefer one element to a full
page.

The screenshot is kept in the workspace's files, and the result's `file` has its
link; `references/FILES.md` says which link to give a person. A
second `screenshot.png` is kept as `screenshot (1).png`, so use the name the
result gives. Over HTTP, a `bytes` value near zero means a blank capture.
