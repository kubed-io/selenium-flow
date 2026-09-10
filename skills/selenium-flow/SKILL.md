---
name: selenium-flow
description: Drive a real Chrome or Firefox browser on Selenium Grid through the selenium-flow MCP server. Use when a task needs a live browser - logging in, filling and submitting a form, clicking through a multi-step flow, reading a page that only renders under JavaScript, capturing how something looks, or checking a page in a second browser. Start here to decide whether you must pass session_id, then read the one reference that matches what you are doing.
---

# Driving a browser with selenium-flow

The browser is **real and persistent**. It lives on Selenium Grid, not in the
server, and it keeps its page, cookies, storage and scroll position between your
calls. You are steering one tab, not making stateless requests.

Two consequences drive everything else:

- **State carries over.** Log in once and every later call is logged in. But a
  browser left on the wrong page makes your next XPath fail for a reason that
  has nothing to do with the XPath.
- **Slots are scarce.** The Grid runs a handful of browsers in total. An
  abandoned one holds its slot until it is reaped, so closing is capacity, not
  politeness.

## Step 0: which session mode are you in?

Read the `session://current` resource before your first action. It is free and
it decides how every later call is shaped.

| It reports | You are | Read |
|---|---|---|
| `"mode": "stateless"` | you own the session id — pass it on every call | `references/STATELESS.md` |
| `"mode": "saved"` | the server holds your browser — never pass an id | `references/SAVED_SESSIONS.md` |

It also links the right reference in its `guidance` field, so you do not have to
remember which. The two modes are **exclusive**: using the wrong one fails every
call the same way. If you cannot read resources, the `current_session` tool
returns the same object.

**Either way, call `open_session` first.** Nothing opens a browser implicitly,
because `open_session` is the only place its browser, window size and timeouts
can be set.

## Chrome or Firefox

`open_session(browser="firefox")` opens Firefox; the default is Chrome. Every
other tool behaves identically on both, so this is the only call that changes —
reach for Firefox when the task is *about* Firefox, such as confirming a
rendering difference or a site that treats the two differently, and otherwise
leave it alone.

A session cannot switch browser. To compare the two, open one session per
browser and keep both ids; `session://current` reports which browser the one
you are holding is.

## The three rules

**1. Read with `extract`, not `screenshot`.** An image of text costs orders of
magnitude more, cannot be quoted, and may be a picture of a half-rendered page.
Screenshot only when the *visual* is the answer.

**2. `url` is not an assertion.** Every action except open/close takes an
optional `url` and navigates there first if the browser is elsewhere. One call,
not two:

```
extract(url="https://example.com/settings", xpath="//h1")
```

**3. Always close.** Including on failure paths. `close_session()` frees the
slot; skipping it makes the next person wait.

## Where to go next

Load only what the task needs.

| Doing | Read |
|---|---|
| Deciding how to pass sessions, or recovering a dead one | `references/STATELESS.md` / `references/SAVED_SESSIONS.md` |
| Getting content out of a page, choosing an XPath | `references/READING_PAGES.md` |
| Clicking, hovering, typing, uploading, dialogs, scrolling, waiting | `references/INTERACTION.md` |
| A timeout, an empty screenshot, a click that did nothing | `references/TROUBLESHOOTING.md` |
| Setting the server up, connecting a client, which env var to change | `references/CONFIGURATION.md` |

## A whole task, minimally

In saved mode — `open_session` once, then no `session_id` anywhere:

```
open_session(width=1400, height=900)
write(url="https://example.com/login", xpath="//input[@name='email']", text="a@example.com")
write(xpath="//input[@name='password']", text="...")
interact(action="click", xpath="//button[@type='submit']")
extract(xpath="//h1")            # confirm you landed
close_session()
```

Stateless is the same shape with `session_id` on every call. That is the only
difference between the two modes.

## The rest of the surface

`interact` is every mouse gesture — click, double_click, right_click, hover,
scroll_to. `frame` moves in and out of iframes, whose contents are otherwise
invisible to every locator. `upload_file` attaches a file to a file input. `dialog` answers a
native alert, confirm or prompt, which otherwise blocks everything. `resize`
changes the window at any time, not just at open.

`execute_script` is the escape hatch for anything left over — page-level
scrolling above all, plus batch reads, computed styles and direct DOM access.
`press_key` sends named keys (`tab`, `escape`, `enter`, arrows) but is **not** a
reliable way to scroll. All of it is in `references/INTERACTION.md`.
