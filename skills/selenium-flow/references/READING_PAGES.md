# Reading a page cheaply

The cost difference between the three ways to read is enormous, and picking the
wrong one is the most expensive mistake available with these tools.

| Want | Use | Rough cost |
|---|---|---|
| Text, values, "did it load" | `extract` | cheap |
| Many values at once, computed state | `execute_script` | cheap, one round trip |
| Layout, styling, a rendered chart | `screenshot` | very expensive |

**Never screenshot to read text.** An image of a paragraph costs orders of
magnitude more than the paragraph, cannot be quoted or searched, and may be
wrong if the page had not finished rendering.

## Addressing an element: `xpath` or `css`

Every tool that acts on an element takes **either** `xpath` **or** `css` —
never both, never neither. Passing both is refused rather than resolved, because
acting on whichever element one of them happened to find would hide a typo in
the other.

```
interact(action="click", xpath="//button[@type='submit']")
interact(action="click", css="button[type=submit]")
```

Which to reach for:

| Situation | Use |
|---|---|
| An id, class, attribute or descendant — most of the time | `css` — shorter, and the syntax you already know from the page's own stylesheet |
| Matching on **visible text** | `xpath` — `//button[contains(., 'Save')]`, which CSS cannot do at all |
| Walking **upwards** to a parent or ancestor | `xpath` — `//input[@id='x']/ancestor::form` |
| Anything positional or structural beyond `:nth-child` | `xpath` |

`css` is usually the shorter of the two and `#id` covers the single most common
case, so it is a good default. But **text matching and ancestor traversal are
XPath-only**, and both come up constantly on real pages — a button you can see
but whose markup you cannot guess is the normal case, and `//button[contains(.,
'Continue')]` finds it in one line.

There is no fallback: if the selector matches nothing, the wait times out and
the error tells you what it waited for and what page the browser was actually
on. See `TROUBLESHOOTING.md`.

## extract

Returns `text` (visible text) and `html` (`innerHTML`) for one element, plus the
page `url` and `title`.

```
extract(xpath="//h1")
extract(url="https://example.com/settings", xpath="//main")
```

Start wide, then narrow. `//body` on an unfamiliar page is still far cheaper
than a screenshot and tells you the structure; once you know it, use a specific
selector so the result stays small.

Cheap orientation checks worth knowing:

```
extract(xpath="//title")   # where am I, really
extract(xpath="//h1")      # did the expected page load
```

## execute_script for batches and anything computed

One `execute_script` beats five `extract` calls. Use `return` to send a value
back; anything JSON-serialisable comes through.

```
execute_script(script="return [...document.querySelectorAll('.row')].map(r => r.innerText)")
execute_script(script="return {url: location.href, rows: document.querySelectorAll('tr').length}")
execute_script(script="return getComputedStyle(document.querySelector('.cta')).backgroundColor")
```

It is also the only reliable way to scroll — see `references/INTERACTION.md`.

## outline, when you need a selector

`outline` maps the page: every element worth acting on, with **one selector that
has been checked to match exactly one element**, and whether it can be used.

```
outline(css="nav")
→ {"role": "button", "name": "HelpDesk", "xpath": "//button[normalize-space()=\"HelpDesk\"]",
   "visible": true, "expanded": false}
  {"role": "link", "name": "Feature Requests", "css": "a[href=\"/extensions/feature-requests\"]",
   "visible": false, "reason": "hidden", "blocked_by": "ul.menu-content",
   "revealed_by": "button[aria-controls=\"menu\"]", "open_with": "click"}
```

That second entry is the whole point: the link is real, its selector works, and
clicking it now would time out — because `ul.menu-content` is hidden. The reasons
are `hidden`, `covered` (with `blocked_by`), `zero_size`, `offscreen` and
`disabled`.

**`blocked_by` says what is in the way; `revealed_by` says what to act on.** Act
on `revealed_by`, and use the gesture `open_with` names:

- `open_with: "click"` — the trigger carries `aria-expanded`, so the page has
  told you it toggles on a click. Hovering it does nothing.
- `open_with: "hover"` — nothing said otherwise, and a `:hover` menu is the
  usual reason an ancestor is `display: none`.

Neither appears when nothing visible above the element has a checked selector,
because `body` is a true answer and useless advice.

- **Scope it** with `css` or `xpath` to one region, so you get a panel rather
  than a page.
- **Filter** with `text` to find one thing by its label: `outline(text="Save")`.
- `limit` defaults to 50. `interactive=false` lists every element, not only the
  ones you can act on.

**Do not read HTML to find selectors.** `extract` is for *content*; a DOM dump
through `execute_script` is the long way round and does not tell you whether the
element can be used.

`outline` is **not** a flow step: it is how you work out what a flow should do,
and how you repair one when a page has changed underneath it.

## screenshot, when the visual is the point

Three modes, in precedence order: `xpath` for one element, `full_page` for the
whole scrollable page, otherwise the viewport.

```
screenshot(xpath="//div[@class='chart']")     # smallest useful image
screenshot(full_page=true, width=1280)
```

Prefer an element screenshot over a full page — it is smaller and it answers the
question you actually asked. Set `width`/`height` when layout matters; headless
defaults are small and vary between Grid nodes.

Over MCP you get an image block you can see. Over HTTP you get base64 plus real
pixel dimensions and a `bytes` count — a `bytes` value near zero means a blank
capture, which almost always means the page had not rendered yet.

**A screenshot is saved** with the session's files by default, and the result
carries that file's link. `save=false` opts out, and if the page blocked the
download there is no file at all — the result says `file_error` instead and you
still get the image.

**Give a person the link, not the picture.** They cannot see a tool result, many
clients cannot render an image block at all, and describing it is worse than
both. Paste the URL, or `![](absolute_url)` — it opens in any browser and needs
no token. A server that has not been told its public address returns only the
relative `url`; hand that over with the address you reached the server on.

```
screenshot(xpath="//div[@class='chart']")
→ file: {name: "screenshot.png", absolute_url: "https://…/files/…?exp=…&sig=…"}
```

Those files go when the browser goes. `keep_file(name)` makes one outlive it,
and `save=false` skips saving for a capture nobody will ever reopen.

## XPath that keeps working

- Prefer stable attributes: `//input[@name='q']` over `//div[3]/input`.
- Match visible text: `//button[contains(., 'Submit')]`.
- Use `//` freely rather than spelling out the full path from the root.
- Scope to a region when a page repeats a pattern:
  `//table[@id='results']//tr[2]/td[1]`.

If an XPath fails, suspect the page before the expression — see
`references/TROUBLESHOOTING.md`.
