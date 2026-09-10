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

## XPath that keeps working

- Prefer stable attributes: `//input[@name='q']` over `//div[3]/input`.
- Match visible text: `//button[contains(., 'Submit')]`.
- Use `//` freely rather than spelling out the full path from the root.
- Scope to a region when a page repeats a pattern:
  `//table[@id='results']//tr[2]/td[1]`.

If an XPath fails, suspect the page before the expression — see
`references/TROUBLESHOOTING.md`.
