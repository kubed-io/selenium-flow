---
name: selenium-flow
description: Drive a real browser on Selenium Grid through the selenium-flow MCP server. Use when a task needs a live browser - logging in, filling and submitting a form, clicking through a multi-step flow, reading a page that only renders under JavaScript, or capturing how something looks. Covers deciding whether to pass session_id, reading a page cheaply instead of screenshotting it, jumping straight to a URL instead of clicking a path to it, scrolling, recovering a lost session, and freeing the Grid slot when done.
---

# Driving a browser with selenium-flow

The browser is **real and persistent**. It lives on Selenium Grid, not in the
server, and it keeps its page, cookies, storage and scroll position between your
calls. You are steering one tab, not making stateless requests.

Two things follow, and they are the whole skill:

- **State carries over.** Log in once and every later call is logged in. But a
  browser left on the wrong page will silently make your next XPath fail.
- **Slots are scarce.** The Grid runs a handful of browsers in total. An
  abandoned one holds its slot until the Grid reaps it, so closing is not
  politeness, it is capacity.

## First: do you need to pass session_id?

Read the `session://current` resource before your first action. It answers this
in one shot and costs nothing:

```json
{"session_id": "...", "url": "...", "live": true,
 "key": "named:desktop", "key_source": "named", "store": "memory"}
```

- **`key` is not null** — the server can identify you. **Omit `session_id`** on
  every call. It finds your browser, and opens one on first use if you have
  none, so you can start with `navigate` and skip `open_session` entirely.
- **`key` is null** — the server cannot identify you. **You own the session.**
  Call `open_session`, keep the `session_id` it returns, and pass it on every
  single call. This is the normal mode for a workflow engine driving the HTTP
  endpoints, where the session is passed between steps.

If you cannot read resources, call the `selenium_flow_skill` tool's sibling
`current_session` tool instead - it returns the same object.

A call that needs a session and cannot find one fails with a message telling you
exactly which of the two situations you are in. Read it rather than guessing.

## Read pages with extract, not screenshot

This is the biggest cost decision you will make. `extract` returns text and HTML
for an XPath. `screenshot` returns an image that costs orders of magnitude more
context and cannot be searched or quoted.

- Reading content, checking a value, confirming a page loaded → **`extract`**.
- Layout, styling, a rendered chart, "does this look right" → **`screenshot`**.

Start wide and narrow down: `extract` with `//body` for a first look at an
unfamiliar page, then a specific XPath once you know the structure. `//body` on a
large page is still far cheaper than an image of it.

## Jump straight to the page

Every action except `open_session` and `close_session` takes an optional `url`,
and it is **not an assertion**. If the browser is somewhere else, the action
navigates there first and then acts.

```
extract(url="https://example.com/settings", xpath="//h1")
```

That is one call, not navigate-then-extract, and it does nothing if you are
already there. Use it whenever you know the address. Clicking a path through a
site is for when you genuinely cannot address the destination.

URLs compare with the fragment and any trailing slash ignored, so `/x`, `/x/`
and `/x#top` are the same page. Query strings count as different.

## Fill a form in as few calls as possible

`write` takes `submit`, which presses Enter after typing. A search box is one
call, not three:

```
write(url="https://example.com", xpath="//input[@name='q']",
      text="selenium grid", submit=true)
```

`write` returns `value`, read back off the element, so you can confirm the text
actually landed rather than assuming. It is read *before* any submit, because
submitting navigates and the element reference goes stale.

For a multi-field form, `write` each field with `clear=true` (the default), then
`click` the submit button. Check the returned `url` and `title` - they are read
*after* the click, so a navigation tells you the submit worked.

## Scrolling is execute_script

`press_key` with `page_down` only moves the page when focus happens to be on the
scrollable container, which it usually is not. Do not rely on it.

```
execute_script(script="window.scrollTo(0, document.body.scrollHeight)")
```

`execute_script` is the general escape hatch: computed styles, drag and drop,
reading many values at once, triggering events, anything the eight other tools do
not cover. Use `return` to send a value back.

Reading several things in one call beats several `extract` calls:

```
execute_script(script="return [...document.querySelectorAll('.row')].map(r => r.innerText)")
```

## Waiting

`click` and `write` wait for the element to be *clickable*; `extract` and
`screenshot` wait for it to *exist*. All default to 30 seconds and take
`wait_timeout`.

So you rarely need an explicit wait - just act on the element and let the tool
wait. If something is slow, raise `wait_timeout` on that call rather than
polling in a loop.

## When something goes wrong

**A timeout on a sensible XPath usually means you are on the wrong page.** The
browser persists, so a failed earlier step can leave it somewhere unexpected.
Confirm with a cheap `extract` of `//title` or `//h1`, or read
`session://current` for the URL, before you start rewriting the XPath.

**A "session not found" or invalid-session error** means the Grid reaped the
browser. If the server holds your session for you, the next call reopens one and
returns to the last page automatically - just retry. If you are passing
`session_id` yourself, that id is dead: call `open_session` for a new one and
navigate back.

**A screenshot that is nearly all one colour** usually means the page had not
finished rendering. `extract` something from it first to force a wait, then
capture.

**XPath tips.** Prefer stable attributes over position: `//input[@name='q']`
beats `//div[3]/input`. Match on visible text with
`//button[contains(., 'Submit')]`. Use `//` liberally rather than spelling out
the full path.

## Always close

```
close_session()          # or close_session(session_id="...") if you own it
```

Call it when finished, **including after a failure**. A browser you abandon
holds one of the Grid's few slots until it times out. If you are mid-task and
hit an error you cannot recover from, close before you stop.

Do not close a session you did not open and do not own - in a workflow that
passes a `session_id` between steps, only the final step closes it.
