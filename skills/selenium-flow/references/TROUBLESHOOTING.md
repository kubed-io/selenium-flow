# When something goes wrong

**Read the error first.** When the element is on the page but cannot be used,
the failure says which of these it is and what to do about it: an ancestor is
hidden — a `:hover` menu looks exactly like this, and the message names the
*visible* element to hover when it can identify one, because nothing with
`display: none` can receive a pointer — something is on top of it (dismiss the
banner), it has no size, it is outside the viewport (`scroll_to`), or it is
disabled. That sentence is the diagnosis; the list below
is for when there is no element at all.

## A timeout on an XPath you believe in

**Suspect the page before the expression.** The browser is persistent, so a
failed earlier step can leave it somewhere you did not expect, and every later
XPath then fails for a reason that has nothing to do with the XPath.

The error says which XPath was waited for, for how long, and what URL the
browser was on when it gave up — read it before changing anything. If that URL
is not the page you expected, the selector was never the problem.

Check where you are, cheaply:

```
extract(selector={"xpath": "//title"})
```

or read `session://current`, which reports the URL without touching the browser.

If the URL is right and the element still is not found, in order of likelihood:

1. **It is inside an iframe** — or you are *already* inside one and the element
   is not. Check `in_frame` on `session://current`: a frame switch sticks until
   something switches back, so a locator on the main page fails while you are
   still in a frame. Use `frame(action="switch", ...)` to go in and
   `frame(action="default")` to come back.
2. **It has not rendered yet** and the wait was too short — raise `wait_timeout`.
3. **It is off-screen in a virtualised list** — scroll it into view first
   (`references/INTERACTION.md`).
4. **The XPath is brittle** — positional paths break on any layout change; match
   on an attribute or on visible text instead.

## "name your session"

You called without naming one. Add `?session=<name>` to the server URL, or send
an `X-Session-Key` header — whichever your client can set. Any name you choose
is fine; the same name always comes back to the same browser.

**"name your session once"** is the other half: the request carried *both*, and
two names is two ideas about who is calling. Send whichever one you control and
drop the other.

Neither is transient; retrying unchanged will not help. There is no `session_id`
to pass on any call — see `references/SESSIONS.md`.

## "no browser is open for you yet"

Nothing opens a browser implicitly — `open_session` is the only place that
happens, because it is the only place window size and timeouts can be chosen.
Call it once, then carry on.

## An invalid or unknown session

The Grid reaped the browser. Nothing to do: your session survives it, and the
next call reopens a browser and returns to the last URL. Just retry.

`session://current` shows `live: false` in the meantime.

## "unexpected alert open", or a null url and title

A native dialog is open, and it blocks reading the page. The action that opened
it will have told you so:

```
{"action": "click", "url": null, "title": null, "dialog": "Delete everything?"}
```

Answer it with `dialog` — `accept`, `dismiss`, or `send_text` for a prompt — and
carry on. See `references/INTERACTION.md`. Nothing else will work until you do.

## A dialog does not mean the session died

An open dialog blocks most commands, including the one that checks whether a
session is still alive. That is a *blocked* session, not a gone one — answer the
dialog and carry on with the same browser. Nothing needs reopening.

## A blank or single-colour screenshot

The page had not finished rendering. Force a wait by extracting something from
it first, then capture:

```
extract(selector={"xpath": "//main"})
screenshot()
```

Over HTTP, a `bytes` value near zero is the same signal.

## A click that appears to do nothing

`click` returns the URL and title *after* the click. If they are unchanged, the
click either hit the wrong element or the page uses JavaScript that has not
settled. Confirm what you actually clicked:

```
extract(selector={"xpath": "//button[contains(., 'Submit')]"})
```

If an overlay or cookie banner is intercepting the click, dismiss it first —
they are the most common cause of a click that lands on nothing.

## A hover that reported success and opened nothing

A hover onto a target the pointer is **already inside** fires no `mouseover`: the
pointer did not move, so nothing happened, and the call still returns `ok`. It is
the same menu failing twice in a row that gives it away.

The server now steps the pointer aside before moving back, and the result says
`nudged: true` when it did. If you are on an older server, hover something else
first and then hover the target again.

Do not try to check afterwards with `document.querySelectorAll(':hover')` — it
comes back **empty** while the `:hover` styling is plainly applying. Read the
thing you wanted instead: `outline` the menu, or check the revealed element's
computed `display`.

## The advice said hover and hovering did nothing

Then it is a menu that opens on **click**. `outline` says which: an entry's
`open_with` is `click` when the trigger carries `aria-expanded`, and the trigger
itself is in `revealed_by`. Click that.

## Everything is slow or a session will not open

The Grid runs a small, fixed number of browsers. If they are all held, a new
session waits for a slot. That is almost always abandoned sessions from earlier
runs, not load. Close what you own, and remember that failure paths need to
reach `end_browser` too.

**`"ready": false` with no nodes is not a broken Grid** when the Grid scales
itself — KEDA on Kubernetes, say. An idle autoscaled Grid runs no browsers at
all, so its status says it is not ready and lists no nodes, and it starts a
browser when a session is asked for. The first `open_session` after a quiet spell
can take a minute while one boots. Only a failing `open_session` means something
is wrong.

## Running out of slots

The Grid's capacity is shared. An unclosed browser holds a slot until its idle
timeout expires, so the cost of forgetting to close is paid by whoever runs
next. Close in a finally-shaped way: on success, on failure, and before you give
up on a task.
