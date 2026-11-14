# Acting on a page

## Clicks, hovers and the pointer

```
interact(action="click", selector={"xpath": "//button[@type='submit']"})
interact(action="hover", selector={"xpath": "//nav//li[contains(., 'Account')]"})
interact(action="scroll_to", selector={"xpath": "//tr[last()]"})
```

- Every gesture except `scroll_to` leaves the pointer on the element, so a menu
  that opens on `:hover` stays open: hover the menu, then click the item in it.
  A script's synthetic events never set `:hover`, so `execute_script` cannot open
  such a menu.
- A hover onto an element the pointer is already inside would send no
  `mouseover`, so the server steps the pointer aside first and says
  `nudged: true`.
- `click`, `double_click` and `right_click` wait for the element to be
  clickable. `hover` and `scroll_to` wait only for it to exist, so `scroll_to`
  works on an element off-screen.
- When a selector matches several elements, a click uses the first that is
  displayed and enabled; `hover`, `scroll_to` and `drag`'s `to` use the first
  displayed, or the first if none is. `matched` and `index` (from 0) in the
  result say which; for `drag` they describe the source.
- A click returns `received_by` and, when the click may have gone to something
  else, a `warning` (TROUBLESHOOTING.md, *A click that appears to do nothing*).
- `glide=true` moves the pointer in small steps, for interfaces that watch
  movement. The first move in a browser has no known start, so it is a jump, and
  `glide_note` says so.

## Dragging

```
drag(selector={"css": ".card:nth-child(2)"}, to={"css": ".column.done"})
drag(selector={"css": "input[type=range]"}, by_x=120)
```

Give `to` or an offset, never both. `drag` glides by default. On Firefox a
native HTML5 drop does not complete, so a `draggable=true` element there may
need the page's own fallback.

## Going to a page in the same call

An action with an optional `url` navigates there first only when the browser is
elsewhere; on that page it does nothing. `navigate` always loads. `frame`,
`resize` and `dialog` take no `url`.

```
interact(url="https://example.com/settings", action="click", selector={"css": "#save"})
```

Use it whenever you know the address, and click through a site only to reach a
page you cannot address.

- Two URLs are one page when host, port, path, hash route, query and credentials
  match: `/x`, `/x/` and `/x#top` are one page, `/#/` and `/` are one, `/#/a` and
  `/#/b` are two, and so are `/x?p=1` and `/x`. A route's own query counts
  (`#/w?file-id=1`), and `url="https://app/"` from `#/dashboard` opens the root.
- Moving between hash routes does not reload the page, so wait on something only
  the new screen has.
- Only `http`, `https` and `about:blank` open, here and in `navigate` and
  `open_session`. `file:`, `chrome:`, `view-source:`, `javascript:` and `data:`
  are refused before the browser is touched.

## Typing

```
write(selector={"xpath": "//input[@name='q']"}, text="selenium grid", submit=true)
write(selector={"css": "#q"}, text="")    # empty the field
```

`write` returns `value` read back before any submit. A password field, and a
field typed from a secret, are never read back: `value` is null there. A real
password goes in a `secret`, never `text` (SECRETS.md).

## Keys

```
press_key(key="Escape")
press_key(selector={"xpath": "//input[@name='q']"}, key="Tab")
press_key(selector={"css": "textarea"}, key="Control+a")
```

In a combination each modifier is held for the keys after it and released at
the end. `Control++` is Control and the plus key. The browser runs on Linux, so
a site's shortcuts use `Control`, not Command.

## Uploading a file

```
upload_file(selector={"xpath": "//input[@type='file']"}, text='{"rows": 3}', filename="data.json")
```

Send content you wrote as `text`, unencoded. A file on the server's own disk
cannot be uploaded; a file the workspace has goes by its `workspace://files` or
`session://downloads` URI as `file`. The result has the `filename` and `bytes`
attached.

## Iframes

An element inside a frame is invisible to every locator until you switch in,
the usual cause of a timeout on a selector that looks right.

```
frame(action="switch", selector={"xpath": "//iframe[@id='checkout']"})
write(selector={"xpath": "//input[@name='card']"}, text="4242...")
frame(action="default")
```

`parent` goes up one level. A `selector` wins over `index` when both are given.
The switch sticks for every later call, so switch back as soon as you are done.
When something obvious fails, check `in_frame` on `workspace://current`.

## Native dialogs

An `alert`, `confirm` or `prompt` blocks the page: until it is answered, nothing
can read the URL or title. The action that opened one still succeeds and says so:

```
interact(action="click", selector={"xpath": "//button[@id='delete']"})
  -> {"action": "click", "url": null, "title": null,
      "dialog": "Delete everything?", "hint": "a dialog is open ..."}
```

```
dialog(action="read")                     # the message, left open
dialog(action="accept")                   # the page gets true
dialog(action="dismiss")                  # the page gets false
dialog(action="send_text", text="Ada")    # fill a prompt and accept
```

The server never answers a dialog for you. A dialog also blocks the check that a
session is alive, so a blocked session is not a gone one: answer it and carry on
with the same browser.

## Scrolling and other scripts

`press_key(key="page_down")` scrolls only when focus is on the scrolling
container, which it usually is not. Scroll with a script:

```
execute_script(script="window.scrollTo(0, document.body.scrollHeight)")
execute_script(script="window.scrollBy(0, 800)")
execute_script(script="document.querySelector('#row-42').scrollIntoView()")
```

`interact(action="scroll_to", ...)` does the same for a selector. Scripts also
cover dispatching events, setting a value `write` cannot reach and clearing
storage. For drag and drop use `drag`, which sends real pointer input.

## Waiting for content

A tool waits for its element, so to wait for content, select the element that
holds it:

```
extract(selector={"xpath": "//div[@class='results'][.//li]"}, wait_timeout=60)
```

On a slow page raise `wait_timeout` on that call. For an effect no element
shows, `assert` a script that returns true once it has happened.
