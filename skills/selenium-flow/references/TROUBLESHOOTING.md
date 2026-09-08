# When something goes wrong

## A timeout on an XPath you believe in

**Suspect the page before the expression.** The browser is persistent, so a
failed earlier step can leave it somewhere you did not expect, and every later
XPath then fails for a reason that has nothing to do with the XPath.

The error says which XPath was waited for, for how long, and what URL the
browser was on when it gave up — read it before changing anything. If that URL
is not the page you expected, the selector was never the problem.

Check where you are, cheaply:

```
extract(xpath="//title")
```

or read `session://current`, which reports the URL without touching the browser.

If the URL is right and the element still is not found, in order of likelihood:

1. **It is inside an iframe.** Selenium does not cross frame boundaries, and
   there is no frame tool yet — use `execute_script` against the frame's own
   document, or address the frame's `src` URL directly with `navigate`.
2. **It has not rendered yet** and the wait was too short — raise `wait_timeout`.
3. **It is off-screen in a virtualised list** — scroll it into view first
   (`references/INTERACTION.md`).
4. **The XPath is brittle** — positional paths break on any layout change; match
   on an attribute or on visible text instead.

## "session_id is required"

The message says which situation you are in. Either the server cannot identify
you (`references/STATELESS.md`) or saved sessions are switched off. It is not a
transient error and retrying unchanged will not help.

## An invalid or unknown session

The Grid reaped the browser.

- **Server holds your session:** nothing to do. The next call reopens one and
  returns to the last URL. Just retry.
- **You hold the session id:** it is dead. `open_session` for a new one and
  navigate back. Do not retry the old id.

Read `session://current` to tell the two apart: `live: false` with a non-null
`key` means a refresh is available on the next call.

## "unexpected alert open", or a null url and title

A native dialog is open, and it blocks reading the page. The action that opened
it will have told you so:

```
{"action": "click", "url": null, "title": null, "dialog": "Delete everything?"}
```

Answer it with `dialog` — `accept`, `dismiss`, or `send_text` for a prompt — and
carry on. See `references/INTERACTION.md`. Nothing else will work until you do.

## A blank or single-colour screenshot

The page had not finished rendering. Force a wait by extracting something from
it first, then capture:

```
extract(xpath="//main")
screenshot()
```

Over HTTP, a `bytes` value near zero is the same signal.

## A click that appears to do nothing

`click` returns the URL and title *after* the click. If they are unchanged, the
click either hit the wrong element or the page uses JavaScript that has not
settled. Confirm what you actually clicked:

```
extract(xpath="//button[contains(., 'Submit')]")
```

If an overlay or cookie banner is intercepting the click, dismiss it first —
they are the most common cause of a click that lands on nothing.

## Everything is slow or a session will not open

The Grid runs a small, fixed number of browsers. If they are all held, a new
session waits for a slot. That is almost always abandoned sessions from earlier
runs, not load. Close what you own, and remember that failure paths need to
reach `close_session` too.

## Running out of slots

The Grid's capacity is shared. An unclosed browser holds a slot until its idle
timeout expires, so the cost of forgetting to close is paid by whoever runs
next. Close in a finally-shaped way: on success, on failure, and before you give
up on a task.
