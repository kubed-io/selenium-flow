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

## extract

Returns `text` (visible text) and `html` (`innerHTML`) for one XPath, plus the
page `url` and `title`.

```
extract(xpath="//h1")
extract(url="https://example.com/settings", xpath="//main")
```

Start wide, then narrow. `//body` on an unfamiliar page is still far cheaper
than a screenshot and tells you the structure; once you know it, use a specific
XPath so the result stays small.

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
