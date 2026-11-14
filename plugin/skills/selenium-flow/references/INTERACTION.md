# Acting on a page

## Mouse gestures are one tool

`interact` takes an `action`:

| `action` | Use for |
|---|---|
| `click` | the common case |
| `double_click` | file-manager style UIs, text selection |
| `right_click` | context menus |
| `hover` | menus that only appear on mouse-over |
| `scroll_to` | bringing an off-screen element into view before acting on it |

```
interact(action="click", selector={"xpath": "//button[@type='submit']"})
interact(action="hover", selector={"xpath": "//nav//li[contains(., 'Account')]"})
interact(action="scroll_to", selector={"xpath": "//tr[last()]"})
```

Every gesture except `scroll_to` moves the pointer onto the element before it
acts and leaves it there. A menu that opens on `:hover` therefore stays open for
the next call: hover the menu, then click the item inside it. A script's
synthetic events do not set `:hover`, so an `execute_script` that "opens" such a
menu does not reliably do it. A hover onto something the pointer is already
inside would send no `mouseover`, so the server steps the pointer aside first
and says `nudged: true` when it did.

## Gliding, and when to ask for it

The move is an instant jump. `glide=true` sends many small moves instead:

```
interact(action="hover", selector={"css": ".slider-handle"}, glide=true)
```

Ask for it when the interface watches movement rather than arrival: sliders,
sortable lists, drag thresholds, menus that track which way you came from.
Otherwise leave it off. The server plots the path from where it last put the
pointer to where the element is now; you never give coordinates. The first move
in a browser has no known start, so it is a jump and the result says so in
`glide_note`.

## Dragging

`drag` needs a source and a destination:

```
drag(selector={"css": ".card:nth-child(2)"}, to={"css": ".column.done"})   # onto another element
drag(selector={"css": "input[type=range]"}, by_x=120)                 # by an offset in pixels
```

Say where it goes with either a `to` selector or a `by_x`/`by_y` offset, never
both and never neither. It presses, holds briefly, travels and releases, and
`glide` defaults to true here, the opposite of `interact`.

This drives pointer events. On Chrome that is enough for native HTML5
drag-and-drop as well: `dragstart` through `drop` fire. On Firefox the native
drop does not complete, so a `draggable=true` element there may need whatever
fallback the page offers.

`click`, `double_click` and `right_click` wait for the element to be clickable.
`hover` and `scroll_to` wait only for it to exist, because requiring
clickability would refuse the off-screen element `scroll_to` is for.

When a selector matches several elements, the clickable waits use the first
match that is displayed and enabled; `hover`, `scroll_to` and `drag`'s `to` use
the first displayed match, or the first match if none is displayed. `matched`
and `index` (from 0) appear in the result when the selector matched several; for
`drag` they describe its source.

The result has the `action` performed plus the URL and title after it, so a
navigation it caused is visible. A click, double click or right click also
returns `received_by`, and a `warning` when the click may have gone to something
other than what you selected
(`skill://selenium-flow/references/TROUBLESHOOTING.md`).

## Reach the page in one call

`interact`, `drag`, `write`, `press_key`, `extract`, `outline`, `screenshot`,
`print`, `execute_script`, `assert` and `save_site_data` take an optional `url`,
and it is not an assertion. If the browser is elsewhere it navigates
there first, then acts. If it is already there, nothing happens. `navigate`'s
`url` is required and always loads; `frame`, `resize` and `dialog` take none.

```
interact(url="https://example.com/settings", action="click", selector={"css": "#save"})
```

Use it whenever you know the address; click through a site only to reach
destinations you cannot address.

`url=` compares host, port, path, hash route, query and credentials: `/x`,
`/x/` and `/x#top` are one page, `/#/` and `/` are one page, `/#/a` and `/#/b`
are two, and so are `/x?p=1` and `/x`. A route's own query counts too
(`#/w?file-id=1`), and `url=https://app/` from `#/dashboard` opens the root
page.

Moving between hash routes does not reload the page, so wait on something
only the new screen has (a selector the old screen lacks), not on the load.

Only `http`, `https` and `about:blank` can be opened, here and in `navigate`
and `open_session`. `file:`, `chrome:`, `view-source:`, `javascript:` and
`data:` are refused before the browser is touched.

## Typing

`write` clears the field first by default and returns `value` read back off the
element, so you can confirm the text landed. A password field and a field typed
from a secret are never read back, so `value` is null there.

```
write(selector={"xpath": "//input[@name='q']"}, text="selenium grid", submit=true)
```

`submit` presses Enter afterwards, so a search box is one call. The value is read
before the submit.

A multi-field form is one `write` per field, then a `click` on the button:

```
write(selector={"xpath": "//input[@name='email']"}, text="a@example.com")
write(selector={"xpath": "//input[@name='password']"},
      secret={"name": "example", "key": "password"})
interact(action="click", selector={"xpath": "//button[@type='submit']"})
```

A real password is never `text`: name a secret
(`skill://selenium-flow/references/SECRETS.md`). The click returns `url` and
`title` read after it, so a changed URL is your confirmation the submit worked.

To empty a field: `write(selector={"css": "#q"}, text="")`.

## Keys

`press_key` sends a key to an element, or to wherever focus is. It takes the key
three ways:

- A name, in the browser's spelling or Selenium's: `Enter` or `enter`,
  `ArrowDown` or `arrow_down`, `PageUp` or `page_up`, `Escape`, `Tab`,
  `Backspace`, `F5`. Case, underscores and hyphens do not matter.
- One character: `a`, `/`, `?`.
- A combination joined with `+`: `Control+a` selects everything, `Shift+Tab`
  moves focus back. Each modifier is held for the keys after it and released at
  the end. `Control++` is Control and the plus key.

```
press_key(key="Escape")                        # dismiss a modal
press_key(selector={"xpath": "//input[@name='q']"}, key="Tab")
press_key(selector={"css": "textarea"}, key="Control+a")     # select all of it
```

The browser runs on Linux, so a site's shortcuts use `Control`, not the macOS
Command key.

## Uploading a file

If you wrote the content yourself, send it as text and do not encode it:

```
upload_file(selector={"xpath": "//input[@type='file']"},
            text='{"rows": 3}', filename="data.json")
```

The server writes the real file and ships it to the browser. Exactly one source
is required:

| Pass | For |
|---|---|
| `text` | content you have as text: JSON, CSV, YAML, markdown, a log excerpt |
| `content` | base64, for binary |
| `file` | a file this workspace has, by its `workspace://files` or `session://downloads` URI |

A file on the server's own disk cannot be uploaded.

Name it with an extension. The page reads a file's type from the filename:
`data.json` arrives as `application/json`, while a file called `data` arrives
with an empty type and may be rejected by an upload form that checks. If you
cannot give an extension, pass `mime_type`. The result has the `filename` and
`bytes` actually attached. Over HTTP the same action takes a `multipart/form-data`
file part.

## Iframes: switch in, and switch back

An element inside a frame is invisible to every locator until the session is
switched into it, which is the usual cause of an "XPath is definitely right"
timeout.

```
frame(action="switch", selector={"xpath": "//iframe[@id='checkout']"})
write(selector={"xpath": "//input[@name='card']"}, text="4242...")     # inside the frame
frame(action="default")                                   # back to the page
```

| `action` | Goes |
|---|---|
| `switch` | into the frame named by `selector` (or `index`; a selector wins when both are given) |
| `parent` | up one level, for nested frames |
| `default` | all the way back to the main page |

The switch sticks: every later action stays inside that frame until something
switches back, so a locator on the main page fails for a reason that looks
nothing like the cause. Switch back as soon as you are done, and if something
obvious is failing, check `in_frame` on `workspace://current` before rewriting
the selector.

## Native dialogs block everything

An `alert`, `confirm` or `prompt` freezes the page: until it is answered, other
actions cannot read the URL or the title. An action that opens one still
succeeds and tells you:

```
interact(action="click", selector={"xpath": "//button[@id='delete']"})
  -> {"action": "click", "url": null, "title": null,
      "dialog": "Delete everything?", "hint": "a dialog is open ..."}
```

Answer it with `dialog`:

```
dialog(action="read")                       # see the message, leave it open
dialog(action="accept")                     # confirm  -> the page gets true
dialog(action="dismiss")                    # cancel   -> the page gets false
dialog(action="send_text", text="Ada")      # fill a prompt, then accept
```

The server never answers a dialog for you. An open dialog blocks most commands,
including the one that checks a session is alive, so a blocked session is not a
gone one: answer the dialog and carry on with the same browser. `dialog` waits
up to 10 seconds for a dialog.

## Resizing

```
resize(width=1400, height=900)
```

A window size you leave out is the browser's own default. Set it before judging
anything visual. `resize` is remembered, so a browser reopened after a reap has
the same size.

## Scrolling is execute_script

`press_key(key="page_down")` only moves the page when focus is on the scrollable
container, which it usually is not.

```
execute_script(script="window.scrollTo(0, document.body.scrollHeight)")   # bottom
execute_script(script="window.scrollBy(0, 800)")                          # one screen
execute_script(script="document.querySelector('#row-42').scrollIntoView()")
```

`scrollIntoView` is usually what you want before interacting with something far
down a long page. `interact(action="scroll_to", ...)` does the same for a
selector.

## Waiting

You rarely need an explicit wait. `click` and `write` wait for the element to be
clickable; `extract` and `screenshot` wait for it to exist. All default to 30
seconds and take `wait_timeout`. If a page is genuinely slow, raise
`wait_timeout` on that one call rather than polling in a loop.

Never sleep in a script. Wait for the effect, with `assert`:

```
assert(script="return document.querySelector('#finish')?.offsetParent !== null",
       wait_timeout=30, message="The result never appeared.")
```

To wait for content, wait for the element that holds it:

```
extract(selector={"xpath": "//div[@class='results'][.//li]"}, wait_timeout=60)
```

## Anything else

`execute_script` covers what the other tools do not: dispatching events, setting
values on inputs a normal `write` cannot reach, reading computed styles, clearing
storage. For drag and drop use `drag`, which sends real pointer input.

```
execute_script(script="localStorage.clear(); return true")
```
