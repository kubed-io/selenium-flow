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

**If you wrote the content yourself, just send it as text.** Do not encode it.

```
upload_file(xpath="//input[@type='file']",
            text='{"rows": 3}', filename="data.json")
```

That is the normal case: JSON, CSV, YAML, markdown, a log excerpt — anything you
produced. The server writes the real file and ships it to the browser, which
runs on another machine.

The other two sources, one of which is required:

| Pass | For |
|---|---|
| `text` | content you have as text |
| `content` | base64, for binary — the only shape a tool argument can carry |
| `path` | a file already on the *server's* filesystem |

**Name it with an extension.** The page reads a file's type from the filename,
not from anything sent with it: `data.json` arrives as `application/json`, while
a file called `data` arrives with an empty type and may be rejected by an upload
form that checks. If you cannot give an extension, pass `mime_type` and one is
chosen for you.

Returns the `filename` and `bytes` actually attached, so you can confirm the
page received what you meant.

Over the HTTP endpoint the same action takes a normal `multipart/form-data` file
part, which is easier from a script or an n8n node and needs no encoding at all.

## Iframes: switch in, and remember to switch back

Selenium does not look inside frames. An element in one is invisible to every
locator until the session is switched into it — which is the real cause of most
"this XPath is definitely right" timeouts.

```
frame(action="switch", xpath="//iframe[@id='checkout']")
write(xpath="//input[@name='card']", text="4242...")     # inside the frame
frame(action="default")                                   # back to the page
```

| `action` | Goes |
|---|---|
| `switch` | into the frame named by `xpath` (or `index`) |
| `parent` | up one level, for nested frames |
| `default` | all the way back to the main page |

**The switch sticks.** It is session state on the Grid, not something held for
one call, so every later action stays inside that frame until something switches
back. A locator on the main page will then fail for a reason that looks nothing
like the cause — so if something obvious is failing, check `in_frame` on
`session://current` before rewriting the selector.

Switch back as soon as you are done in there.

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
