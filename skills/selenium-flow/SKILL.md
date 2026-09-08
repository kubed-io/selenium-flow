---
name: selenium-flow
description: Drive a real browser on Selenium Grid through the selenium-flow MCP server. Use when a task needs a live browser - logging in, filling and submitting a form, clicking through a multi-step flow, reading a page that only renders under JavaScript, or capturing how something looks. Start here to decide whether you must pass session_id, then read the one reference that matches what you are doing.
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
| `"key": null` | stateless — you own the session id | `references/STATELESS.md` |
| a non-null `key` | the server holds your browser | `references/SAVED_SESSIONS.md` |

Get this wrong and every call fails the same way, so it is worth the one read.
If you cannot read resources, the `current_session` tool returns the same object.

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
| Typing, clicking, submitting a form, scrolling, waiting | `references/INTERACTION.md` |
| A timeout, an empty screenshot, a click that did nothing | `references/TROUBLESHOOTING.md` |

## A whole task, minimally

With saved sessions on, no `session_id` anywhere and no explicit open:

```
write(url="https://example.com/login", xpath="//input[@name='email']", text="a@example.com")
write(xpath="//input[@name='password']", text="...")
click(xpath="//button[@type='submit']")
extract(xpath="//h1")            # confirm you landed
close_session()
```

Stateless is the same shape with `open_session` first and `session_id` on every
call. That is the only difference between the two modes.

## The rest of the surface

`execute_script` is the escape hatch for anything the other tools do not cover —
scrolling above all, plus batch reads, computed styles and direct DOM access.
`press_key` sends named keys (`tab`, `escape`, `enter`, arrows) but is **not** a
reliable way to scroll. Both are covered in `references/INTERACTION.md`.
