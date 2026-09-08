# Acting on a page

## Reach the page in one call

Every action except `open_session` and `close_session` takes an optional `url`,
and it is **not an assertion**. If the browser is elsewhere it navigates there
first, then acts. If it is already there, nothing happens.

```
click(url="https://example.com/settings", xpath="//button[@id='save']")
```

That is one call instead of navigate-then-click. Use it whenever you know the
address; clicking a path through a site is for destinations you cannot address.

URLs compare with the fragment and any trailing slash ignored, so `/x`, `/x/`
and `/x#top` are one page. Query strings count as different.

## Typing

`write` clears the field first by default and returns `value` read back off the
element, so you can confirm the text landed instead of assuming.

```
write(xpath="//input[@name='q']", text="selenium grid", submit=true)
```

`submit` presses Enter afterwards — a search box in one call. The value is read
*before* the submit, because submitting navigates and the element reference goes
stale.

A multi-field form is one `write` per field, then a `click` on the button:

```
write(xpath="//input[@name='email']", text="a@example.com")
write(xpath="//input[@name='password']", text="...")
click(xpath="//button[@type='submit']")
```

`click` returns `url` and `title` read *after* the click, so a changed URL is
your confirmation the submit worked. Check it rather than assuming.

To empty a field: `write(xpath=..., text="", clear=true)`.

## Keys

`press_key` sends a named key to an element or to wherever focus is. Names are
lowercase: `tab`, `enter`, `escape`, `backspace`, `delete`, `space`, `home`,
`end`, `page_up`, `page_down`, `arrow_up`, `arrow_down`, `arrow_left`,
`arrow_right`, `f1`–`f12`, and the rest of Selenium's set.

```
press_key(key="escape")                        # dismiss a modal
press_key(xpath="//input[@name='q']", key="tab")
```

## Scrolling is execute_script

`press_key(key="page_down")` only moves the page when focus happens to be on the
scrollable container, which it usually is not. Do not rely on it.

```
execute_script(script="window.scrollTo(0, document.body.scrollHeight)")   # bottom
execute_script(script="window.scrollBy(0, 800)")                          # one screen
execute_script(script="document.querySelector('#row-42').scrollIntoView()")
```

`scrollIntoView` is usually what you want before interacting with something far
down a long page.

## Waiting

You rarely need an explicit wait. `click` and `write` wait for the element to be
*clickable*; `extract` and `screenshot` wait for it to *exist*. All default to
30 seconds and take `wait_timeout`.

So: act on the element and let the tool wait. If a page is genuinely slow, raise
`wait_timeout` on that one call rather than polling in a loop.

For something that has no element to wait on — an animation settling, a
background fetch — wait for its *effect*:

```
extract(xpath="//div[@class='results'][.//li]", wait_timeout=60)
```

## Anything else

`execute_script` covers what the other tools do not: drag and drop, dispatching
events, setting values on inputs a normal `write` cannot reach, reading computed
styles, clearing storage.

```
execute_script(script="localStorage.clear(); return true")
```
