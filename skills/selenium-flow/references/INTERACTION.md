# Acting on a page

## Mouse gestures are one tool

`interact` takes an `action`, because all five gestures need identical arguments
and differ only in what is sent:

| `action` | Use for |
|---|---|
| `click` | the common case |
| `double_click` | file-manager style UIs, text selection |
| `right_click` | context menus |
| `hover` | menus that only appear on mouse-over — impossible any other way |
| `scroll_to` | bringing an off-screen element into view before acting on it |

```
interact(action="click", xpath="//button[@type='submit']")
interact(action="hover", xpath="//nav//li[contains(., 'Account')]")
interact(action="scroll_to", xpath="//tr[last()]")
```

`click` and the two double/right variants wait for the element to be
*clickable*; `hover` and `scroll_to` only wait for it to *exist*, because
requiring clickability would refuse exactly the off-screen element `scroll_to`
is for.

Returns the `action` performed plus the URL and title *after* it, so a
navigation it caused is visible in the result.

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

## Uploading a file

```
upload_file(xpath="//input[@type='file']", content="<base64>", filename="report.csv")
```

The browser runs on another machine, so the bytes are shipped to it for you —
pass the file itself as base64 in `content` with the `filename` you want the
page to see. Over the HTTP endpoint you can instead post a normal
`multipart/form-data` file part, which is usually easier from a script or an n8n
node.

`path` is the alternative when the file already sits on the *server's* own
filesystem. Pass `content` or `path`, never both.

Returns the `filename` and `bytes` actually attached, so you can confirm the
page received what you meant.

## Native dialogs block everything

An `alert`, `confirm` or `prompt` freezes the page: until it is answered, other
actions cannot read the URL or the title. So when an action opens one, it still
succeeds and tells you:

```
interact(action="click", xpath="//button[@id='delete']")
  -> {"action": "click", "url": null, "title": null,
      "dialog": "Delete everything?", "hint": "a dialog is open ..."}
```

Answer it with `dialog`:

```
dialog(action="read")                       # see the message, leave it open
dialog(action="accept")                     # confirm  -> the page gets true
dialog(action="dismiss")                    # cancel   -> the page gets false
dialog(action="send_text", text="Dr K")     # fill a prompt, then accept
```

This server never answers a dialog for you. Chrome's default is to silently
dismiss one — which quietly clicks *Cancel* on a confirmation and destroys the
evidence — so that is turned off deliberately.

## Resizing

```
resize(width=1400, height=900)
```

Window size is one of the few things changeable after the browser is open, so it
is its own tool rather than only an `open_session` argument. Reach for it when
layout matters and the browser was opened for you. The headless default is
narrow and varies between Grid nodes, so set it before judging anything visual.

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
